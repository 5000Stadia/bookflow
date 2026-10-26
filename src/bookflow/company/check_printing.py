"""Print Checks: the queue of cheques saved to be printed, print runs, "Did checks print OK?", reprints.

**The queue is derived, never stored.** A cheque saved "to be printed" -- a check from Write
Checks, a payment by check from Pay Bills, a sales tax payment by check from Pay Sales Tax, or a
customer refund by check (co0096) -- is written with no number and one immutable
``check_print_marks`` row. It is in the queue for its bank account while it was marked, carries no
number in ``check_instruments``, stands posted, and no print run is holding it. Nothing else says
it is queued, so nothing else has to be kept in step: typing a number on a correction, printing
it, or voiding it takes it out, and a misprint puts it back by itself.

**Numbers are given at print time** from the first number the person confirms is loaded in the
printer, consecutively, because preprinted paper is consecutive: a number already used on this
account is refused by name rather than skipped, since skipping would put cheque 1003's details on
paper that says 1004. Each number is checked against everything ``company/check_numbers.py`` checks
-- issued, retired, and (through this module) on paper in the printer or spoiled -- so one
chequebook still never carries one number twice.

**A run is on paper until somebody says otherwise.** ``check print`` writes the run and the number
each cheque took in it (audited) and returns the PDF; until ``check print-confirm`` answers, those
numbers are occupied and the cheques are out of the queue. Confirming is the anchor's
"Did checks print OK?": the checks that printed are given their number for good (the
``check_instruments`` row ``check post`` would have written), and the ones that did not go back to
the queue while their numbers are recorded in ``check_spoiled_numbers`` -- so ``report
missing-checks`` explains them and nothing hands them out again. ``check print-cancel`` is for a
run that never reached paper: its numbers are released, unspoiled.

**Who may do what.** Queueing cheques and preparing a run are bookkeeping, and an agent may do
both. Confirming a run, cancelling one and reprinting a cheque say what happened to paper in a
printer, which only a person standing at it knows, so they are people-only.

**A reprint** prints one numbered cheque again, from Write Checks or the register in the anchor.
With the same number nothing about the cheque changes. With a new number the paper carrying the
old one is spoiled: the old number is recorded as spoiled and the cheque's current number becomes
the new one. The revision the cheque is at keeps the number it was first written with in
``check_instrument_revisions`` (history is never rewritten), and the projection carries the new
one -- the document did not change, the paper did.

**Closed periods.** Giving a number to a cheque changes what the document says, so a cheque dated
on or before the closing date is not printed or renumbered: ``check print`` and ``check reprint
--new-number`` refuse it with E_PERIOD_CLOSED, naming the cheque. Confirming a run records what
the printer did with numbers already given while the period was open, and is never refused for
the period. Reprinting with the same number changes nothing and is always allowed.

**Sales tax payments and refunds** print like any other cheque: the stub of a remittance names
the agency and the period it paid through, and the stub of a refund names each credit memo or
overpayment it paid out. Their own profiles keep the number they were written with (none, for one
saved to be printed); the number they print with lives in ``check_instruments`` like every other
cheque's, and their outputs, the register and statement import read it from there. A refund is
corrected with a new revision, so a correction carries its printed number forward onto the new
revision (``carry_forward``), as a check's correction does.

**After printing.** A cheque that printed is paper with a payee and an amount on it, so ``check
update`` refuses to change its amount, its payee or its bank account (void it and write a new
one); the memo, date and lines that do not change the total stay correctable. A cheque in a run
awaiting confirmation cannot be corrected or voided at all until the run is answered.
"""
from __future__ import annotations

import base64
import json

import sqlalchemy as sa

from bookflow.company import accounts, check_numbers, journals, schema as c
from bookflow.company.check_print_models import (
    CheckPdfOutput, CheckPrintQueueOutput, CheckPrintRunOutput, CheckPrintSetupOutput,
    CheckPrintWriteOutput, PrintRunItem, PrintSettingsOutput, QueueAccount, QueuedCheck, SkippedCheck,
)
from bookflow.company.ledger_reports import money
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.registry import Applied, Plan, Touched

DOCUMENT_TYPES = {'journal_entry': 'check', 'bill_payment': 'bill_payment', 'sales_tax_payment': 'sales_tax_payment',
                  'customer_refund': 'customer_refund'}
DEFAULT_STYLE = 'voucher'


def _invalid(field, problem, **details):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}], **details})


def _conn(s):
    return s.company.conn


# ---------------------------------------------------------------- what the allocator asks


def _pending_items():
    """Print run items nobody has answered yet: numbers on paper in the printer."""
    i, o = c.check_print_run_items, c.check_print_outcomes
    return sa.select(i).where(~sa.exists(sa.select(o.c.item_id).where(o.c.item_id == i.c.id)))


def held_in_printer(s, account_id, key, *, excluding=None):
    """The cheque a number in an unanswered print run was printed for, shaped as an instrument."""
    i, r = c.check_print_run_items, c.check_print_runs
    pending = _pending_items().subquery()
    query = (sa.select(pending.c.transaction_id, pending.c.check_number)
             .join(r, r.c.id == pending.c.run_id)
             .where(r.c.account_id == account_id, pending.c.check_number_key == key))
    if excluding is not None:
        query = query.where(pending.c.transaction_id != excluding)
    found = _conn(s).execute(query).mappings().first()
    return dict(found) if found else None


def on_paper(s, account_id, keys, *, excluding=None):
    """Which of these numbers are on paper: in an unanswered run, or spoiled."""
    if not keys:
        return set()
    r, z = c.check_print_runs, c.check_spoiled_numbers
    pending = _pending_items().subquery()
    query = (sa.select(pending.c.check_number_key).join(r, r.c.id == pending.c.run_id)
             .where(r.c.account_id == account_id, pending.c.check_number_key.in_(sorted(keys))))
    if excluding is not None:
        query = query.where(pending.c.transaction_id != excluding)
    found = {row[0] for row in _conn(s).execute(query)}
    found.update(row[0] for row in _conn(s).execute(sa.select(z.c.check_number_key).where(
        z.c.account_id == account_id, z.c.check_number_key.in_(sorted(keys)))))
    found.update(typed_elsewhere(s, account_id, keys, excluding=excluding))
    return found


