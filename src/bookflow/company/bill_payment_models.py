"""What paying a bill takes and what it gives back.

``bill pay`` is the one verb that reaches across documents: you hand it the bills, the account
the money comes out of and the method, and it writes one payment per payee. That is why its
output is a list -- selecting bills from two vendors is two checks, never one, and the shape
says so rather than a note in the help saying so.

``bill payment unapply`` and ``bill payment void`` are the two halves of taking it back. The
first detaches money from a bill and leaves the cash where it went; the second reverses the
cash and requires that nothing is still attached, which is exactly the discipline the customer
receipt already has.

``bill payment apply`` is the other direction of the first of those: capacity a payment has
free -- never applied, or freed by an unapply -- attached to more of the same vendor's open
bills. Detaching and re-attaching is ordinary work rather than a correction, so the way out of
a payment pointed at the wrong bill is to re-point it, not to void it and write another check.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.bill_payment_facts import BillPaymentProfile
from bookflow.company.journal_models import MoneyInput, _Date, _Input, _Number, _Selector, _Version
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
Reference = Annotated[str, Field(max_length=128)]
CheckNumber = Annotated[str, Field(min_length=1, max_length=64)]


class BillSelectionInput(_Input):
    """One bill to settle, and how much of it.

    ``amount`` is optional and defaults to everything still open on that bill, which is what a
    person selecting a row on a Pay Bills screen means. Supplying less is a partial payment and
    leaves the remainder open; supplying more than is open is refused rather than turned into
    a credit, because a vendor credit is a document this command does not write.
    """

    bill: _Selector
    amount: str | MoneyInput | None = None
    expected_version: _Version | None = None


Selections = Annotated[list[BillSelectionInput], Field(min_length=1, max_length=200)]


class BillPayInput(_Input):
    date: _Date
    bills: Selections
    funding_account: _Selector
    method: _Selector
    check_number: CheckNumber | None = None
    reference: Reference | None = None
    memo: Text | None = None
    number: _Number | None = None
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def one_row_per_bill(self) -> Self:
        selectors = [row.bill for row in self.bills]
        if len(set(selectors)) != len(selectors):
            raise ValueError('name each bill once; give one amount per bill')
        return self


class BillPaymentShowInput(_Input):
    payment: _Selector


class BillPaymentUnapplyInput(_Input):
    payment: _Selector
    expected_version: _Version | None = None
    # Omitted means every application still standing on this payment. Naming bills unapplies
    # only those, so one wrong row on a three-bill check is one correction, not three.
    bills: list[_Selector] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode='after')
    def one_row_per_bill(self) -> Self:
        if self.bills is not None and len(set(self.bills)) != len(self.bills):
            raise ValueError('name each bill once')
        return self


class BillPaymentApplyInput(_Input):
    """Attach what a payment still has free to more of the same vendor's open bills.

    The money already left when the payment posted, so this moves nothing: it decides which
    payables the payment answers. ``date`` is the settlement's own accounting date and defaults
    to the payment's date; it can be later, which is what lets a check answer a bill entered
    after it was written, but never earlier than the payment or than a bill it settles, and
    never in a period the closing date has shut.
    """

    payment: _Selector
    expected_version: _Version | None = None
    bills: Selections
    date: _Date | None = None

    @model_validator(mode='after')
    def one_row_per_bill(self) -> Self:
        selectors = [row.bill for row in self.bills]
        if len(set(selectors)) != len(selectors):
            raise ValueError('name each bill once; give one amount per bill')
        return self


class BillPaymentVoidInput(_Input):
    payment: _Selector
    expected_version: _Version | None = None


class BillPaymentQueryInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)
    date_from: _Date | None = None
    date_to: _Date | None = None
    vendor: _Selector | None = None
    bill: _Selector | None = None
    funding_account: _Selector | None = None
    method: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    check_number: str | None = Field(default=None, max_length=64)
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest payment first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class BillApplicationOutput(CreatedOutput):
    """One dated settlement edge: this much of this payment answered this bill."""

    kind: Literal['apply', 'unapply']
    source_transaction_id: str
    source_key_id: str
    source_component_id: str
    obligation_transaction_id: str
    obligation_key_id: str
    bill_number: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    effective_date: str
    reverses_application_id: str | None
    audit_event_id: str
    active: bool


class BillPaymentSettlementOutput(_Input):
    """Where this payment's money currently stands."""

    payment_id: str
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


class BillPaymentLineOutput(CreatedOutput):
    """One selected bill on the payment, with the capacity it carries.

    ``description`` keeps the bill this line was entered for, which never changes. ``bill_id``
    and ``bill_number`` say where this line's capacity is attached **now**, and are empty when
    nothing holds it or when an apply split it across more than one bill; ``applications`` on
    the payment is the authority in either case.
    """

    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['bill_payment']
    bill_id: str
    bill_number: str
    source_component_id: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    applied_minor_units: int
    applied: MoneyOutput
    class_id: str | None
    class_name: str | None
    description: str | None


class BillPaymentRevisionOutput(CreatedOutput):
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal['vendor']
    name_id: str
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str
    audit_event_id: str
    profile: BillPaymentProfile
    issuer_snapshot: dict[str, str | None]
    lines: list[BillPaymentLineOutput]
    batches: list[JournalBatchOutput]


class BillPaymentSummaryOutput(CommonOut):
    type: Literal['bill_payment']
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
    funding_account_id: str
    funding_kind: Literal['bank_cash', 'card_liability']
    payment_method_id: str
    payment_method_name: str
    check_number: str | None
    reference: str | None
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str
    settlement_current: BillPaymentSettlementOutput


class BillPaymentOutput(BillPaymentSummaryOutput):
    revision: BillPaymentRevisionOutput
    applications: list[BillApplicationOutput] = Field(default_factory=list)


class BillPaymentWriteOutput(BillPaymentOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class BillPayOutput(WriteOutput):
    """What one ``bill pay`` wrote.

    ``payments`` is one entry per payee group. ``group_count`` above one means the selection
    spanned more than one vendor or payable account, and every split is visible here rather
    than implied.
    """

    payments: list[BillPaymentOutput]
    group_count: int
    paid_minor_units: int
    paid: MoneyOutput
    currency: str
    bill_count: int


class BillPaymentPageOutput(_Input):
    items: list[BillPaymentSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
