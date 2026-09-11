"""One aging rule, read by both sides of the books.

An aging report sorts what is outstanding into columns by how many whole days
past its own date each amount is. Receivables and payables age the same way and
must age the same way -- a bookkeeper reading A/R aging and A/P aging on one
screen is comparing two columns called ``1-30``, and two different definitions
of ``1-30`` would make that comparison a lie. So the boundaries, the column
names and the two evaluators live here once, and the two report modules import
them rather than restating them.

Edges are exact. An amount due exactly 30 days before the as-of date is
``1-30``; one due exactly 31 days before is ``31-60``.
"""
from __future__ import annotations

from datetime import date

# Column order is the order a bookkeeper reads an aging, and the JSON field
# names are the columns.
BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "over_90")
BUCKET_EDGES = (0, 30, 60, 90)
# SQL never names a bucket, so no column alias can collide with a keyword.
COLUMNS = (*(f"bucket_{index}" for index in range(len(BUCKETS))), "total")

# The SQL twin of ``bucket_of``; one rule, two evaluators, same boundaries. It
# reads an ``aging_date`` column and the named edge parameters ``bucket_edges``
# produces, and it compares ISO date text only.
BUCKET_SQL = ("CASE WHEN aging_date>=:edge0 THEN 0 WHEN aging_date>=:edge1 THEN 1 "
              "WHEN aging_date>=:edge2 THEN 2 WHEN aging_date>=:edge3 THEN 3 ELSE 4 END")


def bucket_edges(as_of: str) -> dict[str, str]:
    """The as-of date and the three past-due boundary dates, as ISO date text.

    Calendar arithmetic happens once, here, in exact whole days; SQL only ever
    compares ISO date text, whose lexicographic order is its calendar order.
    Dates before year 1 do not exist, so an early as-of date clamps rather than
    underflowing, which collapses the older columns instead of failing.
    """
    ordinal = date.fromisoformat(as_of).toordinal()
    return {f"edge{index}": date.fromordinal(max(1, ordinal - days)).isoformat()
            for index, days in enumerate(BUCKET_EDGES)}


def days_past_due(as_of: str, aging_date: str) -> int:
    """Whole days this row is past due; zero or negative while it is current."""
    return date.fromisoformat(as_of).toordinal() - date.fromisoformat(aging_date).toordinal()


def bucket_of(as_of: str, aging_date: str) -> str:
    """The Python twin of ``BUCKET_SQL``; one rule, two evaluators."""
    days = days_past_due(as_of, aging_date)
    for index, edge in enumerate(BUCKET_EDGES):
        if days <= edge:
            return BUCKETS[index]
    return BUCKETS[-1]