def highest_on_paper(s, account_id):
    r, i, z = c.check_print_runs, c.check_print_run_items, c.check_spoiled_numbers
    printed = _conn(s).execute(sa.select(sa.func.max(i.c.check_sequence)).join(r, r.c.id == i.c.run_id)
                               .where(r.c.account_id == account_id)).scalar()
    spoiled = _conn(s).execute(sa.select(sa.func.max(z.c.check_sequence)).where(
        z.c.account_id == account_id)).scalar()
    return max(int(printed or 0), int(spoiled or 0))


# ---------------------------------------------------------------- one cheque, as it prints


def _bank(s, selector, field='account'):
    from bookflow.company.check_models import FUNDING_TYPE
    account = accounts.resolve_account(s.company, selector, follow_merge=True)
    if account['type'] != FUNDING_TYPE['check']:
        raise _invalid(field, f'cheques are printed on a bank account; "{account["name"]}" is a '
                              f'{account["type"].replace("_", " ")} account')
    return account


def _header(s, transaction_id):
    t = c.transactions
    key = transaction_id.upper() if is_ulid(transaction_id) else transaction_id
    row = _conn(s).execute(sa.select(t).where(t.c.id == key)).mappings().first()
    return dict(row) if row else None


def _payee(s, name_type, name_id, fallback):
    """The name and envelope address a cheque is printed with."""
    from bookflow.documents.model import address_block
    tables = {'vendor': (c.vendors, 'address'), 'customer': (c.customers, 'billing'),
              'employee': (c.employees, 'address'), 'other_name': (c.other_names, 'address')}
    if name_type not in tables or not name_id:
        return fallback or '', ()
    table, prefix = tables[name_type]
    row = _conn(s).execute(sa.select(table).where(table.c.id == name_id)).mappings().first()
    if row is None:
        return fallback or '', ()
    row = dict(row)
    name = row.get('print_name_on_check_as') or fallback or row.get('full_name') or row.get('name') or ''
    address = {leaf: row.get(f'{prefix}_{leaf}') for leaf in ('line1', 'line2', 'city', 'state', 'postal_code', 'country')}
    return name, tuple(address_block(address))


def facts(s, header):
    """Everything about one cheque that printing reads: who, how much, when, from which account."""
    from bookflow.company import check_items, money_out
    revision = journals.revision(s, header)
    base = dict(transaction_id=header['id'], type=header['type'], document_type=DOCUMENT_TYPES.get(header['type']),
                reference=header['number'], status=header['status'], version=header['version'],
                revision=revision, date=revision['date'], memo=revision['memo'])
    if header['type'] == 'journal_entry':
        lines = money_out.lines(s, revision)
        funding = check_items.funding(s, lines)
        captured = check_items.stored(s, lines)
        item_ids = {row['line_id'] for row in captured}
        stub = []
        for line in lines:
            if line['kind'] != 'journal' or line['line_id'] == funding.get('line_id') or line['line_id'] in item_ids:
                continue
            snapshot = json.loads(line['account_snapshot']) if line['account_snapshot'] else {}
            signed = line['amount_minor_units'] if line['side'] == 'debit' else -line['amount_minor_units']
            stub.append(('expense', snapshot.get('full_name') or snapshot.get('name') or '', line['description'] or '', signed))
        for row in captured:
            item = getattr(row['profile'], 'item', None)
            stub.append(('item', getattr(item, 'label', None) or 'Item', row['memo'] or '', row['amount_minor_units']))
        base.update(account_id=funding['account_id'], amount_minor_units=funding['amount_minor_units'],
                    currency=funding['currency'], name_type=funding.get('name_type'), name_id=funding.get('name_id'),
                    party_name=funding.get('party_name'), stub=stub)
    elif header['type'] == 'sales_tax_payment':
        from bookflow.company import sales_tax_payments
        from bookflow.company.sales_tax_payment_facts import SalesTaxPaymentProfile
        profile = sales_tax_payments.profile_row(s, revision)
        captured = SalesTaxPaymentProfile.model_validate_json(profile['profile_snapshot'])
        # One line: the agency and the period the remittance answered, with the agency's own
        # account number when it has one, which is what an agency files the paper under.
        stub = [('tax', captured.agency.label, captured.agency.account_number or '', profile['through_date'],
                 profile['amount_minor_units'])]
        base.update(account_id=profile['funding_account_id'], amount_minor_units=profile['amount_minor_units'],
                    currency=revision['currency'], name_type='vendor', name_id=profile['agency_id'],
                    party_name=captured.agency.label, stub=stub)
    elif header['type'] == 'customer_refund':
        from bookflow.company import refunds
        from bookflow.company.refund_facts import CustomerRefundProfile
        profile = refunds.profile_row(s, revision)
        captured = CustomerRefundProfile.model_validate_json(profile['profile_snapshot'])
        stub = [('refund', ('Credit memo ' if row.credit_memo_id else 'Overpayment, payment ') + row.document_number,
                 row.credit_memo_date or row.payment_date or '', row.amount_minor_units)
                for row in captured.sources]
        base.update(account_id=profile['funding_account_id'], amount_minor_units=profile['amount_minor_units'],
                    currency=revision['currency'], name_type='customer', name_id=profile['party_id'],
                    party_name=captured.customer.label, stub=stub)
    else:
        from bookflow.company import bill_payments
        profile = bill_payments.profile_row(s, revision)
        shown = bill_payments._output(s, header, revision, profile)
        stub = []
        for application in shown.applications:
            if application.kind != 'apply' or not application.active:
                continue
            bill = _header(s, application.obligation_transaction_id)
            bill_revision = journals.revision(s, bill) if bill else None
            stub.append(('bill', application.bill_number, bill_revision['date'] if bill_revision else '',
                         bill_revision['total_minor_units'] if bill_revision else None,
                         application.discount_minor_units,
                         # What the cheque paid on the bill: the application settles the
                         # discount as well, so the money is what is left after it, and the
                         # stub's Paid column adds up to the face.
                         application.amount_minor_units - application.discount_minor_units))
        base.update(account_id=profile['funding_account_id'], amount_minor_units=profile['amount_minor_units'],
                    currency=revision['currency'], name_type='vendor', name_id=profile['vendor_id'],
                    party_name=shown.vendor_name, stub=stub)
    base['payee'], base['address'] = _payee(s, base['name_type'], base['name_id'], base['party_name'])
    return base


