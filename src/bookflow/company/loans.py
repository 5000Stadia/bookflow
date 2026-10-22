"""Loans: amortization schedules and payments that split principal, interest and escrow (R119).

The anchor's Loan Manager: a loan names the liability account carrying its balance, the lender,
the amount borrowed, the rate, the term and how often payments fall due, the first due date, an
optional balloon and escrow. It computes the amortization schedule and sets up each payment as a
check whose lines split it -- principal to the liability, interest to the interest account,
escrow to the escrow account. The money borrowed reaches the liability account the way the anchor
asks: a deposit or journal entry crediting it; a loan never posts by being created.

**Schedule.** Computed, never stored: the payments recorded (each a ``loan_payments`` row naming
its check, standing while the check is posted) come first, in date order, with the split each was
written at; from the balance they leave, the remaining scheduled payments follow at the regular
payment, each with interest on the balance then owed, the last absorbing the rounding (see
``company/amortization.py``). Extra principal therefore shortens the loan: the same payment,
fewer of them, as the anchor's what-if puts it. Voiding a payment's check puts it back in the
schedule.

**The balance sheet tie.** The schedule's balance is the amount borrowed less the principal of
the payments standing; the liability account moves by exactly those principal lines, so the two
agree whenever the borrowing was recorded into the account. ``loan show`` reports both and whether
they tie.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import amortization as math, schema as c
from bookflow.company.fixed_asset_models import (
    LoanOutput, LoanPageOutput, LoanPayOutput, LoanScheduleRow, LoanShowOutput, LoanWriteOutput,
)
from bookflow.company.fixed_assets import _account, _invalid, _labels, _money, _units, currency_of, standing
from bookflow.company.lists import normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_percentage_millionths, parse_percentage_millionths
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

LIABILITY_TYPES = {'other_current_liability', 'long_term_liability'}
INTEREST_TYPES = {'expense', 'other_expense'}
PAYMENT_TYPES = {'bank': 'check', 'credit_card': 'card-charge'}


def _rate(text: str) -> int:
    try:
        value = parse_percentage_millionths(text, field='annual_rate')
    except BookflowError:
        raise _invalid('annual_rate', 'must be a percent such as 6.5, with at most six decimal places') from None
    if not 0 <= value <= 100_000_000:
        raise _invalid('annual_rate', 'must be from 0 through 100')
    return value


def resolve_loan(s, selector: str) -> dict[str, Any]:
    t = c.loans
    if is_ulid(selector):
        row = s.company.conn.execute(sa.select(t).where(t.c.id == selector.upper())).mappings().first()
    else:
        _, key = normalize_display_name(selector)
        row = s.company.conn.execute(sa.select(t).where(t.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'loan', 'selector': selector})
    return dict(row)


def _unique_name(s, name: str, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field='name')
    t = c.loans
    found = s.company.conn.execute(sa.select(t.c.id).where(t.c.name_key == key)).scalar()
    if found is not None and found != own:
        raise BookflowError('E_NAME_TAKEN', details={'record_type': 'loan', 'name': display, 'record_id': found})
    return display, key


def _vendor(s, selector) -> str:
    from bookflow.company.parties import resolve_party
    row = resolve_party(s.company, 'vendor', selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'lender'})
    return row['id']


def _payment_account(s, selector, field: str) -> dict[str, Any]:
    return _account(s, selector, field, set(PAYMENT_TYPES))


def payments(s, loan_id: str) -> list[dict[str, Any]]:
    p = c.loan_payments
    rows = [dict(r) for r in s.company.conn.execute(sa.select(p).where(p.c.loan_id == loan_id).order_by(
        p.c.payment_date, p.c.created_at, p.c.id)).mappings()]
    live = standing(s, {r['transaction_id'] for r in rows})
    return [dict(r, document_number=live[r['transaction_id']]) for r in rows if r['transaction_id'] in live]


def build(loan: dict[str, Any], paid: list[dict[str, Any]]) -> dict[str, Any]:
    """The schedule: paid rows, then remaining rows from the balance they leave."""
    balance = int(loan['principal_minor_units'])
    rows = []
    for p in paid:
        balance -= int(p['principal_minor_units'])
        rows.append(dict(number=p['payment_number'], status='paid',
                         due_date=None if p['payment_number'] is None else math.due_date(loan['first_payment_date'], loan['frequency'], p['payment_number']),
                         payment_date=p['payment_date'], principal=int(p['principal_minor_units']),
                         interest=int(p['interest_minor_units']), escrow=int(p['escrow_minor_units']), balance=balance,
                         transaction_id=p['transaction_id'], document_number=p['document_number']))
    numbers = [p['payment_number'] for p in paid if p['payment_number'] is not None]
    rate = parse_percentage_millionths(loan['annual_rate'])
    try:
        remaining = math.remaining_rows(max(balance, 0), payment=int(loan['payment_minor_units']), rate_millionths=rate,
                                        frequency=loan['frequency'], first_payment_date=loan['first_payment_date'],
                                        term=int(loan['term_payments']), start_number=max(numbers, default=0) + 1)
    except ValueError:
        raise _invalid('payment', 'does not cover the interest on the balance owed') from None
    escrow = int(loan['escrow_minor_units'])
    for row in remaining:
        rows.append(dict(number=row['number'], status='remaining', due_date=row['date'], payment_date=None,
                         principal=row['principal'], interest=row['interest'], escrow=escrow, balance=row['balance'],
                         transaction_id=None, document_number=None))
    return dict(rows=rows, balance=balance, remaining=[r for r in rows if r['status'] == 'remaining'])


def _row_out(row, currency) -> LoanScheduleRow:
    m = lambda units: _money(units, currency)  # noqa: E731
    return LoanScheduleRow(number=row['number'], status=row['status'], due_date=row['due_date'], payment_date=row['payment_date'],
                           principal=m(row['principal']), interest=m(row['interest']), escrow=m(row['escrow']),
                           total=m(row['principal'] + row['interest'] + row['escrow']), balance=m(row['balance']),
                           transaction_id=row['transaction_id'], document_number=row['document_number'])


def liability_balance(s, account_id: str) -> int:
    from bookflow.company.ledger_reports import net_balances
    return -net_balances(s.company).get(account_id, 0)


def show_output(s, loan, paid=None) -> LoanShowOutput:
    currency = loan['currency']
    m = lambda units: _money(units, currency)  # noqa: E731
    paid = payments(s, loan['id']) if paid is None else paid
    sched = build(loan, paid)
    labels = _labels(s, [loan['liability_account_id'], loan['interest_account_id'], loan['escrow_account_id'], loan['payment_account_id']])
    lender = None
    if loan['lender_id']:
        lender = s.company.conn.execute(sa.select(c.vendors.c.name).where(c.vendors.c.id == loan['lender_id'])).scalar()
    books = liability_balance(s, loan['liability_account_id'])
    remaining = sched['remaining']
    out = LoanOutput(
        id=loan['id'], version=loan['version'], name=loan['name'], lender_id=loan['lender_id'], lender=lender,
        liability_account_id=loan['liability_account_id'], liability_account=labels[loan['liability_account_id']],
        interest_account=labels[loan['interest_account_id']], escrow_account=labels.get(loan['escrow_account_id']),
        payment_account=labels.get(loan['payment_account_id']), principal=m(loan['principal_minor_units']),
        annual_rate=loan['annual_rate'], term_payments=loan['term_payments'], frequency=loan['frequency'],
        origination_date=loan['origination_date'], first_payment_date=loan['first_payment_date'],
        payment=m(loan['payment_minor_units']), escrow=m(loan['escrow_minor_units']), balloon=m(loan['balloon_minor_units']),
        balance=m(sched['balance']), liability_balance=m(books), ties=books == sched['balance'],
        paid_count=sum(1 for r in sched['rows'] if r['status'] == 'paid'), remaining_count=len(remaining),
        maturity_date=remaining[-1]['due_date'] if remaining else None,
        interest_paid=m(sum(r['interest'] for r in sched['rows'] if r['status'] == 'paid')),
        interest_remaining=m(sum(r['interest'] for r in remaining)),
        next_payment=_row_out(remaining[0], currency) if remaining else None)
    return LoanShowOutput(loan=out, rows=[_row_out(r, currency) for r in sched['rows']])


def show(s, inp) -> LoanShowOutput:
    return show_output(s, resolve_loan(s, inp.loan))


def page(s, inp) -> LoanPageOutput:
    t = c.loans
    rows = [dict(r) for r in s.company.conn.execute(sa.select(t).order_by(t.c.name_key).limit(inp.limit)).mappings()]
    return LoanPageOutput(items=[show_output(s, r).loan for r in rows])


# ------------------------------------------------------------------------------------ create/update

def prepare_create(s, ctx, inp) -> Plan:
    currency = currency_of(s)
    display, key = _unique_name(s, inp.name)
    liability = _account(s, inp.liability_account, 'liability_account', LIABILITY_TYPES)
    interest_account = _account(s, inp.interest_account, 'interest_account', INTEREST_TYPES)
    principal = _units(inp.principal, currency, 'principal')
    if principal <= 0:
        raise _invalid('principal', 'must be above 0.00')
    rate = _rate(inp.annual_rate)
    balloon = _units(inp.balloon, currency, 'balloon')
    if balloon >= principal:
        raise _invalid('balloon', 'must be less than the principal')
    escrow = _units(inp.escrow, currency, 'escrow')
    escrow_account = _account(s, inp.escrow_account, 'escrow_account')['id'] if inp.escrow_account else None
    if escrow and escrow_account is None:
        raise _invalid('escrow_account', 'is required with escrow')
    if inp.origination_date and inp.origination_date > inp.first_payment_date:
        raise _invalid('origination_date', 'cannot follow the first payment date')
    payment = (_units(inp.payment, currency, 'payment') if inp.payment is not None
               else math.level_payment(principal, rate, inp.frequency, inp.term_payments, balloon))
    if payment <= 0:
        raise _invalid('payment', 'must be above 0.00')
    at = clock.now_iso()
    loan = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), name=display, name_key=key,
                lender_id=_vendor(s, inp.lender) if inp.lender else None, liability_account_id=liability['id'],
                interest_account_id=interest_account['id'], escrow_account_id=escrow_account,
                payment_account_id=_payment_account(s, inp.payment_account, 'payment_account')['id'] if inp.payment_account else None,
                principal_minor_units=principal, annual_rate=format_percentage_millionths(rate), term_payments=inp.term_payments,
                frequency=inp.frequency, origination_date=inp.origination_date, first_payment_date=inp.first_payment_date,
                payment_minor_units=payment, balloon_minor_units=balloon, escrow_minor_units=escrow, currency=currency)
    return Plan(LoanWriteOutput(loan=show_output(s, loan, [])), dict(loan=loan))


def apply_create(plan: Plan, ctx, s) -> Applied:
    loan = plan.data['loan']
    s.company.conn.execute(c.loans.insert().values(**loan))
    return Applied(plan.preview, [Touched('loan', loan['id'], 'create', None, 1, loan, db='company')], f"created loan {loan['name']}")


def prepare_update(s, ctx, inp) -> Plan:
    before = resolve_loan(s, inp.loan)
    if inp.expected_version is not None and inp.expected_version != before['version']:
        raise BookflowError('E_VERSION_CONFLICT', details={'record_type': 'loan', 'expected_version': inp.expected_version,
                                                           'current_version': before['version']})
    currency = before['currency']
    loan = dict(before)
    if inp.name is not None:
        loan['name'], loan['name_key'] = _unique_name(s, inp.name, own=before['id'])
    if inp.interest_account is not None:
        loan['interest_account_id'] = _account(s, inp.interest_account, 'interest_account', INTEREST_TYPES)['id']
    if inp.lender is not None:
        loan['lender_id'] = _vendor(s, inp.lender)
    if inp.payment_account is not None:
        loan['payment_account_id'] = _payment_account(s, inp.payment_account, 'payment_account')['id']
    if inp.escrow_account is not None:
        loan['escrow_account_id'] = _account(s, inp.escrow_account, 'escrow_account')['id']
    if inp.escrow is not None:
        loan['escrow_minor_units'] = _units(inp.escrow, currency, 'escrow')
    if loan['escrow_minor_units'] and not loan['escrow_account_id']:
        raise _invalid('escrow_account', 'is required with escrow')
    if inp.annual_rate is not None:
        loan['annual_rate'] = format_percentage_millionths(_rate(inp.annual_rate))
    if inp.payment is not None:
        loan['payment_minor_units'] = _units(inp.payment, currency, 'payment')
        if loan['payment_minor_units'] <= 0:
            raise _invalid('payment', 'must be above 0.00')
    elif inp.annual_rate is not None and loan['annual_rate'] != before['annual_rate']:
        # A new rate with no new payment: the level payment over the scheduled payments left.
        sched = build(before, payments(s, before['id']))
        numbers = [r['number'] for r in sched['rows'] if r['status'] == 'paid' and r['number'] is not None]
        left = loan['term_payments'] - max(numbers, default=0)
        if sched['balance'] > 0 and left > 0:
            balloon = min(int(loan['balloon_minor_units']), sched['balance'])
            loan['payment_minor_units'] = math.level_payment(sched['balance'], parse_percentage_millionths(loan['annual_rate']),
                                                             loan['frequency'], left, balloon)
    changed = loan != before
    if changed:
        loan.update(version=before['version'] + 1, updated_at=clock.now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(LoanWriteOutput(loan=show_output(s, loan), changed=changed), dict(before=before, loan=loan, changed=changed))


def apply_update(plan: Plan, ctx, s) -> Applied:
    if not plan.data['changed']:
        return Applied(plan.preview, [], 'no change')
    loan, before = plan.data['loan'], plan.data['before']
    s.company.conn.execute(c.loans.update().where(c.loans.c.id == loan['id']).values(**loan))
    return Applied(plan.preview, [Touched('loan', loan['id'], 'update', before['version'], loan['version'], loan, before, db='company')],
                   f"updated loan {loan['name']}")


# ---------------------------------------------------------------------------------------------- pay

def _payment(s, inp) -> dict[str, Any]:
    loan = resolve_loan(s, inp.loan)
    currency = loan['currency']
    sched = build(loan, payments(s, loan['id']))
    if sched['balance'] <= 0:
        raise _invalid('loan', 'is paid off')
    extra = _units(inp.extra_principal, currency, 'extra_principal') if inp.extra_principal is not None else 0
    if inp.extra_only:
        if extra <= 0:
            raise _invalid('extra_principal', 'is required with extra_only, above 0.00')
        if extra > sched['balance']:
            raise _invalid('extra_principal', 'is more than the balance owed')
        number, principal, interest, escrow = None, extra, 0, 0
        date = inp.date
        if date is None:
            raise _invalid('date', 'is required for an extra payment')
    else:
        due = sched['remaining'][0]
        number, interest, escrow = due['number'], due['interest'], due['escrow']
        principal = due['principal'] + extra
        if principal > sched['balance']:
            raise _invalid('extra_principal', 'is more than the balance left after the scheduled principal')
        date = inp.date or due['due_date']
    selector = inp.from_account or loan['payment_account_id']
    if selector is None:
        raise _invalid('from_account', "is required: the loan has no payment account")
    account = _payment_account(s, selector, 'from_account')
    noun = PAYMENT_TYPES[account['type']]
    lines = [(loan['liability_account_id'], principal, 'Principal'), (loan['interest_account_id'], interest, 'Interest'),
             (loan['escrow_account_id'], escrow, 'Escrow')]
    lines = [line for line in lines if line[1] > 0]
    label = f"{loan['name']} payment {number}" if number else f"{loan['name']} extra principal"
    expenses = [dict(account=a, amount=_money(amount, currency).amount, memo=f"{text}, {label}") for a, amount, text in lines]
    face: dict[str, Any] = dict(account=account['id'], date=date, amount=_money(principal + interest + escrow, currency).amount,
                                memo=inp.memo or label, expenses=expenses)
    if loan['lender_id']:
        face['pay_to'] = dict(name_type='vendor', name_id=loan['lender_id'])
    if noun == 'check':
        from bookflow.company.check_models import CheckPostInput
        if inp.number is not None:
            face['number'] = inp.number
        document = CheckPostInput.model_validate(face)
    else:
        from bookflow.company.check_models import CardChargePostInput
        if inp.number is not None:
            raise _invalid('number', 'a card charge carries no check number')
        document = CardChargePostInput.model_validate(face)
    return dict(loan=loan, number=number, date=date, principal=principal, interest=interest, escrow=escrow,
                balance_after=sched['balance'] - principal, noun=noun, document=document, label=label)


def _pay_output(p, *, transaction_id=None, number=None) -> LoanPayOutput:
    currency = p['loan']['currency']
    m = lambda units: _money(units, currency)  # noqa: E731
    total = p['principal'] + p['interest'] + p['escrow']
    verb = 'Paid' if transaction_id else 'Would pay'
    return LoanPayOutput(
        loan_id=p['loan']['id'], loan=p['loan']['name'], payment_number=p['number'], date=p['date'], principal=m(p['principal']),
        interest=m(p['interest']), escrow=m(p['escrow']), total=m(total), balance_after=m(p['balance_after']),
        document_type='check' if p['noun'] == 'check' else 'card_charge', transaction_id=transaction_id, document_number=number,
        summary=f"{verb} {p['label']} on {p['date']}: {m(total).amount} (principal {m(p['principal']).amount}, "
                f"interest {m(p['interest']).amount}, escrow {m(p['escrow']).amount}); {m(p['balance_after']).amount} left")


def prepare_pay(s, ctx, inp) -> Plan:
    from bookflow.company import checks
    p = _payment(s, inp)
    checks.prepare(s, ctx, p['document'], p['noun'], 'post')
    return Plan(_pay_output(p), dict(input=inp))


def apply_pay(plan: Plan, ctx, s) -> Applied:
    from bookflow.company import checks
    from bookflow.core import audit
    inp = plan.data['input']
    p = _payment(s, inp)
    written = checks.apply(checks.prepare(s, ctx, p['document'], p['noun'], 'post'), ctx, s)
    doc = written.output
    event = new_id()
    row = dict(id=new_id(), loan_id=p['loan']['id'], payment_number=p['number'], payment_date=p['date'],
               principal_minor_units=p['principal'], interest_minor_units=p['interest'], escrow_minor_units=p['escrow'],
               currency=p['loan']['currency'], transaction_id=doc.id, created_at=clock.now_iso(), created_by=s.actor.id,
               created_via=ctx.interface.value, audit_event_id=event)
    touch = Touched('loan_payment', row['id'], 'create', None, 1, row, db='company')
    audit.write_event_to(s.company, ctx, 'loan pay', f"recorded {p['label']}", [touch], actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=event)
    s.company.conn.execute(c.loan_payments.insert().values(**row))
    number = doc.document.check_number or doc.number
    output = _pay_output(p, transaction_id=doc.id, number=number)
    return Applied(output, [*written.touched, touch], output.summary, audited=True)
