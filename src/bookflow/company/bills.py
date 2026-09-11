"""Bills: what a vendor is owed, entered as a document rather than as two ledger sides.

A bill is an accrual purchase. Entering one debits each expense account by its own line
amount and credits Accounts Payable by the total, and the bill then stands open at that total
until something settles it. Correcting one appends an exact reversal of what it used to say
and a full replacement of what it says now; voiding one reverses it at its own date and keeps
every earlier revision readable. That is the invoice's lifecycle, on the other side of the
books, and it is deliberately the same one.

**The payable is a record, not a subtraction.** ``ap_obligation_keys`` holds one stable row
per bill -- the vendor, the payable account and the currency an application has to match --
and it is created once, at posting, so a correction never moves it and a settlement that
attached to it stays attached. ``ap_obligation_components`` breaks that obligation down per
entered line and names the exact ``posting_line_sources`` row that credited AP for it, which
is what an allocation targets when a payment has to land on particular lines. Nothing here
applies anything: ``applied_totals`` is the single function the settlement owner answers, and
``bill pay`` in ``company/bill_payments.py`` is what makes it answer more than zero.

**The two tabs.** A bill has an Expenses grid and an Items grid, and it may be entered on
either or on both. They are two profile tables over one envelope family: every entered line is
a ``purchase`` envelope in ``document_lines``, owned by exactly one of
``purchase_expense_lines`` and ``purchase_item_lines``, and which one it is changes only what
was captured -- never the numbering, the terms, the payable, the obligation component or the
shape of the posting. An expense line names its own account; an item line takes the account
off the item it names. Both then debit that account for their own amount, and Accounts Payable
is credited their sum, once.

**Why an inventory part is refused.** Receiving stock debits Inventory Asset and moves quantity
on hand, and nothing in this product owns either. So a bill admits exactly the three item
families an invoice already sells -- service, non-inventory part and other charge -- each of
which posts to one account named on the item itself, and refuses an inventory item by name
rather than quietly debiting an expense account it was never meant to touch.
"""
from __future__ import annotations

import json
import zlib
from datetime import date as calendar_date

import sqlalchemy as sa

from bookflow.company import accounts, journals, list_service, schema as c
from bookflow.company import document_effects as effects
from bookflow.company import journal_custom_fields as custom
from bookflow.company.bill_facts import (
    PURCHASABLE_ITEM_TYPES, Account, BillExpenseProfile, BillItemProfile, BillProfile, Origin,
    Reference, Term, Vendor,
)
from bookflow.company.bill_models import (
    BillExpenseInput, BillHistoryOutput, BillObligationComponentOutput, BillObligationOutput,
    BillOutput, BillPageOutput, BillRevisionOutput, BillRevisionSummaryOutput,
    BillSettlementOutput, BillSettlementSourceOutput, BillSummaryOutput, BillWriteOutput,
    DuplicateReferenceOutput,
    reference_key,
)
from bookflow.company.journal_models import checked_sum, parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.company.profiles import TermInput, compute_term_dates
from bookflow.company.sales_calculations import extension
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    format_percentage_millionths, format_quantity_micro_units, parse_quantity_micro_units,
)
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Plan
from bookflow.hub.users import common

DOCUMENT_TYPE = 'bill'

# Insert order matters: attribution rows follow the posting lines they hang off, and an
# obligation component follows both the payable it belongs to and the attribution it names.
TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('purchase_profiles', 'purchase_profile', 'revision_id'),
    ('purchase_expense_lines', 'purchase_expense_line', 'document_line_id'),
    ('purchase_item_lines', 'purchase_item_line', 'document_line_id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('ap_obligation_keys', 'ap_obligation_key', 'id'),
    ('ap_obligation_components', 'ap_obligation_component', 'id'),
)

# What a bill's expense line may debit. The typed money commands own the accounts this list
# leaves out: a bank or card account funds a purchase rather than being an expense of it, and
# AP and AR are moved by the documents that owe and are owed, never by a free expense row.
EXPENSE_ACCOUNTS = frozenset({'expense', 'other_expense', 'cost_of_goods_sold',
                              'fixed_asset', 'other_asset', 'other_current_asset'})
# A system role means some other command owns what lands in that account: Undeposited Funds is
# the deposit owner's, Inventory Asset is the item owner's. Cost of Goods Sold is the exception
# -- it carries a role but it is an ordinary purchase account a bookkeeper picks by hand, and
# refusing it would refuse freight in and subcontracted cost.
LINE_ROLES = frozenset({'cost_of_goods_sold'})


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def resolve(s, selector):
    t = c.transactions
    key = selector.upper() if is_ulid(selector) else selector
    found = effects.rows(s, t, t.c.type == DOCUMENT_TYPE, t.c.id == key)
    if not found:
        found = effects.rows(s, t, t.c.type == DOCUMENT_TYPE, t.c.number == selector)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': DOCUMENT_TYPE, 'selector': selector})
    return found[0]


def _info(s):
    return dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())


def profile_row(s, revision):
    return effects.rows(s, c.purchase_profiles, c.purchase_profiles.c.revision_id == revision['id'])[0]


# The captured-fact shape each line family stores, keyed by the family name the resolved and
# merged line dictionaries carry. One mapping rather than three branches: every place that has
# to parse a stored ``line_snapshot`` asks this which shape it is holding.
LINE_FACTS = {'expense': BillExpenseProfile, 'item': BillItemProfile}


def merged_line(envelope, profile, family):
    """One entered line as a reader sees it: the envelope's identity, the profile's money.

    The envelope and the profile both carry an ``account_id`` and an ``amount_minor_units``
    column, and on a purchase envelope both of those are null -- the accounting lives in the
    profile. Spelling the overlap out here rather than merging the two rows blindly is what
    keeps a line's account from silently reading as null.

    An item line adds what the Items tab has and the Expenses tab does not: the item itself,
    the quantity and the unit cost the amount came from. ``memo`` and ``description`` are the
    same stored envelope text under each tab's own column name.
    """
    merged = dict(envelope, family=family, memo=envelope['description'],
                  account_id=profile['account_id'],
                  amount_minor_units=profile['amount_minor_units'],
                  customer_id=profile['customer_id'], billable=bool(profile['billable']),
                  line_snapshot=profile['line_snapshot'])
    if family == 'item':
        merged.update(item_id=profile['item_id'],
                      quantity_microunits=profile['quantity_microunits'],
                      quantity=format_quantity_micro_units(profile['quantity_microunits']),
                      unit_cost_minor_units=profile['unit_cost_minor_units'])
    return merged


def line_profiles(s, revision):
    """Every stored line profile of a revision, by envelope id, with the family that owns it."""
    found = {}
    for family, table in (('expense', c.purchase_expense_lines), ('item', c.purchase_item_lines)):
        for row in effects.rows(s, table, table.c.revision_id == revision['id']):
            found[row['document_line_id']] = (family, row)
    return found


