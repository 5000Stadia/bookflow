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
import base64
import hmac
import json

from bookflow.company import reconciliation_commands_models as m
from bookflow.company import reconciliation_discrepancies as discrepancies
from bookflow.company import reconciliation_queries as queries
from bookflow.core.errors import BookflowError
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
    """The chain a prepared change believes it is finishing against.

    `PreparedChange` requires this field and nothing defined what it holds, so this is the
    definition, chosen here rather than inferred later from a digest: the draft's own
    `base_chain_version`, `base_opening_id` and `base_head_id` -- the three facts that say which
    opening and which head certificate the draft was started against.

    What it guards: a client that read a draft, went away, and came back to finish it after
    someone else certified a statement on the same account. The stored chain is checked against
    the draft regardless, so a wrong guard never lets a bad certificate through; what the guard
    adds is that the client states the world it believes it is in, so the refusal says
    E_RECONCILIATION_CHAIN_STALE instead of the client silently certifying against a chain it
    never saw. Get it wrong and a legitimate finish is refused until the client re-previews --
    inconvenient, never unsafe.

    `reconcile preview` returns it, and is the only thing a caller should get it from. If a real
    requirement later needs a different set of facts in here, this docstring is the contract to
    change; nothing else infers meaning from the digest.
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


def _gaps(s, snapshot, draft):
    """Ticked movements the draft's imported statement has no line for; None when none was imported."""
    from bookflow.company import statement_store
    found = statement_store.gaps(s, snapshot, draft)
    return None if found is None else tuple(m.StatementGap(
        movement=v.ref[0], group_fingerprint=v.ref[1], date=v.date, amount=v.amount, number=v.number,
        payees=v.payees, memo=v.memo or None) for v in found)


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
        # A name resolves to the stable ID first, so the recorded request and every
        # later step carry the ID whichever way the caller named the account.
        from bookflow.company.accounts import resolve_account
        inp = inp.model_copy(update={'account': resolve_account(s.company, inp.account)['id']})
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
    'First step for an account never reconciled in Bookflow: open a draft that adopts a bank or '
    'credit card account at an opening date and balance. If you have an earlier statement you '
    'trust, use its date and ending balance and mark what it covered. If this is the first '
    'statement ever (no earlier one), start from zero the way a first reconciliation does: an '
    'opening_date before the account\'s first movement and entered_balance 0.00, marking nothing. '
    'The opening is not finished on its own: next run `reconcile start` with opening_draft_id set '
    'to this draft and the statement\'s date (after the opening date) and ending balance, tick its '
    'movements with `reconcile mark`, and `reconcile finish` that statement draft, which '
    'certifies the opening and the statement together. Later statements use `reconcile start` '
    'alone.')

reconcile_start = _start_family(
    'start', m.Start,
    'Open a draft for one bank or credit card statement. It follows the account\'s adopted opening '
    'unless you name opening_id, or opening_draft_id for an opening not finished yet; an account '
    'with no opening needs `reconcile opening start` first. Then `reconcile candidates`, '
    '`reconcile mark`, `reconcile preview` and `reconcile finish`. ' + preparation.STOP_RULE)


def _marked(inp, value):
    if inp.all and inp.all_action == 'unmark':
        return 'cleared every mark; ' + str(len(value.selections)) + ' movements remain marked'
    return 'marked ' + str(len(value.selections)) + ' movements on a reconciliation'


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
                'marked in full or not at all, so its components can never be half cleared. Name '
                'the movements in `entries` (movement and group_fingerprint, from `reconcile '
                'candidates`), or pass `all` to tick every movement dated on or before the '
                'statement date that no earlier statement has cleared, in one step (QuickBooks\' '
                '"Mark All"); `all_action: unmark` clears every tick, and `filters` narrows what '
                '`all` touches. Preview it with --dry-run, then `reconcile preview` and `reconcile '
                'finish`.',
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
            summary=_marked(inp, value),
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
    return Applied(m.DraftOutput(draft=value), [], _marked(inp, value), audited=True)


def _adopting(snapshot, draft):
    """The open opening draft a first statement adopts, or None once the account has one."""
    if draft.base_opening_id is not None:
        return None
    found = [d for d in snapshot.rows['drafts'] if d['account_id'] == draft.account_id
             and d['kind'] == 'opening' and d['state'] == 'open']
    preparation.require(len(found) == 1, 'E_RECONCILIATION_DEPENDENCY')
    return _draft(snapshot, found[0]['id'])


def _totals(snapshot, draft):
    """What the statement comes to, whether or not it balances. Preview and finish share it."""
    opening_draft = _adopting(snapshot, draft)
    return opening_draft, preparation.statement(snapshot, draft, opening_draft=opening_draft)


