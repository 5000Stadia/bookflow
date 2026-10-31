"""Sorting the bank inbox: match, add, ignore, transfers (R108 increment 3, design/bank-feeds.md §2, §4, §9).

A waiting line is **matched** to an entry already in the books (never changing it), **added** (its entry made by the
account's own command, run as the caller, so it is exactly the entry typed in by hand: `check post` for money out of a
bank account, `deposit post` for money in, `card-charge post` and `card-credit post` on a card), **ignored** with a
reason, or paired as a **transfer** (two lines of two accounts linked to one `transfer` entry, matched or posted with
`transfer post`; or one line sent to a named account now, its other end matching that entry when it comes in).
`bank-line unmatch` returns a line to waiting: a match, an ignore, or an added line whose entry was since deleted.

**Everything is recorded for reversal.** A sort act records each line it changed (`bank_line` `sorted`, with the
line's link columns before), each entry it made (`transaction` `created`) and each entry it linked (`transaction`
`linked`) on the line's own import batch, so reversing the import deletes the entries its lines were added as (through
increment 2's adapter) and undoes the matches. A `--lines` act is also its own batch (kind `bank-sort`, the same
effects), undone together by `bank-line undo`: the entries it made deleted by their family's own command, its lines
returned to how they stood. Nothing is guessed: an add without a payee or account asks; two candidates that fit equally
are listed, never chosen; amounts and dates are the line's exactly; a closed period refuses with the command's own code.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import bank_feeds, bank_reversal, batches, schema as c, statement_files as files
from bookflow.company import bank_feed_models as m
from bookflow.core.ids import new_id

SORT_KIND = 'bank-sort'
SORT_SCOPE = 'bank-inbox'
# Days apart the two lines of one transfer may be (design/bank-feeds.md §4 and §12, default 5): money leaves one
# account and arrives in the other within a few business days.
TRANSFER_DAYS = 5
# Days either side a one-sided transfer already posted from the other account is looked for, when that account's line
# comes in later than TRANSFER_DAYS (a cheque-like transfer can take weeks to show on the other statement): it is
# completed, never posted twice.
LATE_TRANSFER_DAYS = 30
_LINK = ('state', 'entry_id', 'movement', 'link_how', 'match_reason', 'created', 'payee_display', 'ignore_reason',
         'entry_date', 'answer')
_CLEAR = dict(state='waiting', entry_id=None, movement=None, link_how=None, match_reason=None, created=0,
              payee_display=None, ignore_reason=None, entry_date=None, answer=None)
# What a line's sort is, compared by an undo: the state and link, not the wording around it.
_IDENT = ('state', 'entry_id', 'created', 'ignore_reason')
_LINKED = ('matched', 'settled', 'transfer', 'added')
_NOUN = {'vendor': 'vendor', 'customer': 'customer', 'employee': 'employee', 'other_name': 'other-name'}


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _refuse(field: str, message: str, **details) -> BookflowError:
    return BookflowError('E_VALIDATION', message=message, details={
        'field': field, 'fields': [{'field': field, 'problem': message}], **details})


# ------------------------------------------------------------------ reading lines

def lines(s, ids) -> list[dict]:
    found = {}
    for chunk in bank_feeds._chunks(list(dict.fromkeys(ids))):
        for row in s.company.conn.execute(sa.select(c.bank_lines).where(c.bank_lines.c.id.in_(chunk))).mappings():
            found[row['id']] = dict(row)
    for line_id in ids:
        if line_id not in found:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bank_line', 'selector': line_id})
    return [found[i] for i in ids]


def _account(s, account_id: str) -> dict:
    return dict(s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == account_id)).mappings().one())


def _label(s, line: dict) -> str:
    places = bank_feeds._currency_places(s, line['account_id'])
    return (f"line {line['id']} ({line['date']}, {bank_feeds.decimal(line['amount'], places)}, "
            f"{(line['payee'] or line['memo'] or '').strip()!r})")


def _import_batch(s, line: dict) -> dict:
    return dict(s.company.conn.execute(sa.select(c.batches.c.id, c.batches.c.state).join(
        c.bank_feed_imports, c.bank_feed_imports.c.batch_id == c.batches.c.id).where(
        c.bank_feed_imports.c.id == line['import_id'])).mappings().one())


def _sortable(s, line: dict, states=('waiting',)) -> str:
    """The line's import batch, once the line can be sorted: live, its import standing, no undo of a batch over it part
    way, in one of ``states``."""
    if not line['live']:
        raise _refuse('line', f"{_label(s, line)} went with its import's reversal; it can no longer be sorted.",
                      line=line['id'])
    batch = _import_batch(s, line)
    if batch['state'] != 'live':
        raise _refuse('line', f"{_label(s, line)} belongs to import {line['import_id']}, which is being reversed "
                              f"({batch['state']}); `bank-feed reverse {line['import_id']}` finishes it.",
                      line=line['id'], state=batch['state'])
    undoing = _undoing(s, line['id'])
    if undoing is not None:
        raise _refuse('line', f"{_label(s, line)}: undo in progress; finish it with `bank-line undo {undoing}` (its "
                              'lines stay as they are until it ends).', line=line['id'], batch=undoing)
    if line['state'] not in states:
        raise _refuse('line', f"{_label(s, line)} is {line['state']}" + (
            f'; {_way_back(s, line)}.' if line['state'] != 'waiting' else '.'),
            line=line['id'], state=line['state'])
    return batch['id']


def _undoing(s, line_id: str) -> str | None:
    """A bank-sort batch over this line whose undo is part way (reversing, or blocked part way): the line is frozen."""
    return s.company.conn.execute(sa.text(
        "SELECT b.id FROM batch_effects e JOIN batches b ON b.id = e.batch_id WHERE e.record_type = 'bank_line' "
        "AND e.record_id = :line AND b.kind = :kind AND b.state IN ('reversing', 'blocked') LIMIT 1"),
        {'line': line_id, 'kind': SORT_KIND}).scalar()


def _sort_batch_of(s, line: dict) -> str | None:
    """The live bank-sort batch that last sorted this line as it now stands (its undo takes the act back)."""
    return s.company.conn.execute(sa.text(
        "SELECT b.id FROM batch_effects e JOIN batches b ON b.id = e.batch_id WHERE e.record_type = 'bank_line' "
        "AND e.record_id = :line AND e.version_after = :version AND b.kind = :kind AND b.state = 'live' LIMIT 1"),
        {'line': line['id'], 'version': line['version'], 'kind': SORT_KIND}).scalar()


def _way_back(s, line: dict) -> str:
    """How this line returns to waiting, as it stands now: the one command (or two) that will do it."""
    if (line['created'] or line['state'] == 'added') and line['entry_id'] and \
            line['id'] not in entry_trouble(s, [line]):
        kind = s.company.conn.execute(sa.select(c.transactions.c.type).where(
            c.transactions.c.id == line['entry_id'])).scalar() or 'entry'
        family = bank_reversal._family(s, line['entry_id'], kind)
        inverse = ' then '.join(f'`{cmd}`' for cmd, _ in bank_reversal.INVERSE.get(family, ())) or 'its own command'
        sort_id = _sort_batch_of(s, line)
        return (f"it was entered as {family.replace('_', ' ')} {line['entry_id']}: "
                + (f'`bank-line undo {sort_id}` takes back its batch, or ' if sort_id else '')
                + f"delete that entry ({inverse}) and then `bank-line unmatch --line {line['id']}` releases it, or "
                f"reverse its import (`bank-feed reverse {line['import_id']}`)")
    return f"`bank-line unmatch --line {line['id']}` returns it to waiting first"


def entry_trouble(s, rows) -> dict[str, str]:
    """Each linked line whose entry is no longer what it was linked to (design §2, `entry_gone`): voided, deleted, or
    changed in amount or date on the line's account; with the reason. Read for many lines in a few queries."""
    linked = [r for r in rows if r.get('entry_id') and r.get('live', 1) and r.get('state') in _LINKED]
    if not linked:
        return {}
    from bookflow.company.reconciliation_preparation import groups, statement_amount
    conn = s.company.conn
    ids = sorted({r['entry_id'] for r in linked})
    present, voided = set(), set()
    for chunk in bank_feeds._chunks(ids):
        for tid, at in conn.execute(sa.select(c.transactions.c.id, c.transactions.c.voided_at).where(
                c.transactions.c.id.in_(chunk))).all():
            present.add(tid)
            if at is not None:
                voided.add(tid)
    from bookflow.core.deletion_families import tombstone_tables
    deleted = set(ids) - present
    for name in tombstone_tables():
        table = c.metadata.tables[name]
        for chunk in bank_feeds._chunks(ids):
            deleted |= set(conn.execute(sa.select(table.c.transaction_id).where(
                table.c.transaction_id.in_(chunk))).scalars())
    voided -= deleted
    t = c.metadata.tables
    versions, heads = t['reconciliation_effect_versions'], t['reconciliation_effect_heads']
    moves: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for chunk in bank_feeds._chunks([i for i in ids if i not in deleted and i not in voided]):
        values = [dict(v) for v in conn.execute(sa.select(
            versions.c.key_id, versions.c.transaction_id, versions.c.account_id, versions.c.account_type,
            versions.c.effective_date, versions.c.signed_debit, versions.c.movement_snapshot).select_from(
            versions.join(heads, heads.c.version_id == versions.c.id)).where(
            versions.c.transaction_id.in_(chunk), versions.c.active == 1)).mappings()]
        for group in groups(values).values():
            first = group[0]
            moves.setdefault((first['transaction_id'], first['account_id']), []).append(
                (first['effective_date'], sum(statement_amount(v) for v in group)))
    kinds = {}
    for chunk in bank_feeds._chunks(sorted({r['account_id'] for r in linked})):
        kinds.update(conn.execute(sa.select(c.accounts.c.id, c.accounts.c.type).where(c.accounts.c.id.in_(chunk))).all())
    out = {}
    for r in linked:
        e = r['entry_id']
        if e in deleted:
            out[r['id']] = f'entry {e} was deleted'
        elif e in voided:
            out[r['id']] = f'entry {e} was voided'
        else:
            signed = -r['amount'] if kinds.get(r['account_id']) == 'credit_card' else r['amount']
            found = moves.get((e, r['account_id']), [])
            days = [d for d, a in found if a == signed]
            if not days:
                places = bank_feeds._currency_places(s, r['account_id'])
                now = ', '.join(bank_feeds.decimal(abs(a), places) for _, a in found) or 'nothing'
                out[r['id']] = (f"entry {e} was changed: it moves this account by {now}, not the line's "
                                f"{bank_feeds.decimal(abs(r['amount']), places)}")
            elif r.get('entry_date') and r['entry_date'] not in days:
                out[r['id']] = f"entry {e} was changed: it is dated {days[0]}, not {r['entry_date']}"
    return out