def saved_lines(s, revision):
    """The entered lines of a revision in stored position order, each tagged with its family."""
    lines = effects.rows(s, c.document_lines, c.document_lines.c.revision_id == revision['id'],
                         order=c.document_lines.c.position)
    profiles = line_profiles(s, revision)
    return [merged_line(line, profiles[line['id']][1], profiles[line['id']][0]) for line in lines]


def by_family(lines, family):
    return [line for line in lines if line['family'] == family]


# ---------------------------------------------------------------- header resolution


def _reference(row):
    return Reference(id=row['id'], label=row.get('full_name') or row.get('name') or row.get('code'),
                     version=row['version'])


def _account_facts(row):
    return Account(**{k: row[k] for k in ('id', 'name', 'full_name', 'number', 'type')},
                   normal_balance=accounts.NORMAL_BALANCE[row['type']])


def _account_row(s, selector, field):
    try:
        row = accounts.resolve_account(s.company, selector)
    except BookflowError as exc:
        if exc.code in ('E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE'):
            exc.details.setdefault('record_type', 'account')
            exc.details['field'] = field
        raise
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'account', 'record_id': row['id'], 'field': field})
    return row


def _ap_account(s, selector, currency, previous, *, noun='bill'):
    """The payable this document is owed out of: named, carried forward, or the only one there is.

    Never an arbitrary choice. With no explicit account and no previous one, the company must
    have exactly one active Accounts Payable account; anything else is a question for the
    person entering the document, not a guess for the writer. ``noun`` names the document in
    the refusal, because a vendor credit resolves its payable by exactly this rule and a
    message naming a bill would be about the wrong document.
    """
    explicit = selector is not None
    if selector is None and previous is not None:
        selector = previous.ap_account.id
    if selector is None:
        eligible = s.company.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.type == 'accounts_payable', c.accounts.c.active.is_(True))).scalars().all()
        if len(eligible) != 1:
            raise _invalid('ap_account', f'name the Accounts Payable account this {noun} is owed from; '
                                         f'the company has {len(eligible)} active ones')
        selector = eligible[0]
    row = _account_row(s, selector, 'ap_account')
    if row['type'] != 'accounts_payable':
        raise _invalid('ap_account', f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; '
                                     f'a {noun} is owed out of an Accounts Payable account')
    if row['currency'] != currency:
        raise _invalid('ap_account', 'account must use the home currency')
    return _account_facts(row), Origin(kind='explicit' if explicit else 'default')


