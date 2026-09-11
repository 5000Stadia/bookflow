"""What the company holds and what it is worth, read straight off the movement ledger.

Both reports are the same rows with different columns beside them, so they are the same
query. On-hand quantity and asset value are the running sums of ``inventory_movements`` up to
the as-of date -- corrections included, because a correction is a row like any other and
carries the date of the movement it corrects.

**The correctness property of this whole increment is asserted here, on every run.** The
inventory-asset accounts' balance for the as-of date is read from the posting lines, and the
total of these rows is read from the movements. They are the same amount or the report
refuses: every movement is one inventory-asset posting line and every such line is exactly
one movement, so if these two ever disagree something has posted to a control account that
no item owns, and printing a valuation that the balance sheet contradicts would be worse
than saying so.

Reporting uses identical effective-date bounds on both sides -- ``effective_date <= as_of``
on the movement and on the posting batch -- which is what makes the comparison meaningful on
any date, not merely today.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bookflow.company import inventory, ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date, money
from bookflow.core.errors import BookflowError
from bookflow.core.exact import format_quantity_micro_units, round_ratio_half_even

MICRO = 10 ** 6


class StockInput(StrictModel):
    as_of: str = Field(min_length=10, max_length=10,
                       description="Inclusive accounting as-of date, YYYY-MM-DD; stock is valued at it.")
    basis: Literal["accrual"] = "accrual"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _as_of = field_validator("as_of")(iso_date)

    @property
    def date_to(self) -> str:
        return self.as_of


class InventoryValuationInput(StockInput):
    pass


class StockStatusInput(StockInput):
    pass


class StockRow(StrictModel):
    item_id: str
    item_name: str
    item_type: str
    description: str | None
    active: bool
    quantity_on_hand: str
    average_cost: MoneyOutput
    asset_value: MoneyOutput


class InventoryValuationRow(StockRow):
    pass


class StockStatusRow(StockRow):
    quantity_available: str
    quantity_on_order: str
    reorder_point_min: str | None
    reorder_point_max: str | None
    below_reorder_point: bool
    preferred_vendor_id: str | None


class StockTotals(StrictModel):
    asset_value: MoneyOutput


class StockStatusTotals(StockTotals):
    below_reorder_point: int = Field(ge=0)


class InventoryValuationOutput(ledger.Page):
    totals: StockTotals
    rows: list[InventoryValuationRow]


class StockStatusOutput(ledger.Page):
    totals: StockStatusTotals
    rows: list[StockStatusRow]


# The stock-carrying item types come from the inventory module, which derives them from the
# item master's own profile registry. Writing the two names here again is the hand-listed set
# this codebase keeps getting bitten by.
_TYPES = ", ".join(f"'{name}'" for name in inventory.TRACKED_TYPES)

# Never arithmetic on bookflow_sum_int: it returns lossless text and SQLite coerces text
# arithmetic to REAL. Both sums are second-level aggregates over stored integer columns and
# every comparison and subtraction below happens in Python.
_SELECTED = f"""
WITH stock AS (
 SELECT item_id, bookflow_sum_int(quantity_microunits) AS quantity,
        bookflow_sum_int(value_minor_units) AS value
 FROM inventory_movements WHERE effective_date<=:date_to GROUP BY item_id
), selected AS (
 SELECT i.id, i.full_name, i.full_name_key, i.type, i.active, i.description,
        i.reorder_point_min_microunits AS reorder_min, i.reorder_point_max_microunits AS reorder_max,
        i.preferred_vendor_id,
        coalesce(s.quantity,'0') AS quantity, coalesce(s.value,'0') AS value
 FROM items i LEFT JOIN stock s ON s.item_id=i.id
 WHERE i.type IN ({_TYPES})
   AND (i.active=1 OR coalesce(s.quantity,'0')!='0' OR coalesce(s.value,'0')!='0')
) """

# The same date bound on the other side of the tie: what the general ledger says the
# inventory-asset accounts hold on the as-of date.
_CONTROL = """
SELECT bookflow_sum_int(l.debit_minor_units-l.credit_minor_units)
FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id
JOIN accounts a ON a.id=l.account_id
WHERE a.system_role='inventory_asset' AND b.effective_date<=:date_to
"""


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _average(value: int, quantity: int) -> int:
    return 0 if quantity <= 0 else round_ratio_half_even(value * MICRO, quantity)


def _quantity(value) -> str:
    return format_quantity_micro_units(int(value))


def _below(quantity: int, reorder_min) -> bool:
    return reorder_min is not None and quantity <= int(reorder_min)


def _check_tie(raw, params, total: int) -> None:
    """Refuse rather than print a valuation the balance sheet contradicts.

    Only an inventory adjustment may post to an inventory-asset account, so a difference
    means one of two things: a defect in what writes the movements, or a company whose books
    already carried a hand-typed entry on that account from before this ledger existed. The
    second has a remedy a bookkeeper can carry out -- `journal void` on that entry, then
    `inventory adjust` to put the stock where it belongs -- so the message names the gap
    rather than only reporting that there is one.
    """
    control = raw.execute(_CONTROL, params).fetchone()[0]
    control = 0 if control is None else int(control)
    if control != total:
        raise BookflowError("E_INTERNAL", message=(
            f"The inventory asset in the general ledger is {control} minor units on "
            f"{params['date_to']} and the items hold {total}. An entry has posted to an "
            "inventory-asset account without saying which item it belongs to; find it with "
            "`report general-ledger` on that account, void it, and record the stock with "
            "`inventory adjust`."), details={
            "general_ledger_minor_units": control, "stock_total_minor_units": total,
            "difference_minor_units": control - total, "as_of": params["date_to"]})


def _run(inp, s, principal_id, report):
    """Both reports: the same selected rows, totalled over the whole filter, then paged."""
    with ledger._snapshot(s.company):
        ledger.register_ledger_functions(s.company)
        state, offset = ledger._state(s, inp, report, principal_id, None, account_scoped=False)
        raw, currency = s.company.raw, state.metadata.currency
        params = {"date_to": inp.as_of}
        total = below = 0
        # Stream every row, including those past this page, so a page boundary can never
        # hide an amount from a total or a count.
        for quantity, value, reorder_min in raw.execute(
                _SELECTED + "SELECT quantity, value, reorder_min FROM selected", params):
            total += money(int(value), currency).minor_units
            below += int(_below(int(quantity), reorder_min))
        _check_tie(raw, params, total)
        page = _rows(raw.execute(
            _SELECTED + """SELECT * FROM selected
            ORDER BY full_name_key, id LIMIT :limit OFFSET :offset""",
            {**params, "limit": inp.limit + 1, "offset": offset}))
        return state, offset, page, currency, total, below


def _shared(row, currency):
    quantity, value = int(row["quantity"]), int(row["value"])
    return dict(item_id=row["id"], item_name=row["full_name"], item_type=row["type"],
                description=row["description"], active=bool(row["active"]),
                quantity_on_hand=_quantity(quantity),
                average_cost=money(_average(value, quantity), currency),
                asset_value=money(value, currency))


def inventory_valuation(inp: InventoryValuationInput, s, *, principal_id=None) -> InventoryValuationOutput:
    state, offset, page, currency, total, _ = _run(inp, s, principal_id, "inventory-valuation")
    rows = [InventoryValuationRow(**_shared(row, currency)) for row in page[:inp.limit]]
    return InventoryValuationOutput(
        metadata=state.metadata, rows=rows, count=len(rows),
        totals=StockTotals(asset_value=money(total, currency)),
        next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))


def stock_status(inp: StockStatusInput, s, *, principal_id=None) -> StockStatusOutput:
    state, offset, page, currency, total, below = _run(inp, s, principal_id, "stock-status")
    rows = []
    for row in page[:inp.limit]:
        quantity = int(row["quantity"])
        rows.append(StockStatusRow(
            **_shared(row, currency),
            # Nothing commits or orders stock yet -- no sales order, purchase order or build
            # exists -- so available is on hand and on order is nothing. Both are here because
            # a reader needs the column, and both become real when those documents land.
            quantity_available=_quantity(quantity), quantity_on_order="0",
            reorder_point_min=None if row["reorder_min"] is None else _quantity(row["reorder_min"]),
            reorder_point_max=None if row["reorder_max"] is None else _quantity(row["reorder_max"]),
            below_reorder_point=_below(quantity, row["reorder_min"]),
            preferred_vendor_id=row["preferred_vendor_id"]))
    return StockStatusOutput(
        metadata=state.metadata, rows=rows, count=len(rows),
        totals=StockStatusTotals(asset_value=money(total, currency), below_reorder_point=below),
        next_cursor=ledger._continuation(state, offset, len(rows), len(page) > inp.limit, s.company))
