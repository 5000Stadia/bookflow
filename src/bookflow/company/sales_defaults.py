"""Read-only, origin-aware resolution of commercial service-sale facts.

Posting account activity for retained facts belongs to the writer, after its
no-op comparison. This module checks activity when capturing new references.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

import sqlalchemy as sa

from bookflow.company import list_service, schema
from bookflow.company.accounts import NORMAL_BALANCE
from bookflow.company.lists import get_list_definition
from bookflow.company.list_service import normalize_lookup_key
from bookflow.company.parties import project_party_record
from bookflow.company.profiles import TermInput, compute_term_dates
from bookflow.company.sales_calculations import (
    adjusted_price, base_quantity, extension, nonnegative, selected_price, tax, total,
)
from bookflow.company.sales_facts import (
    Account, CommercialProfile, Customer, Origin, Preferences, PriceRule, Reference, SalesLineProfile,
    SalesProfile, TaxCode, TaxRule, Term, Unit,
)
from bookflow.company.sales_models import Address, SalesLineInput, _invalid, money
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_percentage_millionths, parse_quantity_micro_units, parse_percentage_millionths


_TABLES = {
    'customer': ('customer', schema.customers),
    'terms': ('term', schema.terms),
    'ship_method': ('ship-method', schema.ship_methods),
    'sales_rep': ('sales-rep', schema.sales_reps),
    'class_id': ('class', schema.classes),
    'customer_tax_code': ('sales-tax-code', schema.sales_tax_codes),
    'tax_code': ('sales-tax-code', schema.sales_tax_codes),
    'sales_tax_item': ('item', schema.items),
    'item': ('item', schema.items),
    'price_level': ('price-level', schema.price_levels),
    'payment_method': ('payment-method', schema.payment_methods),
    'customer_message_item': ('customer-message', schema.customer_messages),
    'account': ('account', schema.accounts),
    'agency': ('vendor', schema.vendors),
}


def _info(db):
    return dict(db.conn.execute(sa.select(schema.company_info)).mappings().one())


def _active(row, field, source=None):
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={
            'field': field, 'record_id': row['id'], 'source_id': source,
        })
    return row


def _row(db, field, selector, *, active=True, source=None):
    noun, table = _TABLES[field]
    definition = get_list_definition(noun)
    result = list_service.resolve_selector(db, table, definition, selector)
    return _active(result, field, source) if active else result


def _ref(row, *, version=None):
    return Reference(id=row['id'], label=row.get('full_name') or row.get('name') or row.get('code'),
                     version=version if version is not None else row['version'])


def _account(db, selector, field, allowed, *, role=None):
    if selector is None:
        raise _invalid(field, 'a mapped account is required')
    try:
        row = _row(db, 'account', selector)
    except BookflowError as exc:
        if exc.code == 'E_INACTIVE_REFERENCE':
            exc.details['field'] = field
        raise
    if row['type'] not in allowed or (role and row['system_role'] != role):
        raise _invalid(field, 'account must have the required type and system role')
    if row['currency'] != _info(db)['home_currency']:
        raise _invalid(field, 'account must use home currency')
    return Account(**{k: row[k] for k in ('id', 'name', 'full_name', 'number', 'type')},
                   normal_balance=NORMAL_BALANCE[row['type']])


def _capture(row, field):
    ref = _ref(row).model_dump()
    if field in ('tax_code', 'customer_tax_code'):
        return TaxCode(**ref, taxable=bool(row['taxable']))
    if field == 'terms':
        return Term(**ref, **{k: row[k] for k in Term.model_fields if k not in ref})
    return Reference(**ref)


def _same(db, field, selector, saved):
    if selector is None or saved is None:
        return selector is None and saved is None
    if selector == saved.id:
        return True
    # Use current canonical list lookup, not a historical label that might now
    # identify another master. Resolving identity alone does not require active.
    return _row(db, field, selector, active=False)['id'] == saved.id


class _Fields:
    """Keep origin changes even when the effective value compares equal."""

    def __init__(self, inp, previous, refresh, warnings):
        self.inp, self.previous = inp, previous
        self.refresh, self.warnings = refresh, warnings
        self.supplied = inp.model_fields_set
        self.defaults = set(inp.use_defaults)
        self.origins = dict(previous.origins) if previous else {}

    def defaulted(self, field):
        return self.origins.get(field, Origin(kind='default')).kind == 'default'

    def needs(self, field, dependency=False):
        return (self.previous is None or field in self.supplied or field in self.defaults
                or (self.defaulted(field) and (dependency or self.refresh)))

    def value(self, field, default: Callable, *, dependency=False, saved=None):
        if field in self.supplied:
            self.origins[field] = Origin(kind='explicit')
            return getattr(self.inp, field)
        if field in self.defaults or self.previous is None or (self.defaulted(field) and (dependency or self.refresh)):
            value, source = default()
            self.origins[field] = Origin(kind='default', source_id=source)
            if self.previous is not None:
                self.warnings.append(f'{field}: defaults resolved again')
            return value
        return saved

    def reference(self, db, field, default: Callable, *, dependency=False, active=True):
        saved = getattr(self.previous, field) if self.previous else None
        needs = self.needs(field, dependency)
        if not needs and not self.refresh:
            return saved
        selector = self.value(field, default, dependency=dependency,
                              saved=saved.id if saved else None)
        if selector is None:
            return None
        if not self.refresh and field not in self.defaults and _same(db, field, selector, saved):
            return saved
        row = _row(db, field, selector, active=active,
                   source=self.origins[field].source_id)
        return _capture(row, field)


def _tax_rules(db, selector, source=None):
    row = _row(db, 'sales_tax_item', selector, source=source)
    if row['type'] == 'sales_tax_group':
        ids = db.conn.execute(sa.select(schema.item_members.c.component_item_id).where(
            schema.item_members.c.owner_item_id == row['id'], schema.item_members.c.active.is_(True)
        ).order_by(schema.item_members.c.position, schema.item_members.c.id)).scalars().all()
        if not ids or len(ids) > 200 or len(ids) != len(set(ids)):
            raise _invalid('sales_tax_item', 'tax group needs 1–200 distinct active components')
        rows = [_row(db, 'sales_tax_item', member, source=source) for member in ids]
    else:
        rows = [row]
    rules = []
    for component in rows:
        if component['type'] != 'sales_tax_item' or component['tax_percent_millionths'] is None:
            raise _invalid('sales_tax_item', 'select a sales tax item or tax group')
        agency = _row(db, 'agency', component['tax_agency_vendor_id'], source=source)
        if not agency['is_tax_agency']:
            raise _invalid('sales_tax_item', 'tax agency must be a flagged vendor')
        account = _account(db, component['liability_account_id'], 'sales_tax_item',
                           {'other_current_liability'}, role='sales_tax_payable')
        rules.append(TaxRule(**_ref(component).model_dump(),
                             rate_percent_millionths=component['tax_percent_millionths'],
                             agency=_ref(agency), liability_account=account))
    return _ref(row), rules


def resolve_header(s, inp, doc_type, *, previous: SalesProfile | None = None,
                   old_date: str | None = None) -> tuple[SalesProfile, list[str]]:
    """Resolve a header without identities, audit, or database mutations."""
    nonposting = doc_type in ('proposal', 'estimate', 'work_order')
    if doc_type not in ('invoice', 'sales_receipt') and not nonposting:
        raise _invalid('type', 'expected invoice or sales_receipt')
    db = s.company
    info = _info(db)
    refresh = inp.refresh_defaults
    warnings = []
    fields = _Fields(inp, previous, refresh, warnings)
    prefs = Preferences(**{k: info[k] for k in Preferences.model_fields})
    if previous and not refresh:
        # Tax policy is a document fact; availability of new class/unit/price
        # choices is a current-company control.
        prefs = previous.preferences.model_copy(deep=True)
    customer_selector = getattr(inp, 'customer', None) or (previous.customer.id if previous else None)
    same_customer = previous is not None and _same(db, 'customer', customer_selector, previous.customer)
    customer_changed = not same_customer
    row = _row(db, 'customer', customer_selector, active=customer_changed or refresh)
    # Passing empty custom values avoids unrelated orphan custom slots affecting
    # commercial party projection. None would read and validate all custom data.
    party = project_party_record(db, 'customer', row, custom_values=())
    customer = previous.customer if same_customer and not refresh else Customer(
        **_ref(row).model_dump(), **{k: party.get(k) for k in Customer.model_fields
                                   if k not in Reference.model_fields})
    fields.origins['customer'] = Origin(kind='explicit')

    def party_default(key):
        return lambda: (party.get('effective_' + key), party.get(key.removesuffix('_id') + '_source_id'))

    default_map = {
        'terms': 'terms_id', 'ship_method': 'preferred_ship_method_id',
        'sales_rep': 'sales_rep_id', 'class_id': 'default_class_id',
        'customer_tax_code': 'sales_tax_code_id', 'sales_tax_item': 'sales_tax_item_id',
        'price_level': 'price_level_id', 'payment_method': 'preferred_payment_method_id',
    }
    out = {'customer': customer, 'preferences': prefs}
    if previous and prefs != previous.preferences:
        warnings.append('preferences: current company settings captured by refresh')
    for field in ('terms', 'ship_method', 'sales_rep', 'class_id', 'customer_tax_code', 'price_level', 'payment_method'):
        if (field == 'terms' and doc_type != 'invoice' and not nonposting) or (field == 'payment_method' and doc_type != 'sales_receipt'):
            continue
        enabled = info['use_classes'] if field == 'class_id' else info['enable_price_levels'] if field == 'price_level' else True
        if not enabled and field in fields.supplied and getattr(inp, field) is not None:
            raise _invalid(field, 'feature is disabled in company preferences')
        default = party_default(default_map[field])
        if field == 'class_id' and not enabled:
            default = lambda: (None, info['id'])
        # Disabled stored price assignments are still identified, but unused
        # inactive levels must not block standard/manual pricing.
        needed = enabled
        submitted_lines = getattr(inp, 'lines', None)
        if field == 'price_level' and submitted_lines and all(
            'unit_price' in line.model_fields_set or 'price_level' in line.model_fields_set
            or (nonposting and bool(line.model_fields_set & {'markup_percent', 'net_amount'}))
            for line in submitted_lines
        ):
            needed = False
        if field == 'customer_tax_code' and not prefs.sales_tax_enabled and field not in fields.supplied:
            # Default taxable codes are unused under disabled tax. Nontaxable
            # codes still represent an exemption and may be captured.
            selector, source = default()
            if selector and _row(db, field, selector, active=False)['taxable']:
                default = lambda: (None, source)
        out[field] = fields.reference(db, field, default, dependency=customer_changed,
                                      active=needed or field in fields.supplied or refresh)
    if not prefs.enable_price_levels:
        warnings.append('price_level: price levels disabled; standard/manual price is in use')

    out['billing_address'] = fields.value('billing_address', lambda: (
        Address.model_validate(party['effective_billing_address']) if party['effective_billing_address'] else None,
        party['billing_address_source_id']), dependency=customer_changed,
        saved=previous.billing_address if previous else None)
    saved_shipping = previous.shipping_address if previous else None
    selected_id = previous.shipping_address_id if previous else None
    if 'shipping_address_id' in fields.supplied:
        selected_id = inp.shipping_address_id
        fields.origins['shipping_address'] = Origin(kind='explicit')
        saved_shipping = None
    elif 'shipping_address' in fields.supplied or 'shipping_address' in fields.defaults:
        selected_id = None
    if selected_id is not None:
        if customer_changed or refresh or 'shipping_address_id' in fields.supplied:
            selected = next((a for a in party['shipping_addresses'] if a['id'] == selected_id), None)
            if selected is None:
                raise _invalid('shipping_address_id', 'select an active address in the effective customer collection')
            saved_shipping = Address(**{k: selected.get(k) for k in Address.model_fields})
        out['shipping_address'] = saved_shipping
    elif 'shipping_address_id' in fields.supplied:
        out['shipping_address'] = None
    else:
        def shipping_default():
            selected = next((a for a in party['shipping_addresses'] if a['is_default']), None)
            return (Address(**{k: selected.get(k) for k in Address.model_fields}) if selected else None,
                    party['shipping_addresses_source_id'])
        out['shipping_address'] = fields.value('shipping_address', shipping_default,
                                               dependency=customer_changed, saved=saved_shipping)
    out['shipping_address_id'] = selected_id

    if not nonposting:
        control_field = 'ar_account' if doc_type == 'invoice' else 'deposit_to'
        control_selector = getattr(inp, control_field, None)
        old_control = previous.control_account if previous else None
        if old_control and (control_field not in fields.supplied or _same(db, 'account', control_selector, old_control)) and not refresh:
            control = old_control
        else:
            if control_selector is None and old_control:
                control_selector = old_control.id
            if control_selector is None and doc_type == 'invoice':
                rows = db.conn.execute(sa.select(schema.accounts.c.id).where(
                    schema.accounts.c.type == 'accounts_receivable', schema.accounts.c.active.is_(True))).scalars().all()
                if len(rows) != 1:
                    raise _invalid('ar_account', 'select an active AR account when there is not exactly one')
                control_selector = rows[0]
            if doc_type == 'invoice':
                control = _account(db, control_selector, control_field, {'accounts_receivable'})
            else:
                control = _account(db, control_selector, control_field, {'bank', 'other_current_asset'})
                control_row = _row(db, 'account', control.id)
                if control.type != 'bank' and control_row['system_role'] != 'undeposited_funds':
                    raise _invalid('deposit_to', 'select bank or system Undeposited Funds')
        out['control_account'] = control
        if control_field in fields.supplied or previous is None:
            fields.origins[control_field] = Origin(kind='explicit' if control_field in fields.supplied else 'default',
                                                 source_id=None if control_field in fields.supplied else info['id'])
        if doc_type == 'sales_receipt' and out['payment_method'] is None:
            raise _invalid('payment_method', 'a resolved payment method is required')

    for field in ('ship_date', 'payment_reference', 'customer_purchase_order'):
        if hasattr(inp, field):
            out[field] = fields.value(field, lambda: (None, None),
                                       saved=getattr(previous, field) if previous else None)
    message_ref = previous.customer_message_item if previous else None
    if 'customer_message_item' in fields.supplied or (refresh and message_ref and 'customer_message' not in fields.supplied):
        message_ref = fields.reference(db, 'customer_message_item', lambda: (None, None))
        out['customer_message'] = (_row(db, 'customer_message_item', message_ref.id)['text']
                                   if message_ref else None)
        fields.origins['customer_message'] = Origin(kind='default', source_id=message_ref.id if message_ref else None)
    else:
        out['customer_message'] = fields.value('customer_message', lambda: (None, None),
                                               saved=previous.customer_message if previous else None)
    if 'customer_message' in fields.supplied:
        message_ref = None
    out['customer_message_item'] = message_ref

    issue = getattr(inp, 'date', None) or old_date
    if issue is None:
        raise _invalid('date', 'an issue date is required')
    if doc_type == 'invoice':
        term = out['terms']
        due, discount = issue, None
        if term:
            payload = {k: getattr(term, k) for k in TermInput.model_fields if k not in ('name', 'discount_percent')}
            payload.update(name=term.label, discount_percent=(format_percentage_millionths(term.discount_percent_millionths)
                           if term.discount_percent_millionths is not None else None))
            try:
                dates = compute_term_dates(TermInput(**payload), date.fromisoformat(issue))
            except (OverflowError, ValueError):
                raise _invalid('terms', 'computed dates must fit supported ISO years') from None
            due = dates.due_date.isoformat()
            discount = dates.discount_date.isoformat() if dates.discount_date else None
        out['due_date'] = fields.value('due_date', lambda: (due, term.id if term else None),
                                       dependency=previous is not None and (issue != old_date or term != previous.terms),
                                       saved=previous.due_date if previous else None)
        if out['due_date'] is None or out['due_date'] < issue:
            raise _invalid('due_date', 'must be on or after invoice date')
        out['discount_date'] = discount
        out['discount_available'] = bool(discount and issue <= discount <= out['due_date'])

    exempt = out['customer_tax_code'] is not None and not out['customer_tax_code'].taxable
    disabled = not prefs.sales_tax_enabled
    if disabled and out['customer_tax_code'] and out['customer_tax_code'].taxable:
        if fields.origins['customer_tax_code'].kind == 'explicit':
            raise _invalid('customer_tax_code', 'explicit taxable treatment requires sales tax enabled')
        out['customer_tax_code'] = None
    def tax_default():
        value, source = party_default('sales_tax_item_id')()
        return (value, source) if value else (info['default_sales_tax_item_id'], info['id'])
    saved_tax = previous.sales_tax_item if previous else None
    tax_selector = fields.value('sales_tax_item', tax_default, dependency=customer_changed,
                                saved=saved_tax.id if saved_tax else None)
    tax_origin = fields.origins['sales_tax_item']
    if disabled:
        if tax_selector and tax_origin.kind == 'explicit':
            raise _invalid('sales_tax_item', 'explicit tax selection requires sales tax enabled')
        out['sales_tax_item'], out['tax_rules'] = None, None
    elif tax_selector is None:
        out['sales_tax_item'], out['tax_rules'] = None, None
    elif previous and not refresh and 'sales_tax_item' not in fields.defaults and _same(db, 'sales_tax_item', tax_selector, saved_tax):
        out['sales_tax_item'], out['tax_rules'] = saved_tax, previous.tax_rules
    else:
        try:
            out['sales_tax_item'], out['tax_rules'] = _tax_rules(db, tax_selector, tax_origin.source_id)
        except BookflowError as exc:
            if not (exempt and tax_origin.kind == 'default' and exc.code == 'E_INACTIVE_REFERENCE' and not refresh):
                raise
            out['sales_tax_item'], out['tax_rules'] = None, None
    out['origins'] = fields.origins
    if not nonposting:
        from bookflow.company import tax_policy
        policy, policy_origin = tax_policy.resolve(inp, previous, info)
        out.update(schema_version=2, sales_tax_calculation=policy, tax_policy_origin=policy_origin)
    return (CommercialProfile(**out) if nonposting else SalesProfile(**out)), warnings


def _unit(db, item, selector, mode):
    if mode == 'disabled':
        if selector is not None:
            raise _invalid('unit', 'units of measure are disabled')
        return None
    set_id = item['unit_of_measure_set_id']
    if set_id is None:
        if selector is not None:
            raise _invalid('unit', 'item has no unit set')
        return None
    owner = list_service.require_active_reference(db, schema.units_of_measure, set_id,
                                                  field='unit', record_type='unit_of_measure')
    rows = list(db.conn.execute(sa.select(schema.unit_conversions).where(
        schema.unit_conversions.c.unit_of_measure_id == set_id)).mappings())
    base = next((r for r in rows if r['is_base'] and r['active']), None)
    if selector is None:
        selected = (next((r for r in rows if r['id'] == owner['default_sales_unit_id']), None)
                    if mode == 'multiple_related_units' and owner['default_sales_unit_id'] else base)
    else:
        key = normalize_lookup_key(selector)
        selected = next((r for r in rows if selector == r['id']), None)
        if selected is None:
            matches = [r for r in rows if key in (r['name_key'], r['abbreviation_key'])]
            if len(matches) != 1:
                raise _invalid('unit', 'select an unambiguous member of the item unit set')
            selected = matches[0]
    if selected is None:
        raise _invalid('unit', 'unit set needs an active base/default sales unit')
    _active(selected, 'unit', set_id)
    if mode == 'single_unit_per_item' and not selected['is_base']:
        raise _invalid('unit', 'single-unit mode requires the base unit')
    return Unit(**_ref(selected, version=owner['version']).model_dump(), set_id=set_id,
                abbreviation=selected['abbreviation'], factor_nanounits=selected['base_factor_nanounits'])


def _price_rule(db, selector, item_id, currency, *, captured=None):
    row = _row(db, 'price_level', selector, active=captured is None)
    historical = None
    if captured is not None and row['version'] != captured.version:
        # A newly added commercial line inherits the selected header's version,
        # including per-item rules. Audit owns the complete immutable aggregate;
        # each resulting line captures only the rule it actually uses.
        from bookflow.core.audit import decode_snapshot
        entries = schema.audit_entries
        for side, version_column in (('after', entries.c.version_after), ('before', entries.c.version_before)):
            blob = db.conn.execute(sa.select(entries.c[side]).where(entries.c.record_type == 'price_level',
                entries.c.record_id == captured.id, version_column == captured.version).order_by(entries.c.id).limit(1)).scalar()
            if blob is not None:
                historical = decode_snapshot(blob)
                break
        if not historical or historical.get('id') != captured.id or historical.get('version') != captured.version:
            raise _invalid('price_level', 'saved price-level version is unavailable; explicitly refresh or select a price level')
        row = dict(historical)
        row['percent_millionths'] = parse_percentage_millionths(row['percent']) if row.get('percent') is not None else None
        for prefix in ('rounding_increment', 'rounding_offset'):
            row[prefix + '_minor_units'] = row[prefix]['minor_units']
            row[prefix + '_currency'] = row[prefix]['currency']
    if (row['currency'] or currency) != currency or any(row[k] != currency for k in ('rounding_increment_currency', 'rounding_offset_currency')):
        raise _invalid('price_level', 'price level must use home currency')
    values = dict(**_ref(row).model_dump(), kind=row['kind'], currency=currency,
                  rounding_mode=row['rounding_mode'], increment_minor_units=row['rounding_increment_minor_units'],
                  offset_minor_units=row['rounding_offset_minor_units'], percent_millionths=row['percent_millionths'])
    if row['kind'] == 'per_item':
        if historical is None:
            member = db.conn.execute(sa.select(schema.price_level_items).where(
                schema.price_level_items.c.price_level_id == row['id'],
                schema.price_level_items.c.item_id == item_id, schema.price_level_items.c.active.is_(True))).mappings().first()
        else:
            saved = next((value for value in historical['items'] if value['item_id'] == item_id and value.get('active', True)), None)
            member = (dict(price_minor_units=saved['price']['minor_units'] if saved['price'] else None,
                price_currency=saved['price']['currency'] if saved['price'] else None,
                percent_millionths=parse_percentage_millionths(saved['percent']) if saved['percent'] is not None else None,
                adjustment_basis=saved['adjustment_basis']) if saved else None)
        values['matched'] = member is not None
        if member:
            if member['price_minor_units'] is not None and member['price_currency'] != currency:
                raise _invalid('price_level', 'fixed price must use home currency')
            values.update(fixed_minor_units=member['price_minor_units'], percent_millionths=member['percent_millionths'],
                          adjustment_basis=member['adjustment_basis'])
    return PriceRule(**values)


def _derived_price(profile):
    rule = profile.price_rule
    base = profile.standard_price_minor_units
    if rule and rule.matched:
        if rule.fixed_minor_units is not None:
            base = rule.fixed_minor_units
            percent = 0
        else:
            base = {'standard_price': base, 'cost': profile.cost_minor_units,
                    'current_custom_price': profile.price_basis_minor_units}[rule.adjustment_basis]
            percent = rule.percent_millionths
        if base is None:
            raise _invalid('unit_price', f'selected rule requires {rule.adjustment_basis}; supply its base or an explicit price')
        return adjusted_price(base, percent, rule.increment_minor_units,
                              rule.offset_minor_units, rule.rounding_mode)
    if base is None:
        raise _invalid('unit_price', 'item has no standard price; supply an explicit price or usable rule')
    return nonnegative(base, 'unit_price')


def resolve_line(s, inp: SalesLineInput, header: SalesProfile, *, previous: dict | None = None,
                 previous_header: SalesProfile | None = None, refresh: bool = False,
                 nonposting: bool = False, price_override: Callable | None = None,
                 net_override: int | None = None, defer_tax: bool = False) -> tuple[dict, list[str]]:
    """Resolve one commercial line using preserved rules for ordinary edits."""
    if not nonposting and (price_override is not None or net_override is not None):
        raise _invalid('unit_price', 'price hooks require non-posting resolution')
    db = s.company
    info = _info(db)
    currency = info['home_currency']
    warnings = []
    refresh = refresh or inp.refresh_defaults
    old = None
    if previous:
        snapshot = previous['item_snapshot']
        old = SalesLineProfile.model_validate_json(snapshot) if isinstance(snapshot, str) else SalesLineProfile.model_validate(snapshot)
    if old is not None and old.pricing_basis == 'allocated':
        if nonposting:
            raise _invalid('item', 'allocated sale facts cannot become a work quote')
        from bookflow.company.billing_edits import retained_allocated_line
        return retained_allocated_line(s, inp, previous, refresh=refresh)
    fields = _Fields(inp, old, refresh, warnings)
    # Work owns its existing amount/markup hooks and captured representation.
    amount_mode = price_override is None and (
        'net_amount' in fields.supplied or
        (old is not None and old.pricing_basis == 'amount'
         and not fields.supplied & {'unit_price', 'price_level'}
         and 'unit_price' not in fields.defaults))
    if amount_mode and 'price_basis_amount' in fields.supplied:
        raise _invalid('price_basis_amount', 'amount pricing has no unit-price basis; select unit pricing first')
    item_changed = old is None or not _same(db, 'item', inp.item, old.item)
    item = _row(db, 'item', inp.item, active=item_changed or refresh)
    if item_changed or refresh:
        if item['type'] not in ('service', 'non_inventory_part', 'other_charge'):
            raise _invalid('item', 'this sale supports service, nonstock, and fixed-charge items only')
        if not item['sales_enabled']:
            raise _invalid('item', 'item is not enabled for sales')
        if item['type'] == 'other_charge' and item['other_charge_percent_millionths'] is not None:
            raise _invalid('item', 'percentage charges are not supported')
        income = _account(db, item['income_account_id'], 'item.income_account', {'income', 'other_income'})
        for name in ('price', 'cost'):
            if item[name + '_minor_units'] is not None and item[name + '_currency'] != currency:
                raise _invalid('item.' + name, 'item amounts must use home currency')
        profile = SalesLineProfile(item=_ref(item), item_type=item['type'], income_account=income,
                                   standard_price_minor_units=item['price_minor_units'], cost_minor_units=item['cost_minor_units'])
    else:
        profile = old.model_copy(deep=True)
    fields.origins['item'] = Origin(kind='explicit')
    quantity = (previous['quantity_microunits'] if previous and 'quantity' not in inp.model_fields_set
                else parse_quantity_micro_units(inp.quantity))
    description = fields.value('description', lambda: (item['description'], item['id']),
                               dependency=item_changed, saved=previous['description'] if previous else None)

    unit_needs = fields.needs('unit', item_changed) or refresh
    if unit_needs:
        saved_unit = old.unit if old else None
        selector = fields.value('unit', lambda: (None, item['id']), dependency=item_changed,
                                saved=saved_unit.id if saved_unit else None)
        same_unit = saved_unit is not None and selector == saved_unit.id
        if saved_unit and selector is not None and not same_unit:
            key = normalize_lookup_key(selector)
            matches = db.conn.execute(sa.select(schema.unit_conversions.c.id).where(
                schema.unit_conversions.c.unit_of_measure_id == saved_unit.set_id,
                sa.or_(schema.unit_conversions.c.name_key == key,
                       schema.unit_conversions.c.abbreviation_key == key))).scalars().all()
            same_unit = matches == [saved_unit.id]
        if same_unit and not item_changed and not refresh and 'unit' not in fields.defaults:
            profile.unit = saved_unit
        else:
            profile.unit = _unit(db, item, selector, info['units_of_measure_mode'])
        if saved_unit != profile.unit and old and not amount_mode and not fields.defaulted('unit_price'):
            warnings.append('unit_price: explicit price retained per selected unit')
    factor = profile.unit.factor_nanounits if profile.unit else 1_000_000_000
    base_qty = base_quantity(quantity, factor)

    customer_changed = previous_header is not None and header.customer.id != previous_header.customer.id
    header_class_changed = customer_changed or (previous_header is not None and header.class_id != previous_header.class_id)
    header_tax_changed = customer_changed or (previous_header is not None and header.customer_tax_code != previous_header.customer_tax_code)
    for field, master_key, fallback, changed in (
        ('class_id', 'default_class_id', header.class_id, header_class_changed),
        ('tax_code', 'sales_tax_code_id', header.customer_tax_code, header_tax_changed),
    ):
        disabled = field == 'class_id' and not info['use_classes']
        if disabled and field in fields.supplied and getattr(inp, field) is not None:
            raise _invalid(field, 'classes are disabled')
        def default(master_key=master_key, fallback=fallback, disabled=disabled):
            if disabled:
                return None, info['id']
            if item[master_key]:
                return item[master_key], item['id']
            return (fallback.id if fallback else None), header.customer.id
        # Header changes affect only a line whose default came from the header.
        dependent = item_changed or (changed and fields.origins.get(field, Origin(kind='default')).source_id != item['id'])
        if changed and old and not item_changed and not refresh and field not in fields.defaults:
            # A header dependency does not refresh a newly assigned item
            # default that was absent when this line was captured.
            default = lambda fallback=fallback, disabled=disabled: (
                None if disabled or fallback is None else fallback.id, header.customer.id)
        if field == 'tax_code' and not header.preferences.sales_tax_enabled and field not in fields.supplied:
            selector, source = default()
            if selector and _row(db, field, selector, active=False)['taxable']:
                default = lambda: (None, source)
        value = fields.reference(db, field, default, dependency=dependent)
        setattr(profile, field, value)
    if info['use_classes'] and info['prompt_for_class'] and profile.class_id is None:
        warnings.append(f'class_id: line {inp.line_id or profile.item.label} has no effective class')

    if amount_mode:
        price = None
        profile.schema_version = 2
        profile.pricing_basis = 'amount'
        profile.net_amount_minor_units = (
            money(inp.net_amount, currency, 'net_amount').minor_units
            if 'net_amount' in fields.supplied else old.net_amount_minor_units)
        profile.price_rule = None
        profile.price_basis_minor_units = None
        for field in ('unit_price', 'price_level', 'price_basis_amount'):
            fields.origins.pop(field, None)
        fields.origins['net_amount'] = Origin(kind='explicit')
        if old and (item_changed or old.unit != profile.unit):
            warnings.append('net_amount: explicit amount retained after item or unit change')
    else:
        profile.schema_version = 1
        profile.pricing_basis = 'unit'
        profile.net_amount_minor_units = None
        fields.origins.pop('net_amount', None)
        old_rule = old.price_rule if old else None
        old_level = Reference(**{k: getattr(old_rule, k) for k in Reference.model_fields}) if old_rule else None
        header_price_changed = ((customer_changed or (previous_header is not None and header.price_level != previous_header.price_level))
                                and fields.defaulted('price_level'))
        level_default = lambda: (header.price_level.id if header.price_level else None, header.customer.id)
        level_selector = fields.value('price_level', level_default, dependency=header_price_changed,
                                      saved=old_level.id if old_level else None)
        if not info['enable_price_levels'] and 'price_level' in fields.supplied and level_selector is not None:
            raise _invalid('price_level', 'price levels are disabled')
        level_changed = not _same(db, 'price_level', level_selector, old_level)
        unit_changed = old is not None and old.unit != profile.unit
        leaving_amount = old is not None and old.pricing_basis == 'amount'
        price_needs = leaving_amount or fields.needs('unit_price', item_changed or unit_changed or level_changed or header_price_changed)
        price_explicit = (price_override is not None or 'unit_price' in fields.supplied or
                          (old is not None and not leaving_amount and not fields.defaulted('unit_price') and 'unit_price' not in fields.defaults))
        if (level_changed or item_changed or refresh or 'price_level' in fields.defaults) and info['enable_price_levels'] and level_selector:
            # An explicit price makes a missing/inactive inherited level unused.
            # Explicitly selecting or refreshing the level still validates it.
            try:
                captured = None
                if not refresh and 'price_level' not in fields.supplied:
                    if fields.defaulted('price_level') and header.price_level and header.price_level.id == level_selector:
                        captured = header.price_level
                    elif not level_changed:
                        captured = old_level
                profile.price_rule = _price_rule(db, level_selector, item['id'], currency, captured=captured)
            except BookflowError as exc:
                if not (price_explicit and fields.defaulted('price_level') and not refresh
                        and exc.code == 'E_INACTIVE_REFERENCE'):
                    raise
                profile.price_rule = None
        elif level_selector is None or (not info['enable_price_levels'] and price_needs and not price_explicit):
            profile.price_rule = None
        if 'price_basis_amount' in fields.supplied:
            profile.price_basis_minor_units = money(inp.price_basis_amount, currency, 'price_basis_amount').minor_units
            fields.origins['price_basis_amount'] = Origin(kind='explicit')
            price_needs = price_needs or fields.defaulted('unit_price')
        elif old:
            profile.price_basis_minor_units = old.price_basis_minor_units
        if price_override is not None:
            profile.origins = fields.origins
            price = price_override(profile, factor)
        elif 'unit_price' in fields.supplied:
            price = money(inp.unit_price, currency).minor_units
            fields.origins['unit_price'] = Origin(kind='explicit')
        elif price_needs and not price_explicit:
            base_price = _derived_price(profile)
            price = selected_price(base_price, factor)
            fields.origins['unit_price'] = Origin(kind='default', source_id=profile.price_rule.id if profile.price_rule else item['id'])
            if base_price and not price:
                warnings.append(f'unit_price: nonzero base price {base_price} rounded to zero at factor {factor}')
        else:
            price = previous['unit_price_minor_units']
        if not info['enable_price_levels'] and price_needs:
            warnings.append('price_level: price levels disabled; standard/manual price is in use')

    profile.origins = fields.origins
    net = (profile.net_amount_minor_units if amount_mode else
           nonnegative(net_override, 'line.net') if net_override is not None else extension(quantity, price))
    exempt = header.customer_tax_code is not None and not header.customer_tax_code.taxable
    taxable = profile.tax_code is not None and profile.tax_code.taxable and not exempt
    if not header.preferences.sales_tax_enabled:
        if profile.tax_code and profile.tax_code.taxable and not fields.defaulted('tax_code'):
            raise _invalid('tax_code', 'explicit taxable treatment requires sales tax enabled')
        if fields.defaulted('tax_code'):
            profile.tax_code = None
        taxable = False
    taxes = []
    if taxable:
        if not header.tax_rules:
            raise _invalid('sales_tax_item', 'taxable treatment requires a valid captured tax item; select one or use_defaults')
        if not nonposting and header.preferences.sales_tax_liability_basis != 'invoice_date':
            raise _invalid('sales_tax_item', 'taxable sales require invoice_date liability policy')
        taxes = [dict(rule=rule, taxable_minor_units=net, tax_minor_units=0 if defer_tax else tax(net, rule.rate_percent_millionths))
                 for rule in header.tax_rules]
    tax_amount = total((component['tax_minor_units'] for component in taxes), 'line.tax')
    gross = total((net, tax_amount), 'line.gross')
    return dict(item_id=profile.item.id, quantity_microunits=quantity,
                unit_id=profile.unit.id if profile.unit else None, unit_factor_nanounits=factor,
                base_quantity_microunits=base_qty, unit_price_minor_units=price,
                net_minor_units=net, tax_minor_units=tax_amount, gross_minor_units=gross,
                description=description, profile=profile, taxes=taxes), warnings