def _how(s) -> str:
    return 'agent' if getattr(s.actor, 'kind', None) == 'agent' else 'person'


def _linked_entries(s, account_id: str | None = None, *, besides=()) -> set[str]:
    q = sa.select(c.bank_lines.c.entry_id).where(c.bank_lines.c.live == 1, c.bank_lines.c.entry_id.is_not(None))
    if account_id is not None:
        q = q.where(c.bank_lines.c.account_id == account_id)
    if besides:
        q = q.where(c.bank_lines.c.id.not_in(list(besides)))
    return set(s.company.conn.execute(q).scalars())


def _gone(s, entry_id: str) -> bool:
    return bool(bank_reversal._gone(s, [entry_id]))


# ------------------------------------------------------------------ writing a line

def _via(ctx) -> str:
    return str(getattr(ctx.interface, 'value', ctx.interface))[:16]


def _write_line(s, ctx, line: dict, after: dict, *, kind: str, details: dict, event_id: str, batch_ids: list[str],
                extra=()) -> dict:
    """Change one line's link columns (inside the caller's transaction), append its event, and record the change on
    each batch (its import's, and a --lines act's own): the line as it was, and each entry made or linked."""
    conn = s.company.conn
    # Fetched pages persist pending-rule provenance before acting, so recovery can
    # resume after a crash. A completed decision must consume that marker in the
    # same transaction; unmatching it later is a person's decision to wait.
    if after.get('state') != 'waiting':
        proposal = json.loads(after.get('proposal', line.get('proposal')) or '{}')
        if (proposal.get('rule') or {}).pop('pending', None) is not None:
            after = {**after, 'proposal': json.dumps(proposal, sort_keys=True)}
    done = conn.execute(c.bank_lines.update().where(
        c.bank_lines.c.id == line['id'], c.bank_lines.c.version == line['version'], c.bank_lines.c.live == 1)
        .values(**after, version=line['version'] + 1))
    if done.rowcount != 1:
        raise BookflowError('E_VERSION_CONFLICT', message=f"Line {line['id']} changed meanwhile; read it again.",
                            details={'line': line['id']})
    before = {k: line.get(k) for k in _LINK}
    before['version'] = line['version']
    # What the act left, so an undo compares the line with exactly that (not with an ever-growing version).
    before['after'] = {k: after.get(k, _CLEAR.get(k)) for k in _IDENT}
    for batch_id in batch_ids:
        batches.record_effect(s, batch_id, record_type='bank_line', record_id=line['id'], action='updated',
                              version_after=line['version'] + 1, before=before, event_id=event_id)
        for record_type, record_id, action, version in extra:
            batches.record_effect(s, batch_id, record_type=record_type, record_id=record_id, action=action,
                                  version_after=version, event_id=event_id)
    conn.execute(c.bank_line_events.insert().values(
        id=new_id(), line_id=line['id'], kind=kind, import_id=line['import_id'], batch_id=batch_ids[-1],
        details=json.dumps(details, sort_keys=True, default=str), created_by=s.actor.id, created_via=_via(ctx),
        created_at=batches.now(), audit_event_id=event_id))
    return {**line, **after, 'version': line['version'] + 1}