def printed(s, transaction_id):
    """Whether this cheque has come out of a printer and been confirmed as good paper."""
    i, o = c.check_print_run_items, c.check_print_outcomes
    return _conn(s).execute(sa.select(i.c.id).join(o, o.c.item_id == i.c.id).where(
        i.c.transaction_id == transaction_id, o.c.outcome == 'printed').limit(1)).first() is not None


def in_printer(s, transaction_id):
    """The unanswered print run holding this cheque, if any."""
    pending = _pending_items().subquery()
    return _conn(s).execute(sa.select(pending.c.run_id).where(
        pending.c.transaction_id == transaction_id).limit(1)).scalar()


def marked(s, transaction_id):
    m = c.check_print_marks
    return _conn(s).execute(sa.select(m.c.transaction_id).where(m.c.transaction_id == transaction_id)).first() is not None


def queued(s, transaction_id):
    """Whether this cheque is waiting in its account's print queue."""
    header = _header(s, transaction_id)
    return bool(header and header['status'] == 'posted' and marked(s, header['id'])
                and check_numbers.current(s, header['id']) is None and in_printer(s, header['id']) is None
                and not _deleted(s, header['id']))


def print_status(s, transaction_id):
    """queued, in_run or printed, for a cheque saved to be printed; None for one written by hand."""
    if in_printer(s, transaction_id):
        return 'in_run'
    if printed(s, transaction_id):
        return 'printed'
    if marked(s, transaction_id) and check_numbers.current(s, transaction_id) is None:
        return 'queued'
    return None


def guard_correction(s, header, *, before, after, voiding=False):
    """What a correction or void of a cheque may not do once it is in a printer or on paper."""
    run = in_printer(s, header['id'])
    if run:
        raise _invalid('check', f'cheque {header["number"]} is in print run {run}, which is waiting for '
                                f'"Did checks print OK?"; confirm or cancel that run first', run=run)
    if voiding or before is None or not printed(s, header['id']):
        return
    for key, word in (('amount_minor_units', 'amount'), ('account_id', 'bank account'),
                      (('name_type', 'name_id'), 'payee')):
        old = tuple(before.get(k) for k in key) if isinstance(key, tuple) else before.get(key)
        new = tuple(after.get(k) for k in key) if isinstance(key, tuple) else after.get(key)
        if old != new:
            held = check_numbers.current(s, header['id'])
            number = held['check_number'] if held else header['number']
            raise _invalid(word.replace('bank ', ''), (
                f'check {number} has been printed, so its {word} is on paper; void it and write a new check '
                f'instead of changing the {word}'), printed=True)


def _deleted(s, transaction_id):
    for table in ('purchase_deletions',):
        if sa.inspect(_conn(s)).has_table(table):
            t = getattr(c, table)
            if _conn(s).execute(sa.select(t.c.transaction_id).where(t.c.transaction_id == transaction_id)).first():
                return True
    return False


def printed_number(s, transaction_id):
    """The number a cheque carries in its chequebook now, or None: what a reader shows as its check number."""
    held = check_numbers.current(s, transaction_id)
    return held['check_number'] if held else None


def shown_number(s, transaction_id, typed):
    """The check number a sales tax payment or refund shows: the printed one, else the one typed on it."""
    if not sa.inspect(_conn(s)).has_table('check_print_marks'):
        return typed
    return printed_number(s, transaction_id) or typed


def may_print(to_be_printed, *, method_kind, bank, typed_number):
    """The one rule for saving a sales tax payment or a refund to be printed."""
    if not to_be_printed:
        return
    if method_kind != 'check' or not bank:
        raise _invalid('to_be_printed', 'only a payment by check from a bank account is printed')
    if typed_number is not None:
        raise _invalid('check_number', 'a cheque saved to be printed takes its number when it prints; '
                                       'leave check_number empty')


def guard_typed_number(s, header, number):
    """A refund correction may not type a number over one the print layer owns."""
    if number is None:
        return
    status = print_status(s, header['id'])
    if status == 'queued':
        raise _invalid('check_number', 'this refund is waiting in the print queue and takes its number when it '
                                       'prints; print it with `check print`')
    if status in ('in_run', 'printed'):
        raise _invalid('check_number', 'this refund was printed with a number from a print run; reprint it with '
                                       '`check reprint --new-number` to change the number on the paper')


def carry_forward(s, ctx, header, revision, *, at, event):
    """A correction's new revision keeps the number its cheque was printed with (refunds)."""
    held = check_numbers.current(s, header['id'])
    if held is None:
        return None
    settled = {key: held[key] for key in check_numbers.IDENTITY} | {'pointer': None}
    return check_numbers.rows(s, ctx, header, revision, settled, held, at=at, event=event)


def typed_elsewhere(s, account_id, keys, *, excluding=None):
    """Numbers typed by hand on a sales tax payment or refund drawn on this account.

    Those two keep a hand-written number on their own profile rather than in the chequebook, so
    the allocator and the print run ask here too: a number already on paper is never handed out again.
    """
    if not keys:
        return set()
    found = set()
    for table in (c.sales_tax_payment_profiles, c.customer_refund_profiles):
        query = sa.select(table.c.check_number).where(table.c.funding_account_id == account_id,
                                                      table.c.check_number.is_not(None))
        if excluding is not None:
            query = query.where(table.c.transaction_id != excluding)
        for (number,) in _conn(s).execute(query):
            try:
                key = check_numbers.canonical(number)[1]
            except BookflowError:
                continue
            if key in keys:
                found.add(key)
    return found


def mark_row(header_id, type_, *, at, actor_id, interface, event):
    return dict(transaction_id=header_id, type=type_, created_at=at, created_by=actor_id,
                created_via=interface, audit_event_id=event)


# ---------------------------------------------------------------- the queue


def _queue(s, account_id=None):
    t, r, m = c.transactions, c.transaction_revisions, c.check_print_marks
    pending = _pending_items().subquery()
    query = (sa.select(t.c.id).select_from(m.join(t, t.c.id == m.c.transaction_id)
                                         .join(r, r.c.id == t.c.current_revision_id))
             .where(t.c.status == 'posted',
                    ~sa.exists(sa.select(c.check_instruments.c.transaction_id).where(
                        c.check_instruments.c.transaction_id == t.c.id)),
                    ~sa.exists(sa.select(pending.c.id).where(pending.c.transaction_id == t.c.id)))
             .order_by(r.c.date, t.c.created_at, t.c.id))
    found = []
    for (identifier,) in _conn(s).execute(query):
        if _deleted(s, identifier):
            continue
        fact = facts(s, _header(s, identifier))
        if account_id is None or fact['account_id'] == account_id:
            found.append(fact)
    return found


