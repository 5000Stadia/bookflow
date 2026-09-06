"""Hand-fixed owning-fixture cents and cross-interface draft-origin witnesses."""
import pytest

from bookflow.company.payment_calculations import (
    ComponentKey as K, DraftRow as R, allocate, calculate, receipt_components,
)
from bookflow.core.exact import INT64_MAX


def test_partial_final_and_added_exempt_line_oracles():
    net, tax, exempt = K(1, 0), K(1, 1, 'tax'), K(2, 0)
    assert allocate(5401, {net: 10000, tax: 800}) == {net: 5001, tax: 400}
    assert allocate(5399, {net: 4999, tax: 400}) == {net: 4999, tax: 400}
    assert allocate(5401, {net: 10000, tax: 800, exempt: 2000}) == {net: 4219, tax: 338, exempt: 844}
    assert allocate(7399, {net: 5781, tax: 462, exempt: 1156}) == {net: 5781, tax: 462, exempt: 1156}


def test_owning_ties_use_ordinals_net_then_binary_tax_not_physical_ids():
    retained_z, new_a = K(1, 0), K(2, 0)
    assert allocate(1, {new_a: 1, retained_z: 1}) == {retained_z: 1}
    assert allocate(1, {K(1, 1, 'tax'): 1, retained_z: 1}) == {retained_z: 1}
    net, a, z = retained_z, K(1, 1, 'A'), K(1, 1, 'Z')
    assert allocate(8, {z: 8, a: 8, net: 100}) == {a: 1, net: 7}
    assert allocate(108, {net: 93, a: 7, z: 8}) == {net: 93, a: 7, z: 8}


def test_intermediate_products_use_unbounded_integers():
    assert allocate(INT64_MAX, {K(1, 0): INT64_MAX}) == {K(1, 0): INT64_MAX}


@pytest.mark.parametrize('bad', [True, 1.0, -1, 0, INT64_MAX + 1])
def test_bad_application_amount_rejected(bad):
    with pytest.raises(ValueError):
        allocate(bad, {K(1, 0): 100})


def test_no_zero_or_over_capacity_allocations():
    assert allocate(1, {K(1, 0): 0, K(2, 0): 1}) == {K(2, 0): 1}
    with pytest.raises(ValueError):
        allocate(2, {K(1, 0): 1})


def test_family_receipt_owns_residual_at_payer():
    assert receipt_components('P', 1000, [('P', 100), ('A', 600), ('B', 200)]) == {'P': 200, 'A': 600, 'B': 200}
    assert receipt_components('P', 200, [('A', 200)]) == {'A': 200}
    assert receipt_components('P', 100, []) == {'P': 100}
    with pytest.raises(ValueError):
        receipt_components('P', 100, [('A', 101)])


def test_entered_cash_handoff_after_deselect_recalculates_only_derived_rows():
    original = calculate(15000, 'entered', [R('A', 1, 10000, 10000, 'calculated'), R('B', 2, 10000, 5000, 'calculated')])
    assert [r.amount for r in original.rows] == [10000, 5000]
    calculated = calculate(original.amount, original.amount_origin, [original.rows[1]])
    assert (calculated.amount, calculated.rows[0].amount, calculated.unapplied) == (15000, 10000, 5000)
    manual = calculate(15000, 'entered', [R('B', 2, 10000, 5000, 'entered')])
    assert (manual.amount, manual.rows[0].amount, manual.unapplied) == (15000, 5000, 10000)


def test_entered_rows_reserved_before_any_calculated_row():
    result = calculate(150, 'entered', [R('A', 1, 100, 100, 'calculated'), R('B', 2, 100, 75, 'entered')])
    assert [r.amount for r in result.rows] == [75, 75]
    result = calculate(50, 'entered', [R('A', 1, 100, 100, 'calculated'), R('B', 2, 100, 75, 'entered')])
    assert [r.amount for r in result.rows] == [0, 75]
    assert result.problems == ('A:unfunded', 'applications:exceed_amount')


def test_selection_total_and_unresolved_are_distinct_without_policy_adoption():
    rows = [R('A', 1, 100, None, 'unresolved')]
    assert calculate(None, 'selection_total', rows).amount is None
    result = calculate(None, 'selection_total', rows, calculate_unresolved=True)
    assert (result.amount, result.rows[0].origin) == (100, 'calculated')
    assert calculate(None, 'unresolved', rows).amount is None


def test_complete_receipt_has_no_read_page_business_limit():
    rows = [R(str(i), i + 1, 3, 3, 'calculated') for i in range(403)]
    result = calculate(1209, 'entered', rows)
    assert len(result.rows) == 403 and result.unapplied == 0 and not result.problems
    assert len(receipt_components('P', 1209, [(str(i), 3) for i in range(403)])) == 403