def _in_tx(s, work):
    with s.commits.operation(batches.OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            out = work()
            s.commits.commit(s.company, batches.OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return out


def _audit(s, ctx, command: str, summary: str, changes: list[tuple[dict, dict]]) -> str:
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    touched = [Touched('bank_line', line['id'], 'update', line['version'], line['version'] + 1,
                       {'state': after['state'], 'entry_id': after.get('entry_id')},
                       {'state': line['state'], 'entry_id': line['entry_id']}, db='company')
               for line, after in changes]
    return write_event_to(s.company, ctx, command, summary[:500], touched, actor_id=s.actor.id,
                          actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None))


def _open_sort(s, ctx, label: str) -> str:
    """A --lines act's own batch, inside the caller's transaction."""
    return batches._insert_batch(s, ctx, kind=SORT_KIND, label=label[:200], scope=SORT_SCOPE, ordering='record',
                                 source_type='bank_line', source_id=None)


def _many(problems: list[tuple[dict, BookflowError]], total: int, what: str) -> BookflowError:
    """One refusal for a --lines act, naming every line refused; nothing was changed."""
    if len(problems) == 1 and total == 1:
        return problems[0][1]
    codes = {e.code for _, e in problems}
    listed = [{'line': line['id'], 'code': e.code, 'message': e.message, 'details': e.details} for line, e in problems]
    return BookflowError(codes.pop() if len(codes) == 1 else 'E_VALIDATION', message=(
        f"Nothing was {what}: {len(problems)} of the {total} lines cannot be. First, {problems[0][1].message}"),
        details={'lines': json.loads(json.dumps(listed[:batches.MAX_BLOCKERS], default=str)), 'total': len(problems),
                 'field': 'lines'})


# ------------------------------------------------------------------ candidates

def _signed(s, line: dict) -> int:
    """The line's amount in reconciliation sign (a card's flipped), as the matcher reads it."""
    return -line['amount'] if _account(s, line['account_id'])['type'] == 'credit_card' else line['amount']


def _entry_of(candidate) -> str:
    return candidate.ref[0].transaction_id


def _entry_candidates(s, account_id: str, entry_id: str) -> list:
    from bookflow.commands.reconcile_import_cmds import window_candidates
    t = c.metadata.tables
    versions, heads = t['reconciliation_effect_versions'], t['reconciliation_effect_heads']
    lo, hi = s.company.conn.execute(sa.select(sa.func.min(versions.c.effective_date),
                                              sa.func.max(versions.c.effective_date)).select_from(
        versions.join(heads, heads.c.version_id == versions.c.id)).where(
        versions.c.transaction_id == entry_id, versions.c.account_id == account_id, versions.c.active == 1)).one()
    if lo is None:
        return []
    return [v for v in window_candidates(s, account_id, lo, hi, hi) if _entry_of(v) == entry_id]


def _as_line(line: dict, amount: int) -> files.Line:
    return files.Line(line_id=line['line_key'], date=line['date'], amount=amount, payee=line['payee'],
                      memo=line['memo'], number=line['number'], fitid=line['fitid'], kind=line['kind'])


def _choices(candidates) -> list[dict]:
    return [{'entry_id': _entry_of(v), 'date': v.date, 'amount': v.amount, 'number': v.number,
             'payees': list(v.payees)} for v in candidates[:10]]


# ------------------------------------------------------------------ match

def _match_after(s, line: dict, entry_id: str | None, taken: set[str]) -> tuple[dict, str]:
    from bookflow.commands.reconcile_import_cmds import window_candidates
    signed = _signed(s, line)
    linked = _linked_entries(s, line['account_id'], besides=(line['id'],)) | taken
    if entry_id is not None:
        found = _entry_candidates(s, line['account_id'], entry_id)
        name = bank_feeds._full_name(s, line['account_id'])
        if not found:
            raise _refuse('entry', f'Entry {entry_id} has no movement on {name} to match {_label(s, line)} to.',
                          line=line['id'], entry=entry_id)
        fit = [v for v in found if v.amount == signed]
        if not fit:
            raise _refuse('entry', f"Entry {entry_id} moves {name} by another amount than {_label(s, line)}; a match "
                                   'is exact or refused.', line=line['id'], entry=entry_id)
        if entry_id in linked:
            other = s.company.conn.execute(sa.select(c.bank_lines.c.id).where(
                c.bank_lines.c.entry_id == entry_id, c.bank_lines.c.live == 1,
                c.bank_lines.c.id != line['id'])).scalar()
            raise _refuse('entry', f"Entry {entry_id} is already matched to line {other or 'in this act'}; "
                                   f'`bank-line unmatch {other}` frees it.' if other else
                          f'Entry {entry_id} is already matched by another line of this act.',
                          line=line['id'], entry=entry_id, matched_line=other)
        chosen, reason = fit[0], f'named by the {_how(s)}'
    else:
        lo, hi = _shift(line['date'], -files.SUGGEST_DAYS), _shift(line['date'], files.SUGGEST_DAYS)
        population = [v for v in window_candidates(s, line['account_id'], lo, hi, hi) if _entry_of(v) not in linked]
        result = files.match([_as_line(line, signed)], population)[0]
        options = result.suggestions if result.status == 'suggested' else []
        if result.status in ('matched', 'reconciled'):
            chosen = result.match
        elif len(options) == 1:
            chosen = options[0]
        elif options:
            raise _refuse('entry', f"{len(options)} entries fit {_label(s, line)} equally ({result.reason}); name one "
                                   'with entry (never chosen for you).', line=line['id'],
                          candidates=_choices(options))
        else:
            raise _refuse('entry', f'No entry of that amount is near {_label(s, line)}: name one with entry, or add '
                                   'it (`bank-line add`).', line=line['id'])
        reason = result.reason or 'the one candidate'
    return dict(state='settled' if chosen.reconciled else 'matched', entry_id=_entry_of(chosen),
                movement=chosen.ref[0].model_dump_json(), link_how=_how(s), match_reason=reason[:500], created=0,
                payee_display=None, ignore_reason=None, entry_date=chosen.date), 'matched'


# ------------------------------------------------------------------ unmatch and ignore

def _unmatch_after(s, line: dict) -> tuple[dict, str]:
    if line['state'] == 'waiting':
        raise _refuse('line', f'{_label(s, line)} is already waiting.', line=line['id'])
    if line['created'] or line['state'] == 'added':
        if line['entry_id'] and line['id'] not in entry_trouble(s, [line]):
            raise _refuse('line', f"{_label(s, line)} cannot be returned to waiting while its entry stands; "
                                  f"{_way_back(s, line)}.", line=line['id'], entry=line['entry_id'])
        return dict(_CLEAR), 'released'
    if line['state'] == 'ignored':
        return dict(_CLEAR), 'unignored'
    return dict(_CLEAR), 'unmatched'


def _ignore_after(s, line: dict, reason: str) -> tuple[dict, str]:
    return dict(_CLEAR, state='ignored', ignore_reason=reason), 'ignored'


# ------------------------------------------------------------------ the pure acts: match, unmatch, ignore

_ACTS = {'match': ('waiting', 'matched', 'settled'), 'unmatch': None, 'ignore': ('waiting',)}


def act(s, ctx, kind: str, inp, *, dry_run: bool) -> dict:
    """bank-line match, unmatch or ignore: on one line, or several as one bank-sort batch, all or nothing."""
    if kind == 'match':
        items = [(inp.line, inp.entry)] if inp.line else [(i.line, i.entry) for i in inp.lines]
    else:
        items = [(inp.line, None)] if inp.line else [(i, None) for i in inp.lines]
    ids = [i for i, _ in items]
    if len(set(ids)) != len(ids):
        raise _refuse('lines', 'A line is named twice.')

    def plan():
        found, changes, problems, taken = lines(s, ids), [], [], set()
        for line, (_, entry) in zip(found, items):
            try:
                states = _ACTS[kind] or ('matched', 'settled', 'transfer', 'ignored', 'added', 'waiting')
                batch_id = _sortable(s, line, states)
                if kind == 'match':
                    after, event = _match_after(s, line, entry, taken)
                    taken.add(after['entry_id'])
                elif kind == 'unmatch':
                    after, event = _unmatch_after(s, line)
                else:
                    after, event = _ignore_after(s, line, inp.ignore_reason)
                changes.append((line, after, event, batch_id))
            except BookflowError as error:
                problems.append((line, error))
        if problems:
            raise _many(problems, len(items), {'match': 'matched', 'unmatch': 'returned to waiting',
                                               'ignore': 'ignored'}[kind])
        return changes

    if dry_run:
        changes = plan()
        return dict(lines=tuple(_out(s, {**line, **after}) for line, after, _, _ in changes), entries=(),
                    batch_id=None, summary=f'Dry run: {_said(kind, changes)}. Nothing was changed.',
                    next=f"the same call without --dry-run")

    def write():
        changes = plan()
        sort_id = _open_sort(s, ctx, f'Bank inbox: {kind} {len(changes)} line(s)') if inp.lines else None
        summary = _said(kind, changes)
        event = _audit(s, ctx, f'bank-line {kind}', summary, [(line, after) for line, after, _, _ in changes])
        out = []
        for line, after, event_kind, batch_id in changes:
            extra = [('transaction', after['entry_id'], 'linked', None)] if after.get('entry_id') else []
            details = {'how': _how(s), 'entry': after.get('entry_id'), 'reason': after.get('match_reason'),
                       'entry_left': line['entry_id'] if not after.get('entry_id') else None,
                       'ignore_reason': after.get('ignore_reason'), 'sort_batch': sort_id,
                       'note': getattr(inp, 'note', None)}
            out.append(_write_line(s, ctx, line, after, kind=event_kind, details=details, event_id=event,
                                   batch_ids=[batch_id] + ([sort_id] if sort_id else []), extra=extra))
        return out, sort_id, summary

    written, sort_id, summary = _in_tx(s, write)
    return dict(lines=tuple(_out(s, v) for v in written), entries=(), batch_id=sort_id, summary=summary + '.',
                next=(f'`bank-line undo {sort_id}` undoes this batch' if sort_id else None))


def _said(kind: str, changes) -> str:
    n = len(changes)
    if kind == 'match':
        return f"{n} line(s) matched to " + ', '.join(after['entry_id'] for _, after, _, _ in changes[:5]) + (
            '...' if n > 5 else '')
    if kind == 'ignore':
        return f'{n} line(s) ignored'
    return f'{n} line(s) returned to waiting'


# ------------------------------------------------------------------ add

def _payee(s, payee: str, payee_type: str) -> dict:
    from bookflow.company import parties
    row = parties.resolve_party(s.company, _NOUN[payee_type], payee)
    return {'id': row['id'], 'name': row.get('full_name') or row.get('name') or row.get('display_name') or row['id']}


def _merge(inp, item) -> m.AddItem:
    """An item of a --lines add, with the top-level fields filling what it leaves out."""
    values = {k: getattr(item, k) for k in m.AddItem.model_fields if k != 'line'}
    for k in values:
        if values[k] in (None, ()):
            values[k] = getattr(inp, k)
    return m.AddItem(line=item.line, **values)


def _add_plan(s, line: dict, item: m.AddItem) -> dict:
    """The command, and its exact input, that enters this line: the line's own date and amount, the caller's payee,
    account (or splits), class and memo. Nothing is guessed: no payee or no account asks."""
    account = _account(s, line['account_id'])
    card = account['type'] == 'credit_card'
    if line['amount'] == 0:
        raise _refuse('line', f'{_label(s, line)} moves no money; ignore it.', line=line['id'])
    out = line['amount'] < 0
    command = ('card-charge post' if out else 'card-credit post') if card else ('check post' if out else 'deposit post')
    ask = [f for f, v in (('payee', item.payee), ('account', item.account or item.splits or None)) if v is None]
    if ask:
        raise BookflowError('E_VALIDATION', message=(
            f"Which {' and which '.join(ask)} for {_label(s, line)}? Ask the person and pass "
            f"{' and '.join(ask)}: Bookflow never guesses who a line was for or what it was for."),
            details={'field': ask[0], 'fields': [{'field': f, 'problem': 'ask the person; never guess'} for f in ask],
                     'line': line['id'], 'ask': ask})
    if item.account and item.splits:
        raise _refuse('splits', 'Give account or splits, not both.', line=line['id'])
    if item.number is not None and command != 'check post':
        raise _refuse('number', f'Only a check takes a number; {_label(s, line)} is entered with {command}.')
    places = bank_feeds._places(account['currency'])
    total = abs(line['amount'])
    if item.splits:
        rows = []
        for n, split in enumerate(item.splits):
            units = files.amount(split.amount, places, f'splits[{n}].amount')
            if units <= 0:
                raise _refuse(f'splits[{n}].amount', 'Each split is a positive amount.')
            rows.append((split.account, units, split.memo, split.class_id))
        given = sum(units for _, units, _, _ in rows)
        if given != total:
            raise _refuse('splits', f"The splits add up to {bank_feeds.decimal(given, places)}; {_label(s, line)} is "
                                    f"{bank_feeds.decimal(total, places)} (a difference of "
                                    f"{bank_feeds.decimal(total - given, places)}). Amounts are exact or refused.",
                          line=line['id'])
    else:
        rows = [(item.account, total, None, None)]
    payee_type = item.payee_type or 'vendor'
    payee = _payee(s, item.payee, payee_type)
    memo = item.memo if item.memo is not None else ((line['payee'] or line['memo'] or '').strip() or None)
    money = lambda units: bank_feeds.decimal(units, places)
    if command == 'deposit post':
        payload = {'operation_key': f"bank-line-add:{line['id']}:{line['version']}", 'document': {
            'mode': 'inline', 'deposit_to': account['id'], 'date': line['date'], 'memo': memo,
            'additional': [{'received_from': {'kind': payee_type, 'id': payee['id']}, 'from_account': a,
                            'amount': money(units), **({'memo': note} if note else {}),
                            **({'class': cls or item.class_id} if (cls or item.class_id) else {})}
                           for a, units, note, cls in rows]}}
    else:
        payload = {'account': account['id'], 'pay_to': {'name_type': payee_type, 'name_id': payee['id']},
                   'date': line['date'], 'amount': money(total), 'memo': memo,
                   'expenses': [{'account': a, 'amount': money(units), **({'memo': note} if note else {}),
                                 **({'class_id': cls} if cls else {})} for a, units, note, cls in rows]}
        if item.class_id:
            payload['class_id'] = item.class_id
        if command == 'check post':
            number = item.number
            if number is None and line['number'].strip().isdigit() and line['kind'].upper() in ('', 'CHECK'):
                number = line['number'].strip()
            if number:
                payload['number'] = number
            else:
                payload['no_number'] = True
    return {'command': command, 'input': payload, 'payee': payee['name']}


def _created_id(output) -> str:
    out = output.model_dump() if hasattr(output, 'model_dump') else output
    if isinstance(out.get('deposit'), dict):
        return out['deposit']['id']
    if out.get('payments'):
        return out['payments'][0]['id']
    return out['id']


def _version(s, entry_id: str) -> int:
    return s.company.conn.execute(sa.select(c.transactions.c.version).where(c.transactions.c.id == entry_id)).scalar()


def _proposed(line: dict, item: m.AddItem) -> tuple[m.AddItem, dict | None]:
    """An add that names no payee or no account takes them from the line's rule or history proposal (design §5: one
    click is Add on a row with a proposal); what the caller named always wins."""
    from bookflow.company import bank_rules
    if item.payee is not None and (item.account is not None or item.splits):
        return item, None
    found = bank_rules.proposal_fields(line)
    if found is None:
        return item, None
    values = item.model_dump()
    if item.payee is None and found['payee']:
        values.update(payee=found['payee'], payee_type=item.payee_type or found['payee_type'])
    if item.account is None and not item.splits and found['account']:
        values['account'] = found['account']
        if item.class_id is None and found['class_id']:
            values['class_id'] = found['class_id']
    if item.memo is None and found['memo']:
        values['memo'] = found['memo']
    return m.AddItem(**values), found


def _answer(s, plan: dict, item: m.AddItem) -> dict | None:
    """What the line was added as, for history and learning: one account only (splits teach nothing)."""
    from bookflow.company import bank_rules
    if item.splits or not item.account:
        return None
    from bookflow.company.accounts import resolve_account
    class_id = None
    if item.class_id:
        from bookflow.company.budgets import resolve_class
        class_id = resolve_class(s, item.class_id)
    payee = _payee(s, item.payee, item.payee_type or 'vendor')
    return bank_rules.answer_of(plan['input'], payee['id'], item.payee_type or 'vendor', payee['name'],
                                resolve_account(s.company, item.account)['id'], class_id)


def add(s, ctx, inp, *, dry_run: bool) -> dict:
    items = [m.AddItem(line=inp.line, **{k: getattr(inp, k) for k in m.AddItem.model_fields if k != 'line'})] \
        if inp.line else [_merge(inp, i) for i in inp.lines]
    ids = [i.line for i in items]
    if len(set(ids)) != len(ids):
        raise _refuse('lines', 'A line is named twice.')
    found = lines(s, ids)
    plans, problems = [], []
    for n, (line, item) in enumerate(zip(found, items)):
        try:
            _sortable(s, line)
            item, came = _proposed(line, item)
            items[n] = item
            plan = _add_plan(s, line, item)
            plan['from'] = came
            plans.append((line, plan))
        except BookflowError as error:
            problems.append((line, error))
    if problems:
        raise _many(problems, len(items), 'added')
    # Each command is tried first (a closed period, a locked account, an inactive name refuse here, with the
    # command's own code), so a batch is added whole or not at all.
    if dry_run or inp.lines:
        for line, plan in plans:
            try:
                batches.run_nested(s, ctx, plan['command'], plan['input'], source_ref=f"bank-line:{line['id']}",
                                   dry_run=True, recovery=False)
            except BookflowError as error:
                problems.append((line, error))
        if problems:
            raise _many(problems, len(items), 'added')
    if dry_run:
        return dict(lines=tuple(_out(s, {**line, 'state': 'added', 'created': 1, 'payee_display': plan['payee']})
                                for line, plan in plans),
                    entries=tuple(m.Entered(line_id=line['id'], entry_id=None, command=plan['command'],
                                            input=plan['input']) for line, plan in plans),
                    batch_id=None, summary=f'Dry run: {len(plans)} line(s) would be entered. Nothing was changed.',
                    next='the same call without --dry-run')
    # A --lines add's batch opens in the first posting's own transaction, so a failure there leaves no empty batch.
    opened: list[str] = []
    how = _how(s)
    entered, written, learned = [], [], []
    by_line = {i.line: i for i in items}
    for n, (line, plan) in enumerate(plans):
        def record(s2, cmd, applied, output, line=line, plan=plan):
            from bookflow.company import bank_rules
            entry_id = _created_id(output)
            current = lines(s2, [line['id']])[0]
            if current['version'] != line['version'] or current['state'] != 'waiting' or not current['live']:
                raise BookflowError('E_VERSION_CONFLICT', message=f"Line {line['id']} changed meanwhile; nothing "
                                                                  'was entered for it.', details={'line': line['id']})
            batch = _import_batch(s2, current)
            if batch['state'] != 'live':
                raise _refuse('line', f"Import {line['import_id']} is being reversed; nothing was entered.")
            if inp.lines and not opened:
                opened.append(_open_sort(s2, ctx, f'Bank inbox: add {len(plans)} line(s)'))
            sort_id = opened[0] if opened else None
            came = plan.get('from') or {}
            answer = _answer(s2, plan, by_line[line['id']])
            written.append(_write_line(
                s2, ctx, current, dict(state='added', entry_id=entry_id, movement=None, link_how=how,
                                       match_reason=f'entered with {cmd.name}', created=1,
                                       payee_display=plan['payee'], ignore_reason=None, entry_date=line['date'],
                                       answer=json.dumps(answer, sort_keys=True) if answer else None),
                kind='added', details={'entry': entry_id, 'command': cmd.name, 'payee': plan['payee'], 'how': how,
                                       'sort_batch': sort_id, 'proposal': came.get('source'),
                                       'rule_id': came.get('rule_id')},
                event_id=batches.last_event_id(s2), batch_ids=[batch['id']] + ([sort_id] if sort_id else []),
                extra=[('transaction', entry_id, 'created', _version(s2, entry_id))]))
            if came.get('rule_id'):
                s2.company.conn.execute(c.bank_lines.update().where(c.bank_lines.c.id == line['id']).values(
                    rule_id=came['rule_id']))
            note = bank_rules.learn(s2, ctx, current, answer, batch['id'], always=bool(getattr(inp, 'always', False)),
                                    sort_batch=sort_id)
            if note:
                learned.append(note)
            entered.append(m.Entered(line_id=line['id'], entry_id=entry_id, command=cmd.name, input=plan['input']))
        try:
            batches.run_nested(s, ctx, plan['command'], plan['input'], source_ref=f"bank-line:{line['id']}",
                               recorder=record, recovery=False)
        except BookflowError as error:
            if not n:
                raise
            sort_id = opened[0]
            raise BookflowError(error.code, message=(
                f"{error.message} {n} of the {len(plans)} lines were entered (batch {sort_id}); the rest wait. "
                f"`bank-line undo {sort_id}` takes back those entered."),
                details={**(error.details or {}), 'batch': sort_id, 'entered': n, 'line': line['id']}) from None
    sort_id = opened[0] if opened else None
    return dict(lines=tuple(_out(s, v) for v in written), entries=tuple(entered), batch_id=sort_id,
                summary=f'{len(entered)} line(s) entered: ' + ', '.join(
                    f'{e.command.split()[0]} {e.entry_id}' for e in entered[:5]) + ('...' if len(entered) > 5 else '')
                + '.', next=' '.join([*learned, *([f'`bank-line undo {sort_id}` undoes this batch'] if sort_id else [])])
                or None)


def add_one(s, ctx, line: dict, item: m.AddItem, *, how: str, rule_id: str, source_ref: str) -> str:
    """One waiting line entered by a live auto-add rule (bank_rules.run_live), as ``ctx``'s actor (its establisher):
    the same typed command as `bank-line add`, citing the rule, recorded in the line's import batch only. Learns
    nothing (the rule's own answer). Returns the entry."""
    _sortable(s, line)
    plan = _add_plan(s, line, item)
    made = []

    def record(s2, cmd, applied, output):
        entry_id = _created_id(output)
        current = lines(s2, [line['id']])[0]
        if current['version'] != line['version'] or current['state'] != 'waiting' or not current['live']:
            raise BookflowError('E_VERSION_CONFLICT', message=f"Line {line['id']} changed meanwhile; nothing was "
                                                              'entered for it.', details={'line': line['id']})
        batch = _import_batch(s2, current)
        if batch['state'] != 'live':
            raise _refuse('line', f"Import {line['import_id']} is being reversed; nothing was entered.")
        _write_line(s2, ctx, current, dict(state='added', entry_id=entry_id, movement=None, link_how=how,
                                           match_reason=f'entered with {cmd.name} on a rule', created=1,
                                           payee_display=plan['payee'], ignore_reason=None, entry_date=line['date'],
                                           answer=None),
                    kind='added', details={'entry': entry_id, 'command': cmd.name, 'payee': plan['payee'],
                                           'how': how, 'rule_id': rule_id, 'source_ref': source_ref},
                    event_id=batches.last_event_id(s2), batch_ids=[batch['id']],
                    extra=[('transaction', entry_id, 'created', _version(s2, entry_id))])
        s2.company.conn.execute(c.bank_lines.update().where(c.bank_lines.c.id == line['id']).values(rule_id=rule_id))
        made.append(entry_id)
    batches.run_nested(s, ctx, plan['command'], plan['input'], source_ref=source_ref, recorder=record, recovery=False)
    return made[0]


# ------------------------------------------------------------------ add more details (increment 5)

def _difference(s, line: dict, selected: int, what: str, *, hint: str) -> BookflowError:
    places = bank_feeds._currency_places(s, line['account_id'])
    total = abs(line['amount'])
    money = lambda units: bank_feeds.decimal(units, places)
    return BookflowError('E_VALIDATION', message=(
        f"The selected {what} come to {money(selected)}; {_label(s, line)} is {money(total)}: a difference of "
        f"{money(total - selected)} remaining. {hint}"), details={
        'field': what, 'fields': [{'field': what, 'problem': f'difference {money(total - selected)} remaining'}],
        'line': line['id'], 'line_amount': money(total), 'selected': money(selected),
        'difference': money(total - selected)})


def _deposit_plan(s, ctx, line: dict, inp: m.DepositLineInput) -> dict:
    """`deposit post` of the selected Undeposited Funds payments into the line's account on the line's date. The
    payments must come to the line exactly; a difference is refused, showing it, unless the caller names an
    adjustment: then it is one more row of the deposit (other money in, or money kept back), never guessed."""
    account = _account(s, line['account_id'])
    if account['type'] == 'credit_card' or line['amount'] <= 0:
        raise _refuse('line', f'{_label(s, line)} is not money into a bank account; only such a line is a deposit.',
                      line=line['id'])
    if len(set(inp.payments)) != len(inp.payments):
        raise _refuse('payments', 'A payment is named twice.')
    rows = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(
        c.transactions.c.id, c.transactions.c.type, c.transactions.c.version).where(
        c.transactions.c.id.in_(list(inp.payments)))).mappings()}
    sources = []
    for n, pid in enumerate(inp.payments):
        row = rows.get(pid)
        if row is None:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'payment', 'selector': pid,
                                                                'field': f'payments[{n}]'})
        if row['type'] not in ('payment', 'sales_receipt'):
            raise _refuse(f'payments[{n}]', f"{pid} is a {row['type'].replace('_', ' ')}, not a customer payment or "
                                            'sales receipt waiting in Undeposited Funds.')
        sources.append({'source_type': row['type'], 'source': pid, 'expected_version': row['version']})
    memo = inp.memo if inp.memo is not None else ((line['payee'] or line['memo'] or '').strip() or None)
    document = {'mode': 'inline', 'deposit_to': account['id'], 'date': line['date'], 'memo': memo,
                'sources': sources}
    payload = {'operation_key': f"bank-line-deposit:{line['id']}:{line['version']}", 'document': document}
    # What the payments come to, as the deposit itself reads them (a payment already deposited, voided or not in
    # Undeposited Funds is refused here by `deposit post`'s own code).
    preview = batches.run_nested(s, ctx, 'deposit post', payload, source_ref=f"bank-line:{line['id']}",
                                 dry_run=True, recovery=False)
    selected = preview['deposit']['effective_bank_total']['minor_units']
    total = line['amount']
    places = bank_feeds._places(account['currency'])
    money = lambda units: bank_feeds.decimal(units, places)
    given = [f for f in ('adjustment_account', 'adjustment_payee', 'adjustment_payee_type', 'adjustment_memo',
                         'adjustment_class_id') if getattr(inp, f) is not None]
    if total == selected and given:
        raise _refuse(given[0], f'The payments come to {_label(s, line)} exactly: there is no difference to adjust, '
                                f"so {', '.join(given)} would be dropped. Leave them out.", line=line['id'])
    if total < selected:
        # Money kept back is a cash-back row: an account and a memo, no payee and no class.
        dropped = [f for f in ('adjustment_payee', 'adjustment_payee_type', 'adjustment_class_id')
                   if getattr(inp, f) is not None]
        if dropped:
            raise _refuse(dropped[0], f'The line is less than the payments, so the difference is money kept back '
                                      f"(cash back), which takes an account and a memo only: {', '.join(dropped)} "
                                      'would be dropped. Leave them out.', line=line['id'])
    if total != selected and inp.adjustment_account is None:
        raise _difference(s, line, selected, 'payments', hint=(
            'Select payments that add up to the line, or name an `adjustment_account` (and an `adjustment_payee` when '
            'the line is more) to enter the difference as its own row of the deposit.'))
    if total != selected:
        if total > selected:
            if inp.adjustment_payee is None:
                raise BookflowError('E_VALIDATION', message=(
                    f"Who did the other {money(total - selected)} of {_label(s, line)} come from? Ask the person "
                    'and pass adjustment_payee: Bookflow never guesses.'),
                    details={'field': 'adjustment_payee', 'fields': [{'field': 'adjustment_payee',
                                                                      'problem': 'ask the person; never guess'}],
                             'line': line['id'], 'ask': ['adjustment_payee']})
            payee_type = inp.adjustment_payee_type or 'customer'
            payee = _payee(s, inp.adjustment_payee, payee_type)
            document['additional'] = [{'received_from': {'kind': payee_type, 'id': payee['id']},
                                       'from_account': inp.adjustment_account, 'amount': money(total - selected),
                                       **({'memo': inp.adjustment_memo} if inp.adjustment_memo else {}),
                                       **({'class': inp.adjustment_class_id} if inp.adjustment_class_id else {})}]
        else:
            document['cash_back'] = {'account': inp.adjustment_account, 'amount': money(selected - total),
                                     **({'memo': inp.adjustment_memo} if inp.adjustment_memo else {})}
    payer = preview['receipts'][0]['received_from'] if preview.get('receipts') else None
    return {'command': 'deposit post', 'input': payload, 'payee': payer, 'selected': selected}


