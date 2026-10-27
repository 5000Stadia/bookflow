"""Inputs and outputs of bins, the items-by-bin report and cycle counts (R104 increment 5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.company.sales_models import Selector


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


SITE_HELP = 'Site the bin is at, by id or name; omitted, the bin is found by id, or by a name or label only one site has.'

# ---------------------------------------------------------------------------- bins


class BinCreateInput(_Strict):
    site: Selector | None = Field(None, description='Site the bin is at, by id or name; omitted, the default site. The site must track bins.')
    name: str = Field(min_length=1, max_length=200, description='This level of the bin name, such as "A", "3" or "Top" (no colons).')
    parent: Selector | None = Field(None, description='Enclosing bin at the same site, by id or full name such as "A:3"; bins go three levels deep.')
    barcode: str | None = Field(None, min_length=1, max_length=128, description='Label code a scanner reads; unique among active bins.')
    description: str | None = Field(None, max_length=1000)


class BinUpdateInput(_Strict):
    bin: Selector = Field(description='Bin id, full name (A:3:Top) or label code.')
    site: Selector | None = Field(None, description=SITE_HELP)
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200, description='New name for this level; the bins inside it are renamed with it.')
    barcode: str | None = Field(None, max_length=128, description='New label code; an empty string clears it.')
    description: str | None = Field(None, max_length=1000)

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in ('description', 'barcode'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class BinSelectInput(_Strict):
    bin: Selector = Field(description='Bin id, full name (A:3:Top) or label code.')
    site: Selector | None = Field(None, description=SITE_HELP)
    expected_version: _Version | None = None


class BinShowInput(_Strict):
    bin: Selector = Field(description='Bin id, full name (A:3:Top) or label code.')
    site: Selector | None = Field(None, description=SITE_HELP)
    as_of: _Date | None = Field(None, description='Stock as of this date; omitted, today.')


class BinListInput(_Strict):
    site: Selector | None = Field(None, description='Only the bins of this site.')
    include_inactive: bool = False


class BinStockRow(_Strict):
    item_id: str
    item_name: str
    lot: str | None = None
    serial_number: str | None = None
    quantity_on_hand: str


class BinOutput(_Strict):
    id: str
    version: int
    site_id: str
    site_name: str
    name: str
    full_name: str
    parent_id: str | None
    depth: int
    barcode: str | None
    description: str | None
    active: bool
    item_count: int = Field(0, description='Items with a nonzero quantity in this bin (and the bins inside it) today.')
    stock: list[BinStockRow] = Field(default_factory=list, description='On show: what the bin and the bins inside it hold.')


class BinWriteOutput(BinOutput):
    changed: bool = True
    renamed: list[str] = Field(default_factory=list, description='Full names of the bins inside it renamed with it.')


class BinPageOutput(_Strict):
    bins: list[BinOutput]


# ---------------------------------------------------------------------------- items by bin


class ItemsByBinInput(_Strict):
    site: Selector | None = Field(None, description='Site to read; omitted, every site that tracks bins.')
    bin: Selector | None = Field(None, description='Only this bin and the bins inside it.')
    item: Selector | None = Field(None, description='Only this item.')
    as_of: _Date | None = Field(None, description='Stock as of this date; omitted, today.')
    include_zero: bool = Field(False, description='Also list places that hold nothing.')


class ItemsByBinRow(_Strict):
    site_id: str
    site_name: str
    bin_id: str | None
    bin: str = Field(description='Bin full name, or "(no bin)".')
    item_id: str
    item_name: str
    lot: str | None = None
    serial_number: str | None = None
    quantity_on_hand: str


class ItemsByBinOutput(_Strict):
    as_of: str
    rows: list[ItemsByBinRow]
    site_totals: list[dict] = Field(default_factory=list, description='Each site and item: its bins (with no bin) add up to what the site holds.')


# ---------------------------------------------------------------------------- cycle classes and counts


class CycleClassCreateInput(_Strict):
    name: str = Field(min_length=1, max_length=200, description='Class name, such as A.')
    interval_days: int = Field(ge=1, le=3660, description='Days between counts of an item in this class at a site.')
    description: str | None = Field(None, max_length=1000)


class CycleClassUpdateInput(_Strict):
    cycle_class: Selector = Field(description='Class id or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    interval_days: int | None = Field(None, ge=1, le=3660)
    description: str | None = Field(None, max_length=1000)

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        if 'description' not in self.model_fields_set:
            values.pop('description', None)
        return values


class CycleClassSelectInput(_Strict):
    cycle_class: Selector = Field(description='Class id or name.')
    expected_version: _Version | None = None


class CycleClassShowInput(_Strict):
    cycle_class: Selector = Field(description='Class id or name.')


class CycleClassListInput(_Strict):
    include_inactive: bool = False


class CycleClassOutput(_Strict):
    id: str
    version: int
    name: str
    interval_days: int
    description: str | None
    active: bool
    item_count: int = Field(0, description='Active items in the class.')


class CycleClassWriteOutput(CycleClassOutput):
    changed: bool = True


class CycleClassPageOutput(_Strict):
    cycle_classes: list[CycleClassOutput]
    seeded: bool = Field(False, description='True when this call found no classes and seeded A 30, B 90 and C 365.')


class ClassifyInput(_Strict):
    date_from: _Date | None = Field(None, description='Start of the period whose cost of goods issued ranks the items; omitted, a year before date_to.')
    date_to: _Date | None = Field(None, description='End of the period; omitted, today.')
    a_share: str = Field('80', max_length=12, description='Percent of issued cost the A items make up (default 80).')
    b_share: str = Field('15', max_length=12, description='Percent the B items make up after A (default 15); the rest are C.')
    classes: list[Selector] | None = Field(None, min_length=3, max_length=3, description=(
        'The three classes to give, best first; omitted, A, B and C (seeded when none exist).'))


class ClassifyRow(_Strict):
    item_id: str
    item_name: str
    issued_cost: JournalMoneyOutput
    cumulative_share: str = Field(description='Share of the period\'s issued cost up to and including this item, percent.')
    current_class: str | None
    proposed_class: str | None = Field(description='The class proposed; null when nothing was issued and the item has none.')
    changes: bool


class ClassifyOutput(_Strict):
    date_from: str
    date_to: str
    total_issued_cost: JournalMoneyOutput
    rows: list[ClassifyRow]
    changes: int
    applied: bool = False
    message: str | None = Field(None, description='Why nothing changes, when nothing was issued in the period.')


class DueInput(_Strict):
    site: Selector | None = Field(None, description='Site to read; omitted, the default site.')
    as_of: _Date | None = Field(None, description='Due as of this date; omitted, today.')
    limit: int = Field(200, ge=1, le=2000)


class DueRow(_Strict):
    item_id: str
    item_name: str
    cycle_class: str
    interval_days: int
    last_counted: str | None = Field(description='Date of its last recorded count at the site; null when never counted there.')
    due_date: str | None = Field(description='When it fell due: last count plus the interval, or its first stock date there when never counted.')
    days_overdue: int | None = Field(description='Days past its due date. Rows rank by this over the class interval.')
    on_hand: str


class DueOutput(_Strict):
    site_id: str
    site_name: str
    as_of: str
    rows: list[DueRow]
    due: int
    not_due: int = Field(description='Classified items at the site that are not yet due.')
