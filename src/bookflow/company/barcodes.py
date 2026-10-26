"""Barcodes (R104 increment 3, design/advanced-inventory.md §7).

A code names an item (``items.barcode``); bins, serial numbers and lots join the look-up in that
order as later increments give them codes (``_KINDS`` below is where each slots in). Every ``item``
field on every surface resolves id, then name, then barcode (``list_service.resolve_selector``), so a
scanned code works wherever an item is named. `barcode lookup` answers a code with its kind, the
record, where it is and the line fields to use; `barcode generate` gives items without a code an
EAN-13 in the restricted-circulation range (prefix 2) from the company's ``barcode`` counter.
Existing codes never change.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import items as item_service, list_service, placements, schema as c
from bookflow.company.lists import get_list_definition
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units
from bookflow.core.registry import Touched

#: Item types that carry a barcode (the item profiles' ``barcode`` field).
CODED_TYPES = tuple(sorted(kind for kind, profile in item_service.ITEM_PROFILES.items() if "barcode" in profile.allowed))
PREFIX = "2"  # GS1 restricted circulation: never collides with a manufacturer's code
SERIES = "barcode"


def ean13_check_digit(body: str) -> str:
    """The check digit of a 12-digit EAN-13 body: weights 1, 3, 1, 3 ... from the left."""
    if len(body) != 12 or not body.isdigit():
        raise ValueError("an EAN-13 body is 12 digits")
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(body))
    return str((10 - total % 10) % 10)


def is_ean13(code: str) -> bool:
    return isinstance(code, str) and len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def ean13(number: int) -> str:
    """The ``number``-th code of the company series: prefix 2, eleven digits, check digit."""
    if not 0 < number < 10 ** 11:
        raise ValueError("the barcode series is exhausted")
    body = f"{PREFIX}{number:011d}"
    return body + ean13_check_digit(body)


# ---------------------------------------------------------------------------------- lookup

def _site_names(s) -> dict[str, str]:
    return {row["id"]: row["name"] for row in placements.all_sites(s)}


def _item_match(s, row: dict[str, Any], sites: dict[str, str], as_of: str) -> dict[str, Any]:
    held = placements.by_item(placements.on_hand(s, as_of, item_ids=[row["id"]])).get(row["id"], {})
    on_hand = [{"site_id": site, "site": sites.get(site, site), "quantity": format_quantity_micro_units(quantity)}
               for site, quantity in sorted(held.items(), key=lambda pair: sites.get(pair[0], pair[0]).casefold())]
    return {
        "kind": "item", "id": row["id"], "name": row["full_name"], "type": row["type"], "active": bool(row["active"]),
        "barcode": row["barcode"], "on_hand": on_hand,
        "line_fields": {"item": row["full_name"]},
    }


def _items(s, code: str, sites: dict[str, str], as_of: str) -> list[dict[str, Any]]:
    rows = list_service.code_matches(s.company, c.items, get_list_definition(item_service.ITEM_NOUN), code)
    return [_item_match(s, row, sites, as_of) for row in rows]


def _numbers(kind):
    """A scanned serial-number or lot label (R104 increment 4): the number of a tracked item, where it is, and
    the line fields that name it."""
    table = c.serial_numbers if kind == "serial_number" else c.lots
    column = "serial_number_id" if kind == "serial_number" else "lot_id"

    def finder(s, code: str, sites: dict[str, str], as_of: str) -> list[dict[str, Any]]:
        from bookflow.company import serial_lots
        rows = s.company.conn.execute(sa.select(table.c.id, table.c.number, c.items.c.id.label("item_id"),
                                                c.items.c.full_name, c.items.c.type, c.items.c.active)
                                      .join(c.items, c.items.c.id == table.c.item_id)
                                      .where(table.c.number_key == serial_lots.number_key(code))).mappings().all()
        out = []
        for row in rows:
            held = s.company.conn.execute(sa.text(
                f"SELECT site_id, sum(quantity_microunits) FROM inventory_placements WHERE {column} = :id "
                "AND effective_date <= :as_of GROUP BY site_id"), {"id": row["id"], "as_of": as_of}).all()
            on_hand = [{"site_id": site, "site": sites.get(site, site), "quantity": format_quantity_micro_units(int(q))}
                       for site, q in sorted(held, key=lambda pair: sites.get(pair[0], pair[0]).casefold()) if int(q)]
            fields = {"item": row["full_name"]}
            fields.update({"serial_numbers": [row["number"]]} if kind == "serial_number" else {"lot": row["number"]})
            out.append({"kind": kind, "id": row["id"], "name": f"{row['full_name']} {row['number']}", "type": row["type"],
                        "active": bool(row["active"]), "barcode": None, "on_hand": on_hand, "line_fields": fields})
        return out
    return finder


# The look-up order of §7: items, then serial numbers and lots (increment 4); bins (increment 5) slot in here,
# each a function returning its matches in the same shape with its own ``kind`` and ``line_fields``.
_KINDS = (("item", _items), ("serial_number", _numbers("serial_number")), ("lot", _numbers("lot")))


def lookup(s, inp) -> dict[str, Any]:
    code = inp.code.strip()
    from bookflow.company.memorized_schedule import today
    as_of = inp.as_of or today(getattr(s, "company_tz", None))
    sites = _site_names(s)
    matches: list[dict[str, Any]] = []
    for _kind, finder in _KINDS:
        matches = finder(s, code, sites, as_of)
        if matches:
            break
    active = [match for match in matches if match["active"]]
    chosen = active if active else matches
    ambiguous = len(chosen) > 1
    found = chosen[0] if len(chosen) == 1 else None
    if not matches:
        message = f"No item, serial number or lot carries the code {code!r}. Assign it to an item with `item update <item> --barcode {code}`."
    elif ambiguous:
        message = (f"The code {code!r} is on {len(chosen)} items: {', '.join(m['name'] for m in chosen)}. "
                   "Name the item instead, or give one of them a different code.")
    elif not found["active"]:
        message = f"The code {code!r} is on {found['name']}, which is inactive."
    elif found["kind"] != "item":
        where = ", ".join(f"{place['site']} {place['quantity']}" for place in found["on_hand"]) or "not in stock"
        message = f"{code} is {found['kind'].replace('_', ' ')} {code} of {found['line_fields']['item']} ({where})."
    else:
        message = f"{code} is {found['name']}."
    return {
        "code": code, "as_of": as_of, "found": found is not None,
        "kind": found["kind"] if found else ("ambiguous" if ambiguous else None),
        "match": found, "matches": matches, "ambiguous": ambiguous,
        "valid_ean13": is_ean13(code), "message": message,
    }


# -------------------------------------------------------------------------------- generate

def _taken_codes(s) -> set[str]:
    rows = s.company.conn.execute(sa.select(c.items.c.barcode).where(c.items.c.barcode.is_not(None))).scalars()
    bins = s.company.conn.execute(sa.select(c.inventory_bins.c.barcode).where(c.inventory_bins.c.barcode.is_not(None))).scalars()
    return {*rows, *bins}


def _next_number(s) -> int:
    row = s.company.conn.execute(sa.select(c.sequences.c.next_number).where(c.sequences.c.name == SERIES)).scalar()
    return int(row) if row is not None else 1


def plan_generate(s, ctx, inp) -> dict[str, Any]:
    if inp.items:
        chosen, seen = [], set()
        for selector in inp.items:
            row = item_service.resolve_item(s.company, selector)
            if row["id"] not in seen:
                seen.add(row["id"])
                chosen.append(row)
    else:
        t = c.items
        chosen = [dict(row) for row in s.company.conn.execute(
            sa.select(t).where(t.c.active.is_(True), t.c.type.in_(CODED_TYPES), t.c.barcode.is_(None))
            .order_by(t.c.full_name_key, t.c.id)).mappings()]
    taken, number = _taken_codes(s), _next_number(s)
    assigned, skipped, updates = [], [], []
    for row in chosen:
        if row["barcode"]:
            skipped.append({"item_id": row["id"], "item": row["full_name"], "barcode": row["barcode"],
                            "reason": "already has a code; existing codes never change"})
            continue
        if row["type"] not in CODED_TYPES:
            skipped.append({"item_id": row["id"], "item": row["full_name"], "barcode": None,
                            "reason": f"a {row['type'].replace('_', ' ')} carries no barcode"})
            continue
        if not row["active"]:
            raise BookflowError("E_INACTIVE_REFERENCE", details={"field": "items", "record_id": row["id"]})
        code = ean13(number)
        while code in taken:
            number += 1
            code = ean13(number)
        taken.add(code)
        number += 1
        update = item_service.plan_item_update(s.company, row["id"], {"barcode": code},
                                               actor_id=s.actor.id, via=ctx.interface.value)
        updates.append(update)
        assigned.append({"item_id": row["id"], "item": row["full_name"], "barcode": code})
    return {"assigned": assigned, "skipped": skipped, "next_number": number, "updates": updates}


def apply_generate(s, planned: dict[str, Any]) -> list:
    touched = []
    for update in planned["updates"]:
        item_service.persist_item_update(s.company, update)
        mutation = update.mutation
        touched.append(Touched(item_service.ITEM_RECORD_TYPE, str(mutation.after["id"]), mutation.action,
                               int(mutation.before["version"]), int(mutation.after["version"]),
                               mutation.after_snapshot, mutation.before_snapshot, db="company"))
    if planned["updates"]:
        t = c.sequences
        if s.company.conn.execute(sa.select(t.c.name).where(t.c.name == SERIES)).first() is None:
            s.company.conn.execute(t.insert().values(name=SERIES, next_number=planned["next_number"], prefix=""))
        else:
            s.company.conn.execute(t.update().where(t.c.name == SERIES).values(next_number=planned["next_number"]))
    return touched
