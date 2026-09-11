"""Company-local dates for a memorized schedule: what today is, and what the next slot is.

Three decisions live here, written down rather than left to emerge from whatever ``date``
arithmetic happened to be convenient.

**Today is the company's today.** A slot comes due against the current date *in the company's
own timezone*, never the host's. A host in UTC at 02:00 on 1 March is still on 28 February for
a company in Los Angeles, so a slot dated 1 March is not due there yet. ``today`` is the only
place that decision is made.

**The 31st in a 30-day month is the 30th, and the 31st again next month.** A month-family
schedule keeps an **anchor day** -- the day of the month its start date fell on -- and clamps
that day to the last day of whatever month it lands in. Clamping is applied at the target, not
stored back, so 31 January -> 28 February -> 31 March. Storing the clamped day would quietly
turn a month-end schedule into a 28th-of-the-month schedule after one February.

**Twice a month is the anchor day and the anchor day plus fifteen**, each clamped the same way,
and the next slot is simply the smallest candidate strictly after the current one. Where both
candidates clamp onto the same day -- an anchor of 28 in February -- that month yields one slot
rather than two identical ones, because two occurrences cannot share a nominal slot date.

Stop conditions are counted, not inferred: a schedule ends when its next slot would fall after
``stop_date``, or when its remaining count reaches zero. The remaining count is decremented by
a successful scheduled entry and by nothing else -- not by a blocked attempt, not by a skip,
not by an extra entry made by hand -- so a template promised twelve entries makes twelve.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bookflow.company.memorized_schema import FREQUENCIES


def today(zone: str | None, at: datetime | None = None) -> str:
    """The current company-local date as an ISO date string."""
    from bookflow.core import clock
    moment = at or clock.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if zone:
        try:
            moment = moment.astimezone(ZoneInfo(zone))
        except Exception:
            moment = moment.astimezone(timezone.utc)
    return moment.date().isoformat()


def parse(value: str) -> date:
    return date.fromisoformat(value)


def _clamp(year: int, month: int, day: int) -> date:
    """The anchor day in this month, or the last day of it when the month is shorter."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(anchor: date, months: int, anchor_day: int) -> date:
    total = (anchor.year * 12 + anchor.month - 1) + months
    return _clamp(total // 12, total % 12 + 1, anchor_day)


def _semimonthly_candidates(year: int, month: int, anchor_day: int) -> list[date]:
    first = _clamp(year, month, anchor_day)
    second = _clamp(year, month, anchor_day + 15)
    return [first] if second == first else [first, second]


def anchor_day_for(frequency: str, start: str | None) -> int | None:
    """The day of the month a month-family schedule keeps; None for the others."""
    unit, _ = FREQUENCIES[frequency]
    if unit in ('month', 'semimonth') and start:
        return parse(start).day
    return None


def advance(frequency: str, current: str, anchor_day: int | None) -> str | None:
    """The nominal slot strictly after ``current``, or None when the schedule never advances."""
    unit, count = FREQUENCIES[frequency]
    if unit is None:
        return None
    at = parse(current)
    if unit == 'day':
        return (at + timedelta(days=count)).isoformat()
    day = anchor_day or at.day
    if unit == 'month':
        return _add_months(at, count, day).isoformat()
    # semimonth: this month's remaining candidate, else the first of the next month's pair
    for candidate in _semimonthly_candidates(at.year, at.month, day):
        if candidate > at:
            return candidate.isoformat()
    following = _add_months(date(at.year, at.month, 1), 1, 1)
    return _semimonthly_candidates(following.year, following.month, day)[0].isoformat()


def due_date(slot: str, days_in_advance: int) -> str:
    """When a slot becomes enterable. Never the accounting date, which stays the slot date."""
    return (parse(slot) - timedelta(days=max(0, days_in_advance))).isoformat()


def finished(next_date: str | None, stop_date: str | None, remaining_count: int | None) -> bool:
    """Whether a schedule has run out: past its stop date, or out of promised entries."""
    if next_date is None:
        return True
    if remaining_count is not None and remaining_count <= 0:
        return True
    return bool(stop_date and parse(next_date) > parse(stop_date))


def slots_due(frequency: str, next_date: str | None, anchor_day: int | None, stop_date: str | None,
              remaining_count: int | None, days_in_advance: int, as_of: str, limit: int) -> list[str]:
    """Every nominal slot from ``next_date`` onward whose due date has arrived, oldest first.

    A backlog is a list, not one entry: a company that has not run for a month owes every slot
    in that month, each at its own accounting date.
    """
    found: list[str] = []
    slot, left = next_date, remaining_count
    while slot is not None and len(found) < limit:
        if finished(slot, stop_date, left):
            break
        if due_date(slot, days_in_advance) > as_of:
            break
        found.append(slot)
        if left is not None:
            left -= 1
        slot = advance(frequency, slot, anchor_day)
    return found
