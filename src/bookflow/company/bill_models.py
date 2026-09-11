"""What a bill takes and what it gives back.

A bill is the payables mirror of the invoice: a vendor instead of a customer, a payable
instead of a receivable, and a total that stands open until it is paid. The verbs, the cursor
discipline and the shape of what comes back are the invoice's, deliberately, so that a person
or an agent who has entered one has already learned the other.

**Where the Items tab attaches.** The line collection is ``expenses``, not ``lines``, and
nothing in the header, the totals or the posting is named after it: ``item_total`` and an
``items`` collection arrive beside ``expense_total`` and ``expenses`` without renaming or
re-cutting anything that is here. That is the same seam the check and the card charge left.
"""
from __future__ import annotations

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import Field, model_serializer, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.bill_facts import BillExpenseProfile, BillProfile
from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.journal_models import (
    MoneyInput, _Date, _Input, _Number, _Selector, _Version,
)
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
# The supplier's own number as printed on their document. A plain string: it is their
# spelling, not our identifier, and it is never parsed.
SupplierReference = Annotated[str, Field(max_length=128)]


def reference_key(value: str | None) -> str | None:
    """The form two spellings of one supplier reference have in common.

    NFC so that a composed and a decomposed accent are one reference, trimmed so that a
    trailing space is not a second document, case-folded so that ``inv-7`` and ``INV-7`` are
    the same number. A reference that is blank after trimming is no reference at all and can
    never collide with another.
    """
    if value is None:
        return None
    folded = unicodedata.normalize('NFC', value).strip().casefold()
    return folded or None


class BillExpenseInput(_Input):
    """One row of the Expenses grid: an account, an amount, and who it was for.

    A class typed on the line is the line's class, exactly as in the check's grid; a line
    without one takes the bill's. ``class_mode`` says the one thing the column cannot:
    ``none`` leaves this line unclassified while the bill itself carries a class.

    ``customer`` names the customer or job the cost belongs to. ``billable`` marks it to be
    passed on to that customer later, and it is a separate fact from naming the job -- costs
    are attributed to jobs that are never rebilled.
    """

    line_id: _Selector | None = None
    account: _Selector
    amount: str | MoneyInput
    memo: Text | None = None
    customer: _Selector | None = None
    billable: bool = False
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
        if self.billable and self.customer is None:
            raise ValueError('billable requires a customer or job to bill the cost to')
        return self


Expenses = Annotated[list[BillExpenseInput], Field(min_length=1, max_length=200)]


class _BillFields(_Input):
    number: _Number | None = None
    ap_account: _Selector | None = None
    terms: _Selector | None = None
    due_date: _Date | None = None
    supplier_reference: SupplierReference | None = None
    memo: Text | None = None
    class_id: _Selector | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(
        default_factory=lambda: CustomFieldKindExpectations({}))


class BillPostInput(_BillFields):
    date: _Date
    vendor: _Selector
    expenses: Expenses

    @model_validator(mode='after')
    def new_lines(self) -> Self:
        if any(line.line_id is not None for line in self.expenses):
            raise ValueError('new expense lines cannot supply an existing line identity')
        return self


class BillUpdateInput(_BillFields):
    bill: _Selector
    expected_version: _Version | None = None
    date: _Date | None = None
    vendor: _Selector | None = None
    expenses: Expenses | None = None

    @model_validator(mode='after')
    def required_values(self) -> Self:
        for field in ('date', 'vendor', 'expenses', 'number', 'ap_account'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self

    @model_serializer(mode='wrap')
    def only_supplied(self, handler):
        values = handler(self)
        for key in ('terms', 'due_date', 'supplier_reference', 'memo', 'class_id'):
            if key not in self.model_fields_set:
                values.pop(key, None)
        return values


class BillVoidInput(_Input):
    bill: _Selector
    expected_version: _Version | None = None


class BillShowInput(_Input):
    bill: _Selector
    revision_number: _Version | None = None


class BillPageInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class BillQueryInput(BillPageInput):
    date_from: _Date | None = None
    date_to: _Date | None = None
    due_from: _Date | None = None
    due_to: _Date | None = None
    vendor: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    supplier_reference: SupplierReference | None = None
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest bill first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        for first, second, name in ((self.date_from, self.date_to, 'date_from'),
                                    (self.due_from, self.due_to, 'due_from')):
            if first and second and first > second:
                raise ValueError(f'{name} cannot follow its matching upper bound')
        return self


class BillHistoryInput(BillPageInput):
    bill: _Selector


class BillExpenseOutput(CreatedOutput):
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
    billable: bool
    class_id: str | None
    class_name: str | None
    name_type: str | None
    name_id: str | None
    party_name: str | None
    line_snapshot: BillExpenseProfile


class BillObligationOutput(CreatedOutput):
    """The payable a settlement attaches to, and the per-line breakdown it allocates over."""

    transaction_id: str
    ordinal: int
    vendor_id: str
    ap_account_id: str
    currency: str
    audit_event_id: str
    components: list['BillObligationComponentOutput'] = Field(default_factory=list)


class BillObligationComponentOutput(CreatedOutput):
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


class BillSettlementSourceOutput(_Input):
    """How much of what settled this bill came from one kind of money."""

    source_type: Literal['bill_payment', 'vendor_credit']
    applied: MoneyOutput
    applied_minor_units: int


class BillSettlementOutput(_Input):
    """What is still owed on this bill. ``applied`` is what a settlement owner has taken off.

    ``applied`` is one number -- everything that settled this bill, whatever settled it -- and
    ``open`` and ``status`` are arithmetic on exactly that. ``sources`` is the additive answer
    to the different question: which kinds of money made it up, so a reader can say "46254
    credit, 53746 cash" without any of the three fields above changing shape. Kinds worth
    nothing are omitted, so an unsettled bill carries an empty list.
    """

    bill_id: str
    obligation_id: str
    version: int
    revision_id: str
    gross_minor_units: int
    applied_minor_units: int
    open_minor_units: int
    gross: MoneyOutput
    applied: MoneyOutput
    open: MoneyOutput
    currency: str
    status: Literal['voided', 'paid', 'partial', 'unpaid']
    sources: list[BillSettlementSourceOutput] = Field(default_factory=list)


class DuplicateReferenceOutput(_Input):
    """Another bill from this vendor carrying the same supplier reference."""

    bill_id: str
    number: str
    date: str
    status: Literal['posted', 'voided']
    supplier_reference: str
    total: MoneyOutput
    total_minor_units: int


class BillRevisionSummaryOutput(CreatedOutput):
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal['vendor']
    name_id: str
    memo: str | None
    due_date: str
    expense_total: MoneyOutput
    total: MoneyOutput
    expense_total_minor_units: int
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: list[JournalBatchOutput]


class BillRevisionOutput(BillRevisionSummaryOutput):
    issuer_snapshot: dict[str, str | None]
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    profile: BillProfile
    expenses: list[BillExpenseOutput]
    obligation: BillObligationOutput | None = None


class BillSummaryOutput(CommonOut):
    type: Literal['bill']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    due_date: str
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
    settlement_current: BillSettlementOutput


class BillOutput(BillSummaryOutput):
    revision: BillRevisionOutput
    duplicate_references: list[DuplicateReferenceOutput] = Field(default_factory=list)


class BillWriteOutput(BillOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class BillPageOutput(_Input):
    items: list[BillSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class BillHistoryOutput(_Input):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal['posted', 'voided']
    items: list[BillRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


BillObligationOutput.model_rebuild()
