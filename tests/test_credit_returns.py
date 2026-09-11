"""The endpoint rule, on its own, with nothing else in the way.

These are properties of the arithmetic rather than of any document: that disjoint intervals
telescope to exactly the captured totals whatever order they are claimed in, that releasing an
interval and claiming it again returns the identical cents, and that a running remainder would
not do either. They are here because a rule checked only through the command it serves is
checked only on the paths that command happens to take.
"""
import itertools

import pytest

from bookflow.company import credit_returns as returns
from bookflow.core.errors import BookflowError

# The plan's worked line: three units, a captured net of 100 and one captured tax cell of 7.
QUANTITY = 3_000_000
NET = 100
TAX = 7
UNITS = ((0, 1_000_000), (1_000_000, 2_000_000), (2_000_000, 3_000_000))


def test_the_three_units_are_worth_what_the_oracle_says():
    assert [returns.owned(NET, QUANTITY, start, end) for start, end in UNITS] == [33, 33, 34]
    cells = [returns.priced([span], QUANTITY, NET, [dict(taxable_minor_units=NET, tax_minor_units=TAX)])
             for span in UNITS]
    assert [cell['net_minor_units'] for cell in cells] == [33, 33, 34]
    assert [cell['taxes'][0]['tax_minor_units'] for cell in cells] == [2, 2, 3]


def test_any_order_of_claiming_reaches_the_same_totals_and_the_same_cents():
    """Order-independence is the property a running remainder does not have."""
    seen = set()
    for order in itertools.permutations(UNITS):
        claimed, priced = [], {}
        for span in order:
            taken = returns.take(claimed, QUANTITY, span[1] - span[0])
            # Taking lowest-first means the spans come back in quantity order whatever order
            # they were asked for in, and each is worth what that position is worth.
            claimed.extend(taken)
            for interval in taken:
                priced[interval] = returns.priced(
                    [interval], QUANTITY, NET, [dict(taxable_minor_units=NET, tax_minor_units=TAX)])
        assert sorted(claimed) == list(UNITS)
        assert sum(row['net_minor_units'] for row in priced.values()) == NET
        assert sum(row['taxes'][0]['tax_minor_units'] for row in priced.values()) == TAX
        seen.add(tuple(sorted((key, row['net_minor_units'], row['taxes'][0]['tax_minor_units'])
                              for key, row in priced.items())))
    assert len(seen) == 1


def test_releasing_a_span_and_claiming_it_again_returns_the_identical_cents():
    for released in range(3):
        remaining = [span for index, span in enumerate(UNITS) if index != released]
        assert returns.available(remaining, QUANTITY) == [UNITS[released]]
        again = returns.take(remaining, QUANTITY, 1_000_000)
        assert again == [UNITS[released]]
        first = returns.priced([UNITS[released]], QUANTITY, NET,
                               [dict(taxable_minor_units=NET, tax_minor_units=TAX)])
        second = returns.priced(again, QUANTITY, NET,
                                [dict(taxable_minor_units=NET, tax_minor_units=TAX)])
        assert first == second


def test_every_partition_of_the_quantity_telescopes_to_the_captured_totals():
    """Not just the unit split: every way of cutting the line up adds to the same cents."""
    for cuts in itertools.combinations(range(1, 30), 3):
        spans = list(zip((0,) + tuple(c * 100_000 for c in cuts),
                         tuple(c * 100_000 for c in cuts) + (QUANTITY,)))
        spans = [(start, end) for start, end in spans if end > start]
        priced = returns.priced(spans, QUANTITY, NET,
                                [dict(taxable_minor_units=NET, tax_minor_units=TAX)])
        assert priced['net_minor_units'] == NET
        assert priced['taxes'][0]['tax_minor_units'] == TAX
        assert priced['taxes'][0]['taxable_minor_units'] == NET


def test_the_residue_is_the_gaps_and_running_out_refuses_before_it_takes_anything():
    assert returns.remaining([], QUANTITY) == QUANTITY
    assert returns.remaining([UNITS[1]], QUANTITY) == 2_000_000
    assert returns.available([UNITS[1]], QUANTITY) == [UNITS[0], UNITS[2]]
    # Two units when the middle one is gone: the two gaps, lowest first.
    assert returns.take([UNITS[1]], QUANTITY, 2_000_000) == [UNITS[0], UNITS[2]]
    with pytest.raises(BookflowError) as caught:
        returns.take([UNITS[1]], QUANTITY, 2_000_001)
    assert caught.value.code == 'E_RETURN_EXHAUSTED'
    assert caught.value.details['remaining_base_microunits'] == 2_000_000


def test_overlapping_claims_are_a_contradiction_rather_than_a_smaller_residue():
    with pytest.raises(BookflowError) as caught:
        returns.available([(0, 2_000_000), (1_000_000, 3_000_000)], QUANTITY)
    assert caught.value.code == 'E_INTERNAL'


def test_a_line_worth_one_cent_still_partitions_without_losing_or_inventing_it():
    """The pathological case the nullable attribution exists for."""
    spans = [returns.priced([span], QUANTITY, 1, []) for span in UNITS]
    assert [row['net_minor_units'] for row in spans] == [0, 0, 1]
    assert sum(row['net_minor_units'] for row in spans) == 1
