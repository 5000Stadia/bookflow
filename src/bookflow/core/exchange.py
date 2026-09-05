"""Canonical manual exchange rates and exact, once-rounded money conversion."""
from __future__ import annotations

import re
from typing import Any

from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64
from bookflow.core.money import Money, minor_units_of

_RATE = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,18})?")


def canonical_rate(value: Any) -> str:
    """Validate a positive plain decimal rate and remove insignificant zeros."""
    if not isinstance(value, str) or _RATE.fullmatch(value) is None:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'rate', 'problem': 'must be a positive plain decimal string with 1–12 integer and at most 18 fractional digits'}]})
    whole, _, fraction = value.partition('.')
    whole, fraction = whole.lstrip('0') or '0', fraction.rstrip('0')
    if whole == '0' and not fraction:
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'rate', 'problem': 'must be greater than zero'}]})
    return whole + ('.' + fraction if fraction else '')


def convert_money(original: Money, home_currency: str, rate: str) -> Money:
    """Convert positive i64 money using integer intermediates and half-even rounding."""
    if not isinstance(original, Money):
        raise BookflowError('E_VALIDATION', details={'fields': [
            {'field': 'original', 'problem': 'must be Money'}]})
    units = _require_i64(original.minor_units, field='original', positive=True)
    p, h = minor_units_of(original.currency), minor_units_of(home_currency)
    whole, _, fraction = canonical_rate(rate).partition('.')
    numerator = units * int(whole + fraction) * 10**h
    denominator = 10**(p + len(fraction))
    result, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or (2 * remainder == denominator and result % 2):
        result += 1
    return Money(_require_i64(result, field='amount', positive=True), home_currency)
