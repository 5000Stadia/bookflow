"""Purchase orders: what was ordered, before anything is owed.

A purchase order is a commitment, not a liability. Entering one posts no journal entry, moves
no account and creates no payable, so the trial balance after writing one is the trial balance
before it -- byte for byte. That is structural rather than a rule: nothing in this module ever
touches ``posting_batches``, ``posting_lines`` or ``ap_obligation_keys``, and a purchase order
is not a ``transactions`` row, so it has no batch to own.

**The lifecycle.** ``post`` writes the order. ``update`` replaces it whole and appends a new
immutable revision, exactly as a bill correction does, and it is also where the receiving
state moves: ``open`` while nothing has arrived, ``partly_received`` while some has, ``closed``
when nothing more is coming. There is no separate close verb because closing is reversible and
this codebase already puts reversible state changes on ``update`` -- ``estimate update`` is
where ``accepted`` and ``declined`` live. ``void`` is the exception, as it is for an estimate:
it needs a reason, it is not a state you come back from, and after it the order can never
become a bill.

**Becoming a bill.** ``bill post`` may name an order. The bill then takes the order's vendor,
terms, class, memo and lines unless the caller overrode them, and ``purchase_order_conversions``
records what was consumed: the exact revision, the exact version, and the bill it became. That
table is unique on both sides, so an order becomes at most one bill and a bill comes from at
most one order -- which is why "already consumed" cannot be edited around. The order is then
closed, because what was ordered has been billed.

This is the estimate-to-invoice shape with the fractions taken out. An estimate is billed in
percentages across many invoices, so it needs allocations, spans and a conversion key; an order
with no item receipts behind it is billed once and entirely, so consumption is one row and the
uniqueness constraint is the whole retry story.
"""
from __future__ import annotations

from datetime import date as calendar_date
import json
import zlib

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert

from bookflow.company import accounts, list_service, schema as c
from bookflow.company import document_effects as effects
from bookflow.company.bill_models import BillExpenseInput
from bookflow.company.journal_models import checked_sum, parse_domestic_amount
from bookflow.company.lists import get_list_definition
from bookflow.company.parties import resolve_party
from bookflow.company.profiles import TermInput, compute_term_dates
from bookflow.company.purchase_order_facts import (
    Account, Item, Origin, PurchaseOrderLineProfile, PurchaseOrderProfile, Reference, Term, Vendor,
)
from bookflow.company.purchase_order_models import (
    PurchaseOrderConversionOutput, PurchaseOrderHistoryOutput, PurchaseOrderLineOutput,
    PurchaseOrderOutput, PurchaseOrderPageOutput, PurchaseOrderRevisionOutput,
    PurchaseOrderRevisionSummaryOutput, PurchaseOrderSummaryOutput, PurchaseOrderWriteOutput,
)
from bookflow.company import sales_calculations as calc
from bookflow.core import audit, clock, versioning
from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    format_percentage_millionths, format_quantity_micro_units, parse_quantity_micro_units,
)
from bookflow.core.ids import is_ulid, new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

RECORD_TYPE = 'purchase_order'
SEQUENCE = 'purchase_order'
# Insert order matters: a line names its identity and its revision, so both exist first.
TABLE_KINDS = (
    ('purchase_order_revisions', 'purchase_order_revision', 'id'),
    ('purchase_order_line_identities', 'purchase_order_line_identity', 'id'),
    ('purchase_order_lines', 'purchase_order_line', 'id'),
)
# What an ordered expense line may be destined for. The same list a bill's expense line takes,
# because an order for goods becomes a bill for the same goods and a destination a bill would
# refuse is one this order could never be billed through.
ORDER_ACCOUNTS = frozenset({'expense', 'other_expense', 'cost_of_goods_sold',
                            'fixed_asset', 'other_asset', 'other_current_asset'})
LINE_ROLES = frozenset({'cost_of_goods_sold'})
# Where an item's purchase cost is destined, in the order the anchor's item record resolves it.
ITEM_ACCOUNTS = ('expense_account_id', 'cogs_account_id', 'asset_account_id')


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def resolve(s, selector):
    t = c.purchase_orders
    key = selector.upper() if is_ulid(selector) else selector
    found = effects.rows(s, t, t.c.id == key)
    if not found:
        found = effects.rows(s, t, t.c.number == selector)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': RECORD_TYPE, 'selector': selector})
    return found[0]


