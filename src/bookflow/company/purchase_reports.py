"""What was bought and what is still on order.

**Purchases by vendor and by item are item purchases**, as the anchor's are: what the
company bought as items on bills, checks, credit card charges and item receipts. A purchase
entered on an expense account names no item and is on ``report expenses-by-vendor`` instead.

Both read posted effects only, over accounting dates:

* **A stock item** is read off the inventory ledger, which ties every movement to the
  posting line carrying its value. A purchase is a receipt of stock whose other side is
  Accounts Payable, a bank account or a credit card -- what a bill, an item receipt, a check
  or a card charge posts -- together with the purchase-price corrections that later amend
  such a receipt, and the reversals a void or a correction writes for either. A customer
  return, an inventory adjustment, a sale and a replay's recost of a sale are not purchases.
  A bill entered for goods already received moves the receipt's payable onto itself and
  posts no stock, so what was received is counted once, at the receipt, with any price the
  bill corrects added on its own date.
* **Any other item** -- a non-inventory part, a service, an other charge -- is read off the
  cost posting it made: each posting line on an account other than Accounts Payable that
  ``posting_line_sources`` attributes to a purchase item line, with that line's quantity,
  signed by the side the posting line is on so a reversal takes both back off.

A purchase belongs to the vendor the posting line names, or, where the line names none, to
the one vendor its posting names -- which is how a check's payee reaches the item lines
behind it. Rows worth nothing are omitted, and totals cover every row.

**Open purchase orders** are the orders with something still to receive: what was ordered,
what the receipts recorded against each line have taken, and what is left. An order is not
a posting, so this report reads the orders and their receipts as they stand now; the date
bounds which orders by their own date.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.company.payable_reports import NO_VENDOR
from bookflow.company.summary_reports import NO_ITEM, _percent
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, round_ratio_half_even

QUANTITY_SCALE = 10 ** 6


class _Period(StrictModel):
    date_from: str = Field(min_length=10, max_length=10, description="Inclusive first accounting date, YYYY-MM-DD.")
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last accounting date, YYYY-MM-DD.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator("date_from", "date_to")(iso_date)

    @model_validator(mode="after")
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class PurchasesByVendorInput(_Period):
    pass


class PurchasesByItemInput(_Period):
    pass


class OpenPurchaseOrdersInput(StrictModel):
    date_to: str = Field(min_length=10, max_length=10, description="Inclusive last order date, YYYY-MM-DD; orders dated after it are left out. What is still open is what has not been received today.")
    vendor: str | None = Field(default=None, min_length=1, max_length=1000, description="Optional vendor ID or name; omit for every vendor with an open order.")
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _date_to = field_validator("date_to")(iso_date)


class PurchasesTotals(StrictModel):
    amount: MoneyOutput


class PurchasesByVendorRow(StrictModel):
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    amount: MoneyOutput
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class PurchasesByItemRow(StrictModel):
    item_id: str | None
    current_item_label: str | None
    current_item_name: str | None
    display_item_label: str
    item_type: str | None
    active: bool | None
    quantity: str
    quantity_microunits: int
    amount: MoneyOutput
    average_cost: MoneyOutput | None
    percent_of_total: str | None
    percent_of_total_millionths: int | None


class OpenPurchaseOrdersTotals(StrictModel):
    amount: MoneyOutput
    received: MoneyOutput
    open_balance: MoneyOutput


class OpenPurchaseOrderRow(StrictModel):
    purchase_order_id: str
    number: str
    date: str
    expected_date: str | None
    status: Literal["open", "partly_received"]
    vendor_id: str | None
    current_vendor_name: str | None
    display_vendor_label: str
    active: bool | None
    reference: str | None
    memo: str | None
    amount: MoneyOutput
    received: MoneyOutput
    open_balance: MoneyOutput


class PurchasesByVendorOutput(ledger.Page):
    totals: PurchasesTotals
    rows: list[PurchasesByVendorRow]


class PurchasesByItemOutput(ledger.Page):
    totals: PurchasesTotals
    rows: list[PurchasesByItemRow]


class OpenPurchaseOrdersOutput(ledger.Page):
    totals: OpenPurchaseOrdersTotals
    rows: list[OpenPurchaseOrderRow]


# Never let SQLite do arithmetic on an aggregate: bookflow_sum_int returns lossless text.
# Only stored INTEGER columns are multiplied or negated below.
#
# `bought` is the stock purchased from a vendor: a receipt whose other side is a payable,
# a bank account or a card, the recosts that amend such a receipt, and the reversals of
# either. `other_items` is every other purchased item's cost posting, attributed to its
# entered line; the posting line that carries a stock movement is left to `bought`, so no
# cent is read twice. `first` keeps a line's quantity on one posting line of one batch.
_PURCHASES = """
WITH receipts AS (
 SELECT m.id FROM inventory_movements m JOIN accounts o ON o.id=m.offset_account_id
 WHERE m.kind='receipt' AND m.returns_movement_id IS NULL
   AND o.type IN ('accounts_payable', 'bank', 'credit_card')
), amended AS (
 SELECT id FROM receipts
 UNION ALL
 SELECT m.id FROM inventory_movements m
 WHERE m.kind='recost' AND m.corrects_movement_id IN (SELECT id FROM receipts)
), bought AS (
 SELECT id FROM amended
 UNION ALL
 SELECT m.id FROM inventory_movements m
 WHERE m.kind='reversal' AND m.reverses_movement_id IN (SELECT id FROM amended)
), purchase_lines AS (
 SELECT document_line_id, item_id, quantity_microunits AS quantity FROM purchase_item_lines
 UNION ALL
 SELECT document_line_id, item_id,
        CAST(json_extract(line_snapshot, '$.quantity_microunits') AS INTEGER) FROM money_out_item_lines
), other_items AS (
 SELECT l.id AS line_id, l.batch_id, l.name_type, l.name_id, p.item_id,
        CASE WHEN l.debit_minor_units>0 THEN 1 ELSE -1 END AS direction,
        s.amount_minor_units AS attributed, p.quantity,
        row_number() OVER (PARTITION BY l.batch_id, s.document_line_id ORDER BY l.line_no) AS first
 FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
 JOIN accounts a ON a.id=l.account_id
 JOIN posting_line_sources s ON s.posting_line_id=l.id
 JOIN purchase_lines p ON p.document_line_id=s.document_line_id
 WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to
   AND a.type!='accounts_payable'
   AND NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.posting_line_id=l.id)
), effects AS (
 SELECT m.item_id, m.quantity_microunits AS quantity, m.value_minor_units AS amount,
        m.posting_batch_id AS batch_id, l.name_type, l.name_id
 FROM inventory_movements m JOIN bought k ON k.id=m.id
 JOIN posting_batches b ON b.id=m.posting_batch_id
 LEFT JOIN posting_lines l ON l.id=m.posting_line_id
 WHERE b.effective_date>=:date_from AND b.effective_date<=:date_to
 UNION ALL
 SELECT item_id, CASE WHEN first=1 THEN direction*quantity ELSE 0 END, direction*attributed,
        batch_id, name_type, name_id
 FROM other_items
), effect_vendor AS (
 SELECT batch_id, min(name_id) AS vendor_id, count(DISTINCT name_id) AS named
 FROM posting_lines
 WHERE name_type='vendor' AND batch_id IN (SELECT batch_id FROM effects)
 GROUP BY batch_id
), keyed AS (
 SELECT e.item_id, e.quantity, e.amount,
        CASE WHEN e.name_type='vendor' THEN e.name_id WHEN v.named=1 THEN v.vendor_id END AS party
 FROM effects e LEFT JOIN effect_vendor v ON v.batch_id=e.batch_id
)
"""

_BY_VENDOR = _PURCHASES + """, grouped AS (
 SELECT party, bookflow_sum_int(amount) AS amount FROM keyed GROUP BY party
), selected AS (
 SELECT g.party, g.amount, v.id AS vendor_id, v.name, v.name_key, v.active
 FROM grouped g LEFT JOIN vendors v ON v.id=g.party WHERE g.amount!='0'
) """

_BY_ITEM = _PURCHASES + """, grouped AS (
 SELECT item_id, bookflow_sum_int(amount) AS amount, bookflow_sum_int(quantity) AS quantity
 FROM keyed GROUP BY item_id
), selected AS (
 SELECT g.item_id, g.amount, g.quantity, i.full_name, i.name, i.full_name_key, i.type, i.active
 FROM grouped g LEFT JOIN items i ON i.id=g.item_id WHERE g.amount!='0' OR g.quantity!='0'
) """

_VENDOR_ORDER = "name_key IS NULL, name_key, coalesce(party,'')"
_ITEM_ORDER = "full_name_key IS NULL, full_name_key, coalesce(item_id,'')"


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _active(value):
    return None if value is None else bool(value)


def purchases_by_vendor(inp: PurchasesByVendorInput, s, *, principal_id=None) -> PurchasesByVendorOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "purchases-by-vendor", principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"date_from": inp.date_from, "date_to": inp.date_to}
        grand = sum(money(int(value), currency).minor_units
                    for value, in raw.execute(_BY_VENDOR + "SELECT amount FROM selected", params))
        every = sum(money(int(value), currency).minor_units
                    for value, in raw.execute(_PURCHASES + "SELECT coalesce(bookflow_sum_int(amount),'0') FROM keyed", params))
        if grand != every:
            raise BookflowError("E_INTERNAL", message="Purchases by vendor do not add up to the period's item purchases")
        page = _rows(raw.execute(_BY_VENDOR + f"""SELECT * FROM selected ORDER BY {_VENDOR_ORDER}
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["amount"]), currency)
            text, coefficient = _percent(amount.minor_units, grand)
            rows.append(PurchasesByVendorRow(vendor_id=row["vendor_id"], current_vendor_name=row["name"],
                display_vendor_label=NO_VENDOR if row["name"] is None else str(row["name"]),
                active=_active(row["active"]), amount=amount,
                percent_of_total=text, percent_of_total_millionths=coefficient))
        return PurchasesByVendorOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=PurchasesTotals(amount=money(grand, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def purchases_by_item(inp: PurchasesByItemInput, s, *, principal_id=None) -> PurchasesByItemOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, "purchases-by-item", principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"date_from": inp.date_from, "date_to": inp.date_to}
        grand = sum(money(int(value), currency).minor_units
                    for value, in raw.execute(_BY_ITEM + "SELECT amount FROM selected", params))
        page = _rows(raw.execute(_BY_ITEM + f"""SELECT * FROM selected ORDER BY {_ITEM_ORDER}
            LIMIT :limit OFFSET :offset""", {**params, "limit": inp.limit + 1, "offset": offset}))
        rows = []
        for row in page[:inp.limit]:
            amount = money(int(row["amount"]), currency)
            quantity = int(row["quantity"])
            text, coefficient = _percent(amount.minor_units, grand)
            average = (money(round_ratio_half_even(amount.minor_units * QUANTITY_SCALE, quantity), currency)
                       if quantity else None)
            rows.append(PurchasesByItemRow(item_id=row["item_id"], current_item_label=row["full_name"],
                current_item_name=row["name"],
                display_item_label=NO_ITEM if row["full_name"] is None else str(row["full_name"]),
                item_type=row["type"], active=_active(row["active"]),
                quantity=format_quantity_micro_units(quantity), quantity_microunits=quantity,
                amount=amount, average_cost=average,
                percent_of_total=text, percent_of_total_millionths=coefficient))
        return PurchasesByItemOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=PurchasesTotals(amount=money(grand, currency)),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


# ---------------------------------------------------------------- open purchase orders

_OPEN_ORDERS = """
SELECT t.id, t.number, t.status, r.id AS revision_id, r.date, r.expected_date, r.reference,
       r.memo, r.total_minor_units, r.vendor_id, v.name, v.name_key, v.active
FROM purchase_orders t JOIN purchase_order_revisions r ON r.id=t.current_revision_id
LEFT JOIN vendors v ON v.id=r.vendor_id
WHERE t.status IN ('open', 'partly_received')
  AND NOT EXISTS (SELECT 1 FROM purchase_order_conversions k WHERE k.source_document_id=t.id)
  AND r.date<=:date_to AND (:vendor IS NULL OR r.vendor_id=:vendor)
ORDER BY r.date, t.number, t.id
"""

# What each ordered line has had received against it: the active physical claims the
# receipts made on it. A released claim was given back by a receipt correction or a void.
_RECEIVED = """
SELECT l.line_id, l.quantity_microunits, l.amount_minor_units,
       coalesce((SELECT sum(c.quantity_microunits) FROM purchase_order_receipt_claims c
                 WHERE c.order_line_id=l.line_id
                   AND NOT EXISTS (SELECT 1 FROM purchase_order_receipt_releases x WHERE x.claim_id=c.id)), 0)
FROM purchase_order_lines l WHERE l.revision_id=:revision
"""


def _open_amount(ordered_quantity, amount, received_quantity):
    """The ordered amount of what has not arrived, by exact proportion of the line.

    A line with no quantity -- an account line -- stays open for its whole amount until
    the order is billed or closed. A received share is rounded half to even at the cent.
    """
    if not ordered_quantity:
        return amount
    received = min(received_quantity, ordered_quantity)
    return amount - round_ratio_half_even(amount * received, ordered_quantity)


def open_purchase_orders(inp: OpenPurchaseOrdersInput, s, *, principal_id=None) -> OpenPurchaseOrdersOutput:
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        vendor_id = None
        if inp.cursor is not None:
            vendor_id = ledger._decode_cursor(inp.cursor, s.company).account_id
        elif inp.vendor is not None:
            from bookflow.company.parties import resolve_party
            vendor_id = resolve_party(s.company, "vendor", inp.vendor)["id"]
        state, offset = ledger._state(s, inp, "open-purchase-orders", principal_id, vendor_id, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        found = []
        for order in _rows(raw.execute(_OPEN_ORDERS, {"date_to": inp.date_to, "vendor": vendor_id})):
            open_balance = sum(_open_amount(quantity, amount, received)
                               for _, quantity, amount, received in raw.execute(_RECEIVED, {"revision": order["revision_id"]}))
            if open_balance:
                found.append((order, open_balance))
        totals = dict(amount=0, received=0, open_balance=0)
        for order, open_balance in found:
            totals["amount"] += order["total_minor_units"]
            totals["received"] += order["total_minor_units"] - open_balance
            totals["open_balance"] += open_balance
        page = found[offset:offset + inp.limit + 1]
        rows = [OpenPurchaseOrderRow(purchase_order_id=order["id"], number=order["number"], date=order["date"],
            expected_date=order["expected_date"], status=order["status"], vendor_id=order["vendor_id"],
            current_vendor_name=order["name"],
            display_vendor_label=NO_VENDOR if order["name"] is None else str(order["name"]),
            active=_active(order["active"]), reference=order["reference"], memo=order["memo"],
            amount=money(order["total_minor_units"], currency),
            received=money(order["total_minor_units"] - open_balance, currency),
            open_balance=money(open_balance, currency))
            for order, open_balance in page[:inp.limit]]
        return OpenPurchaseOrdersOutput(metadata=state.metadata, rows=rows, count=len(rows),
            totals=OpenPurchaseOrdersTotals(**{key: money(value, currency) for key, value in totals.items()}),
            next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
