"""Input and output shapes for landed cost (R104 increment 6, design/advanced-inventory.md §5)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from bookflow.company.journal_models import _Date as IsoDate
from bookflow.company.journal_outputs import JournalMoneyOutput, JournalOutput, JournalWriteOutput

Selector = Annotated[str, Field(min_length=1, max_length=1004), BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v)]
Basis = Literal['quantity', 'value', 'weight', 'volume', 'amounts']


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ChargeInput(_Strict):
    charge: Selector | None = Field(default=None, description=(
        'A charge, by the `charge` id `landed-cost charges` lists (the document\'s id). Give this or document.'))
    document: Selector | None = Field(default=None, description=(
        'The bill, check, card charge or vendor credit holding the charge, by id or number: its Landed Cost '
        'Clearing lines together are one charge.'))
    amount: str | None = Field(default=None, description=(
        'How much of the charge to allocate now, at most what is unallocated; omitted, all of it. A freight '
        'credit (a vendor credit line) is negative; give its amount as a positive number or leave it out.'))


class TargetInput(_Strict):
    document: Selector | None = Field(default=None, description=(
        'An item receipt, or a bill, check or card charge whose item lines brought the stock in, by id or number: '
        'each of its stock lines, or the one line or item named below.'))
    movement: Selector | None = Field(default=None, description='One receipt movement by id (as `landed-cost show` lists).')
    line: int | None = Field(default=None, ge=1, description="Only this stock line of the document (1 is its first stock line).")
    item: Selector | None = Field(default=None, description="Only the document's lines of this item (name, id or barcode).")
    account: Selector | None = Field(default=None, description=(
        'An expense or cost-of-goods account instead of a receipt, for a share that should not be capitalised; '
        'basis amounts only.'))
    amount: str | None = Field(default=None, description='This target\'s share, with basis amounts; the shares sum exactly to the charges.')
    unit_weight: str | None = Field(default=None, description="With basis weight: the weight of one base unit, instead of the item's.")
    unit_volume: str | None = Field(default=None, description="With basis volume: the volume of one base unit, instead of the item's.")


class LandedCostPostInput(_Strict):
    charges: list[ChargeInput] = Field(min_length=1, max_length=50, description=(
        'The charges to allocate: Landed Cost Clearing lines of bills, checks, card charges and vendor credits '
        '(`landed-cost charges` lists them).'))
    targets: list[TargetInput] = Field(min_length=1, max_length=100, description=(
        'Where the charges go: item receipts and the item lines of bills, checks and card charges that brought '
        'stock in, whole or by line or item; or an account for a share not capitalised.'))
    basis: Basis = Field(default='quantity', description=(
        'How the charges are shared: quantity (base units, the default), value (each line\'s own cost before any '
        'landed cost), weight or volume (base quantity times the item\'s unit weight or volume), or amounts (given '
        'per target).'))
    date: IsoDate | None = Field(default=None, description=(
        'When the allocation is recognised. Default: the latest charge date, never before a target receipt, and the '
        'first open date when that is later. Stock sold before it owes a forward true-up dated here.'))
    memo: str | None = Field(default=None, max_length=4000)
    number: str | None = Field(default=None, min_length=1, max_length=64, description='Allocation number; default the next LC-n.')


class LandedCostVoidInput(_Strict):
    allocation: Selector = Field(description='Allocation number (LC-n) or id.')


class LandedCostShowInput(_Strict):
    allocation: Selector = Field(description='Allocation number (LC-n) or id.')


class LandedCostQueryInput(_Strict):
    status: Literal['posted', 'voided'] | None = None
    kind: Literal['allocation', 'receipt_shipping'] | None = Field(default='allocation', description=(
        "allocation (the default: landed-cost post), receipt_shipping (item receipts' own shipping), or null for both."))
    document: Selector | None = Field(default=None, description='Only allocations touching this receipt or charge document.')
    date_from: IsoDate | None = None
    date_to: IsoDate | None = None
    limit: int = Field(default=50, ge=1, le=500)


class LandedCostChargesInput(_Strict):
    as_of: IsoDate | None = Field(default=None, description='Charges and allocations dated on or before this date; default every date.')
    vendor: Selector | None = Field(default=None, description='Only this vendor\'s charges.')
    include_allocated: bool = Field(default=False, description='Also list charges with nothing left to allocate.')


class LandedCostSetupInput(_Strict):
    pass





class ChargeOutput(BaseModel):
    charge: str = Field(description='The charge\'s id (its document\'s): pass it as `charges[].charge`.')
    transaction_id: str
    document_type: str
    document_number: str | None
    vendor: str | None
    date: str
    description: str | None
    amount: JournalMoneyOutput
    allocated: JournalMoneyOutput
    unallocated: JournalMoneyOutput


class ChargesOutput(BaseModel):
    as_of: str
    clearing_account_id: str | None
    clearing_account: str | None
    rows: list[ChargeOutput]
    total_unallocated: JournalMoneyOutput
    clearing_balance: JournalMoneyOutput
    message: str | None = None


class AllocationLineOutput(BaseModel):
    position: int
    charge: str | None
    charge_document: str | None
    target_document: str | None
    target_movement_id: str | None
    target_transaction_id: str | None = None
    target_quantity: str | None = Field(default=None, description="The target receipt line's base quantity.")
    item_id: str | None
    item_name: str | None
    account_id: str | None
    account_name: str | None
    basis: str
    measure: str
    amount: JournalMoneyOutput
    landed_movement_id: str | None
    reversed: bool = False


class TargetShareOutput(BaseModel):
    """What one target receipt took, split into what stays in stock and what was issued before the allocation date."""

    target_document: str | None
    target_movement_id: str | None
    item_name: str | None
    account_name: str | None
    amount: JournalMoneyOutput
    to_stock: JournalMoneyOutput = Field(description='The part the stock still on hand (or consumed on or after the allocation date) carries.')
    issued_before: JournalMoneyOutput = Field(description=(
        'The part owed by stock issued before the allocation date: forward true-ups dated at the allocation, to cost '
        'of goods sold for a sale, to its own account for an adjustment, and on into the assembly for a build.'))


class TrueUpOutput(BaseModel):
    item_id: str
    item_name: str
    effective_date: str
    corrects_movement_id: str
    landed_movement_id: str
    delta: JournalMoneyOutput
    transaction_id: str = ''
    number: str = ''


class AllocationSummary(BaseModel):
    id: str | None
    number: str
    kind: str
    status: str
    date: str
    basis: str
    memo: str | None = None
    reason: str | None = None
    total: JournalMoneyOutput
    lines: list[AllocationLineOutput]
    targets: list[TargetShareOutput]
    true_ups: list[TrueUpOutput] = Field(default_factory=list)
    void_reason: str | None = None


class LandedCostWriteOutput(JournalWriteOutput):
    allocation: AllocationSummary


class LandedCostOutput(JournalOutput):
    allocation: AllocationSummary


class LandedCostListRow(BaseModel):
    id: str
    number: str
    kind: str
    status: str
    date: str
    basis: str
    total: JournalMoneyOutput
    targets: int
    memo: str | None


class LandedCostListOutput(BaseModel):
    rows: list[LandedCostListRow]
    count: int


class LandedCostSetupOutput(BaseModel):
    account_id: str
    account: str
    created: bool
    message: str
