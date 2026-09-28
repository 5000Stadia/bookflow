"""The demo's dates follow the day it is reset.

`seed.toml` is written as of one day, its `[calendar] written_as_of`. `demo reset` moves every
date in the seed by the same whole number of months, the most that puts the written day on or
before the reset day, so nothing the demo records is dated after the day it was reset and all
the stock its story buys has arrived by then. Moving by whole months, not days, keeps what the
books are about: month ends stay month ends, a sale and its payment stay in the same month, a
sales-tax month keeps its sales, and the order of every pair of dates is kept (never reversed).

A date equal to `[calendar] fiscal_year_start` (the opening balances) stays the first day of a
fiscal year: it moves to the start of the fiscal year its moved date falls in, so the company
still opens at the start of a year before anything else happens.

Dates written inside text ("Accepted by telephone on 2026-09-10") move with the rest.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def reset_day(zone: str | None) -> date:
    """The day a demo reset happens, in the demo company's own timezone."""
    from bookflow.core import clock
    try:
        tz = ZoneInfo(zone) if zone else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    moment: datetime = clock.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date()


def add_months(day: date, months: int) -> date:
    """`day` moved by whole months; a month's last day stays its month's last day."""
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    last = calendar.monthrange(year, month)[1]
    if day.day == calendar.monthrange(day.year, day.month)[1]:
        return date(year, month, last)
    return date(year, month, min(day.day, last))


def months_to_move(written: date, day: date) -> int:
    """The most whole months that put `written` on or before `day` (usually negative)."""
    months = (day.year - written.year) * 12 + day.month - written.month
    while add_months(written, months) > day:
        months -= 1
    return months


def fiscal_year_start(day: date, start_month: int) -> date:
    year = day.year if day.month >= start_month else day.year - 1
    return date(year, start_month, 1)


def move_seed(seed: dict[str, Any], day: date) -> dict[str, Any]:
    """A copy of `seed` with every date moved for a reset on `day`.

    A seed without a `[calendar]` table is returned unchanged.
    """
    table = seed.get("calendar")
    if not table:
        return seed
    written = date.fromisoformat(table["written_as_of"])
    months = months_to_move(written, day)
    opening = table.get("fiscal_year_start")
    start_month = int(seed.get("company", {}).get("fiscal_year_start_month") or 1)

    def one(match: re.Match[str]) -> str:
        return add_months(date.fromisoformat(match.group(0)), months).isoformat()

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            if opening is not None and value == opening:
                return fiscal_year_start(add_months(date.fromisoformat(value), months), start_month).isoformat()
            return _DATE.sub(one, value)
        return value

    moved = dict(seed)
    for key in ("directives", "updates", "commands"):
        if key in moved:
            moved[key] = walk(moved[key])
    moved["calendar"] = {**table, "reset_day": day.isoformat(), "months_moved": months}
    return moved
