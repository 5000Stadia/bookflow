"""Input and output shapes for shipments (R181 increment 1, design/pick-pack-ship.md)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from bookflow.company import serial_lot_models as _SL
from bookflow.company.journal_models import _Date as IsoDate
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.company.ledger_reports import ReportMetadata

Selector = Annotated[str, Field(min_length=1, max_length=1004), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]


def number_text(value):
    """A JSON number given where decimal text is asked, as that text, exactly: an integer as its digits, a float by its
    shortest repr when that is a plain decimal (0.1 is '0.1'); anything else is left for the field to refuse."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        import math
        import re
        text = repr(value)
        if math.isfinite(value) and re.fullmatch(r'-?[0-9]+(\.[0-9]+)?', text):
            return text[:-2] if text.endswith('.0') else text
    return value


Quantity = Annotated[str, Field(min_length=1, max_length=40), BeforeValidator(number_text)]
Tracking = Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(max_length=50)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ShipmentLineInput(_Strict):
    order_line: Selector | None = Field(default=None, description=(
        "The order line's line_id (as `sales-order show` lists it). Give this or item."))
    item: Selector | None = Field(default=None, description=(
        'An item exactly one of the order\'s lines with something to ship carries (name, id or barcode).'))
    quantity: Quantity | None = Field(default=None, description=(
        "How many to ship, in the order line's unit; omitted, all it has to ship."))
    bin: _SL.LineBin = Field(default=None, description=_SL.BIN_HELP)
    lot: _SL.LineLot = Field(default=None, description=_SL.LOT_HELP)
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=10000, description=_SL.SERIAL_HELP)


class _Facts(_Strict):
    count_position: Literal['before', 'after'] | None = Field(default=None, description=(
        'Before (the default) or after a physical count of its site on the ship date.'))
    ship_method: Selector | None = Field(default=None, description='Ship Via list entry; default the order\'s.')
    carrier: str | None = Field(default=None, max_length=200, description='Carrier or service, as text.')
    tracking_numbers: Tracking | None = Field(default=None, description='Tracking numbers, recorded and printed as text.')
    memo: str | None = Field(default=None, max_length=4000)


class PackageContentInput(_Strict):
    line: Selector | None = Field(default=None, description="The shipment line: its position or line_identity.")
    item: Selector | None = Field(default=None, description='An item exactly one line carries.')
    quantity: Quantity | None = Field(default=None, description="Base units in the box; omitted, the serials' count or all.")
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=10000)


class PackageInput(_Strict):
    box: int | None = Field(default=None, ge=1, le=999, description='Box number; default the next one.')
    package_type: str | None = Field(default=None, max_length=200)
    weight: Quantity | None = Field(default=None, description='Measured weight, in the company weight unit.')
    tare: Quantity | None = Field(default=None, description='Empty package weight, added to the computed weight.')
    dimensions: str | None = Field(default=None, max_length=200)
    tracking_number: str | None = Field(default=None, max_length=64)
    contents: list[PackageContentInput] = Field(default_factory=list, max_length=200)


class _Freighted(_Facts):
    freight_amount: str | None = Field(default=None, min_length=1, max_length=40, description=(
        'Freight to bill, e.g. "12.50": billed once, by the shipment\'s first invoice; "0" clears it.'))
    freight_item: Selector | None = Field(default=None, description=(
        "Other-charge or service item the freight bills as; default the company's shipping item."))


class ShipmentPostInput(_Freighted):
    shipment: Selector | None = Field(default=None, description=(
        'A draft shipment to post (packing layer): its lines, places, numbers and boxes ship as they are.'))
    expected_version: int | None = Field(default=None, ge=1, description="The draft's version, if you want it checked.")
    packages: list[PackageInput] | None = Field(default=None, min_length=1, max_length=200, description=(
        'Boxes and their contents (packing layer, needs pick_and_pack_enabled).'))
    invoice: bool = Field(default=False, description=(
        'Also invoice the shipment, in the same transaction: dated the ship date, at the order\'s prices.'))
    sales_order: Selector | None = Field(default=None, description=(
        'The sales order shipped, by number or id; with pick_list, needed only when the list covers several.'))
    pick_list: Selector | None = Field(default=None, description=(
        'Ship what was picked on this pick list for the order (bins, lots and serial numbers as picked).'))
    date: IsoDate | None = Field(default=None, description='The ship date: the date the stock left; default today.')
    site: Selector | None = Field(default=None, description=(
        'Inventory site the shipment leaves; default the order lines\' site, else the default site. One shipment '
        'leaves one site.'))
    lines: list[ShipmentLineInput] | None = Field(default=None, min_length=1, max_length=200, description=(
        "What ships. Omitted: every order line's quantity still to ship, up to what is available to the order at "
        "the site. With pick_list: part of what was picked; the rest stays on the list."))


