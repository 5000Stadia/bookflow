"""R186 inverse declarations are independent of what files can import."""
from bookflow.company import batches, import_kinds, reversal_registry
from bookflow.core import registry


def test_import_inverses_still_agree_with_independent_registry():
    for kind in import_kinds.DOC_KINDS.values():
        rec = batches.document_record(kind.family)
        assert (rec.noun, rec.selector, rec.inverse, rec.table.name, rec.type_column) == (
            kind.noun, kind.selector, kind.inverse, kind.table, kind.type_column)
    rec = batches.document_record('statement_charge')
    assert rec.inverse == 'statement-charge void'
    assert rec.selector in registry.get(rec.inverse).input_model.model_fields
    assert 'statement-charge' not in import_kinds.DOC_KINDS


def test_import_catalog_does_not_control_reversibility(monkeypatch):
    monkeypatch.setattr(import_kinds, 'DOC_KINDS', {})
    assert batches.document_record('invoice').inverse == 'invoice delete'
    assert batches.document_record('bill_payment').inverse == 'bill payment void'
    rec = reversal_registry.list_record('customer')
    assert reversal_registry.steps_for({'action': 'updated', 'record_id': 'id', 'before': {'phone': 'old'}},
                                       rec, 3) == [('customer update', {
                                           'customer': 'id', 'expected_version': 3, 'phone': 'old'})]


def test_door_authority_is_read_capability_and_confirm_first():
    door, show = registry.get('batch reverse'), registry.get('batch show')
    assert (door.capability, door.required_role) == (show.capability, show.required_role)
    assert not door.resource_requirements
    assert 'Confirm-first' in door.description and 'cannot be recalled' in door.description


def test_new_process_loads_every_adopter_without_first_running_it():
    import subprocess
    import sys
    from tests.provenance import child_env
    result = subprocess.run([sys.executable, '-c', '''
from bookflow.company import batches
for kind in ('batch-invoice', 'bill-pay', 'fixed-asset-depreciation', 'finance-charge-assessment',
             'inventory-count', 'check-print', 'delivery-batch'):
    assert batches.adapter_for(kind).kind == kind
'''], capture_output=True, text=True, timeout=60, env=child_env())
    assert result.returncode == 0, result.stderr
