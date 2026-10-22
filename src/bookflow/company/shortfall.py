"""Shortfall coverage (sales-orders design §5): ordering what an order or a work order is short.

`sales-order purchase-order` and `work-order purchase-order` write one ordinary purchase order
through `purchase-order post` plus one `purchase_coverage_links` row per purchase-order line, in one
company transaction. Links never change; what a link covers is derived:

    coverage(link) = min(link quantity, what its purchase-order line still has to arrive)

and is zero once the line is removed or its order closed or voided. Received quantity is on hand,
open to every document as on-hand stock is.

Default quantity, for one committing document D and one item:

    residual(X)   = max(0, open(X) - coverage(X))            for every committing document X
    pool          = on hand + incoming not covered by any link
    shortfall(D)  = max(0, residual(D) - max(0, pool - sum of residual of every other document))

shared in line order across D's lines. A document's linked coverage serves only it, other
documents' uncovered demand has first claim on the pool, so no incoming unit is counted twice and a
retry -- or a second run before anything changes -- orders nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.sales_models import _invalid
from bookflow.core.errors import BookflowError

LIVE = ('open', 'partly_received')


def _plain(microunits):
    from bookflow.core.exact import format_quantity_micro_units
    return format_quantity_micro_units(microunits)


def live_lines(s):
    """Every purchase-order line still to arrive: identity -> item, remaining, order, expected date."""
    from bookflow.company import purchase_orders as orders, receiving
    t = c.purchase_orders
    out = {}
    for header in s.company.conn.execute(sa.select(t).where(t.c.status.in_(LIVE))).mappings():
        rev = orders.revision(s, dict(header))
        for line in receiving.order_remaining(s, dict(header), rev):
            out[line['order_line_id']] = dict(item_id=line['item_id'], remaining=max(0, line['remaining_quantity_microunits']),
                                              purchase_order_id=header['id'], number=rev['number'],
                                              expected_date=rev['expected_date'])
    return out


def link_rows(s, *, document_ids=None, purchase_order_id=None):
    t = c.purchase_coverage_links
    query = sa.select(t).order_by(t.c.created_at, t.c.id)
    if document_ids is not None:
        query = query.where(t.c.work_document_id.in_(list(document_ids)))
    if purchase_order_id is not None:
        query = query.where(t.c.purchase_order_id == purchase_order_id)
    return [dict(row) for row in s.company.conn.execute(query).mappings()]


def covered(link, live):
    line = live.get(link['purchase_order_line_id'])
    return 0 if line is None else min(link['quantity_microunits'], line['remaining'])


@dataclass
class Short:
    line_id: str
    item_id: str
    item_name: str
    short: int          # base microunits


def shortfalls(s, header):
    """Each line of `header` with the base quantity it is short by default, in line order."""
    from bookflow.company import commitments
    live = live_lines(s)
    links = link_rows(s)
    line_cover, item_unlinked = {}, {}
    for link in links:
        key = (link['work_document_id'], link['work_line_id'])
        line_cover[key] = line_cover.get(key, 0) + covered(link, live)
    linked_by_po_line = {}
    for link in links:
        linked_by_po_line[link['purchase_order_line_id']] = linked_by_po_line.get(link['purchase_order_line_id'], 0) + covered(link, live)
    for identity, line in live.items():
        free = line['remaining'] - linked_by_po_line.get(identity, 0)
        if free > 0:
            item_unlinked[line['item_id']] = item_unlinked.get(line['item_id'], 0) + free
    mine = commitments.open_lines(s, document_ids=[header['id']])
    items = sorted({line.item_id for line in mine})
    if not items:
        return []
    every = commitments.open_lines(s, items=items)
    on_hand = commitments.on_hand(s, items)
    residual_line = {}
    residual_doc = {}
    for line in every:
        rest = max(0, line.open_base - line_cover.get((line.document_id, line.line_id), 0))
        residual_line[(line.document_id, line.line_id)] = rest
        key = (line.document_id, line.item_id)
        residual_doc[key] = residual_doc.get(key, 0) + rest
    names = {row['id']: row['full_name'] for row in s.company.conn.execute(
        sa.select(c.items.c.id, c.items.c.full_name).where(c.items.c.id.in_(items))).mappings()}
    out = []
    for item in items:
        pool = on_hand.get(item, 0) + item_unlinked.get(item, 0)
        others = sum(value for (doc, it), value in residual_doc.items() if it == item and doc != header['id'])
        short = max(0, residual_doc.get((header['id'], item), 0) - max(0, pool - others))
        for line in mine:
            if line.item_id != item or short <= 0:
                continue
            take = min(short, residual_line[(header['id'], line.line_id)])
            if take > 0:
                out.append(Short(line.line_id, item, names[item], take))
                short -= take
    order = {line.line_id: index for index, line in enumerate(mine)}
    return sorted(out, key=lambda row: order[row.line_id])


def _preferred_vendor(s, item_id):
    return s.company.conn.execute(sa.select(c.items.c.preferred_vendor_id).where(c.items.c.id == item_id)).scalar()


def _rate(s, item_id, vendor_id, currency):
    """The purchase order's own cost: this vendor's purchase cost for the item, else the item's cost."""
    p = c.item_vendor_profiles
    row = s.company.conn.execute(sa.select(p.c.purchase_cost_minor_units, p.c.purchase_cost_currency).where(
        p.c.item_id == item_id, p.c.vendor_id == vendor_id, p.c.active.is_(True))).first()
    if row is not None and row[0] is not None and row[1] == currency:
        return row[0]
    item = s.company.conn.execute(sa.select(c.items.c.cost_minor_units, c.items.c.cost_currency).where(
        c.items.c.id == item_id)).first()
    if item is not None and item[0] is not None and item[1] == currency:
        return item[0]
    return None


