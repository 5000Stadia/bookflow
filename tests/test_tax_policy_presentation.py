"""Human policy controls preserve capture semantics and show core interpretation."""
from types import SimpleNamespace
from bookflow.adapters.workbench import forms
from bookflow.adapters.workbench.sales import editable_values
from bookflow.company.sales_models import InvoiceUpdateInput
from bookflow.company.tax_policy import POLICY_LABELS
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY
from tests.test_service_sales_presentation import render


def test_policy_edit_baseline_and_readable_captured_details(client, tax_sale):
    original = client.run('invoice post', request(tax_sale), company=COMPANY)
    baseline = editable_values(original)
    policy = 'invoice_combined_half_up'
    assert baseline['sales_tax_calculation'] == policy
    command = SimpleNamespace(input_model=InvoiceUpdateInput, name='invoice update')
    form = {'f:invoice': original['id'], 'f:expected_version': '1',
            'f:sales_tax_calculation': policy, 'f:memo': 'Human metadata edit'}
    raw, _, _ = forms.translate(command, form, baseline)
    assert 'sales_tax_calculation' not in raw
    saved = client.run('invoice update', raw, company=COMPANY)
    assert saved['revision']['tax_calculation_details']['origin'] == original['revision']['tax_calculation_details']['origin']
    html = render(saved)
    assert POLICY_LABELS[policy] in html
    assert 'Captured company default' in html and 'stable tax order' in html
    assert 'Stable tax order: 1' in html and 'Stable tax order: 2' in html
    assert '0.02' in html
    leaf = next(leaf for leaf in forms.leaves(InvoiceUpdateInput) if leaf['path'] == 'sales_tax_calculation')
    assert leaf['choices'] == list(POLICY_LABELS)
    assert leaf['choice_labels'] == POLICY_LABELS
    form.update({'f:expected_version': '2', 'f:sales_tax_calculation': 'line_component_half_even'})
    raw, _, _ = forms.translate(command, form, editable_values(saved))
    saved = client.run('invoice update', raw, company=COMPANY)
    assert saved['tax_minor_units'] == 0
    assert 'Explicit document choice' in render(saved)
    assert 'Remainder ties' not in render(saved)
