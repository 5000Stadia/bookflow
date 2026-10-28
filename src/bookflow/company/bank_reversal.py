"""Reversing a bank import, and moving it to another account (R108 increment 2, design/bank-feeds.md §6).

Not a second reverser: a bank import is a batch of kind ``bank-import`` in the shared ledger (design/bulk-import.md
§8a), and this module registers that kind's adapter. The ledger keeps the ordering (``strict``: newest first per
account), the plan and its fingerprint, the reversal batch, slices, ``reversing``/``blocked`` and resuming; the adapter
says what each effect's inverse is:

- an entry the import **created** (increments 3 to 5 add them) is deleted by its family's own ``delete`` (or ``void``
  where the family has no delete), so its history is preserved; these are the slices, each its own command;
- every **line** is hidden and its key released (``live`` cleared), every **match** undone, the **account record**
  (number, kind, last import) returned to what it was before the import, and each later increment's own effects (rules
  learned, increment 4) retired through ``RETIRE``: all of it in the one transaction that marks the batch reversed.

**What blocks it** is computed by the plan, never by a parallel dependency engine: a later live import on the account
(``later_import``), an entry edited since (``entry_edited``), an entry another account's live line is linked to
(``entry_linked``), a line copied into a reconciliation's statement (``statement_copy``, once increment 6 records the
copy), and, for everything else, the inverse command's own refusal from a dry run, reported by name (``entry_reconciled``,
``entry_deposited``, ``entry_applied``, ``no_authority``, ``period_closed``).

A **move** is the reversal followed, in the transaction that finishes it, by an import of the reversed import's own
stored lines into the right account (no file needed), recording ``moved_from``.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import bank_feeds, batches, schema as c, statement_files as files
from bookflow.company import bank_feed_models as m
from bookflow.core.ids import new_id

# The commands that take back an entry of each family, in order, and the field each selects the entry by. A bill
# payment an import marked paid (increment 5) is unapplied from its bills, then voided.
INVERSE = {
    'journal_entry': (('journal delete', 'journal'),), 'check': (('check delete', 'check'),),
    'card_charge': (('card-charge delete', 'card_charge'),), 'card_credit': (('card-credit void', 'card_credit'),),
    'transfer': (('transfer void', 'transfer'),), 'payment': (('payment delete', 'payment'),),
    'sales_receipt': (('sales-receipt delete', 'sales_receipt'),), 'invoice': (('invoice delete', 'invoice'),),
    'bill': (('bill delete', 'bill'),), 'credit_memo': (('credit-memo delete', 'credit_memo'),),
    'deposit': (('deposit delete', 'deposit'),),
    'bill_payment': (('bill payment unapply', 'payment'), ('bill payment void', 'payment')),
}


def inverse_payload(command: str, selector: str, batch_id: str, transaction_id: str, version: int | None,
                    step: int = 0) -> dict:
    """One inverse step's input. A command that requires an operation key gets one derived from the batch, the entry
    and the step, so a retry is the same operation (idempotent), never a second one."""
    from bookflow.core import registry
    fields = registry.get(command).input_model.model_fields
    payload = {selector: transaction_id}
    if 'expected_version' in fields:
        payload['expected_version'] = version
    if 'operation_key' in fields and fields['operation_key'].is_required():
        payload['operation_key'] = f'bank-reverse:{batch_id}:{transaction_id}:{step}'
    return payload


# An inverse command's refusal, by the blocker it is (design/bank-feeds.md §6), and what clears it.
CODES = {'E_RECONCILIATION_DEPENDENCY': 'entry_reconciled', 'E_DEPOSIT_DEPENDENCY': 'entry_deposited',
         'E_HAS_APPLICATIONS': 'entry_applied', 'E_HAS_REFUND': 'entry_applied', 'E_PERMISSION': 'no_authority',
         'E_PERIOD_CLOSED': 'period_closed', 'E_VERSION_CONFLICT': 'entry_edited'}
CLEARS = {
    'E_RECONCILIATION_DEPENDENCY': 'undo that reconciliation step (`reconcile` commands) first',
    'E_DEPOSIT_DEPENDENCY': 'void the deposit that includes it (`deposit void`) first',
    'E_HAS_APPLICATIONS': 'unapply what was applied against it (`bill-payment unapply`, `payment unapply`) first',
    'E_HAS_REFUND': 'take back the refund against it first',
    'E_PERMISSION': 'a person with the grant the details name',
    'E_PERIOD_CLOSED': 'reopen the period, or leave the import and ignore its lines',
    'E_VERSION_CONFLICT': 'the entry changed meanwhile; run the reversal again',
}
# Effects later increments record, with what retires each, called in the transaction that marks the import reversed:
# (record_type, action) -> retire(s, effect, batch, reversal_id) -> 'retired' | 'kept'. Increment 4 registers
# ('bank_rule', 'rule_learned') here; a learned rule a person edited is kept.
RETIRE: dict = {}
_OWN = {('bank_feed_import', 'created'), ('bank_line', 'created'), ('transaction', 'linked'),
        ('bank_feed_account', 'created'), ('bank_feed_account', 'updated'), ('transaction', 'created')}
_ACCOUNT_FIELDS = ('bank_number', 'account_last4', 'kind', 'last_import_id')


# ------------------------------------------------------------------ reading

def import_row(s, import_id: str) -> dict:
    row = s.company.conn.execute(sa.select(c.bank_feed_imports).where(
        c.bank_feed_imports.c.id == import_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bank_feed_import', 'selector': import_id})
    return dict(row)


def _import_of_batch(s, batch_id: str) -> dict:
    return dict(s.company.conn.execute(sa.select(c.bank_feed_imports).where(
        c.bank_feed_imports.c.batch_id == batch_id)).mappings().one())


def _family(s, transaction_id: str, kind: str) -> str:
    if kind == 'journal_entry':
        from bookflow.company.money_out import owning_document_kind
        return owning_document_kind(s, transaction_id) or kind
    return kind


def _gone(s, ids: list[str]) -> set[str]:
    """Entries already deleted or voided (by a person, or by an earlier slice): nothing left to take back."""
    from bookflow.core.deletion_families import tombstone_tables
    out = set()
    for chunk in bank_feeds._chunks(ids):
        out |= set(s.company.conn.execute(sa.select(c.transactions.c.id).where(
            c.transactions.c.id.in_(chunk), c.transactions.c.voided_at.is_not(None))).scalars())
        for name in tombstone_tables():
            t = c.metadata.tables[name]
            out |= set(s.company.conn.execute(sa.select(t.c.transaction_id).where(
                t.c.transaction_id.in_(chunk))).scalars())
    return out


def _blocker(code: str, record_type: str, record_id: str, message: str, clears: str | None, **extra) -> dict:
    return {'seq': extra.pop('seq', None), 'record_type': record_type, 'record_id': record_id, 'code': code,
            'message': message, 'clears': clears, **extra}


# ------------------------------------------------------------------ the adapter

def _plan(s, ctx, batch: dict, all_effects: list[dict], done: dict, probe: bool, ordering: list[dict]) -> dict:
    imp = _import_of_batch(s, batch['id'])
    conn = s.company.conn
    unknown = sorted({(e['record_type'], e['action']) for e in all_effects} - _OWN - set(RETIRE))
    if unknown:
        raise BookflowError('E_VALIDATION', message=f'No inverse is known for {unknown[0][0]} {unknown[0][1]} effects.',
                            details={'field': 'batch', 'effects': [list(u) for u in unknown]})
    blockers = []
    # The ordering: a later live import on the account blocks this one, named as the import it is.
    for b in ordering:
        if b.get('code') != 'later_batch' or b.get('record_type') != 'batch':
            blockers.append(b)
            continue
        later = conn.execute(sa.select(c.bank_feed_imports).where(
            c.bank_feed_imports.c.batch_id == b['record_id'])).mappings().first()
        if later is None:
            blockers.append(b)
            continue
        blockers.append(_blocker(
            'later_import', 'bank_feed_import', later['id'],
            f"a later import on this account is still live: import {later['id']} of {later['created_at'][:10]} "
            f"({later['lines_new']} lines, {later['date_from']} to {later['date_to']}).",
            f"`bank-feed reverse {later['id']}` first, or `bank-feed reverse {imp['id']} --through` to reverse the "
            'newer ones with it', batch=b['record_id'], at=later['created_at'], lines=later['lines_new']))
    # Entries the import created (increments 3 to 5): each taken back by its family's own command(s).
    created: dict[str, dict] = {}
    for e in all_effects:
        if e['record_type'] == 'transaction' and e['action'] == 'created':
            created.setdefault(e['record_id'], e)
    gone = _gone(s, list(created))
    pending_ids = [i for i in created if i not in gone]
    rows = {}
    for chunk in bank_feeds._chunks(pending_ids):
        for row in conn.execute(sa.select(c.transactions.c.id, c.transactions.c.version, c.transactions.c.type,
                                          c.transactions.c.number, c.transactions.c.updated_by,
                                          c.transactions.c.updated_at).where(c.transactions.c.id.in_(chunk))).mappings():
            rows[row['id']] = dict(row)
    linked = _linked(s, imp, pending_ids)
    ticked = _ticked(s, pending_ids)
    # ``probe``: True dry-runs every entry's inverse; a number, only that many (the slice this call runs, when a dry
    # run's fingerprint vouches for the rest); False, none.
    budget = None if probe is True else int(probe or 0)
    steps, work, probed, entries = [], [], 0, 0
    for transaction_id in reversed(pending_ids):  # newest first
        e, row = created[transaction_id], rows.get(transaction_id)
        if row is None:
            continue
        family = _family(s, transaction_id, row['type'])
        label = f"{family.replace('_', ' ')} {row['number'] or transaction_id}"
        inverse = INVERSE.get(family, ())
        already = done.get(('transaction', transaction_id), [])
        if inverse and len(already) >= len(inverse):
            continue
        entries += 1
        expected = already[-1]['version_after'] if already else e['version_after']
        if row['version'] != expected:
            blockers.append(_blocker(
                'entry_edited', 'transaction', transaction_id,
                f"{label} was edited since the import made it (version {row['version']}, the import left "
                f"{expected}), by {row['updated_by']} at {row['updated_at']}.",
                'delete it yourself, or leave it and ignore the import', seq=e['seq'],
                changed_by=row['updated_by'], changed_at=row['updated_at']))
            continue
        if transaction_id in linked:
            blockers.append(linked[transaction_id](label, e['seq']))
            continue
        if transaction_id in ticked:
            draft = ticked[transaction_id]
            blockers.append(_blocker(
                'entry_ticked', 'transaction', transaction_id,
                f"{label} is ticked on open reconciliation draft {draft['id']} of {draft['account']}; deleting it "
                'would leave that draft unable to go on.',
                f"unmark it on that draft (`reconcile mark {draft['id']}`, action unmark) or discard the draft "
                f"(`reconcile discard {draft['id']}`)", seq=e['seq'], draft=draft['id'], account=draft['account']))
            continue
        if not inverse:
            blockers.append(_blocker('no_inverse', 'transaction', transaction_id,
                                     f'{label} has no delete or void this reversal can run.',
                                     'delete it through its own command first', seq=e['seq']))
            continue
        left = inverse[len(already):]
        item = {'seq': e['seq'], 'record_type': 'transaction', 'record_id': transaction_id, 'label': label,
                'command': left[0][0], 'inverse': left, 'first_step': len(already), 'version': row['version'],
                'batch_id': batch['id'], 'code_for': CODES, 'clears_for': CLEARS}
        steps.append({'seq': e['seq'], 'record_type': 'transaction', 'record_id': transaction_id, 'name': label,
                      'action': 'created', 'version': row['version'], 'steps': [cmd for cmd, _ in left]})
        work.append(item)
        if budget is None or probed < budget:
            probed += 1
            # The first step is dry-run; a later step depends on it and is checked when it runs.
            try:
                batches.run_nested(s, ctx, left[0][0], _step_payload(item, 0, row['version']),
                                   source_ref=f"reverse:{batch['id']}", dry_run=True, recovery=False)
            except BookflowError as error:
                blockers.append(_blocker(CODES.get(error.code, error.code), 'transaction', transaction_id,
                                         f'{label}: {error.message}', CLEARS.get(error.code), seq=e['seq'],
                                         error=error.code, details=error.details))
    # A line copied into a reconciliation's statement (increment 6 records the copy on `statement_lines`).
    if 'bank_line_id' in c.statement_lines.c:
        copied = conn.execute(sa.select(c.statement_lines.c.bank_line_id, c.statement_lines.c.draft_id).join(
            c.bank_lines, c.bank_lines.c.id == c.statement_lines.c.bank_line_id).where(
            c.bank_lines.c.import_id == imp['id'])).first()
        if copied is not None:
            blockers.append(_blocker('statement_copy', 'bank_line', copied[0],
                                     f'a line of this import is in reconciliation draft {copied[1]}.',
                                     'not clearable while that draft exists', draft=copied[1]))
    lines, matches = _inbox_counts(s, imp['id'])
    inbox = {'seq': 0, 'record_type': 'bank_feed_import', 'record_id': imp['id'], 'name': f"import {imp['id']}",
             'action': 'created', 'version': lines,
             'steps': [f'hide {lines} line(s) and release their keys', f'undo {matches} match(es)',
                       'restore the account record', *(f'retire {t} ({a})' for t, a in sorted(
                           {(e['record_type'], e['action']) for e in all_effects} & set(RETIRE)))]}
    return {'steps': [*steps, inbox], 'work': work, 'blockers': blockers, 'records': len(created) + 1,
            'pending': entries + 1, 'lines': lines, 'matches': matches, 'entries': entries}


def _step_payload(item: dict, k: int, version: int | None) -> dict:
    command, selector = item['inverse'][k]
    return inverse_payload(command, selector, item['batch_id'], item['record_id'], version, item['first_step'] + k)


def _linked(s, imp: dict, ids: list[str]) -> dict:
    """Entries a live line of another import is matched to, each with the blocker it makes. What clears it depends on
    whose line it is: an older import of this account cannot be reversed before this one, so only unmatching helps."""
    conn = s.company.conn
    mine = conn.execute(sa.text('SELECT rowid FROM batches WHERE id = :id'), {'id': imp['batch_id']}).scalar()
    query = sa.text(
        'SELECT l.entry_id, l.import_id, l.id, l.account_id, b.rowid AS at FROM bank_lines l '
        'JOIN bank_feed_imports i ON i.id = l.import_id JOIN batches b ON b.id = i.batch_id '
        'WHERE l.live = 1 AND l.import_id != :mine AND l.entry_id IN :ids').bindparams(
        sa.bindparam('ids', expanding=True))
    out: dict = {}
    for chunk in bank_feeds._chunks(ids):
        for row in conn.execute(query, {'mine': imp['id'], 'ids': list(chunk)}).mappings():
            if row['entry_id'] in out:
                continue
            other, line = row['import_id'], row['id']
            unmatch = f'`bank-line unmatch {line}` (the entry stays, the line goes back to waiting)'
            if row['account_id'] == imp['account_id'] and row['at'] < mine:
                where = (f'a line of import {other}, an older import of this account, which cannot be reversed '
                         'while this one is live')
                clears = f'{unmatch} first'
            elif row['account_id'] == imp['account_id']:
                where = f'a line of import {other}, a newer import of this account'
                clears = (f"`bank-feed reverse {imp['id']} --through` reverses that newer import with it, or "
                          f'{unmatch} first')
            else:
                where = f"a line of {bank_feeds._full_name(s, row['account_id'])}'s live import {other}"
                clears = f'`bank-feed reverse {other}` first (newest first on its account), or {unmatch} first'

            def make(label, seq, where=where, clears=clears, other=other, line=line, entry=row['entry_id']):
                return _blocker('entry_linked', 'transaction', entry, f'{label} is matched by {where}.', clears,
                                seq=seq, import_id=other, line=line)
            out[row['entry_id']] = make
    return out


def _ticked(s, ids: list[str]) -> dict[str, dict]:
    """Entries ticked on an open reconciliation draft (any account), by the draft: deleting one leaves the draft
    refusing to go on (the tick goes stale and cannot be unmarked), so the person clears it there first."""
    t = c.metadata.tables
    drafts, members = t['reconciliation_drafts'], t['reconciliation_draft_members']
    sources = t['reconciliation_effect_sources']
    out: dict[str, dict] = {}
    for chunk in bank_feeds._chunks(ids):
        for transaction_id, draft_id, account_id in s.company.conn.execute(
                sa.select(sources.c.transaction_id, drafts.c.id, drafts.c.account_id).select_from(
                    members.join(drafts, sa.and_(drafts.c.id == members.c.draft_id,
                                                 drafts.c.current_revision_id == members.c.revision_id))
                    .join(sources, sources.c.version_id == members.c.version_id))
                .where(drafts.c.state == 'open', sources.c.transaction_id.in_(chunk))).all():
            out.setdefault(transaction_id, {'id': draft_id, 'account': bank_feeds._full_name(s, account_id)})
    return out


def _inbox_counts(s, import_id: str) -> tuple[int, int]:
    lines, matches = s.company.conn.execute(sa.select(
        sa.func.count(), sa.func.count(c.bank_lines.c.entry_id)).where(
        c.bank_lines.c.import_id == import_id, c.bank_lines.c.live == 1)).one()
    return int(lines), int(matches)


def _run(s, ctx, batch: dict, reversal_id: str, item: dict) -> None:
    """Take back one created entry: each remaining inverse step, each its own command, recorded as it commits."""
    from bookflow.core import registry
    version = item['version']
    for k, (command, _) in enumerate(item['inverse']):
        def record(s, cmd, applied, output, step=item['first_step'] + k):
            batches.record_effect(s, reversal_id, record_type='transaction', record_id=item['record_id'],
                                  action='reversed', version_after=output.get('version') if isinstance(output, dict)
                                  else None, before={'of_seq': item['seq'], 'step': step, 'command': cmd.name},
                                  event_id=batches.last_event_id(s))
        payload = _step_payload(item, k, version)
        if 'dependency_guard' in registry.get(command).input_model.model_fields:
            # A command that saves only against its own preview's guard (`deposit delete`) is previewed first, in
            # this same call, and saved with that guard.
            preview = batches.run_nested(s, ctx, command, dict(payload), source_ref=f"reverse:{batch['id']}",
                                         dry_run=True, recovery=False)
            payload['dependency_guard'] = preview['dependency_guard']
        output = batches.run_nested(s, ctx, command, payload, source_ref=f"reverse:{batch['id']}", recorder=record,
                                    recovery=False)
        version = output.get('version') if isinstance(output, dict) else None


def _release(s, batch: dict) -> None:
    """In the transaction that marks the import reversed: hide its lines (releasing their keys), undo its matches,
    restore the account record, retire what later increments learned from it."""
    conn = s.company.conn
    imp = _import_of_batch(s, batch['id'])
    reversal_id = batch['reversed_by']
    via = conn.execute(sa.select(c.batches.c.via).where(c.batches.c.id == reversal_id)).scalar()
    event = batches.last_event_id(s)
    at = batches.now()
    actor = s.actor.id
    found = conn.execute(sa.select(c.bank_lines.c.id, c.bank_lines.c.state, c.bank_lines.c.entry_id).where(
        c.bank_lines.c.import_id == imp['id'], c.bank_lines.c.live == 1)).mappings().all()
    conn.execute(c.bank_lines.update().where(c.bank_lines.c.import_id == imp['id'], c.bank_lines.c.live == 1)
                 .values(live=0, version=c.bank_lines.c.version + 1))
    rows = [dict(id=new_id(), line_id=v['id'], kind='reversed', import_id=imp['id'], batch_id=reversal_id,
                 details=json.dumps({'state': v['state'], 'entry_unlinked': v['entry_id'],
                                     'reason': batch.get('reversal_reason')}, sort_keys=True),
                 created_by=actor, created_via=via, created_at=at, audit_event_id=event) for v in found]
    for chunk in bank_feeds._chunks(rows):
        conn.execute(c.bank_line_events.insert(), chunk)
    effects = batches.effects(s, batch['id'])
    for e in effects:
        if (e['record_type'], e['action']) == ('transaction', 'linked'):
            batches.record_effect(s, reversal_id, record_type='transaction', record_id=e['record_id'],
                                  action='reversed', version_after=None, before={'of_seq': e['seq'], 'unlinked': True},
                                  event_id=event)
    account = next((e for e in effects if e['record_type'] == 'bank_feed_account'), None)
    if account is not None:
        before = json.loads(account['before']) if account['before'] else dict.fromkeys(_ACCOUNT_FIELDS)
        version = conn.execute(sa.select(c.bank_feed_accounts.c.version).where(
            c.bank_feed_accounts.c.account_id == account['record_id'])).scalar()
        conn.execute(c.bank_feed_accounts.update().where(
            c.bank_feed_accounts.c.account_id == account['record_id']).values(
            **{k: before.get(k) for k in _ACCOUNT_FIELDS}, updated_at=at, version=version + 1))
        batches.record_effect(s, reversal_id, record_type='bank_feed_account', record_id=account['record_id'],
                              action='reversed', version_after=version + 1,
                              before={'of_seq': account['seq'], 'restored': {k: before.get(k) for k in _ACCOUNT_FIELDS}},
                              event_id=event)
    for e in effects:
        retire = RETIRE.get((e['record_type'], e['action']))
        if retire is not None:
            outcome = retire(s, e, batch, reversal_id)
            batches.record_effect(s, reversal_id, record_type=e['record_type'], record_id=e['record_id'],
                                  action='reversed', version_after=None, before={'of_seq': e['seq'], 'outcome': outcome},
                                  event_id=event)
    batches.record_effect(s, reversal_id, record_type='bank_feed_import', record_id=imp['id'], action='reversed',
                          version_after=None, before={'lines': len(found),
                                                      'matches': sum(1 for v in found if v['entry_id'])},
                          event_id=event)


def _read(s, batch: dict) -> None:
    batches.require_read(s, 'bank-feed show')


batches.register(batches.Adapter(bank_feeds.BATCH_KIND, 'strict', on_finished=_release, label='Bank import',
                                 planner=_plan, runner=_run, reader=_read, blocks=True))


# ------------------------------------------------------------------ bank-feed reverse

def _chain(s, imp: dict, through: bool) -> list[dict]:
    """The imports to reverse, newest first: this one, and with ``through`` every later live import of its account."""
    batch = batches._batch(s, imp['batch_id'])
    if batch['state'] == 'reversed':
        raise BookflowError('E_VALIDATION', message=f"Import {imp['id']} was already reversed.",
                            details={'field': 'import_id', 'state': 'reversed'})
    chain = [imp]
    if through:
        later = s.company.conn.execute(sa.text(
            "SELECT i.* FROM bank_feed_imports i JOIN batches b ON b.id = i.batch_id WHERE b.scope = :scope "
            "AND b.reversal_of IS NULL AND b.state IN ('live', 'reversing', 'blocked') "
            "AND b.rowid > (SELECT rowid FROM batches WHERE id = :id) ORDER BY b.rowid DESC"),
            {'scope': batch['scope'], 'id': batch['id']}).mappings().all()
        chain = [dict(v) for v in later] + chain
    return chain


def _plans(s, ctx, chain: list[dict], probe) -> list[dict]:
    """Each import's plan, newest first. ``probe`` as for the planner; a number is shared out over the chain in the
    order the entries will be taken back (the slice this call runs)."""
    batch_ids = {imp['batch_id'] for imp in chain}
    import_ids = {imp['id'] for imp in chain}
    plans = []
    for imp in chain:
        found = batches.plan(s, ctx, imp['batch_id'], probe=probe)
        # A later import this same call reverses first is not a blocker, nor a line of one matched to an entry here
        # (its reversal, first, undoes that match).
        found['blockers'] = [b for b in found['blockers'] if not (
            (b['code'] == 'later_import' and b.get('batch') in batch_ids)
            or (b['code'] == 'entry_linked' and b.get('import_id') in import_ids))]
        if probe is not True and probe:
            probe = max(0, probe - len(found['work']))
        plans.append(found)
    return plans


def _fingerprint(plans: list[dict]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps([p['fingerprint'] for p in plans]).encode()).hexdigest()


def _out(plans, chain, *, state, reversed_now=0, summary='', next_=None, fingerprint=None) -> m.ReverseOutput:
    blockers = [b for p in plans for b in p['blockers']]
    steps = [x for p in plans for x in p['steps']]
    return m.ReverseOutput(
        imports=tuple(i['id'] for i in chain), state=state,
        lines=sum(p['lines'] for p in plans), matches=sum(p['matches'] for p in plans),
        entries=sum(p['entries'] for p in plans), reversed_now=reversed_now, remaining=len(steps),
        steps=tuple(steps[:batches.MAX_BLOCKERS]), blockers=tuple(_jsonable(b) for b in blockers[:batches.MAX_BLOCKERS]),
        total_blockers=len(blockers), fingerprint=fingerprint,
        reversal_batches=tuple(p['batch']['reversed_by'] for p in plans if p['batch'].get('reversed_by')),
        summary=summary, next=next_)


def _jsonable(blocker: dict) -> dict:
    return json.loads(json.dumps(blocker, default=str))


def _refuse(plans: list[dict], chain: list[dict]) -> BookflowError:
    blockers = [b for p in plans for b in p['blockers']]
    first = blockers[0]
    return BookflowError('E_BATCH_BLOCKED', message=(
        f"Import {chain[-1]['id']} cannot be reversed: {len(blockers)} blocker{'s' if len(blockers) != 1 else ''}, "
        f"first {first['code']}: {first['message']} Nothing was changed."),
        details={'import': chain[-1]['id'], 'blockers': [_jsonable(b) for b in blockers[:batches.MAX_BLOCKERS]],
                 'total': len(blockers), 'state': plans[-1]['batch']['state']})


def _summary(plans) -> str:
    lines, matches = sum(p['lines'] for p in plans), sum(p['matches'] for p in plans)
    entries = sum(p['entries'] for p in plans)
    return (f"{len(plans)} import(s): hide {lines} line(s) and release their keys, undo {matches} match(es), "
            f"delete {entries} created entr{'y' if entries == 1 else 'ies'}, restore the account record")


def reverse(s, ctx, inp, *, dry_run: bool, then=None, command: str | None = None, on_begin=None) -> dict:
    """Reverse an import (and, with ``through``, the later ones first). Returns the output and, when ``then`` ran,
    its result. ``on_begin(s, reversal_id)`` runs in the transaction that opens the import's reversal."""
    imp = import_row(s, inp.import_id)
    chain = _chain(s, imp, getattr(inp, 'through', False))
    starting = all(batches._batch(s, i['batch_id'])['state'] == 'live' for i in chain)
    # The whole plan is probed by a dry run, or by a first call that comes without one; a first call carrying a dry
    # run's fingerprint probes only the slice it runs (the fingerprint vouches for the rest, and a refusal met later
    # stops that slice `blocked`, naming it). A later slice probes nothing: its first refusal stops it the same way.
    probe = True if dry_run else ((inp.limit if inp.fingerprint is not None else True) if starting else False)
    plans = _plans(s, ctx, chain, probe=probe)
    if any(p['blockers'] for p in plans):
        if not dry_run:
            # A reversal under way that meets a blocker stops `blocked`, naming it (the ledger marks it).
            for i in chain:
                if batches._batch(s, i['batch_id'])['state'] == 'reversing':
                    batches._set_state(s, i['batch_id'], 'blocked')
        plans = [dict(p, batch=batches._batch(s, p['batch']['id'])) for p in plans]
        raise _refuse(plans, chain)
    fingerprint = _fingerprint(plans)
    again = command or f"bank-feed reverse {inp.import_id}" + (' --through' if getattr(inp, 'through', False) else '')
    if dry_run:
        under_way = next((p for p in (progress(s, i) for i in chain) if p is not None), None)
        summary = 'Dry run: ' + _summary(plans) + '. Nothing was changed.'
        if under_way is not None:
            summary = f"{under_way['summary']} Dry run of finishing it: " + _summary(plans) + '. Nothing was changed.'
        return {'out': _out(plans, chain, state=batches._batch(s, imp['batch_id'])['state'], summary=summary,
                            fingerprint=fingerprint, next_=f'{again} --fingerprint {fingerprint}'), 'then': None}
    if inp.fingerprint is not None and inp.fingerprint != fingerprint:
        raise BookflowError('E_VERSION_CONFLICT', message='The import changed since that dry run; run it again.',
                            details={'field': 'fingerprint', 'current': fingerprint})
    left, done_now, then_out = inp.limit, 0, None
    for i, (item, found) in enumerate(zip(chain, plans)):
        last = i == len(chain) - 1
        result = batches.reverse(s, ctx, item['batch_id'], reason=ctx.reason, limit=left,
                                 fingerprint=found['fingerprint'], then=then if last else None,
                                 on_begin=on_begin if last else None)
        done_now += result['reversed_now']
        left = max(1, left - result['reversed_now'])
        if result['state'] != 'reversed':
            rest = _plans(s, ctx, chain[i:], probe=False)
            return {'out': _out(rest, chain[i:], state='reversing', reversed_now=done_now, next_=again, summary=(
                f"Reversing: {done_now} entr{'y' if done_now == 1 else 'ies'} deleted in this slice; run "
                f"`{again}` to go on (the account takes no new import until it finishes).")), 'then': None}
        then_out = result.get('then')
    final = [dict(p, batch=batches._batch(s, p['batch']['id'])) for p in plans]
    return {'out': _out(final, chain, state='reversed', reversed_now=done_now,
                        summary='Reversed ' + _summary(plans) + '.'), 'then': then_out}