def _settings(s, account_id):
    t = c.check_print_settings
    row = _conn(s).execute(sa.select(t).where(t.c.account_id == account_id)).mappings().first()
    return dict(row) if row else None


def settings_output(s, account_id):
    row = _settings(s, account_id)
    if row is None:
        return PrintSettingsOutput(account_id=account_id, style=DEFAULT_STYLE, offset_x_hundredths=0,
                                   offset_y_hundredths=0, version=None)
    return PrintSettingsOutput(account_id=account_id, style=row['style'], offset_x_hundredths=row['offset_x_hundredths'],
                               offset_y_hundredths=row['offset_y_hundredths'], version=row['version'])


def _queued_output(fact):
    return QueuedCheck(transaction_id=fact['transaction_id'], document_type=fact['document_type'],
                       reference=fact['reference'], account_id=fact['account_id'], date=fact['date'],
                       payee_name=fact['payee'] or None, amount=money(fact['amount_minor_units'], fact['currency']),
                       memo=fact['memo'])


def _proposed_first(s, account):
    start, width, state = check_numbers.pointer(account)
    if state == 'opaque':
        return None
    value = start if start is not None else check_numbers._highest_issued(s, account['id']) + 1
    while True:
        literal = check_numbers._formatted(value, width)
        if not check_numbers._ever_issued(s, account['id'], {check_numbers.canonical(literal)[1]}) \
                and check_numbers._held(s, account['id'], check_numbers.canonical(literal)[1]) is None:
            return literal
        value += 1


def queue(s, inp):
    account = _bank(s, inp.account) if inp.account else None
    found = _queue(s, account['id'] if account else None)
    grouped = {}
    for fact in found:
        grouped.setdefault(fact['account_id'], []).append(fact)
    if account is not None:
        grouped.setdefault(account['id'], [])
    out = []
    for account_id, rows in grouped.items():
        row = accounts.resolve_account(s.company, account_id)
        currency = rows[0]['currency'] if rows else journals_home(s)
        out.append(QueueAccount(account_id=account_id, account_name=row['full_name'] or row['name'],
                                next_check_number=_proposed_first(s, row), settings=settings_output(s, account_id),
                                checks=[_queued_output(fact) for fact in rows],
                                total=money(sum(fact['amount_minor_units'] for fact in rows), currency)))
    out.sort(key=lambda a: a.account_name.casefold())
    r = c.check_print_runs
    waiting = sorted({row.run_id for row in _conn(s).execute(sa.select(_pending_items().subquery().c.run_id))
                      if account is None or _run_row(s, row.run_id)['account_id'] == account['id']})
    return CheckPrintQueueOutput(accounts=out, count=len(found), awaiting_confirmation=waiting)


def journals_home(s):
    return _conn(s).execute(sa.select(c.company_info.c.home_currency)).scalar_one()


# ---------------------------------------------------------------- reading a run


def _run_row(s, run_id):
    r = c.check_print_runs
    key = run_id.upper() if is_ulid(run_id) else run_id
    row = _conn(s).execute(sa.select(r).where(r.c.id == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'check_print_run', 'selector': run_id})
    return dict(row)


def _items(s, run_id):
    i, o = c.check_print_run_items, c.check_print_outcomes
    rows = _conn(s).execute(sa.select(i, o.c.outcome).outerjoin(o, o.c.item_id == i.c.id)
                            .where(i.c.run_id == run_id).order_by(i.c.position)).mappings()
    return [dict(row) for row in rows]


def run_output(s, run, items=None, pending_facts=None, *, cls=CheckPrintRunOutput, **extra):
    from bookflow.documents.check_pdf import amount_in_words
    items = items if items is not None else _items(s, run['id'])
    account = accounts.resolve_account(s.company, run['account_id'])
    shown, currency, total = [], journals_home(s), 0
    for item in items:
        fact = (pending_facts or {}).get(item['transaction_id']) or facts(s, _header(s, item['transaction_id']))
        currency = fact['currency']
        total += fact['amount_minor_units']
        shown.append(PrintRunItem(
            item_id=item['id'], position=item['position'], transaction_id=item['transaction_id'],
            document_type=fact['document_type'], check_number=item['check_number'], date=fact['date'],
            payee_name=fact['payee'] or None, amount=money(fact['amount_minor_units'], fact['currency']),
            amount_in_words=amount_in_words(fact['amount_minor_units'], fact['currency']),
            outcome=item.get('outcome') or 'pending'))
    outcomes = {item.outcome for item in shown}
    status = ('awaiting_confirmation' if 'pending' in outcomes else
              'cancelled' if outcomes == {'cancelled'} else 'confirmed')
    z = c.check_spoiled_numbers
    spoiled = [row[0] for row in _conn(s).execute(sa.select(z.c.check_number).where(z.c.run_id == run['id'])
                                                  .order_by(z.c.check_sequence))]
    return cls(id=run['id'], kind=run['kind'], account_id=run['account_id'],
               account_name=account['full_name'] or account['name'], style=run['style'],
               first_number=run['first_number'], checks_on_first_page=run['checks_on_first_page'],
               offset_x_hundredths=run['offset_x_hundredths'], offset_y_hundredths=run['offset_y_hundredths'],
               status=status, created_at=run['created_at'], created_by=run['created_by'], items=shown,
               total=money(total, currency), spoiled_numbers=extra.pop('spoiled', spoiled),
               pdf=f'check print-pdf --run {run["id"]}', **extra)


def show_run(s, inp):
    return run_output(s, _run_row(s, inp.run))


# ---------------------------------------------------------------- the PDF


