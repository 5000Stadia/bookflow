"""Exact amount pricing at the shared fact/default boundary; no sales persistence."""
import json

import pytest
from pydantic import ValidationError

from bookflow.company import schema
from bookflow.company.sales_defaults import resolve_line
from bookflow.company.sales_facts import SalesLineProfile
from bookflow.company.sales_models import SalesLineInput
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from tests.test_sales_defaults import header, line, sale, saved, update  # noqa: F401


@pytest.mark.parametrize('values', [
    {'net_amount': None}, {'net_amount': True}, {'net_amount': 1.01},
    {'net_amount': {'minor_units': True, 'currency': 'USD'}},
    {'net_amount': {'minor_units': -1, 'currency': 'USD'}},
    {'net_amount': {'minor_units': INT64_MAX + 1, 'currency': 'USD'}},
    {'net_amount': '1', 'unit_price': '1'},
    {'net_amount': '1', 'price_level': None},
    {'net_amount': '1', 'price_basis_amount': '1'},
    {'net_amount': '1', 'use_defaults': ['unit_price']},
])
def test_invalid_amount_inputs(values):
    with pytest.raises((ValidationError, BookflowError)):
        SalesLineInput(item='item', **values)


@pytest.mark.parametrize('amount', ['10.01', {'minor_units': 1001, 'currency': 'USD', 'amount': '10.01'}])
def test_exact_amount_and_ordinary_rate(sale, amount):
    h = header(sale)
    before = sale.company.raw.total_changes
    result = line(sale, h, quantity='2', net_amount=amount, tax_code=sale.taxable)
    assert (result['quantity_microunits'], result['unit_price_minor_units'], result['net_minor_units'],
            result['tax_minor_units'], result['gross_minor_units']) == (2_000_000, None, 1001, 80, 1081)
    profile = result['profile']
    assert profile.pricing_basis == 'amount' and profile.net_amount_minor_units == 1001
    assert profile.schema_version == 2 and profile.origins['net_amount'].kind == 'explicit'
    assert profile.price_rule is None and profile.price_basis_minor_units is None
    assert SalesLineProfile.model_validate_json(profile.model_dump_json()) == profile
    ordinary = line(sale, h, quantity='2', unit_price='5', tax_code=sale.taxable)
    assert (ordinary['net_minor_units'], ordinary['tax_minor_units'], ordinary['gross_minor_units']) == (1000, 80, 1080)
    assert sale.company.raw.total_changes == before


def test_amount_noop_quantity_edit_and_explicit_override_survive_refresh(sale):
    h = header(sale)
    original = line(sale, h, quantity='2', net_amount='10.01', tax_code=sale.taxable)
    sale.change(schema.items, sale.item, price_minor_units=99999, version=2, description='New description')
    same = resolve_line(sale, SalesLineInput(item=sale.item), h, previous=saved(original))[0]
    assert same == original
    edited = resolve_line(sale, SalesLineInput(item=sale.item, quantity='3'), h, previous=saved(original))[0]
    assert edited['quantity_microunits'] == 3_000_000
    assert edited['net_minor_units'] == 1001 and edited['unit_price_minor_units'] is None
    assert edited['profile'].model_dump_json() == original['profile'].model_dump_json()
    refreshed = resolve_line(sale, SalesLineInput(item=sale.item, refresh_defaults=True), h,
                             previous=saved(edited))[0]
    assert refreshed['profile'].item.version == 2
    assert refreshed['net_minor_units'] == 1001 and refreshed['unit_price_minor_units'] is None
    replaced = resolve_line(sale, SalesLineInput(item=sale.item, net_amount='7.77'), h,
                            previous=saved(refreshed))[0]
    assert replaced['net_minor_units'] == replaced['profile'].net_amount_minor_units == 777


@pytest.mark.parametrize('selection,price', [({'unit_price': '5'}, 500),
    ({'price_level': None}, 10000), ({'use_defaults': ['unit_price']}, 10000)])
