"""Bounded journal inputs and exact domestic money validation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date as calendar_date
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX, _parse_scaled_decimal, _require_i64
from bookflow.core.money import Money, is_currency, minor_units_of

__all__ = [
    "MoneyInput", "JournalLineInput", "JournalPostInput", "JournalUpdateInput",
    "JournalVoidInput", "JournalShowInput", "JournalQueryInput", "JournalHistoryInput",
    "parse_domestic_amount", "checked_sum",
]

_AMOUNT = re.compile(r"\s*(-?[0-9]+(?:\.[0-9]+)?)\s*([A-Z]{3})?\s*")


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})


def _parse_string(value: str, home_currency: str, field: str) -> Money:
    match = _AMOUNT.fullmatch(value)
    if match is None:
        raise _invalid(field, "must be a plain decimal string with an optional currency code")
    number, explicit_currency = match.groups()
    currency = explicit_currency or home_currency
    if not is_currency(currency):
        raise _invalid(field, "unknown currency code")
    if currency != home_currency:
        raise _invalid(field, "foreign currency amounts are not supported; use home currency")
    places = minor_units_of(currency)
    fraction = number.partition(".")[2]
    if len(fraction) > places:
        raise BookflowError("E_AMOUNT_PRECISION", details={
            "field": field, "currency": currency,
            "allowed_places": places, "given_places": len(fraction),
        })
    units = _parse_scaled_decimal(number, scale=places, field=field, positive=True)
    return Money(units, currency)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MoneyInput(_Input):
    """Integer money with an optional, consistent decimal representation."""

    minor_units: int = Field(strict=True, gt=0, le=INT64_MAX)
    currency: str = Field(min_length=3, max_length=3)
    amount: str | None = None

    @model_validator(mode="after")
    def consistent_amount(self) -> Self:
        if not is_currency(self.currency):
            raise ValueError("unknown currency code")
        if "amount" in self.model_fields_set:
            if self.amount is None:
                raise ValueError("supplied amount must be a decimal string")
            try:
                parsed = _parse_string(self.amount, self.currency, "amount")
            except BookflowError as exc:
                raise ValueError(str(exc)) from exc
            if parsed.minor_units != self.minor_units:
                raise ValueError("amount contradicts minor_units")
        return self


def parse_domestic_amount(value: Any, home_currency: str, field: str = "amount") -> Money:
    """Return positive i64 home-currency money without numeric coercion.

    Inputs are decimal strings, MoneyInput objects or dictionaries of that shape.
    Precision errors use E_AMOUNT_PRECISION, bounds use E_VALUE_RANGE, and
    malformed or foreign inputs use E_VALIDATION.
    """
    if not is_currency(home_currency):
        raise _invalid(field, "unknown home currency code")
    if isinstance(value, str):
        return _parse_string(value, home_currency, field)
    if isinstance(value, MoneyInput):
        value = value.model_dump(exclude_unset=True)
    if not isinstance(value, dict):
        raise _invalid(field, "must be a decimal string or integer money object; never a number or boolean")
    if set(value) - {"minor_units", "currency", "amount"} or not {"minor_units", "currency"} <= set(value):
        raise _invalid(field, "money object requires minor_units and currency, with only optional amount")
    units = _require_i64(value["minor_units"], field=field, positive=True)
    currency = value["currency"]
    if not is_currency(currency):
        raise _invalid(field, "unknown currency code")
    if currency != home_currency:
        raise _invalid(field, "foreign currency amounts are not supported; use home currency")
    if "amount" in value:
        if not isinstance(value["amount"], str):
            raise _invalid(field, "supplied amount must be a decimal string")
        if _parse_string(value["amount"], currency, field).minor_units != units:
            raise _invalid(field, "amount contradicts minor_units")
    return Money(units, currency)


def checked_sum(values: Iterable[int], field: str) -> int:
    """Sum signed i64 values exactly and require a signed i64 result."""
    total = sum(_require_i64(value, field=field) for value in values)
    return _require_i64(total, field=field)


def _iso_date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("must be an ISO date YYYY-MM-DD")
    calendar_date.fromisoformat(value)
    return value


def _trim(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


_Date = Annotated[str, Field(min_length=10, max_length=10, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"), BeforeValidator(_iso_date)]
_Selector = Annotated[str, Field(min_length=1), BeforeValidator(_trim)]
_Number = Annotated[str, Field(min_length=1, max_length=64), BeforeValidator(_trim)]
_Version = Annotated[int, Field(strict=True, ge=1)]


class JournalLineInput(_Input):
    line_id: _Selector | None = None
    account: _Selector
    side: Literal["debit", "credit"]
    amount: str | MoneyInput
    name_type: Literal["customer", "vendor", "employee", "other_name"] | None = None
    name_id: _Selector | None = None
    class_id: _Selector | None = None
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def paired_party(self) -> Self:
        if (self.name_type is None) != (self.name_id is None):
            raise ValueError("name_type and name_id must be supplied together")
        return self


_Lines = Annotated[list[JournalLineInput], Field(min_length=2, max_length=200)]


class JournalPostInput(_Input):
    date: _Date
    number: _Number | None = None
    memo: str | None = Field(default=None, max_length=2000)
    lines: _Lines

    @model_validator(mode="after")
    def new_line_ids(self) -> Self:
        if any("line_id" in line.model_fields_set for line in self.lines):
            raise ValueError("line_id cannot be supplied when posting a new journal")
        return self


class JournalUpdateInput(_Input):
    journal: _Selector
    expected_version: _Version | None = None
    date: _Date | None = None
    number: _Number | None = None
    memo: str | None = Field(default=None, max_length=2000)
    lines: _Lines | None = None
    refresh_defaults: bool = False

    @model_validator(mode="after")
    def required_when_supplied(self) -> Self:
        for field in ("date", "number", "lines"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        return self


class JournalVoidInput(_Input):
    journal: _Selector
    expected_version: _Version | None = None


class JournalShowInput(_Input):
    journal: _Selector
    revision_number: _Version | None = None


class _PageInput(_Input):
    limit: int = Field(default=50, strict=True, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)


class JournalQueryInput(_PageInput):
    date_from: _Date | None = None
    date_to: _Date | None = None
    status: Literal["posted", "voided"] | None = None
    query: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def ordered_dates(self) -> Self:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class JournalHistoryInput(_PageInput):
    journal: _Selector