def _list_row(s, noun, table, selector, field, kind):
    row = list_service.resolve_selector(s.company, table, get_list_definition(noun), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': kind, 'record_id': row['id'], 'field': field})
    return row


def _term_facts(row):
    ref = _reference(row).model_dump()
    return Term(**ref, **{k: row[k] for k in Term.model_fields if k not in ref})


def _due_from_terms(term, date):
    """The terms owner computes the date; this only asks it and reports what it said."""
    payload = {k: getattr(term, k) for k in TermInput.model_fields if k not in ('name', 'discount_percent')}
    payload.update(name=term.label, discount_percent=(
        format_percentage_millionths(term.discount_percent_millionths)
        if term.discount_percent_millionths is not None else None))
    try:
        return compute_term_dates(TermInput(**payload), calendar_date.fromisoformat(date)).due_date.isoformat()
    except (OverflowError, ValueError):
        raise _invalid('terms', 'computed dates must fit supported ISO years') from None


def _supplied(inp, field):
    return field in inp.model_fields_set


def resolve_header(s, inp, date, previous, old_date):
    """Every header fact a bill revision captures, and where each one came from."""
    info = _info(s)
    currency = info['home_currency']
    origins = dict(previous.origins) if previous else {}

    selector = getattr(inp, 'vendor', None) or (previous.vendor.id if previous else None)
    if selector is None:
        raise _invalid('vendor', 'a bill is owed to a vendor')
    row = resolve_party(s.company, 'vendor', selector)
    changed_vendor = previous is None or row['id'] != previous.vendor.id
    if changed_vendor and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'vendor'})
    vendor = Vendor(**_reference(row).model_dump(),
                    **{k: row.get(k) for k in ('company_name', 'email', 'phone', 'account_number')})
    origins['vendor'] = Origin(kind='explicit')

    ap_account, origins['ap_account'] = _ap_account(s, inp.ap_account, currency, previous)

    # Terms: what was typed, else the vendor's own terms when the vendor is new to this bill,
    # else whatever the bill already carried.
    if _supplied(inp, 'terms'):
        term = _term_facts(_list_row(s, 'term', c.terms, inp.terms, 'terms', 'term')) if inp.terms else None
        origins['terms'] = Origin(kind='explicit')
    elif changed_vendor:
        term = _term_facts(_list_row(s, 'term', c.terms, row['terms_id'], 'terms', 'term')) if row['terms_id'] else None
        origins['terms'] = Origin(kind='default', source_id=row['id'] if term else None)
    else:
        term = previous.terms
    derived = _due_from_terms(term, date) if term else date

    kept_due = (previous is not None and not changed_vendor and not _supplied(inp, 'terms')
                and date == old_date and previous.due_date_basis == 'entered')
    if _supplied(inp, 'due_date') and inp.due_date is not None:
        due_date, basis = inp.due_date, 'entered'
        origins['due_date'] = Origin(kind='explicit')
    elif kept_due:
        # An entered due date survives an edit that changed neither the bill date nor the terms.
        due_date, basis = previous.due_date, 'entered'
    else:
        due_date, basis = derived, ('terms' if term else 'bill_date')
        origins['due_date'] = Origin(kind='default', source_id=term.id if term else None)
    if due_date < date:
        raise _invalid('due_date', 'must be on or after the bill date')

    if _supplied(inp, 'supplier_reference'):
        reference = (inp.supplier_reference or '').strip() or None
        origins['supplier_reference'] = Origin(kind='explicit')
    else:
        reference = previous.supplier_reference if previous else None

    if _supplied(inp, 'class_id'):
        klass = (_reference(_list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
                 if inp.class_id else None)
        origins['class_id'] = Origin(kind='explicit')
    else:
        klass = previous.class_id if previous else None

    return dict(vendor=vendor, ap_account=ap_account, terms=term, due_date=due_date,
                due_date_basis=basis, supplier_reference=reference,
                supplier_reference_key=reference_key(reference), class_id=klass,
                currency=currency, origins=origins), info


def _expense_line(s, line, header_class, currency, index):
    field = f'expenses.{index}'
    row = _account_row(s, line.account, field + '.account')
    if row['type'] not in EXPENSE_ACCOUNTS:
        raise _invalid(field + '.account',
                       f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; an expense line '
                       'takes an expense, cost of goods sold, or asset account')
    if row['system_role'] is not None and row['system_role'] not in LINE_ROLES:
        raise _invalid(field + '.account',
                       f'"{row["full_name"]}" is written by the command that owns it, not by a bill line')
    if row['currency'] != currency:
        raise _invalid(field + '.account', 'account must use the home currency')
    amount = parse_domestic_amount(line.amount, currency, field + '.amount')
    if line.class_mode == 'none':
        klass = None
    elif line.class_id is not None:
        klass = _reference(_list_row(s, 'class', c.classes, line.class_id, field + '.class_id', 'class'))
    else:
        klass = header_class
    customer = (_reference(_list_row(s, 'customer', c.customers, line.customer, field + '.customer', 'customer'))
                if line.customer else None)
    # A vendor credit's grid is this grid without the billable column: a credit is not a cost
    # to pass on, so its row carries no such field and the captured fact is plainly false.
    profile = BillExpenseProfile(
        account=_account_facts(row), class_id=klass, customer=customer,
        billable=getattr(line, 'billable', False),
        origins={'class_id': Origin(kind='explicit' if line.class_mode == 'value' else 'default')})
    return dict(line_id=None, family='expense', amount_minor_units=amount.minor_units,
                memo=line.memo, profile=profile)


def _line_class(s, line, header_class, field):
    """A class typed on the row is the row's; a row without one takes the bill's."""
    if line.class_mode == 'none':
        return None
    if line.class_id is not None:
        return _reference(_list_row(s, 'class', c.classes, line.class_id, field + '.class_id', 'class'))
    return header_class


def _purchasable_item(s, selector, field):
    """The item a bill line may buy, and the one account buying it debits.

    Three refusals, each naming what is actually wrong. An inventory part is refused by its own
    name because receiving it is real work nobody has built -- it debits Inventory Asset and
    moves quantity on hand -- and posting it to an expense account instead would be a wrong
    debit that balances, which is the worst kind. An item with no purchase side is refused
    because it has no purchase account to debit and inventing one would be a guess. A
    percentage charge is refused for the reason the invoice refuses it: there is no base here
    to take a percentage of.
    """
    row = _list_row(s, 'item', c.items, selector, field, 'item')
    if row['type'] not in PURCHASABLE_ITEM_TYPES:
        spelled = row['type'].replace('_', ' ')
        problem = (f'"{row["full_name"]}" is an inventory part; receiving stock is not implemented, '
                   'so a bill cannot post one. Enter what was bought as an expense line against '
                   'the account it should land in.'
                   if row['type'] in ('inventory_part', 'inventory_assembly') else
                   f'"{row["full_name"]}" is a {spelled} item; a bill line takes a service, '
                   'non-inventory part or other charge item')
        raise BookflowError('E_VALIDATION', details={
            'fields': [{'field': field, 'problem': problem}], 'record_type': 'item',
            'record_id': row['id'], 'item_type': row['type'],
            'reason': 'inventory_receipt_not_implemented'
            if row['type'] in ('inventory_part', 'inventory_assembly') else 'item_type_not_purchasable',
            'supported_item_types': list(PURCHASABLE_ITEM_TYPES)})
    if not row['purchase_enabled'] or row['expense_account_id'] is None:
        raise _invalid(field, f'"{row["full_name"]}" has no purchase side; give it a purchase '
                              'description and an expense account, or enter the cost as an expense line')
    if row['other_charge_percent_millionths'] is not None:
        raise _invalid(field, f'"{row["full_name"]}" is a percentage charge; a bill line has no base '
                              'to take a percentage of')
    account = _account_row(s, row['expense_account_id'], field)
    if account['type'] not in EXPENSE_ACCOUNTS or (
            account['system_role'] is not None and account['system_role'] not in LINE_ROLES):
        raise _invalid(field, f'"{row["full_name"]}" posts to "{account["full_name"]}", which a bill '
                              'line may not debit; repoint the item at an expense or cost account')
    return row, account


def _item_line(s, line, header_class, currency, index):
    """One Items-tab row resolved into the amount it debits and the facts it captures."""
    field = f'items.{index}'
    row, account = _purchasable_item(s, line.item, field + '.item')
    if account['currency'] != currency:
        raise _invalid(field + '.item', 'account must use the home currency')
    if row['cost_minor_units'] is not None and row['cost_currency'] != currency:
        raise _invalid(field + '.item', 'item amounts must use home currency')
    quantity = parse_quantity_micro_units(line.quantity, field=field + '.quantity')
    if line.amount is not None:
        basis = 'amount'
        unit_cost = None
        amount = parse_domestic_amount(line.amount, currency, field + '.amount').minor_units
    else:
        basis = 'unit_cost'
        if line.unit_cost is not None:
            unit_cost = parse_domestic_amount(line.unit_cost, currency, field + '.unit_cost').minor_units
        elif row['cost_minor_units'] is not None:
            unit_cost = int(row['cost_minor_units'])
        else:
            raise _invalid(field + '.unit_cost',
                           f'"{row["full_name"]}" has no standard cost; give a unit cost or an amount')
        if unit_cost < 0:
            raise _invalid(field + '.unit_cost', 'must not be negative')
        amount = extension(quantity, unit_cost)
    if amount <= 0:
        raise _invalid(field + '.amount', 'an item line must be worth more than nothing')
    customer = (_reference(_list_row(s, 'customer', c.customers, line.customer, field + '.customer', 'customer'))
                if line.customer else None)
    profile = BillItemProfile(
        item=_reference(row), item_type=row['type'], account=_account_facts(account),
        quantity_microunits=quantity, unit_cost_minor_units=unit_cost, amount_basis=basis,
        standard_cost_minor_units=(None if row['cost_minor_units'] is None
                                   else int(row['cost_minor_units'])),
        class_id=_line_class(s, line, header_class, field), customer=customer,
        billable=line.billable,
        origins={'class_id': Origin(kind='explicit' if line.class_mode == 'value' else 'default'),
                 'description': Origin(kind='explicit' if line.description is not None
                                       else 'default', source_id=row['id']),
                 'unit_cost': Origin(kind='explicit' if line.unit_cost is not None or basis == 'amount'
                                     else 'default', source_id=None if line.unit_cost is not None
                                     or basis == 'amount' else row['id'])})
    # The purchase description is the item's own words unless the row overrode them; it is
    # stored on the envelope, exactly where an expense row's memo is stored.
    memo = line.description if line.description is not None else row['purchase_description']
    return dict(line_id=None, family='item', amount_minor_units=amount, memo=memo, profile=profile)


# ---------------------------------------------------------------- supplier reference


def duplicate_references(s, vendor_id, key, own_id=None, *, limit=20):
    """Other bills from this vendor carrying the same supplier reference.

    Reported, never enforced. Voided bills count -- a reference that was already entered and
    then voided is exactly the case a person needs to be told about -- and a blank reference
    never collides with anything. Nothing in storage forbids the repeat, because the
    configurable warn-and-acknowledge mode this detection exists for has not been adopted, and
    a uniqueness constraint would make that mode unimplementable.
    """
    if not key:
        return []
    p, t, r = c.purchase_profiles, c.transactions, c.transaction_revisions
    query = (sa.select(t.c.id, t.c.number, t.c.status, r.c.date, r.c.total_minor_units,
                       r.c.currency, p.c.supplier_reference)
             .select_from(p.join(r, r.c.id == p.c.revision_id).join(t, t.c.id == r.c.transaction_id))
             .where(p.c.vendor_id == vendor_id, p.c.supplier_reference_key == key,
                    t.c.current_revision_id == r.c.id, t.c.type == DOCUMENT_TYPE)
             .order_by(r.c.date, t.c.id).limit(limit))
    if own_id is not None:
        query = query.where(t.c.id != own_id)
    return [DuplicateReferenceOutput(
        bill_id=row['id'], number=row['number'], date=row['date'], status=row['status'],
        supplier_reference=row['supplier_reference'], total_minor_units=row['total_minor_units'],
        total=Money(row['total_minor_units'], row['currency']).to_dict())
        for row in s.company.conn.execute(query).mappings()]


# ---------------------------------------------------------------- the settlement seam


def applied_totals(s, obligation_ids):
    """How much has been settled against each payable, by ``ap_obligation_keys.id``.

    This is the whole of the seam the AP settlement owner attaches to, and the only line in
    this module that knows a settlement exists: ``company/ap_settlement.py`` nets the active
    applications standing against these keys. Everything else here -- the open balance, the
    status, the refusal to correct or void a bill that has been paid -- is arithmetic on what
    this answers, which is why the settlement owner landing moved nothing else.

    **One number, whatever settled it.** A bill paid by a check and a bill credited by the
    vendor are both settled here, and this deliberately does not say which: ``open`` is
    ``gross - applied`` and ``status`` is read off that scalar by four callers. What composed
    it is a separate read, ``applied_by_source_kind``, surfaced as ``settlement.sources``.
    """
    from bookflow.company import ap_settlement
    return ap_settlement.applied_totals(s, obligation_ids)


def applied_sources(s, obligation_ids):
    """The additive composition beside ``applied_totals``: settled amount per source kind."""
    from bookflow.company import ap_settlement
    return ap_settlement.applied_by_source_kind(s, obligation_ids)


def obligation_row(s, transaction_id, pending=None):
    rows = [row for row in (pending or {}).get('ap_obligation_keys', [])
            if row['transaction_id'] == transaction_id]
    if rows:
        return rows[0]
    found = effects.rows(s, c.ap_obligation_keys, c.ap_obligation_keys.c.transaction_id == transaction_id)
    return found[0] if found else None


def settlement_output(header, revision, obligation, applied, sources=None):
    """What is still open on this bill, and -- separately -- what kinds of money closed it.

    ``applied`` stays one number and ``open`` and ``status`` stay derived from it alone: a bill
    settled in full by a vendor credit reads ``paid`` exactly as one settled by a check does,
    with no branch here asking which. ``sources`` is the additive composition read, a mapping
    of source kind to netted amount from ``ap_settlement.applied_by_source_kind``; callers that
    do not ask for it get an empty list and the same three fields they always read.
    """
    gross = revision['total_minor_units'] if header['status'] == 'posted' else 0
    open_amount = gross - applied
    currency = revision['currency']
    return BillSettlementOutput(
        bill_id=header['id'], obligation_id=obligation['id'] if obligation else '',
        version=header['version'], revision_id=revision['id'],
        gross_minor_units=gross, applied_minor_units=applied, open_minor_units=open_amount,
        gross=Money(gross, currency).to_dict(), applied=Money(applied, currency).to_dict(),
        open=Money(open_amount, currency).to_dict(), currency=currency,
        status=('voided' if header['status'] == 'voided' else
                'paid' if open_amount == 0 else 'partial' if applied else 'unpaid'),
        sources=[BillSettlementSourceOutput(
            source_type=kind, applied_minor_units=units,
            applied=Money(units, currency).to_dict())
            for kind, units in sorted((sources or {}).items())])


# ---------------------------------------------------------------- reads


def order_ids(s, transaction_ids):
    """The purchase order each of these bills was entered from, where there was one."""
    identifiers = list(transaction_ids)
    if not identifiers:
        return {}
    t = c.purchase_order_conversions
    return {row['destination_transaction_id']: row['source_document_id']
            for row in effects.rows(s, t, t.c.destination_transaction_id.in_(identifiers))}


def summary(header, revision, profile, settlement, purchase_order_id=None):
    currency = revision['currency']
    captured = BillProfile.model_validate_json(profile['profile_snapshot'])
    return dict(header, purchase_order_id=purchase_order_id,
                date=revision['date'], due_date=profile['due_date'],
                vendor_id=profile['vendor_id'], vendor_name=captured.vendor.label,
                ap_account_id=profile['ap_account_id'],
                supplier_reference=profile['supplier_reference'], memo=revision['memo'],
                currency=currency,
                expense_total_minor_units=profile['expense_total_minor_units'],
                item_total_minor_units=profile['item_total_minor_units'],
                total_minor_units=revision['total_minor_units'],
                expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                item_total=Money(profile['item_total_minor_units'], currency).to_dict(),
                total=Money(revision['total_minor_units'], currency).to_dict(),
                settlement_current=settlement)


def _obligation_output(s, header, revision, pending=None):
    obligation = obligation_row(s, header['id'], pending)
    if obligation is None:
        return None
    components = [row for row in (pending or {}).get('ap_obligation_components', [])
                  if row['revision_id'] == revision['id']]
    if not components:
        components = effects.rows(s, c.ap_obligation_components,
                                  c.ap_obligation_components.c.revision_id == revision['id'],
                                  order=c.ap_obligation_components.c.id)
    return BillObligationOutput(**obligation, components=[BillObligationComponentOutput(
        **row, amount=Money(row['amount_minor_units'], row['currency']).to_dict()) for row in components])


def revision_output(s, header, revision, pending=None, *, summary_only=False):
    pending = pending or {}
    profile = next((row for row in pending.get('purchase_profiles', [])
                    if row['revision_id'] == revision['id']), None) or profile_row(s, revision)
    envelopes = [line for line in pending.get('document_lines', []) if line['revision_id'] == revision['id']]
    if summary_only:
        lines = envelopes
        line_count = len(lines) if lines else s.company.conn.execute(
            sa.select(sa.func.count()).select_from(c.document_lines).where(
                c.document_lines.c.revision_id == revision['id'])).scalar_one()
    elif envelopes:
        details = {row['document_line_id']: (family, row) for family, table in
                   (('expense', 'purchase_expense_lines'), ('item', 'purchase_item_lines'))
                   for row in pending[table]}
        lines = [merged_line(line, details[line['id']][1], details[line['id']][0])
                 for line in envelopes]
    else:
        lines = saved_lines(s, revision)
    saved_batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                 order=c.posting_batches.c.id)
    summaries = [journals.batch_output(s, batch) for batch in saved_batches]
    summaries += [journals.batch_output(s, batch, [line for line in pending['posting_lines']
                                                   if line['batch_id'] == batch['id']])
                  for batch in pending.get('posting_batches', []) if batch['revision_id'] == revision['id']]
    currency = revision['currency']
    values = {k: v for k, v in revision.items() if not k.endswith('_snapshot')}
    values.update(due_date=profile['due_date'],
                  expense_total_minor_units=profile['expense_total_minor_units'],
                  item_total_minor_units=profile['item_total_minor_units'],
                  expense_total=Money(profile['expense_total_minor_units'], currency).to_dict(),
                  item_total=Money(profile['item_total_minor_units'], currency).to_dict(),
                  total=Money(revision['total_minor_units'], currency).to_dict(),
                  line_count=line_count if summary_only else len(lines), batches=summaries)
    if summary_only:
        return BillRevisionSummaryOutput(**values)
    snapshot = json.loads(revision['custom_fields_snapshot'])
    return BillRevisionOutput(
        **values, profile=json.loads(profile['profile_snapshot']),
        issuer_snapshot=json.loads(revision['issuer_snapshot']),
        custom_fields_snapshot=snapshot, custom_fields=custom.project(snapshot),
        obligation=_obligation_output(s, header, revision, pending),
        expenses=[dict(line, amount=Money(line['amount_minor_units'], currency).to_dict(),
                       line_snapshot=json.loads(line['line_snapshot']))
                  for line in by_family(lines, 'expense')],
        items=[dict(line, amount=Money(line['amount_minor_units'], currency).to_dict(),
                    unit_cost=(None if line['unit_cost_minor_units'] is None else
                               Money(line['unit_cost_minor_units'], currency).to_dict()),
                    line_snapshot=json.loads(line['line_snapshot']))
               for line in by_family(lines, 'item')])


