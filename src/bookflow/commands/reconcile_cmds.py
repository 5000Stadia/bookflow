"""Bank reconciliation: adopt an opening balance, tick a statement, certify it.

The work itself already lived in `reconciliation_drafts` and `reconciliation_preparation`; these
four commands are what lets a person reach it. Each loads the account's stored state through
`reconciliation_loading`, prepares the change with no database in sight, and writes the rows
`reconciliation_persistence` shapes -- then reloads, which re-runs the whole aggregate validation
and proves every capture the operation stored before the transaction may commit.

Identifiers are minted in the planner so a preview and the run that follows it name the same
records, and every planner is pure enough to run twice: apply re-derives under its own write
transaction rather than trusting what the preview saw.
"""
from bookflow.company import reconciliation_commands_models as m
from bookflow.company import reconciliation_drafts as drafts
from bookflow.company import reconciliation_loading as loading
from bookflow.company import reconciliation_persistence as persistence
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company import schema as c
from bookflow.company.reconciliation_storage_validation import digest
from bookflow.core import clock
from bookflow.core.audit import write_event_to
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched, command

# Every private reason this family can answer with. Registered here so each one reaches a caller
# as itself rather than as a generic validation failure.
ERRORS = ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_RECONCILIATION_ATTEMPT_STATE',
          'E_RECONCILIATION_CHAIN_STALE', 'E_RECONCILIATION_DATE', 'E_RECONCILIATION_DEPENDENCY',
          'E_RECONCILIATION_DIFFERENCE', 'E_RECONCILIATION_DRAFT_STATE',
          'E_RECONCILIATION_MANIFEST', 'E_RECONCILIATION_MEMBERSHIP_CONFLICT',
          'E_RECONCILIATION_OPENING_UNPROVEN', 'E_RECONCILIATION_OPERATION_KEY_REUSED',
          'E_RECONCILIATION_SELECTION_STALE', 'E_RECONCILIATION_SOURCE_INVALID',
          'E_RECONCILIATION_UNSUPPORTED']


def dependency_guard(draft):
    """What a prepared change asserts it depends on: the chain the draft was started against.

    The stored state is checked against the draft regardless; this is the client saying which
    world it believes it is finishing in, so a draft prepared against a chain that has since
    moved is refused with the reason rather than quietly certified against the new one.
    """
    return digest(dict(chain_version=draft.base_chain_version, opening=draft.base_opening_id,
                       head=draft.base_head_id))


def _account_of(s, identity):
    row = s.company.conn.execute(c.reconciliation_drafts.select()
                                 .where(c.reconciliation_drafts.c.id == identity)).mappings().first()
    preparation.require(row is not None, 'E_RECORD_NOT_FOUND')
    return row['account_id']


def _reuse(s, operation_key):
    found = s.company.conn.execute(c.reconciliation_operations.select()
                                   .where(c.reconciliation_operations.c.operation_key == operation_key)
                                   ).mappings().first()
    preparation.require(found is None, 'E_RECONCILIATION_OPERATION_KEY_REUSED')


def _loaded(s, account_id):
    return loading.load(s, account_id)


def _draft(snapshot, identity):
    return drafts.load(snapshot, identity, authority_transactions=snapshot.authority_transactions)


def _commit(s, ctx, name, *, account_id, operation_id, event_id, kind, summary, document, targets,
            rows, updates=(), touched=()):
    """Write one operation and prove the account that now holds it."""
    at = clock.now_iso()
    actor = s.actor
    audit_event_id = write_event_to(s.company, ctx, name, summary, list(touched),
                                    actor_id=actor.id if actor else None,
                                    actor_kind=actor.kind if actor else None)
    made = persistence.created(ctx, actor.id if actor else None, audit_event_id, at)
    everything = persistence.merge(
        persistence.receipt(operation_id, command=name, operation_key=document['operation_key'],
                            request=document['request'], effect=document['effect'],
                            targets=targets, made=made),
        {'events': [persistence.event(event_id, operation_id=operation_id,
                                      audit_event_id=audit_event_id, kind=kind, ctx=ctx,
                                      actor_id=actor.id if actor else None, at=at)]},
        rows(made))
    for table in persistence.ORDER:
        values = everything.get(table)
        if values:
            s.company.conn.execute(c.metadata.tables['reconciliation_' + table].insert(), values)
    for statement in updates:
        s.company.conn.execute(statement)
    loading.prove_written(s, account_id, operation_id)
    return audit_event_id


def _targets(account_id, **kinds):
    found = [dict(kind='accounts', id=account_id)]
    for kind, values in kinds.items():
        found.extend(dict(kind=kind, id=v) for v in values if v)
    return found