class ShipmentUpdateInput(_Freighted):
    shipment: Selector = Field(description='Shipment number or id.')
    expected_version: int = Field(ge=1)
    date: IsoDate | None = Field(default=None, description='A new ship date; refused later than an invoice billing it.')
    lines: list[ShipmentLineInput] | None = Field(default=None, min_length=1, max_length=200, description=(
        'Every line the shipment now holds (it replaces them); omitted, the lines stay. Not below what invoices '
        'billed.'))


class ShipmentVoidInput(_Strict):
    shipment: Selector = Field(description='Shipment number or id.')
    expected_version: int | None = Field(default=None, ge=1)


class ShipmentShowInput(_Strict):
    shipment: Selector = Field(description='Shipment number or id.')


class ShipmentQueryInput(_Strict):
    sales_order: Selector | None = Field(default=None, description='Only this order\'s shipments.')
    customer: Selector | None = Field(default=None, description='Only this customer\'s shipments.')
    status: Literal['shipped', 'voided', 'draft', 'cancelled'] | None = Field(default=None, description=(
        'draft and cancelled list draft shipments (packing layer); omitted, shipments and open drafts.'))
    not_invoiced: bool = Field(default=False, description='Only shipments holding units not yet invoiced.')
    date_from: IsoDate | None = None
    date_to: IsoDate | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ShipmentInvoiceInput(_Strict):
    shipment: Selector = Field(description='Shipment number or id: its units not yet invoiced are billed.')
    date: IsoDate | None = Field(default=None, description='Invoice date, on or after the ship date; default today.')
    conversion_key: str | None = Field(default=None, min_length=1, max_length=128, description=(
        'Retry key: the same key returns the original invoice. Default derived from the shipment.'))
    number: str | None = Field(default=None, min_length=1, max_length=64)
    memo: str | None = Field(default=None, max_length=4000)
    ar_account: Selector | None = None
    terms: Selector | None = None
    apply_prepayment: bool = Field(default=True, description='Use money held on the order toward the invoice.')


class ShippedNotInvoicedInput(_Strict):
    as_of: IsoDate | None = Field(default=None, description='Waiting on this date; default today.')
    customer: Selector | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=4096, description='Continue from a previous page\'s next_cursor.')


class ShipmentLineOutput(BaseModel):
    line_identity: str
    position: int
    order_line_id: str
    item_id: str
    item_name: str
    quantity: str = Field(description='Shipped, in the item\'s base unit.')
    bin: str | None = None
    lot: str | None = None
    serial_numbers: list[str] | None = None
    cost: JournalMoneyOutput = Field(description='What the units cost: the replay\'s value of the issue, every later correction included.')
    invoiced: str = Field(description='What live invoices have billed of it.')
    returned: str = Field(default='0', description='What shipment returns not voided brought back of it.')
    not_invoiced: str = Field(description='Still out and not billed: shipped less invoiced less returned.')
    waiting: JournalMoneyOutput = Field(description='Its cost still in Shipped Not Invoiced.')


class ShipmentBillingOutput(BaseModel):
    invoice_id: str
    invoice_number: str | None
    date: str
    quantity: str
    line_identity: str


class ShipmentReturnRef(BaseModel):
    return_id: str
    number: str
    date: str
    status: str
    reopen: bool
    quantity: str = Field(description='Base quantity it brought back, all lines.')


class ShipmentHistoryOutput(BaseModel):
    version: int
    status: str
    date: str
    reason: str | None
    created_at: str
    created_by: str
    created_via: str


class PackageContentOutput(BaseModel):
    line_identity: str
    position: int | None
    item_name: str
    quantity: str
    serial_numbers: list[str] | None = None
    weight: str | None = Field(default=None, description='Computed: quantity times the item weight; null: no weight.')


class PackageOutput(BaseModel):
    box: int
    package_type: str | None = None
    dimensions: str | None = None
    tracking_number: str | None = None
    tare: str | None = None
    measured_weight: str | None = None
    computed_weight: str
    weight: str = Field(description='What prints and totals: the measured weight when entered, else the computed one.')
    unweighed: str = Field(default='0', description='Units in it whose item has no weight.')
    contents: list[PackageContentOutput] = Field(default_factory=list)


class UnboxedOutput(BaseModel):
    line_identity: str
    position: int
    item_name: str
    quantity: str
    serial_numbers: list[str] | None = None


class PackingOutput(BaseModel):
    weight_unit: str
    packages: list[PackageOutput] = Field(default_factory=list)
    unboxed: list[UnboxedOutput] = Field(default_factory=list)
    packed: bool = Field(description='Every unit of every line is in a box.')
    total_weight: str
    unweighed: str = Field(default='0', description='Units whose item has no weight, boxed or not.')