def current_settlement(s, header, obligation=None, revision=None):
    obligation = obligation if obligation is not None else obligation_row(s, header['id'])
    revision = revision if revision is not None else journals.revision(s, header)
    keys = [obligation['id']] if obligation else []
    applied = applied_totals(s, keys)
    return settlement_output(header, revision, obligation,
                             applied.get(obligation['id'], 0) if obligation else 0,
                             applied_sources(s, keys).get(obligation['id']) if obligation else None)


def show(s, inp):
    header = resolve(s, inp.bill)
    requested = journals.revision(s, header, inp.revision_number)
    profile = profile_row(s, requested)
    return BillOutput(
        **summary(header, requested, profile, current_settlement(s, header),
                  order_ids(s, [header['id']]).get(header['id'])),
        revision=revision_output(s, header, requested),
        duplicate_references=duplicate_references(
            s, profile['vendor_id'], profile['supplier_reference_key'], header['id']))


def page(s, ctx, inp, *, history=False):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'bill ' + ('history' if history else 'query'), Contract(), ctx.on_behalf_of)
    if history:
        header = resolve(s, inp.bill)
        query = sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.transaction_id == header['id']).order_by(
            c.transaction_revisions.c.revision_number)
    else:
        t, r, p = c.transactions, c.transaction_revisions, c.purchase_profiles
        query = (sa.select(t.c.id).select_from(
            t.join(r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id))
            .where(t.c.type == DOCUMENT_TYPE))
        if inp.vendor:
            query = query.where(p.c.vendor_id == resolve_party(s.company, 'vendor', inp.vendor)['id'])
        if inp.date_from:
            query = query.where(r.c.date >= inp.date_from)
        if inp.date_to:
            query = query.where(r.c.date <= inp.date_to)
        if inp.due_from:
            query = query.where(p.c.due_date >= inp.due_from)
        if inp.due_to:
            query = query.where(p.c.due_date <= inp.due_to)
        if inp.status:
            query = query.where(t.c.status == inp.status)
        if inp.number:
            query = query.where(t.c.number.contains(inp.number, autoescape=True))
        if inp.supplier_reference is not None:
            query = query.where(p.c.supplier_reference_key == reference_key(inp.supplier_reference))
        # Descending is the exact reverse of the stated order, so the row before a given bill
        # is the row after it here. The cursor carries the direction that minted it:
        # page_state rejects a continuation whose contract has changed.
        order = (r.c.date, t.c.id)
        query = query.order_by(*([column.desc() for column in order] if inp.direction == 'desc' else order))
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    shared = dict(count=len(found), has_more=more,
                  next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)
    if history:
        return BillHistoryOutput(
            **{k: header[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
            items=[revision_output(s, header, revision, summary_only=True) for revision in found], **shared)
    identifiers = [row['id'] for row in found]
    headers = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transactions).where(c.transactions.c.id.in_(identifiers))).mappings()} if found else {}
    ordered = [headers[identifier] for identifier in identifiers]
    revision_ids = [header['current_revision_id'] for header in ordered]
    revisions = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.id.in_(revision_ids))).mappings()} if found else {}
    profiles = {row['revision_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.purchase_profiles).where(
            c.purchase_profiles.c.revision_id.in_(revision_ids))).mappings()} if found else {}
    obligations = {row['transaction_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.ap_obligation_keys).where(
            c.ap_obligation_keys.c.transaction_id.in_(identifiers))).mappings()} if found else {}
    keys = [row['id'] for row in obligations.values()]
    applied = applied_totals(s, keys)
    composed = applied_sources(s, keys)
    orders = order_ids(s, identifiers)
    items = []
    for header in ordered:
        revision = revisions.get(header['current_revision_id'])
        if revision is None or revision['transaction_id'] != header['id']:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'transaction_revision'})
        obligation = obligations.get(header['id'])
        settlement = settlement_output(header, revision, obligation,
                                       applied.get(obligation['id'], 0) if obligation else 0,
                                       composed.get(obligation['id']) if obligation else None)
        items.append(BillSummaryOutput(**summary(header, revision, profiles[revision['id']], settlement,
                                                 orders.get(header['id']))))
    return BillPageOutput(items=items, **shared)


