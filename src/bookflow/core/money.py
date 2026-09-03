"""Exact money: integer minor units plus an ISO 4217 code. Never a float."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Any

from bookflow.core.errors import BookflowError


def _load_currencies() -> dict[str, tuple[int, str]]:
    table: dict[str, tuple[int, str]] = {}
    with resources.files("bookflow.data").joinpath("currencies.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            table[row["code"]] = (int(row["minor_units"]), row["name"])
    return table


CURRENCIES: dict[str, tuple[int, str]] = _load_currencies()

_AMOUNT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([A-Z]{3})?\s*$")


def is_currency(code: Any) -> bool:
    return isinstance(code, str) and code in CURRENCIES


def minor_units_of(code: str) -> int:
    if not is_currency(code):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "currency", "problem": f"unknown currency code {code!r}"}]})
    return CURRENCIES[code][0]


@dataclass(frozen=True, slots=True)
class Money:
    minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise TypeError("minor_units must be an int, never a float or bool")
        if not is_currency(self.currency):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "currency", "problem": f"unknown currency code {self.currency!r}"}]})

    @property
    def amount(self) -> str:
        """Decimal string with the currency's number of places, e.g. '12.50'."""
        places = CURRENCIES[self.currency][0]
        sign = "-" if self.minor_units < 0 else ""
        units = abs(self.minor_units)
        if places == 0:
            return f"{sign}{units}"
        whole, frac = divmod(units, 10**places)
        return f"{sign}{whole}.{frac:0{places}d}"

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency, "minor_units": self.minor_units}

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    @classmethod
    def parse(cls, value: Any, default_currency: str | None = None) -> "Money":
        """Parse '12.50', '12.50 USD', '2345 JPY', or the JSON object form.

        A string without a code takes ``default_currency``; when neither is given
        the result is E_VALIDATION. Floats and bools are rejected outright.
        """
        if isinstance(value, Money):
            return value
        if isinstance(value, bool) or isinstance(value, float):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": "amounts must be decimal strings, never floats"}]})
        if isinstance(value, dict):
            if "minor_units" in value and "currency" in value:
                mu = value["minor_units"]
                if isinstance(mu, bool) or not isinstance(mu, int):
                    raise BookflowError("E_VALIDATION", details={"fields": [{"field": "minor_units", "problem": "must be an integer"}]})
                return cls(mu, value["currency"])
            if "amount" in value:
                if isinstance(value["amount"], (float, bool)):
                    raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": "amounts must be decimal strings, never floats"}]})
                return cls.parse(str(value["amount"]), value.get("currency", default_currency))
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": "object needs amount and currency"}]})
        if isinstance(value, int):
            value = str(value)
        if not isinstance(value, str):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": f"cannot parse {type(value).__name__}"}]})
        m = _AMOUNT_RE.match(value)
        if not m:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": f"not an amount: {value!r}"}]})
        number, code = m.group(1), m.group(2) or default_currency
        if code is None:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": "no currency code given and no default applies"}]})
        places = minor_units_of(code)
        try:
            dec = Decimal(number)
        except InvalidOperation:
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": "amount", "problem": f"not a decimal: {number!r}"}]})
        exponent = -dec.as_tuple().exponent if dec.as_tuple().exponent < 0 else 0
        if exponent > places:
            raise BookflowError("E_AMOUNT_PRECISION", details={"currency": code, "allowed_places": places, "given_places": exponent})
        return cls(int(dec.scaleb(places)), code)