def _printed_check(fact, number, sample=False):
    from bookflow.documents.check_pdf import PrintedCheck, StubLine
    from bookflow.documents.check_pdf import amount_in_figures

    def shown(units):
        # Grouped like the figure on the face (1,234.56), without its protective asterisks.
        if units is None:
            return ''
        return ('-' if units < 0 else '') + amount_in_figures(abs(units), fact['currency']).lstrip('*')

    if fact['type'] == 'sales_tax_payment':
        heading = ('Sales tax agency', 'Agency account', 'Tax through', '', 'Amount')
        lines = tuple(StubLine((agency, account, through, '', shown(amount)))
                      for _, agency, account, through, amount in fact['stub'])
    elif fact['type'] == 'customer_refund':
        heading = ('Refund of', 'Dated', '', '', 'Refunded')
        lines = tuple(StubLine((ref, date, '', '', shown(amount))) for _, ref, date, amount in fact['stub'])
    elif fact['type'] == 'bill_payment':
        heading = ('Bill', 'Date', 'Original', 'Discount', 'Paid')
        lines = tuple(StubLine((ref, date, shown(original), shown(discount) if discount else '', shown(paid)))
                      for _, ref, date, original, discount, paid in fact['stub'])
    else:
        heading = ('Account / item', 'Memo', '', '', 'Amount')
        lines = tuple(StubLine((name, memo, '', '', shown(amount))) for _, name, memo, amount in fact['stub'])
    return PrintedCheck(number=number, date=fact['date'], payee=fact['payee'], amount_minor_units=fact['amount_minor_units'],
                        currency=fact['currency'], memo=fact['memo'] or '', address=fact['address'],
                        account_name=fact.get('account_name', ''), stub_heading=heading, stub_lines=lines,
                        stub_total=shown(fact['amount_minor_units']), sample=sample)


def pdf(s, inp):
    from bookflow.documents.check_pdf import Alignment, arrange, to_pdf
    from bookflow.documents.render import filename_for
    run = _run_row(s, inp.run)
    account = accounts.resolve_account(s.company, run['account_id'])
    checks = []
    for item in _items(s, run['id']):
        if item.get('outcome') in ('failed', 'cancelled'):
            continue
        fact = facts(s, _header(s, item['transaction_id']))
        fact['account_name'] = account['full_name'] or account['name']
        checks.append(_printed_check(fact, item['check_number']))
    alignment = Alignment(run['offset_x_hundredths'], run['offset_y_hundredths'])
    content = to_pdf(checks, run['style'], alignment=alignment, checks_on_first_page=run['checks_on_first_page'],
                     title=f'Checks {run["first_number"]}')
    pages = arrange(checks, run['style'], run['checks_on_first_page']).pages
    return CheckPdfOutput(filename=filename_for('checks', (account['name'], run['first_number'])),
                          media_type='application/pdf', pages=max(pages, 1), checks=len(checks),
                          content_base64=base64.b64encode(content).decode('ascii'))


def alignment_sample(s, inp):
    """The anchor's Print Sample: one cheque of made-up figures at the offsets being tried."""
    from bookflow.documents.check_pdf import Alignment, PrintedCheck, StubLine, to_pdf
    from bookflow.documents.render import filename_for
    account = _bank(s, inp.account)
    saved = settings_output(s, account['id'])
    style = inp.style or saved.style
    alignment = Alignment(saved.offset_x_hundredths if inp.offset_x_hundredths is None else inp.offset_x_hundredths,
                          saved.offset_y_hundredths if inp.offset_y_hundredths is None else inp.offset_y_hundredths)
    if style == 'voucher' and inp.checks_on_first_page != 3:
        raise _invalid('checks_on_first_page', 'voucher checks print one to a page')
    currency = journals_home(s)
    sample = PrintedCheck(number='SAMPLE', date='2026-01-01', payee='Sample Payee', amount_minor_units=123456,
                          currency=currency, memo='Alignment sample', address=('100 Sample Street', 'Sampletown, ST 00000'),
                          account_name=account['full_name'] or account['name'],
                          stub_heading=('Account / item', 'Memo', '', '', 'Amount'),
                          stub_lines=(StubLine(('Sample expense', 'Alignment', '', '', '1,234.56')),),
                          stub_total='1,234.56', sample=True)
    content = to_pdf([sample], style, alignment=alignment, checks_on_first_page=inp.checks_on_first_page,
                     title='Check alignment sample')
    return CheckPdfOutput(filename=filename_for('check-alignment', (account['name'], style)),
                          media_type='application/pdf', pages=1, checks=1,
                          content_base64=base64.b64encode(content).decode('ascii'))


# ---------------------------------------------------------------- numbers for a run


def _numbers(s, account, first, count, *, excluding=None):
    """``count`` consecutive numbers from ``first``, each free on this account, or a refusal."""
    literal, key, sequence = check_numbers.canonical(first)
    if sequence is None:
        raise _invalid('first_number', 'a printed check number is a plain run of digits')
    width = len(literal) if literal.startswith('0') else None
    out = []
    for offset in range(count):
        value = sequence + offset
        if value >= check_numbers.SEQUENCE_LIMIT:
            raise BookflowError('E_VALUE_RANGE', details={'field': 'first_number'})
        text = check_numbers._formatted(value, width)
        _, k, seq = check_numbers.canonical(text)
        holder = check_numbers._held(s, account['id'], k, excluding=excluding)
        if holder is not None:
            raise check_numbers.duplicate(account, text, holder)
        if check_numbers._ever_issued(s, account['id'], {k}, excluding=excluding):
            raise BookflowError('E_DUPLICATE_NUMBER', message=(
                f'Check number {text} on "{account["name"]}" was already used -- retired by a correction or '
                f'spoiled in an earlier print run -- so it cannot be printed again. Start the run after it.'), details={
                'fields': [{'field': 'first_number', 'problem': f'{text} was already used on "{account["name"]}"'}],
                'number': text, 'account_id': account['id']})
        out.append((text, k, seq))
    return out, check_numbers._formatted(sequence + count, width)


def _move_pointer(s, account, after_last):
    """Leave the account's next check number past the run, never backwards."""
    start, width, state = check_numbers.pointer(account)
    _, _, sequence = check_numbers.canonical(after_last)
    if state == 'opaque' or (start is not None and sequence <= start):
        return None
    return after_last


def _created(s, ctx, at, event):
    return dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)


# ---------------------------------------------------------------- check print


