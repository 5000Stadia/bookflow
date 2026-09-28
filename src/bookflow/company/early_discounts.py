"""Early-payment discounts taken when a payment settles an invoice or a bill.

Terms such as "2% 10 Net 30" promise a discount when the document is paid by its discount
date. The discount is taken where the payment is recorded -- ``payment receive`` for an
invoice, ``bill pay`` for a bill -- and only when the caller names it: the suggested amount is
shown, never applied on its own.

**What a discount is in the books.** A customer discount is a debit to a discount account
("Discounts Given" by default) and a credit to Accounts Receivable, posted on the receipt beside
the cash. A vendor discount is a debit to Accounts Payable and a credit to a discount account
("Discounts Taken" by default), posted on the bill payment beside the money that left. Either
way the document is settled by the cash plus the discount, through the same settlement edge the
cash uses, so its open balance, the aging and the customer or vendor balance all read it without
knowing it was a discount.

**The suggested amount** is the terms percentage of the document's total -- the anchor's rule,
tax included -- less any discount already taken on that document, and never more than what is
still open. It is zero when the payment is dated after the document's discount date; a
discount can still be entered then, and the output warns that it is late.

**Where the account comes from.** An explicit account on the command, else the company
preference (``customer_discount_account_id`` / ``vendor_discount_account_id``), else an active
account already named "Discounts Given" / "Discounts Taken", else that account is created in
the chart, as an income account, in the same write.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date as calendar_date

import sqlalchemy as sa

from bookflow.company import accounts, schema as c
from bookflow.company.lists import normalize_display_name
from bookflow.core.errors import BookflowError

# A percentage is stored in millionths of one percentage point, so 100% is 10**8.
WHOLE = 100_000_000
DISCOUNT_ACCOUNT_TYPES = frozenset({'income', 'other_income', 'expense', 'other_expense', 'cost_of_goods_sold'})
SIDES = {
    'customer': dict(name='Discounts Given', type='income', preference='customer_discount_account_id'),
    'vendor': dict(name='Discounts Taken', type='income', preference='vendor_discount_account_id'),
}


@dataclass(frozen=True)
class Terms:
    percent_millionths: int | None
    discount_date: str | None


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def percent_of(gross, percent_millionths):
    """``percent`` of ``gross``, to the cent, half a cent rounding up."""
    if not percent_millionths or gross <= 0:
        return 0
    return (gross * percent_millionths * 2 + WHOLE) // (2 * WHOLE)


def invoice_terms(profile_snapshot):
    """The discount the invoice's captured terms offer, and the date it lapses."""
    captured = json.loads(profile_snapshot) if isinstance(profile_snapshot, str) else profile_snapshot
    term = captured.get('terms') or {}
    return Terms(term.get('discount_percent_millionths'), captured.get('discount_date'))


def bill_terms(profile_snapshot, bill_date):
    """The same answer for a bill: its captured terms, dated from the bill's own date."""
    from bookflow.company.profiles import TermInput, compute_term_dates, format_percentage_millionths
    captured = json.loads(profile_snapshot) if isinstance(profile_snapshot, str) else profile_snapshot
    term = captured.get('terms')
    if not term or term.get('discount_percent_millionths') is None:
        return Terms(None, None)
    payload = {key: term.get(key) for key in TermInput.model_fields if key not in ('name', 'discount_percent')}
    payload.update(name=term.get('label') or 'terms',
                   discount_percent=format_percentage_millionths(term['discount_percent_millionths']))
    try:
        dates = compute_term_dates(TermInput(**payload), calendar_date.fromisoformat(bill_date))
    except (OverflowError, ValueError):
        return Terms(term['discount_percent_millionths'], None)
    return Terms(term['discount_percent_millionths'],
                 dates.discount_date.isoformat() if dates.discount_date else None)


def terms_amount(terms, gross):
    """What the terms offer on the whole document, before anything is taken or dated."""
    return percent_of(gross, terms.percent_millionths)


def suggested(terms, gross, pay_date, *, taken=0, due=None):
    """The discount a payment dated ``pay_date`` earns: zero once the discount date has passed."""
    if terms.percent_millionths is None or terms.discount_date is None or pay_date > terms.discount_date:
        return 0
    amount = max(0, terms_amount(terms, gross) - taken)
    return amount if due is None else max(0, min(amount, due))


def late_warning(document, number, terms, pay_date):
    """Advice, not a refusal: the anchor accepts a discount the customer took late."""
    if terms.discount_date is None:
        return f'discount: {document} {number} has no early-payment discount in its terms; the discount entered is taken anyway'
    if pay_date > terms.discount_date:
        return (f'discount: {document} {number} was discountable only through {terms.discount_date}; '
                f'the discount entered on this {pay_date} payment is taken anyway')
    return None


# ------------------------------------------------------------------ the discount account


def _account_facts(row):
    return {key: row[key] for key in ('id', 'name', 'full_name', 'number', 'type')} | {
        'normal_balance': accounts.NORMAL_BALANCE[row['type']]}


def _usable(row, field, home_currency):
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': 'account', 'record_id': row['id'], 'field': field})
    if row['type'] not in DISCOUNT_ACCOUNT_TYPES:
        raise _invalid(field, f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; a discount '
                              'posts to an income, expense or cost of goods sold account')
    if row['currency'] != home_currency:
        raise _invalid(field, 'account must use the home currency')
    return row


def validate_preference(db, field, account_id):
    """``company update`` asks the same question a payment would, before it saves the default."""
    info = dict(db.conn.execute(sa.select(c.company_info)).mappings().one())
    row = accounts.resolve_account(db, account_id)
    return _usable(row, field, info['home_currency'])['id']


def resolve_account(s, ctx, side, selector, *, at, field='discount_account'):
    """The account a discount posts to, and the account row to create first when there is none.

    Returns ``(facts, created)``. ``facts`` is the captured account shape a posting line carries;
    ``created`` is the new chart row when nothing named the account, no preference is set and the
    default account does not exist yet -- the write inserts it before its postings.
    """
    spec = SIDES[side]
    db = s.company
    info = dict(db.conn.execute(sa.select(c.company_info)).mappings().one())
    home = info['home_currency']
    if selector is not None:
        return _account_facts(_usable(accounts.resolve_account(db, selector), field, home)), None
    preferred = info.get(spec['preference'])
    if preferred:
        row = dict(db.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == preferred)).mappings().one())
        return _account_facts(_usable(row, spec['preference'], home)), None
    _, key = normalize_display_name(spec['name'])
    found = [dict(row) for row in db.conn.execute(sa.select(c.accounts).where(
        c.accounts.c.full_name_key == key, c.accounts.c.active.is_(True))).mappings()]
    if found:
        return _account_facts(_usable(found[0], field, home)), None
    mutation = accounts.plan_account_create(db, {'name': spec['name'], 'type': spec['type']},
                                            actor_id=s.actor.id, via=ctx.interface.value, at=at)
    return _account_facts(mutation.after), mutation


def account_identity(facts, created):
    """What a preview binds: the account, or -- when it is still to be created -- its name and type."""
    if created is None:
        return facts
    return {key: value for key, value in facts.items() if key != 'id'} | {'created': True}


def created_account_touches(mutation):
    from bookflow.core.registry import Touched
    if mutation is None:
        return []
    return [Touched('account', mutation.after['id'], 'create', None, mutation.after['version'],
                    dict(mutation.after), None, db='company')]


def persist_created_account(s, mutation):
    if mutation is not None:
        accounts.persist_account_mutation(s.company, mutation)
