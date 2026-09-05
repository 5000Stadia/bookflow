"""Exact sales arithmetic; intermediate ratios use unbounded Python integers."""
from bookflow.core.exact import INT64_MAX, _require_i64, round_ratio_half_even
from bookflow.company.sales_models import _invalid


def nonnegative(value, field):
    _require_i64(value, field=field)
    if value < 0:
        raise _invalid(field, "must be nonnegative")
    return value


def extension(quantity_microunits, price_minor_units):
    return nonnegative(round_ratio_half_even(quantity_microunits * price_minor_units, 1_000_000), "line.net")


def base_quantity(quantity_microunits, factor_nanounits):
    value = round_ratio_half_even(quantity_microunits * factor_nanounits, 1_000_000_000)
    return _require_i64(value, field="base_quantity", positive=True)


def tax(net_minor_units, percent_millionths):
    return nonnegative(round_ratio_half_even(net_minor_units * percent_millionths, 100_000_000), "line.tax")


def selected_price(base_minor_units, factor_nanounits):
    return nonnegative(round_ratio_half_even(base_minor_units * factor_nanounits, 1_000_000_000), "unit_price")


def adjusted_price(base_minor_units, percent_millionths, increment_minor_units=1,
                   offset_minor_units=0, rounding_mode="nearest"):
    """Adjust and round (price-offset)/increment once, then restore the offset."""
    nonnegative(base_minor_units, "price_basis")
    _require_i64(percent_millionths, field="percent")
    if not -100_000_000 <= percent_millionths <= 1_000_000_000_000:
        raise _invalid("percent", "outside supported price-level range")
    _require_i64(increment_minor_units, field="increment", positive=True)
    _require_i64(offset_minor_units, field="offset")
    numerator = base_minor_units * (100_000_000 + percent_millionths) - offset_minor_units * 100_000_000
    denominator = increment_minor_units * 100_000_000
    if rounding_mode == "nearest":
        steps = round_ratio_half_even(numerator, denominator)
    elif rounding_mode == "up":
        steps = -(-numerator // denominator)
    elif rounding_mode == "down":
        steps = numerator // denominator
    else:
        raise _invalid("rounding_mode", "must be nearest, up or down")
    return nonnegative(steps * increment_minor_units + offset_minor_units, "unit_price")


def total(values, field="total"):
    return nonnegative(sum(values), field)
