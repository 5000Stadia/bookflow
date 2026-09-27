"""How a list page lays out its rows, and what its controls read as changed.

Presentation only, and the workbench's own preference. Core list metadata keeps its defaults
(it also drives the CLI tables); here a list opens on the few columns a bookkeeper reads first,
and Customize list still adds any column. Nothing is computed: money and dates are shown as
the command returned them, through the display filters.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bookflow.adapters.workbench.list_paging import CARRIED
from bookflow.core.money import Money

# columns: what a browsing list opens with (a workbench default, not core metadata).
# amount: the figure at the right of a phone card.
# secondary: the muted line under the card's name, in this order. A column a person adds
# through Customize list joins it, so nothing chosen is hidden on a phone.
LAYOUTS: dict[str, dict[str, Any]] = {
    "customer": {"columns": ("full_name", "phone", "open_balance"), "amount": "open_balance", "secondary": ("phone",)},
    "vendor": {"columns": ("name", "phone", "open_balance"), "amount": "open_balance", "secondary": ("phone",)},
    "employee": {"columns": ("name", "phone", "email"), "secondary": ("phone", "email")},
    "other-name": {"columns": ("name", "phone", "email"), "secondary": ("phone", "email")},
    "item": {"columns": ("full_name", "type", "price", "quantity_on_hand"), "amount": "price", "secondary": ("type",)},
    "account": {"columns": ("number", "full_name", "type", "balance"), "amount": "balance", "secondary": ("type",)},
    "invoice": {"amount": "total", "secondary": ("customer_name", "date", "status")},
    "sales-receipt": {"amount": "total", "secondary": ("customer_name", "date", "status")},
    "bill": {"amount": "total", "secondary": ("vendor_name", "due_date", "status")},
}

# A bare value on a card's second line needs a word to say what it is.
PREFIX = {"due_date": "Due", "quantity_on_hand": "On hand", "hire_date": "Hired", "release_date": "Released"}

# Parameters that are not list state: paging, and the notices a redirect lands with.
_NOT_STATE = (*CARRIED, "flash", "deleted")


def default_columns(noun: str, offered: Iterable[str]) -> list[str]:
    """The columns a browsing list opens with: the workbench's choice, else the core's."""
    declared = LAYOUTS.get(noun, {}).get("columns")
    return list(declared) if declared else list(offered)


def is_money(value: Any) -> bool:
    return isinstance(value, Mapping) and "amount" in value and "currency" in value


def open_balances(noun: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Name the open balance the document query already returns in its settlement.

    A bill's settlement carries it as money; an invoice's as the minor units still due,
    which the core Money type spells as the same exact decimal. No arithmetic is done.
    """
    if noun not in ("invoice", "bill"):
        return items
    rows = []
    for item in items:
        settlement = item.get("settlement_current") or {}
        balance = settlement.get("open") if noun == "bill" else None
        if noun == "invoice" and settlement.get("due_minor_units") is not None and settlement.get("currency"):
            balance = Money(settlement["due_minor_units"], settlement["currency"]).to_dict()
        rows.append({**item, "open_balance": balance} if balance is not None else item)
    return rows


def plan(noun: str, columns: list[str], rows: list[Mapping[str, Any]], *, name: str | None = None,
         kinds: Mapping[str, str] | None = None, defaults: Iterable[str] = (),
         words: Iterable[str] = ()) -> dict[str, Any]:
    """Which columns hold money (and the one currency they share, if any), which hold dates,
    and what a phone card shows. ``rows`` map column keys to values; ``kinds`` are the
    column kinds a browsing query declares, where it declares them; ``words`` are built-in
    choice columns whose stored values ("accounts_receivable") read better as words."""
    kinds = kinds or {}
    money: dict[str, str | None] = {}
    for column in columns:
        values = [row.get(column) for row in rows]
        if kinds.get(column) == "money" or any(is_money(value) for value in values):
            currencies = {value["currency"] for value in values if is_money(value)}
            money[column] = next(iter(currencies)) if len(currencies) == 1 else None
    dates = {column for column in columns
             if kinds.get(column) == "date" or (column not in kinds and (column == "date" or column.endswith("_date")))}
    layout = LAYOUTS.get(noun, {})
    name = name if name in columns else (columns[0] if columns else None)
    amount = layout.get("amount") if layout.get("amount") in columns else next((c for c in columns if c in money and c != name), None)
    rest = [column for column in columns if column not in (name, amount)]
    declared = layout.get("secondary")
    defaults = set(defaults)
    secondary = rest if declared is None else [c for c in declared if c in rest] + [c for c in rest if c not in declared and c not in defaults]
    return {"money": money, "dates": dates, "name": name, "amount": amount, "secondary": secondary, "prefix": PREFIX,
            "words": set(words) & set(columns)}


def state(params: Any, defaults: Mapping[str, str], quiet: Iterable[str] = ()) -> dict[str, Any]:
    """Whether the list differs from how it opens, and how many filters are set.

    ``quiet`` names controls that change the list without filtering it (search, sort,
    columns): they show Reset but are not counted on the phone's Filters button.
    """
    quiet = set(quiet)
    changed = {key for key, value in params.multi_items()
               if key not in _NOT_STATE and value.strip() and value != defaults.get(key)}
    return {"changed": bool(changed), "filters": len(changed - quiet)}