def test_amount_to_unit_modes(sale, selection, price):
    h = header(sale)
    original = line(sale, h, quantity='2', net_amount='10.01')
    result = resolve_line(sale, SalesLineInput(item=sale.item, **selection), h, previous=saved(original))[0]
    assert result['unit_price_minor_units'] == price and result['net_minor_units'] == 2 * price
    profile = result['profile']
    assert profile.pricing_basis == 'unit' and profile.net_amount_minor_units is None
    assert profile.schema_version == 1 and 'net_amount' not in profile.origins
    assert 'pricing_basis' not in profile.model_dump() and 'net_amount_minor_units' not in profile.model_dump()


def test_price_level_switch_and_unit_to_amount_clear_old_basis(sale):
    level = sale.add(schema.price_levels, name='Amount switch level', kind='fixed_percent',
        percent_millionths=10_000_000, rounding_mode='nearest', rounding_increment_minor_units=1,
        rounding_increment_currency='USD', rounding_offset_minor_units=0, rounding_offset_currency='USD')
    h = header(sale)
    original = line(sale, h, quantity='2', net_amount='10.01')
    rated = resolve_line(sale, SalesLineInput(item=sale.item, price_level=level), h, previous=saved(original))[0]
    assert rated['unit_price_minor_units'] == 11000
    amount = resolve_line(sale, SalesLineInput(item=sale.item, net_amount='10.01'), h, previous=saved(rated))[0]
    assert amount['unit_price_minor_units'] is None and amount['profile'].price_rule is None
    assert not set(amount['profile'].origins) & {'unit_price', 'price_level', 'price_basis_amount'}
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        resolve_line(sale, SalesLineInput(item=sale.item, price_basis_amount='9'), h, previous=saved(amount))


def test_item_and_unit_changes_retain_amount_with_warning(sale):
    unit_set = sale.add(schema.units_of_measure, name='Amount units')
    each = sale.add(schema.unit_conversions, unit_of_measure_id=unit_set, position=0,
        name='Each', abbreviation='ea', abbreviation_key='ea', is_base=True, base_factor_nanounits=1_000_000_000)
    half = sale.add(schema.unit_conversions, unit_of_measure_id=unit_set, position=1,
        name='Half', abbreviation='hf', abbreviation_key='hf', is_base=False, base_factor_nanounits=500_000_000)
    sale.change(schema.items, sale.item, unit_of_measure_set_id=unit_set)
    h = header(sale)
    original = line(sale, h, quantity='2', unit=each, net_amount='10.01')
    changed, warnings = resolve_line(sale, SalesLineInput(item=sale.item, unit=half), h, previous=saved(original))
    assert changed['base_quantity_microunits'] == 1_000_000 and changed['net_minor_units'] == 1001
    assert any('net_amount: explicit amount retained' in w for w in warnings)
    other = sale.add(schema.items, name='Amount other item', type='service', sales_enabled=True,
                     income_account_id=sale.income)
    changed, warnings = resolve_line(sale, SalesLineInput(item=other, unit=None), h, previous=saved(changed))
    assert changed['item_id'] == other and changed['unit_price_minor_units'] is None
    assert changed['net_minor_units'] == 1001
    assert any('net_amount: explicit amount retained' in w for w in warnings)


def test_amount_requires_no_catalog_price_or_unused_rule(sale):
    level = sale.add(schema.price_levels, name='Unused amount level', kind='fixed_percent',
        percent_millionths=10_000_000, rounding_mode='nearest', rounding_increment_minor_units=1,
        rounding_increment_currency='USD', rounding_offset_minor_units=0, rounding_offset_currency='USD')
    h = header(sale, price_level=level)
    sale.change(schema.price_levels, level, active=False)
    sale.change(schema.items, sale.item, price_minor_units=None, price_currency=None)
    original = line(sale, h, net_amount='10.01')
    refreshed = resolve_line(sale, SalesLineInput(item=sale.item, refresh_defaults=True), h,
                             previous=saved(original))[0]
    assert refreshed['net_minor_units'] == 1001 and refreshed['profile'].price_rule is None


