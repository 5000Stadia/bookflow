"""`reconcile import`: a bank statement file (CSV, OFX or QFX) read against the account's books.

Each statement line comes back matched to one movement, suggested (an amount that fits but not
surely), unmatched (nothing in the books yet), or reconciled (cleared by a statement already
certified). Nothing is ever posted. With a draft, or with `start`, the matched movements are
ticked on a statement reconciliation, through the same `reconcile start` and `reconcile mark`
operations a person's clicks make, so the draft is finished the ordinary way.

Running it again changes nothing that is already true: a movement already ticked stays ticked,
a draft already started for the same statement date is reused, and a FITID the file repeats is
read once. The file is never stored and never leaves the machine.
"""
from typing import Literal

from pydantic import Field, model_validator

from bookflow.commands import reconcile_cmds as rc
from bookflow.company import reconciliation_commands_models as m
from bookflow.company import reconciliation_drafts as drafts
from bookflow.company import reconciliation_persistence as persistence
from bookflow.company import statement_files as files
from bookflow.company.reconciliation_preparation import (
    account_population, claimed, group_fingerprint, groups, statement_amount)
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched, command

import json

MARK_CHUNK = 200  # `reconcile mark` takes at most this many movements per operation


class CsvMapping(m.Model):
    """Which CSV column holds what. Any column left empty is guessed from common bank headers."""
    date: str = Field('', max_length=200, description='Header of the posted-date column.')
    amount: str = Field('', max_length=200, description='Header of a signed amount column (positive = money in).')
    debit: str = Field('', max_length=200, description='Header of a money-out column, when the bank splits amounts.')
    credit: str = Field('', max_length=200, description='Header of a money-in column, when the bank splits amounts.')
    payee: str = Field('', max_length=200, description='Header of the payee or description column.')
    memo: str = Field('', max_length=200, description='Header of a memo column.')
    number: str = Field('', max_length=200, description='Header of the check-number column.')
    fitid: str = Field('', max_length=200, description="Header of the bank's own transaction id column.")
    balance: str = Field('', max_length=200, description='Header of a running-balance column; the latest one is the ending balance.')
    date_format: Literal['auto', 'YYYY-MM-DD', 'MM/DD/YYYY', 'DD/MM/YYYY', 'MM/DD/YY', 'YYYYMMDD'] = Field(
        'auto', description='How dates are written; auto reads ISO, YYYYMMDD and US month-first dates.')
    invert: bool = Field(False, description='Flip every amount, for a bank that shows money in as negative.')


class ImportInput(m.Dated):
    account: m.AccountSelector
    content: str = Field(min_length=1, max_length=5_000_000,
                         description="The statement file's text, exactly as downloaded from the bank.")
    format: Literal['auto', 'ofx', 'qfx', 'csv'] = Field(
        'auto', description='File format; auto tells OFX/QFX (an <OFX> element) from CSV.')
    csv_mapping: CsvMapping = Field(default_factory=CsvMapping,
                                    description='CSV columns; ignored for OFX and QFX.')
    draft: m.ID | None = Field(None, description='An open statement reconciliation on this account to tick the matched movements on.')
    start: bool = Field(False, description='Start a statement reconciliation from the file (or reuse the open one for the same statement date) and tick the matched movements on it.')
    statement_date: str | None = Field(None, description="Statement date; defaults to the file's balance date (OFX) or its last line's date (CSV).")
    ending_balance: str | None = Field(None, max_length=40, description="Ending balance, as money (\"6236.95\"); defaults to the file's ledger balance (OFX) or latest running balance (CSV).")
    match_days: int = Field(files.MATCH_DAYS, strict=True, ge=0, le=60,
                            description='Days apart a line and an entry of the same amount may be dated and still match.')
    suggest_days: int = Field(files.SUGGEST_DAYS, strict=True, ge=0, le=120,
                              description='Days apart for a same-amount entry to be offered as a suggestion.')

    @model_validator(mode='after')
    def target(self):
        if self.draft is not None and self.start:
            raise ValueError('give draft or start, not both')
        if self.suggest_days < self.match_days:
            raise ValueError('suggest_days must be at least match_days')
        return self