def revision(s, header, number=None):
    t = c.purchase_order_revisions
    found = effects.rows(s, t, t.c.document_id == header['id'],
                         t.c.id == header['current_revision_id'] if number is None
                         else t.c.revision_number == number)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'purchase_order_revision'})
    return found[0]


def saved_lines(s, rev):
    return effects.rows(s, c.purchase_order_lines, c.purchase_order_lines.c.revision_id == rev['id'],
                        order=c.purchase_order_lines.c.position)


def conversion_row(s, document_id):
    t = c.purchase_order_conversions
    found = effects.rows(s, t, t.c.source_document_id == document_id)
    return found[0] if found else None


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


def _list_row(s, noun, table, selector, field, kind):
    row = list_service.resolve_selector(s.company, table, get_list_definition(noun), selector)
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': kind, 'record_id': row['id'], 'field': field})
    return row


def _term_facts(row):
    ref = _reference(row).model_dump()
    return Term(**ref, **{k: row[k] for k in Term.model_fields if k not in ref})


def _term_due(term, date):
    """Asked of the terms owner and reported; an order derives no due date of its own."""
    payload = {k: getattr(term, k) for k in TermInput.model_fields if k not in ('name', 'discount_percent')}
    payload.update(name=term.label, discount_percent=(
        format_percentage_millionths(term.discount_percent_millionths)
        if term.discount_percent_millionths is not None else None))
    try:
        compute_term_dates(TermInput(**payload), calendar_date.fromisoformat(date))
    except (OverflowError, ValueError):
        raise _invalid('terms', 'computed dates must fit supported ISO years') from None


def _supplied(inp, field):
    return field in inp.model_fields_set


def _info(s):
    return dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())


def resolve_header(s, inp, date, previous):
    """Every header fact a purchase order revision captures, and where each came from."""
    info = _info(s)
    currency = info['home_currency']
    origins = dict(previous.origins) if previous else {}

    selector = getattr(inp, 'vendor', None) or (previous.vendor.id if previous else None)
    if selector is None:
        raise _invalid('vendor', 'a purchase order is placed with a vendor')
    row = resolve_party(s.company, 'vendor', selector)
    changed_vendor = previous is None or row['id'] != previous.vendor.id
    if changed_vendor and not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE',
                            details={'record_type': 'vendor', 'record_id': row['id'], 'field': 'vendor'})
    vendor = Vendor(**_reference(row).model_dump(),
                    **{k: row.get(k) for k in ('company_name', 'email', 'phone', 'account_number')})
    origins['vendor'] = Origin(kind='explicit')

    # Terms: what was typed, else the vendor's own when the vendor is new to this order, else
    # whatever the order already carried. An order fixes no due date -- the bill does that --
    # but the terms travel to the bill, so they are captured and validated here.
    if _supplied(inp, 'terms'):
        term = _term_facts(_list_row(s, 'term', c.terms, inp.terms, 'terms', 'term')) if inp.terms else None
        origins['terms'] = Origin(kind='explicit')
    elif changed_vendor:
        term = _term_facts(_list_row(s, 'term', c.terms, row['terms_id'], 'terms', 'term')) if row['terms_id'] else None
        origins['terms'] = Origin(kind='default', source_id=row['id'] if term else None)
    else:
        term = previous.terms
    if term:
        _term_due(term, date)

    if _supplied(inp, 'expected_date'):
        expected = inp.expected_date
        origins['expected_date'] = Origin(kind='explicit')
    else:
        expected = previous.expected_date if previous else None
    if expected is not None and expected < date:
        raise _invalid('expected_date', 'must be on or after the order date')

    if _supplied(inp, 'ship_to'):
        ship_to = (inp.ship_to or '').strip() or None
        origins['ship_to'] = Origin(kind='explicit')
    elif previous is not None:
        ship_to = previous.ship_to
    else:
        # No shipping address typed: the company's own, which is where an order lands by default.
        parts = [info.get('ship_address_' + field) for field in
                 ('line1', 'line2', 'city', 'state', 'postal_code', 'country')]
        ship_to = ', '.join(part for part in parts if part) or None
        origins['ship_to'] = Origin(kind='default', source_id=info['id'] if ship_to else None)

    if _supplied(inp, 'reference'):
        order_reference = (inp.reference or '').strip() or None
        origins['reference'] = Origin(kind='explicit')
    else:
        order_reference = previous.reference if previous else None

    if _supplied(inp, 'class_id'):
        klass = (_reference(_list_row(s, 'class', c.classes, inp.class_id, 'class_id', 'class'))
                 if inp.class_id else None)
        origins['class_id'] = Origin(kind='explicit')
    else:
        klass = previous.class_id if previous else None

    return dict(vendor=vendor, expected_date=expected, ship_to=ship_to, terms=term,
                reference=order_reference, class_id=klass, currency=currency, origins=origins), info