def _finish_prepare(inp, ctx, s, ids):
    """Certify a statement, and the opening it adopts when this is the account's first one."""
    _reuse(s, inp.operation_key)
    account_id = _account_of(s, inp.draft)
    snapshot = _loaded(s, account_id)
    value = _draft(snapshot, inp.draft)
    # Named before any fingerprint: an opening draft is finished through its first statement,
    # and the refusal says so instead of reporting a stale or mismatched guard.
    preparation.require(value.kind in ('statement', 'amendment') and value.state == 'open',
                        'E_RECONCILIATION_DRAFT_STATE', lambda: preparation.statement_only(value))
    preparation.require(value.version == inp.expected_version, 'E_VERSION_CONFLICT')
    preparation.require(inp.dependency_guard == dependency_guard(value), 'E_RECONCILIATION_CHAIN_STALE')
    preparation.require(inp.expected_facts_fingerprint == preparation.fingerprint(snapshot, value),
                        'E_RECONCILIATION_SELECTION_STALE')
    opening_draft, totals = _totals(snapshot, value)
    currency = snapshot.source.accounts[account_id]['currency']
    if inp.adjustment is not None:
        # Asked for by name, refused to anyone but a person whatever the difference is.
        discrepancies.require_person(s, ctx, totals.difference, currency)
        return snapshot, value, opening_draft, totals
    return snapshot, value, opening_draft, preparation.certify(totals, currency)


def _adjusting(inp, totals):
    """Whether this finish posts an adjustment: asked for, and something is left to adjust."""
    return inp.adjustment is not None and totals.difference != 0


reconcile_finish = command(
    'reconcile finish', scope='company',
    description='Certify a statement reconciliation whose difference is zero, storing the statement '
                'it reconciles to and the account exactly as it stood when it was certified. Takes a '
                'statement draft from `reconcile start`, never an opening draft: on an account\'s '
                'first reconciliation the opening that statement follows is certified with it. '
                + preparation.STOP_RULE + ' '
                'That adjustment (QuickBooks\' "Enter Adjustment") posts one journal for the exact '
                'remaining difference, dated the statement date, to the Reconciliation Discrepancies '
                'expense account (made on first use), and certifies the statement with it. Preview it '
                'with --dry-run. An agent passing `adjustment` is refused.',
    input_model=m.Finish, output_model=m.FinishOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, positional=['draft'],
    error_codes=list(ERRORS) + ['E_PERMISSION', 'E_PERIOD_CLOSED'])(
    lambda inp, ctx, s: _finish_planner(inp, ctx, s))
reconcile_finish.ledger = True


def _finish_planner(inp, ctx, s):
    ids = dict(opening=new_id(), certificate=new_id(), operation=new_id(), event=new_id(), consume={})
    snapshot, value, opening_draft, totals = _finish_prepare(inp, ctx, s, ids)
    for d in (value, opening_draft):
        if d is not None:
            ids['consume'][d.id] = new_id()
    original, adjustment = totals.difference, None
    if _adjusting(inp, totals):
        # Nothing is written here: the preview names the amount, the date and the account the
        # journal would post to, and the totals as they stand once it is cleared.
        from bookflow.company import journals
        journals.open_dates(s, [value.header.statement_date])
        row, created = discrepancies.account(s, ctx)
        adjustment = discrepancies.output(row, created, draft=value, amount=original,
                                          currency=snapshot.source.accounts[value.account_id]['currency'],
                                          reason=inp.adjustment.reason)
        totals = discrepancies.projected(totals, original)
    # The output shows the statement draft as the finish leaves it: consumed by this operation,
    # its content kept, so a read beside the new certificate never shows it still open.
    finished = drafts.revised(value, ids['consume'][value.id], state='consumed',
                              terminal_operation_id=ids['operation'])
    return Plan(m.FinishOutput(draft=finished, account_id=value.account_id,
                               opening_id=value.base_opening_id or ids['opening'],
                               certificate_id=ids['certificate'], totals=totals,
                               cleared_without_statement_line=_gaps(s, snapshot, value),
                               original_difference=original, adjustment=adjustment),
                dict(ids=ids, input=inp))


