"""Hand-fixed Row24 arithmetic witnesses; no company or provider fixtures."""
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
from itertools import permutations

import pytest
from pydantic import ValidationError

from bookflow.company.tax_calculations import (
    TaxAccount, TaxCalculation, TaxLine, TaxPolicy, TaxReference, TaxRule, calculate_tax,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX

LEGACY, LINE, INVOICE = tuple(TaxPolicy)


def rule(id="A", rate=10_000_000, *, agency=None, account="payable", label=None, version=1):
    return TaxRule(
        id=id, label=label or id, version=version, rate_percent_millionths=rate,
        agency=TaxReference(id=agency or f"agency-{id}", label=f"Agency {id}", version=version),
        liability_account=TaxAccount(id=account, name="Sales tax", full_name="Sales tax",
                                     number=None, type="other_current_liability", normal_balance="credit"),
    )


def line(net, ordinal=1, rules=None):
    return TaxLine(tax_ordinal=ordinal, net_minor_units=net,
                   rules=(rule(),) if rules is None else tuple(rules))


def calc(lines, policy=INVOICE, currency="USD"):
    return calculate_tax(lines, policy=policy, currency=currency)


def cells(result):
    return {(c.tax_ordinal, c.rule.id): c.tax_minor_units
            for b in result.buckets for c in b.cells}


def agencies(result):
    totals = {}
    for liability in result.liabilities:
        totals[liability.agency_id] = totals.get(liability.agency_id, 0) + liability.tax_minor_units
    return totals


def assert_reconciles(result):
    assert result.tax_minor_units == sum(b.tax_minor_units for b in result.buckets)
    assert result.tax_minor_units == sum(l.tax_minor_units for l in result.lines)
    assert result.tax_minor_units == sum(a.tax_minor_units for a in result.accounts)
    assert result.tax_minor_units == sum(a.tax_minor_units for a in result.liabilities)
    assert result.gross_minor_units == result.net_minor_units + result.tax_minor_units
    for bucket in result.buckets:
        assert bucket.tax_minor_units == sum(c.tax_minor_units for c in bucket.cells)
        assert bucket.exact_numerator == sum(c.exact_numerator for c in bucket.cells)
        assert bucket.gross_minor_units == bucket.net_minor_units + bucket.tax_minor_units
    by_line = {}
    for (ordinal, _), amount in cells(result).items():
        by_line[ordinal] = by_line.get(ordinal, 0) + amount
    for total in result.lines:
        assert total.tax_minor_units == by_line.get(total.tax_ordinal, 0)
        assert total.gross_minor_units == total.net_minor_units + total.tax_minor_units


@pytest.mark.parametrize("policy", TaxPolicy)
def test_150_cents_at_7_25_percent(policy):
    result = calc([line(150, rules=[rule(rate=7_250_000)])], policy)
    assert result.buckets[0].exact_numerator == 1_087_500_000  # 10.875 cents / $0.10875
    assert cells(result) == {(1, "A"): 11}
    assert (result.net_minor_units, result.tax_minor_units, result.gross_minor_units) == (150, 11, 161)
    assert_reconciles(result)


@pytest.mark.parametrize("policy,expected", [(LEGACY, 2), (LINE, 3), (INVOICE, 3)])
def test_100_cents_at_2_5_percent(policy, expected):
    assert cells(calc([line(100, rules=[rule(rate=2_500_000)])], policy)) == {(1, "A"): expected}


@pytest.mark.parametrize("policy,expected", [(LEGACY, 0), (LINE, 1), (INVOICE, 1)])
def test_combined_is_not_independent_half_up(policy, expected):
    result = calc([line(10, rules=[rule("Z", 5_000_000), rule("A", 5_000_000)])], policy)
    assert cells(result) == {(1, "A"): expected, (1, "Z"): 0}
    assert result.tax_minor_units == expected


@pytest.mark.parametrize("policy,expected", [
    (LEGACY, {(1, "A"): 0, (2, "A"): 0}),
    (LINE, {(1, "A"): 1, (2, "A"): 1}),
    (INVOICE, {(1, "A"): 1, (2, "A"): 0}),
])
def test_two_half_cent_lines(policy, expected):
    result = calc([line(5, 2), line(5, 1)], policy)
    assert cells(result) == expected
    assert_reconciles(result)


@pytest.mark.parametrize("policy,first_net,expected_cells,expected_agencies", [
    (INVOICE, 5, {(1, "A"): 1, (1, "Z"): 0, (2, "A"): 1, (2, "Z"): 0}, {"agency-A": 2, "agency-Z": 0}),
    (INVOICE, 10, {(1, "A"): 1, (1, "Z"): 1, (2, "A"): 0, (2, "Z"): 0}, {"agency-A": 1, "agency-Z": 1}),
    (LINE, 5, {(1, "A"): 1, (1, "Z"): 0, (2, "A"): 1, (2, "Z"): 1}, {"agency-A": 2, "agency-Z": 1}),
    (LINE, 10, {(1, "A"): 1, (1, "Z"): 1, (2, "A"): 1, (2, "Z"): 0}, {"agency-A": 2, "agency-Z": 1}),
])
def test_net5_net10_new_document_order_oracle(policy, first_net, expected_cells, expected_agencies):
    rules = [rule("A", 10_000_000), rule("Z", 5_000_000)]
    result = calc([line(first_net, 1, rules), line(15 - first_net, 2, rules)], policy)
    assert cells(result) == expected_cells
    assert agencies(result) == expected_agencies
    assert result.tax_minor_units == (2 if policy == INVOICE else 3)
    assert result.accounts[0].tax_minor_units == result.tax_minor_units
    assert_reconciles(result)


def test_global_cell_allocation_and_reorders_keep_captured_ordinals():
    a, z = rule("A", 5_000_000), rule("Z", 5_000_000)
    expected = calc([line(10, 1, [a, z]), line(10, 8, [a, z])])
    assert cells(expected) == {(1, "A"): 1, (1, "Z"): 1, (8, "A"): 0, (8, "Z"): 0}
    for order in permutations([1, 8]):
        for rule_order in permutations([a, z]):
            assert calc([line(10, ordinal, rule_order) for ordinal in order]) == expected
    # Retired ordinal 1 is never reassigned; appending 9 leaves retained 8 ahead.
    assert cells(calc([line(5, 9), line(5, 8)])) == {(8, "A"): 1, (9, "A"): 0}


def test_provenance_does_not_split_economic_bucket_and_is_retained():
    old = rule(label="Old name", version=1)
    new = rule(label="New name", version=7).model_copy(update={
        "agency": TaxReference(id="agency-A", label="Renamed agency", version=12),
        "liability_account": rule().liability_account.model_copy(update={"name": "Renamed", "number": "2200"}),
    })
    result = calc([line(5, 1, [old]), line(5, 2, [new])])
    assert len(result.buckets) == 1
    assert result.tax_minor_units == 1
    assert [c.rule for c in result.buckets[0].cells] == [old, new]
    assert calc([line(5, 1, [old]), line(5, 2, [new])], LINE).tax_minor_units == 2


@pytest.mark.parametrize("second_rules", [
    [rule("B")], [rule(agency="other-agency")], [rule(account="other-account")],
    [rule(rate=5_000_000), rule("Z", 5_000_000)],
    [rule(rate=9_000_000)],
])
def test_economically_different_vectors_never_merge(second_rules):
    result = calc([line(5, 1), line(5, 2, second_rules)])
    assert len(result.buckets) == 2
    # Every equal-10% alternative rounds its own .5 cent up.
    assert result.tax_minor_units == (1 if second_rules[0].rate_percent_millionths == 9_000_000 else 2)
    assert_reconciles(result)


@pytest.mark.parametrize("policy", TaxPolicy)
def test_exempt_disabled_zero_net_and_zero_rate_keep_correct_cells(policy):
    result = calc([line(123, 1, []), line(456, 2, []), line(0, 3),
                   line(5, 4, [rule(rate=0), rule("Z")])], policy)
    assert cells(result) == {(3, "A"): 0, (4, "A"): 0, (4, "Z"): 0 if policy == LEGACY else 1}
    assert result.net_minor_units == 584
    assert [b.net_minor_units for b in result.buckets] == [0, 5]
    assert [l.tax_minor_units for l in result.lines[:2]] == [0, 0]
    assert_reconciles(result)
    empty = calc([], policy)
    assert empty.lines == empty.buckets == empty.accounts == empty.liabilities == ()
    assert empty.gross_minor_units == 0


@pytest.mark.parametrize("policy", TaxPolicy)
@pytest.mark.parametrize("rate,half_up,half_even", [(49_999_999, 0, 0), (50_000_000, 1, 0), (50_000_001, 1, 1)])
def test_below_at_above_half_cent_without_six_decimal_truncation(policy, rate, half_up, half_even):
    assert calc([line(1, rules=[rule(rate=rate)])], policy).tax_minor_units == (
        half_even if policy == LEGACY else half_up)


@pytest.mark.parametrize("currency", ["USD", "JPY", "KWD"])
def test_minor_units_do_not_depend_on_decimal_currency_scale(currency):
    assert calc([line(5)], currency=currency).tax_minor_units == 1


@pytest.mark.parametrize("policy", TaxPolicy)
def test_decimal_independent_single_cell_oracle_and_large_intermediates(policy):
    # Decimal is a test-only independent arithmetic path, with sufficient precision.
    with localcontext() as context:
        context.prec = 100
        for net in [0, 1, 5, 17, 150, 999_999, INT64_MAX // 2]:
            for rate in [0, 1, 7_250_000, 50_000_001, 100_000_000]:
                exact = Decimal(net) * Decimal(rate) / Decimal(100_000_000)
                expected = int(exact.to_integral_value(rounding=ROUND_HALF_EVEN if policy == LEGACY else ROUND_HALF_UP))
                result = calc([line(net, rules=[rule(rate=rate)])], policy)
                assert result.tax_minor_units == expected
                assert_reconciles(result)


def test_200_lines_and_large_rule_vector_with_global_ties():
    # The engine also serves forecasts beyond command entry collection limits.
    rules = [rule(f"R{i:03d}", 1_000_000) for i in range(200)]
    result = calc([line(1, ordinal, rules) for ordinal in range(200, 0, -1)])
    assert result.tax_minor_units == 400  # 200 cents net at 200% combined
    assert result.net_minor_units == 200
    assert result.gross_minor_units == 600
    assert all(c.tax_minor_units == (1 if c.tax_ordinal <= 2 else 0)
               for b in result.buckets for c in b.cells)
    assert_reconciles(result)
    forecast = calc([line(5, ordinal) for ordinal in range(1, 202)])
    assert forecast.tax_minor_units == 101


def assert_hand_attribution(result):
    """A fixed external oracle checks ownership as well as balanced amounts."""
    actual = {(c.tax_ordinal, c.rule.id, c.rule.agency.id, c.rule.liability_account.id): c.tax_minor_units
              for bucket in result.buckets for c in bucket.cells}
    assert actual == {(1, "A", "agency-A", "payable"): 1, (1, "Z", "agency-Z", "payable"): 0}
    assert agencies(result) == {"agency-A": 1, "agency-Z": 0}


def test_coherent_wrong_agency_is_rejected_by_independent_hand_oracle():
    result = calc([line(10, rules=[rule("A", 5_000_000), rule("Z", 5_000_000)])])
    assert_hand_attribution(result)
    # Commercial cents AND agency totals are swapped; account/document still balance.
    payload = result.model_dump()
    payload["buckets"][0]["cells"][0]["tax_minor_units"] = 0
    payload["buckets"][0]["cells"][1]["tax_minor_units"] = 1
    payload["liabilities"][0]["tax_minor_units"] = 0
    payload["liabilities"][1]["tax_minor_units"] = 1
    wrong = TaxCalculation.model_validate(payload)
    assert_reconciles(wrong)
    with pytest.raises(AssertionError):
        assert_hand_attribution(wrong)


@pytest.mark.parametrize("policy", TaxPolicy)
def test_signed64_exact_boundary_and_unbounded_intermediates(policy):
    assert calc([line(INT64_MAX, rules=[])], policy).gross_minor_units == INT64_MAX
    # Large exact products are allowed when every stored amount fits.
    net = INT64_MAX // 2
    result = calc([line(net, rules=[rule(rate=100_000_000)])], policy)
    assert result.tax_minor_units == net
    assert result.gross_minor_units == INT64_MAX - 1
    assert result.buckets[0].exact_numerator > INT64_MAX


@pytest.mark.parametrize("policy", TaxPolicy)
@pytest.mark.parametrize("bad_lines", [
    lambda: [line(INT64_MAX + 1, rules=[])],  # input net
    lambda: [line(INT64_MAX, rules=[rule(rate=100_000_000)])],  # net+tax
    lambda: [line(INT64_MAX, rules=[rule("A", 100_000_000), rule("Z", 100_000_000)])],  # line tax
    lambda: [line(INT64_MAX, 1, []), line(1, 2, [])],  # accumulated document net
    lambda: [line(INT64_MAX // 2, 1), line(INT64_MAX // 2, 2)],  # combined gross
    lambda: [line(INT64_MAX // 2, 1, [rule("A", 100_000_000)]),
             line(INT64_MAX // 2, 2, [rule("Z", 100_000_000)])],  # separate bucket document gross
])
def test_overflow_rejected_before_return(policy, bad_lines):
    with pytest.raises(BookflowError) as exc:
        calc(bad_lines(), policy)
    assert exc.value.code == "E_VALUE_RANGE"


@pytest.mark.parametrize("same_agency,same_account,expected_field", [
    (True, True, "liability.tax"), (False, True, "account.tax"),
    (False, False, "document.tax"),
])
def test_accumulated_agency_account_and_document_tax_overflow(same_agency, same_account, expected_field):
    # Each line/bucket gross fits, document net fits, but aggregate tax does not.
    net = INT64_MAX // 3
    rules1 = [rule("A", 100_000_000), rule("Z", 100_000_000, agency="agency-A")]
    rules2 = [rule("A", 100_000_000, agency="agency-A" if same_agency else "other",
                   account="payable" if same_account else "other-account"),
              rule("Z", 100_000_000, agency="agency-A" if same_agency else "other",
                   account="payable" if same_account else "other-account")]
    with pytest.raises(BookflowError) as exc:
        calc([line(net, 1, rules1), line(net, 2, rules2)], LINE)
    assert exc.value.code == "E_VALUE_RANGE"
    assert exc.value.details["fields"][0]["field"] == expected_field


def test_bucket_tax_overflow_with_different_accounts():
    # Individually bounded line, liability, account totals; one invoice bucket's tax overflows.
    rules = [rule("A", 100_000_000, account="a"), rule("Z", 100_000_000, account="z")]
    with pytest.raises(BookflowError) as exc:
        calc([line(INT64_MAX // 3, 1, rules), line(INT64_MAX // 3, 2, rules)])
    assert exc.value.code == "E_VALUE_RANGE"
    assert exc.value.details["fields"][0]["field"] == "bucket.tax"


@pytest.mark.parametrize("bad", [True, 1.0, "1", None])
@pytest.mark.parametrize("field", ["net_minor_units", "tax_ordinal"])
def test_no_scalar_coercion_even_for_unvalidated_model_copy(field, bad):
    with pytest.raises(BookflowError) as exc:
        calc([line(5).model_copy(update={field: bad})])
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize("bad", [True, 1.0, "1", None, -1, 100_000_001, INT64_MAX + 1])
def test_malformed_rates_reject(bad):
    bad_rule = rule().model_copy(update={"rate_percent_millionths": bad})
    bad_line = line(5).model_copy(update={"rules": (bad_rule,)})
    with pytest.raises(BookflowError) as exc:
        calc([bad_line])
    assert exc.value.code == ("E_VALUE_RANGE" if bad == INT64_MAX + 1 else "E_VALIDATION")


@pytest.mark.parametrize("bad", ["", " ", " A", "A ", "A\x00", "\ud800"])
def test_malformed_ids_reject(bad):
    with pytest.raises(BookflowError) as exc:
        calc([line(5, rules=[rule(bad)])])
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize("lines", [
    [line(5, 1), line(5, 1)],
    [line(5, rules=[rule(), rule()])],
    [line(5, rules=[rule(), rule(rate=5_000_000)])],
    [line(-1)], [line(5, rules=[rule(version=0)])],
])
def test_ambiguous_or_invalid_inputs_reject(lines):
    with pytest.raises(BookflowError) as exc:
        calc(lines)
    assert exc.value.code in {"E_VALIDATION", "E_VALUE_RANGE"}


@pytest.mark.parametrize("bad", [None, True, 0, [], "half_up", ""])
def test_unknown_policy_rejects(bad):
    with pytest.raises(BookflowError) as exc:
        calc([], bad)
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize("bad", [None, True, 0, [], "usd", "XYZ"])
def test_unknown_currency_rejects(bad):
    with pytest.raises(BookflowError) as exc:
        calc([], currency=bad)
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize("bad", [None, "", {}, [None], [{"tax_ordinal": 1, "net_minor_units": 5}]])
def test_malformed_collection_rejects(bad):
    with pytest.raises(BookflowError) as exc:
        calc(bad)
    assert exc.value.code == "E_VALIDATION"


def test_typed_input_is_frozen_and_forbids_extra_fields():
    with pytest.raises(ValidationError):
        TaxLine(tax_ordinal=1, net_minor_units=5, taxable=False)
    with pytest.raises(ValidationError):
        line(5).net_minor_units = 10


def test_existing_legacy_helper_keeps_hand_fixed_ties():
    from bookflow.company.sales_calculations import tax
    assert [tax(net, rate) for net, rate in [(150, 7_250_000), (100, 2_500_000),
                                             (5, 10_000_000), (15, 10_000_000)]] == [11, 2, 0, 2]


def test_bucket_net_overflow_even_when_every_tax_is_zero():
    with pytest.raises(BookflowError) as exc:
        calc([line(INT64_MAX, 1, [rule(rate=0)]), line(1, 2, [rule(rate=0)])])
    assert exc.value.code == "E_VALUE_RANGE"
    assert exc.value.details["fields"][0]["field"] == "bucket.net"


def test_rule_shape_accepts_complete_existing_captured_facts_without_loss():
    from bookflow.company.sales_facts import TaxRule as CapturedTaxRule
    captured = CapturedTaxRule.model_validate(rule().model_dump())
    local = TaxRule.model_validate(captured.model_dump())
    result = calc([line(5, rules=[local])])
    assert result.buckets[0].cells[0].rule.model_dump() == captured.model_dump()


def test_ordinal_bounds_and_missing_nested_references():
    for ordinal in (0, -1, INT64_MAX + 1):
        with pytest.raises(BookflowError) as exc:
            calc([line(5, ordinal)])
        assert exc.value.code == "E_VALUE_RANGE"
    for broken in (rule().model_copy(update={"agency": None}),
                   rule().model_copy(update={"liability_account": None})):
        with pytest.raises(BookflowError) as exc:
            calc([line(5).model_copy(update={"rules": (broken,)})])
        assert exc.value.code == "E_VALIDATION"
