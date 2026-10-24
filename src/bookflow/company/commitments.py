"""Committed stock: what open sales orders and work orders have promised, and what is left available.

Sales-orders design §3. **Committing documents** are sales orders whose stored status is open and
work orders that are not cancelled -- either active -- dated on or before the report date; a
complete work order still commits until it is invoiced. **Committed** per item is the open
quantity of their stocked lines (ordered less invoiced less cancelled) in the item's base unit.
Nothing is stored: every reader calls `open_lines` or `committed`, as `receiving.order_remaining`
owns on-order. As with on-order, a past report date selects the documents dated by then and
measures what remains open now. A line for an assembly commits the assembly item itself.

**Available** is on hand less committed while `available_deducts_sales_orders` is on, on hand while
it is off. **Available to a document** leaves out that document's own commitment. Pending assembly
builds are a second source, returned separately (`for_assemblies`), dated like orders and deducted
under its own preference, `available_deducts_pending_builds`.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company.items import TRACKED_TYPES
from bookflow.company.sales_calculations import base_quantity

COMMITTING = ("(d.kind = 'sales_order' AND d.status = 'open') OR "
              "(d.kind = 'work_order' AND d.status <> 'cancelled')")


@dataclass(frozen=True)
class OpenLine:
    document_id: str
    kind: str
    number: str
    date: str
    customer_id: str
    revision_id: str
    line_id: str
    position: int
    item_id: str
    ordered: int            # selected-unit microunits
    invoiced: Fraction      # selected-unit microunits, exact
    cancelled: int
    open: Fraction          # ordered - invoiced - cancelled
    open_base: int          # the same in the item's base unit
    open_net: int           # exact net of the open quantity, minor units
    unit_factor_nanounits: int


def _rows(s, as_of, items, document_ids, stocked_only=True, kinds=None):
    types = ", ".join(f"'{name}'" for name in TRACKED_TYPES)
    where = [f"({COMMITTING})", "d.active = 1",
             f"i.type IN ({types})" if stocked_only else "i.type NOT IN ('subtotal', 'discount')"]
    params = {}
    if kinds is not None:
        where.append("d.kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")")
    if as_of is not None:
        where.append("r.date <= :as_of")
        params["as_of"] = as_of
    if items is not None:
        marks = ", ".join(f":item{n}" for n in range(len(items)))
        where.append(f"l.item_id IN ({marks})" if items else "0")
        params.update({f"item{n}": item for n, item in enumerate(items)})
    if document_ids is not None:
        marks = ", ".join(f":doc{n}" for n in range(len(document_ids)))
        where.append(f"d.id IN ({marks})" if document_ids else "0")
        params.update({f"doc{n}": doc for n, doc in enumerate(document_ids)})
    query = f"""
        SELECT d.id AS document_id, d.kind, d.number, r.date, r.customer_id, r.id AS revision_id,
               l.line_id, l.position, l.item_id, l.quantity_microunits, l.cancelled_quantity_microunits,
               l.unit_factor_nanounits, l.net_minor_units, l.id AS row_id,
               w.root_document_id, w.root_line_id
        FROM work_documents d
        JOIN work_revisions r ON r.id = d.current_revision_id
        JOIN work_lines l ON l.revision_id = r.id
        JOIN work_line_identities w ON w.id = l.line_id
        JOIN items i ON i.id = l.item_id
        WHERE {' AND '.join(where)}
        ORDER BY r.date, d.number, d.id, l.position"""
    return [dict(row) for row in s.company.conn.execute(sa.text(query), params).mappings()]


def open_lines(s, as_of=None, *, items=None, document_ids=None, keep_closed=False, stocked_only=True, kinds=None):
    """Every stocked line of a committing document, with what is still open on it.

    `keep_closed` keeps lines with nothing open (the open-sales-orders report shows them beside
    their open neighbours); commitment reads only open ones.
    """
    from bookflow.company import billing_allocations as alloc, billing_math as math, tax_policy, work
    from bookflow.company.sales_orders import invoiced_microunits
    rows = _rows(s, as_of, items, document_ids, stocked_only, kinds)
    roots = list({(row["root_document_id"], row["root_line_id"]) for row in rows})
    occupied = alloc.occupied_roots(s, roots) if roots else set()
    policies = {}
    out = []
    for row in rows:
        root = (row["root_document_id"], row["root_line_id"])
        quantity, cancelled = row["quantity_microunits"], row["cancelled_quantity_microunits"]
        if root in occupied:
            # Only lines an invoice has touched need their facts and the allocation ledger.
            line = work.rows(s, c.work_lines, c.work_lines.c.id == row["row_id"])[0]
            facts = work.line_facts(line)
            if row["revision_id"] not in policies:
                rev = work.rows(s, c.work_revisions, c.work_revisions.c.id == row["revision_id"])[0]
                policies[row["revision_id"]] = tax_policy.effective(work.facts(rev).profile)
            policy = policies[row["revision_id"]]
            invoiced = invoiced_microunits(s, root, facts, policy)
            open_net = alloc.remaining(s, root, facts, policy=policy)[1]
        else:
            invoiced = Fraction(0)
            d = math.denominator(quantity, row["net_minor_units"])
            open_net = math.portion(row["net_minor_units"], 0, d // quantity * (quantity - cancelled), d)
        remaining = quantity - invoiced - cancelled
        if remaining <= 0 and not keep_closed:
            continue
        open_micro = int(round(remaining)) if remaining > 0 else 0
        open_base = base_quantity(open_micro, row["unit_factor_nanounits"]) if open_micro > 0 else 0
        out.append(OpenLine(row["document_id"], row["kind"], row["number"], row["date"], row["customer_id"],
                            row["revision_id"], row["line_id"], row["position"], row["item_id"], quantity,
                            invoiced, cancelled, remaining, open_base, open_net, row["unit_factor_nanounits"]))
    return out


def committed(s, as_of=None, *, items=None, excluding=None):
    """Committed base quantity per item, in microunits; `excluding` leaves one document out."""
    result = {}
    for line in open_lines(s, as_of, items=items):
        if line.document_id == excluding:
            continue
        result[line.item_id] = result.get(line.item_id, 0) + line.open_base
    return result


def committed_by(s, item_id, *, excluding=None):
    """The documents committing one item: [(kind, number, base microunits)], in date order."""
    found = {}
    for line in open_lines(s, items=[item_id]):
        if line.document_id == excluding:
            continue
        key = (line.kind, line.number, line.document_id)
        found[key] = found.get(key, 0) + line.open_base
    return [(kind, number, quantity) for (kind, number, _), quantity in found.items()]


def for_assemblies(s, as_of=None):
    """Quantity reserved for pending assembly builds, per item: the second source of commitment.

    Read from `assembly_builds.reserved`, counting pending builds dated on or before `as_of`, as
    open orders are counted.
    """
    from bookflow.company import assembly_builds
    return dict(assembly_builds.reserved(s, as_of=as_of))


def _preference(s, name, default):
    columns = c.company_info.c
    if name not in columns:
        return default
    return bool(s.company.conn.execute(sa.select(columns[name])).scalar())


def deducts_documents(s):
    return _preference(s, "available_deducts_sales_orders", True)


def deducts_builds(s):
    return _preference(s, "available_deducts_pending_builds", True)


@dataclass(frozen=True)
class Figures:
    on_hand: int
    committed: int
    for_assemblies: int
    available: int


def figures(s, on_hand, item_id, *, committed_map, builds_map, deduct_documents, deduct_builds):
    taken = committed_map.get(item_id, 0)
    builds = builds_map.get(item_id, 0)
    available = on_hand - (taken if deduct_documents else 0) - (builds if deduct_builds else 0)
    return Figures(on_hand, taken, builds, available)


def on_hand(s, item_ids, as_of=None):
    """Quantity on hand per item from the movement ledger, through `as_of` when given."""
    m = c.inventory_movements
    query = sa.select(m.c.item_id, m.c.quantity_microunits).where(m.c.item_id.in_(list(item_ids)))
    if as_of is not None:
        query = query.where(m.c.effective_date <= as_of)
    result = {}
    for item, quantity in s.company.conn.execute(query):
        result[item] = result.get(item, 0) + int(quantity)
    return result


def available_to(s, item_ids, *, excluding=None, as_of=None):
    """On hand less what every other document commits, per item (on hand while the preference is off)."""
    item_ids = list(item_ids)
    stock = on_hand(s, item_ids, as_of)
    taken = committed(s, as_of, items=item_ids, excluding=excluding) if deducts_documents(s) else {}
    builds = for_assemblies(s, as_of) if deducts_builds(s) else {}
    return {item: stock.get(item, 0) - taken.get(item, 0) - builds.get(item, 0) for item in item_ids}


def _plain(microunits):
    from bookflow.core.exact import format_quantity_micro_units
    return format_quantity_micro_units(microunits)


def taking_warnings(s, entries, *, own_transaction=None):
    """An ordinary invoice or sales receipt that takes committed stock: say so, and name the documents.

    `entries` are the sale's stock entries (`inventory_effects.Entry`). A sale that leaves less on
    hand than open orders and work orders have committed proceeds, and its preview and result say
    which documents now cannot all ship. Nothing is refused.
    """
    if not deducts_documents(s):
        return []
    issued = {}
    names = {}
    for entry in entries:
        if entry.kind == 'issue':
            issued[entry.item_id] = issued.get(entry.item_id, 0) + entry.quantity_microunits
            names[entry.item_id] = entry.item_name
    if not issued:
        return []
    stock = on_hand(s, issued)
    if own_transaction is not None:
        m = c.inventory_movements
        for item, quantity in s.company.conn.execute(sa.select(m.c.item_id, m.c.quantity_microunits).where(
                m.c.transaction_id == own_transaction, m.c.item_id.in_(list(issued)))):
            stock[item] = stock.get(item, 0) - int(quantity)   # this sale's own earlier issue comes back first
    warnings = []
    for item, quantity in issued.items():
        documents = committed_by(s, item)
        taken = sum(q for _, _, q in documents)
        left = stock.get(item, 0) - quantity
        if taken and left < taken:
            listed = ', '.join(f"{kind.replace('_', ' ')} {number} ({_plain(q)})" for kind, number, q in documents)
            warnings.append(f'{names[item]}: this sale leaves {_plain(left)} on hand against {_plain(taken)} committed '
                            f'to open orders and work orders ({listed}); not all of them can ship.')
    return warnings


def shortage_warnings(s, view, document_id):
    """An order or work order whose stocked lines need more than is available to it: one warning per item."""
    need, names = {}, {}
    for line in view.revision.lines:
        figures = line.order
        if figures is None or line.facts.profile.item_type not in TRACKED_TYPES:
            continue
        open_micro = Fraction(figures.open)
        if open_micro <= 0:
            continue
        base = base_quantity(int(round(open_micro * 1_000_000)), line.facts.unit_factor_nanounits)
        need[line.facts.item_id] = need.get(line.facts.item_id, 0) + base
        names[line.facts.item_id] = line.facts.profile.item.label
    if not need:
        return []
    available = available_to(s, need, excluding=document_id)
    from bookflow.company.inventory_reports import _incoming
    from bookflow.core import clock
    on_order, _ = _incoming(s, clock.today_iso())
    warnings = []
    for item, quantity in need.items():
        short = quantity - available.get(item, 0)
        if short > 0:
            warnings.append(f'{_plain(short)} {names[item]} short: {_plain(available.get(item, 0))} available to this '
                            f'document, {_plain(on_order.get(item, 0))} on order.')
    return warnings