class FreightOutput(BaseModel):
    amount: JournalMoneyOutput
    item_id: str
    item_name: str | None = None
    billed_by: str | None = Field(default=None, description='Invoice number that billed it; null: not yet billed.')
    to_bill: bool = Field(default=True, description='Still to bill: a later invoice of its units carries it.')


class ShipmentInvoiceRef(BaseModel):
    id: str
    number: str | None = None
    total: JournalMoneyOutput | None = None


class ShipmentOutput(BaseModel):
    id: str | None
    number: str | None
    version: int | None
    status: str
    sales_order_id: str
    sales_order_number: str | None
    customer_id: str
    customer_name: str | None
    date: str
    count_position: str
    site_id: str
    site_name: str | None
    ship_method_id: str | None = None
    carrier: str | None = None
    tracking_numbers: list[str] = Field(default_factory=list)
    memo: str | None = None
    transaction_id: str | None = None
    lines: list[ShipmentLineOutput]
    cost: JournalMoneyOutput = Field(description='What the shipment\'s units cost in all.')
    shipped_not_invoiced: JournalMoneyOutput = Field(description='Cost still waiting in Shipped Not Invoiced.')
    invoices: list[ShipmentBillingOutput] = Field(default_factory=list)
    returns: list[ShipmentReturnRef] = Field(default_factory=list, description='Shipment returns of its units, voided ones too.')
    order_to_ship: dict[str, str] = Field(default_factory=dict, description='Each order line\'s quantity still to ship after this.')
    history: list[ShipmentHistoryOutput] = Field(default_factory=list)
    packing: PackingOutput | None = Field(default=None, description='Boxes, weights and unboxed units; null: none.')
    freight: FreightOutput | None = None
    invoice: ShipmentInvoiceRef | None = Field(default=None, description='The invoice `invoice: true` made.')
    warnings: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    changed: bool = True


class ShipmentListRow(BaseModel):
    id: str
    number: str
    version: int
    status: str
    date: str
    sales_order_number: str | None
    customer_name: str | None
    site_name: str | None
    quantity: str
    cost: JournalMoneyOutput
    shipped_not_invoiced: JournalMoneyOutput
    tracking_numbers: list[str] = Field(default_factory=list)


class ShipmentListOutput(BaseModel):
    rows: list[ShipmentListRow]
    total: int


class ShippedNotInvoicedRow(BaseModel):
    customer_id: str
    customer_name: str | None
    sales_order_number: str | None
    shipment_number: str
    shipment_id: str
    ship_date: str
    line_identity: str
    item_name: str
    quantity: str
    cost: JournalMoneyOutput


class ShippedNotInvoicedOutput(BaseModel):
    metadata: ReportMetadata
    count: int = Field(ge=0, le=200, description='Rows on this page.')
    next_cursor: str | None = None
    as_of: str
    account_id: str | None
    account_name: str | None
    rows: list[ShippedNotInvoicedRow]
    total: JournalMoneyOutput
    account_balance: JournalMoneyOutput


# ---------------------------------------------------------------- shipment returns (R181 increment 2)


class ShipmentReturnLineInput(_Strict):
    shipment_line: Selector | None = Field(default=None, description=(
        "The shipment line given back: its position (1, 2, ...) or line_identity, as `shipment show` lists them. "
        'Give this or item.'))
    item: Selector | None = Field(default=None, description='An item exactly one line of the shipment carries.')
    quantity: Quantity | None = Field(default=None, description=(
        "How many came back, in the item's base unit (as the shipment shows it); omitted, all of the line still out "
        'and not invoiced.'))
    bin: _SL.LineBin = Field(default=None, description=(
        'Bin the units go back to; default the bin the shipment took them from.'))
    lot: _SL.LineLot = Field(default=None, description=_SL.LOT_HELP)
    serial_numbers: _SL.LineSerials = Field(default=None, max_length=10000, description=(
        'Serial numbers that came back; each must be one the shipment took.'))


class ShipmentReturnInput(_Strict):
    shipment: Selector = Field(description='Shipment number or id whose units came back.')
    expected_version: int | None = Field(default=None, ge=1, description="The shipment's version, if you want it checked.")
    date: IsoDate | None = Field(default=None, description='Date the units came back into stock; default today, never before the ship date.')
    lines: list[ShipmentReturnLineInput] | None = Field(default=None, min_length=1, max_length=200, description=(
        'What came back. Omitted: every unit of the shipment still out and not invoiced.'))
    reopen: bool = Field(default=True, description=(
        'True (the default): the units are to ship again on the order. False: they are cancelled on the order.'))
    count_position: Literal['before', 'after'] | None = Field(default=None, description=(
        'Before (the default) or after a physical count of the site on the return date.'))
    memo: str | None = Field(default=None, max_length=4000)