class Suggestion(m.Model):
    movement: m.MovementKey
    group_fingerprint: m.Fingerprint
    date: str
    amount: m.Units
    number: str
    payees: tuple[str, ...]
    memo: str | None


class ImportedLine(m.Model):
    line_id: str = Field(description='FITID-based, or a stable hash of the line when the bank gave no FITID.')
    fitid: str | None
    date: str
    amount: m.Units = Field(description='Minor units as the bank shows it: positive is money in (or a card payment).')
    amount_decimal: str
    payee: str
    memo: str
    number: str
    status: Literal['matched', 'suggested', 'unmatched', 'reconciled', 'duplicate']
    reason: str
    movement: m.MovementKey | None = None
    group_fingerprint: m.Fingerprint | None = None
    already_marked: bool = Field(False, description='The matched movement was ticked on the draft before this import.')
    marked: bool = Field(False, description='This import ticks the matched movement on the draft.')
    suggestions: tuple[Suggestion, ...] = ()


class ImportCounts(m.Model):
    lines: m.Count
    matched: m.Count
    suggested: m.Count
    unmatched: m.Count
    reconciled: m.Count
    duplicate: m.Count
    newly_marked: m.Count
    cleared_without_line: m.Count


class ImportOutput(m.Model):
    account_id: m.ID
    currency: m.Currency
    format: Literal['ofx', 'qfx', 'csv']
    statement_date: str | None
    ending_balance: m.Units | None = Field(description='Minor units, in the reconciliation\'s sign (what a card owes is positive).')
    counts: ImportCounts
    draft: m.Draft | None
    draft_started: bool
    lines: tuple[ImportedLine, ...]
    cleared_without_line: tuple[Suggestion, ...] = Field(description=(
        'Movements ticked on the draft that no line of this statement accounts for: cleared '
        'without a statement line. Flagged, never refused; check each before finishing.'))
    next_step: str


def _places(currency):
    from bookflow.core.money import CURRENCIES
    return CURRENCIES[currency][0]


def _decimal(units, places):
    sign = '-' if units < 0 else ''
    units = abs(units)
    if not places:
        return sign + str(units)
    return f'{sign}{units // 10 ** places}.{units % 10 ** places:0{places}d}'


def _statement_draft(snapshot, account_id, statement_date):
    """The open statement draft already started on this account for this date, if one is."""
    found = [d for d in snapshot.rows['drafts'] if d['account_id'] == account_id
             and d['kind'] == 'statement' and d['state'] == 'open']
    for row in found:
        value = rc._draft(snapshot, row['id'])
        if value.header.statement_date == statement_date:
            return value
    return None


def _opening_draft(snapshot, account_id):
    """The open opening draft a first statement follows, or None when the account has an opening."""
    state = next((v for v in snapshot.rows['accounts'] if v['account_id'] == account_id), None)
    if state is not None and state['opening_id'] is not None:
        return None
    found = [d for d in snapshot.rows['drafts'] if d['account_id'] == account_id
             and d['kind'] == 'opening' and d['state'] == 'open']
    if len(found) != 1:
        raise drafts.no_opening(snapshot, account_id)
    return rc._draft(snapshot, found[0]['id'])


def _candidates(snapshot, account_id, cutoff, draft):
    values, _ = account_population(snapshot, account_id, cutoff)
    selected = {v.key_id for v in draft.selections} if draft is not None else set()
    claims = claimed(snapshot)
    result = []
    for group in groups(values).values():
        first = group[0]
        display = json.loads(first['display_snapshot'])
        keys = {v['key_id'] for v in group}
        movement = m.MovementKey.model_validate_json(first['movement_snapshot'])
        result.append(files.Candidate(
            ref=(movement, group_fingerprint(group)), date=first['effective_date'],
            amount=sum(statement_amount(v) for v in group), number=display['number'] or '',
            payees=tuple(display['payees']), memo=display['memo'] or '',
            selected=bool(keys & selected), reconciled=bool(keys & set(claims)),
            eligible=first['effective_date'] <= cutoff))
    result.sort(key=lambda c: (c.date, c.ref[0].model_dump_json()))
    return result


