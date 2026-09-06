"""Numeric entry metadata follows exact command meanings and rendered controls."""
import json
from html.parser import HTMLParser

from pydantic import BaseModel, Field

from bookflow.adapters.workbench import forms
from bookflow.core import registry
from bookflow.core.exchange import canonical_rate
from bookflow.core.exact import parse_custom_number_nano_units, parse_quantity_micro_units
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser


def descriptors(command):
    return {row['path']: row for row in forms.leaves(registry.get(command).input_model)}


def children(command, field):
    return {row['name']: row for row in forms.collection_schema(descriptors(command)[field]['annotation'])['item']['fields']}


def test_exact_precision_contracts_and_recursive_models():
    assert descriptors('rate set')['rate']['math'] == {'scale': 18}
    assert descriptors('journal post')['rate']['math'] == {'scale': 18}
    assert canonical_rate('0.123456789012345678') == '0.123456789012345678'
    assert children('estimate create', 'lines')['quantity']['math'] == {'scale': 6}
    assert children('estimate create', 'lines')['markup_percent']['math'] == {'scale': 6}
    assert children('unit-of-measure create', 'units')['base_factor']['math'] == {'scale': 9}
    assert children('item create', 'vendor_profiles')['minimum_quantity']['math'] == {'scale': 6}
    assert children('item create', 'vendor_profiles')['preferred_rank']['math'] == {'scale': 0}
    assert parse_quantity_micro_units('0.123456') == 123456
    assert parse_custom_number_nano_units('0.123456789') == 123456789


def test_money_meanings_include_untyped_inputs_and_collections():
    for field in ('price', 'cost', 'discount_amount', 'original_cost', 'disposal_proceeds', 'disposal_costs', 'book_basis', 'tax_basis'):
        assert descriptors('item create')[field]['math'] == {'currency': 'company'}
    assert children('item create', 'vendor_profiles')['purchase_cost']['math'] == {'currency': 'company'}
    assert children('invoice post', 'lines')['price_basis_amount']['math'] == {'currency': 'company'}
    assert descriptors('customer create')['credit_limit']['math'] == {'currency': 'company'}
    assert descriptors('price-level create')['rounding_increment']['math'] == {'currency': 'company'}


def test_identity_fields_are_not_numeric_and_escape_hatch_is_recursive():
    for name in ('asset_number', 'serial_number', 'barcode', 'manufacturer_part_number'):
        assert descriptors('item create')[name]['math'] == {}
    assert descriptors('invoice post')['number']['math'] == {}
    class Child(BaseModel):
        measurement: str = Field(json_schema_extra={'math': {'scale': 4}})
        sequence_identity: int = Field(json_schema_extra={'math': False})
    class Parent(BaseModel):
        rows: list[Child]
    fields = forms.collection_schema(Parent.model_fields['rows'].annotation)['item']['fields']
    assert [f['math'] for f in fields] == [{'scale': 4}, {}]


def test_custom_number_default_and_captured_kind():
    cmd = registry.get('custom-field create')
    fields = forms.describe_fields('custom-field', 'create', cmd.input_model, {}, {})
    default = next(f for f in fields if f['path'] == 'default')
    assert default['math'] == {'scale': 9, 'active': [{'name': 'f:kind', 'values': ['number']}]}
    definition = dict(id='n', name='Numeric', kind='number', choices=[])
    assert forms.custom_field_descriptors([definition])[0]['math'] == {'scale': 9}
    assert forms.custom_field_descriptors([definition], attempted={'cf-kind:n': 'text'})[0]['math'] == {}


def test_numeric_inventory_covers_all_registered_integer_controls():
    registry.load_all()
    seen = set()

    def check(schema, command):
        item = schema['item']
        controls = item['fields'] if item['kind'] == 'object' else [item]
        for control in controls:
            if control['kind'] == 'collection':
                check(control['collection'], command)
            elif forms._base(control['annotation'])[0] is int:
                assert control['math'] == {'scale': 0}, (command, control)
                seen.add(control['name'])

    for command in registry.all_commands():
        for leaf in forms.leaves(command.input_model):
            if leaf['kind'] == 'collection':
                check(forms.collection_schema(leaf['annotation']), command.name)
            elif forms._base(leaf['annotation'])[0] is int:
                assert leaf['math'] == {'scale': 0}, (command.name, leaf)
                seen.add(leaf['path'])
    assert {'limit', 'due_days', 'preferred_rank', 'lead_time_days', 'useful_life_months', 'display_order', 'fiscal_year_start_month', 'attachment_max_bytes'} <= seen


