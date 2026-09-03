"""Rendering for the CLI: tables, field views, JSON, and errors (blueprint 5.4)."""

from __future__ import annotations

import json
import sys
from typing import Any

from bookflow.core.errors import BookflowError


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
    cols = columns or [k for k in items[0] if not isinstance(items[0][k], (dict, list))]
    widths = {c: max(len(c), *(len(_cell(i.get(c))) for i in items)) for c in cols}
    head = "  ".join(c.ljust(widths[c]) for c in cols)
    rows = ["  ".join(_cell(i.get(c)).ljust(widths[c]) for c in cols) for i in items]
    return "\n".join([head, "  ".join("-" * widths[c] for c in cols), *rows])


def render_fields(obj: dict[str, Any], indent: int = 0) -> str:
    lines = []
    pad = " " * indent
    for k, v in obj.items():
        if isinstance(v, dict):
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
PREFERRED = ["display_name", "organization_name", "legal_name", "home_currency", "access", "role", "at", "command", "actor_name", "interface", "reason", "summary", "company_id", "organization_id", "event_count"]
HIDDEN = COMMON | {"path", "schema_revision", "entries", "session_id", "request_id", "client_version", "client_host", "client_name", "actor_id", "actor_kind", "on_behalf_of", "directive_id", "source_ref", "registered_by_name", "is_demo", "entry_count"}


def render_output(out: dict[str, Any], as_json: bool) -> str:
    if as_json:
        return json.dumps(out, default=str)
    if "items" in out and isinstance(out["items"], list):
        items = out["items"]
        cols = None
        if items:
            keys = [k for k in items[0] if k not in HIDDEN and not isinstance(items[0][k], (dict, list))]
            cols = [k for k in PREFERRED if k in keys] + [k for k in keys if k not in PREFERRED]
        text = render_table(items, cols)
        extra = {k: v for k, v in out.items() if k != "items"}
        return text + "\n" + " ".join(f"{k}={_cell(v)}" for k, v in extra.items())
    return render_fields(out)