def _item_destination(s, row, field):
    """The account an item's purchase cost is destined for, captured with the item."""
    for column in ITEM_ACCOUNTS:
        if row.get(column):
            return _account_facts(_account_row(s, row[column], field))
    raise _invalid(field, f'"{row["name"]}" has no purchase account; set an expense, cost of goods '
                          'sold or asset account on the item before ordering it')


def _line(s, line, header_class, currency, index):
    field = f'lines.{index}'
    origins = {'class_id': Origin(kind='explicit' if line.class_mode == 'value' else 'default')}
    if line.item is not None:
        row = _list_row(s, 'item', c.items, line.item, field + '.item', 'item')
        account = _item_destination(s, row, field + '.item')
        item = Item(**_reference(row).model_dump(), type=row['type'],
                    purchase_description=row.get('purchase_description'), account=account)
        account_facts = None
        description = line.description if line.description is not None else row.get('purchase_description')
        if line.description is None and description is not None:
            origins['description'] = Origin(kind='default', source_id=row['id'])
    else:
        row = _account_row(s, line.account, field + '.account')
        if row['type'] not in ORDER_ACCOUNTS:
            raise _invalid(field + '.account',
                           f'"{row["full_name"]}" is a {row["type"].replace("_", " ")} account; an ordered '
                           'line takes an expense, cost of goods sold, or asset account')
        if row['system_role'] is not None and row['system_role'] not in LINE_ROLES:
            raise _invalid(field + '.account',
                           f'"{row["full_name"]}" is written by the command that owns it, not by a '
                           'purchase order line')
        if row['currency'] != currency:
            raise _invalid(field + '.account', 'account must use the home currency')
        item, account_facts = None, _account_facts(row)
        description = line.description

    quantity = rate = None
    if line.quantity is not None:
        quantity = parse_quantity_micro_units(line.quantity, field=field + '.quantity')
        if quantity <= 0:
            raise BookflowError('E_VALUE_RANGE', details={
                'fields': [{'field': field + '.quantity', 'problem': 'must be greater than zero'}]})
        rate = parse_domestic_amount(line.rate, currency, field + '.rate', ).minor_units
        extended = calc.extension(quantity, rate)
        if extended <= 0:
            raise _invalid(field + '.amount', 'quantity times rate must come to at least one minor unit')
        if line.amount is not None:
            stated = parse_domestic_amount(line.amount, currency, field + '.amount').minor_units
            if stated != extended:
                raise _invalid(field + '.amount',
                               f'quantity times rate is {extended} minor units, not {stated}')
        amount = extended
    else:
        amount = parse_domestic_amount(line.amount, currency, field + '.amount').minor_units

    if line.class_mode == 'none':
        klass = None
    elif line.class_id is not None:
        klass = _reference(_list_row(s, 'class', c.classes, line.class_id, field + '.class_id', 'class'))
    else:
        klass = header_class
    customer = (_reference(_list_row(s, 'customer', c.customers, line.customer, field + '.customer', 'customer'))
                if line.customer else None)
    profile = PurchaseOrderLineProfile(item=item, account=account_facts, class_id=klass,
                                       customer=customer, billable=line.billable, origins=origins)
    return dict(line_id=None, description=description, quantity_microunits=quantity,
                rate_minor_units=rate, amount_minor_units=amount, profile=profile)


# ---------------------------------------------------------------- reads


def summary(s, header, rev, conversion=None):
    currency = rev['currency']
    captured = PurchaseOrderProfile.model_validate_json(rev['profile_snapshot'])
    conversion = conversion if conversion is not None else conversion_row(s, header['id'])
    return dict(header, date=rev['date'], vendor_id=rev['vendor_id'], vendor_name=captured.vendor.label,
                expected_date=rev['expected_date'], reference=rev['reference'], memo=rev['memo'],
                currency=currency, total_minor_units=rev['total_minor_units'],
                total=Money(rev['total_minor_units'], currency).to_dict(),
                consumed=conversion is not None,
                bill_id=conversion['destination_transaction_id'] if conversion else None)