def _prepare(inp, ctx, s, ids):
    """Everything the import will do, derived afresh: preview and apply both run this."""
    from bookflow.company.accounts import resolve_account
    account_id = resolve_account(s.company, inp.account)['id']
    snapshot = rc._loaded(s, account_id)
    account = snapshot.source.accounts[account_id]
    currency = account['currency']
    card = account['type'] == 'credit_card'
    places = _places(currency)
    parsed = files.parse(inp.content, inp.format, places, inp.csv_mapping.model_dump())
    if parsed.currency and parsed.currency.upper() != currency:
        raise files.invalid('content', f'the file is in {parsed.currency}; this account is in {currency}')
    sign = -1 if card else 1

    draft = None
    started = None
    opening_draft = None
    statement_date = inp.statement_date or parsed.statement_date
    if inp.ending_balance is not None:
        ending = m.statement_balance(inp.ending_balance, currency, 'ending_balance')
    else:
        ending = None if parsed.ending_balance is None else sign * parsed.ending_balance
    if inp.draft is not None:
        draft = rc._draft(snapshot, inp.draft)
        if draft.account_id != account_id or draft.kind != 'statement' or draft.state != 'open':
            raise BookflowError('E_RECONCILIATION_DRAFT_STATE', message=(
                'draft must be an open statement reconciliation on this account'))
        statement_date = draft.header.statement_date
    elif inp.start:
        if statement_date is None or ending is None:
            raise files.invalid('ending_balance' if statement_date else 'statement_date',
                                'the file gives no statement date and ending balance; pass statement_date and ending_balance')
        draft = _statement_draft(snapshot, account_id, statement_date)
        if draft is None:
            opening_draft = _opening_draft(snapshot, account_id)
            start_input = m.Start(operation_key=ids['start_key'], account=account_id,
                                  statement_date=statement_date,
                                  ending_balance=m.StatementMoney(minor_units=ending, currency=currency),
                                  opening_draft_id=opening_draft.id if opening_draft else None)
            draft = drafts.start(snapshot, start_input, identity=ids['draft'],
                                 revision_id=ids['start_revision'], opening_draft=opening_draft)
            started = start_input
    cutoff = statement_date or max((v.date for v in parsed.lines), default=None)
    if cutoff is None:
        raise files.invalid('content', 'the file has no transactions')

    lines = [files.Line(**{**v.__dict__, 'amount': sign * v.amount}) for v in parsed.lines]
    candidates = _candidates(snapshot, account_id, cutoff, draft)
    results = files.match(lines, candidates, match_days=inp.match_days, suggest_days=inp.suggest_days)

    to_mark = []
    if draft is not None:
        for result in results:
            if result.status == 'matched' and not result.match.selected and result.match.eligible:
                to_mark.append(result.match)
    marks = []
    value = draft
    for n, start_at in enumerate(range(0, len(to_mark), MARK_CHUNK)):
        chunk = to_mark[start_at:start_at + MARK_CHUNK]
        mark_input = m.Mark(operation_key=ids['mark_keys'][n] if n < len(ids['mark_keys']) else new_id(),
                            draft=value.id, expected_version=value.version,
                            entries=tuple(m.MarkEntry(movement=c.ref[0], group_fingerprint=c.ref[1],
                                                      action='mark') for c in chunk))
        value = drafts.mark(snapshot, value, mark_input, revision_id=new_id())
        marks.append(mark_input)
    marked = {id(c) for c in to_mark}
    unsupported = files.cleared_without_line(results, candidates) if draft is not None else []

    out_lines = []
    for result in results:
        line, match = result.line, result.match
        out_lines.append(ImportedLine(
            line_id=line.line_id, fitid=line.fitid, date=line.date, amount=sign * line.amount,
            amount_decimal=_decimal(sign * line.amount, places), payee=line.payee, memo=line.memo,
            number=line.number, status=result.status, reason=result.reason,
            movement=match.ref[0] if match else None, group_fingerprint=match.ref[1] if match else None,
            already_marked=bool(match and match.selected), marked=bool(match and id(match) in marked),
            suggestions=tuple(Suggestion(movement=c.ref[0], group_fingerprint=c.ref[1], date=c.date,
                                         amount=c.amount, number=c.number, payees=c.payees,
                                         memo=c.memo or None) for c in result.suggestions)))
    for line in parsed.duplicates:
        out_lines.append(ImportedLine(
            line_id=line.line_id, fitid=line.fitid, date=line.date, amount=line.amount,
            amount_decimal=_decimal(line.amount, places), payee=line.payee, memo=line.memo,
            number=line.number, status='duplicate', reason='the file repeats this FITID; read once'))
    count = lambda status: sum(1 for v in out_lines if v.status == status)
    counts = ImportCounts(lines=len(out_lines), matched=count('matched'), suggested=count('suggested'),
                          unmatched=count('unmatched'), reconciled=count('reconciled'),
                          duplicate=count('duplicate'), newly_marked=len(to_mark),
                          cleared_without_line=len(unsupported))
    if value is None:
        step = ('Review only: pass start (or draft) to tick the matched entries on a reconciliation. '
                'Enter each unmatched line as a transaction, then import again.')
    else:
        step = ('Enter each unmatched line as a transaction and import again, settle the suggestions with '
                '`reconcile mark`, then `reconcile preview` and `reconcile finish` draft ' + value.id + '.')
        if unsupported:
            step = (f'{len(unsupported)} ticked movement(s) match no line of this statement (cleared without '
                    'a statement line): untick each one the bank did not show, with `reconcile mark` '
                    'action unmark, unless you know why it is missing. ' + step)
    output = ImportOutput(account_id=account_id, currency=currency, format=parsed.format,
                          statement_date=statement_date, ending_balance=ending, counts=counts,
                          draft=value, draft_started=started is not None, lines=tuple(out_lines),
                          cleared_without_line=tuple(
                              Suggestion(movement=c.ref[0], group_fingerprint=c.ref[1], date=c.date,
                                         amount=c.amount, number=c.number, payees=c.payees,
                                         memo=c.memo or None) for c in unsupported),
                          next_step=step)
    return dict(output=output, account_id=account_id, started=started, start_value=draft if started else None,
                opening_draft=opening_draft, marks=marks)