def _print_plan(s, ctx, inp, at, event):
    account = _bank(s, inp.account)
    waiting = _queue(s, account['id'])
    if inp.checks is not None:
        wanted = []
        by_id = {fact['transaction_id']: fact for fact in waiting}
        for selector in inp.checks:
            key = selector.upper() if is_ulid(selector) else selector
            if key not in by_id:
                raise _invalid('checks', f'{selector} is not waiting to be printed on "{account["name"]}"')
            if by_id[key] not in wanted:
                wanted.append(by_id[key])
        waiting = [fact for fact in waiting if fact in wanted]
    if not waiting:
        raise _invalid('account', f'no cheques are waiting to be printed on "{account["name"]}"')
    closing = _conn(s).execute(sa.select(c.company_info.c.closing_date)).scalar_one()
    skipped = []
    if inp.checks is None and closing:
        # A bare run prints what it can: a cheque dated in a closed period (a misprint left
        # behind when the period closed, say) is left in the queue and named, rather than
        # holding up every other cheque on the account.
        skipped = [SkippedCheck(transaction_id=fact['transaction_id'], document_type=fact['document_type'],
                                reference=fact['reference'], date=fact['date'], payee_name=fact['payee'] or None,
                                reason=f'dated {fact["date"]}, on or before the closing date {closing}')
                   for fact in waiting if fact['date'] <= closing]
        waiting = [fact for fact in waiting if fact['date'] > closing]
        if not waiting:
            raise BookflowError('E_PERIOD_CLOSED', message=(
                f'Every cheque waiting on "{account["name"]}" is dated on or before the closing date {closing}; '
                f'printing one would give a closed-period document its number.'),
                details={'closing_date': closing, 'skipped': [row.transaction_id for row in skipped]})
    for fact in waiting:
        if not fact['name_id']:
            raise _invalid('checks', (
                f'{fact["document_type"].replace("_", " ")} {fact["reference"]} dated {fact["date"]} for '
                f'{money(fact["amount_minor_units"], fact["currency"]).amount} has no payee, so there is no name or address '
                f'to print; set its pay_to with `check update`, or leave it out of this run with `checks`'),
                transaction_id=fact['transaction_id'])
        if closing and fact['date'] <= closing:
            raise BookflowError('E_PERIOD_CLOSED', message=(
                f'{fact["document_type"].replace("_", " ").capitalize()} {fact["reference"]} to {fact["payee"]} is dated '
                f'{fact["date"]}, on or before the closing date {closing}; printing it would give a closed-period '
                f'document its number. Leave it out of this run with `checks`.'),
                details={'date': fact['date'], 'closing_date': closing, 'transaction_id': fact['transaction_id']})
    saved = settings_output(s, account['id'])
    style = inp.style or saved.style
    if style == 'voucher' and inp.checks_on_first_page != 3:
        raise _invalid('checks_on_first_page', 'voucher checks print one to a page, so there is no partial first sheet')
    first = inp.first_number or _proposed_first(s, account)
    if first is None:
        raise _invalid('first_number', f'"{account["name"]}" has no next check number that counts; type the number '
                                       f'on the first check in the printer')
    numbers, after_last = _numbers(s, account, first, len(waiting))
    run = dict(id=new_id(), account_id=account['id'], kind='batch', style=style, first_number=numbers[0][0],
               checks_on_first_page=inp.checks_on_first_page, offset_x_hundredths=saved.offset_x_hundredths,
               offset_y_hundredths=saved.offset_y_hundredths, **_created(s, ctx, at, event))
    items = [dict(id=new_id(), run_id=run['id'], transaction_id=fact['transaction_id'], position=position,
                  check_number=text, check_number_key=key, check_sequence=seq, **_created(s, ctx, at, event))
             for position, (fact, (text, key, seq)) in enumerate(zip(waiting, numbers), 1)]
    pointer = _move_pointer(s, account, after_last)
    output = run_output(s, run, items, {fact['transaction_id']: fact for fact in waiting}, cls=CheckPrintWriteOutput,
                        spoiled=[], skipped=skipped)
    return output, dict(run=run, items=items, pointer=(account, pointer) if pointer else None)


def _account_touch(s, account, value):
    after = dict(account, next_check_number=value)
    return Touched('account', account['id'], 'update', account['version'], account['version'], after, account, db='company')


def prepare_print(s, ctx, inp):
    output, _ = _print_plan(s, ctx, inp, clock.now_iso(), new_id())
    return Plan(output, {'input': inp})


def apply_print(plan, ctx, s):
    at, event = clock.now_iso(), new_id()
    output, writes = _print_plan(s, ctx, plan.data['input'], at, event)
    run, items = writes['run'], writes['items']
    touched = [Touched('check_print_run', run['id'], 'create', None, 1, run, db='company')]
    touched += [Touched('check_print_run_item', item['id'], 'create', None, 1, item, db='company') for item in items]
    if writes['pointer']:
        touched.append(_account_touch(s, *writes['pointer']))
    summary = (f'printed {len(items)} check{"s" if len(items) != 1 else ""} {items[0]["check_number"]}'
               + (f'-{items[-1]["check_number"]}' if len(items) > 1 else '') + f' on {output.account_name}')
    _audit(s, ctx, 'check print', summary, touched, event)
    _conn(s).execute(c.check_print_runs.insert().values(**run))
    _conn(s).execute(c.check_print_run_items.insert(), items)
    if writes['pointer']:
        account, value = writes['pointer']
        _conn(s).execute(c.accounts.update().where(c.accounts.c.id == account['id']).values(next_check_number=value))
    return Applied(output, touched, summary, audited=True)


def _audit(s, ctx, name, summary, touched, event):
    audit.write_event_to(s.company, ctx, name, summary, touched, actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=event)


# ---------------------------------------------------------------- check print-confirm / print-cancel


def _instrument(s, ctx, header, account_id, number, at, event):
    """The chequebook row a printed cheque carries from now on: what ``check post`` would have written."""
    from bookflow.company.money_out_schema import CHEQUE_DOCUMENT_TYPES
    if header['type'] not in CHEQUE_DOCUMENT_TYPES:
        raise BookflowError('E_INTERNAL', message=f'A {header["type"]} carries no cheque number.')
    revision = journals.revision(s, header)
    literal, key, sequence = check_numbers.canonical(number)
    settled = dict(account_id=account_id, check_number=literal, check_number_key=key, check_sequence=sequence,
                   origin='issued', pointer=None)
    holder = _conn(s).execute(sa.select(c.check_instruments).where(
        c.check_instruments.c.account_id == account_id, c.check_instruments.c.check_number_key == key,
        c.check_instruments.c.transaction_id != header['id'])).mappings().first()
    if holder is not None:
        raise check_numbers.duplicate(accounts.resolve_account(s.company, account_id), literal, dict(holder))
    if _conn(s).execute(sa.select(c.check_instrument_revisions.c.revision_id).where(
            c.check_instrument_revisions.c.revision_id == revision['id'])).first():
        raise BookflowError('E_INTERNAL', message=f'Cheque {header["number"]} already carries a number on its current revision.')
    return check_numbers.rows(s, ctx, header, revision, settled, None, at=at, event=event)