def _document(s, inp, kind):
    from bookflow.company import work
    header = work.resolve(s, getattr(inp, kind), kind)
    if not header['active'] or (kind == 'sales_order' and header['status'] != 'open') or header['status'] in ('cancelled', 'voided'):
        raise BookflowError('E_WORK_DEPENDENCY', details=dict(source_id=header['id'],
            problem=f'a {kind.replace("_", " ")} that is closed, cancelled, voided or inactive commits nothing to order'))
    return header


def plan(s, inp, kind):
    """(header, vendor id, [(line_id, item_id, base quantity)], left for other vendors)."""
    from bookflow.company import commitments, sales_defaults as sd
    header = _document(s, inp, kind)
    from bookflow.company.parties import resolve_party
    vendor = resolve_party(s.company, 'vendor', inp.vendor)['id'] if inp.vendor else None
    left = []
    if inp.lines:
        open_lines = commitments.open_lines(s, document_ids=[header['id']], keep_closed=True)
        chosen = []
        for index, entry in enumerate(inp.lines):
            if entry.line_id:
                match = [line for line in open_lines if line.line_id == entry.line_id]
            else:
                item = sd._row(s.company, 'item', entry.item, active=False)['id']
                match = [line for line in open_lines if line.item_id == item]
            if len(match) != 1:
                raise _invalid(f'lines.{index}', 'name one stocked line of this document, by line_id or by an item one line carries')
            from bookflow.core.exact import parse_quantity_micro_units
            chosen.append((match[0].line_id, match[0].item_id, parse_quantity_micro_units(entry.quantity, field=f'lines.{index}.quantity')))
        if vendor is None:
            raise _invalid('vendor', 'name the vendor to order these lines from')
        return header, vendor, chosen, left
    short = shortfalls(s, header)
    by_vendor = {}
    for row in short:
        by_vendor.setdefault(_preferred_vendor(s, row.item_id), []).append(row)
    if vendor is None:
        named = [key for key in by_vendor if key]
        if len(by_vendor) > 1 or (by_vendor and not named):
            names = {row['id']: row['name'] for row in s.company.conn.execute(sa.select(c.vendors.c.id, c.vendors.c.name).where(
                c.vendors.c.id.in_([key for key in by_vendor if key]))).mappings()}
            raise BookflowError('E_VALIDATION', details=dict(
                problem='these short lines come from different vendors; one purchase order is for one vendor, so name it',
                vendors=[dict(vendor=names.get(key),
                              lines=[dict(line_id=row.line_id, item=row.item_name, short=_plain(row.short)) for row in rows])
                         for key, rows in by_vendor.items()]))
        vendor = named[0] if named else None
    lines = [(row.line_id, row.item_id, row.short) for row in by_vendor.get(vendor, [])]
    left = [row for key, rows in by_vendor.items() if key != vendor for row in rows]
    if not lines:
        raise BookflowError('E_VALIDATION', details=dict(
            problem='nothing on this document is short for this vendor: what is open is on hand or already on order',
            left_for_other_vendors=[dict(line_id=row.line_id, item=row.item_name, short=_plain(row.short)) for row in left]))
    return header, vendor, lines, left


