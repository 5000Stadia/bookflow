"""Inputs and outputs of physical counts (R104 increment 2, design/advanced-inventory.md §6)."""
from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.exact import parse_quantity_micro_units


class _Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _Output(BaseModel):
    model_config = ConfigDict(extra='forbid')


def _iso_date(value: Any) -> str:
    from datetime import date as calendar_date
    import re
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('must be an ISO date YYYY-MM-DD')
    calendar_date.fromisoformat(value)
    return value


def _quantity(value: Any) -> str:
    if parse_quantity_micro_units(value, field='quantity') < 0:
        raise ValueError('a counted quantity cannot be below zero')
    return value


Selector = Annotated[str, Field(min_length=1, max_length=1000),
                     BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
Date = Annotated[str, Field(min_length=10, max_length=10), BeforeValidator(_iso_date)]
Quantity = Annotated[str, Field(min_length=1, max_length=40), BeforeValidator(_quantity)]
Version = Annotated[int, Field(strict=True, ge=1)]
Status = Literal['open', 'recorded', 'cancelled', 'voided']


class CountCreateInput(_Input):
    site: Selector | None = Field(None, description='Site to count, by id or name; omitted, the default site.')
    date: Date | None = Field(None, description="Date of the count; omitted, today. Its differences post at this date.")
    items: list[Selector] | None = Field(None, min_length=1, max_length=500,
                                         description='Count only these items (id, name or barcode).')
    category: Selector | None = Field(None, description='Count only the items in this item category.')
    bins: list[Selector] | None = Field(None, description='Count these bins (bins arrive with a later increment).')
    cycle: bool = Field(False, description='Count the next items due by cycle class (arrives with bins and cycle counts).')
    limit: int | None = Field(None, ge=1, le=500, description='With cycle: how many due items to count.')
    blind: bool = Field(False, description='Hide expected quantities and differences on the sheet; they show only in the post preview.')
    memo: str | None = Field(None, max_length=2000, description='Memo for the count.')
    number: str | None = Field(None, min_length=1, max_length=64, description='Count number; omit for the next (C-1, C-2, ...).')

    @model_validator(mode='after')
    def one_selection(self) -> Self:
        chosen = [name for name in ('items', 'category', 'bins') if getattr(self, name) is not None] + (
            ['cycle'] if self.cycle else [])
        if len(chosen) > 1:
            raise ValueError('choose one of items, category, bins or cycle, or none for every stocked item')
        if self.limit is not None and not self.cycle:
            raise ValueError('limit applies only with cycle')
        return self


class CountEntryInput(_Input):
    item: Selector | None = Field(None, description='Item counted, by id, name or barcode.')
    code: str | None = Field(None, min_length=1, max_length=128, description="A scanned barcode naming the item.")
    bin: Selector | None = Field(None, description='Bin counted (bins arrive with a later increment).')
    lot: Selector | None = Field(None, description='Lot counted (lots arrive with a later increment).')
    serial_number: Selector | None = Field(None, description='Serial number counted (a later increment).')
    quantity: Quantity = Field(description='Quantity counted at the place, in the item\'s base unit; 0 counts it empty.')
    mode: Literal['set', 'add'] = Field('set', description='set (the default): the place holds this many. add: this many more '
                                                          '(a scan adds 1).')

    @model_validator(mode='after')
    def names_one_item(self) -> Self:
        if (self.item is None) == (self.code is None):
            raise ValueError('name the item, or give the scanned code, not both')
        return self


class CountEnterInput(_Input):
    count: Selector = Field(description='Count id or number.')
    entries: list[CountEntryInput] = Field(min_length=1, max_length=500, description='1-500 entries, appended in order.')


class CountSelectInput(_Input):
    count: Selector = Field(description='Count id or number.')


class CountQueryInput(_Input):
    site: Selector | None = Field(None, description='Only counts of this site.')
    status: Status | None = Field(None, description='Only counts with this status.')
    date_from: Date | None = None
    date_to: Date | None = None
    limit: int = Field(50, ge=1, le=500)


class CountPostInput(_Input):
    count: Selector = Field(description='Count id or number.')
    expected_version: Version = Field(description='Version of the count this post is for, from its show or sheet.')
    adjustment_account: Selector = Field(description='Account carrying the other side of the differences, such as '
                                                     'Inventory Shrinkage or Cost of Goods Sold.')
    class_id: Selector | None = Field(None, description='Class captured on the posted differences.')
    preview_fingerprint: str | None = Field(None, max_length=128, description=(
        'From the dry run of this post. The count is recorded only if what it would post is still exactly what that '
        'preview showed (E_PREVIEW_STALE otherwise).'))


class CountEndInput(_Input):
    count: Selector = Field(description='Count id or number.')
    expected_version: Version = Field(description='Version of the count, from its show.')


class CountLineOutput(_Output):
    line_id: str
    position: int
    item_id: str
    item_name: str
    description: str | None = None
    unit: str | None = Field(None, description="The item's base unit, where it has one.")
    place: str = Field(description='Where at the site: "unassigned" until bins, lots and serial numbers arrive.')
    origin: Literal['sheet', 'found']
    expected: str | None = Field(None, description="What the books hold at the place at the count's position; null on a blind sheet.")
    counted: str | None = Field(None, description='What was counted; null while nobody has counted the place.')
    difference: str | None = Field(None, description='Counted less expected; null when blind or uncounted.')
    entries: int = Field(0, description='How many entries counted this line.')


class CountDifferenceOutput(_Output):
    item_id: str
    item_name: str
    expected: str
    counted: str
    difference: str
    value: JournalMoneyOutput = Field(description='Its value at the average at the count: negative for a loss, positive for found.')
    movement_kind: Literal['issue', 'found', 'none']


class CountDocumentOutput(_Output):
    transaction_id: str
    number: str
    purpose: Literal['record', 'rederive', 'void']
    date: str


class SameDayDocument(_Output):
    kind: str
    id: str
    number: str | None = None
    position: Literal['before', 'after']
    entered_at: str


class CountOutput(_Output):
    id: str | None = None
    number: str
    version: int
    status: Literal['open', 'recorded', 'cancelled', 'voided', 'will_record', 'will_void', 'will_cancel', 'will_create']
    site_id: str
    site_name: str
    date: str
    blind: bool
    selection: dict = Field(default_factory=dict, description='What the sheet was made from: kind all, items, or category.')
    memo: str | None = None
    adjustment_account_id: str | None = None
    recorded_at: str | None = None
    recorded_by: str | None = None
    lines: list[CountLineOutput] = Field(default_factory=list)
    counted_lines: int = 0
    uncounted_lines: int = 0
    documents: list[CountDocumentOutput] = Field(default_factory=list, description='Journal entries that posted its differences.')
    warnings: list[str] = Field(default_factory=list)


class CountEnterOutput(CountOutput):
    entered: list[CountLineOutput] = Field(default_factory=list, description='The lines these entries counted, after them.')
    uncounted: list[CountLineOutput] = Field(default_factory=list, description='Lines nobody has counted yet.')


class CountPostOutput(CountOutput):
    preview_fingerprint: str = Field(description='Pass this to post; it changes if anything the post would write changes.')
    differences: list[CountDifferenceOutput] = Field(default_factory=list, description='Per item: what posts.')
    total_value: JournalMoneyOutput
    after_counting_began: list[SameDayDocument] = Field(
        default_factory=list, description="Stock documents dated the count's date and entered after it was created, with "
                                          'their position against it.')


class CountPageOutput(_Output):
    counts: list[CountOutput]
    count: int