def _bills_plan(s, ctx, line: dict, inp: m.PayBillsLineInput) -> dict:
    """`bill pay` of the named bills from the line's account on the line's date: one payment to one vendor, paying
    exactly the line (partial amounts named per bill), or refused showing the difference."""
    account = _account(s, line['account_id'])
    if line['amount'] >= 0:
        raise _refuse('line', f'{_label(s, line)} is not money out; only a payment out pays bills.', line=line['id'])
    if inp.method is None:
        raise BookflowError('E_VALIDATION', message=(
            f'Which payment method paid the bills on {_label(s, line)}? Ask the person and pass method '
            '(`payment-method query` lists them): Bookflow never guesses.'),
            details={'field': 'method', 'fields': [{'field': 'method', 'problem': 'ask the person; never guess'}],
                     'line': line['id'], 'ask': ['method']})
    from bookflow.company import bill_payments
    cheque = bill_payments._method(s, inp.method).kind == 'check' and account['type'] != 'credit_card'
    number = inp.check_number
    if cheque and number is None:
        # The bank has shown this cheque: its number is the line's, never the chequebook's next.
        if line['number'].strip().isdigit() and line['kind'].upper() in ('', 'CHECK'):
            number = line['number'].strip()
        else:
            raise BookflowError('E_VALIDATION', message=(
                f'Which check number paid the bills on {_label(s, line)}? The line carries none; ask the person and '
                'pass check_number (the chequebook\'s next number is never used for a cheque the bank has already '
                'shown).'), details={'field': 'check_number', 'fields': [
                    {'field': 'check_number', 'problem': 'ask the person; never guess'}],
                    'line': line['id'], 'ask': ['check_number']})
    memo = inp.memo if inp.memo is not None else ((line['payee'] or line['memo'] or '').strip() or None)
    payload = {'date': line['date'], 'funding_account': account['id'], 'method': inp.method,
               'bills': [{'bill': b.bill, **({'amount': b.amount} if b.amount is not None else {}),
                          **({'discount': b.discount} if b.discount is not None else {})} for b in inp.bills]}
    for key, value in (('memo', memo), ('check_number', number),
                       ('discount_account', inp.discount_account)):
        if value is not None:
            payload[key] = value
    preview = batches.run_nested(s, ctx, 'bill pay', payload, source_ref=f"bank-line:{line['id']}", dry_run=True,
                                 recovery=False)
    if preview['group_count'] != 1:
        names = sorted({p['vendor_name'] for p in preview['payments']})
        raise _refuse('bills', f"These bills make {preview['group_count']} payments ({', '.join(names)}); one bank "
                               'line is one payment to one vendor. Pay the others from their own lines.',
                      line=line['id'], vendors=names)
    selected = preview['paid_minor_units']
    if selected != -line['amount']:
        raise _difference(s, line, selected, 'bills', hint=(
            'Name the amount paid on each bill so they add up to the line (a part payment leaves the rest open).'))
    return {'command': 'bill pay', 'input': payload, 'payee': preview['payments'][0]['vendor_name'],
            'selected': selected}


