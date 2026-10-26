"""Input and output shapes for serial and lot numbers (R104 increment 4, design/advanced-inventory.md §4)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

Number = Annotated[str, Field(min_length=1, max_length=128), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
# A real calendar date, as every date field takes it (2017-02-30 is refused).
from bookflow.company.journal_models import _Date as IsoDate  # noqa: E402
Selector = Annotated[str, Field(min_length=1, max_length=1004), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]



def _blank_is_none(value):
    return None if isinstance(value, str) and not value.strip() else value


def _drop_blanks(value):
    return [n for n in value if not (isinstance(n, str) and not n.strip())] if isinstance(value, list) else value


# On a stock line a blank lot, or a blank entry among serial numbers (a form's empty cell), is no number at all.
LineLot = Annotated[Number | None, BeforeValidator(_blank_is_none)]
LineSerials = Annotated[list[Number] | None, BeforeValidator(_drop_blanks)]

# A blank bin cell is no bin named (R104 increment 5).
LineBin = Annotated[Selector | None, BeforeValidator(_blank_is_none)]

NUMBER_FIELDS = ('lot', 'lot_expiry', 'serial_numbers', 'bin')
BIN_HELP = ("Bin at the line's site the units go to or leave from (R104 increment 5), by id, full name such as "
            "A:3 or label code; the site must track bins. \"(no bin)\" names the units outside every bin. Omitted: the "
            "bin the line's stock stood in (a correction), its sale's bin (a return), else the item's default bin at "
            "the site, else no bin -- and an outgoing line takes its units from the default bin when it holds them, "
            "else from no bin, else from the bins holding them, never driving a bin below zero while the units are "
            "elsewhere at the site.")
LOT_HELP = ("Lot number of a lot-tracked item: the lot an incoming line receives into (made when new), or the lot an "
            "outgoing line takes from. Omitted on an outgoing line, the units leave unassigned and the result names "
            "the earliest-expiring lot in stock there.")
EXPIRY_HELP = "Expiry date of a new lot (ISO); change an existing lot's with `lot update`."
SERIAL_HELP = ("Serial numbers of a serial-tracked item, one per unit, up to the line's quantity; units left unnumbered "
               "are unassigned (number them later with `inventory assign`). Never make one up. An outgoing number leaves "
               "from where the books hold it; a number never on file is accepted with a warning and shows -1 until its "
               "receipt. Omitted on a correction, the line keeps the numbers it had.")


def number_fields():
    """The three fields a stock line gains, as ``(name, annotation, Field)``."""
    return (('lot', Number | None, Field(None, description=LOT_HELP)),
            ('lot_expiry', IsoDate | None, Field(None, description=EXPIRY_HELP)),
            ('serial_numbers', list[Number] | None, Field(None, max_length=10000, description=SERIAL_HELP)))


def drop_unset(model, values):
    """Leave the number fields out of a serialized request when they were not given, so requests written
    before serial and lot numbers existed fingerprint exactly as before."""
    for name in NUMBER_FIELDS:
        if name not in model.model_fields_set:
            values.pop(name, None)
    return values


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


# ---------------------------------------------------------------- inventory assign

class AssignInput(_Strict):
    item: Selector = Field(description='Tracked item whose units are numbered: ID, full name or barcode.')
    serial_numbers: list[Number] | None = Field(None, min_length=1, max_length=500, description=(
        'Serial numbers to give units (assign) or to take off them (unassign), one unit each.'))
    lot: Number | None = Field(None, description='Lot to put units in (assign) or take them out of (unassign).')
    lot_expiry: IsoDate | None = Field(None, description='Expiry date of a new lot.')
    quantity: str | None = Field(None, description='How many units go into or out of the lot; serial numbers count themselves.')
    site: Selector | None = Field(None, description="Site the units are at; omitted, the document line's site, else the default site.")
    bin: Selector | None = Field(None, description="Bin the units are in, at a site that tracks bins; omitted, the document line's bin, else no bin.")
    date: IsoDate | None = Field(None, description='Date the units are numbered on; omitted, today (or the document line\'s own date).')
    count_position: Literal['before', 'after'] | None = Field(None, description='Before (the default) or after a count of the site on that date.')
    document: Selector | None = Field(None, description=(
        'A document whose stock line moved the units: number those units (which water heater a sale took). Its type '
        'and number, e.g. "invoice 1001", or its transaction ID.'))
    line: Selector | None = Field(None, description='Line identity of that document\'s stock line; omitted when it has one line of the item.')
    direction: Literal['assign', 'unassign'] = Field('assign', description=(
        'assign: unassigned units take the numbers. unassign: numbered units become unassigned.'))
    memo: str | None = Field(None, max_length=2000)


class PlaceQuantity(_Strict):
    site_id: str
    site_name: str
    bin_id: str | None = None
    lot: str | None = None
    serial_number: str | None = None
    quantity: str


class AssignOutput(_Strict):
    id: str | None
    item_id: str
    item_name: str
    site_id: str
    site_name: str
    date: str
    count_position: str
    direction: str
    document: str | None = Field(None, description='The document line whose units were numbered, in words; null for stock on hand.')
    numbered: list[PlaceQuantity] = Field(description='What moved: each number and the unassigned bucket, signed.')
    unassigned_after: str = Field(description="Unassigned units of the item at the site on the date, after this.")
    posted: bool = Field(False, description='Always false: an assignment posts nothing.')
    warnings: list[str] = Field(default_factory=list)
    status: str


# ---------------------------------------------------------------- lot and serial-number lists

class NumberShowInput(_Strict):
    item: Selector | None = Field(None, description='Item the number belongs to; needed only when two items share the number.')
    number: Selector = Field(description='The lot or serial number, or its ID.')
    as_of: IsoDate | None = Field(None, description='Date to read where it is; omitted, today.')


class NumberListInput(_Strict):
    item: Selector | None = Field(None, description='Only this item\'s numbers.')
    site: Selector | None = Field(None, description='Only numbers held at this site.')
    in_stock: bool = Field(False, description='Only numbers with a quantity on hand.')
    expiring_by: IsoDate | None = Field(None, description='Lots only: those expiring on or before this date.')
    as_of: IsoDate | None = None
    limit: int = Field(200, ge=1, le=1000)


class LotUpdateInput(_Strict):
    item: Selector | None = Field(None, description='Item the lot belongs to; needed only when two items share the number.')
    number: Selector = Field(description='The lot number or its ID.')
    expected_version: int | None = Field(None, ge=1)
    expiry_date: IsoDate | None = Field(None, description='New expiry date; give null with clear_expiry to remove it.')
    clear_expiry: bool = False
    memo: str | None = Field(None, max_length=2000)


class SerialUpdateInput(_Strict):
    item: Selector | None = Field(None, description='Item the serial number belongs to; needed only when two items share it.')
    number: Selector = Field(description='The serial number or its ID.')
    expected_version: int | None = Field(None, ge=1)
    memo: str | None = Field(None, max_length=2000)


class NumberOutput(_Strict):
    id: str
    kind: Literal['lot', 'serial_number']
    item_id: str
    item_name: str
    number: str
    version: int
    expiry_date: str | None = None
    expired: bool = False
    memo: str | None = None
    as_of: str
    on_hand: str = Field(description='Quantity on hand across sites (a serial number: 1, 0, or -1 when sold before its receipt).')
    places: list[PlaceQuantity] = Field(default_factory=list, description='Where it is: each site holding it.')
    last_document: str | None = Field(None, description='The document that last moved it, in words.')
    status: str | None = None


class NumberPageOutput(_Strict):
    numbers: list[NumberOutput]
    count: int


# ---------------------------------------------------------------- reports

class StockReportInput(_Strict):
    item: Selector | None = Field(None, description='Only this item.')
    site: Selector | None = Field(None, description='Only this site.')
    expiring_by: IsoDate | None = Field(None, description='Only lots expiring on or before this date.')
    as_of: IsoDate | None = Field(None, description='Date the quantities are read as of; omitted, today.')
    include_unassigned: bool = Field(True, description='Show each place\'s unassigned units too.')
    limit: int = Field(500, ge=1, le=5000)


class StockRow(_Strict):
    item_id: str
    item_name: str
    tracking: str
    number: str | None = Field(description='Lot or serial number; null for the unassigned bucket.')
    site_id: str
    site_name: str
    bin_id: str | None = None
    quantity: str
    expiry_date: str | None = None
    expired: bool = False


class StockReportOutput(_Strict):
    as_of: str
    rows: list[StockRow]
    count: int


class HistoryInput(_Strict):
    item: Selector | None = Field(None, description='Item the number belongs to; needed only when two items share it.')
    number: Selector = Field(description='The serial or lot number.')
    kind: Literal['serial', 'lot'] | None = Field(None, description='serial or lot; omitted, whichever the number is.')


class HistoryRow(_Strict):
    date: str
    count_position: str
    document: str
    document_type: str | None = None
    transaction_id: str | None = None
    name: str | None = Field(None, description='Customer or vendor on the document.')
    site_name: str
    quantity_in: str
    quantity_out: str
    balance: str


class HistoryOutput(_Strict):
    item_id: str
    item_name: str
    kind: str
    number: str
    rows: list[HistoryRow]
    on_hand: str


class TraceLink(_Strict):
    document: str
    date: str
    item_name: str
    numbers: list[str] = Field(default_factory=list)


class TraceOutput(HistoryOutput):
    customers: list[str] = Field(description='Every customer who received units of the number, directly or inside an assembly.')
    vendors: list[str] = Field(description='Every vendor it came from.')
    went_into: list[TraceLink] = Field(description='Builds that consumed it, with the assembly and its numbers.')
    made_from: list[TraceLink] = Field(description='When it is an assembly\'s number: the components and their numbers it was built from.')