@reconcile_finish.applier
def _finish_apply(plan, ctx, s):
    inp, ids = plan.data['input'], plan.data['ids']
    snapshot, value, opening_draft, totals = _finish_prepare(inp, ctx, s, ids)
    gaps = _gaps(s, snapshot, value)
    account_id = value.account_id
    original, adjustment, journal_ids, created_touches = totals.difference, None, [], []
    if _adjusting(inp, totals):
        currency = snapshot.source.accounts[account_id]['currency']
        expected = discrepancies.projected(totals, original)
        header, row, created, created_touches = discrepancies.post(
            s, ctx, value, original, inp.adjustment.reason)
        journal_ids = [header['id']]
        # The account again, now holding the adjusting journal's movement, which this statement
        # clears: the certificate lists it as selected, so no later statement sees it again.
        snapshot = _loaded(s, account_id)
        value = _draft(snapshot, value.id)
        added = [v for v in snapshot.current.values() if v['transaction_id'] == header['id']
                 and v['account_id'] == account_id and v['active']]
        preparation.require(bool(added), 'E_RECONCILIATION_MANIFEST')
        value = m.Draft.model_validate(dict(value.model_dump(), selections=sorted(
            [*(v.model_dump() for v in value.selections),
             *(dict(key_id=v['key_id'], version_id=v['id'], action='mark') for v in added)],
            key=lambda v: v['key_id'])))
        opening_draft, totals = _totals(snapshot, value)
        totals = preparation.certify(totals, currency)
        # What the dry run promised is what was certified, to the minor unit.
        preparation.require(totals == expected, 'E_RECONCILIATION_DIFFERENCE')
        adjustment = discrepancies.output(row, created, draft=value, amount=original,
                                          currency=currency, reason=inp.adjustment.reason,
                                          journal_id=header['id'], number=header['number'])
    opening_id = value.base_opening_id or ids['opening']
    certificate_id = ids['certificate']
    from bookflow.company.reconciliation_storage_validation import canonical
    issuer = canonical(persistence.issuer(snapshot.referenced_rows['company_info'][0]))
    consumed = [d.id for d in (value, opening_draft) if d is not None]
    state = next((row for row in snapshot.rows['accounts']
                  if row['account_id'] == account_id), None)
    certificates = snapshot.by('certificates')
    generation = 1 + max((row['generation'] for row in certificates.values()
                          if row['account_id'] == account_id), default=0)
    prior = set()
    previous = value.base_head_id
    while previous is not None:
        prior.update(row['key_id'] for row in snapshot.rows['certificate_members']
                     if row['certificate_id'] == previous and row['classification'] == 'selected')
        previous = certificates[previous]['previous_certificate_id']
    account = snapshot.source.accounts[account_id]
    chain_rows = persistence.chain(
        account_id=account_id, currency=account['currency'],
        convention='card_debt' if account['type'] == 'credit_card' else 'bank',
        opening_id=opening_id, certificate_id=certificate_id, event_id=ids['event'],
        statement_date=value.header.statement_date,
        before_version=state['version'] if state else 0,
        before_opening=state['opening_id'] if state else None,
        before_head=state['head_certificate_id'] if state else None)
    chain_updates = []
    if state is not None:
        after = chain_rows.pop('accounts')[0]
        chain_updates.append(c.reconciliation_accounts.update()
                             .where(c.reconciliation_accounts.c.account_id == account_id)
                             .values(**after))

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
            prior=prior, issuer=issuer, made=made, generation=generation,
            previous=value.base_head_id, original_difference=original)
        built.append(certificate_rows)
        built.append(chain_rows)
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
            summary=_certified(value, adjustment),
            document=dict(operation_key=inp.operation_key, request=inp.model_dump(mode='json'),
                          effect=dict(certificate=certificate_id, opening=opening_id,
                                      account=account_id, **({} if gaps is None else dict(
                                          cleared_without_statement_line=[
                                              v.model_dump(mode='json') for v in gaps])),
                                      **({'adjustment': adjustment.model_dump(mode='json')}
                                         if adjustment else {}))),
            targets=_targets(account_id, drafts=consumed, openings=[opening_id],
                             certificates=[certificate_id], transactions=journal_ids),
            rows=lambda made: persistence.merge(rows(made), *(
                {k: v for k, v in persistence.draft(
                    d, made=made, previous_revision_id=before.current_revision_id).items()
                 if k != 'drafts'}
                for d, before in zip(consumed_values, [value, opening_draft]))),
            updates=chain_updates + [table.update().where(table.c.id == d.id).values(
                state='consumed', terminal_operation_id=ids['operation'], version=d.version,
                current_revision_id=d.current_revision_id) for d in consumed_values],
            touched=[Touched('reconciliation_certificate', certificate_id, 'create', None, 1,
                             dict(account_id=account_id, statement_date=value.header.statement_date,
                                  **({'adjustment_journal_id': journal_ids[0], 'original_difference': original}
                                     if journal_ids else {})),
                             db='company'), *created_touches])
    return Applied(m.FinishOutput(draft=consumed_values[0], account_id=account_id, opening_id=opening_id,
                                  certificate_id=certificate_id, totals=totals,
                                  cleared_without_statement_line=gaps,
                                  original_difference=original, adjustment=adjustment), [],
                   _certified(value, adjustment), audited=True)


def _certified(value, adjustment):
    said = 'certified a reconciliation to ' + value.header.statement_date
    if adjustment is not None:
        said += (' with a reconciliation adjustment of ' + adjustment.amount_decimal + ' to '
                 + adjustment.account_name)
    return said


