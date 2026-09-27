"""A read-only command's answer, shown on the page that asked for it.

Submitting a read-only command's form shows its result under the form, with the inputs still
filled in: a table for each collection of rows, labelled fields for a single object, money and
dates as a person reads them. The complete result stays beneath, under "Technical result".
Only a `list` (its list page) and a `show` that names its record (the record page) go elsewhere.
"""
from __future__ import annotations

from typing import Any

from bookflow.adapters.workbench import display as Display
from bookflow.adapters.workbench import routing as Routing
from bookflow.core import registry
from bookflow.core.models import HIDDEN_COLUMNS, list_columns
from bookflow.core.money import Money

# The columns a noun's own list shows, where the rows alone would choose poorly.
COLUMNS = {
    "rate": ("date", "from_currency", "to_currency", "rate", "source", "version"),
}
MOST = 8  # columns in a table; every field stays in the technical result beneath it
# Paging and freshness markers: the technical result carries them, the reader does not need them.
QUIET = {"next_cursor", "audit_watermark", "has_more", "projection"}


def answers_in_place(cmd: registry.Command, target: str, company_id: str | None) -> bool:
    """Whether a read-only command's result belongs on its own page rather than at `target`."""
    if cmd.is_write:
        return False
    if cmd.verb == "list":
        return False  # its list page is where its rows are
    home = f"/c/{company_id}/" if company_id else None
    fallback = f"/hub/{Routing.segment(cmd.noun)}"
    if cmd.verb == "show":
        # A record page is the richer view of what `show` returns; a page that is not the
        # record (the Overview, or a hub noun page) is not an answer at all.
        return target in (home, fallback)
    return True


def _money(value: Any) -> bool:
    return (isinstance(value, dict) and isinstance(value.get("currency"), str)
            and ("amount" in value or isinstance(value.get("minor_units"), int))
            and set(value) <= {"amount", "currency", "minor_units"})


def _shown_money(value: dict, home: str | None) -> str:
    if "amount" not in value:  # integer minor units only: exact decimal text first
        value = Money(value["minor_units"], value["currency"]).to_dict()
    return Display.money(value, home=home)


def _dated(key: str) -> bool:
    return key in ("date", "as_of", "date_from", "date_to", "due_from", "due_to") or key.endswith("_date")


def _text(key: str, value: Any, home: str | None) -> str | None:
    """How one value reads, or None when it is structure rather than a value."""
    if value is None:
        return ""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if _money(value):
        return _shown_money(value, home)
    if isinstance(value, dict):
        return None
    if isinstance(value, list):
        if all(not isinstance(part, (dict, list)) for part in value):
            return ", ".join(str(part) for part in value) if value else "None"
        return None
    if isinstance(value, str) and _dated(key):
        return Display.longday(value)
    return str(value)


def _rows(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(part, dict) for part in value)


def _table(key: str, items: list[dict], home: str | None, *, noun: str | None = None,
           link_base: str | None = None) -> dict:
    first = items[0]
    if noun in COLUMNS:
        columns = [column for column in COLUMNS[noun] if column in first]
    else:
        columns = list_columns(items) or []
        columns += [column for column, value in first.items() if _money(value) and column not in columns]
        # The linked first column names the row; a yes/no flag never should.
        columns = [c for c in columns if c != "active"] + [c for c in columns if c == "active"]
        columns = columns[:MOST]
    rows = []
    for item in items:
        href = f"{link_base}/{item['id']}" if link_base and item.get("id") else None
        rows.append({"href": href, "cells": [_text(column, item.get(column), home) or "" for column in columns]})
    return {"key": key, "columns": columns, "rows": rows, "count": len(items)}


def _fields(value: dict, home: str | None) -> tuple[list, list, list]:
    """(labelled fields, tables, nested objects) for one mapping."""
    fields, tables, nested = [], [], []
    for key, part in value.items():
        if key in QUIET or key.endswith("_fingerprint") or (key in HIDDEN_COLUMNS and key not in ("id", "version")):
            continue
        if _rows(part):
            tables.append(_table(key, part, home))
            continue
        text = _text(key, part, home)
        if text is not None:
            fields.append({"key": key, "text": text})
        elif isinstance(part, dict):
            nested.append({"key": key, "value": part})
        elif isinstance(part, list) and not part:
            fields.append({"key": key, "text": "None"})
    return fields, tables, nested


def view(company_id: str | None, noun: str, verb: str, result: Any, home: str | None = None) -> dict | None:
    """The readable form of one read-only result, or None when there is nothing to lay out."""
    if not isinstance(result, dict):
        return None
    fields, tables, nested = _fields({k: v for k, v in result.items() if k != "items"}, home)
    items = result.get("items")
    rows = None
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        link = (Routing.base(company_id, noun)
                if verb == "query" and registry.get(f"{noun} show") is not None else None)
        rows = _table("items", items, home, noun=noun, link_base=link) if items else {"key": "items", "columns": [], "rows": [], "count": 0}
    elif items is not None:
        fields.insert(0, {"key": "items", "text": _text("items", items, home) or ""})
    groups = []
    for part in nested:
        inner_fields, inner_tables, deeper = _fields(part["value"], home)
        groups.append({"key": part["key"], "fields": inner_fields, "tables": inner_tables,
                       "more": bool(deeper)})
    return {
        "rows": rows,
        "fields": fields,
        "tables": tables,
        "groups": groups,
        "next_cursor": result.get("next_cursor"),
    }
