"""What a vendor credit takes and what it gives back.

A vendor credit is the bill read backwards, and its verbs say so: ``post``, ``show``,
``query`` and ``void`` are the bill's, and ``vendor-credit apply`` / ``unapply`` are the bill
payment's. That is deliberate -- whichever of the three payables documents a person learned
first, the next one is already familiar.

**Two settlements, one shape.** ``VendorCreditSettlementOutput`` is
``BillPaymentSettlementOutput`` field for field, because what a credit has free and what a
check has free is the same question about the same ``ap_applications`` edge. A reader that can
tell what is still unapplied on a check can tell what is still unapplied on a credit without
learning a second vocabulary.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.bill_models import MoneyOutput, SupplierReference, Text, reference_key
from bookflow.company.bill_payment_models import BillApplicationOutput
from bookflow.company.journal_models import (
    MoneyInput, _Date, _Input, _Number, _Selector, _Version,
)
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput
from bookflow.company.vendor_credit_facts import BillExpenseProfile, VendorCreditProfile
from bookflow.core.models import WriteOutput

__all__ = ['reference_key']


class VendorCreditExpenseInput(_Input):
    """One credited row: the account the cost went to, the amount coming back, and whose job.

    The bill's Expenses row, with ``billable`` removed. Marking a cost billable is a decision
    about passing it on to a customer, and a credit does not make that decision -- naming the
    ``customer`` still attributes the credit to the job, so job costing nets.

    A class typed on the line is the line's class and a line without one takes the credit's;
    ``class_mode`` set to ``none`` leaves this line unclassified while the credit carries one.
    """

    line_id: _Selector | None = None
    account: _Selector
    amount: str | MoneyInput
    memo: Text | None = None
    customer: _Selector | None = None
    class_id: _Selector | None = None
    class_mode: Literal['inherit', 'none', 'value'] = 'inherit'

    @model_validator(mode='after')
    def explicit_class(self) -> Self:
        if self.class_mode == 'value' and self.class_id is None:
            raise ValueError('class_id is required when class_mode is value')
        if self.class_id is not None and self.class_mode != 'value':
            if self.class_mode == 'none':
                raise ValueError('class_mode none leaves the line unclassified; remove class_id')
            self.class_mode = 'value'
        return self


Expenses = Annotated[list[VendorCreditExpenseInput], Field(min_length=1, max_length=200)]


class VendorCreditPostInput(_Input):
    date: _Date
    vendor: _Selector
    expenses: Expenses
    number: _Number | None = None
    ap_account: _Selector | None = None
    supplier_reference: SupplierReference | None = None
    memo: Text | None = None
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def new_lines(self) -> Self:
        if any(line.line_id is not None for line in self.expenses):
            raise ValueError('new credited lines cannot supply an existing line identity')
        return self


class VendorCreditVoidInput(_Input):
    credit: _Selector
    expected_version: _Version | None = None


class VendorCreditShowInput(_Input):
    credit: _Selector
    revision_number: _Version | None = None


class VendorCreditPageInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class VendorCreditQueryInput(VendorCreditPageInput):
    date_from: _Date | None = None
    date_to: _Date | None = None
    vendor: _Selector | None = None
    bill: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    supplier_reference: SupplierReference | None = None
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest credit first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class BillSelectionInput(_Input):
    """One bill this credit settles, and how much of it.

    ``amount`` defaults to everything still open on that bill or everything the credit still
    has free, whichever is less -- which is what selecting a row on a screen means. Supplying
    more than is open on the bill is refused rather than turned into a second credit.
    """

    bill: _Selector
    amount: str | MoneyInput | None = None
    expected_version: _Version | None = None


Selections = Annotated[list[BillSelectionInput], Field(min_length=1, max_length=200)]


class VendorCreditApplyInput(_Input):
    """Attach what a credit still has free to open bills of the same vendor.

    Nothing is posted: the payable moved when the credit posted, so this only decides which
    bills it answers. ``date`` is the settlement's own accounting date and defaults to the
    credit's date; it can be later, never earlier than the credit or than a bill it settles,
    and never in a period the closing date has shut.
    """

    credit: _Selector
    expected_version: _Version | None = None
    bills: Selections
    date: _Date | None = None

    @model_validator(mode='after')
    def one_row_per_bill(self) -> Self:
        selectors = [row.bill for row in self.bills]
        if len(set(selectors)) != len(selectors):
            raise ValueError('name each bill once; give one amount per bill')
        return self


class VendorCreditUnapplyInput(_Input):
    credit: _Selector
    expected_version: _Version | None = None
    # Omitted means every application still standing on this credit. Naming bills detaches only
    # those, so one wrong row on a three-bill credit is one correction, not three.
    bills: list[_Selector] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def one_row_per_bill(self) -> Self:
        if self.bills is not None and len(set(self.bills)) != len(self.bills):
            raise ValueError('name each bill once')
        return self


class VendorCreditExpenseOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['purchase']
    account_id: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    memo: str | None
    customer_id: str | None
    class_id: str | None
    class_name: str | None
    name_type: str | None
    name_id: str | None
    party_name: str | None
    line_snapshot: BillExpenseProfile


class VendorCreditSourceOutput(CreatedOutput):
    """The settlement source a vendor credit carries, and the capacity it breaks into."""

    transaction_id: str
    ordinal: int
    source_type: Literal['vendor_credit']
    vendor_id: str
    ap_account_id: str
    currency: str
    audit_event_id: str
    components: list['VendorCreditComponentOutput'] = Field(default_factory=list)


class VendorCreditComponentOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    key_id: str
    document_line_id: str
    ordinal: int
    posting_source_id: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    audit_event_id: str
    applied_minor_units: int
    applied: MoneyOutput


class VendorCreditSettlementOutput(_Input):
    """Where this credit currently stands: what it is worth, and what it still has free."""

    credit_id: str
    source_key_id: str
    version: int
    revision_id: str
    amount_minor_units: int
    applied_minor_units: int
    unapplied_minor_units: int
    amount: MoneyOutput
    applied: MoneyOutput
    unapplied: MoneyOutput
    currency: str
    status: Literal['voided', 'applied', 'partial', 'unapplied']


class VendorCreditRevisionSummaryOutput(CreatedOutput):
    """Everything a revision says about itself apart from what it captured and credited.

    Split out from the full revision the way the bill's is, so a ``history`` verb can page
    revisions without their line grids when one is built. Nothing pages it today.
    """

    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal['vendor']
    name_id: str
    memo: str | None
    expense_total: MoneyOutput
    total: MoneyOutput
    expense_total_minor_units: int
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: list[JournalBatchOutput]
    applications: list[BillApplicationOutput]


class VendorCreditRevisionOutput(VendorCreditRevisionSummaryOutput):
    issuer_snapshot: dict[str, str | None]
    profile: VendorCreditProfile
    expenses: list[VendorCreditExpenseOutput]
    source: VendorCreditSourceOutput | None = None


class VendorCreditSummaryOutput(CommonOut):
    type: Literal['vendor_credit']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    vendor_id: str
    vendor_name: str
    ap_account_id: str
    supplier_reference: str | None
    memo: str | None
    expense_total: MoneyOutput
    total: MoneyOutput
    expense_total_minor_units: int
    total_minor_units: int
    currency: str
    settlement_current: VendorCreditSettlementOutput


class VendorCreditOutput(VendorCreditSummaryOutput):
    revision: VendorCreditRevisionOutput
    applications: list[BillApplicationOutput] = Field(default_factory=list)


class VendorCreditWriteOutput(VendorCreditOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class VendorCreditPageOutput(_Input):
    items: list[VendorCreditSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
