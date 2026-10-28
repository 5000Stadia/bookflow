"""Shapes for the fulfillment worksheet and the shipments report (R181 increment 5, design/pick-pack-ship.md §8)."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from bookflow.company import ledger_reports as ledger
from bookflow.company.ledger_reports import MoneyOutput, StrictModel, iso_date
from bookflow.core.clock import TODAY_DEFAULT, today_default

STATUSES = ('to_pick', 'picking', 'picked', 'packed', 'partly_shipped', 'shipped')
_DATE = {"format": "date"}


def _date(value):
    return None if value is None else iso_date(value)


class OrderFulfillmentInput(StrictModel):
    as_of: str = Field(default_factory=today_default, min_length=10, max_length=10,
                       json_schema_extra={**TODAY_DEFAULT, **_DATE},
                       description='The day lateness counts to, YYYY-MM-DD; default today. Orders and stock are read '
                                   'as they stand now, as `shipment post` reads them.')
    site: str | None = Field(None, min_length=1, max_length=1000, description=(
        'Figures at this site (what `shipment post --site` would ship from it); only orders with something to ship '
        'there, or shipped from there and not invoiced. Default: each order at the site `shipment post` picks.'))
    customer: str | None = Field(None, min_length=1, max_length=1000, description='Only this customer or job.')
    status: str | None = Field(None, min_length=1, max_length=200, description=(
        'Only these fulfillment statuses, comma-separated: to_pick, picking, picked, packed, partly_shipped, '
        'shipped. Example: "to_pick,picking".'))
    complete_only: bool = Field(False, description='Only orders that can ship complete now.')
    can_ship: bool = Field(False, description='Only orders with something that can ship now.')
    shipped_not_invoiced: bool = Field(False, description='Only orders holding shipped units not yet invoiced.')
    newly_shippable: str | None = Field(None, min_length=10, max_length=10, json_schema_extra=_DATE, description=(
        'Only orders that can ship more now than with the stock on hand at the end of this date, YYYY-MM-DD: '
        'receipts since made them shippable.'))
    sort: Literal['date', 'shippable_amount', 'customer'] = Field('date', description=(
        'date (order date, the default), shippable_amount (largest first) or customer.'))
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator('as_of', 'newly_shippable')(_date)


class FulfillmentLine(StrictModel):
    order_line_id: str
    position: int
    item_id: str
    item_name: str
    site_name: str | None = Field(None, description='The site the order line names; null ships from any.')
    ordered: str = Field(description="In the item's base unit, as every figure here.")
    shipped: str = Field(description='On posted shipments, less shipment returns.')
    shipped_not_invoiced: str
    to_ship: str
    can_ship_now: str = Field(description='What `shipment post --sales-order` ships of it now, at the row\'s site.')
    on_picks: str = Field('0', description='Held on live pick lists (picking on).')
    picked: str = Field('0', description='Picked on live pick lists and not shipped (picking on).')
    drafted: str = Field('0', description='Held on draft shipments (packing on).')
    packed: str = Field('0', description='Of the drafted units, in boxes.')
    shippable_amount: MoneyOutput = Field(description='can_ship_now at the order price.')


class SiteShippable(StrictModel):
    site_id: str
    site_name: str
    lines: int = Field(description='Lines with something that can ship from this site now.')
    shippable_amount: MoneyOutput
    complete: bool = Field(description='Everything left to ship can ship from this site now.')


class OrderFulfillmentRow(StrictModel):
    sales_order_id: str
    sales_order_number: str
    sales_order_version: int = Field(description="The order's version when read: pass it as `expected_version` to "
                                                 "`sales-order invoice` so a stale reading bills nothing.")
    date: str
    customer_id: str
    customer_name: str
    promised_ship_date: str | None = Field(None, description="The order's ship date.")
    days_late: int | None = Field(None, description='Days past the promised ship date with something still to ship.')
    fulfillment_status: Literal['to_pick', 'picking', 'picked', 'packed', 'partly_shipped', 'shipped']
    invoice_progress: str = Field(description="The order's billing progress, as `sales-order show` reads it.")
    site_id: str | None
    site_name: str | None = Field(None, description='The site these figures ship from.')
    can_ship_complete: bool
    to_ship_amount: MoneyOutput
    shippable_amount: MoneyOutput
    shipped_not_invoiced_amount: MoneyOutput = Field(description='At the order prices.')
    pick_lists: list[str] = Field(default_factory=list, description='Live pick lists holding it (picking on).')
    drafts: list[str] = Field(default_factory=list, description='Its draft shipments (packing on).')
    packed_drafts: list[str] = Field(default_factory=list, description=(
        'Its drafts with every unit in a box: ship each with `shipment post --shipment`.'))
    by_site: list[SiteShippable] = Field(default_factory=list)
    lines: list[FulfillmentLine]
    note: str | None = Field(None, description='Why nothing ships from the order alone, and what to do instead.')


class OrderFulfillmentTotals(StrictModel):
    orders: int
    to_ship_amount: MoneyOutput
    shippable_amount: MoneyOutput
    shipped_not_invoiced_amount: MoneyOutput


class OrderFulfillmentOutput(ledger.Page):
    as_of: str
    sort: str
    pick_and_pack: bool = Field(description='Whether picking and packing are on: the to-pick, picking and packed '
                                            'statuses only come from them.')
    totals: OrderFulfillmentTotals
    rows: list[OrderFulfillmentRow]


class ShipmentsReportInput(StrictModel):
    date_from: str | None = Field(None, min_length=10, max_length=10, json_schema_extra=_DATE,
                                  description='Earliest ship date, YYYY-MM-DD.')
    date_to: str = Field(default_factory=today_default, min_length=10, max_length=10,
                         json_schema_extra={**TODAY_DEFAULT, **_DATE},
                         description='Last ship date, YYYY-MM-DD; default today.')
    customer: str | None = Field(None, min_length=1, max_length=1000, description='Only this customer or job.')
    site: str | None = Field(None, min_length=1, max_length=1000, description='Only shipments from this site.')
    ship_method: str | None = Field(None, min_length=1, max_length=1000, description='Only this Ship Via entry.')
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096)

    _dates = field_validator('date_from', 'date_to')(_date)

    @model_validator(mode='after')
    def interval(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class ShipmentsReportBox(StrictModel):
    box: int
    package_type: str | None = None
    weight: str
    tracking_number: str | None = None


class ShipmentsReportRow(StrictModel):
    shipment_id: str
    shipment_number: str
    ship_date: str
    sales_order_number: str
    customer_name: str | None
    site_name: str | None
    ship_method: str | None
    carrier: str | None
    tracking_numbers: list[str] = Field(description="The shipment's and its boxes' tracking numbers.")
    quantity: str = Field(description="Units shipped, in the items' base units.")
    boxes: int
    packages: list[ShipmentsReportBox]
    weight: str = Field(description='Boxes and unboxed lines, in the company weight unit.')
    unweighed: str = Field(description='Units whose item has no weight.')
    freight: MoneyOutput | None = None


class ShipmentsReportTotals(StrictModel):
    shipments: int
    boxes: int
    weight: str


class ShipmentsReportOutput(ledger.Page):
    date_from: str | None
    date_to: str
    weight_unit: str
    totals: ShipmentsReportTotals
    rows: list[ShipmentsReportRow]
