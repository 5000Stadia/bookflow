"""Commercial defaults witnesses on disposable company databases."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.sales_defaults import resolve_header, resolve_line
from bookflow.company.sales_models import (
    InvoicePostInput, InvoiceUpdateInput, SalesLineInput, SalesReceiptPostInput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database


@pytest.fixture
def sale(client):
    path = Path(client.company.show(company='Demo Plumbing Co')['path']) / 'company.db'
    with open_database(path, writable=True) as db:
        def add(table, **values):
            record_id = new_id()
            values = dict(values, id=record_id)
            if 'version' in table.c:
                values.update(version=1, created_at='2026-01-01T00:00:00Z', updated_at='2026-01-01T00:00:00Z',
                              created_by='fixture', updated_by='fixture', created_via='python', updated_via='python')
            if 'name' in values:
                values['name_key'] = values['name'].casefold()
                if 'full_name' in table.c:
                    values.setdefault('full_name', values['name'])
                    values.setdefault('full_name_key', values['full_name'].casefold())
                    values.setdefault('depth', 1)
                    parent_path = ''
                    if values.get('parent_id'):
                        parent_path = db.conn.execute(sa.select(table.c.path).where(table.c.id == values['parent_id'])).scalar_one().rstrip('/')
                    values.setdefault('path', parent_path + '/' + record_id + '/')
            db.conn.execute(table.insert().values(**values))
            return record_id
        def change(table, record_id=None, **values):
            query = table.update()
            if record_id:
                query = query.where(table.c.id == record_id)
            db.conn.execute(query.values(**values))
        income = add(schema.accounts, name='Resolver income', type='income', currency='USD')
        customer = add(schema.customers, name='Resolver customer', billing_line1='Saved street',
                       notes='PRIVATE NOTES', payment_last4='1234')
        item = add(schema.items, name='Resolver service', type='service', sales_enabled=True,
                   income_account_id=income, description='Saved service', price_minor_units=10000, price_currency='USD')
        taxable = add(schema.sales_tax_codes, code='ZZT', code_key='zzt', taxable=True)
        exempt = add(schema.sales_tax_codes, code='ZZN', code_key='zzn', taxable=False)
        agency = add(schema.vendors, name='Resolver tax agency', is_tax_agency=True)
        liability = db.conn.execute(sa.select(schema.accounts.c.id).where(
            schema.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        tax_item = add(schema.items, name='Resolver 8 percent', type='sales_tax_item',
                       tax_percent_millionths=8_000_000, tax_agency_vendor_id=agency, liability_account_id=liability)
        change(schema.company_info, sales_tax_enabled=True, sales_tax_liability_basis='invoice_date',
               default_sales_tax_item_id=tax_item, use_classes=True, enable_price_levels=True,
               units_of_measure_mode='multiple_related_units')
        yield SimpleNamespace(company=db, add=add, change=change, customer=customer, item=item,
                              income=income, taxable=taxable, exempt=exempt, agency=agency, tax_item=tax_item)


def header(s, **kwargs):
    return resolve_header(s, InvoicePostInput(date='2026-01-31', customer=s.customer,
                          lines=[{'item': s.item}], **kwargs), 'invoice')[0]


def line(s, h, **kwargs):
    return resolve_line(s, SalesLineInput(item=s.item, **kwargs), h)[0]


def saved(line):
    return {**{k: v for k, v in line.items() if k not in ('profile', 'taxes')},
            'item_snapshot': line['profile'].model_dump_json()}


def update(s, h, **kwargs):
    return resolve_header(s, InvoiceUpdateInput(invoice='fixture', **kwargs), 'invoice',
                          previous=h, old_date='2026-01-31')[0]


def test_public_inherited_party_and_shipping_ownership(sale):
    s = sale
    address = s.add(schema.customer_addresses, customer_id=s.customer, position=0,
                    label='Site', label_key='site', is_default=True, address_line1='Site street')
    job = s.add(schema.customers, name='Resolver job', parent_id=s.customer,
                full_name='Resolver customer:Resolver job', depth=2, address_mode='inherit', contact_mode='inherit')
    h = resolve_header(s, InvoicePostInput(date='2026-01-31', customer=job,
                       lines=[{'item': s.item}]), 'invoice')[0]
    assert h.billing_address.line1 == 'Saved street'
    assert h.shipping_address.line1 == 'Site street'
    assert h.origins['billing_address'].source_id == s.customer
    assert 'PRIVATE NOTES' not in h.model_dump_json() and '1234' not in h.model_dump_json()
    chosen = header(s, shipping_address_id=address)
    other = s.add(schema.customers, name='Another customer')
    with pytest.raises(BookflowError) as caught:
        update(s, chosen, customer=other)
    assert caught.value.details['fields'][0]['field'] == 'shipping_address_id'
    manual = update(s, chosen, customer=other, shipping_address={'line1': 'Manual'})
    assert manual.shipping_address_id is None and manual.shipping_address.line1 == 'Manual'
    default = update(s, chosen, customer=other, use_defaults=['shipping_address'])
    assert default.shipping_address_id is None and default.shipping_address is None


def test_snapshot_noop_quantity_and_origin_only_override(sale):
    s = sale
    h = header(s)
    original = line(s, h, quantity='2.5', tax_code=s.taxable)
    s.change(schema.items, s.item, description='Today', price_minor_units=90000, active=False, type='inventory_part')
    s.change(schema.customers, s.customer, active=False, billing_line1='Today')
    s.change(schema.accounts, s.income, active=False)
    s.change(schema.company_info, use_classes=False, enable_price_levels=False, sales_tax_enabled=False)
    h2 = update(s, h, memo='unrelated')
    assert h2 == h
    same = resolve_line(s, SalesLineInput(item=s.item), h2, previous=saved(original), previous_header=h)[0]
    assert same == original
    equal_price = resolve_line(s, SalesLineInput(item=s.item, unit_price='100'), h2,
                               previous=saved(original), previous_header=h)[0]
    assert equal_price['quantity_microunits'] == 2_500_000
    assert equal_price['net_minor_units'] == 25000
    assert equal_price['profile'].origins['unit_price'].kind == 'explicit'
    assert equal_price['profile'] != original['profile']
    changed = resolve_line(s, SalesLineInput(item=s.item, quantity='3'), h2, previous=saved(original), previous_header=h)[0]
    assert changed['gross_minor_units'] == 32400
    with pytest.raises(BookflowError, match='E_INACTIVE_REFERENCE'):
        resolve_line(s, SalesLineInput(item=s.item), h, previous=saved(original), refresh=True)


def test_terms_captured_rule_and_explicit_due_date(sale):
    s = sale
    term = s.add(schema.terms, name='Resolver terms', kind='date_driven', due_day_of_month=31,
                 due_next_month_if_within_days=0, discount_day_of_month=10, discount_percent_millionths=2_000_000)
    h = header(s, terms=term)
    assert h.due_date == '2026-02-28' and h.discount_date == '2026-02-10' and h.discount_available
    s.change(schema.terms, term, due_day_of_month=5, active=False)
    edited = update(s, h, date='2026-02-01')
    assert edited.due_date == '2026-02-28' and edited.terms == h.terms
    override = update(s, h, due_date='2026-02-05')
    assert override.discount_date == '2026-02-10' and not override.discount_available
    with pytest.raises(BookflowError):
        update(s, override, date='2026-02-06')
    with pytest.raises(BookflowError):
        update(s, h, refresh_defaults=True)


def test_refresh_preserves_explicit_leaves_and_reloads_selected_reference(sale):
    s = sale
    h = header(s, billing_address={'line1': 'Manual'}, customer_message='Manual text')
    original = line(s, h, description='Manual line', unit_price='77', class_id=None, tax_code=s.exempt)
    s.change(schema.items, s.item, name='Renamed service', full_name='Renamed service', version=2,
             description='New default', price_minor_units=20000)
    s.change(schema.customers, s.customer, name='Renamed customer', full_name='Renamed customer', version=2,
             billing_line1='New customer address')
    new_h = update(s, h, refresh_defaults=True)
    assert new_h.customer.label == 'Renamed customer' and new_h.billing_address == h.billing_address
    assert new_h.customer_message == 'Manual text'
    new_line = resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(original),
                            previous_header=h, refresh=True)[0]
    assert new_line['profile'].item.version == 2 and new_line['unit_price_minor_units'] == 7700
    assert new_line['description'] == 'Manual line' and new_line['profile'].class_id is None
    assert new_line['profile'].tax_code.id == s.exempt


def test_tax_group_order_component_rounding_exemption_and_captured_policy(sale):
    s = sale
    half = s.add(schema.items, name='Resolver half tax', type='sales_tax_item', tax_percent_millionths=50_000_000,
                 tax_agency_vendor_id=s.agency,
                 liability_account_id=s.company.conn.execute(sa.select(schema.accounts.c.id).where(
                     schema.accounts.c.system_role == 'sales_tax_payable')).scalar_one())
    group = s.add(schema.items, name='Resolver group', type='sales_tax_group')
    s.add(schema.item_members, owner_item_id=group, component_item_id=half, quantity_microunits=1_000_000, position=0)
    s.add(schema.item_members, owner_item_id=group, component_item_id=s.tax_item, quantity_microunits=1_000_000, position=1)
    h = header(s, sales_tax_item=group)
    original = line(s, h, unit_price='0.01', tax_code=s.taxable)
    assert [t['rule'].id for t in original['taxes']] == [half, s.tax_item]
    assert [t['tax_minor_units'] for t in original['taxes']] == [0, 0]
    assert original['gross_minor_units'] == 1
    s.change(schema.company_info, sales_tax_liability_basis='payment_receipt')
    s.change(schema.items, half, active=False)
    s.change(schema.vendors, s.agency, active=False)
    ordinary = update(s, h)
    changed = resolve_line(s, SalesLineInput(item=s.item, quantity='3'), ordinary,
                           previous=saved(original), previous_header=h)[0]
    assert changed['tax_minor_units'] == 2
    exempt = update(s, h, customer_tax_code=s.exempt)
    assert resolve_line(s, SalesLineInput(item=s.item), exempt, previous=saved(original), previous_header=h)[0]['taxes'] == []
    with pytest.raises(BookflowError):
        update(s, h, refresh_defaults=True)


def test_exempt_without_rules_cannot_silently_acquire_today_tax(sale):
    s = sale
    s.change(schema.items, s.tax_item, active=False)
    h = header(s, customer_tax_code=s.exempt)
    assert h.tax_rules is None
    original = line(s, h, tax_code=s.taxable)
    s.change(schema.items, s.tax_item, active=True)
    taxable = update(s, h, customer_tax_code=s.taxable)
    with pytest.raises(BookflowError):
        resolve_line(s, SalesLineInput(item=s.item), taxable, previous=saved(original), previous_header=h)
    resolved = update(s, h, customer_tax_code=s.taxable, use_defaults=['sales_tax_item'])
    assert resolve_line(s, SalesLineInput(item=s.item), resolved, previous=saved(original), previous_header=h)[0]['tax_minor_units'] == 800


def test_unit_factor_price_rounding_and_historical_preference(sale):
    s = sale
    unit_set = s.add(schema.units_of_measure, name='Resolver units')
    base = s.add(schema.unit_conversions, unit_of_measure_id=unit_set, position=0,
                 name='Each', abbreviation='ea', abbreviation_key='ea', is_base=True, base_factor_nanounits=1_000_000_000)
    half = s.add(schema.unit_conversions, unit_of_measure_id=unit_set, position=1,
                 name='Half', abbreviation='hf', abbreviation_key='hf', is_base=False, base_factor_nanounits=500_000_000)
    s.change(schema.units_of_measure, unit_set, default_sales_unit_id=half, version=7)
    s.change(schema.items, s.item, unit_of_measure_set_id=unit_set, price_minor_units=1)
    h = header(s)
    result, warnings = resolve_line(s, SalesLineInput(item=s.item, quantity='3'), h)
    assert result['base_quantity_microunits'] == 1_500_000 and result['unit_price_minor_units'] == 0
    assert result['profile'].unit.version == 7 and any('rounded to zero' in w for w in warnings)
    s.change(schema.company_info, units_of_measure_mode='disabled')
    retained = resolve_line(s, SalesLineInput(item=s.item, quantity='4'), h, previous=saved(result), previous_header=h)[0]
    assert retained['base_quantity_microunits'] == 2_000_000 and retained['unit_id'] == half
    with pytest.raises(BookflowError):
        resolve_line(s, SalesLineInput(item=s.item, unit=base), h, previous=saved(result))
    refreshed = resolve_line(s, SalesLineInput(item=s.item), h, previous=saved(result), refresh=True)[0]
    assert refreshed['unit_id'] is None and refreshed['unit_price_minor_units'] == 1


@pytest.mark.parametrize('basis,price,cost,custom,expected', [
    ('standard_price', 100, None, None, 110), ('cost', None, 200, None, 220),
    ('current_custom_price', None, None, '3.00', 330),
])
def test_pricing_actual_base_and_noncompounding_refresh(sale, basis, price, cost, custom, expected):
    s = sale
    level = s.add(schema.price_levels, name='Resolver pricing', kind='per_item', rounding_mode='nearest',
                  rounding_increment_minor_units=1, rounding_increment_currency='USD',
                  rounding_offset_minor_units=0, rounding_offset_currency='USD')
    s.add(schema.price_level_items, price_level_id=level, position=0, item_id=s.item,
          percent_millionths=10_000_000, adjustment_basis=basis)
    s.change(schema.items, s.item, price_minor_units=price, price_currency='USD' if price is not None else None,
             cost_minor_units=cost, cost_currency='USD' if cost is not None else None)
    h = header(s, price_level=level)
    kwargs = {'price_basis_amount': custom} if custom is not None else {}
    original = line(s, h, **kwargs)
    assert original['unit_price_minor_units'] == expected
    second = resolve_line(s, SalesLineInput(item=s.item), h, previous=saved(original), refresh=True)[0]
    assert second['unit_price_minor_units'] == expected


def test_fixed_price_needs_no_standard_and_unused_level_does_not_block(sale):
    s = sale
    level = s.add(schema.price_levels, name='Resolver fixed', kind='per_item', rounding_mode='nearest',
                  rounding_increment_minor_units=1, rounding_increment_currency='USD',
                  rounding_offset_minor_units=0, rounding_offset_currency='USD')
    s.add(schema.price_level_items, price_level_id=level, position=0, item_id=s.item,
          price_minor_units=42, price_currency='USD', adjustment_basis='standard_price')
    s.change(schema.items, s.item, price_minor_units=None, price_currency=None)
    h = header(s, price_level=level)
    assert line(s, h)['unit_price_minor_units'] == 42
    s.change(schema.price_levels, level, active=False)
    assert line(s, h, unit_price='7')['unit_price_minor_units'] == 700
    s.change(schema.company_info, enable_price_levels=False)
    s.change(schema.customers, s.customer, price_level_id=level)
    h2 = header(s)
    assert h2.price_level.id == level
    assert line(s, h2, unit_price='8')['unit_price_minor_units'] == 800


def test_receipt_default_method_and_terms_are_not_used(sale):
    s = sale
    method = s.add(schema.payment_methods, name='Resolver check', kind='check')
    term = s.add(schema.terms, name='Inactive irrelevant terms', kind='standard', due_days=30, active=False)
    s.change(schema.customers, s.customer, preferred_payment_method_id=method, terms_id=term)
    h = resolve_header(s, SalesReceiptPostInput(date='2026-01-31', customer=s.customer,
                       deposit_to='Undeposited Funds', lines=[{'item': s.item}]), 'sales_receipt')[0]
    assert h.payment_method.id == method and h.terms is None and h.due_date is None
    with pytest.raises(BookflowError):
        resolve_header(s, SalesReceiptPostInput(date='2026-01-31', customer=s.customer,
                       deposit_to='Undeposited Funds', payment_method=None, lines=[{'item': s.item}]), 'sales_receipt')


def test_class_header_dependency_preserves_item_and_explicit_origins(sale):
    s = sale
    first = s.add(schema.classes, name='Resolver class one')
    second = s.add(schema.classes, name='Resolver class two')
    h = header(s, class_id=first)
    inherited = line(s, h)
    explicit = line(s, h, class_id=None)
    new_h = update(s, h, class_id=second)
    assert resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(inherited), previous_header=h)[0]['profile'].class_id.id == second
    assert resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(explicit), previous_header=h)[0]['profile'].class_id is None
    s.change(schema.items, s.item, default_class_id=first)
    item_default = line(s, h)
    assert resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(item_default), previous_header=h)[0]['profile'].class_id.id == first


def test_no_write_and_validation_bounds(sale):
    s = sale
    h = header(s)
    before = s.company.raw.total_changes
    line(s, h)
    header(s)
    assert s.company.raw.total_changes == before
    with pytest.raises(BookflowError):
        line(s, h, quantity='9223372036854.775807', unit_price='92233720368547758.07')
    s.change(schema.items, s.item, type='other_charge', other_charge_percent_millionths=1)
    with pytest.raises(BookflowError):
        line(s, h)


def test_orphan_custom_slots_do_not_enter_party_capture(sale, monkeypatch):
    from bookflow.company import custom_fields
    def forbidden(*args, **kwargs):
        raise AssertionError('commercial resolution inspected unrelated custom slots')
    monkeypatch.setattr(custom_fields, 'read_owner_values', forbidden)
    assert header(sale).customer.id == sale.customer


def test_header_equal_override_changes_origin_and_refresh_restores_only_defaults(sale):
    s = sale
    h = header(s)
    explicit = update(s, h, billing_address=h.billing_address.model_dump())
    assert explicit.billing_address == h.billing_address
    assert explicit.origins['billing_address'].kind == 'explicit'
    s.change(schema.customers, s.customer, billing_line1='Today')
    assert update(s, explicit, refresh_defaults=True).billing_address == h.billing_address
    restored = update(s, explicit, use_defaults=['billing_address'])
    assert restored.billing_address.line1 == 'Today'
    assert restored.origins['billing_address'].kind == 'default'


def test_new_header_selection_checks_current_class_preference(sale):
    s = sale
    first = s.add(schema.classes, name='Existing class')
    second = s.add(schema.classes, name='Another class')
    h = header(s, class_id=first)
    s.change(schema.company_info, use_classes=False)
    assert update(s, h).class_id.id == first
    with pytest.raises(BookflowError):
        update(s, h, class_id=second)
    assert update(s, h, class_id=None).class_id is None


def test_disabled_tax_ignores_unused_inactive_default_code(sale):
    s = sale
    s.change(schema.company_info, sales_tax_enabled=False)
    s.change(schema.sales_tax_codes, s.taxable, active=False)
    s.change(schema.customers, s.customer, sales_tax_code_id=s.taxable)
    h = header(s)
    assert h.customer_tax_code is None and h.tax_rules is None
    s.change(schema.items, s.item, sales_tax_code_id=s.taxable)
    assert line(s, h)['tax_minor_units'] == 0


def test_new_header_unused_inactive_pricing_and_explicit_selection(sale):
    s = sale
    level = s.add(schema.price_levels, name='Unused inactive pricing', kind='fixed_percent', percent_millionths=1,
                  active=False, rounding_mode='nearest', rounding_increment_minor_units=1,
                  rounding_increment_currency='USD', rounding_offset_minor_units=0, rounding_offset_currency='USD')
    s.change(schema.customers, s.customer, price_level_id=level)
    inp = InvoicePostInput(date='2026-01-31', customer=s.customer, lines=[{'item': s.item, 'unit_price': '7'}])
    h = resolve_header(s, inp, 'invoice')[0]
    assert resolve_line(s, inp.lines[0], h)[0]['unit_price_minor_units'] == 700
    with pytest.raises(BookflowError) as caught:
        header(s)
    assert caught.value.code == 'E_INACTIVE_REFERENCE' and caught.value.details['source_id'] == s.customer
    with pytest.raises(BookflowError):
        resolve_header(s, inp.model_copy(update={'price_level': level}), 'invoice')


def test_tax_refresh_disabled_requires_clearing_explicit_treatment(sale):
    s = sale
    h = header(s, customer_tax_code=s.taxable)
    original = line(s, h, tax_code=s.taxable)
    s.change(schema.company_info, sales_tax_enabled=False)
    with pytest.raises(BookflowError):
        update(s, h, refresh_defaults=True)
    refreshed = update(s, h, customer_tax_code=None, refresh_defaults=True)
    with pytest.raises(BookflowError):
        resolve_line(s, SalesLineInput(item=s.item), refreshed, previous=saved(original), refresh=True)
    cleared = resolve_line(s, SalesLineInput(item=s.item, tax_code=None), refreshed,
                           previous=saved(original), refresh=True)[0]
    assert cleared['tax_minor_units'] == 0


def test_header_dependency_does_not_load_new_item_default(sale):
    s = sale
    first = s.add(schema.classes, name='Old header class')
    second = s.add(schema.classes, name='New header class')
    third = s.add(schema.classes, name='New item assignment')
    h = header(s, class_id=first)
    original = line(s, h)
    s.change(schema.items, s.item, default_class_id=third)
    new_h = update(s, h, class_id=second)
    result = resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(original), previous_header=h)[0]
    assert result['profile'].class_id.id == second
    refreshed = resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(result), refresh=True)[0]
    assert refreshed['profile'].class_id.id == third


def test_message_refresh_and_simultaneous_manual_override(sale):
    s = sale
    message = s.add(schema.customer_messages, name='Resolver message', text='Saved message')
    h = header(s, customer_message_item=message)
    s.change(schema.customer_messages, message, text='Today message', version=2)
    assert update(s, h).customer_message == 'Saved message'
    assert update(s, h, refresh_defaults=True).customer_message == 'Today message'
    manual = update(s, h, customer_message='Manual', refresh_defaults=True)
    assert manual.customer_message == 'Manual' and manual.customer_message_item is None


def test_term_year_overflow_is_a_domain_error(sale):
    s = sale
    term = s.add(schema.terms, name='Beyond supported year', kind='standard', due_days=30)
    with pytest.raises(BookflowError) as caught:
        resolve_header(s, InvoicePostInput(date='9999-12-31', customer=s.customer,
                       terms=term, lines=[{'item': s.item}]), 'invoice')
    assert caught.value.code == 'E_VALIDATION'


@pytest.mark.parametrize('mode,expected', [('nearest', 9), ('up', 14), ('down', 9)])
def test_price_rounding_offset_and_fixed_percent(sale, mode, expected):
    s = sale
    level = s.add(schema.price_levels, name='Offset rule', kind='fixed_percent', percent_millionths=5_000_000,
                  rounding_mode=mode, rounding_increment_minor_units=5, rounding_increment_currency='USD',
                  rounding_offset_minor_units=-1, rounding_offset_currency='USD')
    s.change(schema.items, s.item, price_minor_units=10)
    assert line(s, header(s, price_level=level))['unit_price_minor_units'] == expected


def test_price_level_header_change_does_not_reprice_explicit_line_level(sale):
    s = sale
    levels = [s.add(schema.price_levels, name=name, kind='fixed_percent', percent_millionths=10_000_000,
                    rounding_mode='nearest', rounding_increment_minor_units=1, rounding_increment_currency='USD',
                    rounding_offset_minor_units=0, rounding_offset_currency='USD') for name in ('Header level', 'Line level')]
    h = header(s)
    original = line(s, h, price_level=levels[1])
    new_h = update(s, h, price_level=levels[0])
    s.change(schema.company_info, enable_price_levels=False)
    unchanged = resolve_line(s, SalesLineInput(item=s.item), new_h,
                             previous=saved(original), previous_header=h)[0]
    assert unchanged == original


def test_new_tax_reference_validates_agency_and_liability_role(sale):
    s = sale
    s.change(schema.vendors, s.agency, is_tax_agency=False)
    with pytest.raises(BookflowError) as caught:
        header(s)
    assert caught.value.details['fields'][0]['field'] == 'sales_tax_item'
    s.change(schema.vendors, s.agency, is_tax_agency=True)
    s.change(schema.items, s.tax_item, liability_account_id=s.income)
    with pytest.raises(BookflowError):
        header(s)


def test_no_matching_price_row_falls_back_to_standard_and_missing_custom_base_fails(sale):
    s = sale
    level = s.add(schema.price_levels, name='Empty rule', kind='per_item', rounding_mode='nearest',
                  rounding_increment_minor_units=5, rounding_increment_currency='USD',
                  rounding_offset_minor_units=-1, rounding_offset_currency='USD')
    h = header(s, price_level=level)
    assert line(s, h)['unit_price_minor_units'] == 10000
    s.add(schema.price_level_items, price_level_id=level, position=0, item_id=s.item,
          percent_millionths=10_000_000, adjustment_basis='current_custom_price')
    with pytest.raises(BookflowError):
        line(s, h)
    assert line(s, h, unit_price='2')['unit_price_minor_units'] == 200


def test_customer_change_updates_line_default_sources_even_with_equal_values(sale):
    s = sale
    common = s.add(schema.classes, name='Shared customer class')
    s.change(schema.customers, s.customer, default_class_id=common, sales_tax_code_id=s.taxable)
    other = s.add(schema.customers, name='Same defaults customer', default_class_id=common, sales_tax_code_id=s.taxable)
    h = header(s)
    original = line(s, h)
    new_h = update(s, h, customer=other)
    result = resolve_line(s, SalesLineInput(item=s.item), new_h, previous=saved(original), previous_header=h)[0]
    assert result['gross_minor_units'] == original['gross_minor_units']
    assert result['profile'].class_id == original['profile'].class_id
    for field in ('class_id', 'tax_code', 'price_level'):
        assert result['profile'].origins[field].source_id == other
        assert original['profile'].origins[field].source_id == s.customer
