"""Exact work facts witnesses using public catalog setup in disposable roots."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from bookflow.company import sales_defaults
from bookflow.company.sales_facts import SalesProfile
from bookflow.company.sales_models import InvoicePostInput
from bookflow.company.work_defaults import resolve_header, resolve_line
from bookflow.company.work_facts import WorkFacts, WorkLineFacts
from bookflow.company.work_models import (
    EstimateCopyInput, EstimateCreateInput, EstimateUpdateInput, EstimateWorkOrderInput,
    ProposalCreateInput, ProposalEstimateInput, ProposalUpdateInput, WorkLineInput,
    WorkOrderCompleteInput, WorkOrderCreateInput, WorkOrderUpdateInput, WorkQueryInput,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX
from bookflow.storage.engine import open_database

COMPANY = 'Demo Plumbing Co'


@pytest.fixture
def catalog(client):
    income = client.account.create(name='Work income', type='income', company=COMPANY)['id']
    customer = client.customer.create(name='Work customer', billing_address={'line1': 'Captured street'}, company=COMPANY)['id']
    agency = client.vendor.create(name='Work agency', is_tax_agency=True, company=COMPANY)['id']
    accounts = client.account.list(company=COMPANY)['items']
    liability = next(value['id'] for value in accounts if value.get('system_role') == 'sales_tax_payable')
    tax_item = client.run('item create', dict(name='Work eight percent', type='sales_tax_item',
        tax_percent='8', tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id']
    codes = client.run('sales-tax-code list', {}, company=COMPANY)['items']
    taxable = next(value['id'] for value in codes if value['taxable'])
    exempt = next(value['id'] for value in codes if not value['taxable'])
    client.company.update(sales_tax_enabled=True, sales_tax_liability_basis='payment_receipt',
        default_sales_tax_item_id=tax_item, enable_price_levels=True, company=COMPANY)
    item = client.run('item create', dict(name='Work service', type='service', sales_enabled=True, description='Work labor',
        income_account_id=income, price='6.00', cost='4.00', sales_tax_code_id=taxable), company=COMPANY)['id']
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    return SimpleNamespace(client=client, customer=customer, item=item, income=income,
                           tax_item=tax_item, taxable=taxable, exempt=exempt, path=path)


def quote(catalog, *, kind='estimate', header=None, previous=None, **values):
    with open_database(catalog.path, writable=False) as db:
        session = SimpleNamespace(company=db)
        if header is None:
            inp = EstimateCreateInput(date='2026-01-01', customer=catalog.customer, title='Quote',
                                      lines=[{'item': catalog.item}])
            header = resolve_header(session, inp, kind)[0]
        before = db.raw.total_changes
        line, warnings = resolve_line(session, WorkLineInput(item=catalog.item, **values), header,
                                      previous=previous, previous_header=header, kind=kind)
        assert db.raw.total_changes == before
        return header, line, warnings


def item_update(catalog, **values):
    current = catalog.client.run('item show', {'item': catalog.item}, company=COMPANY)
    return catalog.client.run('item update', dict(item=catalog.item, expected_version=current['version'], **values), company=COMPANY)


@pytest.mark.parametrize('values,rate,net,tax,cost', [
    ({'quantity': '2', 'markup_percent': '25'}, 500, 1000, 80, 800),
    ({'quantity': '2', 'net_amount': '10.01'}, None, 1001, 80, 800),
    ({'quantity': '2', 'unit_price': '5.00'}, 500, 1000, 80, 800),
    ({'quantity': '2'}, 600, 1200, 96, 800),
    ({'quantity': '0.5', 'unit_price': '0.01'}, 1, 0, 0, 200),
    ({'quantity': '1.5', 'unit_price': '0.01'}, 1, 2, 0, 600),
])
def test_exact_prices_and_nonposting_payment_policy(catalog, values, rate, net, tax, cost):
    h, line, _ = quote(catalog, **values)
    assert (line.unit_price_minor_units, line.net_minor_units, line.tax_minor_units,
            line.gross_minor_units, line.estimated_cost_minor_units) == (rate, net, tax, net + tax, cost)
    assert h.preferences.sales_tax_liability_basis == 'payment_receipt'
    assert not set(h.model_dump()) & {'control_account', 'due_date', 'payment_method', 'payment_reference'}
    assert WorkLineFacts.model_validate_json(line.model_dump_json()) == line
    assert WorkFacts.model_validate_json(WorkFacts(profile=h, issuer_snapshot={}).model_dump_json()).profile == h
    with open_database(catalog.path, writable=False) as db:
        s = SimpleNamespace(company=db)
        inp = InvoicePostInput(date='2026-01-01', customer=catalog.customer, lines=[{'item': catalog.item}])
        actual = sales_defaults.resolve_header(s, inp, 'invoice')[0]
        with pytest.raises(BookflowError, match='E_VALIDATION'):
            sales_defaults.resolve_line(s, inp.lines[0], actual)


@pytest.mark.parametrize('old_mode', ['catalog', 'manual', 'markup', 'amount'])
@pytest.mark.parametrize('new_mode', ['catalog', 'manual', 'markup', 'amount'])
def test_every_price_switch_retains_cost_and_clears_mode_origins(catalog, old_mode, new_mode):
    inputs = {'catalog': {}, 'manual': {'unit_price': '7.00'},
              'markup': {'markup_percent': '25'}, 'amount': {'net_amount': '10.01'}}
    h, old, _ = quote(catalog, quantity='2', estimated_unit_cost='4.00', **inputs[old_mode])
    switch = inputs[new_mode] if new_mode != 'catalog' else {'use_defaults': ['unit_price']}
    _, new, _ = quote(catalog, header=h, previous=old, **switch)
    assert new.pricing_basis == new_mode
    assert new.estimated_unit_cost_minor_units == 400 and new.estimated_cost_origin.kind == 'explicit'
    assert new.net_minor_units == {'catalog': 1200, 'manual': 1400, 'markup': 1000, 'amount': 1001}[new_mode]
    assert set(new.profile.origins) & {'markup_percent', 'net_amount'} == (
        {'markup_percent'} if new_mode == 'markup' else {'net_amount'} if new_mode == 'amount' else set())
    assert new.markup_percent_millionths == (25_000_000 if new_mode == 'markup' else None)
    _, quantity, _ = quote(catalog, header=h, previous=new, quantity='3')
    assert quantity.net_minor_units == (1001 if new_mode == 'amount' else new.unit_price_minor_units * 3)


def test_unknown_zero_clear_cost_and_markup_updates(catalog):
    h, unknown, _ = quote(catalog, estimated_unit_cost=None, net_amount='10.01')
    assert unknown.estimated_unit_cost_minor_units is None and unknown.estimated_cost_minor_units is None
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        quote(catalog, header=h, previous=unknown, markup_percent='25')
    _, zero, _ = quote(catalog, header=h, previous=unknown, estimated_unit_cost='0', markup_percent='25')
    assert zero.unit_price_minor_units == zero.estimated_cost_minor_units == zero.net_minor_units == 0
    _, changed, _ = quote(catalog, header=h, previous=zero, estimated_unit_cost='4.00')
    assert changed.unit_price_minor_units == 500
    _, reset, _ = quote(catalog, header=h, previous=unknown, use_defaults=['estimated_unit_cost'])
    assert reset.estimated_unit_cost_minor_units == 400 and reset.estimated_cost_origin.kind == 'default'


def test_master_edits_retention_refresh_and_inactive(catalog):
    h, original, _ = quote(catalog, quantity='2', markup_percent='25')
    item_update(catalog, cost='8.00', price='9.00')
    _, kept, _ = quote(catalog, header=h, previous=original)
    assert kept == original
    _, refreshed, _ = quote(catalog, header=h, previous=original, refresh_defaults=True)
    assert refreshed.estimated_unit_cost_minor_units == 800 and refreshed.unit_price_minor_units == 1000
    _, explicit, _ = quote(catalog, header=h, previous=original, estimated_unit_cost='3.00')
    _, retained, _ = quote(catalog, header=h, previous=explicit, refresh_defaults=True)
    assert retained.estimated_unit_cost_minor_units == 300 and retained.unit_price_minor_units == 375
    catalog.client.run('item deactivate', {'item': catalog.item}, company=COMPANY)
    _, inactive, _ = quote(catalog, header=h, previous=original, quantity='3')
    assert inactive.unit_price_minor_units == 500
    with pytest.raises(BookflowError, match='E_INACTIVE_REFERENCE'):
        quote(catalog, header=h, previous=original, refresh_defaults=True)


def test_selected_unit_cost_half_even_and_explicit_retention(catalog):
    catalog.client.company.update(units_of_measure_mode='multiple_related_units', company=COMPANY)
    units = catalog.client.run('unit-of-measure create', dict(name='Work units', units=[
        dict(name='Each', abbreviation='ea', is_base=True, base_factor='1'),
        dict(name='Half', abbreviation='hf', base_factor='0.5'),
    ]), company=COMPANY)
    item_update(catalog, unit_of_measure_set_id=units['id'], cost='0.05')
    h, half, _ = quote(catalog, unit='hf', quantity='2', markup_percent='0')
    assert half.estimated_unit_cost_minor_units == 2 and half.estimated_cost_minor_units == 4
    _, explicit, _ = quote(catalog, header=h, previous=half, estimated_unit_cost='0.07')
    _, each, warnings = quote(catalog, header=h, previous=explicit, unit='ea')
    assert each.estimated_unit_cost_minor_units == 7 and each.unit_price_minor_units == 7
    assert any('explicit cost retained per selected unit' in value for value in warnings)


def test_completed_quantities_work_order_only_and_exact_bounds(catalog):
    h, old, _ = quote(catalog, kind='work_order', quantity='2', completed_quantity='1')
    assert old.completed_quantity_microunits == 1_000_000
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        quote(catalog, kind='work_order', header=h, previous=old, quantity='0.5')
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        quote(catalog, completed_quantity='0')
    with pytest.raises(BookflowError, match='E_VALUE_RANGE'):
        quote(catalog, quantity='2', unit_price={'minor_units': INT64_MAX, 'currency': 'USD'})
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        quote(catalog, unit_price={'minor_units': 1, 'currency': 'EUR'})


@pytest.mark.parametrize('values', [
    {'unit_price': None}, {'markup_percent': None}, {'net_amount': None},
    {'unit_price': '1', 'net_amount': '1'}, {'markup_percent': '0', 'unit_price': '1'},
    {'markup_percent': '0', 'net_amount': '1'}, {'net_amount': '1', 'price_level': None},
    {'markup_percent': '0', 'use_defaults': ['unit_price']},
    {'markup_percent': '-100.000001'}, {'markup_percent': '1000000.000001'},
    {'markup_percent': 25}, {'quantity': 2}, {'completed_quantity': '-1'},
    {'quantity': '1', 'completed_quantity': '2'}, {'billable': 1},
    {'estimated_unit_cost': {'minor_units': True, 'currency': 'USD'}},
])
def test_invalid_line_inputs(values):
    with pytest.raises((ValidationError, BookflowError)):
        WorkLineInput(item='item', **values)


def test_kind_inputs_versions_nulls_and_canonical_copy_ids():
    base = dict(date='2026-01-01', customer='Customer', title='Scope')
    assert ProposalCreateInput(**base).lines == []
    with pytest.raises(ValidationError):
        EstimateCreateInput(**base, lines=[])
    for model in (ProposalCreateInput, EstimateCreateInput):
        with pytest.raises(ValidationError):
            model(**base, lines=[dict(item='Item', completed_quantity='0')])
    for patch in ({'ar_account': 'AR'}, {'deposit_to': 'Bank'}, {'due_date': '2026-02-01'},
                  {'payment_method': 'Cash'}, {'priority': 'high'}, {'number': None}):
        with pytest.raises(ValidationError):
            ProposalCreateInput(**base, **patch)
    for model, selector in ((ProposalUpdateInput, 'proposal'), (EstimateUpdateInput, 'estimate'), (WorkOrderUpdateInput, 'work_order')):
        with pytest.raises(ValidationError):
            model(**{selector: 'number'})
        with pytest.raises(ValidationError):
            model(**{selector: 'number'}, expected_version=1, lines=None)
    for value in ('number', '01arz3ndektsv4rrffq69g5fav', '81ARZ3NDEKTSV4RRFFQ69G5FAV'):
        with pytest.raises(ValidationError):
            EstimateCopyInput(estimate=value, expected_version=1, date='2026-01-01')
    copied = EstimateCopyInput(estimate='01ARZ3NDEKTSV4RRFFQ69G5FAV', expected_version=1, date='2026-01-01')
    assert copied.copy_mode == 'alternative'
    with pytest.raises(ValidationError):
        ProposalEstimateInput(proposal=copied.estimate, expected_version=1, date='2026-01-01', conversion_key='k', refresh_defaults=True)
    assert WorkQueryInput(active=None, minimum_net='0').active is None
    assert WorkQueryInput().limit == 50


def test_operational_utc_and_snapshot_validation(catalog):
    inp = WorkOrderCreateInput(date='2026-01-01', customer='Customer', title='Scope',
        scheduled_start='2026-01-01T10:00:00-08:00', scheduled_end='2026-01-01T19:00:00Z')
    assert inp.scheduled_start == '2026-01-01T18:00:00Z'
    with pytest.raises(ValidationError):
        WorkOrderCompleteInput(work_order='WO1', expected_version=1, actual_end='2026-01-01T10:00:00')
    with pytest.raises(ValidationError):
        EstimateWorkOrderInput(estimate='01ARZ3NDEKTSV4RRFFQ69G5FAV', expected_version=1,
            date='2026-01-01', conversion_key='k', assignees=['same', 'same'])
    h, facts, _ = quote(catalog, net_amount='10.01')
    for key, value in (('unit_price_minor_units', 500), ('gross_minor_units', 1), ('estimated_cost_minor_units', None),
                       ('completed_quantity_microunits', 2_000_000), ('net_minor_units', True)):
        with pytest.raises(ValidationError):
            WorkLineFacts.model_validate({**facts.model_dump(), key: value})
    with pytest.raises(ValidationError):
        WorkFacts(profile=h, issuer_snapshot={}, actual_end='2026-01-01T00:00:00Z')


def test_legacy_sales_profile_json_keys_order_and_roundtrip(catalog):
    with open_database(catalog.path, writable=False) as db:
        inp = InvoicePostInput(date='2026-01-01', customer=catalog.customer, lines=[dict(item=catalog.item)])
        profile = sales_defaults.resolve_header(SimpleNamespace(company=db), inp, 'invoice')[0]
    expected = ('schema_version customer control_account preferences billing_address shipping_address shipping_address_id '
        'terms due_date discount_date discount_available ship_date ship_method sales_rep class_id customer_tax_code '
        'sales_tax_item tax_rules price_level payment_method payment_reference customer_message customer_message_item '
        'customer_purchase_order origins').split()
    serialized = profile.model_dump_json()
    assert list(json.loads(serialized)) == expected
    assert SalesProfile.model_validate_json(serialized).model_dump_json() == serialized
    assert list(profile.model_dump(include={'customer', 'control_account'})) == ['customer', 'control_account']


def test_work_needs_no_ar_and_captures_terms_without_due_dates(catalog):
    catalog.client.account.create(name='Second work AR', type='accounts_receivable', company=COMPANY)
    term = catalog.client.run('term create', dict(name='Work net thirty', kind='standard', due_days=30), company=COMPANY)['id']
    customer = catalog.client.customer.show(customer=catalog.customer, company=COMPANY)
    catalog.client.customer.update(customer=catalog.customer, expected_version=customer['version'], terms_id=term, company=COMPANY)
    with open_database(catalog.path, writable=False) as db:
        s = SimpleNamespace(company=db)
        inp = ProposalCreateInput(date='2026-01-01', title='Scope', customer=catalog.customer)
        header, _ = resolve_header(s, inp, 'proposal')
        assert header.terms.id == term
        assert not any('due' in key or 'control' in key or 'payment' in key for key in header.model_dump())
        old_json = header.model_dump_json()
    customer = catalog.client.customer.show(customer=catalog.customer, company=COMPANY)
    catalog.client.customer.update(customer=catalog.customer, expected_version=customer['version'],
        billing_address={'line1': 'Current street'}, company=COMPANY)
    with open_database(catalog.path, writable=False) as db:
        s = SimpleNamespace(company=db)
        inp = ProposalUpdateInput(proposal='P1', expected_version=1)
        kept, _ = resolve_header(s, inp, 'proposal', previous=header, old_date='2026-01-01')
        assert kept.model_dump_json() == old_json
        refreshed, _ = resolve_header(s, ProposalUpdateInput(proposal='P1', expected_version=1, refresh_defaults=True),
            'proposal', previous=header, old_date='2026-01-01')
        assert refreshed.billing_address.line1 == 'Current street'
        cleared, _ = resolve_header(s, ProposalUpdateInput(proposal='P1', expected_version=1, terms=None),
            'proposal', previous=header, old_date='2026-01-01')
        assert cleared.terms is None and cleared.origins['terms'].kind == 'explicit'


def test_price_level_switch_and_cost_reset_select_current_defaults(catalog):
    level = catalog.client.run('price-level create', dict(name='Work ten percent', kind='fixed_percent', percent='10'), company=COMPANY)['id']
    h, amount, _ = quote(catalog, quantity='2', net_amount='10.01', estimated_unit_cost='1.00')
    _, catalog_line, _ = quote(catalog, header=h, previous=amount, price_level=level)
    assert catalog_line.pricing_basis == 'catalog' and catalog_line.unit_price_minor_units == 660
    _, manual, _ = quote(catalog, header=h, previous=amount, price_level=level, unit_price='5.00')
    assert manual.pricing_basis == 'manual' and manual.unit_price_minor_units == 500
    assert manual.profile.price_rule.id == level
    item_update(catalog, cost='8.00')
    _, reset, _ = quote(catalog, header=h, previous=amount, use_defaults=['estimated_unit_cost'])
    assert reset.estimated_unit_cost_minor_units == 800 and reset.net_minor_units == 1001
    _, markup, _ = quote(catalog, header=h, previous=amount, markup_percent='25', use_defaults=['estimated_unit_cost'])
    assert markup.estimated_unit_cost_minor_units == 800 and markup.unit_price_minor_units == 1000


def test_amount_and_markup_do_not_require_unused_inactive_inherited_price_level(catalog):
    level = catalog.client.run('price-level create', dict(name='Unused work rule', kind='fixed_percent', percent='10'), company=COMPANY)['id']
    customer = catalog.client.customer.show(customer=catalog.customer, company=COMPANY)
    catalog.client.customer.update(customer=catalog.customer, expected_version=customer['version'], price_level_id=level, company=COMPANY)
    catalog.client.run('price-level deactivate', {'price_level': level}, company=COMPANY)
    with open_database(catalog.path, writable=False) as db:
        s = SimpleNamespace(company=db)
        for override in ({'net_amount': '10.01'}, {'markup_percent': '25'}):
            inp = EstimateCreateInput(date='2026-01-01', title='Scope', customer=catalog.customer,
                lines=[dict(item=catalog.item, **override)])
            header = resolve_header(s, inp, 'estimate')[0]
            line = resolve_line(s, inp.lines[0], header)[0]
            assert line.net_minor_units == (1001 if 'net_amount' in override else 500)
            assert line.profile.origins['unit_price'].kind == 'explicit'


@pytest.mark.parametrize('cost,expected', [(None, None), ('0', 0)])
def test_catalog_unknown_and_zero_are_distinct(catalog, cost, expected):
    item_update(catalog, cost=cost)
    h, line, _ = quote(catalog)
    assert line.estimated_unit_cost_minor_units == expected
    assert line.estimated_cost_minor_units == expected
    assert line.estimated_cost_origin.kind == 'default'
    if expected is None:
        with pytest.raises(BookflowError, match='E_VALIDATION'):
            quote(catalog, header=h, previous=line, markup_percent='25')
    else:
        _, markup, _ = quote(catalog, header=h, previous=line, markup_percent='1000000')
        assert markup.unit_price_minor_units == 0


def test_explicit_cost_edits_do_not_reprice_other_modes(catalog):
    for override in ({}, {'unit_price': '7'}, {'net_amount': '10.01'}):
        h, line, _ = quote(catalog, **override)
        _, changed, _ = quote(catalog, header=h, previous=line, estimated_unit_cost='99')
        assert changed.net_minor_units == line.net_minor_units
        assert changed.unit_price_minor_units == line.unit_price_minor_units
        assert changed.estimated_cost_minor_units == 9900