def _start_family(verb, model, description):
    """`reconcile opening start` and `reconcile start`: both open a draft and differ only in kind."""

    def prepare(inp, ctx, s, ids):
        _reuse(s, inp.operation_key)
        snapshot = _loaded(s, inp.account)
        opening_draft = None
        if getattr(inp, 'opening_draft_id', None):
            opening_draft = _draft(snapshot, inp.opening_draft_id)
        value = drafts.start(snapshot, inp, identity=ids['draft'], revision_id=ids['revision'],
                             opening_draft=opening_draft)
        return value

    def planner(inp, ctx, s):
        ids = dict(draft=new_id(), revision=new_id(), operation=new_id(), event=new_id())
        value = prepare(inp, ctx, s, ids)
        return Plan(m.DraftOutput(draft=value), dict(ids=ids, input=inp))

    cmd = command('reconcile ' + verb, scope='company', description=description,
                  input_model=model, output_model=m.DraftOutput, writes={'company'},
                  required_role='standard', capability='ledger.post',
                  accepts_idempotency_key=True, error_codes=list(ERRORS))(planner)
    cmd.ledger = True

    def apply(plan, ctx, s):
        inp, ids = plan.data['input'], plan.data['ids']
        value = prepare(inp, ctx, s, ids)
        _commit(s, ctx, cmd.name, account_id=value.account_id, operation_id=ids['operation'],
                event_id=ids['event'], kind='draft_change',
                summary='started a ' + value.kind + ' reconciliation',
                document=dict(operation_key=inp.operation_key, request=inp.model_dump(mode='json'),
                              effect=dict(draft=value.id, account=value.account_id, kind=value.kind)),
                targets=_targets(value.account_id, drafts=[value.id]),
                rows=lambda made: persistence.draft(value, made=made),
                touched=[Touched('reconciliation_draft', value.id, 'create', None, 1,
                                 dict(account_id=value.account_id, kind=value.kind), db='company')])
        return Applied(m.DraftOutput(draft=value), [], 'started a ' + value.kind + ' reconciliation',
                       audited=True)

    cmd.applier(apply)
    return cmd


reconcile_opening_start = _start_family(
    'opening start', m.OpeningStart,
    'Open a draft that adopts a bank or credit card account, classifying everything dated on or '
    'before the opening date as covered by the entered balance or still outstanding.')

reconcile_start = _start_family(
    'start', m.Start,
    'Open a draft for one bank or credit card statement, against an adopted opening or the '
    'opening draft that is about to become one.')


def _mark_prepare(inp, ctx, s, ids):
    _reuse(s, inp.operation_key)
    account_id = _account_of(s, inp.draft)
    snapshot = _loaded(s, account_id)
    value = drafts.mark(snapshot, _draft(snapshot, inp.draft), inp, revision_id=ids['revision'])
    return snapshot, value


def _mark_planner(inp, ctx, s):
    ids = dict(revision=new_id(), operation=new_id(), event=new_id())
    _, value = _mark_prepare(inp, ctx, s, ids)
    return Plan(m.DraftOutput(draft=value), dict(ids=ids, input=inp))


reconcile_mark = command(
    'reconcile mark', scope='company',
    description='Tick or untick whole movements on an open reconciliation draft; a movement is '
                'marked in full or not at all, so its components can never be half cleared.',
    input_model=m.Mark, output_model=m.DraftOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['draft'],
    error_codes=list(ERRORS))(_mark_planner)
reconcile_mark.ledger = True


@reconcile_mark.applier
def _mark_apply(plan, ctx, s):
    inp, ids = plan.data['input'], plan.data['ids']
    snapshot, value = _mark_prepare(inp, ctx, s, ids)
    previous = snapshot.by('drafts')[value.id]['current_revision_id']
    table = c.reconciliation_drafts
    _commit(s, ctx, 'reconcile mark', account_id=value.account_id, operation_id=ids['operation'],
            event_id=ids['event'], kind='draft_change',
            summary='marked ' + str(len(value.selections)) + ' movements on a reconciliation',
            document=dict(operation_key=inp.operation_key, request=inp.model_dump(mode='json'),
                          effect=dict(draft=value.id, version=value.version,
                                      selected=len(value.selections))),
            targets=_targets(value.account_id, drafts=[value.id]),
            rows=lambda made: {k: v for k, v in persistence.draft(
                value, made=made, previous_revision_id=previous).items() if k != 'drafts'},
            updates=[table.update().where(table.c.id == value.id).values(
                version=value.version, current_revision_id=value.current_revision_id)],
            touched=[Touched('reconciliation_draft', value.id, 'update', value.version - 1,
                             value.version, dict(selected=len(value.selections)), db='company')])
    return Applied(m.DraftOutput(draft=value), [],
                   'marked ' + str(len(value.selections)) + ' movements on a reconciliation',
                   audited=True)


def _finish_prepare(inp, ctx, s, ids):
    """Certify a statement, and the opening it adopts when this is the account's first one."""
    _reuse(s, inp.operation_key)
    account_id = _account_of(s, inp.draft)
    snapshot = _loaded(s, account_id)
    value = _draft(snapshot, inp.draft)
    preparation.require(inp.dependency_guard == dependency_guard(value), 'E_RECONCILIATION_CHAIN_STALE')
    preparation.require(inp.expected_facts_fingerprint == preparation.fingerprint(snapshot, value),
                        'E_RECONCILIATION_SELECTION_STALE')
    opening_draft = None
    if value.base_opening_id is None:
        found = [d for d in snapshot.rows['drafts']
                 if d['account_id'] == account_id and d['kind'] == 'opening' and d['state'] == 'open']
        preparation.require(len(found) == 1, 'E_RECONCILIATION_DEPENDENCY')
        opening_draft = _draft(snapshot, found[0]['id'])
    totals = preparation.certify(preparation.statement(snapshot, value, opening_draft=opening_draft))
    return snapshot, value, opening_draft, totals