def progress(s, imp: dict) -> dict | None:
    """A reversal (or move) of this import under way or stopped, as `bank-feed show` and a refusal report it: what
    is done, what is left, and the command that finishes it. None when none is."""
    batch = batches._batch(s, imp['batch_id'])
    if batch['state'] not in ('reversing', 'blocked'):
        return None
    created = list(dict.fromkeys(e['record_id'] for e in batches.effects(s, imp['batch_id'])
                                 if e['record_type'] == 'transaction' and e['action'] == 'created'))
    left = len([i for i in created if i not in _gone(s, created)])
    lines, matches = _inbox_counts(s, imp['id'])
    move = _move_record(s, imp)
    source = bank_feeds._full_name(s, imp['account_id'])
    to = bank_feeds._full_name(s, move['to']) if move else None
    finish = f"bank-feed move {imp['id']} --to {move['to']}" if move else f"bank-feed reverse {imp['id']}"
    what = f'moved to {to}' if move else 'reversed'
    summary = (f"Import {imp['id']} is part way {what} ({batch['state']}): {len(created) - left} of {len(created)} "
               f"entr{'y' if len(created) == 1 else 'ies'} it created taken back; its {lines} line(s) and {matches} "
               f"match(es) stay in {source} until the last step"
               + (f', which imports them into {to}' if move else ', which hides them') +
               f"; {source} takes no new import meanwhile. "
               + ('Once what blocks it is cleared, ' if batch['state'] == 'blocked' else '')
               + f'`{finish}` finishes it' + (' (the flags it began with are kept).' if move else '.'))
    return {'state': batch['state'], 'entries': len(created), 'entries_done': len(created) - left,
            'entries_left': left, 'lines': lines, 'matches': matches, 'move_to': move['to'] if move else None,
            'move_to_name': to, 'next': finish, 'summary': summary}