# ------------------------------------------------------------------ reading a draft

# `reconciliation_queries` pages on a private integer offset and says so: an offset a client
# could edit would let it ask for a slice of a population it never saw. The public continuation
# is that offset and the page's own freshness token, signed with the company's cursor key, so a
# cursor is only ever handed back to the query that issued it.
CURSOR = b'bookflow.company.reconcile.candidates.v1\0'


def _cursor_mac(db, payload):
    from bookflow.company.ledger_reports import _cursor_key
    return hmac.digest(_cursor_key(db), CURSOR + payload, 'sha256')


def _encode_cursor(db, offset, fingerprint):
    payload = json.dumps(dict(offset=offset, fingerprint=fingerprint),
                         sort_keys=True, separators=(',', ':')).encode()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return encode(payload) + '.' + encode(_cursor_mac(db, payload))


def _decode_cursor(db, value):
    invalid = BookflowError('E_VALIDATION', details={'fields': [
        {'field': 'cursor', 'problem': 'invalid or mismatched continuation; restart without cursor'}]})
    try:
        if type(value) is not str or not 1 <= len(value) <= 2048:
            raise ValueError
        body, mac = value.encode('ascii').split(b'.')
        decode = lambda x: base64.b64decode(x + b'=' * (-len(x) % 4), altchars=b'-_', validate=True)
        payload = decode(body)
        if not hmac.compare_digest(decode(mac), _cursor_mac(db, payload)):
            raise ValueError
        found = json.loads(payload)
        return int(found['offset']), str(found['fingerprint'])
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise invalid from exc


def _candidates(inp, ctx, s):
    account_id = _account_of(s, inp.draft)
    snapshot = _loaded(s, account_id)
    value = _draft(snapshot, inp.draft)
    offset, expected = (0, None) if inp.cursor is None else _decode_cursor(s.company, inp.cursor)
    page = queries.candidates(snapshot, value, inp.filters,
                              authority_transactions=snapshot.authority_transactions,
                              limit=inp.limit, offset=offset, expected_fingerprint=expected)
    return Plan(m.CandidatesOutput(
        draft=value.id, draft_version=value.version, account_id=account_id,
        currency=snapshot.source.accounts[account_id]['currency'],
        cutoff=value.header.statement_date or value.header.opening_date,
        items=page.items, count=page.count, component_count=page.component_count,
        positive_sum=page.positive_sum, negative_sum=page.negative_sum, fingerprint=page.fingerprint,
        next_cursor=None if page.next_offset is None
        else _encode_cursor(s.company, page.next_offset, page.fingerprint)))


reconcile_candidates = command(
    'reconcile candidates', scope='company',
    description='Page the movements a reconciliation draft can clear, newest filters first; each '
                'row is a whole movement with the fingerprint `reconcile mark` needs to tick it.',
    input_model=m.Candidates, output_model=m.CandidatesOutput, required_role='member',
    capability='ledger.read', positional=['draft'],
    error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE', *ERRORS[2:]])(_candidates)


def _preview(inp, ctx, s):
    account_id = _account_of(s, inp.draft)
    snapshot = _loaded(s, account_id)
    value = _draft(snapshot, inp.draft)
    preparation.require(value.version == inp.expected_version, 'E_VERSION_CONFLICT')
    if value.kind == 'opening':
        totals = preparation.opening(snapshot, value)
    else:
        _, totals = _totals(snapshot, value)
    return Plan(m.PreviewOutput(
        draft=value.id, account_id=account_id,
        currency=snapshot.source.accounts[account_id]['currency'],
        kind=value.kind, version=value.version,
        next_step=(preparation.FIRST_RECONCILIATION if value.kind == 'opening'
                   else None if totals.difference == 0 else preparation.STOP_RULE),
        totals=totals, expected_facts_fingerprint=preparation.fingerprint(snapshot, value),
        dependency_guard=dependency_guard(value), balanced=totals.difference == 0,
        cleared_without_statement_line=None if value.kind == 'opening' else _gaps(s, snapshot, value)))


reconcile_preview = command(
    'reconcile preview', scope='company',
    description='Show what a reconciliation draft currently comes to, and hand back the exact '
                'facts fingerprint and dependency guard `reconcile finish` requires. On an opening '
                'draft, next_step says how it is finished: through its first statement; on a '
                'statement that does not tie, it says what to do instead of forcing it. When a statement '
                'was imported into the draft (`reconcile import`), cleared_without_statement_line lists '
                'each ticked movement no statement line accounts for; `reconcile finish` reports and '
                'records the same list, and refuses nothing for it.',
    input_model=m.Preview, output_model=m.PreviewOutput, required_role='member',
    capability='ledger.read', positional=['draft'], error_codes=list(ERRORS))(_preview)
