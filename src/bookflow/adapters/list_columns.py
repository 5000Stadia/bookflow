"""The columns a person reads first in each noun's list, shared by every human surface.

One declaration for the CLI's `<noun> list` table and the workbench's list pages (decision
D13, modelled on the anchor's Customer Center: name, balance, phone). Presentation only:
JSON, HTTP and MCP results always carry every field, and each surface can still show more --
`--columns` on the CLI, Customize list in the workbench.
"""
from __future__ import annotations

DEFAULT_COLUMNS: dict[str, tuple[str, ...]] = {
    "customer": ("full_name", "phone", "open_balance"),
    "vendor": ("name", "phone", "open_balance"),
    "employee": ("name", "phone", "email"),
    "other-name": ("name", "phone", "email"),
    "item": ("full_name", "type", "price", "quantity_on_hand"),
    "account": ("number", "full_name", "type", "balance"),
}


def default_columns(noun: str) -> tuple[str, ...] | None:
    """The declared default columns for a noun's list, or None where every column shows."""
    return DEFAULT_COLUMNS.get(noun)


COLUMNS_HELP = ("Columns the table prints, comma-separated, or `all` for every column; default {default}. "
                "--json always returns every field")


def curated_columns(cmd) -> tuple[str, ...] | None:
    """The default table columns of a registered `<noun> list`, or None where every column prints."""
    if cmd.verb != "list" or "columns" in cmd.input_model.model_fields:
        return None
    return default_columns(cmd.noun)
