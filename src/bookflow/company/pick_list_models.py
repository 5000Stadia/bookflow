"""Input and output shapes for pick lists (R181 increment 3, design/pick-pack-ship.md §4)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookflow.company.shipment_models import IsoDate, Quantity, Selector, _Strict


class PickListCreateInput(_Strict):
    site: Selector | None = Field(default=None, description='Site to pick at; default the default site.')
    orders: list[Selector] | None = Field(default=None, min_length=1, max_length=200, description=(
        'Sales orders to pick, by number or id; one list may cover several (a wave). Give this or ready.'))
    ready: bool = Field(default=False, description=(
        'Every open sales order with something that can ship at the site now, oldest first.'))
    complete_only: bool = Field(default=False, description='Only orders that can ship whole.')
    limit: int | None = Field(default=None, ge=1, le=200, description='At most this many orders.')
    picker: str | None = Field(default=None, max_length=200, description='Who picks it: a user or free text.')
    date: IsoDate | None = Field(default=None, description='Date of the list; default today.')
    memo: str | None = Field(default=None, max_length=4000)


class PickEntryInput(_Strict):
    line: str | None = Field(default=None, max_length=40, description='The pick line, by position or line_id.')
    item: Selector | None = Field(default=None, description='Or the item picked (name, id or barcode).')
    sales_order: Selector | None = Field(default=None, description='With item: the order, when the list holds the '
                                         'item for two orders.')
    code: str | None = Field(default=None, min_length=1, max_length=200, description=(
        'Or a scanned code: an item barcode, or a serial-number or lot label.'))
    bin: str | None = Field(default=None, max_length=1004, description='Bin the units came from; default the line\'s.')
    lot: str | None = Field(default=None, max_length=200, description='Lot picked (lot-tracked items).')
    serial_number: str | None = Field(default=None, max_length=200, description='Serial number of the unit picked.')
    quantity: Quantity | None = Field(default=None, description='Base units; default 1.')
    mode: Literal['add', 'set'] = Field(default='add', description='add (this many more) or set (the line now '
                                        'holds this many; 0 with serial_number puts that unit back).')


class PickListEnterInput(_Strict):
    pick_list: Selector = Field(description='Pick list number or id.')
    entries: list[PickEntryInput] = Field(min_length=1, max_length=500)
    date: IsoDate | None = Field(default=None, description='Date picked; default today.')


class PickListEndInput(_Strict):
    pick_list: Selector = Field(description='Pick list number or id.')
    expected_version: int | None = Field(default=None, ge=1)
    date: IsoDate | None = Field(default=None, description=(
        'Cancel: the date staged units go back to their bins; default today.'))


class PickListShowInput(_Strict):
    pick_list: Selector = Field(description='Pick list number or id.')


class PickListQueryInput(_Strict):
    status: Literal['open', 'done', 'cancelled'] | None = None
    site: Selector | None = None
    sales_order: Selector | None = Field(default=None, description='Only lists picking this order.')
    limit: int = Field(default=50, ge=1, le=500)


class PickLineOutput(BaseModel):
    line_id: str
    position: int
    sales_order_id: str
    sales_order: str
    customer: str | None = None
    order_line_id: str
    item_id: str
    item_name: str
    quantity: str
    picked: str
    to_pick: str
    short: bool
    over: bool = False
    bin: str | None = None
    lot: str | None = None
    lot_expiry: str | None = None
    serial_hints: list[str] = Field(default_factory=list, description='Serial numbers the books hold in that bin.')
    picked_serial_numbers: list[str] = Field(default_factory=list)
    picked_lots: list[str] = Field(default_factory=list)


class PickOrderOutput(BaseModel):
    sales_order_id: str
    sales_order: str
    customer: str | None = None
    shipments: list[str] = Field(default_factory=list, description='Shipments posted from this list for the order.')


class PickHistoryOutput(BaseModel):
    version: int
    status: str
    reason: str | None = None
    created_at: str
    created_by: str


class PickListOutput(BaseModel):
    id: str | None
    number: str
    version: int
    status: str
    site_id: str
    site_name: str
    date: str
    picker: str | None = None
    memo: str | None = None
    staging_bin: str | None = None
    lines: list[PickLineOutput] = Field(default_factory=list)
    orders: list[PickOrderOutput] = Field(default_factory=list)
    entered: list[PickLineOutput] = Field(default_factory=list, description='On enter: the lines the entries reached.')
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    history: list[PickHistoryOutput] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)


class PickListRow(BaseModel):
    id: str
    number: str
    status: str
    site_name: str
    date: str
    picker: str | None = None
    orders: list[str] = Field(default_factory=list)
    lines: int
    picked_lines: int


class PickListPageOutput(BaseModel):
    rows: list[PickListRow]
    total: int
