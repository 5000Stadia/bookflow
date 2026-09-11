"""What a purchase order takes and what it gives back.

An order is the bill's form before anything is owed: the same vendor, the same terms, the same
class and job columns, and one ordered grid whose rows may name an item or an account. The
verbs are the bill's, deliberately, so that someone who has entered one already knows this.

**One grid, two kinds of row.** ``lines`` is the whole ordered list. A row names exactly one
of ``item`` or ``account``; ``quantity`` and ``rate`` come as a pair or not at all, and
``amount`` may be given instead of, or as a check on, their product. That is what keeps
line 3 in position 3 whichever tab it was typed on, which two tables could not.

**Nothing posts.** No output here carries a batch, a posting line or a payable, because the
order has none. ``conversion`` is the one thing that says what became of it.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_serializer, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.journal_models import (
    MoneyInput, _Date, _Input, _Number, _Selector, _Version,
)
from bookflow.company.journal_outputs import CreatedOutput, JournalMoneyOutput
from bookflow.company.purchase_order_facts import PurchaseOrderLineProfile, PurchaseOrderProfile
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
ShipTo = Annotated[str, Field(max_length=2000)]
# The vendor's own quotation or contract number as they print it. A plain string: their
# spelling, not our identifier, and never parsed.
OrderReference = Annotated[str, Field(max_length=128)]
Quantity = Annotated[str, Field(min_length=1, max_length=40)]
# A live order is one that can still receive goods or be billed; a closed one is finished.
LiveStatus = Literal['open', 'partly_received', 'closed']


class PurchaseOrderLineInput(_Input):
    """One ordered row: what is being bought, how much of it, and who it is for.

    ``item`` and ``account`` are the two kinds of row and exactly one is given. An item row
    takes the item's own purchase account; an account row names the account outright.

    ``quantity`` and ``rate`` travel together -- an order that says how many says what each
    costs -- and their product is the line amount. Give ``amount`` alone for a row that is a
    lump sum, or give it with the pair to have the arithmetic checked rather than trusted.

    A class typed on the row is the row's class; a row without one takes the order's.
    ``class_mode`` set to ``none`` leaves this row unclassified while the order carries a
    class. ``customer`` names the customer or job the cost belongs to and ``billable`` marks
    it to be passed on later, which is a separate fact from naming the job.
    """

    line_id: _Selector | None = None
    item: _Selector | None = None
    account: _Selector | None = None
    description: Text | None = None
    quantity: Quantity | None = None
    rate: str | MoneyInput | None = None
    amount: str | MoneyInput | None = None
    customer: _Selector | None = None
    billable: bool = False
    class_id: _Selector | None = None
    class_mode: Literal['inherit', 'none', 'value'] = 'inherit'

    @model_validator(mode='after')
    def coherent_row(self) -> Self:
        if (self.item is None) == (self.account is None):
            raise ValueError('an ordered line names exactly one of item or account')
        if (self.quantity is None) != (self.rate is None):
            raise ValueError('quantity and rate are given together or not at all')
        if self.quantity is None and self.amount is None:
            raise ValueError('give quantity and rate, or an amount')
        if self.class_mode == 'value' and self.class_id is None:
            raise ValueError('class_id is required when class_mode is value')
        if self.class_id is not None and self.class_mode != 'value':
            if self.class_mode == 'none':
                raise ValueError('class_mode none leaves the line unclassified; remove class_id')
            self.class_mode = 'value'
        if self.billable and self.customer is None:
            raise ValueError('billable requires a customer or job to bill the cost to')
        return self


Lines = Annotated[list[PurchaseOrderLineInput], Field(min_length=1, max_length=200)]


class _PurchaseOrderFields(_Input):
    number: _Number | None = None
    expected_date: _Date | None = None
    ship_to: ShipTo | None = None
    terms: _Selector | None = None
    reference: OrderReference | None = None
    memo: Text | None = None
    class_id: _Selector | None = None


class PurchaseOrderPostInput(_PurchaseOrderFields):
    date: _Date
    vendor: _Selector
    lines: Lines

    @model_validator(mode='after')
    def new_lines(self) -> Self:
        if any(line.line_id is not None for line in self.lines):
            raise ValueError('new ordered lines cannot supply an existing line identity')
        return self


class PurchaseOrderUpdateInput(_PurchaseOrderFields):
    purchase_order: _Selector
    expected_version: _Version | None = None
    date: _Date | None = None
    vendor: _Selector | None = None
    status: LiveStatus | None = None
    lines: Lines | None = None

    @model_validator(mode='after')
    def required_values(self) -> Self:
        for field in ('date', 'vendor', 'lines', 'number', 'status'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in ('expected_date', 'ship_to', 'terms', 'reference', 'memo', 'class_id'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class PurchaseOrderVoidInput(_Input):
    purchase_order: _Selector
    expected_version: _Version | None = None


class PurchaseOrderShowInput(_Input):
    purchase_order: _Selector
    revision_number: _Version | None = None


class PurchaseOrderPageInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class PurchaseOrderQueryInput(PurchaseOrderPageInput):
    date_from: _Date | None = None
    date_to: _Date | None = None
    expected_from: _Date | None = None
    expected_to: _Date | None = None
    vendor: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    reference: OrderReference | None = Field(default=None,
        description="Exact match on the vendor's own quotation or contract reference, trimmed.")
    status: Literal['open', 'partly_received', 'closed', 'voided'] | None = None
    open_only: bool = Field(default=False,
        description='Only orders that can still become a bill: open or partly received, and not '
                    'already consumed by one.')
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the order-date then stable-id page: asc pages the oldest order first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        for first, second, name in ((self.date_from, self.date_to, 'date_from'),
                                    (self.expected_from, self.expected_to, 'expected_from')):
            if first and second and first > second:
                raise ValueError(f'{name} cannot follow its matching upper bound')
        return self


class PurchaseOrderHistoryInput(PurchaseOrderPageInput):
    purchase_order: _Selector


class PurchaseOrderLineOutput(CreatedOutput):
    document_id: str
    revision_id: str
    line_id: str
    position: int
    item_id: str | None
    account_id: str | None
    description: str | None
    quantity: str | None
    quantity_microunits: int | None
    rate: MoneyOutput | None
    rate_minor_units: int | None
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    customer_id: str | None
    billable: bool
    class_id: str | None
    line_snapshot: PurchaseOrderLineProfile


class PurchaseOrderConversionOutput(CreatedOutput):
    """The bill this order became, and the exact revision it was made from."""

    source_document_id: str
    source_revision_id: str
    source_version: int
    destination_transaction_id: str
    destination_revision_id: str
    destination_type: Literal['bill']
    audit_event_id: str


class PurchaseOrderRevisionSummaryOutput(CreatedOutput):
    document_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    status: Literal['open', 'partly_received', 'closed', 'voided']
    vendor_id: str
    expected_date: str | None
    ship_to: str | None
    terms_id: str | None
    reference: str | None
    memo: str | None
    class_id: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int


class PurchaseOrderRevisionOutput(PurchaseOrderRevisionSummaryOutput):
    custom_fields_snapshot: dict[str, SnapshotField]
    profile: PurchaseOrderProfile
    lines: list[PurchaseOrderLineOutput]


class PurchaseOrderSummaryOutput(CommonOut):
    number: str
    current_revision_id: str
    status: Literal['open', 'partly_received', 'closed', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    date: str
    vendor_id: str
    vendor_name: str
    expected_date: str | None
    reference: str | None
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str
    consumed: bool
    bill_id: str | None


class PurchaseOrderOutput(PurchaseOrderSummaryOutput):
    revision: PurchaseOrderRevisionOutput
    conversion: PurchaseOrderConversionOutput | None = None


class PurchaseOrderWriteOutput(PurchaseOrderOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class PurchaseOrderPageOutput(_Input):
    items: list[PurchaseOrderSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class PurchaseOrderHistoryOutput(_Input):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal['open', 'partly_received', 'closed', 'voided']
    items: list[PurchaseOrderRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