# ------------------------------------------------------------------ bank-feed move

def _stored(s, imp: dict) -> dict:
    """The reversed import's own lines as a parsed statement, its digest and the account number it carried."""
    rows = s.company.conn.execute(sa.select(c.bank_lines).where(c.bank_lines.c.import_id == imp['id'])
                                  .order_by(c.bank_lines.c.ordinal)).mappings().all()
    lines = tuple(files.Line(line_id=f"fitid:{v['fitid']}" if v['fitid'] else v['line_key'], date=v['date'],
                             amount=v['amount'], payee=v['payee'], memo=v['memo'], number=v['number'],
                             fitid=v['fitid'], kind=v['kind']) for v in rows)
    currency = s.company.conn.execute(sa.select(c.accounts.c.currency).where(
        c.accounts.c.id == imp['account_id'])).scalar()
    hint = None
    if imp['format'] in ('ofx', 'qfx'):
        # What the source account holds now is what this import left (it is its newest live import): the number a
        # first or confirmed file taught it, or the same number it already had.
        known = s.company.conn.execute(sa.select(c.bank_feed_accounts).where(
            c.bank_feed_accounts.c.account_id == imp['account_id'],
            c.bank_feed_accounts.c.last_import_id == imp['id'])).mappings().first()
        if known is not None and known['account_last4']:
            hint = {'bank_number': known['bank_number'], 'account_last4': known['account_last4'],
                    'kind': known['kind'] or 'bank'}
    # The number this import taught the source (a first or confirmed file) goes back with the reversal: the source
    # does not hold it for the target's check. A number it already had stays its own.
    taught_to = None
    if hint is not None:
        effect = s.company.conn.execute(sa.select(c.batch_effects.c.before).where(
            c.batch_effects.c.batch_id == imp['batch_id'],
            c.batch_effects.c.record_type == 'bank_feed_account')).first()
        before = json.loads(effect[0]) if effect is not None and effect[0] else {}
        if (before.get('account_last4'), before.get('bank_number')) != (hint['account_last4'], hint['bank_number']):
            taught_to = imp['account_id']
    return {'parsed': files.Statement(format=imp['format'], lines=lines, currency=currency),
            'sha': imp['file_sha256'], 'hint': hint, 'taught_to': taught_to}


