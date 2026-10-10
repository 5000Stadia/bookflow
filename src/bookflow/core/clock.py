"""The one time source. Tests replace ``now`` to move time."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from typing import Iterator


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# The day the books are being written as of, when that is not the real day. A demo reset
# writes its story as of the reset day (and the reference year as of its year end), so a
# check that asks "is this past?" must ask it of that day, not of the wall clock, or the
# same seed builds differently on different real days. Real users never set it.
_working_day: ContextVar[date | None] = ContextVar("bookflow_working_day", default=None)


def working_day() -> date | None:
    """The day set by ``as_of_day``, or None when the books are being written today."""
    return _working_day.get()


@contextmanager
def as_of_day(day: date | str | None) -> Iterator[None]:
    """Judge "today" as ``day`` inside the block (None leaves the real day in force)."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    token = _working_day.set(day)
    try:
        yield
    finally:
        _working_day.reset(token)


def today_iso() -> str:
    """Today as an ISO date: the working day when one is set, else the UTC date of ``now``."""
    day = _working_day.get()
    return day.isoformat() if day is not None else now().date().isoformat()
