"""Inputs and outputs of the assembly build commands (R103)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import serial_lot_models as _SL
from bookflow.company.inventory_models import _Date, _Selector, _Version
from bookflow.company.ledger_reports import MoneyOutput


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


_QUANTITY = 'Quantity of the assembly to build, a positive decimal with at most six places.'


class BuildComponentNumbers(_Strict):
    item: _Selector = Field(description='Component item, by id or full name.')
    lot: _SL.Number | None = Field(None, description='Lot the component is taken from.')
    serial_numbers: list[_SL.Number] | None = Field(None, max_length=10000, description='Serial numbers of the component units taken.')


class AssemblyBuildInput(_Strict):
    item: _Selector = Field(description='The inventory assembly item to build, by id or full name.')
    quantity: str = Field(min_length=1, max_length=40, description=_QUANTITY)
    date: _Date = Field(description='Build date: the components are consumed and the assembly received at their average cost on it.')
    memo: str | None = Field(None, max_length=2000, description='Memo on the build.')
    pending: bool = Field(False, description='Mark the build pending when the components on hand are short of it: '
                                             'nothing is posted, the components are reserved, and `assembly finalize` builds it later. '
                                             'A build that can be built is built; pending applies only to a shortfall.')
    site: _Selector | None = Field(None, description='Inventory site the components are taken from and the assembly put at, by id or name; omitted, the default site.')
    lot: _SL.Number | None = Field(None, description='Lot the built assemblies receive into, when the assembly is lot-tracked.')
    lot_expiry: _SL.IsoDate | None = Field(None, description=_SL.EXPIRY_HELP)
    serial_numbers: list[_SL.Number] | None = Field(None, max_length=10000, description=(
        'Serial numbers of the built units, when the assembly is serial-tracked; the rest are unassigned.'))
    components: list[BuildComponentNumbers] | None = Field(None, max_length=200, description=(
        'Lot or serial numbers the tracked components are taken from, one entry per component item; components '
        'not named leave unassigned. The build links them to the assembly for `report lot-trace`.'))


class AssemblyBuildSelectInput(_Strict):
    build: _Selector = Field(description='The build, by its id or by the journal number it posted as.')


class AssemblyBuildUpdateInput(AssemblyBuildSelectInput):
    expected_version: _Version | None = Field(None, description='The build version this edit was decided from; a later change is refused.')
    quantity: str | None = Field(None, min_length=1, max_length=40, description=_QUANTITY + ' Omitted, unchanged.')
    date: _Date | None = Field(None, description='New build date. Omitted, unchanged.')
    memo: str | None = Field(None, max_length=2000, description='New memo. Omitted, unchanged.')
    site: _Selector | None = Field(None, description='New inventory site for the build. Omitted, unchanged.')


class AssemblyBuildVoidInput(AssemblyBuildSelectInput):
    expected_version: _Version | None = Field(None, description='The build version this was decided from; a later change is refused.')


class AssemblyBuildFinalizeInput(AssemblyBuildVoidInput):
    date: _Date | None = Field(None, description="Date to build on; omitted, the pending build's own date.")


class AssemblyBuildListInput(_Strict):
    status: Literal['pending', 'built', 'voided', 'deleted', 'all'] | None = Field(
        None, description='Only builds in this state; omitted, every build except deleted ones. `pending` is the Pending Builds report.')
    item: _Selector | None = Field(None, description='Only builds of this assembly item.')
    site: _Selector | None = Field(None, description='Only builds at this inventory site (Pending Builds by Site).')
    limit: int = Field(50, ge=1, le=200, description='Most builds to return, newest first.')


class AssemblyComponentOutput(_Strict):
    item_id: str
    item_name: str
    item_type: str
    kind: Literal['stock', 'cost'] = Field(description='stock: an inventory part or assembly consumed from stock at its average cost; '
                                                       'cost: a non-stock component whose purchase cost is added to the assembly.')
    quantity_per: str = Field(description='Quantity of this component in one assembly, in its base unit.')
    quantity_needed: str = Field(description='quantity_per x the build quantity.')
    quantity_on_hand: str | None = Field(description='On hand on the build date before this build; null for a non-stock component.')
    reserved_for_pending_builds: str | None = Field(description='Reserved by other pending builds; null for a non-stock component.')
    max_buildable: str | None = Field(description='Assemblies this component alone allows from what is on hand; null for a non-stock component.')
    cost: MoneyOutput = Field(description='What the build takes out of this component: its average cost at the date, or its purchase cost for a non-stock one.')
    account_id: str = Field(description="The account credited: the component's inventory asset account, or a non-stock component's expense account.")


class AssemblyCorrectionOutput(_Strict):
    item_id: str
    effective_date: str
    corrects_movement_id: str
    delta: MoneyOutput


class AssemblyBuildOutput(_Strict):
    build_id: str | None = Field(description='Stable id of the build; null on a preview of a new one.')
    version: int | None = Field(description='Build version (its revision number); null on a preview of a new one.')
    status: Literal['will_build', 'will_pend', 'pending', 'built', 'voided', 'deleted']
    number: str | None = Field(description='Journal number the build posted as; null while pending.')
    transaction_id: str | None
    item_id: str
    item_name: str
    asset_account_id: str
    date: str
    quantity: str
    memo: str | None
    max_buildable: str = Field(description='Most assemblies the components on hand allow on the build date (the anchor\'s "maximum number you can build").')
    shortfall: bool = Field(description='The components on hand are short of the quantity; it can only be marked pending.')
    components: list[AssemblyComponentOutput]
    assembly_cost: MoneyOutput = Field(description='What the assembly is received at: the sum of the component costs.')
    unit_cost: MoneyOutput
    assembly_quantity_on_hand: str = Field(description='The assembly on hand on the build date, after this build when it is built.')
    posts_to_ledger: bool = Field(description='Whether the build moves value between accounts. A build whose components and assembly share one inventory asset account, with no non-stock components, nets to nothing.')
    corrections: list[AssemblyCorrectionOutput] = Field(default_factory=list, description='Dated cost corrections this change posted to later documents.')
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list, description='Every revision of the build: version, status, date, quantity, who and when.')
    site_id: str | None = Field(None, description='Inventory site of the build; null at the default site.')
    site_name: str | None = Field(None, description='Name of the inventory site the build takes from and puts the assembly at.')


class AssemblyBuildListRow(_Strict):
    build_id: str
    version: int
    status: str
    number: str | None
    item_id: str
    item_name: str
    date: str
    quantity: str
    memo: str | None
    assembly_cost: MoneyOutput | None
    site_name: str | None = None


class AssemblyBuildListOutput(_Strict):
    rows: list[AssemblyBuildListRow]
    count: int
    reserved: dict[str, str] = Field(default_factory=dict, description='Component quantities reserved by pending builds, by item id.')