# ---------------------------------------------------------------- change detection


def _custom_semantic(snapshot):
    return {key: {name: value for name, value in field.items() if name != 'value_id'}
            for key, field in snapshot.items()}


def _line_semantic(line):
    return {'line_id': line['line_id'], 'family': line['family'], 'memo': line['memo'],
            'amount_minor_units': line['amount_minor_units'], 'profile': line['profile'].model_dump()}


def _saved_semantic(s, revision):
    profile = profile_row(s, revision)
    lines = [_line_semantic(dict(line, profile=LINE_FACTS[line['family']].model_validate_json(
        line['line_snapshot']))) for line in saved_lines(s, revision)]
    return dict(date=revision['date'], number=revision['number'], memo=revision['memo'],
                issuer=json.loads(revision['issuer_snapshot']),
                profile=BillProfile.model_validate_json(profile['profile_snapshot']).model_dump(),
                lines=lines, custom_fields=_custom_semantic(json.loads(revision['custom_fields_snapshot'])))


def _changes(before, after, path=''):
    if isinstance(before, dict) and isinstance(after, dict):
        return [field for key in sorted(set(before) | set(after))
                for field in _changes(before.get(key), after.get(key), f'{path}.{key}'.lstrip('.'))]
    if path == 'lines' and isinstance(before, list) and isinstance(after, list):
        old = {line['line_id']: line for line in before}
        new = {line['line_id'] or f'new_{i}': line for i, line in enumerate(after)}
        changed = _changes(old, new, path)
        if [line['line_id'] for line in before] != [line['line_id'] for line in after]:
            changed.append('lines.order')
        return changed
    return [path] if before != after else []


