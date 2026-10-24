"""Exact unit prices and costs finer than a minor unit (R150).

A unit price or unit cost may carry three places beyond its currency's minor unit: five
decimal places for a two-place currency, as the anchor allows (fuel at 3.459, a fitting at
0.0325). Amounts stay in whole minor units: a line amount is quantity x unit price, rounded
once, half to even.

Inside the code a price is an integer of *price units*, thousandths of a minor unit
(``pu = minor * 1000 + fraction``). Storage keeps two columns so whole-cent rows never change:
``<prefix>_minor_units`` holds the price rounded half to even to the minor unit, and
``<prefix>_fraction`` holds the signed remainder in thousandths of a minor unit, NULL when the
price is whole. No float or Decimal ever holds a price.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any

from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX, round_ratio_half_even
from bookflow.core.money import is_currency, minor_units_of

EXTRA_PLACES = 3
SCALE = 10**EXTRA_PLACES

_TEXT = re.compile(r"\s*(-?[0-9]+)(?:\.([0-9]+))?\s*([A-Z]{3})?\s*")


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})


def places(currency: str) -> int:
    """The most decimal places a unit price may carry in ``currency`` (5 for USD)."""
    return minor_units_of(currency) + EXTRA_PLACES


def from_minor(minor_units: int | None) -> int | None:
    return None if minor_units is None else int(minor_units) * SCALE


def join(minor_units: int | None, fraction: int | None) -> int | None:
    """Price units from the two stored columns; None when the price is absent."""
    if minor_units is None:
        return None
    return int(minor_units) * SCALE + int(fraction or 0)


def exact(minor_units: int | None, fraction: int | None) -> Fraction | None:
    """THE exact price in minor units from the two stored columns (the one accessor).

    Every calculation that reads a stored price or cost goes through this (or ``join``, its
    integer form in price units): reading ``<prefix>_minor_units`` alone silently loses the
    fraction (1000 x 0.0125 would read 1 cent and give 10.00 instead of 12.50).
    """
    units = join(minor_units, fraction)
    return None if units is None else Fraction(units, SCALE)


def row_units(row: Any, prefix: str) -> int | None:
    """Price units from a row mapping holding ``<prefix>_minor_units`` and maybe ``<prefix>_fraction``."""
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    return join(get(f"{prefix}_minor_units"), get(f"{prefix}_fraction"))


def split(price_units: int | None) -> tuple[int | None, int | None]:
    """(minor_units rounded half to even, signed fraction or None) for storage."""
    if price_units is None:
        return None, None
    minor = round_ratio_half_even(price_units, SCALE)
    fraction = price_units - minor * SCALE
    return minor, (fraction or None)


def nearest_minor(price_units: int) -> int:
    return round_ratio_half_even(price_units, SCALE)


def is_whole(price_units: int | None) -> bool:
    return price_units is None or price_units % SCALE == 0


def text(price_units: int, currency: str) -> str:
    """Exact decimal text, trailing zeros trimmed to the currency's places: '3.459', '12.50'."""
    digits = minor_units_of(currency)
    total = digits + EXTRA_PLACES
    sign = "-" if price_units < 0 else ""
    whole, frac = divmod(abs(price_units), 10**total)
    if total == 0:
        return f"{sign}{whole}"
    frac_text = f"{frac:0{total}d}"
    keep = max(digits, len(frac_text.rstrip("0")))
    frac_text = frac_text[:keep]
    return f"{sign}{whole}.{frac_text}" if frac_text else f"{sign}{whole}"


def to_dict(price_units: int, currency: str) -> dict[str, Any]:
    """The MoneyOutput shape: exact ``amount`` text, ``minor_units`` the nearest minor unit.

    A whole-minor price gives exactly ``Money(minor, currency).to_dict()``.
    """
    return {"amount": text(price_units, currency), "currency": currency,
            "minor_units": nearest_minor(price_units)}


def parse_text(value: str, currency: str, field: str, *, signed: bool = False) -> int:
    """Price units from '3.459' or '3.459 USD'; more than ``places`` decimals is refused."""
    match = _TEXT.fullmatch(value) if isinstance(value, str) else None
    if not match or match[3] not in (None, currency):
        raise _invalid(field, "use an exact home-currency decimal string")
    total = places(currency)
    fraction_text = match[2] or ""
    if len(fraction_text) > total:
        raise BookflowError("E_AMOUNT_PRECISION", details={
            "field": field, "currency": currency, "allowed_places": total,
            "given_places": len(fraction_text),
            "problem": f"a unit price or cost may have at most {total} decimal places"})
    whole = match[1]
    negative = whole.startswith("-")
    digits = whole.lstrip("-").lstrip("0") or "0"
    if len(digits) > len(str(INT64_MAX // 10**total)):
        raise BookflowError("E_VALUE_RANGE", details={"field": field})
    units = int(digits) * 10**total + int(fraction_text.ljust(total, "0") or 0)
    units = -units if negative else units
    if units < 0 and not signed:
        raise _invalid(field, "must be nonnegative")
    # Stored minor units must still fit signed 64 bits.
    if abs(round_ratio_half_even(units, SCALE)) > INT64_MAX:
        raise BookflowError("E_VALUE_RANGE", details={"field": field})
    return units


def parse(value: Any, currency: str, field: str = "unit_price", *, signed: bool = False) -> int:
    """Price units from text or the money object form ({amount, currency[, minor_units]}).

    The object form's ``minor_units`` alone is a whole-minor price; with ``amount`` it must be
    the amount's nearest minor unit, so an output echoed back as input is accepted.
    """
    if not is_currency(currency):
        raise _invalid(field, "unknown home currency")
    if isinstance(value, (bool, float)) or value is None:
        raise _invalid(field, "use an exact home-currency decimal string")
    if isinstance(value, str):
        return parse_text(value, currency, field, signed=signed)
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_unset=True)
    if isinstance(value, dict):
        if value.get("currency") != currency:
            raise _invalid(field, "use exact home-currency money")
        minor = value.get("minor_units")
        if minor is not None and (isinstance(minor, bool) or not isinstance(minor, int)):
            raise _invalid(field, "minor_units must be an integer")
        amount = value.get("amount")
        if amount is None:
            if minor is None:
                raise _invalid(field, "object needs amount or minor_units")
            if minor < 0 and not signed:
                raise _invalid(field, "must be nonnegative")
            return minor * SCALE
        units = parse_text(amount, currency, field, signed=signed)
        if minor is not None and minor != nearest_minor(units):
            raise _invalid(field, "amount must agree with minor_units")
        return units
    raise _invalid(field, "use exact home-currency money")


def extension(quantity_microunits: int, price_units: int) -> int:
    """quantity x unit price in minor units, rounded once, half to even."""
    return round_ratio_half_even(quantity_microunits * price_units, 1_000_000 * SCALE)