def purchase_input(s, inp, header, vendor, lines):
    from bookflow.company.purchase_order_models import PurchaseOrderPostInput
    from bookflow.core.money import Money
    currency = s.company_info_row['home_currency']
    rows = []
    for index, (_, item, quantity) in enumerate(lines):
        rate = _rate(s, item, vendor, currency)
        if rate is None:
            raise _invalid(f'lines.{index}', 'the item has no purchase cost for this vendor or on its record; set one on the item')
        rows.append(dict(item=item, quantity=_plain(quantity), rate=Money(rate, currency).to_dict()['amount']))
    values = dict(vendor=vendor, date=inp.date, lines=rows,
                  memo=inp.memo or f'For {header["kind"].replace("_", " ")} {header["number"]}')
    if inp.expected_date:
        values['expected_date'] = inp.expected_date
    return PurchaseOrderPostInput.model_validate(values)


def covers(s, purchase_order_id):
    """What a purchase order covers: each link with the order line it serves and what is still to arrive."""
    from bookflow.company.work_outputs import CoverageOutput
    rows = link_rows(s, purchase_order_id=purchase_order_id)
    if not rows:
        return None
    live = live_lines(s)
    docs = {row['id']: row for row in s.company.conn.execute(sa.select(c.work_documents).where(
        c.work_documents.c.id.in_({row['work_document_id'] for row in rows}))).mappings()}
    return [CoverageOutput(work_document_id=row['work_document_id'], work_kind=docs[row['work_document_id']]['kind'],
                           work_number=docs[row['work_document_id']]['number'], work_line_id=row['work_line_id'],
                           purchase_order_id=row['purchase_order_id'], purchase_order_line_id=row['purchase_order_line_id'],
                           purchase_order_number=(live.get(row['purchase_order_line_id']) or {}).get('number'),
                           linked=_plain(row['quantity_microunits']), to_arrive=_plain(covered(row, live)),
                           expected_date=(live.get(row['purchase_order_line_id']) or {}).get('expected_date'))
            for row in rows]


def document_coverage(s, document_id):
    """Each line of an order or work order: the purchase-order lines ordered for it."""
    from bookflow.company.work_outputs import CoverageOutput
    from bookflow.company import purchase_orders as orders
    rows = link_rows(s, document_ids=[document_id])
    if not rows:
        return {}
    live = live_lines(s)
    doc = s.company.conn.execute(sa.select(c.work_documents.c.kind, c.work_documents.c.number).where(
        c.work_documents.c.id == document_id)).first()
    numbers = {row['id']: row['number'] for row in s.company.conn.execute(sa.select(c.purchase_orders.c.id, c.purchase_orders.c.number).where(
        c.purchase_orders.c.id.in_({row['purchase_order_id'] for row in rows}))).mappings()}
    out = {}
    for row in rows:
        here = live.get(row['purchase_order_line_id']) or {}
        out.setdefault(row['work_line_id'], []).append(CoverageOutput(
            work_document_id=document_id, work_kind=doc.kind, work_number=doc.number, work_line_id=row['work_line_id'],
            purchase_order_id=row['purchase_order_id'], purchase_order_line_id=row['purchase_order_line_id'],
            purchase_order_number=numbers.get(row['purchase_order_id']), linked=_plain(row['quantity_microunits']),
            to_arrive=_plain(covered(row, live)), expected_date=here.get('expected_date')))
    return out