def _target_input(inp, target: str) -> m.ImportInput:
    return m.ImportInput(account=target, content='-', again=True, auto_match=inp.auto_match,
                         match_days=inp.match_days, suggest_days=inp.suggest_days,
                         confirm_account=inp.confirm_account, account_confirmed=inp.account_confirmed, limit=100)


# A move's own flags, kept with its reversal (a `bank_feed_move` `linked` effect of the reversal batch, written in the
# transaction that begins it) so a move that stops after a slice resumes as it began without them given again.
_MOVE_FLAGS = ('confirm_account', 'account_confirmed', 'auto_match', 'match_days', 'suggest_days')


def _move_record(s, imp: dict) -> dict | None:
    reversal = batches._batch(s, imp['batch_id'])['reversed_by']
    if reversal is None:
        return None
    row = s.company.conn.execute(sa.select(c.batch_effects.c.record_id, c.batch_effects.c.before).where(
        c.batch_effects.c.batch_id == reversal, c.batch_effects.c.record_type == 'bank_feed_move')).first()
    return {**json.loads(row[1]), 'to': row[0]} if row is not None else None


def _flags(s, imp: dict, inp, target: dict):
    """The flags this call runs with: a move under way keeps those it began with; a flag given now (other than its
    default) goes on top. A move under way into another account is refused, naming the one it goes to."""
    stored = _move_record(s, imp)
    given = {k: getattr(inp, k) for k in _MOVE_FLAGS}
    if stored is None:
        return inp
    if stored['to'] != target['id']:
        raise files.invalid('to', (
            f"import {imp['id']} is part way moved to {bank_feeds._full_name(s, stored['to'])}; "
            f"`bank-feed move {imp['id']} --to {stored['to']}` finishes that move (or `bank-feed reverse {imp['id']}` "
            'only reverses it)'))
    default = {k: m.MoveInput.model_fields[k].default for k in _MOVE_FLAGS}
    return inp.model_copy(update={k: given[k] if given[k] != default[k] else stored.get(k, default[k])
                                  for k in _MOVE_FLAGS})


