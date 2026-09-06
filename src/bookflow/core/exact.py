"""Exact fixed-point scalars and integer half-even arithmetic.

All public decimal values enter and leave this module as strings. Stored values
are scaled signed 64-bit integers; no binary float or Decimal conversion is
used.
"""

from __future__ import annotations

import re
from typing import Any

from bookflow.core.errors import BookflowError


INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

PERCENTAGE_SCALE = 6
QUANTITY_SCALE = 6
NANO_SCALE = 9

_DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _validation(field: str, problem: str) -> BookflowError:
    return BookflowError(
        "E_VALIDATION",
        details={"fields": [{"field": field, "problem": problem}]},
    )


def _range_error(field: str, problem: str) -> BookflowError:
    return BookflowError(
        "E_VALUE_RANGE",
        details={"fields": [{"field": field, "problem": problem}]},
    )


def _require_i64(value: Any, *, field: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _validation(field, "must be a scaled integer")
    if value < INT64_MIN or value > INT64_MAX:
        raise _range_error(field, "does not fit signed 64-bit storage")
    if positive and value <= 0:
        raise _range_error(field, "must be greater than zero")
    return value


def _parse_scaled_decimal(
    value: Any,
    *,
    scale: int,
    field: str,
    positive: bool = False,
) -> int:
    if not isinstance(value, str):
        raise _validation(field, "must be a decimal string, never a number or boolean")
    if not _DECIMAL_RE.fullmatch(value):
        raise _validation(field, "must be a plain decimal string")

    negative = value.startswith("-")
    unsigned = value[1:] if negative else value
    whole_text, separator, fraction_text = unsigned.partition(".")
    if separator and len(fraction_text) > scale:
        raise _validation(field, f"must have at most {scale} fractional digits")

    significant_whole = whole_text.lstrip("0") or "0"
    maximum_whole_digits = len(str(INT64_MAX // 10**scale))
    if len(significant_whole) > maximum_whole_digits:
        raise _range_error(field, "does not fit signed 64-bit storage")

    coefficient = int(significant_whole) * 10**scale
    if separator:
        coefficient += int(fraction_text.ljust(scale, "0"))
    if negative:
        coefficient = -coefficient

    return _require_i64(coefficient, field=field, positive=positive)


def _format_scaled_decimal(
    coefficient: Any,
    *,
    scale: int,
    field: str,
    positive: bool = False,
) -> str:
    stored = _require_i64(coefficient, field=field, positive=positive)
    if stored == 0:
        return "0"

    sign = "-" if stored < 0 else ""
    whole, fraction = divmod(abs(stored), 10**scale)
    if fraction == 0:
        return f"{sign}{whole}"
    fraction_text = f"{fraction:0{scale}d}".rstrip("0")
    return f"{sign}{whole}.{fraction_text}"


def parse_percentage_millionths(value: Any, *, field: str = "percentage") -> int:
    """Parse signed percentage points with at most six fractional digits."""

    return _parse_scaled_decimal(value, scale=PERCENTAGE_SCALE, field=field)


def format_percentage_millionths(
    coefficient: Any, *, field: str = "percentage"
) -> str:
    """Format a stored percentage coefficient as a canonical decimal string."""

    return _format_scaled_decimal(coefficient, scale=PERCENTAGE_SCALE, field=field)


def parse_quantity_micro_units(value: Any, *, field: str = "quantity") -> int:
    """Parse a signed quantity with at most six fractional digits; .5 is 0.5."""

    if isinstance(value, str):
        if value.startswith("."):
            value = "0" + value
        elif value.startswith("-."):
            value = "-0" + value[1:]
    return _parse_scaled_decimal(value, scale=QUANTITY_SCALE, field=field)


def format_quantity_micro_units(
    coefficient: Any, *, field: str = "quantity"
) -> str:
    """Format a stored quantity coefficient as a canonical decimal string."""

    return _format_scaled_decimal(coefficient, scale=QUANTITY_SCALE, field=field)


def parse_unit_factor_nano_units(value: Any, *, field: str = "base_factor") -> int:
    """Parse a strictly positive unit factor with at most nine fractional digits."""

    return _parse_scaled_decimal(value, scale=NANO_SCALE, field=field, positive=True)


def format_unit_factor_nano_units(
    coefficient: Any, *, field: str = "base_factor"
) -> str:
    """Format a stored positive unit factor as a canonical decimal string."""

    return _format_scaled_decimal(
        coefficient,
        scale=NANO_SCALE,
        field=field,
        positive=True,
    )


def parse_custom_number_nano_units(value: Any, *, field: str = "value") -> int:
    """Parse a signed custom number with at most nine fractional digits."""

    return _parse_scaled_decimal(value, scale=NANO_SCALE, field=field)


def format_custom_number_nano_units(
    coefficient: Any, *, field: str = "value"
) -> str:
    """Format a stored custom number as a canonical decimal string."""

    return _format_scaled_decimal(coefficient, scale=NANO_SCALE, field=field)


def round_ratio_half_even(numerator: Any, denominator: Any) -> int:
    """Round an integer ratio to the nearest integer, resolving ties to even."""

    if isinstance(numerator, bool) or not isinstance(numerator, int):
        raise _validation("numerator", "must be an integer")
    if isinstance(denominator, bool) or not isinstance(denominator, int):
        raise _validation("denominator", "must be an integer")
    if denominator == 0:
        raise _validation("denominator", "must not be zero")

    negative = (numerator < 0) != (denominator < 0)
    quotient, remainder = divmod(abs(numerator), abs(denominator))
    doubled_remainder = remainder * 2
    if doubled_remainder > abs(denominator) or (
        doubled_remainder == abs(denominator) and quotient % 2 == 1
    ):
        quotient += 1
    return -quotient if negative and quotient else quotient


def convert_quantity_micro_units(
    quantity_micro_units: Any,
    source_factor_nano_units: Any,
    target_factor_nano_units: Any,
) -> int:
    """Convert a stored quantity and round once to six decimal places."""

    quantity = _require_i64(
        quantity_micro_units,
        field="quantity",
    )
    source_factor = _require_i64(
        source_factor_nano_units,
        field="source_base_factor",
        positive=True,
    )
    target_factor = _require_i64(
        target_factor_nano_units,
        field="target_base_factor",
        positive=True,
    )
    converted = round_ratio_half_even(quantity * source_factor, target_factor)
    return _require_i64(converted, field="quantity")


def bom_component_extension_minor_units(
    quantity_micro_units: Any,
    unit_cost_minor_units: Any,
) -> int:
    """Round one quantity-times-cost extension to currency minor units."""

    quantity = _require_i64(quantity_micro_units, field="quantity")
    unit_cost = _require_i64(unit_cost_minor_units, field="unit_cost_minor_units")
    extension = round_ratio_half_even(quantity * unit_cost, 10**QUANTITY_SCALE)
    return _require_i64(extension, field="extension_minor_units")
