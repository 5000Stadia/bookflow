"""Declaration metadata must never substitute for validating supplied facts."""
from dataclasses import asdict, replace

import pytest

from bookflow.hub import permission_catalog as catalog
from bookflow.hub import permission_snapshot as snapshot


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('fault', ['threshold', 'capability', 'tuple', 'boolean'])
def test_warm_declarations_still_validate_each_nested_value(native, fault):
    valid = catalog.CompanyAction(
        'invoice.show', (catalog.Requirement('invoice', 'member'),), True, ()
    )
    # Warm the existing declaration cache independently of the decoder. The
    # decoder must still inspect every new value, including native dataclasses.
    for annotation in (catalog.CompanyAction, catalog.Requirement):
        catalog._field_types(annotation)
    value = valid if native else asdict(valid)
    assert snapshot._decode(value, catalog.CompanyAction, 'catalog', native=native) == valid
    if fault == 'threshold':
        bad = replace(valid, requirements=(catalog.Requirement('invoice', 'unknown'),))
    elif fault == 'capability':
        bad = replace(valid, requirements=(catalog.Requirement(7, 'member'),))
    elif fault == 'tuple':
        bad = replace(valid, requirements='invoice')
    else:
        bad = replace(valid, available=1)
    supplied = bad if native else asdict(bad)
    with pytest.raises(snapshot.SnapshotError) as caught:
        snapshot._decode(supplied, catalog.CompanyAction, 'catalog', native=native)
    assert (caught.value.code, caught.value.field) == ('invalid_type', 'catalog')
    # A rejection also must not poison the next valid observation.
    assert snapshot._decode(value, catalog.CompanyAction, 'catalog', native=native) == valid


@pytest.mark.parametrize('fault', ['missing', 'extra'])
def test_warm_declarations_require_exact_serialized_fields(fault):
    catalog._field_types(catalog.Requirement)
    value = {'capability': 'invoice', 'threshold': 'member'}
    assert snapshot._decode(value, catalog.Requirement, 'catalog') == catalog.Requirement('invoice', 'member')
    if fault == 'missing':
        del value['threshold']
    else:
        value['private_extra'] = 'must not escape'
    with pytest.raises(snapshot.SnapshotError) as caught:
        snapshot._decode(value, catalog.Requirement, 'catalog')
    assert (caught.value.code, caught.value.field) == ('invalid_fields', 'catalog')
    assert 'must not escape' not in str(caught.value)