def _half_done(s, imp: dict, error: BookflowError) -> BookflowError:
    """A refusal met while a move is part way through says so, and how to finish it."""
    under_way = progress(s, imp)
    if under_way is None:
        return error
    return BookflowError(error.code, message=f'{error.message} {under_way["summary"]}',
                         details={**(error.details or {}), 'part_way': under_way})


def move(s, ctx, inp, *, dry_run: bool) -> m.MoveOutput:
    imp = import_row(s, inp.import_id)
    try:
        return _move(s, ctx, imp, inp, dry_run=dry_run)
    except BookflowError as error:
        raise _half_done(s, imp, error) from None


def _move(s, ctx, imp: dict, inp, *, dry_run: bool) -> m.MoveOutput:
    target = bank_feeds._account(s, inp.to)
    source = bank_feeds._account(s, imp['account_id'])
    if target['id'] == imp['account_id']:
        raise files.invalid('to', 'the import is already in that account')
    if target['currency'] != source['currency']:
        raise files.invalid('to', f"{bank_feeds._full_name(s, target['id'])} is in {target['currency']}; the "
                                  f"import's lines are in {source['currency']}")
    inp = _flags(s, imp, inp, target)
    stored = _stored(s, imp)
    if not stored['parsed'].lines:
        raise files.invalid('import_id', 'the import holds no lines to move')
    target_inp = _target_input(inp, target['id'])
    command = f"bank-feed move {inp.import_id} --to {target['id']}"
    # Checked whole on every call, before anything (more) is reversed: the target refuses (account check, currency)
    # while the source still holds the lines.
    preview = bank_feeds.prepare(s, ctx, target_inp, stored=stored, exclude_import=imp['id'])
    if dry_run:
        reversal = reverse(s, ctx, inp, dry_run=True, command=command)['out']
        return m.MoveOutput(reversal=reversal, target=bank_feeds.output(preview, dry_run=True), next=reversal.next)

    def reimport():
        prepared = bank_feeds.prepare(s, ctx, target_inp, stored=stored)
        out, _ = bank_feeds.write(s, ctx, prepared, moved_from=imp['id'])
        return out

    def begin(s, reversal_id):
        batches.record_effect(s, reversal_id, record_type='bank_feed_move', record_id=target['id'], action='linked',
                              version_after=None, before={k: getattr(inp, k) for k in _MOVE_FLAGS}, event_id=None)

    done = reverse(s, ctx, inp, dry_run=False, then=reimport, command=command, on_begin=begin)
    out = done['out']
    if out.state != 'reversed':
        return m.MoveOutput(reversal=out, target=None, next=command)
    new = done['then']
    return m.MoveOutput(reversal=out.model_copy(update={'summary': out.summary + (
        f" Moved to {bank_feeds._full_name(s, target['id'])} as import {new.import_id}.")}), target=new,
        next=f'bank-feed show {new.import_id}' if new.import_id else None)
