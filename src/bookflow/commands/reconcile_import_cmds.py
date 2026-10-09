"""`reconcile import`: a bank statement file (CSV, OFX or QFX) read against the account's books.

Each statement line comes back matched to one movement, suggested (an amount that fits but not
surely), unmatched (nothing in the books yet), or reconciled (cleared by a statement already
certified). Nothing is ever posted. With a draft, or with `start`, the matched movements are
ticked on a statement reconciliation, through the same `reconcile start` and `reconcile mark`
operations a person's clicks make, so the draft is finished the ordinary way.

Running it again changes nothing that is already true: a movement already ticked stays ticked,
a draft already started for the same statement date is reused, and a FITID the file repeats is
read once. Pasted text is never stored and never leaves the machine; a file given as an attachment
stays the attachment it already was.

The statement comes as pasted text (`content`) or as an attachment already in the company
(`attachment`), as `attachment add company_info <company id> FILE` returns it; over MCP that file
goes in `transport.input_file` of `attachment add`. `reconcile import` itself takes no
`transport.input_file`: a preview must read the file, and a command with a binary body is only
shown its digest when previewing.
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
    content: str | None = Field(None, min_length=1, max_length=5_000_000,
                                description=("The statement file's text, exactly as downloaded from the bank. "
                                             "Give this or `attachment`."),
                                json_schema_extra={'multiline': True, 'text_file': '.csv,.ofx,.qfx,.txt,text/csv,text/plain'})
    attachment: str | None = Field(None, min_length=1, max_length=26, description=(
        "An attachment in this company holding the statement file, as `attachment add company_info <company id> FILE` "
        "returns its id (over MCP the file goes in that command's transport.input_file). Give this or `content`."))
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
    mapping_name: str = Field('', max_length=80, description=(
        "Use the CSV mapping saved under this name for the account; columns given in csv_mapping override it."))
    save_mapping: str = Field('', max_length=80, description=(
        'Save the CSV mapping used for this import under this name for the account (a newer save of a name replaces it for later use).'))

    @model_validator(mode='after')
    def target(self):
        if (self.content is None) == (self.attachment is None):
            raise ValueError('give exactly one of content or attachment')
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
    previously_imported: bool = Field(False, description=(
        'This line (by FITID, or by its hash) was imported for this account before.'))


class ImportCounts(m.Model):
    lines: m.Count
    matched: m.Count
    suggested: m.Count
    unmatched: m.Count
    reconciled: m.Count
    duplicate: m.Count
    newly_marked: m.Count
    cleared_without_line: m.Count
    previously_imported: m.Count


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


def check_numbers(conn, revision_ids):
    """The number each of these revisions was written on a check with, keyed by revision.

    A check, a bill payment by check, a refund check and a sales tax payment by check each carry the
    number on the paper apart from the document reference Bookflow gives the entry; the bank prints
    the first, so that is the one a statement line is paired on.
    """
    import sqlalchemy as sa
    from bookflow.company import schema as c
    found = {}
    ids = sorted(revision_ids)
    for table in (c.check_instrument_revisions, c.customer_refund_profiles, c.ap_payment_profiles,
                  c.sales_tax_payment_profiles):
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            for row in conn.execute(sa.select(table.c.revision_id, table.c.check_number)
                                    .where(table.c.revision_id.in_(chunk))).mappings():
                if row['check_number'] and row['revision_id'] not in found:
                    found[row['revision_id']] = row['check_number']
    return found


def candidates(snapshot, account_id, cutoff, draft, conn=None):
    """Each whole movement on the account as a match candidate, in reconciliation sign."""
    values, _ = account_population(snapshot, account_id, cutoff)
    selected = {v.key_id for v in draft.selections} if draft is not None else set()
    claims = claimed(snapshot)
    result = []
    grouped = list(groups(values).values())
    movements = [m.MovementKey.model_validate_json(g[0]['movement_snapshot']) for g in grouped]
    written = check_numbers(conn, {v.revision_id for v in movements}) if conn is not None else {}
    for group, movement in zip(grouped, movements):
        first = group[0]
        display = json.loads(first['display_snapshot'])
        keys = {v['key_id'] for v in group}
        result.append(files.Candidate(
            ref=(movement, group_fingerprint(group)), date=first['effective_date'],
            amount=sum(statement_amount(v) for v in group),
            number=written.get(movement.revision_id) or display['number'] or '',
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
    from bookflow.company import statement_store as store
    mapping = {}
    if inp.mapping_name:
        mapping = store.saved_mapping(s.company.conn, account_id, inp.mapping_name)
        if mapping is None:
            raise files.invalid('mapping_name', f'no CSV mapping is saved as {inp.mapping_name!r} for this account')
    given = inp.csv_mapping.model_dump()
    default = CsvMapping().model_dump()
    mapping = {**default, **mapping, **{k: v for k, v in given.items() if v != default[k]}}
    if inp.attachment is not None:
        from bookflow.company.attachment_text import attachment_text
        text = attachment_text(s, inp.attachment, 'attachment')[1]
    else:
        text = inp.content
    parsed = files.parse(text, inp.format, places, mapping)
    if inp.save_mapping and parsed.format != 'csv':
        raise files.invalid('save_mapping', 'only a CSV file has a column mapping to save')
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
    population = candidates(snapshot, account_id, cutoff, draft, s.company.conn)
    results = files.match(lines, population, match_days=inp.match_days, suggest_days=inp.suggest_days)
    known = store.known_line_ids(s.company.conn, account_id)

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
    unsupported = []
    if draft is not None:
        # The draft's statement is every line imported into it before, and this file's.
        held, window = (store.draft_lines(s.company.conn, draft.id)
                        if started is None else (None, None))
        held_ids = {v.line_id for v in lines}
        statement = lines + [v for v in held or () if v.line_id not in held_ids]
        ticked = [c for c in population if c.eligible and (c.selected or id(c) in marked)]
        unsupported = files.cleared_without_line(statement, ticked,
                                                 suggest_days=max(inp.suggest_days, window or 0))

    out_lines = []
    for result in results:
        line, match = result.line, result.match
        out_lines.append(ImportedLine(
            line_id=line.line_id, fitid=line.fitid, date=line.date, amount=sign * line.amount,
            amount_decimal=_decimal(sign * line.amount, places), payee=line.payee, memo=line.memo,
            number=line.number, status=result.status, reason=result.reason,
            movement=match.ref[0] if match else None, group_fingerprint=match.ref[1] if match else None,
            already_marked=bool(match and match.selected), marked=bool(match and id(match) in marked),
            previously_imported=line.line_id in known,
            suggestions=tuple(Suggestion(movement=c.ref[0], group_fingerprint=c.ref[1], date=c.date,
                                         amount=c.amount, number=c.number, payees=c.payees,
                                         memo=c.memo or None) for c in result.suggestions)))
    for line in parsed.duplicates:
        out_lines.append(ImportedLine(
            line_id=line.line_id, fitid=line.fitid, date=line.date, amount=line.amount,
            amount_decimal=_decimal(line.amount, places), payee=line.payee, memo=line.memo,
            number=line.number, status='duplicate', reason='the file repeats this FITID; read once',
            previously_imported=line.line_id in known))
    count = lambda status: sum(1 for v in out_lines if v.status == status)
    counts = ImportCounts(lines=len(out_lines), matched=count('matched'), suggested=count('suggested'),
                          unmatched=count('unmatched'), reconciled=count('reconciled'),
                          duplicate=count('duplicate'), newly_marked=len(to_mark),
                          cleared_without_line=len(unsupported),
                          previously_imported=sum(1 for v in out_lines if v.previously_imported))
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
    return dict(output=output, text=text, account_id=account_id, started=started, start_value=draft if started else None,
                opening_draft=opening_draft, marks=marks, parsed=parsed, lines=lines, mapping=mapping,
                draft_id=value.id if value is not None else None, ending=ending,
                statement_date=statement_date)


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
    description='Read a bank or credit card statement file (OFX, QFX or CSV; pasted text, or an attachment by id) against the account: '
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
    stored = _store(s, ctx, inp, prepared, summary)
    return Applied(output, [], summary,
                   audited=started is not None or bool(prepared['marks']) or stored)


def _store(s, ctx, inp, prepared, summary):
    """Keep the draft's statement lines and any saved mapping; True when anything was written."""
    import hashlib
    from bookflow.company import statement_store as store
    from bookflow.core import clock
    from bookflow.core.audit import write_event_to
    conn = s.company.conn
    account_id, draft_id = prepared['account_id'], prepared['draft_id']
    new_lines = []
    if draft_id is not None:
        held = {v.line_id for v in (store.draft_lines(conn, draft_id)[0] or ())}
        new_lines = [v for v in prepared['lines'] if v.line_id not in held]
    saving = bool(inp.save_mapping) and store.saved_mapping(conn, account_id, inp.save_mapping) != prepared['mapping']
    if not new_lines and not saving:
        return False
    actor = s.actor
    touched = []
    import_id = None
    if new_lines:
        import_id = new_id()
        touched.append(Touched('statement_import', import_id, 'create', None, 1,
                               dict(draft_id=draft_id, new_lines=len(new_lines)), db='company'))
    if saving:
        touched.append(Touched('statement_csv_mapping', account_id, 'create', None, 1,
                               dict(name=inp.save_mapping), db='company'))
    event = write_event_to(s.company, ctx, 'reconcile import', summary, touched,
                           actor_id=actor.id if actor else None, actor_kind=actor.kind if actor else None)
    at = clock.now_iso()
    if new_lines:
        store.store(conn, identity=import_id, account_id=account_id, draft_id=draft_id,
                    parsed_format=prepared['parsed'].format,
                    file_sha256=hashlib.sha256(prepared['text'].encode()).hexdigest(),
                    statement_date=prepared['statement_date'], ending_balance=prepared['ending'],
                    suggest_days=inp.suggest_days, line_count=len(prepared['lines']), lines=new_lines,
                    made=dict(created_at=at, created_by=actor.id if actor else None,
                              created_via=ctx.interface.value, audit_event_id=event))
    if saving:
        store.save_mapping(conn, account_id=account_id, name=inp.save_mapping, mapping=prepared['mapping'],
                           created_at=at, created_by=actor.id if actor else None, audit_event_id=event)
    return True