def details(s, ctx, kind: str, inp, *, dry_run: bool) -> dict:
    """Add More Details: enter a waiting line as a deposit of selected Undeposited Funds payments (`deposit`) or as
    a payment of open bills (`bills`), through the typed command run as the caller, so the entry is exactly the one
    typed. The act is its own bank-sort batch (`bank-line undo` takes it back) and is recorded on the line's import
    batch, so the import's reversal deletes the deposit (its payments back in Undeposited Funds) or unapplies and
    voids the payment (its bills open again)."""
    line = lines(s, [inp.line])[0]
    _sortable(s, line)
    plan = (_deposit_plan if kind == 'deposit' else _bills_plan)(s, ctx, line, inp)
    places = bank_feeds._currency_places(s, line['account_id'])
    shown = dict(line_amount=bank_feeds.decimal(abs(line['amount']), places),
                 selected=bank_feeds.decimal(plan['selected'], places),
                 difference=bank_feeds.decimal(abs(line['amount']) - plan['selected'], places))
    source_ref = f"bank-line:{line['id']}"
    if dry_run:
        batches.run_nested(s, ctx, plan['command'], plan['input'], source_ref=source_ref, dry_run=True,
                           recovery=False)
        return dict(lines=(_out(s, {**line, 'state': 'added', 'created': 1, 'payee_display': plan['payee']}),),
                    entries=(m.Entered(line_id=line['id'], entry_id=None, command=plan['command'],
                                       input=plan['input']),),
                    batch_id=None, summary=f"Dry run: {_label(s, line)} would be entered with {plan['command']}. "
                                           'Nothing was changed.', next='the same call without --dry-run', **shown)
    how = _how(s)
    done: dict = {}

    def record(s2, cmd, applied, output):
        entry_id = _created_id(output)
        current = lines(s2, [line['id']])[0]
        if current['version'] != line['version'] or current['state'] != 'waiting' or not current['live']:
            raise BookflowError('E_VERSION_CONFLICT', message=f"Line {line['id']} changed meanwhile; nothing was "
                                                              'entered for it.', details={'line': line['id']})
        batch = _import_batch(s2, current)
        if batch['state'] != 'live':
            raise _refuse('line', f"Import {line['import_id']} is being reversed; nothing was entered.")
        sort_id = _open_sort(s2, ctx, f"Bank inbox: {cmd.name} from line {line['id']}")
        done['line'] = _write_line(
            s2, ctx, current, dict(state='added', entry_id=entry_id, movement=None, link_how=how,
                                   match_reason=f'entered with {cmd.name}', created=1, payee_display=plan['payee'],
                                   ignore_reason=None, entry_date=line['date']),
            kind='added', details={'entry': entry_id, 'command': cmd.name, 'payee': plan['payee'], 'how': how,
                                   'sort_batch': sort_id, 'selected': shown['selected'],
                                   'difference': shown['difference']},
            event_id=batches.last_event_id(s2), batch_ids=[batch['id'], sort_id],
            extra=[('transaction', entry_id, 'created', _version(s2, entry_id))])
        done.update(entry=entry_id, batch=sort_id)

    batches.run_nested(s, ctx, plan['command'], plan['input'], source_ref=source_ref, recorder=record,
                       recovery=False)
    return dict(lines=(_out(s, done['line']),),
                entries=(m.Entered(line_id=line['id'], entry_id=done['entry'], command=plan['command'],
                                   input=plan['input']),),
                batch_id=done['batch'], summary=(
                    f"{_label(s, line)} entered with {plan['command']} {done['entry']} ({plan['payee']}); "
                    f"difference {shown['difference']}."),
                next=f"`bank-line undo {done['batch']}` takes it back", **shown)


# ------------------------------------------------------------------ transfers

def _pairings(s, rows: list[dict]) -> dict[str, tuple[list[dict], bool]]:
    """For each waiting line: the waiting lines of other accounts in its currency that could be its other end (the
    opposite amount in the bank's sign, within TRANSFER_DAYS), and whether the pair is certain: exactly one fits it,
    and it is the only one that fits that one (never chosen among equals). One read for all the lines."""
    waiting = [r for r in rows if r.get('state') == 'waiting' and r.get('live', 1) and r.get('id')]
    if not waiting:
        return {}
    conn, t = s.company.conn, c.bank_lines
    lo = _shift(min(r['date'] for r in waiting), -2 * TRANSFER_DAYS)
    hi = _shift(max(r['date'] for r in waiting), 2 * TRANSFER_DAYS)
    amounts = sorted({v for r in waiting for v in (r['amount'], -r['amount'])})
    by_amount: dict[int, list[dict]] = {}
    for chunk in bank_feeds._chunks(amounts):
        for v in conn.execute(sa.select(t.c.id, t.c.account_id, t.c.amount, t.c.date).where(
                t.c.live == 1, t.c.state == 'waiting', t.c.amount.in_(chunk), t.c.date >= lo, t.c.date <= hi)
                .order_by(t.c.date, t.c.id)).mappings():
            by_amount.setdefault(v['amount'], []).append(dict(v))
    accounts = {r['account_id'] for r in waiting} | {v['account_id'] for vs in by_amount.values() for v in vs}
    currency = {}
    for chunk in bank_feeds._chunks(sorted(accounts)):
        currency.update(conn.execute(sa.select(c.accounts.c.id, c.accounts.c.currency).where(
            c.accounts.c.id.in_(chunk))).all())

    def fits(line):
        lo_, hi_ = _shift(line['date'], -TRANSFER_DAYS), _shift(line['date'], TRANSFER_DAYS)
        return [v for v in by_amount.get(-line['amount'], ()) if v['account_id'] != line['account_id']
                and currency.get(v['account_id']) == currency.get(line['account_id']) and lo_ <= v['date'] <= hi_]
    out = {}
    for r in waiting:
        found = fits(r)
        out[r['id']] = (found, len(found) == 1 and [v['id'] for v in fits(found[0])] == [r['id']])
    return out


def _pairing(s, line: dict) -> tuple[list[dict], bool]:
    return _pairings(s, [line]).get(line['id'], ([], False))