def _history_snapshot(blob):
    """Unusable historical evidence means unknown fields, never a decoder failure."""
    try:
        value = audit.decode_snapshot(blob)
    except (ValueError, TypeError, UnicodeError, zlib.error):
        return None
    return value if isinstance(value, dict) else None


def _historical_fields(s, header, expected):
    entries = c.audit_entries
    snapshots = s.company.conn.execute(sa.select(entries.c.after).where(
        entries.c.record_type == 'transaction', entries.c.record_id == header['id'],
        entries.c.version_after == expected, entries.c.action != 'migrate').limit(2)).all()
    if len(snapshots) != 1:
        return None
    old = _history_snapshot(snapshots[0][0])
    if (old is None or old.get('id') != header['id'] or type(old.get('version')) is not int
            or old['version'] != expected or old.get('type') != header['type']
            or old.get('status') not in ('posted', 'voided')
            or not isinstance(old.get('current_revision_id'), str)):
        return None
    prior = effects.rows(s, c.transaction_revisions,
                         c.transaction_revisions.c.transaction_id == header['id'],
                         c.transaction_revisions.c.id == old['current_revision_id'])
    if len(prior) != 1:
        return None
    fields = _changes(_saved_semantic(s, prior[0]), _saved_semantic(s, journals.revision(s, header)))
    if old['status'] != header['status']:
        fields.append('status')
    return sorted(set(fields)) or ['version']


def _conflict_message(details, fields):
    who = details.get('updated_by_name') or details.get('updated_by') or 'unknown'
    behalf = details.get('updated_on_behalf_of_name') or details.get('updated_on_behalf_of')
    if behalf:
        who += f' on behalf of {behalf}'
    ago = details.get('seconds_since_update')
    when = f' ({ago} s ago)' if ago is not None else ''
    what = ', '.join(fields) if fields else 'fields that cannot be determined'
    version = details['current_version']
    return (f'Changes since the expected version: {what}. Latest writer: {who}{when} '
            f'(now version {version}); re-read and retry with expected_version {version}.')


def _version(s, header, expected):
    """A refused write says who changed the bill, how long ago, and what they changed."""
    try:
        return journals.version_meta(s, header, expected, history_decoder=_history_snapshot)
    except BookflowError as exc:
        if exc.code != 'E_VERSION_CONFLICT':
            raise
        fields = None
        if 'unknown_versions' not in exc.details and expected < header['version']:
            fields = _historical_fields(s, header, expected)
            if fields is None:
                exc.details['unknown_versions'] = [expected]
        exc.details['changed_fields'] = fields or []
        raise BookflowError(exc.code, message=_conflict_message(exc.details, fields),
                            details=exc.details) from None


# ---------------------------------------------------------------- writes


def commercial(s, inp, old_header, old_revision, *, document_id):
    old_profile = (BillProfile.model_validate_json(profile_row(s, old_revision)['profile_snapshot'])
                   if old_revision else None)
    date = (getattr(inp, 'date', None) or old_revision['date']) if old_revision else inp.date
    header_facts, info = resolve_header(s, inp, date, old_profile,
                                        old_revision['date'] if old_revision else None)
    currency = header_facts['currency']
    number, sequence = effects.allocate(
        s, DOCUMENT_TYPE, inp.number if inp.number is not None else
        old_header['number'] if old_header else None, document_id)
    memo = inp.memo if _supplied(inp, 'memo') or not old_revision else old_revision['memo']

    issuer = {k: v for k, v in info.items()
              if k in ('id', 'legal_name', 'display_name', 'home_currency')
              or k.startswith(('address_', 'legal_address_', 'ship_address_'))}
    if old_revision:
        issuer = json.loads(old_revision['issuer_snapshot'])
    else:
        # Dispatch pins the authorized name for preparation and validation.
        issuer['display_name'] = s.company_row['display_name']

    old_lines = saved_lines(s, old_revision) if old_revision else []
    # One grid at a time. Supplying a collection replaces that tab outright; leaving it out
    # keeps the tab exactly as it was captured, down to the account name the bill was entered
    # under, so correcting the Expenses grid cannot silently re-resolve an item line.
    seen, grids = set(), {}
    for family, supplied, resolver in (('expense', inp.expenses, _expense_line),
                                       ('item', getattr(inp, 'items', None), _item_line)):
        kept = by_family(old_lines, family)
        if old_revision and supplied is None:
            grids[family] = [dict(line_id=line['line_id'], family=family, memo=line['memo'],
                                  amount_minor_units=line['amount_minor_units'],
                                  profile=LINE_FACTS[family].model_validate_json(line['line_snapshot']))
                             for line in kept]
            seen.update(line['line_id'] for line in kept)
            continue
        collection = f'{family}s' if family == 'item' else 'expenses'
        prior, resolved_lines = {line['line_id'] for line in kept}, []
        for index, line in enumerate(supplied or []):
            key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
            # A retired identity cannot return, and an identity cannot cross tabs: the line it
            # names is on the other grid and moving it would rewrite what that line was.
            if key is not None and (key not in prior or key in seen):
                raise _invalid(collection + '.line_id',
                               'use a unique current line identity from this grid; '
                               'retired identities cannot return and a line cannot change grid')
            if key:
                seen.add(key)
            resolved = resolver(s, line, header_facts['class_id'], currency, index)
            resolved['line_id'] = key
            resolved_lines.append(resolved)
        grids[family] = resolved_lines
    # Expenses first, then items: the order of the two tabs on the document itself.
    lines = grids['expense'] + grids['item']

    expense_total = checked_sum((line['amount_minor_units'] for line in grids['expense']), 'expenses.total')
    item_total = checked_sum((line['amount_minor_units'] for line in grids['item']), 'items.total')
    total = checked_sum((expense_total, item_total), 'total')
    if total <= 0:
        raise _invalid('items' if grids['item'] and not grids['expense'] else 'expenses',
                       'a posted bill must have a positive total')
    profile = BillProfile(**header_facts, expense_total_minor_units=expense_total,
                          item_total_minor_units=item_total)

    custom.validate_kinds(s.company, inp.custom_fields, inp.custom_field_kinds, record_type=DOCUMENT_TYPE)
    custom_plan = custom.prepare(
        s.company, document_id, inp.custom_fields,
        json.loads(old_revision['custom_fields_snapshot']) if old_revision else {},
        creating=old_header is None, record_type=DOCUMENT_TYPE)
    semantic = dict(date=date, number=number, memo=memo, issuer=issuer, profile=profile.model_dump(),
                    lines=[_line_semantic(line) for line in lines],
                    custom_fields=_custom_semantic(custom_plan.snapshot))
    return dict(profile=profile, date=date, number=number, sequence=sequence, memo=memo, issuer=issuer,
                lines=lines, custom_plan=custom_plan, semantic=semantic, currency=currency,
                expense_total=expense_total, item_total=item_total, total=total)


