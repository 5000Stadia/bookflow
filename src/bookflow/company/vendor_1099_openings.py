"""What a 1099 vendor was paid in a year before the company's books began here.

A company that moves to Bookflow in the middle of a year brings each 1099 vendor's payments so far
from the old books' 1099 Summary, so the year's 1099 summary here is the whole year's. Bookflow holds
no payment history from before the move-in, so the amount is kept as one figure per vendor and year,
paid from January 1 through `as_of`: the 1099 summary adds it to the vendor's payments when its dates
include that day, as it would an opening balance dated the cutover. `vendor 1099-opening` sets it,
and setting it again replaces it; 0.00 clears it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pydantic import Field, field_validator, model_validator

from bookflow.company import schema
from bookflow.company.journal_models import MoneyInput
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched


class Vendor1099OpeningInput(StrictModel):
    vendor: str = Field(min_length=1, max_length=1004, description=(
        "The vendor: ID or name. It must be marked eligible for a 1099 (`vendor update` eligible_1099)."))
    year: int = Field(ge=1900, le=9999, description="The calendar year the payments were made in.")
    as_of: str = Field(min_length=10, max_length=10, description=(
        "The last day the amount covers, YYYY-MM-DD in `year`: what was paid from January 1 through this day, usually the "
        "day before the books began here. The 1099 summary counts the amount when its dates include this day."))
    amount: str | MoneyInput = Field(description=(
        "What was paid, as the old books' 1099 Summary shows it: a decimal string in the home currency or exact minor "
        "units. 0.00 clears it."))
    expected_version: int | None = Field(None, ge=0, description=(
        "The version you read, 0 when none is set; given, the change is refused if the amount has changed since."))

    _date = field_validator("as_of")(iso_date)

    @model_validator(mode="after")
    def in_year(self):
        if self.as_of[:4] != f"{self.year:04d}":
            raise ValueError("as_of must be a day in year")
        return self


class Vendor1099OpeningOutput(StrictModel):
    id: str | None = Field(description="The opening amount; null in a dry run of a first one")
    vendor_id: str
    vendor_name: str
    year: int
    as_of: str
    amount: MoneyOutput = Field(description="What the vendor was paid from January 1 through as_of before the books began here")
    version: int = Field(description="0 when none was set and none is set now")
    changed: bool = Field(description="False when the amount already stood as given; nothing was written")


@dataclass
class Planned:
    inp: Vendor1099OpeningInput
    vendor: dict[str, Any]
    before: dict[str, Any] | None
    row: dict[str, Any] | None
    changed: bool
    currency: str


def _home(db) -> str:
    return db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one()


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})


def opening_of(db, vendor_id: str, year: int) -> dict[str, Any] | None:
    t = schema.vendor_1099_openings
    row = db.conn.execute(sa.select(t).where(t.c.vendor_id == vendor_id, t.c.year == year)).mappings().first()
    return dict(row) if row is not None else None


def openings(db) -> list[dict[str, Any]]:
    return [dict(row) for row in db.conn.execute(sa.select(schema.vendor_1099_openings)).mappings()]


def plan(s, ctx, inp: Vendor1099OpeningInput) -> Planned:
    from bookflow.company.parties import resolve_party
    from bookflow.core.money import Money
    db = s.company
    vendor = dict(resolve_party(db, "vendor", inp.vendor))
    if not vendor.get("eligible_1099"):
        raise _invalid("vendor", f"{vendor['name']} is not marked eligible for a 1099; mark it with `vendor update` "
                                 "eligible_1099 true first, or set nothing for it")
    currency = _home(db)
    try:
        amount = Money.parse(inp.amount.model_dump() if isinstance(inp.amount, MoneyInput) else inp.amount, currency)
    except BookflowError:
        raise
    except (ValueError, TypeError) as exc:
        raise _invalid("amount", str(exc)) from None
    if amount.currency != currency:
        raise _invalid("amount", "must be in the company home currency")
    if amount.minor_units < 0:
        raise _invalid("amount", "must not be negative; 0.00 clears it")
    before = opening_of(db, vendor["id"], inp.year)
    version = before["version"] if before else 0
    if inp.expected_version is not None and inp.expected_version != version:
        raise BookflowError("E_VERSION_CONFLICT", details={
            "record_type": "vendor_1099_opening", "record_id": before["id"] if before else None,
            "expected_version": inp.expected_version, "current_version": version,
            "updated_by": before["updated_by"] if before else None, "updated_at": before["updated_at"] if before else None,
            "changed_fields": ["amount", "as_of"] if before else []})
    if before is None:
        changed = amount.minor_units != 0
    else:
        changed = (before["amount_minor_units"], before["as_of"]) != (amount.minor_units, inp.as_of)
    row = None
    if changed:
        at = clock.now_iso()
        row = dict(id=before["id"] if before else new_id(), version=version + 1, vendor_id=vendor["id"], year=inp.year,
                   as_of=inp.as_of, amount_minor_units=amount.minor_units, currency=currency,
                   created_at=before["created_at"] if before else at, created_by=before["created_by"] if before else s.actor.id,
                   created_via=before["created_via"] if before else ctx.interface.value,
                   updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value, audit_event_id=None)
    return Planned(inp, vendor, before, row, changed, currency)


def output(planned: Planned, *, written: bool) -> Vendor1099OpeningOutput:
    """The amount as the change leaves it; a dry run shows it as it would, with no id for a first one."""
    vendor, shown = planned.vendor, planned.row if planned.changed else planned.before
    if shown is None:  # nothing was set, and 0.00 sets nothing
        return Vendor1099OpeningOutput(id=None, vendor_id=vendor["id"], vendor_name=vendor["name"], year=planned.inp.year,
                                       as_of=planned.inp.as_of, amount=money(0, planned.currency), version=0, changed=False)
    return Vendor1099OpeningOutput(
        id=shown["id"] if (written or planned.before) else None, vendor_id=vendor["id"], vendor_name=vendor["name"],
        year=shown["year"], as_of=shown["as_of"], amount=money(shown["amount_minor_units"], planned.currency),
        version=shown["version"], changed=planned.changed)


def apply(s, ctx, planned: Planned, command: str) -> tuple[Vendor1099OpeningOutput, list[Touched]]:
    db, row, before = s.company, dict(planned.row), planned.before
    event = new_id()
    row["audit_event_id"] = event
    shown = lambda r: {k: v for k, v in r.items() if k != "audit_event_id"}
    touched = [Touched("vendor_1099_opening", row["id"], "update" if before else "create",
                       before["version"] if before else None, row["version"], shown(row), shown(before) if before else None,
                       db="company")]
    amount = money(row["amount_minor_units"], planned.currency).amount
    audit.write_event_to(db, ctx, command, f"Set {planned.vendor['name']}'s {row['year']} 1099 payments before the books "
                         f"began here to {amount} (through {row['as_of']})", touched,
                         actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    t = schema.vendor_1099_openings
    if before:
        db.conn.execute(t.update().where(t.c.id == row["id"]).values(**{k: v for k, v in row.items() if k != "id"}))
    else:
        db.conn.execute(t.insert().values(**row))
    planned.row = row
    return output(planned, written=True), touched