def _transfer_entries(s, a_line: dict, b_account: str, b_signed: int, besides: tuple[str, ...]) -> list[str]:
    """Existing transfer entries of the two accounts, the amounts each line shows, near the line's date, not matched by
    another live line of the first account (one linked in the other account is the pair completing)."""
    from bookflow.commands.reconcile_import_cmds import window_candidates
    lo, hi = _shift(a_line['date'], -TRANSFER_DAYS), _shift(a_line['date'], TRANSFER_DAYS)
    a_signed = _signed(s, a_line)
    linked = _linked_entries(s, a_line['account_id'], besides=besides)
    if len(besides) > 1:
        linked |= _linked_entries(s, b_account, besides=besides)
    on_a = {_entry_of(v) for v in window_candidates(s, a_line['account_id'], lo, hi, hi) if v.amount == a_signed}
    on_b = {_entry_of(v) for v in window_candidates(s, b_account, lo, hi, hi) if v.amount == b_signed}
    ids = sorted((on_a & on_b) - linked)
    if not ids:
        return []
    return list(s.company.conn.execute(sa.select(c.money_out_documents.c.transaction_id).where(
        c.money_out_documents.c.transaction_id.in_(ids), c.money_out_documents.c.kind == 'transfer')
        .order_by(c.money_out_documents.c.transaction_id)).scalars())


def _one_sided(s, line: dict, b_account: str | None = None) -> list[dict]:
    """Transfers already in the books that this line could be the other end of, within LATE_TRANSFER_DAYS: a
    `transfer` entry moving this line's account by exactly its amount, between it and one other account (``b_account``
    when given), not matched by another live line of this account. Usually one posted one-sided from the other
    account's line, whose own line is in; listed when several, never chosen."""
    from bookflow.commands.reconcile_import_cmds import window_candidates
    lo, hi = _shift(line['date'], -LATE_TRANSFER_DAYS), _shift(line['date'], LATE_TRANSFER_DAYS)
    signed = _signed(s, line)
    linked = _linked_entries(s, line['account_id'], besides=(line['id'],))
    dates = {}
    for v in window_candidates(s, line['account_id'], lo, hi, hi):
        if v.amount == signed and _entry_of(v) not in linked:
            dates.setdefault(_entry_of(v), v.date)
    if not dates:
        return []
    conn = s.company.conn
    ids = list(conn.execute(sa.select(c.money_out_documents.c.transaction_id).where(
        c.money_out_documents.c.transaction_id.in_(sorted(dates)), c.money_out_documents.c.kind == 'transfer')
        .order_by(c.money_out_documents.c.transaction_id)).scalars())
    out = []
    for entry_id in ids:
        others = sorted(set(conn.execute(sa.select(c.posting_lines.c.account_id).where(
            c.posting_lines.c.transaction_id == entry_id, c.posting_lines.c.account_id != line['account_id']))
            .scalars()))
        if len(others) != 1 or (b_account is not None and others[0] != b_account):
            continue
        end = conn.execute(sa.select(c.bank_lines.c.id).where(
            c.bank_lines.c.live == 1, c.bank_lines.c.entry_id == entry_id,
            c.bank_lines.c.account_id == others[0])).scalar()
        out.append({'entry': entry_id, 'account': others[0], 'account_name': bank_feeds._full_name(s, others[0]),
                    'date': dates[entry_id], 'days_apart': bank_feeds._days(dates[entry_id], line['date']),
                    'line': end})
    return out


def _movement(s, account_id: str, entry_id: str, signed: int):
    found = [v for v in _entry_candidates(s, account_id, entry_id) if v.amount == signed]
    return found[0] if found else None


def transfer(s, ctx, inp, *, dry_run: bool) -> dict:
    line = lines(s, [inp.line])[0]
    _sortable(s, line)
    a = _account(s, line['account_id'])
    pair, completes = None, None
    if inp.pair is not None:
        pair = lines(s, [inp.pair])[0]
        _sortable(s, pair)
        if pair['account_id'] == line['account_id']:
            raise _refuse('pair', 'The two ends of a transfer are lines of two different accounts.')
        if _account(s, pair['account_id'])['currency'] != a['currency'] or pair['amount'] != -line['amount']:
            raise _refuse('pair', f"{_label(s, pair)} is not the opposite of {_label(s, line)}; a transfer is exact or "
                                  'refused.', pair=pair['id'])
        b_id = pair['account_id']
    elif inp.to is not None:
        b_id = _resolve(s, inp.to)
        if b_id == line['account_id']:
            raise _refuse('to', 'A transfer goes to another account.')
    else:
        fits, sure = _pairing(s, line)
        late = [v for v in _one_sided(s, line) if inp.entry in (None, v['entry'])] if not fits else []
        if len(late) > 1:
            raise _refuse('entry', f"{len(late)} transfers already in the books fit {_label(s, line)} (within "
                                   f"{LATE_TRANSFER_DAYS} days): name the one this line completes with entry (never "
                                   'chosen for you).', line=line['id'], candidates=late[:10])
        if not fits and not late:
            raise _refuse('pair', f'No waiting line of another account is the opposite of {_label(s, line)} within '
                                  f'{TRANSFER_DAYS} days, and no transfer already posted fits it within '
                                  f'{LATE_TRANSFER_DAYS}: pass to (the other account) to send it now; its line will '
                                  'match the transfer when it comes in.', line=line['id'])
        if fits and not sure:
            raise _refuse('pair', f"{_label(s, line)} could pair with {len(fits)} line(s), and they with others: name "
                                  'one with pair (never chosen for you).', line=line['id'],
                          candidates=[{'line': v['id'], 'account': bank_feeds._full_name(s, v['account_id']),
                                       'date': v['date']} for v in fits[:10]])
        if late:
            b_id, completes = late[0]['account'], late[0]
            if inp.entry is None:
                inp = inp.model_copy(update={'entry': late[0]['entry']})
        else:
            pair = lines(s, [fits[0]['id']])[0]
            b_id = pair['account_id']
    b = _account(s, b_id)
    places = bank_feeds._places(a['currency'])
    b_signed = (line['amount'] if b['type'] == 'credit_card' else -line['amount'])
    besides = (line['id'],) + ((pair['id'],) if pair else ())
    if inp.entry is not None:
        entry = inp.entry
        if _movement(s, line['account_id'], entry, _signed(s, line)) is None or \
                _movement(s, b_id, entry, b_signed) is None:
            raise _refuse('entry', f"Entry {entry} does not move {bank_feeds.decimal(abs(line['amount']), places)} "
                                   f"between {bank_feeds._full_name(s, line['account_id'])} and "
                                   f"{bank_feeds._full_name(s, b_id)} as the line(s) show.", entry=entry)
        if entry in _linked_entries(s, line['account_id'], besides=besides):
            raise _refuse('entry', f'Entry {entry} is already matched by another line of this account.')
        existing = [entry]
    else:
        existing = _transfer_entries(s, line, b_id, b_signed, besides)
        if len(existing) > 1:
            raise _refuse('entry', f'{len(existing)} transfers between these accounts fit {_label(s, line)}: name one '
                                   'with entry (never chosen for you).', candidates=existing[:10])
        if not existing and pair is None and not inp.new:
            late = _one_sided(s, line, b_id)
            if late:
                named = '; '.join(f"transfer {v['entry']} of {v['date']} ({v['days_apart']} day(s) apart"
                                  + (f", its {v['account_name']} line {v['line']} in" if v['line'] else '') + ')'
                                  for v in late[:3])
                raise _refuse('to', f"{_label(s, line)} may be the other end of a transfer already posted with "
                                    f"{bank_feeds._full_name(s, b_id)}: {named}. `bank-line transfer --line "
                                    f"{line['id']} --entry {late[0]['entry']}` completes it; pass new to post "
                                    'another transfer anyway.', line=line['id'], candidates=late[:10])
    leaving = line if line['amount'] < 0 else (pair or line)
    source, target = (line['account_id'], b_id) if line['amount'] < 0 else (b_id, line['account_id'])
    memo = (line['payee'] or line['memo'] or '').strip() or None
    payload = {'from_account': source, 'to_account': target, 'date': leaving['date'],
               'amount': bank_feeds.decimal(abs(line['amount']), places), 'memo': memo}
    how = _how(s)
    names = f"{bank_feeds._full_name(s, source)} to {bank_feeds._full_name(s, target)}, " \
            f"{bank_feeds.decimal(abs(line['amount']), places)}"
    if dry_run:
        if not existing:
            batches.run_nested(s, ctx, 'transfer post', payload, source_ref=f"bank-line:{line['id']}", dry_run=True,
                               recovery=False)
        shown = [{**line, 'state': 'transfer', 'entry_id': existing[0] if existing else None,
                  'created': 0 if existing else 1}]
        if pair:
            shown.append({**pair, 'state': 'transfer', 'entry_id': existing[0] if existing else None})
        return dict(lines=tuple(_out(s, v) for v in shown),
                    entries=() if existing else (m.Entered(line_id=line['id'], entry_id=None, command='transfer post',
                                                           input=payload),),
                    batch_id=None, summary=f"Dry run: transfer {names}: " + (
                        f'matches transfer {existing[0]}.' if existing else 'posts a transfer.') + ' Nothing changed.',
                    next='the same call without --dry-run')

    def link(s2, entry_id: str, created: bool, event_id: str) -> list[dict]:
        out = []
        for n, ln in enumerate([line] + ([pair] if pair else [])):
            current = lines(s2, [ln['id']])[0]
            if current['version'] != ln['version'] or current['state'] != 'waiting' or not current['live']:
                raise BookflowError('E_VERSION_CONFLICT', message=f"Line {ln['id']} changed meanwhile; nothing was "
                                                                  'done.', details={'line': ln['id']})
            batch = _import_batch(s2, current)
            if batch['state'] != 'live':
                raise _refuse('line', f"Import {ln['import_id']} is being reversed; nothing was done.")
            mine = created and n == 0
            moved = None if created else _movement(s2, ln['account_id'], entry_id, _signed(s2, ln))
            out.append(_write_line(
                s2, ctx, current, dict(state='transfer', entry_id=entry_id,
                                       movement=moved.ref[0].model_dump_json() if moved else None, link_how=how,
                                       match_reason=f'transfer {names}', created=1 if mine else 0,
                                       payee_display=None, ignore_reason=None,
                                       entry_date=moved.date if moved else payload['date']),
                kind='transferred', details={'entry': entry_id, 'pair': (pair or {}).get('id') if n == 0 else
                                             line['id'], 'created': mine, 'how': how},
                event_id=event_id, batch_ids=[batch['id']],
                extra=[('transaction', entry_id, 'created' if mine else 'linked',
                        _version(s2, entry_id) if mine else None)]))
        return out

    if existing:
        def write():
            changes = [(ln, {'state': 'transfer', 'entry_id': existing[0]}) for ln in [line] + ([pair] if pair else [])]
            event = _audit(s, ctx, 'bank-line transfer', f'transfer {names} matched to {existing[0]}', changes)
            return link(s, existing[0], False, event)
        written = _in_tx(s, write)
        entered = ()
    else:
        written, entered = [], []

        def record(s2, cmd, applied, output):
            entry_id = _created_id(output)
            written.extend(link(s2, entry_id, True, batches.last_event_id(s2)))
            entered.append(m.Entered(line_id=line['id'], entry_id=entry_id, command=cmd.name, input=payload))
        batches.run_nested(s, ctx, 'transfer post', payload, source_ref=f"bank-line:{line['id']}", recorder=record,
                           recovery=False)
    entry_id = written[0]['entry_id']
    return dict(lines=tuple(_out(s, v) for v in written), entries=tuple(entered), batch_id=None,
                summary=f"Transfer {names}: " + (f'matched to transfer {entry_id}' if existing else
                                                f'transfer {entry_id} posted') + (
                    '' if pair else (f"; this line completes it, posted {completes['days_apart']} day(s) "
                                      "from the line's date"
                                     if completes else
                                     '; its other end matches it when that account\'s statement comes in')) + '.',
                next=None)


