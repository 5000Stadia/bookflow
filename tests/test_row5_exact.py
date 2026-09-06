from decimal import Decimal

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.exact import (
    INT64_MAX,
    INT64_MIN,
    bom_component_extension_minor_units,
    convert_quantity_micro_units,
    format_custom_number_nano_units,
    format_percentage_millionths,
    format_quantity_micro_units,
    format_unit_factor_nano_units,
    parse_custom_number_nano_units,
    parse_percentage_millionths,
    parse_quantity_micro_units,
    parse_unit_factor_nano_units,
    round_ratio_half_even,
)


def test_scaled_parsers_and_formatters_are_canonical():
    assert parse_percentage_millionths("001.230000") == 1_230_000
    assert format_percentage_millionths(1_230_000) == "1.23"
    assert parse_percentage_millionths("-0.000001") == -1

    assert parse_quantity_micro_units("00012.500000") == 12_500_000
    assert parse_quantity_micro_units(".5") == 500_000
    assert parse_quantity_micro_units("-.5") == -500_000
    assert parse_quantity_micro_units(".000001") == 1
    assert format_quantity_micro_units(12_500_000) == "12.5"
    assert format_quantity_micro_units(-1) == "-0.000001"

    assert parse_unit_factor_nano_units("1.000000000") == 1_000_000_000
    assert format_unit_factor_nano_units(1_000_000_000) == "1"
    assert parse_custom_number_nano_units("-000.010200000") == -10_200_000
    assert format_custom_number_nano_units(-10_200_000) == "-0.0102"

    assert format_percentage_millionths(parse_percentage_millionths("-0.000000")) == "0"
    assert format_custom_number_nano_units(parse_custom_number_nano_units("000.000")) == "0"


@pytest.mark.parametrize(
    "parser",
    [
        parse_percentage_millionths,
        parse_quantity_micro_units,
        parse_unit_factor_nano_units,
        parse_custom_number_nano_units,
    ],
)
@pytest.mark.parametrize("bad", [1, 1.0, True, None, Decimal("1.0")])
def test_scaled_parsers_reject_every_non_string_type(parser, bad):
    with pytest.raises(BookflowError) as exc:
        parser(bad)
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize(
    "bad",
    ["", " 1", "1 ", "+1", ".1", "1.", "1e2", "1_000", "--1"],
)
def test_scaled_parsers_reject_non_plain_decimal_syntax(bad):
    with pytest.raises(BookflowError) as exc:
        parse_custom_number_nano_units(bad)
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize(
    ("parser", "bad"),
    [
        (parse_percentage_millionths, "0.0000001"),
        (parse_quantity_micro_units, "0.0000001"),
        (parse_unit_factor_nano_units, "0.0000000001"),
        (parse_custom_number_nano_units, "0.0000000001"),
    ],
)
def test_scaled_parsers_reject_excess_scale(parser, bad):
    with pytest.raises(BookflowError) as exc:
        parser(bad)
    assert exc.value.code == "E_VALIDATION"


@pytest.mark.parametrize(
    ("parser", "formatter", "maximum", "minimum"),
    [
        (
            parse_percentage_millionths,
            format_percentage_millionths,
            "9223372036854.775807",
            "-9223372036854.775808",
        ),
        (
            parse_quantity_micro_units,
            format_quantity_micro_units,
            "9223372036854.775807",
            "-9223372036854.775808",
        ),
        (
            parse_custom_number_nano_units,
            format_custom_number_nano_units,
            "9223372036.854775807",
            "-9223372036.854775808",
        ),
    ],
)
def test_signed_scaled_values_reach_both_storage_bounds(parser, formatter, maximum, minimum):
    assert parser(maximum) == INT64_MAX
    assert parser(minimum) == INT64_MIN
    assert formatter(INT64_MAX) == maximum
    assert formatter(INT64_MIN) == minimum