def _line_output(line, currency):
    return PurchaseOrderLineOutput(
        **{k: v for k, v in line.items() if k not in ('line_snapshot', 'billable')},
        billable=bool(line['billable']),
        quantity=(format_quantity_micro_units(line['quantity_microunits'])
                  if line['quantity_microunits'] is not None else None),
        rate=(Money(line['rate_minor_units'], currency).to_dict()
              if line['rate_minor_units'] is not None else None),
        amount=Money(line['amount_minor_units'], currency).to_dict(), currency=currency,
        line_snapshot=json.loads(line['line_snapshot']))


def revision_output(s, rev, pending=None, *, summary_only=False):
    pending = pending or {}
    supplied = [row for row in pending.get('purchase_order_lines', []) if row['revision_id'] == rev['id']]
    if summary_only:
        count = len(supplied) if supplied else s.company.conn.execute(
            sa.select(sa.func.count()).select_from(c.purchase_order_lines)
            .where(c.purchase_order_lines.c.revision_id == rev['id'])).scalar_one()
        lines = []
    else:
        lines = supplied or saved_lines(s, rev)
        count = len(lines)
    currency = rev['currency']
    values = {k: v for k, v in rev.items() if not k.endswith('_snapshot')}
    values.update(total=Money(rev['total_minor_units'], currency).to_dict(), line_count=count)
    if summary_only:
        return PurchaseOrderRevisionSummaryOutput(**values)
    return PurchaseOrderRevisionOutput(
        **values, profile=json.loads(rev['profile_snapshot']),
        custom_fields_snapshot=json.loads(rev['custom_fields_snapshot']),
        lines=[_line_output(line, currency) for line in lines])


def _conversion_output(row):
    return PurchaseOrderConversionOutput(**row) if row else None


def output(s, header, rev, pending=None, conversion=None):
    return PurchaseOrderOutput(**summary(s, header, rev, conversion),
                               revision=revision_output(s, rev, pending),
                               conversion=_conversion_output(conversion))


def show(s, inp):
    header = resolve(s, inp.purchase_order)
    requested = revision(s, header, inp.revision_number)
    conversion = conversion_row(s, header['id'])
    return PurchaseOrderOutput(**summary(s, header, revision(s, header), conversion),
                               revision=revision_output(s, requested),
                               conversion=_conversion_output(conversion))


def page(s, ctx, inp, *, history=False):
    from bookflow.company.query import continuation, page_state

    class Contract:
        cursor = inp.cursor
        query = None

        def model_dump(self, **kw):
            return inp.model_dump(**kw)

    state = page_state(s, 'purchase-order ' + ('history' if history else 'query'), Contract(),
                       ctx.on_behalf_of)
    t, r = c.purchase_orders, c.purchase_order_revisions
    if history:
        header = resolve(s, inp.purchase_order)
        query = sa.select(r).where(r.c.document_id == header['id']).order_by(r.c.revision_number)
    else:
        query = (sa.select(t.c.id).select_from(t.join(r, r.c.id == t.c.current_revision_id)))
        if inp.vendor:
            query = query.where(r.c.vendor_id == resolve_party(s.company, 'vendor', inp.vendor)['id'])
        if inp.date_from:
            query = query.where(r.c.date >= inp.date_from)
        if inp.date_to:
            query = query.where(r.c.date <= inp.date_to)
        if inp.expected_from:
            query = query.where(r.c.expected_date >= inp.expected_from)
        if inp.expected_to:
            query = query.where(r.c.expected_date <= inp.expected_to)
        if inp.status:
            query = query.where(t.c.status == inp.status)
        if inp.number:
            query = query.where(t.c.number.contains(inp.number, autoescape=True))
        if inp.reference is not None:
            # Exact on the trimmed value. A bill's supplier reference is matched case-folded
            # because it is looking for a duplicate a person may have typed twice; this is a
            # filter on a value the caller read off the order, so it means what it says.
            query = query.where(sa.func.trim(r.c.reference) == inp.reference.strip())
        if inp.open_only:
            consumed = sa.select(c.purchase_order_conversions.c.source_document_id)
            query = query.where(t.c.status.in_(('open', 'partly_received')), t.c.id.notin_(consumed))
        order = (r.c.date, t.c.id)
        query = query.order_by(*([column.desc() for column in order] if inp.direction == 'desc' else order))
    found = [dict(row) for row in s.company.conn.execute(
        query.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    shared = dict(count=len(found), has_more=more,
                  next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)
    if history:
        return PurchaseOrderHistoryOutput(
            **{k: header[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
            items=[revision_output(s, row, summary_only=True) for row in found], **shared)
    identifiers = [row['id'] for row in found]
    headers = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(t).where(t.c.id.in_(identifiers))).mappings()} if found else {}
    ordered = [headers[identifier] for identifier in identifiers]
    revisions = {row['id']: dict(row) for row in s.company.conn.execute(
        sa.select(r).where(r.c.id.in_([h['current_revision_id'] for h in ordered]))).mappings()} if found else {}
    conversions = {row['source_document_id']: dict(row) for row in s.company.conn.execute(
        sa.select(c.purchase_order_conversions).where(
            c.purchase_order_conversions.c.source_document_id.in_(identifiers))).mappings()} if found else {}
    items = []
    for header in ordered:
        rev = revisions.get(header['current_revision_id'])
        if rev is None or rev['document_id'] != header['id']:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'purchase_order_revision'})
        items.append(PurchaseOrderSummaryOutput(**summary(s, header, rev, conversions.get(header['id']))))
    return PurchaseOrderPageOutput(items=items, **shared)