def _commit(s, ctx, operation, *, account_id, operation_id, event_id, kind, summary, document,
            targets, rows, updates=(), touched=()):
    """`reconcile_cmds._commit`, with the audit event named for this command.

    The stored operation keeps the name of what it is -- a `reconcile start` or `reconcile mark`
    with that command's own request -- so the reconciliation's history and validation read it as
    exactly that; the audit trail says it came from `reconcile import`.
    """
    from bookflow.core import clock
    from bookflow.core.audit import write_event_to
    at = clock.now_iso()
    actor = s.actor
    audit_event_id = write_event_to(s.company, ctx, 'reconcile import', summary, list(touched),
                                    actor_id=actor.id if actor else None,
                                    actor_kind=actor.kind if actor else None)
    made = persistence.created(ctx, actor.id if actor else None, audit_event_id, at)
    everything = persistence.merge(
        persistence.receipt(operation_id, command=operation, operation_key=document['operation_key'],
                            request=document['request'], effect=document['effect'],
                            targets=targets, made=made),
        {'events': [persistence.event(event_id, operation_id=operation_id,
                                      audit_event_id=audit_event_id, kind=kind, ctx=ctx,
                                      actor_id=actor.id if actor else None, at=at)]},
        rows(made))
    for table in persistence.ORDER:
        values = everything.get(table)
        if values:
            s.company.conn.execute(rc.c.metadata.tables['reconciliation_' + table].insert(), values)
    for statement in updates:
        s.company.conn.execute(statement)
    rc.loading.prove_written(s, account_id, operation_id)
    return audit_event_id