def _resolve(s, selector: str) -> str:
    from bookflow.company.accounts import resolve_account
    return resolve_account(s.company, selector)['id']


# ------------------------------------------------------------------ what a line shows

def _out(s, row: dict) -> m.InboxLine:
    extra = decorate(s, [row])
    return m.InboxLine(**bank_feeds._line_out(row, bank_feeds._currency_places(s, row['account_id'])),
                       **extra.get(row['id'], {}))


def decorate(s, rows: list[dict]) -> dict[str, dict]:
    """For each line: its transfer pair (a live line of another account linked to the same entry), `entry_gone` when
    its entry was voided, deleted or changed since, and, while it waits, its proposals with why (the matcher's
    suggestion, still standing and free; the other end of a transfer). Read in a few queries for all the rows."""
    out: dict[str, dict] = {}
    conn = s.company.conn
    entries = {r['entry_id'] for r in rows if r.get('entry_id') and r.get('live', 1)}
    pairs: dict[str, list[tuple[str, str]]] = {}
    for chunk in bank_feeds._chunks(sorted(entries)):
        for line_id, account_id, entry_id in conn.execute(sa.select(
                c.bank_lines.c.id, c.bank_lines.c.account_id, c.bank_lines.c.entry_id).where(
                c.bank_lines.c.live == 1, c.bank_lines.c.entry_id.in_(chunk))).all():
            pairs.setdefault(entry_id, []).append((line_id, account_id))
    trouble = entry_trouble(s, rows)
    pairings = _pairings(s, rows)
    stored = {r['id']: json.loads(r['proposal']) for r in rows
              if r.get('state') == 'waiting' and r.get('live', 1) and r.get('id') and r.get('proposal')}
    proposed = sorted({e for v in stored.values() for e in v.get('entries', ())})
    unavailable = (_linked_entries(s) | bank_reversal._gone(s, proposed)) if proposed else set()
    names, places = {}, {}

    def name(account_id):
        if account_id not in names:
            names[account_id] = bank_feeds._full_name(s, account_id)
        return names[account_id]

    def places_of(account_id):
        if account_id not in places:
            places[account_id] = bank_feeds._currency_places(s, account_id)
        return places[account_id]
    for r in rows:
        extra = {}
        if r.get('entry_id'):
            other = [i for i, a in pairs.get(r['entry_id'], ()) if a != r['account_id']]
            if other:
                extra['pair_line_id'] = other[0]
        if r.get('id') in trouble:
            extra['entry_gone'] = trouble[r['id']] + '; the line is not done: `bank-line unmatch --line ' + \
                r['id'] + '` returns it to waiting'
        if r.get('state') == 'waiting' and r.get('live', 1) and r.get('id'):
            proposals = []
            kept = stored.get(r['id'])
            if kept and kept.get('entries'):
                free = [e for e in kept['entries'] if e not in unavailable]
                if free:
                    proposals.append(m.Proposal(
                        kind='match', reason=kept.get('reason') or 'the matcher suggested it', entry_ids=tuple(free),
                        unique=len(free) == 1,
                        next=f"bank-line match --line {r['id']}" + ('' if len(free) == 1 else ' --entry <one of them>')))
            fits, sure = pairings.get(r['id'], ([], False))
            if fits:
                where = ', '.join(sorted({name(v['account_id']) for v in fits}))
                reason = (f"Transfer with {where}, {bank_feeds.decimal(abs(r['amount']), places_of(r['account_id']))}: "
                          f"the opposite amount "
                          f"{', '.join(str(bank_feeds._days(v['date'], r['date'])) for v in fits[:3])} day(s) apart")
                if not sure:
                    reason += f"; {len(fits)} line(s) fit, or another line fits them too: name one"
                proposals.append(m.Proposal(kind='transfer', reason=reason, line_ids=tuple(v['id'] for v in fits[:10]),
                                            unique=sure, next=f"bank-line transfer --line {r['id']}" + (
                                                '' if sure else ' --pair <one of them>')))
            from bookflow.company import bank_rules
            proposals.extend(bank_rules.proposals_of(s, r))
            if proposals:
                extra['proposals'] = tuple(proposals)
        out[r['id']] = extra
    return out


def proposal_for(result_reason: str, entries: list[str]) -> str | None:
    """The proposal kept on a waiting line at import: the matcher's suggestion and its reason."""
    if not entries:
        return None
    return json.dumps({'kind': 'match', 'entries': entries[:5], 'reason': (result_reason or '')[:500]}, sort_keys=True)


# ------------------------------------------------------------------ undoing a --lines batch (bank-sort)

def _touched(all_effects: list[dict]) -> dict[str, dict]:
    """Each line the batch sorted: how it stood before the batch's first change, what the batch left (its state and
    link), and the version it last left."""
    out: dict[str, dict] = {}
    for e in all_effects:
        if e['record_type'] == 'bank_line' and e['action'] == 'updated':
            before = json.loads(e['before'])
            info = out.setdefault(e['record_id'], {'seq': e['seq'], 'before': before})
            info['version_after'] = e['version_after']
            info['after'] = before.get('after')
    return out


def _ident(values: dict) -> dict:
    return {k: values.get(k, _CLEAR[k]) or _CLEAR[k] for k in _IDENT}


def _shown(values: dict) -> str:
    state = values.get('state') or 'waiting'
    return state + (f" to entry {values['entry_id']}" if values.get('entry_id') else '') + (
        f" ({values['ignore_reason']})" if values.get('ignore_reason') else '')


def _later_sorts(s, batch_id: str, ids: list[str]) -> dict[str, str]:
    """For each line, a later bank-sort batch over it not yet undone: undos unwind newest first."""
    out = {}
    for chunk in bank_feeds._chunks(ids):
        rows = s.company.conn.execute(sa.text(
            "SELECT e.record_id, b.id FROM batch_effects e JOIN batches b ON b.id = e.batch_id WHERE b.kind = :kind "
            "AND b.state IN ('live', 'reversing', 'blocked') AND b.reversal_of IS NULL AND b.id != :me "
            "AND b.rowid > (SELECT rowid FROM batches WHERE id = :me) AND e.record_type = 'bank_line' "
            f"AND e.record_id IN ({', '.join(f':l{n}' for n in range(len(chunk)))}) ORDER BY b.rowid DESC"),
            {'kind': SORT_KIND, 'me': batch_id, **{f'l{n}': v for n, v in enumerate(chunk)}}).all()
        for line_id, later in rows:
            out.setdefault(line_id, later)
    return out


def _restorable(s, touched: dict[str, dict], batch_id: str) -> tuple[dict[str, dict], list[dict]]:
    """The touched lines to put back: live (a line whose import was reversed went with it), standing as the batch
    left them (state and link compared, not versions, so undos unwind in any order), and not already back as they
    were before. Blockers for any sorted otherwise since, whose import is being reversed, or that would be linked
    back to an entry no longer what it was, or now held by another line."""
    rows = {v['id']: v for v in lines(s, list(touched))} if touched else {}
    later = _later_sorts(s, batch_id, list(touched)) if touched else {}
    live, blockers = {}, []
    for line_id, info in touched.items():
        row = rows[line_id]
        if not row['live']:
            continue
        if line_id in later:
            blockers.append(bank_reversal._blocker(
                'later_sort', 'bank_line', line_id, f"{_label(s, row)} was sorted again by a later batch, "
                f"{later[line_id]}, not undone.", f"`bank-line undo {later[line_id]}` first", seq=info['seq'],
                batch=later[line_id]))
            continue
        state = _import_batch(s, row)['state']
        if state != 'live':
            blockers.append(bank_reversal._blocker(
                'import_under_way', 'bank_line', line_id, f"{_label(s, row)}: its import {row['import_id']} is being "
                f'reversed ({state}).', f"`bank-feed reverse {row['import_id']}` finishes it", seq=info['seq']))
            continue
        now, before, after = _ident(row), _ident(info['before']), info.get('after')
        if now == before:
            continue  # already back as it stood (by any path): nothing to put back
        if (_ident(after) != now) if after is not None else (row['version'] != info['version_after']):
            clears = _way_back(s, row) if row['state'] != 'waiting' else None
            if after is not None and 'waiting' not in (before['state'], after['state']):
                clears = (clears + ', then ' if clears else '') + f"put it back as this batch left it ({_shown(after)})"
            blockers.append(bank_reversal._blocker(
                'line_changed', 'bank_line', line_id, f"{_label(s, row)} was sorted again since this batch (now "
                f"{_shown(row)}; the batch left it {_shown(after or {})}).", clears, seq=info['seq']))
            continue
        live[line_id] = row
    relink = {i: touched[i]['before'] for i in live
              if touched[i]['before'].get('entry_id') and touched[i]['before'].get('state') in _LINKED}
    if relink:
        trouble = entry_trouble(s, [{**live[i], 'state': b['state'], 'entry_id': b['entry_id'],
                                     'entry_date': b.get('entry_date')} for i, b in relink.items()])
        for i, why in trouble.items():
            gone = 'changed' not in why
            blockers.append(bank_reversal._blocker(
                'entry_gone', 'bank_line', i, f"{_label(s, live[i])} would be linked back to an entry no longer what "
                f'it was: {why}.', 'not clearable while that entry is gone: leave this batch as it is (each line can '
                'still be sorted on its own)' if gone else 'change that entry back as it was, or leave this batch as '
                'it is', seq=touched[i]['seq'], entry=relink[i]['entry_id']))
        wanted = sorted({b['entry_id'] for b in relink.values()})
        for chunk in bank_feeds._chunks(wanted):
            for other, entry_id in s.company.conn.execute(sa.select(c.bank_lines.c.id, c.bank_lines.c.entry_id).where(
                    c.bank_lines.c.live == 1, c.bank_lines.c.entry_id.in_(chunk))).all():
                if other in live and touched[other]['before'].get('entry_id') != entry_id:
                    continue  # this undo frees it
                for i, b in relink.items():
                    if b['entry_id'] == entry_id and other != i and i not in trouble:
                        blockers.append(bank_reversal._blocker(
                            'entry_matched', 'bank_line', i, f"{_label(s, live[i])} would be linked back to entry "
                            f"{entry_id}, which line {other} holds now: a bank line is never doubled.",
                            f"`bank-line unmatch --line {other}` first, or leave this batch as it is",
                            seq=touched[i]['seq'], entry=entry_id, matched_line=other))
    return live, blockers