def _confirm_plan(s, ctx, inp, at, event, *, cancel=False):
    run = _run_row(s, inp.run)
    items = _items(s, run['id'])
    pending = [item for item in items if item.get('outcome') is None]
    if not pending:
        raise _invalid('run', f'print run {run["id"]} has already been answered')
    failed_keys = set()
    if not cancel:
        known = {item['check_number_key']: item for item in pending}
        if inp.failed:
            for number in inp.failed:
                key = check_numbers.canonical(number)[1]
                if key not in known:
                    raise _invalid('failed', f'{number} is not a check waiting in this run')
                failed_keys.add(key)
        if inp.failed_from:
            _, key, sequence = check_numbers.canonical(inp.failed_from)
            if key not in known:
                raise _invalid('failed_from', f'{inp.failed_from} is not a check waiting in this run')
            failed_keys |= {item['check_number_key'] for item in pending if item['check_sequence'] >= sequence}
    outcomes, spoiled, instruments = [], [], []
    for item in pending:
        if cancel:
            outcome = 'cancelled'
        else:
            outcome = 'failed' if item['check_number_key'] in failed_keys else 'printed'
        outcomes.append(dict(item_id=item['id'], outcome=outcome, **_created(s, ctx, at, event)))
        item['outcome'] = outcome
        if outcome == 'failed':
            spoiled.append(dict(id=new_id(), account_id=run['account_id'], check_number=item['check_number'],
                                check_number_key=item['check_number_key'], check_sequence=item['check_sequence'],
                                transaction_id=item['transaction_id'], run_id=run['id'], reason='misprinted',
                                **_created(s, ctx, at, event)))
        elif outcome == 'printed':
            instruments.append(_instrument(s, ctx, _header(s, item['transaction_id']), run['account_id'],
                                           item['check_number'], at, event))
    pointer = None
    if cancel:
        account = accounts.resolve_account(s.company, run['account_id'])
        start, width, state = check_numbers.pointer(account)
        last = max(item['check_sequence'] for item in pending)
        if state == 'numeric' and start == last + 1 and all(i.get('outcome') == 'cancelled' for i in items):
            pointer = (account, run['first_number'])
    output = run_output(s, run, items, cls=CheckPrintWriteOutput,
                        spoiled=sorted({row['check_number'] for row in spoiled}
                                       | set(run_output(s, run, items).spoiled_numbers),
                                       key=lambda n: check_numbers.canonical(n)[2] or 0))
    return output, dict(outcomes=outcomes, spoiled=spoiled, instruments=instruments, pointer=pointer, run=run)


def prepare_confirm(s, ctx, inp, *, cancel=False):
    output, _ = _confirm_plan(s, ctx, inp, clock.now_iso(), new_id(), cancel=cancel)
    return Plan(output, {'input': inp, 'cancel': cancel})


def apply_confirm(plan, ctx, s):
    at, event = clock.now_iso(), new_id()
    cancel = plan.data['cancel']
    output, writes = _confirm_plan(s, ctx, plan.data['input'], at, event, cancel=cancel)
    touched = [Touched('check_print_outcome', row['item_id'], 'create', None, 1, row, db='company')
               for row in writes['outcomes']]
    touched += [Touched('check_spoiled_number', row['id'], 'create', None, 1, row, db='company')
                for row in writes['spoiled']]
    for planned in writes['instruments']:
        touched.extend(check_numbers.touches(planned))
    if writes['pointer']:
        touched.append(_account_touch(s, *writes['pointer']))
    good = sum(row['outcome'] == 'printed' for row in writes['outcomes'])
    name = 'check print-cancel' if cancel else 'check print-confirm'
    summary = (f'cancelled print run {writes["run"]["id"]}' if cancel else
               f'{good} check{"s" if good != 1 else ""} printed, {len(writes["spoiled"])} spoiled and queued again')
    _audit(s, ctx, name, summary, touched, event)
    _conn(s).execute(c.check_print_outcomes.insert(), writes['outcomes'])
    if writes['spoiled']:
        _conn(s).execute(c.check_spoiled_numbers.insert(), writes['spoiled'])
    for planned in writes['instruments']:
        check_numbers.write(s, planned)
    if writes['pointer']:
        account, value = writes['pointer']
        _conn(s).execute(c.accounts.update().where(c.accounts.c.id == account['id']).values(next_check_number=value))
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- check reprint


