"""Actual payment models keep every branch, including nested preview intent."""
from bookflow.adapters.workbench import forms
from bookflow.core import registry
from bookflow.company.payment_models import PaymentApplyInput, PaymentPreviewItemsInput

GHOST = '01ARZ3NDEKTSV4RRFFQ69G5FAV'


def test_payment_selection_branch_encoding_omits_disabled_inline_fields():
    registry.load_all()
    cmd = registry.get('payment apply')
    leaves = {row['path']: row for row in forms.leaves(cmd.input_model)}
    assert leaves['applications.mode']['choices'] == ['inline', 'selection']
    assert {'applications.selection', 'applications.expected_version', 'applications.items'} <= leaves.keys()
    form = {'f:payment': GHOST, 'f:expected_version': '1', 'f:date': '2026-06-01',
            'f:operation_key': 'saved-selection', 'f:applications.mode': 'selection',
            'f:applications.selection': GHOST, 'f:applications.expected_version': '2',
            'collection:applications.items': '1', 'c:applications.items:0:invoice': 'stale-hidden-value'}
    raw, headers, preview = forms.translate(cmd, form, None)
    assert raw['applications'] == {'mode': 'selection', 'selection': GHOST, 'expected_version': 2}
    PaymentApplyInput.model_validate(raw)
    form['f:applications.mode'] = 'inline'
    form['c:applications.items:0:invoice'] = GHOST
    form['c:applications.items:0:expected_version'] = '3'
    form['c:applications.items:0:amount'] = '1.00'
    raw, _, _ = forms.translate(cmd, form, None)
    assert raw['applications'] == {'mode': 'inline', 'items': [{'invoice': GHOST, 'expected_version': 3, 'amount': '1.00'}]}
    PaymentApplyInput.model_validate(raw)


def test_nested_preview_branch_preserves_both_discriminators():
    registry.load_all()
    cmd = registry.get('payment preview items')
    form = {'f:request.command': 'payment apply', 'f:request.input.payment': GHOST,
            'f:request.input.expected_version': '1', 'f:request.input.date': '2026-06-01',
            'f:request.input.operation_key': 'nested-selection', 'f:request.input.applications.mode': 'selection',
            'f:request.input.applications.selection': GHOST, 'f:request.input.applications.expected_version': '2',
            'f:request.input.customer': 'disabled-receive-branch',
            'f:facts_fingerprint': 'a' * 64, 'f:kind': 'applications'}
    raw, _, _ = forms.translate(cmd, form, None)
    assert 'customer' not in raw['request']['input']
    assert raw['request']['input']['applications'] == {'mode': 'selection', 'selection': GHOST, 'expected_version': 2}
    PaymentPreviewItemsInput.model_validate(raw)
