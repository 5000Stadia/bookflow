"""`report open-sales-orders`: what customers have ordered and not yet received, by customer or by item.

The anchor's Open Sales Orders by Customer and by Item. A row is one open line of a sales order
whose stored status is open, active, dated within the order-date filters: its date, number,
customer PO, ship date, item, ordered, invoiced, cancelled and backordered quantities and its open
amount. By item it adds the item's on hand, available and on order. Every figure comes from
`commitments.open_lines` and the same owners stock status reads, so the two never disagree.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money


def _date(value):
    return None if value is None else iso_date(value)


class OpenSalesOrdersInput(StrictModel):
    by: Literal["customer", "item"] = Field("customer", description="customer (the default) groups rows by customer, item by item.")
    customer: str | None = Field(None, min_length=1, max_length=1000, description="Only this customer or job: ID or full name.")
    item: str | None = Field(None, min_length=1, max_length=1000, description="Only this item: ID or full name.")
    date_from: str | None = Field(None, min_length=10, max_length=10, description="Earliest order date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last order date, YYYY-MM-DD; orders dated after it are left out. What is still open is what has not been invoiced today.")
    short_only: bool = Field(False, description="Only lines for stocked items with more committed than on hand (available below zero).")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(_date)

    @model_validator(mode="after")
    def interval(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot follow date_to")
        return self


class OpenSalesOrderRow(StrictModel):
    sales_order_id: str
    number: str
    date: str
    customer_id: str
    customer_name: str
    customer_purchase_order: str | None
    ship_date: str | None
    line_id: str
    item_id: str
    item_name: str
    ordered: str
    invoiced: str
    cancelled: str
    backordered: str
    open_amount: MoneyOutput
    stocked: bool
    quantity_on_hand: str | None = Field(None, description="By item, stocked items: on hand now.")
    quantity_available: str | None = Field(None, description="By item, stocked items: on hand less committed.")
    quantity_on_order: str | None = Field(None, description="By item, stocked items: still to arrive on live purchase orders.")


class OpenSalesOrdersTotals(StrictModel):
    open_amount: MoneyOutput
    lines: int


class OpenSalesOrdersOutput(ledger.Page):
    by: Literal["customer", "item"]
    totals: OpenSalesOrdersTotals
    rows: list[OpenSalesOrderRow]


def _plain(microunits):
    """A quantity in microunits: exact, signed (available can be below zero)."""
    from bookflow.company import billing_math as math
    from bookflow.core.exact import format_quantity_micro_units
    from fractions import Fraction
    if isinstance(microunits, int) or Fraction(microunits).denominator == 1:
        return format_quantity_micro_units(int(microunits))
    return math.format_fraction(Fraction(microunits) / 1_000_000)


def open_sales_orders(inp: OpenSalesOrdersInput, s, *, principal_id=None) -> OpenSalesOrdersOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        return _open_sales_orders(inp, s, principal_id)


def _open_sales_orders(inp, s, principal_id):
    import json
    import sqlalchemy as sa
    from bookflow.company import commitments, schema as c
    from bookflow.company.items import TRACKED_TYPES
    from bookflow.company.inventory_reports import _incoming
    from bookflow.core import clock
    # The same continuation contract open-purchase-orders uses: orders are read as they stand now
    # and date_to bounds which orders by their own date.
    state, offset = ledger._state(s, inp, "open-sales-orders", principal_id, None, account_scoped=False)
    customer_id = item_id = None
    if inp.customer:
        from bookflow.company.parties import resolve_party
        customer_id = resolve_party(s.company, "customer", inp.customer)["id"]
    if inp.item:
        from bookflow.company.sales_defaults import _row
        item_id = _row(s.company, "item", inp.item, active=False)["id"]
    lines = commitments.open_lines(s, inp.date_to, items=[item_id] if item_id else None,
                                   stocked_only=False, kinds=("sales_order",))
    lines = [line for line in lines if (not inp.date_from or line.date >= inp.date_from)
             and (customer_id is None or line.customer_id == customer_id)]
    item_ids = sorted({line.item_id for line in lines})
    items = {row["id"]: row for row in s.company.conn.execute(
        sa.select(c.items.c.id, c.items.c.full_name, c.items.c.type).where(c.items.c.id.in_(item_ids))).mappings()}
    stocked = {item for item in item_ids if items[item]["type"] in TRACKED_TYPES}
    stock = commitments.on_hand(s, stocked)
    taken = commitments.committed(s, items=list(stocked)) if stocked else {}
    deducts = commitments.deducts_documents(s)
    available = {item: stock.get(item, 0) - (taken.get(item, 0) if deducts else 0) for item in stocked}
    if inp.short_only:
        lines = [line for line in lines if line.item_id in stocked and available[line.item_id] < 0]
    customers = {row["id"]: row["full_name"] for row in s.company.conn.execute(
        sa.select(c.customers.c.id, c.customers.c.full_name).where(
            c.customers.c.id.in_(sorted({line.customer_id for line in lines})))).mappings()}
    revisions = {row["id"]: json.loads(row["facts_snapshot"])["profile"] for row in s.company.conn.execute(
        sa.select(c.work_revisions.c.id, c.work_revisions.c.facts_snapshot).where(
            c.work_revisions.c.id.in_(sorted({line.revision_id for line in lines})))).mappings()}
    if inp.by == "customer":
        key = lambda line: (customers[line.customer_id].casefold(), line.customer_id, line.date, line.number, line.position)
    else:
        key = lambda line: (items[line.item_id]["full_name"].casefold(), line.item_id, line.date, line.number, line.position)
    lines.sort(key=key)
    currency = state.metadata.currency
    on_order = _incoming(s, clock.today_iso())[0] if inp.by == "item" and stocked else {}
    total = sum(line.open_net for line in lines)
    page = lines[offset:offset + inp.limit + 1]
    more, page = len(page) > inp.limit, page[:inp.limit]
    rows = []
    for line in page:
        profile = revisions[line.revision_id]
        is_stocked = line.item_id in stocked
        extra = {}
        if inp.by == "item" and is_stocked:
            extra = dict(quantity_on_hand=_plain(stock.get(line.item_id, 0)),
                         quantity_available=_plain(available[line.item_id]),
                         quantity_on_order=_plain(on_order.get(line.item_id, 0)))
        rows.append(OpenSalesOrderRow(
            sales_order_id=line.document_id, number=line.number, date=line.date, customer_id=line.customer_id,
            customer_name=customers[line.customer_id], customer_purchase_order=profile.get("customer_purchase_order"),
            ship_date=profile.get("ship_date"), line_id=line.line_id, item_id=line.item_id,
            item_name=items[line.item_id]["full_name"], ordered=_plain(line.ordered), invoiced=_plain(line.invoiced),
            cancelled=_plain(line.cancelled), backordered=_plain(line.open), open_amount=money(line.open_net, currency),
            stocked=is_stocked, **extra))
    return OpenSalesOrdersOutput(metadata=state.metadata, by=inp.by, rows=rows, count=len(rows),
        totals=OpenSalesOrdersTotals(open_amount=money(total, currency), lines=len(lines)),
        next_cursor=ledger._continuation(state, offset, len(rows), more, s.company))