reconcile_finish = command(
    'reconcile finish', scope='company',
    description='Certify a reconciliation whose difference is zero, storing the statement it '
                'reconciles to and the account exactly as it stood when it was certified.',
    input_model=m.Finish, output_model=m.FinishOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['draft'],
    error_codes=list(ERRORS))(
    lambda inp, ctx, s: _finish_planner(inp, ctx, s))
reconcile_finish.ledger = True


def _finish_planner(inp, ctx, s):
    ids = dict(opening=new_id(), certificate=new_id(), operation=new_id(), event=new_id(), consume={})
    snapshot, value, opening_draft, totals = _finish_prepare(inp, ctx, s, ids)
    for d in (value, opening_draft):
        if d is not None:
            ids['consume'][d.id] = new_id()
    return Plan(m.FinishOutput(draft=value, account_id=value.account_id,
                               opening_id=value.base_opening_id or ids['opening'],
                               certificate_id=ids['certificate'], totals=totals),
                dict(ids=ids, input=inp))


@reconcile_finish.applier
def _finish_apply(plan, ctx, s):
    inp, ids = plan.data['input'], plan.data['ids']
    snapshot, value, opening_draft, totals = _finish_prepare(inp, ctx, s, ids)
    account_id = value.account_id
    opening_id = value.base_opening_id or ids['opening']
    certificate_id = ids['certificate']
    from bookflow.company.reconciliation_storage_validation import canonical
    issuer = canonical(persistence.issuer(snapshot.referenced_rows['company_info'][0]))
    consumed = [d.id for d in (value, opening_draft) if d is not None]

    def rows(made):
        built = []
        covered = set()
        if opening_draft is not None:
            opening_rows, _ = persistence.opening(opening_id, snapshot, opening_draft, made=made)
            covered = {v['key_id'] for v in opening_rows['opening_members']
                       if v['classification'] == 'covered'}
            built.append(opening_rows)
        else:
            covered = {v['key_id'] for v in snapshot.rows['opening_members']
                       if v['opening_id'] == opening_id and v['classification'] == 'covered'}
        certificate_rows, _ = persistence.certificate(
            certificate_id, snapshot, value, totals, opening_id=opening_id, covered=covered,
            prior=set(), issuer=issuer, made=made)
        built.append(certificate_rows)
        built.append(persistence.chain(
            account_id=account_id, currency=certificate_rows['certificates'][0]['currency'],
            convention=certificate_rows['certificates'][0]['convention'], opening_id=opening_id,
            certificate_id=certificate_id, event_id=ids['event'],
            statement_date=value.header.statement_date))
        built.append(persistence.claims(persistence.merge(*built), account_id=account_id,
                                        event_id=ids['event'], opening_id=opening_id,
                                        certificate_id=certificate_id))
        return persistence.merge(*built)

    # Consuming a draft is a new revision of it, not a flag: the storage will not accept a draft
    # whose version moves without one, which is what keeps a consumed draft's content readable.
    table = c.reconciliation_drafts
    consumed_values = [drafts.revised(d, ids['consume'][d.id], state='consumed',
                                      terminal_operation_id=ids['operation'])
                       for d in (value, opening_draft) if d is not None]
    _commit(s, ctx, 'reconcile finish', account_id=account_id, operation_id=ids['operation'],
            event_id=ids['event'], kind='finish',
            summary='certified a reconciliation to ' + value.header.statement_date,
            document=dict(operation_key=inp.operation_key, request=inp.model_dump(mode='json'),
                          effect=dict(certificate=certificate_id, opening=opening_id,
                                      account=account_id)),
            targets=_targets(account_id, drafts=consumed, openings=[opening_id],
                             certificates=[certificate_id]),
            rows=lambda made: persistence.merge(rows(made), *(
                {k: v for k, v in persistence.draft(
                    d, made=made, previous_revision_id=before.current_revision_id).items()
                 if k != 'drafts'}
                for d, before in zip(consumed_values, [value, opening_draft]))),
            updates=[table.update().where(table.c.id == d.id).values(
                state='consumed', terminal_operation_id=ids['operation'], version=d.version,
                current_revision_id=d.current_revision_id) for d in consumed_values],
            touched=[Touched('reconciliation_certificate', certificate_id, 'create', None, 1,
                             dict(account_id=account_id, statement_date=value.header.statement_date),
                             db='company')])
    return Applied(m.FinishOutput(draft=value, account_id=account_id, opening_id=opening_id,
                                  certificate_id=certificate_id, totals=totals), [],
                   'certified a reconciliation to ' + value.header.statement_date, audited=True)