# ---------------------------------------------------------------- change detection


def _line_semantic(line):
    return {'line_id': line['line_id'], 'description': line['description'],
            'quantity_microunits': line['quantity_microunits'],
            'rate_minor_units': line['rate_minor_units'],
            'amount_minor_units': line['amount_minor_units'],
            'profile': line['profile'].model_dump()}


def _saved_semantic(s, rev):
    lines = [_line_semantic(dict(line, profile=PurchaseOrderLineProfile.model_validate_json(
        line['line_snapshot']))) for line in saved_lines(s, rev)]
    return dict(date=rev['date'], number=rev['number'], status=rev['status'], memo=rev['memo'],
                profile=PurchaseOrderProfile.model_validate_json(rev['profile_snapshot']).model_dump(),
                lines=lines)


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
    try:
        value = audit.decode_snapshot(blob)
    except (ValueError, TypeError, UnicodeError, zlib.error):
        return None
    return value if isinstance(value, dict) else None


def _version(s, header, expected):
    """A refused write says who changed the order, how long ago, and what they changed."""
    writer = versioning.current_writer(s.company, RECORD_TYPE, header['id'], header)
    from bookflow.company.info import principal_names
    if writer:
        names = principal_names(s.company, {v for v in (writer.updated_by, writer.on_behalf_of) if v})
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)

    def history(version):
        entries = versioning.history_from_entries(s.company, RECORD_TYPE, header['id'], version,
                                                  _history_snapshot)
        for entry in entries:
            if entry.changed_columns is not None:
                entry.changed_columns = ['purchase_order']
        return entries

    try:
        return versioning.check_update(
            current_version=header['version'], current_updated_at=header['updated_at'],
            current_writer=writer, changes={'purchase_order'}, expected_version=expected,
            history_since=history, actor_id=s.actor.id,
            window_seconds=s.company_info_row.get('recent_activity_window_seconds', 60))
    except BookflowError as exc:
        if exc.code != 'E_VERSION_CONFLICT':
            raise
        older = effects.rows(s, c.purchase_order_revisions,
                             c.purchase_order_revisions.c.document_id == header['id'],
                             c.purchase_order_revisions.c.revision_number == expected)
        fields = (_changes(_saved_semantic(s, older[0]), _saved_semantic(s, revision(s, header)))
                  if older else ['version'])
        exc.details['changed_fields'] = fields or ['version']
        from bookflow.core.versioning import _conflict_message
        exc.message = _conflict_message(exc.details, exc.details['changed_fields'])
        raise