@pytest.mark.parametrize('amount,code', [('0.001', 'E_AMOUNT_PRECISION'), ('-1', 'E_VALIDATION'),
    ('1 EUR', 'E_VALIDATION'), ({'minor_units': 1, 'currency': 'EUR'}, 'E_VALIDATION')])
def test_amount_home_currency_and_precision(sale, amount, code):
    with pytest.raises(BookflowError, match=code):
        line(sale, header(sale), net_amount=amount)


def test_zero_maximum_and_tax_overflow(sale):
    h = header(sale)
    for amount in (0, INT64_MAX):
        result = line(sale, h, quantity='2', net_amount={'minor_units': amount, 'currency': 'USD'})
        assert result['net_minor_units'] == amount and result['unit_price_minor_units'] is None
    with pytest.raises(BookflowError, match='E_VALUE_RANGE'):
        line(sale, h, net_amount={'minor_units': INT64_MAX, 'currency': 'USD'}, tax_code=sale.taxable)


def test_amount_tax_components_round_independently_and_honor_exemption(sale):
    h = header(sale)
    half = h.tax_rules[0].model_copy(update={'rate_percent_millionths': 50_000_000})
    h = h.model_copy(update={'tax_rules': [half, half.model_copy(update={'id': 'second'})]})
    for amount, expected in [('0.01', [0, 0]), ('0.03', [2, 2])]:
        result = line(sale, h, quantity='2', net_amount=amount, tax_code=sale.taxable)
        assert [v['tax_minor_units'] for v in result['taxes']] == expected
    exempt = update(sale, h, customer_tax_code=sale.exempt)
    assert line(sale, exempt, net_amount='10.01', tax_code=sale.taxable)['taxes'] == []
    payment = h.model_copy(update={'preferences': h.preferences.model_copy(update={'sales_tax_liability_basis': 'payment_receipt'})})
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        line(sale, payment, net_amount='10.01', tax_code=sale.taxable)


LEGACY_PROFILE = ('{"schema_version":1,"item":{"id":"item","label":"Service","version":1},'
    '"item_type":"service","income_account":{"id":"income","name":"Income",'
    '"full_name":"Income","number":null,"type":"income","normal_balance":"credit"},'
    '"unit":null,"class_id":null,"tax_code":null,"price_rule":null,"standard_price_minor_units":500,'
    '"cost_minor_units":null,"price_basis_minor_units":null,"origins":{"unit_price":{"kind":"explicit","source_id":null}}}')


def test_legacy_profile_exact_bytes_and_serialization_filters():
    profile = SalesLineProfile.model_validate_json(LEGACY_PROFILE)
    assert profile.model_dump_json() == LEGACY_PROFILE
    assert profile.model_dump() == json.loads(LEGACY_PROFILE)
    assert profile.model_dump(include={'item', 'pricing_basis'}) == {'item': profile.item.model_dump()}
    amount = SalesLineProfile.model_validate({**json.loads(LEGACY_PROFILE), 'schema_version': 2,
                                             'pricing_basis': 'amount', 'net_amount_minor_units': 1001})
    assert amount.model_dump(include={'pricing_basis', 'net_amount_minor_units'}) == {
        'pricing_basis': 'amount', 'net_amount_minor_units': 1001}
    assert SalesLineProfile.model_validate_json(amount.model_dump_json()) == amount


@pytest.mark.parametrize('values', [
    {'pricing_basis': 'amount'}, {'pricing_basis': 'unit', 'net_amount_minor_units': 1},
    {'pricing_basis': 'amount', 'net_amount_minor_units': -1},
    {'pricing_basis': 'amount', 'net_amount_minor_units': True},
    {'pricing_basis': 'amount', 'net_amount_minor_units': INT64_MAX + 1},
    {'schema_version': 1, 'pricing_basis': 'amount', 'net_amount_minor_units': 1},
])
def test_profile_rejects_inconsistent_or_inexact_basis(values):
    base = json.loads(LEGACY_PROFILE)
    base.pop('schema_version')
    with pytest.raises(ValidationError):
        SalesLineProfile.model_validate({**base, **values})