def _posting_accounts_active(s, resolved):
    """A saved account can be closed or retyped between one revision and the next."""
    ids = {resolved['profile'].ap_account.id} | {line['profile'].account.id for line in resolved['lines']}
    rows = effects.rows(s, c.accounts, c.accounts.c.id.in_(ids))
    if {row['id'] for row in rows} != ids:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'account'})
    for row in rows:
        if not row['active']:
            raise BookflowError('E_INACTIVE_REFERENCE',
                                details={'record_type': 'account', 'record_id': row['id']})
    current = {row['id']: row for row in rows}
    if current[resolved['profile'].ap_account.id]['type'] != 'accounts_payable':
        raise BookflowError('E_VALIDATION', message=(
            'A saved posting account is no longer eligible. Select an active Accounts Payable '
            'account before posting this correction.'),
            details={'field': 'ap_account', 'reason': 'captured_posting_account_type'})
    for line in resolved['lines']:
        account = current[line['profile'].account.id]
        if account['type'] not in EXPENSE_ACCOUNTS or (
                account['system_role'] is not None and account['system_role'] not in LINE_ROLES):
            item = line.get('family') == 'item'
            raise BookflowError('E_VALIDATION', message=(
                'A saved posting account is no longer eligible. Repoint the item at an eligible '
                'expense account before posting this correction.' if item else
                'A saved posting account is no longer eligible. Select an eligible expense '
                'account before posting this correction.'),
                details={'field': 'items' if item else 'expenses',
                         'reason': 'captured_posting_account_type'})


def _has_applications(s, header):
    obligation = obligation_row(s, header['id'])
    if obligation is None:
        return False
    return bool(applied_totals(s, [obligation['id']]).get(obligation['id']))


def _from_order(s, inp, operation):
    """The purchase order this bill is being entered from, and the entry it fills in.

    What the caller supplied wins; the order fills in the rest. Returning a copy rather than
    mutating keeps ``plan.data['input']`` the caller's own words, so ``apply`` re-derives from
    the order inside the writing transaction instead of trusting what the preview read.
    """
    if operation != 'post' or getattr(inp, 'purchase_order', None) is None:
        return None, inp
    from bookflow.company import purchase_orders as orders
    source = orders.bill_source(s, inp.purchase_order)
    carried = orders.bill_entry(source[1], source[2])
    supplied = inp.model_fields_set
    if inp.vendor is not None and resolve_party(s.company, 'vendor', inp.vendor)['id'] != carried['vendor']:
        # The one field the caller may not override: a bill owed to a different vendor is not
        # this order's bill, and nothing downstream would notice -- the conversion row records
        # which order became which bill, not who either was owed to.
        raise _invalid('vendor', 'the bill is owed to the vendor the purchase order was placed with; '
                                 'omit vendor, or enter this bill without naming the order')
    return source, inp.model_copy(update={key: value for key, value in carried.items()
                                          if key not in supplied or getattr(inp, key) is None})


def prepare(s, ctx, inp, operation):
    source, entry = _from_order(s, inp, operation)
    old_header = resolve(s, inp.bill) if operation != 'post' else None
    old_revision = journals.revision(s, old_header) if old_header else None
    meta = _version(s, old_header, inp.expected_version) if old_header else None
    warnings = [w] if meta and (w := list_service.blind_write_warning(meta)) else []
    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if len(ctx.reason.strip()) > 140:
            raise _invalid('reason', 'must be at most 140 characters')
    if operation == 'update' and old_header['status'] == 'voided':
        raise _invalid('bill', 'a voided bill cannot be updated')
    if old_header and _has_applications(s, old_header):
        raise BookflowError('E_HAS_APPLICATIONS', details={
            'bill_id': old_header['id'],
            'next': 'Unapply what has been paid against this bill before correcting or voiding it.'})

    order_id = source[0]['id'] if source else (
        order_ids(s, [old_header['id']]).get(old_header['id']) if old_header else None)

    def unchanged(header, revision):
        profile = profile_row(s, revision)
        return Plan(BillWriteOutput(
            **summary(header, revision, profile, current_settlement(s, header, revision=revision),
                      order_id),
            revision=revision_output(s, header, revision), changed=False, warnings=warnings,
            duplicate_references=duplicate_references(
                s, profile['vendor_id'], profile['supplier_reference_key'], header['id'])),
            dict(input=inp, operation=operation, changed=False))

    if operation == 'void' and old_header['status'] == 'voided':
        return unchanged(old_header, old_revision)

    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    row_provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    header = dict(old_header) if old_header else dict(
        id=new_id(), **common(s.actor.id, ctx.interface.value, at), type=DOCUMENT_TYPE, status='posted',
        voided_at=None, voided_by=None, void_reason=None, void_posting_batch_id=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    resolved, changed_fields, custom_plan, sequence = None, [], None, None

    if operation != 'void':
        resolved = commercial(s, entry, old_header, old_revision, document_id=header['id'])
        changed_fields = _changes(_saved_semantic(s, old_revision), resolved['semantic']) if old_revision else []
        if old_revision and not changed_fields and not resolved['custom_plan'].changed:
            return unchanged(old_header, old_revision)
        journals.open_dates(s, [resolved['date']] + ([old_revision['date']] if old_revision else []))
        _posting_accounts_active(s, resolved)
        profile, currency = resolved['profile'], resolved['currency']
        custom_plan, sequence = resolved['custom_plan'], resolved['sequence']
        revision = dict(**created(), transaction_id=header['id'],
                        revision_number=old_revision['revision_number'] + 1 if old_revision else 1,
                        supersedes_revision_id=old_revision['id'] if old_revision else None,
                        date=resolved['date'], number=resolved['number'], name_type='vendor',
                        name_id=profile.vendor.id, memo=resolved['memo'],
                        total_minor_units=resolved['total'], currency=currency,
                        issuer_snapshot=json_text(resolved['issuer']),
                        custom_fields_snapshot=json_text(custom_plan.snapshot), audit_event_id=event)
        header.update(number=revision['number'], current_revision_id=revision['id'])
        pending['transaction_revisions'].append(revision)
        pending['purchase_profiles'].append(dict(
            transaction_id=header['id'], revision_id=revision['id'], **row_provenance, type=DOCUMENT_TYPE,
            vendor_id=profile.vendor.id, ap_account_id=profile.ap_account.id,
            terms_id=profile.terms.id if profile.terms else None, due_date=profile.due_date,
            supplier_reference=profile.supplier_reference,
            supplier_reference_key=profile.supplier_reference_key,
            expense_total_minor_units=resolved['expense_total'],
            item_total_minor_units=resolved['item_total'],
            profile_snapshot=json_text(profile.model_dump())))
        for position, line in enumerate(resolved['lines'], 1):
            identity = line['line_id']
            if identity is None:
                ident = dict(**created(), transaction_id=header['id'])
                pending['document_line_identities'].append(ident)
                identity = ident['id']
            facts = line['profile']
            envelope = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                            line_id=identity, position=position, kind='purchase', account_id=None,
                            side=None, amount_minor_units=None, currency=currency, account_snapshot=None,
                            name_type='vendor', name_id=profile.vendor.id, party_name=profile.vendor.label,
                            class_id=facts.class_id.id if facts.class_id else None,
                            class_name=facts.class_id.label if facts.class_id else None,
                            description=line['memo'], **dict.fromkeys(journals.FACTS))
            pending['document_lines'].append(envelope)
            # One envelope, one profile row, in whichever of the two tables the line's family
            # owns. Everything above this line -- identity, position, class, party, memo -- is
            # written the same way whichever tab the row was typed on.
            shared = dict(document_line_id=envelope['id'], transaction_id=header['id'],
                          revision_id=revision['id'], **row_provenance,
                          account_id=facts.account.id,
                          amount_minor_units=line['amount_minor_units'],
                          customer_id=facts.customer.id if facts.customer else None,
                          billable=facts.billable, line_snapshot=json_text(facts.model_dump()))
            if line['family'] == 'item':
                pending['purchase_item_lines'].append(dict(
                    shared, item_id=facts.item.id, quantity_microunits=facts.quantity_microunits,
                    unit_cost_minor_units=facts.unit_cost_minor_units))
            else:
                pending['purchase_expense_lines'].append(shared)
    else:
        journals.open_dates(s, [old_revision['date']])
        revision = old_revision

    current_batch = None
    if old_header:
        header.update(version=old_header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface.value)
        current_batch = effects.rows(s, c.posting_batches,
                                     c.posting_batches.c.revision_id == old_revision['id'],
                                     c.posting_batches.c.kind != 'reversal')[0]
        inverse = effects.reverse(s, header, old_revision, current_batch, event, created, pending)
        if operation == 'void':
            header.update(status='voided', voided_at=at, voided_by=s.actor.id,
                          void_reason=ctx.reason.strip(), void_posting_batch_id=inverse['id'])
            changed_fields = ['status']
    if operation != 'void':
        batch = dict(**created(), transaction_id=header['id'], revision_id=revision['id'],
                     kind='replacement' if old_header else 'original', effective_date=revision['date'],
                     reverses_batch_id=None,
                     replaces_batch_id=current_batch['id'] if current_batch else None,
                     audit_event_id=event)
        pending['posting_batches'].append(batch)
        _business_postings(s, header, revision, batch, resolved, pending, created, event)

    consumption = None
    if source is not None:
        from bookflow.company import purchase_orders as orders
        consumption = orders.consume(s, ctx, source, header['id'], revision['id'], at, event)

    view_profile = (pending['purchase_profiles'][0] if pending['purchase_profiles']
                    else profile_row(s, revision))
    obligation = obligation_row(s, header['id'], pending)
    keys = [obligation['id']] if obligation else []
    applied = applied_totals(s, keys)
    output = BillWriteOutput(
        **summary(header, revision, view_profile, settlement_output(
            header, revision, obligation, applied.get(obligation['id'], 0) if obligation else 0,
            applied_sources(s, keys).get(obligation['id']) if obligation else None), order_id),
        revision=revision_output(s, header, revision, pending),
        warnings=warnings, changed_fields=changed_fields,
        duplicate_references=duplicate_references(
            s, view_profile['vendor_id'], view_profile['supplier_reference_key'], header['id']))
    plan = Plan(output, dict(input=inp, operation=operation, changed=True, header=header,
                             before=old_header, old_revision=old_revision, pending=pending,
                             sequence=sequence, event=event, custom_plan=custom_plan,
                             consumption=consumption,
                             semantic=resolved['semantic'] if resolved else None))
    from bookflow.company.bill_validation import validate
    validate(plan, s, ctx)
    return plan