class ShipmentReturnSelector(_Strict):
    shipment_return: Selector = Field(description='Shipment return number or id.')


class ShipmentReturnVoidInput(_Strict):
    shipment_return: Selector = Field(description='Shipment return number or id.')
    expected_version: int | None = Field(default=None, ge=1)


class ShipmentReturnQueryInput(_Strict):
    shipment: Selector | None = Field(default=None, description="Only this shipment's returns.")
    customer: Selector | None = Field(default=None, description="Only this customer's returns.")
    status: Literal['returned', 'voided'] | None = None
    date_from: IsoDate | None = None
    date_to: IsoDate | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ShipmentReturnLineOutput(BaseModel):
    position: int
    shipment_line_identity: str
    shipment_line_position: int | None
    item_id: str
    item_name: str
    quantity: str = Field(description="Returned, in the item's base unit.")
    bin: str | None = None
    lot: str | None = None
    serial_numbers: list[str] | None = None
    value: JournalMoneyOutput = Field(description=(
        "What the units are worth back in stock: the replay's share of the shipment line's cost."))


class ShipmentReturnHistoryOutput(BaseModel):
    version: int
    status: str
    reason: str | None
    created_at: str
    created_by: str
    created_via: str


class ShipmentReturnOutput(BaseModel):
    id: str | None
    number: str | None
    version: int | None
    status: str
    shipment_id: str
    shipment_number: str
    sales_order_id: str
    sales_order_number: str | None
    customer_id: str
    customer_name: str | None
    date: str
    reopen: bool
    memo: str | None = None
    transaction_id: str | None = None
    lines: list[ShipmentReturnLineOutput]
    value: JournalMoneyOutput = Field(description='Taken out of Shipped Not Invoiced and put back into Inventory Asset.')
    order_to_ship: dict[str, str] = Field(default_factory=dict, description="Each order line's quantity still to ship after this.")
    history: list[ShipmentReturnHistoryOutput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_commands: list[str] = Field(default_factory=list)
    changed: bool = True


class ShipmentReturnListRow(BaseModel):
    id: str
    number: str
    version: int
    status: str
    date: str
    shipment_number: str
    sales_order_number: str | None
    customer_name: str | None
    reopen: bool
    quantity: str
    value: JournalMoneyOutput


class ShipmentReturnListOutput(BaseModel):
    rows: list[ShipmentReturnListRow]
    total: int


# ---------------------------------------------------------------- drafts and packing (R181 increment 4)


class ShipmentCreateInput(_Freighted):
    sales_order: Selector | None = Field(default=None, description=(
        'The order the draft will ship: what `shipment post` would propose, or `lines`; with pick_list, needed only '
        'when the list covers several orders.'))
    pick_list: Selector | None = Field(default=None, description="Draft what was picked on this list for the order.")
    date: IsoDate | None = Field(default=None, description='Planned ship date; omitted, the day it ships.')
    site: Selector | None = None
    lines: list[ShipmentLineInput] | None = Field(default=None, min_length=1, max_length=200)
    packages: list[PackageInput] | None = Field(default=None, min_length=1, max_length=200)


class ShipmentPackInput(_Strict):
    shipment: Selector = Field(description='Draft or shipped shipment, number or id.')
    expected_version: int | None = Field(default=None, ge=1)
    packages: list[PackageInput] | None = Field(default=None, max_length=200, description=(
        'The whole packing, replacing it ([] unpacks everything). Or move units with unit/line and box.'))
    unit: Selector | None = Field(default=None, description=(
        'What moves: a serial number (that unit), a lot, or an item (name, id or barcode, as a scan reads it).'))
    line: Selector | None = Field(default=None, description='Or the shipment line moved: position or line_identity.')
    quantity: Quantity | None = Field(default=None, description='Base units moved; default 1, or the serial count.')
    box: int | None = Field(default=None, ge=0, le=999, description=(
        'Box N the units go into (made if new); 0 takes them out of their box. With no unit or line, the facts '
        'below set box N.'))
    package_type: str | None = Field(default=None, max_length=200)
    weight: Quantity | None = Field(default=None, description='Measured weight of box N.')
    tare: Quantity | None = None
    dimensions: str | None = Field(default=None, max_length=200)
    tracking_number: str | None = Field(default=None, max_length=64)


class ShipmentCancelInput(_Strict):
    shipment: Selector = Field(description='Draft shipment number or id.')
    expected_version: int | None = Field(default=None, ge=1)
