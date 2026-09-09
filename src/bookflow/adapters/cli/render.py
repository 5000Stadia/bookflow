"""Rendering for the CLI: tables, field views, JSON, and errors (blueprint 5.4)."""

from __future__ import annotations

import json
import sys
from typing import Any

from bookflow.core.errors import BookflowError
from bookflow.core.models import list_columns
from bookflow.core.money import Money, is_currency


def _money(v: Any) -> Money | None:
    """Recognize only complete money values, never infer a currency or scale."""
    if (isinstance(v, dict) and {"minor_units", "currency"} <= v.keys()
            and v.keys() <= {"minor_units", "currency", "amount"}
            and type(v["minor_units"]) is int
            and isinstance(v["currency"], str) and is_currency(v["currency"])):
        return Money(v["minor_units"], v["currency"])
    return None


def emit_error(err: BookflowError, as_json: bool) -> int:
    if not as_json:
        line = f"error: {err.message}"
        fields = err.details.get("fields") if isinstance(err.details, dict) else None
        if fields:
            line += " " + "; ".join(f"{f.get('field')}: {f.get('problem')}" for f in fields)
        print(line, file=sys.stderr)
    print(json.dumps(err.to_dict(), default=str), file=sys.stderr)
    return err.exit_code


def _cell(v: Any) -> str:
    money = _money(v)
    if money is not None:
        return str(money)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def render_table(items: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not items:
        return "(none)"
    cols = list(columns) if columns is not None else [k for k in items[0] if not isinstance(items[0][k], (dict, list))]
    for item in items:
        for key, value in item.items():
            if key not in cols and _money(value) is not None:
                cols.append(key)
    money_cols = [c for c in cols if any(_money(i.get(c)) is not None for i in items)]
    if money_cols:
        labels = [c for c in cols if c in {"display_name", "full_name", "name", "number", "code"}]
        cols = labels + money_cols + [c for c in cols if c not in labels and c not in money_cols]
    widths = {c: max(len(c), *(len(_cell(i.get(c))) for i in items)) for c in cols}
    head = "  ".join(c.ljust(widths[c]) for c in cols)
    rows = ["  ".join(_cell(i.get(c)).ljust(widths[c]) for c in cols) for i in items]
    return "\n".join([head, "  ".join("-" * widths[c] for c in cols), *rows])


def render_fields(obj: dict[str, Any], indent: int = 0) -> str:
    lines = []
    pad = " " * indent
    for k, v in obj.items():
        if _money(v) is not None:
            lines.append(f"{pad}{k}: {_cell(v)}")
        elif isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(render_fields(v, indent + 2))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            lines.append(f"{pad}{k}:")
            for item in v:
                lines.append(render_fields(item, indent + 2))
                lines.append("")
        else:
            lines.append(f"{pad}{k}: {_cell(v)}")
    return "\n".join(lines)


COMMON = {"id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"}
PREFERRED = ["id", "seq", "display_name", "organization_name", "legal_name", "home_currency", "access", "role", "at", "command", "actor_name", "interface", "reason", "summary", "company_id", "organization_id", "event_count"]
HIDDEN = COMMON | {"path", "schema_revision", "entries", "session_id", "request_id", "client_version", "client_host", "client_name", "actor_id", "actor_kind", "on_behalf_of", "directive_id", "source_ref", "registered_by_name", "is_demo", "entry_count"}


def render_output(out: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(out, default=str)
    if "items" in out and isinstance(out["items"], list):
        items = out["items"]
        cols = list_columns(items)
        text = render_table(items, cols)
        extra = {k: v for k, v in out.items() if k != "items"}
        return text + "\n" + " ".join(f"{k}={_cell(v)}" for k, v in extra.items())
    return render_fields(out)
