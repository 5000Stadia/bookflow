"""Strict dated manual-rate command contracts."""
from __future__ import annotations

import re
from datetime import date as calendar_date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from bookflow.core.exchange import canonical_rate
from bookflow.core.money import is_currency
from bookflow.core.models import WriteOutput


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('must be an ISO date YYYY-MM-DD')
    calendar_date.fromisoformat(value)
    return value


def currency_code(value):
    if not is_currency(value):
        raise ValueError('must be a known currency code')
    return value


Date = Annotated[str, BeforeValidator(iso_date)]
Currency = Annotated[str, BeforeValidator(currency_code)]
Rate = Annotated[str, BeforeValidator(canonical_rate)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RateSetInput(StrictModel):
    date: Date = Field(description='Exact accounting date, YYYY-MM-DD.')
    from_currency: Currency = Field(description='Original currency; must differ from company home currency.')
    rate: Rate = Field(description='Positive home major units per original major unit; up to 12 integer and 18 fractional digits.')
    expected_version: int = Field(default=0, ge=0, le=9223372036854775807, description='Zero creates only; updates require the current positive version, even for a no-op.')


class RateShowInput(StrictModel):
    rate_id: str | None = Field(default=None, min_length=1, max_length=26, description='Stable rate id; use instead of date and from_currency.')
    date: Date | None = None
    from_currency: Currency | None = None

    @model_validator(mode='after')
    def selector(self) -> Self:
        if self.rate_id is not None:
            valid = self.date is None and self.from_currency is None
        else:
            valid = self.date is not None and self.from_currency is not None
        if not valid:
            raise ValueError('supply either rate_id or both date and from_currency')
        return self


class RateQueryInput(StrictModel):
    date_from: Date | None = None
    date_to: Date | None = None
    from_currency: Currency | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    @property
    def query(self):
        """No free-text search; shared query fingerprint compatibility."""
        return None

    @model_validator(mode='after')
    def date_range(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from must not follow date_to')
        return self


class RateOutput(StrictModel):
    id: str
    version: int
    date: str
    from_currency: str
    to_currency: str
    rate: str
    source: Literal['manual']
    entered_by: str
    entered_at: str


class RateWriteOutput(RateOutput, WriteOutput):
    changed: bool
    idempotent_replay: bool = False


class RatePageOutput(StrictModel):
    items: list[RateOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