@pytest.mark.parametrize(
    ("parser", "bad"),
    [
        (parse_percentage_millionths, "9223372036854.775808"),
        (parse_percentage_millionths, "-9223372036854.775809"),
        (parse_quantity_micro_units, "9223372036854.775808"),
        (parse_custom_number_nano_units, "9223372036.854775808"),
        (parse_custom_number_nano_units, "-9223372036.854775809"),
    ],
)
def test_scaled_parsers_report_storage_overflow(parser, bad):
    with pytest.raises(BookflowError) as exc:
        parser(bad)
    assert exc.value.code == "E_VALUE_RANGE"


def test_extreme_decimal_text_is_classified_without_native_integer_failure():
    assert parse_custom_number_nano_units("0" * 5_000 + "1") == 1_000_000_000
    with pytest.raises(BookflowError) as exc:
        parse_custom_number_nano_units("9" * 5_000)
    assert exc.value.code == "E_VALUE_RANGE"


def test_unit_factor_is_positive_and_bounded():
    assert parse_unit_factor_nano_units("0.000000001") == 1
    assert parse_unit_factor_nano_units("9223372036.854775807") == INT64_MAX
    for bad in ("0", "-0", "-1", "9223372036.854775808"):
        with pytest.raises(BookflowError) as exc:
            parse_unit_factor_nano_units(bad)
        assert exc.value.code == "E_VALUE_RANGE"

    for bad in (0, INT64_MAX + 1):
        with pytest.raises(BookflowError) as exc:
            format_unit_factor_nano_units(bad)
        assert exc.value.code == "E_VALUE_RANGE"


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (1, 2, 0),
        (3, 2, 2),
        (5, 2, 2),
        (7, 2, 4),
        (-1, 2, 0),
        (-3, 2, -2),
        (-5, 2, -2),
        (-7, 2, -4),
        (5, -2, -2),
    ],
)
def test_integer_ratio_rounds_half_even(numerator, denominator, expected):
    assert round_ratio_half_even(numerator, denominator) == expected


def test_ratio_rejects_invalid_integer_inputs():
    for numerator, denominator in ((True, 1), (1, False), (1.0, 1), (1, 0)):
        with pytest.raises(BookflowError) as exc:
            round_ratio_half_even(numerator, denominator)
        assert exc.value.code == "E_VALIDATION"


def test_unit_conversion_rounds_one_nonterminating_ratio_once():
    one = parse_quantity_micro_units("1")
    factor_one = parse_unit_factor_nano_units("1")
    factor_three = parse_unit_factor_nano_units("3")

    one_third = convert_quantity_micro_units(one, factor_one, factor_three)
    two_thirds = convert_quantity_micro_units(one * 2, factor_one, factor_three)
    assert format_quantity_micro_units(one_third) == "0.333333"
    assert format_quantity_micro_units(two_thirds) == "0.666667"

    assert convert_quantity_micro_units(1, factor_one, factor_one * 2) == 0
    assert convert_quantity_micro_units(3, factor_one, factor_one * 2) == 2
    assert convert_quantity_micro_units(-3, factor_one, factor_one * 2) == -2


def test_bom_component_extensions_round_each_tie_to_even():
    half = parse_quantity_micro_units("0.5")
    assert bom_component_extension_minor_units(half, 5) == 2
    assert bom_component_extension_minor_units(half, 7) == 4
    assert bom_component_extension_minor_units(-half, 5) == -2
    assert bom_component_extension_minor_units(-half, 7) == -4


def test_arithmetic_reports_result_overflow():
    with pytest.raises(BookflowError) as conversion:
        convert_quantity_micro_units(INT64_MAX, INT64_MAX, 1)
    assert conversion.value.code == "E_VALUE_RANGE"

    with pytest.raises(BookflowError) as extension:
        bom_component_extension_minor_units(INT64_MAX, INT64_MAX)
    assert extension.value.code == "E_VALUE_RANGE"


@pytest.mark.parametrize(
    "formatter",
    [
        format_percentage_millionths,
        format_quantity_micro_units,
        format_custom_number_nano_units,
    ],
)
def test_formatters_reject_wrong_types_and_overflow(formatter):
    for bad, code in ((True, "E_VALIDATION"), (1.0, "E_VALIDATION"), (INT64_MAX + 1, "E_VALUE_RANGE")):
        with pytest.raises(BookflowError) as exc:
            formatter(bad)
        assert exc.value.code == code