def _reprint_plan(s, ctx, inp, at, event):
    header = _header(s, inp.check)
    if header is None or header['type'] not in DOCUMENT_TYPES:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'check', 'selector': inp.check})
    if inp.expected_version is not None and inp.expected_version != header['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id'], 'expected_version': inp.expected_version,
                                                           'current_version': header['version']})
    held = check_numbers.current(s, header['id'])
    if held is None:
        raise _invalid('check', f'{header["number"]} has no check number yet; print it from the queue with `check print`')
    if header['status'] != 'posted':
        raise _invalid('check', f'check {held["check_number"]} is {header["status"]}; a voided check is not reprinted')
    run_id = in_printer(s, header['id'])
    if run_id:
        raise _invalid('check', f'check {held["check_number"]} is in print run {run_id}; answer that run first')
    fact = facts(s, header)
    account = accounts.resolve_account(s.company, held['account_id'])
    saved = settings_output(s, account['id'])
    style = inp.style or saved.style
    if style == 'voucher' and inp.checks_on_first_page != 3:
        raise _invalid('checks_on_first_page', 'voucher checks print one to a page')
    renumber = inp.new_number is not None and check_numbers.canonical(inp.new_number)[1] != held['check_number_key']
    spoiled, projection, pointer = [], None, None
    if renumber:
        journals.open_dates(s, [fact['date']])
        (number,), after_last = _numbers(s, account, inp.new_number, 1, excluding=header['id'])
        text, key, seq = number
        if held['check_sequence'] is not None:
            spoiled.append(dict(id=None, account_id=account['id'], check_number=held['check_number'],
                                check_number_key=held['check_number_key'], check_sequence=held['check_sequence'],
                                transaction_id=header['id'], run_id=None, reason='reprinted',
                                **_created(s, ctx, at, event)))
        projection = dict(held, check_number=text, check_number_key=key, check_sequence=seq, origin='issued',
                          updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, audit_event_id=event)
        pointer = _move_pointer(s, account, after_last)
    else:
        text, key, seq = held['check_number'], held['check_number_key'], held['check_sequence']
        if seq is None:
            raise _invalid('check', f'check {text} has no place in a sequence, so it is reprinted with a new number')
    run = dict(id=new_id(), account_id=account['id'], kind='reprint', style=style, first_number=text,
               checks_on_first_page=inp.checks_on_first_page, offset_x_hundredths=saved.offset_x_hundredths,
               offset_y_hundredths=saved.offset_y_hundredths, **_created(s, ctx, at, event))
    item = dict(id=new_id(), run_id=run['id'], transaction_id=header['id'], position=1, check_number=text,
                check_number_key=key, check_sequence=seq, **_created(s, ctx, at, event))
    outcome = dict(item_id=item['id'], outcome='printed', **_created(s, ctx, at, event))
    for row in spoiled:
        row.update(id=new_id(), run_id=run['id'])
    output = run_output(s, run, [dict(item, outcome='printed')], {header['id']: fact}, cls=CheckPrintWriteOutput,
                        spoiled=[row['check_number'] for row in spoiled])
    return output, dict(run=run, item=item, outcome=outcome, spoiled=spoiled, projection=projection, before=held,
                        pointer=(account, pointer) if pointer else None)


def prepare_reprint(s, ctx, inp):
    output, _ = _reprint_plan(s, ctx, inp, clock.now_iso(), new_id())
    return Plan(output, {'input': inp})


def apply_reprint(plan, ctx, s):
    at, event = clock.now_iso(), new_id()
    output, w = _reprint_plan(s, ctx, plan.data['input'], at, event)
    touched = [Touched('check_print_run', w['run']['id'], 'create', None, 1, w['run'], db='company'),
               Touched('check_print_run_item', w['item']['id'], 'create', None, 1, w['item'], db='company'),
               Touched('check_print_outcome', w['item']['id'], 'create', None, 1, w['outcome'], db='company')]
    touched += [Touched('check_spoiled_number', row['id'], 'create', None, 1, row, db='company') for row in w['spoiled']]
    if w['projection']:
        touched.append(Touched('check_instrument', w['projection']['transaction_id'], 'update', None, 1,
                               w['projection'], w['before'], db='company'))
    if w['pointer']:
        touched.append(_account_touch(s, *w['pointer']))
    old = w['before']['check_number']
    summary = (f'reprinted check {old} as {w["item"]["check_number"]}; {old} spoiled' if w['projection']
               else f'reprinted check {old}')
    _audit(s, ctx, 'check reprint', summary, touched, event)
    _conn(s).execute(c.check_print_runs.insert().values(**w['run']))
    _conn(s).execute(c.check_print_run_items.insert().values(**w['item']))
    _conn(s).execute(c.check_print_outcomes.insert().values(**w['outcome']))
    if w['spoiled']:
        _conn(s).execute(c.check_spoiled_numbers.insert(), w['spoiled'])
    if w['projection']:
        p = w['projection']
        _conn(s).execute(c.check_instruments.update().where(c.check_instruments.c.transaction_id == p['transaction_id'])
                         .values(**{k: p[k] for k in ('check_number', 'check_number_key', 'check_sequence', 'origin',
                                                      'updated_at', 'updated_by', 'updated_via', 'audit_event_id')}))
        if p['type'] == 'bill_payment':
            pass  # the payment's profile keeps what it was written with; its outputs read the projection
    if w['pointer']:
        account, value = w['pointer']
        _conn(s).execute(c.accounts.update().where(c.accounts.c.id == account['id']).values(next_check_number=value))
    return Applied(output, touched, summary, audited=True)


# ---------------------------------------------------------------- check print-setup


def _setup_plan(s, ctx, inp, at):
    from bookflow.hub.users import common
    account = _bank(s, inp.account)
    before = _settings(s, account['id'])
    if before is not None and inp.expected_version is not None and inp.expected_version != before['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_id': before['id'], 'expected_version': inp.expected_version,
                                                           'current_version': before['version']})
    base = before or dict(id=new_id(), account_id=account['id'], style=DEFAULT_STYLE, offset_x_hundredths=0,
                          offset_y_hundredths=0, **common(s.actor.id, ctx.interface.value, at))
    after = dict(base)
    for field in ('style', 'offset_x_hundredths', 'offset_y_hundredths'):
        value = getattr(inp, field)
        if value is not None:
            after[field] = value
    changed = before is None or any(after[k] != before[k] for k in ('style', 'offset_x_hundredths', 'offset_y_hundredths'))
    if before is not None and changed:
        after.update(version=before['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    output = CheckPrintSetupOutput(account_id=account['id'], style=after['style'],
                                   offset_x_hundredths=after['offset_x_hundredths'],
                                   offset_y_hundredths=after['offset_y_hundredths'], version=after['version'],
                                   changed=changed)
    return output, before, after, changed


def prepare_setup(s, ctx, inp):
    output, *_ = _setup_plan(s, ctx, inp, clock.now_iso())
    return Plan(output, {'input': inp})


def apply_setup(plan, ctx, s):
    output, before, after, changed = _setup_plan(s, ctx, plan.data['input'], clock.now_iso())
    if not changed:
        return Applied(output, [], 'no change')
    t = c.check_print_settings
    if before is None:
        _conn(s).execute(t.insert().values(**after))
        touched = [Touched('check_print_settings', after['id'], 'create', None, 1, after, db='company')]
    else:
        done = _conn(s).execute(t.update().where(t.c.id == after['id'], t.c.version == before['version']).values(**after)).rowcount
        if done != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': after['id']})
        touched = [Touched('check_print_settings', after['id'], 'update', before['version'], after['version'],
                           after, before, db='company')]
    return Applied(output, touched, f'check printer setup: {after["style"]}, '
                                    f'{after["offset_x_hundredths"]}/{after["offset_y_hundredths"]} hundredths')