def _ids(chunks=8):
    return dict(start_key=new_id(), draft=new_id(), start_revision=new_id(),
                mark_keys=[new_id() for _ in range(chunks)])


def _planner(inp, ctx, s):
    ids = _ids()
    prepared = _prepare(inp, ctx, s, ids)
    return Plan(prepared['output'], dict(ids=ids, input=inp))


ERRORS = sorted(set(rc.ERRORS) | {'E_AMOUNT_PRECISION'})

reconcile_import = command(
    'reconcile import', scope='company',
    description='Read a bank or credit card statement file (OFX, QFX or CSV text) against the account: '
                'each line comes back matched to one entry, suggested, unmatched (enter it), or already '
                'reconciled. Nothing is posted. With start (or draft) the matched entries are ticked on a '
                'statement reconciliation, started from the file\'s statement date and ending balance; finish '
                'it with `reconcile preview` and `reconcile finish`. Safe to run again: nothing already true '
                'is repeated. Use --dry-run to preview.',
    input_model=ImportInput, output_model=ImportOutput, writes={'company'}, required_role='standard',
    capability='ledger.post', accepts_idempotency_key=True, error_codes=ERRORS)(_planner)
reconcile_import.ledger = True


@reconcile_import.applier
def _apply(plan, ctx, s):
    inp, ids = plan.data['input'], plan.data['ids']
    prepared = _prepare(inp, ctx, s, ids)
    account_id = prepared['account_id']
    started = prepared['started']
    if started is not None:
        first = prepared['start_value']  # as `reconcile start` writes it, before any marks
        _commit(s, ctx, 'reconcile start', account_id=account_id, operation_id=new_id(),
                   event_id=new_id(), kind='draft_change',
                   summary='started a statement reconciliation from an imported statement',
                   document=dict(operation_key=started.operation_key, request=started.model_dump(mode='json'),
                                 effect=dict(draft=first.id, account=account_id, kind=first.kind)),
                   targets=rc._targets(account_id, drafts=[first.id]),
                   rows=lambda made: persistence.draft(first, made=made),
                   touched=[Touched('reconciliation_draft', first.id, 'create', None, 1,
                                    dict(account_id=account_id, kind=first.kind), db='company')])
    for mark_input in prepared['marks']:
        snapshot = rc._loaded(s, account_id)
        current = rc._draft(snapshot, mark_input.draft)
        value = drafts.mark(snapshot, current, mark_input, revision_id=new_id())
        previous = snapshot.by('drafts')[value.id]['current_revision_id']
        table = rc.c.reconciliation_drafts
        _commit(s, ctx, 'reconcile mark', account_id=account_id, operation_id=new_id(),
                   event_id=new_id(), kind='draft_change',
                   summary='marked ' + str(len(mark_input.entries)) + ' movements from an imported statement',
                   document=dict(operation_key=mark_input.operation_key,
                                 request=mark_input.model_dump(mode='json'),
                                 effect=dict(draft=value.id, version=value.version,
                                             selected=len(value.selections))),
                   targets=rc._targets(account_id, drafts=[value.id]),
                   rows=lambda made, value=value, previous=previous: {
                       k: v for k, v in persistence.draft(value, made=made, previous_revision_id=previous).items()
                       if k != 'drafts'},
                   updates=[table.update().where(table.c.id == value.id).values(
                       version=value.version, current_revision_id=value.current_revision_id)],
                   touched=[Touched('reconciliation_draft', value.id, 'update', value.version - 1,
                                    value.version, dict(selected=len(value.selections)), db='company')])
    output = prepared['output']
    if prepared['marks'] or started is not None:
        final = rc._draft(rc._loaded(s, account_id), output.draft.id)
        output = output.model_copy(update={'draft': final})
    counts = output.counts
    summary = (f'imported a {output.format} statement: {counts.matched} matched, {counts.suggested} suggested, '
               f'{counts.unmatched} unmatched, {counts.newly_marked} newly marked')
    return Applied(output, [], summary, audited=started is not None or bool(prepared['marks']))
