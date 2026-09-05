"""Exact work projections and the real generated form boundary."""
from bookflow.adapters.workbench import work as W, forms as F
from bookflow.core import registry
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_customer_work_lifecycle import run, make


def test_amount_unknown_and_origin_preservation(client, sale):
    record = run(client, 'estimate', 'create', date='2026-01-12', title='Amount scope',
        customer=sale['customer'], lines=[dict(item=sale['item'], quantity='2', net_amount='10.01', estimated_unit_cost=None)])
    original = W.editable_values(record)
    row = original['lines'][0]
    assert row['net_amount'] == '10.01' and 'unit_price' not in row
    assert row['estimated_unit_cost'] is None
    form = F.collection_attempt(registry.get('estimate update').input_model.model_fields['lines'].annotation, 'lines', original['lines'])
    form['c:lines:0:quantity'] = '3'
    raw, _, _ = F.translate(registry.get('estimate update'), form, original)
    raw = W.preserve_line_origins(raw, original)
    assert raw['lines'] == [dict(line_id=row['line_id'], item=row['item'], quantity='3')]
    updated = run(client, 'estimate', 'update', estimate=record['id'], expected_version=1, **raw)
    assert updated['net_minor_units'] == 1001
    assert updated['revision']['lines'][0]['unit_price'] is None


def test_catalog_origin_and_copy_nulls(client, sale):
    first = make(client, sale)
    original = W.editable_values(first)
    raw = W.preserve_line_origins(dict(lines=[dict(original['lines'][0], quantity='3')]), original)
    assert 'unit_price' not in raw['lines'][0]
    changed = run(client, 'estimate', 'update', estimate=first['id'], expected_version=1, **raw)
    assert changed['revision']['lines'][0]['facts']['pricing_basis'] == 'catalog'
    copy = run(client, 'estimate', 'copy', estimate=changed['id'], expected_version=2, date='2026-01-13')
    assert copy['revision']['facts']['profile'] == changed['revision']['facts']['profile']
    assert copy['revision']['lines'][0]['estimated_unit_cost'] == changed['revision']['lines'][0]['estimated_unit_cost']
    assert W.detail_context(copy, COMPANY)['related'][0]['revision_url'].endswith('revision_number=2')


def test_explicit_same_rate_mode_switch_and_catalog_return(client, sale):
    record = make(client, sale)
    original = W.editable_values(record)
    cmd = registry.get('estimate update')
    form = F.collection_attempt(cmd.input_model.model_fields['lines'].annotation, 'lines', original['lines'])
    form['price-mode:lines:0'] = 'manual'
    baseline = W.price_originals(original, form)
    raw, _, _ = F.translate(cmd, W.price_controls(form), baseline)
    raw = W.preserve_line_origins(raw, baseline)
    assert raw['lines'][0]['unit_price'] == '12.34'
    switched = run(client, 'estimate', 'update', estimate=record['id'], expected_version=1, **raw)
    assert switched['revision']['lines'][0]['facts']['pricing_basis'] == 'manual'
    original = W.editable_values(switched)
    form = F.collection_attempt(cmd.input_model.model_fields['lines'].annotation, 'lines', original['lines'])
    form['price-mode:lines:0'] = 'catalog'
    raw, _, _ = F.translate(cmd, W.price_controls(form), original)
    raw = W.preserve_line_origins(raw, original)
    assert raw['lines'][0]['use_defaults'] == ['unit_price']
    returned = run(client, 'estimate', 'update', estimate=record['id'], expected_version=2, **raw)
    assert returned['revision']['lines'][0]['facts']['pricing_basis'] == 'catalog'