def test_remaining_decimal_families_and_identity_exclusions():
    fields = descriptors('item create')
    for name in ('reorder_point_min', 'reorder_point_max', 'assembly_build_point', 'tax_percent', 'charge_percent', 'discount_percent'):
        assert fields[name]['math'] == {'scale': 6}
    assert descriptors('term create')['discount_percent']['math'] == {'scale': 6}
    for name in ('completed_quantity', 'markup_percent'):
        assert children('estimate create', 'lines')[name]['math'] == {'scale': 6}
    assert children('estimate create', 'lines')['estimated_unit_cost']['math'] == {'currency': 'company'}
    for name in ('minimum_net', 'maximum_net'):
        assert descriptors('estimate query')[name]['math'] == {'currency': 'company'}
    assert descriptors('sales-receipt update')['amount_received']['math'] == {'currency': 'company'}
    for name in ('number', 'next_check_number', 'check_reorder_number', 'institution_account_last4', 'routing_number_last4'):
        assert descriptors('account create')[name]['math'] == {}
    for command in registry.all_commands():
        for leaf in forms.leaves(command.input_model):
            if leaf['path'].split('.')[-1] in ('postal_code', 'phone', 'date', 'customer_purchase_order', 'payment_reference'):
                assert leaf['math'] == {}, (command.name, leaf)


def test_custom_keep_and_clear_preserve_expression_without_sending_it():
    command = registry.get('journal post')
    definition = dict(id='n', name='Numeric', kind='number', choices=[])
    for state, expected in [('keep', None), ('clear', {'n': None}), ('set', {'n': '1/0'})]:
        attempt = {'cf:n': '1/0', 'cf-kind:n': 'number', 'cf-state:n': state}
        descriptor = forms.custom_field_descriptors([definition], {'n': '2'}, attempt, update=True)[0]
        assert descriptor['state'] == state
        assert descriptor['value'] == '1/0'
        raw, _, _ = forms.translate(command, attempt, {'custom_fields': {'n': '2'}})
        assert raw.get('custom_fields') == expected


class Inputs(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.controls = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and 'name' in attrs:
            self.controls[attrs['name']] = attrs


def test_generated_pages_render_currency_precision_and_dynamic_hooks(hosted):
    browser = _browser(hosted)
    base = f'/c/{hosted.company_id}'
    page = browser.get(base + '/price-level/create')
    assert page.status_code == 200, page.text
    inputs = Inputs(page.text).controls
    assert inputs['f:rounding_increment']['data-math-currency-field'] == 'f:currency'
    assert inputs['c:items:__INDEX0__:price']['data-math-currency-field'] == 'f:currency'
    assert 'id="math-currencies"' in page.text
    assert 'data-math-company-currency="USD"' in page.text
    assert '/static/numeric-entry.js?v=' in page.text
    assert page.text.index('/static/numeric-context.js?v=') < page.text.index('/static/numeric-entry.js?v=')
    page = browser.get(base + '/estimate/create')
    assert page.status_code == 200, page.text
    inputs = Inputs(page.text).controls
    quantity = inputs['c:lines:__INDEX0__:quantity']
    assert quantity['data-math-scale'] == '6'
    assert quantity['data-math-price-mode'] == 'price-mode:lines:__INDEX0__'
    assert inputs['c:lines:__INDEX0__:estimated_unit_cost']['data-math-clear'] == 'clear:c:lines:__INDEX0__:estimated_unit_cost'
    assert not any(key.startswith('data-math') for key in inputs['f:number'])
    definition = hosted.ok('custom-field.create', {'name': 'Math metadata number', 'kind': 'number', 'scopes': ['journal_entry']}, company=hosted.company_id)
    page = browser.get(base + '/journal/post')
    assert page.status_code == 200, page.text
    control = Inputs(page.text).controls['cf:' + definition['id']]
    assert control['data-math-scale'] == '9'
    assert json.loads(control['data-math-active']) == [{'name': 'cf-state:' + definition['id'], 'values': ['set']}]
    assert control['data-math-clear'] == 'clear:custom_fields.' + definition['id']


def test_billing_modes_render_active_dependencies():
    from bookflow.adapters.workbench.pages import env
    text = env.get_template('billing_selection.html').render(
        billing=dict(preferences=dict(progress_billing_enabled=True), currency='JPY', source_id='s', owner_id='s',
                     lines=[dict(line_id='l', description='Line', billable=True, state='open', remaining_quantity='1', remaining_net='1')]),
        attempted={},
    )
    control = Inputs(text).controls['billing-value:l']
    assert control['data-math-currency'] == 'JPY'
    assert control['data-math-mode-field'] == 'billing-mode:l'
    assert json.loads(control['data-math-active']) == [
        {'name': 'billing-selection', 'values': ['partial']},
        {'name': 'billing-line:l', 'checked': True},
        {'name': 'billing-mode:l', 'values': ['quantity', 'net_amount', 'percent']},
    ]
