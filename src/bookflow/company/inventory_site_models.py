"""Inputs and outputs of inventory sites and inventory transfers (R104 increment 1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from bookflow.company import serial_lot_models as _SL
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.sales_models import Selector


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


CountPosition = Literal['before', 'after']

# ---------------------------------------------------------------------------- sites


class SiteCreateInput(_Strict):
    name: str = Field(min_length=1, max_length=200, description='Site name, such as "Warehouse" or "Van 1"; unique.')
    description: str | None = Field(None, max_length=1000, description='What or where the site is.')
    address: str | None = Field(None, max_length=2000, description='Postal address of the site, as free text.')
    is_default: bool = Field(False, description='Make this the default site: stock lines naming no site are placed here.')
    tracks_bins: bool = Field(False, description='Locate stock at this site to bins (add them with `bin create`).')


class SiteUpdateInput(_Strict):
    site: Selector = Field(description='Site ID or name.')
    expected_version: _Version | None = None
    name: str | None = Field(None, min_length=1, max_length=200, description='New name; renaming Main is an ordinary update.')
    description: str | None = Field(None, max_length=1000)
    address: str | None = Field(None, max_length=2000)
    is_default: Literal[True] | None = Field(None, description='true makes this the default site (the previous default stops being it).')
    tracks_bins: bool | None = Field(None, description='true locates stock here to bins; false is refused while any bin holds stock.')

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in ('description', 'address'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class SiteSelectInput(_Strict):
    site: Selector = Field(description='Site ID or name.')
    expected_version: _Version | None = None


class SiteShowInput(_Strict):
    site: Selector = Field(description='Site ID or name.')
    as_of: _Date | None = Field(None, description='Stock as of this date; omitted, today.')


class SiteListInput(_Strict):
    include_inactive: bool = False


class SiteStockRow(_Strict):
    item_id: str
    item_name: str
    quantity_on_hand: str


class SiteOutput(_Strict):
    id: str
    version: int
    name: str
    description: str | None
    address: str | None
    is_default: bool
    tracks_bins: bool
    active: bool
    item_count: int = Field(0, description='Items with a nonzero quantity at this site today.')
    stock: list[SiteStockRow] = Field(default_factory=list, description='On show: each item held at the site.')
    transfers: list[dict] = Field(default_factory=list, description='On show: the latest transfers in or out of the site.')


class SiteWriteOutput(SiteOutput):
    changed: bool = True


class SitePageOutput(_Strict):
    sites: list[SiteOutput]


# ---------------------------------------------------------------------------- transfers


class TransferLineInput(_Strict):
    item: Selector = Field(description='Inventory part or assembly to move, by id or name.')
    quantity: str = Field(min_length=1, max_length=40, description='Quantity to move in the item base unit, a positive decimal.')
    description: str | None = Field(None, max_length=2000)
    from_bin: Selector | None = Field(None, description=(
        "Bin the stock leaves at the from site (a site that tracks bins), by id, full name or label; \"(no bin)\" "
        "names the units outside every bin. Omitted, the units leave from where they are: the item's default bin "
        "when it holds them, else no bin, else the bins holding them."))
    to_bin: Selector | None = Field(None, description=(
        "Bin the stock arrives in at the to site; \"(no bin)\" puts it outside every bin; omitted, the item's "
        "default bin there, else no bin. Moving between two bins of one site names the same site as from_site and "
        "to_site."))
    lot: _SL.Number | None = Field(None, description='Lot moved, of a lot-tracked item; omitted, the units move unassigned.')
    serial_numbers: list[_SL.Number] | None = Field(None, max_length=10000, description=(
        'Serial numbers moved, one per unit, of a serial-tracked item; each must be at the from site on the date. '
        'Units left unnumbered move unassigned.'))


class TransferPostInput(_Strict):
    date: _Date | None = Field(None, description='Date the stock leaves and arrives; omitted, today.')
    number: _Number | None = Field(None, description='Transfer number; omitted, the next in the transfer series.')
    from_site: Selector = Field(description='Site the stock leaves, by id or name.')
    to_site: Selector = Field(description='Site the stock arrives at, by id or name.')
    lines: list[TransferLineInput] = Field(min_length=1, max_length=200)
    memo: str | None = Field(None, max_length=2000)
    count_position: CountPosition = Field('before', description='Before (default) or after a count of either site on the same date.')


class TransferUpdateInput(_Strict):
    transfer: Selector = Field(description='Transfer ID or number.')
    expected_version: _Version = Field(description='The transfer version this change was decided from.')
    date: _Date | None = None
    from_site: Selector | None = None
    to_site: Selector | None = None
    lines: list[TransferLineInput] | None = Field(None, min_length=1, max_length=200)
    memo: str | None = Field(None, max_length=2000)
    count_position: CountPosition | None = None


class TransferVoidInput(_Strict):
    transfer: Selector = Field(description='Transfer ID or number.')
    expected_version: _Version | None = None


class TransferShowInput(_Strict):
    transfer: Selector = Field(description='Transfer ID or number.')


class TransferQueryInput(_Strict):
    site: Selector | None = Field(None, description='Only transfers out of or into this site.')
    item: Selector | None = Field(None, description='Only transfers moving this item.')
    date_from: _Date | None = None
    date_to: _Date | None = None
    include_voided: bool = False
    limit: int = Field(50, ge=1, le=200)


class TransferLineOutput(_Strict):
    line_id: str
    item_id: str
    item_name: str
    quantity: str
    description: str | None
    from_bin: str | None = None
    to_bin: str | None = None
    lot: str | None = None
    serial_numbers: list[str] = Field(default_factory=list)


class TransferSiteStock(_Strict):
    site_id: str
    site_name: str
    item_id: str
    item_name: str
    quantity_on_hand: str


class TransferOutput(_Strict):
    id: str | None = Field(description='Transfer id; null on a preview of a new one.')
    number: str
    version: int
    status: Literal['will_post', 'posted', 'voided']
    date: str
    count_position: CountPosition
    from_site_id: str
    from_site_name: str
    to_site_id: str
    to_site_name: str
    memo: str | None
    reason: str | None
    lines: list[TransferLineOutput]
    on_hand_after: list[TransferSiteStock] = Field(default_factory=list, description="Each line item's quantity at both sites on the transfer date, after it.")
    posts_to_ledger: bool = Field(False, description='Always false: a transfer moves quantity only. No journal entry, no value and no cost change.')
    warnings: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


class TransferWriteOutput(TransferOutput):
    changed: bool = True


class TransferPageOutput(_Strict):
    transfers: list[TransferOutput]
    count: int