def _business_postings(s, header, revision, batch, resolved, pending, created, event):
    """Dr each entered line's own account; Cr Accounts Payable the total, once.

    The payable is one credit because that is what the vendor is owed -- one figure on one
    document -- and each entered line's share of it is carried as an attribution row on that
    credit rather than as a separate leg. Those attribution rows are what the obligation
    components name, which is how a payment can later land on particular lines.

    Nothing here asks which tab a line came from. An expense line's account was typed and an
    item line's was read off the item, but by the time both are stored each is one captured
    account and one positive amount, so both take exactly one debit leg and one component.
    """
    profile = resolved['profile']
    currency = revision['currency']
    obligation = obligation_row(s, header['id'], pending)
    if obligation is None:
        obligation = dict(**created(), transaction_id=header['id'], ordinal=1,
                          vendor_id=profile.vendor.id, ap_account_id=profile.ap_account.id,
                          currency=currency, audit_event_id=event)
        pending['ap_obligation_keys'].append(obligation)
    envelopes = [row for row in pending['document_lines'] if row['revision_id'] == revision['id']]
    profiles = {row['document_line_id']: (family, row) for family, table in
                (('expense', 'purchase_expense_lines'), ('item', 'purchase_item_lines'))
                for row in pending[table]}
    line_no = 0

    def leg(account, amount, debit, class_id, class_name, description):
        nonlocal line_no
        line_no += 1
        value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                     account_id=account.id, account_snapshot=json_text(account.model_dump()),
                     currency=currency, name_type='vendor', name_id=profile.vendor.id,
                     party_name=profile.vendor.label, class_id=class_id, class_name=class_name,
                     description=description,
                     debit_minor_units=amount if debit else 0,
                     credit_minor_units=0 if debit else amount,
                     reversed_line_id=None, **dict.fromkeys(journals.FACTS))
        pending['posting_lines'].append(value)
        return value

    def attribute(posting, envelope, units):
        source = dict(**created(), transaction_id=header['id'], posting_line_id=posting['id'],
                      revision_id=revision['id'], document_line_id=envelope['id'],
                      amount_minor_units=units, currency=currency,
                      reversed_source_id=None, tax_component_id=None)
        pending['posting_line_sources'].append(source)
        return source

    for envelope in envelopes:
        family, line = profiles[envelope['id']]
        facts = LINE_FACTS[family].model_validate_json(line['line_snapshot'])
        cost = leg(facts.account, line['amount_minor_units'], True,
                   envelope['class_id'], envelope['class_name'], envelope['description'])
        attribute(cost, envelope, line['amount_minor_units'])
    payable = leg(profile.ap_account, resolved['total'], False,
                  profile.class_id.id if profile.class_id else None,
                  profile.class_id.label if profile.class_id else None, resolved['memo'])
    for envelope in envelopes:
        line = profiles[envelope['id']][1]
        source = attribute(payable, envelope, line['amount_minor_units'])
        pending['ap_obligation_components'].append(dict(
            **created(), transaction_id=header['id'], revision_id=revision['id'],
            key_id=obligation['id'], document_line_id=envelope['id'], ordinal=1,
            posting_source_id=source['id'], amount_minor_units=line['amount_minor_units'],
            currency=currency, audit_event_id=event))


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: references, numbering and dates are only
    # decisive here, and the preview may have been prepared against an older read.
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    from bookflow.company.bill_validation import validate
    validate(fresh, s, ctx)
    return effects.persist(fresh, ctx, s, command_name='bill ' + plan.data['operation'],
                           table_kinds=TABLE_KINDS, companion=fresh.data.get('consumption'))
