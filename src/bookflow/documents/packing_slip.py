"""The packing slip: a shipment as the customer sees it, without prices (R181 increment 4, design §5).

Company and ship-to, the order number, the customer's P.O., the ship date, ship method and tracking numbers, then
each box with its contents -- item, description, ordered, shipped now, backordered, serial numbers and lot -- and its
weight, and the units not in any box last. Read through `shipment show` and `sales-order show` only, as every
printed document is; nothing is worked out here but the grouping. ``per_box`` gives one page per box.
"""
from __future__ import annotations

from typing import Any, Mapping

from bookflow.documents.model import (
    Column, PrintedDocument, Read, _company, _contact, _customer_party, _facts, _issuer_party, _label, _ship_party,
)

COLUMNS = (Column("Item", 1.3, wrap=True), Column("Description", 1.8, wrap=True), Column("Ordered", 0.9, align="right"),
           Column("Shipped", 0.9, align="right"), Column("Backordered", 1.1, align="right"),
           Column("Serial / lot", 1.6, wrap=True))


def _numbers(serials, lot) -> str:
    parts = list(serials or ())
    if lot:
        parts.append(f"Lot {lot}")
    return ", ".join(parts)


def _box_heading(box: Mapping[str, Any], unit: str) -> str:
    bits = [f"Box {box['box']}"]
    if box.get("package_type"):
        bits.append(str(box["package_type"]))
    bits.append(f"{box['weight']} {unit}")
    if box.get("dimensions"):
        bits.append(str(box["dimensions"]))
    if box.get("tracking_number"):
        bits.append(f"Tracking {box['tracking_number']}")
    return " · ".join(bits)


def packing_slip(read: Read, company_id: str, identity: Mapping[str, Any]) -> PrintedDocument:
    shipment = read("shipment show", {"shipment": identity["document"]}, company_id)
    order = read("sales-order show", {"sales_order": shipment["sales_order_id"]}, company_id)
    info = _company(read, company_id)
    revision = order["revision"]
    profile = revision["facts"]["profile"]
    order_lines = {line["line_id"]: line for line in revision["lines"]}
    lines = {line["line_identity"]: line for line in shipment["lines"]}
    packing = shipment.get("packing") or {}
    unit = packing.get("weight_unit") or info.get("weight_unit") or "lb"

    def row(line, quantity, serials, first):
        source = order_lines.get(line["order_line_id"]) or {}
        figures = source.get("order") or {}
        snapshot = (source.get("facts") or {}).get("profile") or {}
        suffix = f" {_label(snapshot.get('unit'))}" if _label(snapshot.get("unit")) else ""
        ordered = (figures.get("ordered", "") + suffix) if first and figures else ""
        backordered = shipment["order_to_ship"].get(line["order_line_id"], "") if first else ""
        description = (source.get("facts") or {}).get("description") or ""
        return (line["item_name"], description, ordered, quantity, backordered, _numbers(serials, line.get("lot")))

    sections = []
    seen = set()
    for box in packing.get("packages") or ():
        rows = [(_box_heading(box, unit), "", "", "", "", "")]
        for content in box["contents"]:
            line = lines.get(content["line_identity"])
            if line is None:
                continue
            rows.append(row(line, content["quantity"], content.get("serial_numbers"), line["line_identity"] not in seen))
            seen.add(line["line_identity"])
        sections.append(rows)
    loose = []
    if packing.get("packages"):
        for left in packing.get("unboxed") or ():
            line = lines[left["line_identity"]]
            loose.append(row(line, left["quantity"], left.get("serial_numbers"), line["line_identity"] not in seen))
            seen.add(line["line_identity"])
        if loose:
            loose.insert(0, ("Not in a box", "", "", "", "", ""))
    else:
        for line in shipment["lines"]:
            loose.append(row(line, line["quantity"], line.get("serial_numbers"), True))
    if loose:
        sections.append(loose)
    tracking = list(shipment.get("tracking_numbers") or []) + [
        box["tracking_number"] for box in packing.get("packages") or () if box.get("tracking_number")]
    facts = [("Ship date", shipment["date"]), ("Sales order", shipment["sales_order_number"]),
             ("P.O. number", profile.get("customer_purchase_order")),
             ("Ship via", _label(profile.get("ship_method")) if not shipment.get("carrier") else shipment["carrier"]),
             ("Tracking", ", ".join(dict.fromkeys(tracking))),
             ("Boxes", str(len(packing.get("packages") or [])) if packing.get("packages") else None),
             ("Weight", f"{packing['total_weight']} {unit}" if packing.get("total_weight") not in (None, "0") else None)]
    alerts = []
    if shipment["status"] in ("voided", "cancelled"):
        alerts.append(shipment["status"].upper())
    elif shipment["status"] == "draft":
        alerts.append("DRAFT: not shipped yet")
    name = f"Packing Slip {shipment['number']}"
    common = dict(kind="packing-slip", title="Packing Slip", number=shipment["number"],
                  issuer=_issuer_party(revision["facts"].get("issuer_snapshot") or {}, info),
                  parties=(_customer_party(profile, "Sold to"), _ship_party(profile)), facts=_facts(facts),
                  columns=COLUMNS, alerts=tuple(alerts), footer=f"{name} · no prices", name=name,
                  contact=_contact(info))
    if identity.get("per_box") and len(sections) > 1:
        parts = tuple(PrintedDocument(rows=tuple(rows), subject=rows[0][0], **common) for rows in sections)
        return PrintedDocument(rows=parts[0].rows, parts=parts, **common)
    return PrintedDocument(rows=tuple(r for rows in sections for r in rows), **common)
