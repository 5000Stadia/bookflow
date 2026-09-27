"""A query's rows, shown as a table on the page that asked for them.

A `query` command reads rows. Submitting its form shows those rows under the form, with the
filters still filled in, rather than sending the person to another page with the answer folded
into a one-time notice.
"""
from __future__ import annotations

from typing import Any

from bookflow.adapters.workbench import routing as Routing
from bookflow.core import registry
from bookflow.core.models import list_columns

# The columns a noun's own list shows, where the rows alone would choose poorly.
COLUMNS = {
    "rate": ("date", "from_currency", "to_currency", "rate", "source", "version"),
}
MOST = 8  # columns shown; every field stays in the technical result beneath the table


def is_query(noun: str, verb: str) -> bool:
    return verb == "query"


def _money(value: Any) -> bool:
    return isinstance(value, dict) and "amount" in value and "currency" in value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if _money(value):
        return f"{value['amount']} {value['currency']}"
    return str(value)


def view(company_id: str | None, noun: str, verb: str, result: dict | None) -> dict | None:
    """The table for one query result, or None when the result is not a page of rows."""
    if not is_query(noun, verb) or not isinstance(result, dict):
        return None
    items = result.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None
    first = items[0] if items else {}
    if noun in COLUMNS:
        columns = [key for key in COLUMNS[noun] if not items or key in first]
    else:
        columns = list_columns(items) or []
        columns += [key for key, value in first.items() if _money(value) and key not in columns]
        # The linked first column names the row; a yes/no flag never should.
        columns = [key for key in columns if key != "active"] + [key for key in columns if key == "active"]
        columns = columns[:MOST]
    show = registry.get(f"{noun} show") is not None
    rows = []
    for item in items:
        href = f"{Routing.base(company_id, noun)}/{item['id']}" if show and item.get("id") else None
        rows.append({"href": href, "cells": [_text(item.get(key)) for key in columns]})
    return {
        "columns": columns,
        "rows": rows,
        "count": len(items),
        "next_cursor": result.get("next_cursor"),
    }