def _sort_plan(s, ctx, batch: dict, all_effects: list[dict], done: dict, probe, ordering: list[dict]) -> dict:
    known = {('bank_line', 'updated'), ('transaction', 'created'), ('transaction', 'linked'),
             ('bank_rule', 'rule_learned')}
    unknown = sorted({(e['record_type'], e['action']) for e in all_effects} - known)
    if unknown:
        raise BookflowError('E_VALIDATION', message=f'No inverse is known for {unknown[0][0]} {unknown[0][1]} effects.',
                            details={'field': 'batch', 'effects': [list(u) for u in unknown]})
    touched = _touched(all_effects)
    live, blockers = _restorable(s, touched, batch['id'])
    blockers = list(ordering) + blockers

    def linked_for(ids):
        found = {}
        for chunk in bank_feeds._chunks(ids):
            for line_id, entry_id, import_id in s.company.conn.execute(sa.select(
                    c.bank_lines.c.id, c.bank_lines.c.entry_id, c.bank_lines.c.import_id).where(
                    c.bank_lines.c.live == 1, c.bank_lines.c.entry_id.in_(chunk))).all():
                if line_id in live or entry_id in found:
                    continue

                def make(label, seq, line_id=line_id, entry_id=entry_id, import_id=import_id):
                    return bank_reversal._blocker(
                        'entry_linked', 'transaction', entry_id, f'{label} is matched by line {line_id} of import '
                        f'{import_id}, which this undo does not return.',
                        f'`bank-line unmatch --line {line_id}` first', seq=seq, line=line_id, import_id=import_id)
                found[entry_id] = make
        return found
    steps, work, more, entries, created = bank_reversal.entry_plan(s, ctx, batch, all_effects, done, probe, linked_for)
    blockers += more
    learned = sum(1 for e in all_effects if (e['record_type'], e['action']) == ('bank_rule', 'rule_learned'))
    restore = {'seq': 0, 'record_type': 'batch', 'record_id': batch['id'], 'name': 'the lines it sorted',
               'action': 'updated', 'version': len(live),
               'steps': [f'return {len(live)} line(s) to how they stood before the batch',
                         *([f'retire bank_rule (rule_learned): {learned} learned from it, unless edited since']
                           if learned else [])]}
    freed = sum(1 for i, row in live.items() if row['entry_id'] and not row['created']
                and touched[i]['before'].get('entry_id') != row['entry_id'])
    return {'steps': [*steps, restore], 'work': work, 'blockers': blockers, 'records': len(created) + 1,
            'pending': entries + 1, 'lines': len(live), 'entries': entries, 'freed': freed}


def _sort_release(s, batch: dict) -> None:
    """In the transaction that marks the batch reversed: each line it sorted, still live and as it left it, back to
    how it stood before."""
    conn = s.company.conn
    reversal_id = batch['reversed_by']
    event = batches.last_event_id(s)
    via = conn.execute(sa.select(c.batches.c.via).where(c.batches.c.id == reversal_id)).scalar()
    touched = _touched(batches.effects(s, batch['id']))
    live, blockers = _restorable(s, touched, batch['id'])
    if blockers:
        raise BookflowError('E_VERSION_CONFLICT', message=f"{blockers[0]['message']} Run the undo again to see it.",
                            details={'blockers': blockers[:batches.MAX_BLOCKERS]})
    for line_id, row in live.items():
        info = touched[line_id]
        before = {k: info['before'].get(k) for k in _LINK}
        done = conn.execute(c.bank_lines.update().where(c.bank_lines.c.id == line_id,
                                                        c.bank_lines.c.version == row['version'])
                            .values(**before, version=row['version'] + 1))
        if done.rowcount != 1:
            raise BookflowError('E_VERSION_CONFLICT', message=f'Line {line_id} changed meanwhile; run the undo again.')
        conn.execute(c.bank_line_events.insert().values(
            id=new_id(), line_id=line_id, kind='reversed', import_id=row['import_id'], batch_id=reversal_id,
            details=json.dumps({'undo_of': batch['id'], 'state_was': row['state'], 'state': before['state'],
                                'entry_unlinked': row['entry_id']}, sort_keys=True),
            created_by=s.actor.id, created_via=via, created_at=batches.now(), audit_event_id=event))
        batches.record_effect(s, reversal_id, record_type='bank_line', record_id=line_id, action='reversed',
                              version_after=row['version'] + 1,
                              before={'of_seq': info['seq'], 'restored': before['state']}, event_id=event)
    # A line a match held is waiting again with its entry free: read its proposals afresh, and those of the waiting
    # lines of its account that entry could be (one imported meanwhile skipped it while it was taken).
    from bookflow.company import bank_rules
    for e in batches.effects(s, batch['id']):
        if (e['record_type'], e['action']) == ('bank_rule', 'rule_learned'):
            outcome = bank_rules.retire(s, e, batch, reversal_id)
            batches.record_effect(s, reversal_id, record_type='bank_rule', record_id=e['record_id'],
                                  action='reversed', version_after=None, before={'of_seq': e['seq'],
                                                                                 'outcome': outcome}, event_id=event)
    freed = [row for line_id, row in live.items() if row['entry_id'] and not row['created']
             and touched[line_id]['before'].get('entry_id') != row['entry_id']]
    if freed:
        _propose_again(s, freed)


def _proposal_now(s, line: dict) -> str | None:
    """The matcher's suggestion for a waiting line as the books stand now (the import's proposal, read again)."""
    from bookflow.commands.reconcile_import_cmds import window_candidates
    lo, hi = _shift(line['date'], -files.SUGGEST_DAYS), _shift(line['date'], files.SUGGEST_DAYS)
    linked = _linked_entries(s, line['account_id'], besides=(line['id'],))
    population = [v for v in window_candidates(s, line['account_id'], lo, hi, hi) if _entry_of(v) not in linked]
    result = files.match([_as_line(line, _signed(s, line))], population)[0]
    if result.status in ('matched', 'reconciled') and result.match is not None:
        return proposal_for(result.reason, [_entry_of(result.match)])
    return proposal_for(result.reason, [_entry_of(v) for v in result.suggestions]) if result.status == 'suggested' \
        else None


def _propose_again(s, freed: list[dict]) -> None:
    conn, t = s.company.conn, c.bank_lines
    seen = set()
    for row in freed:
        near = conn.execute(sa.select(t).where(
            t.c.live == 1, t.c.state == 'waiting', t.c.account_id == row['account_id'], t.c.amount == row['amount'],
            t.c.date >= _shift(row['date'], -2 * files.SUGGEST_DAYS),
            t.c.date <= _shift(row['date'], 2 * files.SUGGEST_DAYS))).mappings().all()
        for line in near:
            if line['id'] in seen:
                continue
            seen.add(line['id'])
            proposal = _proposal_now(s, dict(line))
            kept = {k: v for k, v in (json.loads(line['proposal']) if line['proposal'] else {}).items()
                    if k in ('rule', 'history')}
            if kept:  # a rule's or history's proposal stays; only the matcher's is read again
                proposal = json.dumps({**(json.loads(proposal) if proposal else {}), **kept}, sort_keys=True)
            if proposal != line['proposal']:
                conn.execute(t.update().where(t.c.id == line['id']).values(proposal=proposal))


def _read(s, batch: dict) -> None:
    batches.require_read(s, 'bank-feed show')


batches.register(batches.Adapter(SORT_KIND, 'record', on_finished=_sort_release, label='Bank inbox sort',
                                 planner=_sort_plan, runner=bank_reversal._run, reader=_read, blocks=True))


def undo(s, ctx, inp, *, dry_run: bool) -> dict:
    batch = batches._batch(s, inp.batch)
    if batch['kind'] != SORT_KIND:
        raise _refuse('batch', f"Batch {inp.batch} is a {batch['kind']} batch, not a bank-inbox sort; "
                               '`bank-feed reverse` undoes an import.', kind=batch['kind'])
    found = batches.plan(s, ctx, inp.batch, probe=False) if batch['state'] != 'reversed' else None
    out = batches.reverse(s, ctx, inp.batch, reason=ctx.reason, dry_run=dry_run, limit=inp.limit,
                          fingerprint=inp.fingerprint)
    lines_n = found['lines'] if found else 0
    entries_n = found['entries'] if found else 0
    freed = found.get('freed', 0) if found else 0
    # What does not come back as it was: the matches' entries are offered afresh, not where they were.
    note = (f' {freed} line(s) it had matched are free of their entries again; their proposals, and those of the '
            "account's waiting lines of the same amount (an import made meanwhile skipped an entry while it was "
            'taken), are read afresh.') if freed else ''
    again = f'bank-line undo {inp.batch}'
    if dry_run:
        summary = (f'Dry run: delete {entries_n} entr{"y" if entries_n == 1 else "ies"} the batch made and return '
                   f'{lines_n} line(s) to how they stood.{note} Nothing was changed.')
        next_ = f"{again} --fingerprint {out['fingerprint']}"
    elif out['state'] == 'reversed':
        summary, next_ = (f'Undone: {entries_n} entr{"y" if entries_n == 1 else "ies"} deleted (their history kept), '
                          f'{lines_n} line(s) returned.{note}'), None
    else:
        summary, next_ = f"Undoing: {out['reversed_now']} entr(ies) deleted in this slice; run `{again}` to go on.", again
    return dict(batch_id=inp.batch, state=out['state'], lines=lines_n, entries=entries_n,
                reversed_now=out['reversed_now'], remaining=out['remaining'],
                steps=tuple(json.loads(json.dumps(x, default=str)) for x in out['steps']),
                blockers=tuple(json.loads(json.dumps(x, default=str)) for x in out['blockers']),
                total_blockers=len(out['blockers']), fingerprint=out['fingerprint'],
                reversal_batch_id=out.get('reversal_batch'), summary=summary, next=next_)