def _number(s, explicit, own=None):
    t = c.purchase_orders

    def occupied(value):
        query = sa.select(t.c.id).where(t.c.number == value)
        if own:
            query = query.where(t.c.id != own)
        return s.company.conn.execute(query).first() is not None

    if explicit is not None:
        if occupied(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': RECORD_TYPE})
        return explicit, None
    found = effects.rows(s, c.sequences, c.sequences.c.name == SEQUENCE)
    value, prefix = (found[0]['next_number'], found[0]['prefix']) if found else (1, '')
    while occupied(f'{prefix}{value}'):
        value += 1
    if value >= 9223372036854775807:
        raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
    return f'{prefix}{value}', dict(name=SEQUENCE, next_number=value + 1, prefix=prefix)


# ---------------------------------------------------------------- writes


def commercial(s, inp, old_header, old_revision, *, document_id):
    old_profile = (PurchaseOrderProfile.model_validate_json(old_revision['profile_snapshot'])
                   if old_revision else None)
    date = (getattr(inp, 'date', None) or old_revision['date']) if old_revision else inp.date
    header_facts, _info_row = resolve_header(s, inp, date, old_profile)
    currency = header_facts['currency']
    number, sequence = _number(
        s, inp.number if inp.number is not None else (old_header['number'] if old_header else None),
        old_header['id'] if old_header else None)
    memo = inp.memo if _supplied(inp, 'memo') or not old_revision else old_revision['memo']
    status = getattr(inp, 'status', None) or (old_revision['status'] if old_revision else 'open')

    old_lines = saved_lines(s, old_revision) if old_revision else []
    if old_revision and inp.lines is None:
        lines = [dict(line_id=line['line_id'], description=line['description'],
                      quantity_microunits=line['quantity_microunits'],
                      rate_minor_units=line['rate_minor_units'],
                      amount_minor_units=line['amount_minor_units'],
                      profile=PurchaseOrderLineProfile.model_validate_json(line['line_snapshot']))
                 for line in old_lines]
    else:
        prior = {line['line_id'] for line in old_lines}
        seen, lines = set(), []
        for index, line in enumerate(inp.lines):
            key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
            if key is not None and (key not in prior or key in seen):
                raise _invalid('lines.line_id', 'use a unique current line identity from this purchase '
                                                'order; retired identities cannot return')
            if key:
                seen.add(key)
            resolved = _line(s, line, header_facts['class_id'], currency, index)
            resolved['line_id'] = key
            lines.append(resolved)

    total = checked_sum((line['amount_minor_units'] for line in lines), 'lines.total')
    if total <= 0:
        raise _invalid('lines', 'a purchase order must have a positive total')
    profile = PurchaseOrderProfile(**header_facts, total_minor_units=total)
    semantic = dict(date=date, number=number, status=status, memo=memo, profile=profile.model_dump(),
                    lines=[_line_semantic(line) for line in lines])
    return dict(profile=profile, date=date, number=number, sequence=sequence, memo=memo, status=status,
                lines=lines, semantic=semantic, currency=currency, total=total)


def prepare(s, ctx, inp, operation):
    old_header = resolve(s, inp.purchase_order) if operation != 'post' else None
    old_revision = revision(s, old_header) if old_header else None
    meta = _version(s, old_header, inp.expected_version) if old_header else None
    warnings = [w] if meta and (w := list_service.blind_write_warning(meta)) else []

    if operation == 'void':
        if not ctx.reason or not ctx.reason.strip():
            raise BookflowError('E_REASON_REQUIRED')
        if len(ctx.reason.strip()) > 140:
            raise _invalid('reason', 'must be at most 140 characters')
    if old_header:
        consumed = conversion_row(s, old_header['id'])
        if consumed is not None:
            # Not E_HAS_APPLICATIONS: that code tells the caller to unapply a settlement, and
            # there is no settlement here to unapply. This is the estimate's own refusal --
            # linked work prevents the change -- on the purchasing side.
            raise BookflowError('E_WORK_DEPENDENCY', details={
                'purchase_order_id': old_header['id'],
                'bill_id': consumed['destination_transaction_id'],
                'problem': 'a purchase order a bill has been entered from cannot be changed',
                'next': 'Correct or void the bill instead.'})
        if old_header['status'] == 'voided' and operation == 'update':
            raise _invalid('purchase_order', 'a voided purchase order cannot be updated')

    def unchanged(header, rev):
        return Plan(PurchaseOrderWriteOutput(**output(s, header, rev).model_dump(),
                                             changed=False, warnings=warnings),
                    dict(input=inp, operation=operation, changed=False))

    if operation == 'void' and old_header['status'] == 'voided':
        return unchanged(old_header, old_revision)

    at, event = clock.now_iso(), new_id()

    def created():
        return dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)

    header = dict(old_header) if old_header else dict(
        id=new_id(), **common(s.actor.id, ctx.interface.value, at), status='open',
        voided_at=None, voided_by=None, void_reason=None)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    resolved, changed_fields, sequence = None, [], None

    if operation != 'void':
        resolved = commercial(s, inp, old_header, old_revision, document_id=header['id'])
        changed_fields = _changes(_saved_semantic(s, old_revision), resolved['semantic']) if old_revision else []
        if old_revision and not changed_fields:
            return unchanged(old_header, old_revision)
        sequence = resolved['sequence']
        rev = dict(**created(), document_id=header['id'],
                   revision_number=old_revision['revision_number'] + 1 if old_revision else 1,
                   supersedes_revision_id=old_revision['id'] if old_revision else None,
                   date=resolved['date'], number=resolved['number'], status=resolved['status'],
                   vendor_id=resolved['profile'].vendor.id,
                   expected_date=resolved['profile'].expected_date,
                   ship_to=resolved['profile'].ship_to,
                   terms_id=resolved['profile'].terms.id if resolved['profile'].terms else None,
                   reference=resolved['profile'].reference, memo=resolved['memo'],
                   class_id=resolved['profile'].class_id.id if resolved['profile'].class_id else None,
                   currency=resolved['currency'], total_minor_units=resolved['total'],
                   profile_snapshot=json_text(resolved['profile'].model_dump()),
                   custom_fields_snapshot=json_text({}), audit_event_id=event)
        header.update(number=rev['number'], status=rev['status'], current_revision_id=rev['id'])
        pending['purchase_order_revisions'].append(rev)
        for position, line in enumerate(resolved['lines'], 1):
            identity = line['line_id']
            if identity is None:
                ident = dict(**created(), document_id=header['id'])
                pending['purchase_order_line_identities'].append(ident)
                identity = ident['id']
            facts = line['profile']
            pending['purchase_order_lines'].append(dict(
                **created(), document_id=header['id'], revision_id=rev['id'], line_id=identity,
                position=position, item_id=facts.item.id if facts.item else None,
                account_id=facts.account.id if facts.account else None,
                description=line['description'], quantity_microunits=line['quantity_microunits'],
                rate_minor_units=line['rate_minor_units'],
                amount_minor_units=line['amount_minor_units'],
                customer_id=facts.customer.id if facts.customer else None, billable=facts.billable,
                class_id=facts.class_id.id if facts.class_id else None,
                line_snapshot=json_text(facts.model_dump())))
    else:
        rev = old_revision

    if old_header:
        header.update(version=old_header['version'] + 1, updated_at=at, updated_by=s.actor.id,
                      updated_via=ctx.interface.value)
    if operation == 'void':
        header.update(status='voided', voided_at=at, voided_by=s.actor.id,
                      void_reason=ctx.reason.strip())
        changed_fields = ['status']

    view = PurchaseOrderWriteOutput(
        **summary(s, header, rev), revision=revision_output(s, rev, pending), conversion=None,
        warnings=warnings, changed_fields=changed_fields)
    plan = Plan(view, dict(input=inp, operation=operation, changed=True, header=header,
                           before=old_header, pending=pending, sequence=sequence, event=event,
                           semantic=resolved['semantic'] if resolved else None))
    from bookflow.company.purchase_order_validation import validate
    validate(plan, s, ctx)
    return plan


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: references, numbering and state are only decisive
    # here, and the preview may have been prepared against an older read.
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    from bookflow.company.purchase_order_validation import validate
    validate(fresh, s, ctx)
    if not fresh.data['changed']:
        return Applied(fresh.preview, [], 'no change')
    data = fresh.data
    header, old, pending = data['header'], data['before'], data['pending']
    touched = [Touched(RECORD_TYPE, header['id'], 'update' if old else 'create',
                       old['version'] if old else None, header['version'], header, old, db='company')]
    for table, kind, key in TABLE_KINDS:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    summary_text = f"{data['operation']} purchase order {header['number']}"
    audit.write_event_to(s.company, ctx, 'purchase-order ' + data['operation'], summary_text, touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind,
                         directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if old:
        changed = s.company.conn.execute(c.purchase_orders.update().where(
            c.purchase_orders.c.id == header['id'],
            c.purchase_orders.c.version == old['version']).values(**header)).rowcount
        if changed != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': header['id']})
    else:
        s.company.conn.execute(c.purchase_orders.insert().values(**header))
    for table, _, _ in TABLE_KINDS:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    if data['sequence']:
        statement = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(statement.on_conflict_do_update(index_elements=['name'],
                                                               set_=data['sequence']))
    return Applied(fresh.preview, touched, summary_text, audited=True)


# ---------------------------------------------------------------- becoming a bill


class Consumption:
    """What entering a bill from an order writes, beside the bill itself.

    The bill owner builds its own graph and knows nothing about purchase orders; this carries
    the order's side of the same audit event -- its closing revision and the conversion row --
    so that one event says both what was entered and what was consumed.
    """

    def __init__(self, header, before, revision_row, lines, conversion):
        self.header, self.before = header, before
        self.revision, self.lines, self.conversion = revision_row, lines, conversion

    @property
    def touches(self):
        return [Touched(RECORD_TYPE, self.header['id'], 'update', self.before['version'],
                        self.header['version'], self.header, self.before, db='company'),
                Touched('purchase_order_revision', self.revision['id'], 'create', None, 1,
                        effects.decoded(self.revision), db='company'),
                *[Touched('purchase_order_line', row['id'], 'create', None, 1,
                          effects.decoded(row), db='company') for row in self.lines],
                Touched('purchase_order_conversion', self.conversion['id'], 'create', None, 1,
                        dict(self.conversion), db='company')]

    def write(self, s):
        changed = s.company.conn.execute(c.purchase_orders.update().where(
            c.purchase_orders.c.id == self.header['id'],
            c.purchase_orders.c.version == self.before['version']).values(**self.header)).rowcount
        if changed != 1:
            raise BookflowError('E_VERSION_CONFLICT', details={'record_id': self.header['id']})
        s.company.conn.execute(c.purchase_order_revisions.insert().values(**self.revision))
        if self.lines:
            s.company.conn.execute(c.purchase_order_lines.insert(), self.lines)
        s.company.conn.execute(c.purchase_order_conversions.insert().values(**self.conversion))


def bill_source(s, selector):
    """The order a bill is being entered from, refused unless it can still become one."""
    header = resolve(s, selector)
    if header['status'] == 'voided':
        raise BookflowError('E_VALIDATION', details={'fields': [{
            'field': 'purchase_order',
            'problem': 'a voided purchase order cannot become a bill'}]})
    existing = conversion_row(s, header['id'])
    if existing is not None:
        raise BookflowError('E_WORK_DEPENDENCY', details={
            'purchase_order_id': header['id'],
            'bill_id': existing['destination_transaction_id'],
            'problem': 'this purchase order has already become a bill',
            'next': 'Correct that bill instead, or enter a new bill without naming the order.'})
    rev = revision(s, header)
    lines = saved_lines(s, rev)
    if not lines:
        raise _invalid('purchase_order', 'the purchase order has no ordered lines')
    return header, rev, lines


def bill_entry(rev, lines):
    """The order's facts in the shape ``bill post`` takes them.

    Validated rows rather than raw dictionaries, because the caller merges these into its own
    input with ``model_copy`` and a copy does not re-validate: an unvalidated row would reach
    the bill writer as a dictionary and fail there instead of here.

    Each ordered line carries its own class outright -- ``class_mode`` is ``value`` when the
    order captured one and ``none`` when it did not -- so a line the order left unclassified
    does not silently pick up the bill's class.
    """
    profile = PurchaseOrderProfile.model_validate_json(rev['profile_snapshot'])
    expenses = []
    for line in lines:
        facts = PurchaseOrderLineProfile.model_validate_json(line['line_snapshot'])
        account = facts.item.account if facts.item else facts.account
        expenses.append(BillExpenseInput(
            account=account.id,
            amount={'minor_units': line['amount_minor_units'], 'currency': rev['currency']},
            memo=line['description'],
            customer=facts.customer.id if facts.customer else None,
            billable=bool(line['billable']),
            class_id=facts.class_id.id if facts.class_id else None,
            class_mode='value' if facts.class_id else 'none'))
    return dict(vendor=profile.vendor.id, expenses=expenses, memo=rev['memo'],
                terms=profile.terms.id if profile.terms else None,
                class_id=profile.class_id.id if profile.class_id else None)


def consume(s, ctx, source, destination_id, destination_revision_id, at, event):
    """Close the order and record the bill it became, as part of the bill's own audit event."""
    header, rev, lines = source
    new_header = dict(header, version=header['version'] + 1, status='closed',
                      updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    provenance = dict(created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    revision_row = dict(rev, id=new_id(), **provenance, revision_number=rev['revision_number'] + 1,
                        supersedes_revision_id=rev['id'], status='closed', audit_event_id=event)
    new_header['current_revision_id'] = revision_row['id']
    carried = [dict(line, id=new_id(), **provenance, revision_id=revision_row['id']) for line in lines]
    conversion = dict(id=new_id(), **provenance, source_document_id=header['id'],
                      source_revision_id=revision_row['id'], source_version=header['version'],
                      destination_transaction_id=destination_id,
                      destination_revision_id=destination_revision_id, destination_type='bill',
                      audit_event_id=event)
    return Consumption(new_header, header, revision_row, carried, conversion)
