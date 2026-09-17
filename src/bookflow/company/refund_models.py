"""What paying a customer back takes and what it gives back.

``customer-refund post`` names the customer, the capacity it pays out and the bank account the
money leaves, and writes one document. Two documents leave a customer holding money -- a
credit memo, and a payment whose cash was more than the invoices it settled -- and a refund
draws on either. It settles nothing: there is no invoice on the other side of a refund, only
standing capacity, so what stops that same money being spent twice is a consumption row
rather than a settlement edge.

``customer-refund update`` corrects a saved refund with another immutable revision, the way
every other posted document is corrected: what is supplied replaces what was captured, the
superseded effect is reversed at its own date and a replacement is posted at the corrected one,
and the consumptions the old revision made are released and retaken in the same write. Each
revision carries its own header row, so ``ar_posting_source_id`` names that revision's own
receivable attribution rather than one shared across the document's life. What no correction
changes is who is paid: the customer, the receivable account and the currency come from the
sources being paid out.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.journal_models import MoneyInput, _Date, _Input, _Number, _Selector, _Version
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.refund_facts import CustomerRefundProfile
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
Reference = Annotated[str, Field(max_length=128)]
CheckNumber = Annotated[str, Field(min_length=1, max_length=64)]


class RefundSourceInput(_Input):
    """One document to pay out, and how much of it.

    Name a ``credit_memo`` or a ``payment``, never both: a payment source pays back the part
    of that receipt which settled no invoice, which is what a customer who overpaid is owed.
    """

    credit_memo: _Selector | None = Field(
        default=None,
        description='A credit memo to pay out. Give this or `payment`, not both.')
    payment: _Selector | None = Field(
        default=None,
        description='A customer payment to pay back out of, for the cash on it that settled '
                    'no invoice. Give this or `credit_memo`, not both.')
    amount: str | MoneyInput | None = Field(
        default=None,
        description='How much of this source to pay out; omit to pay out everything it is '
                    'still worth.')

    @model_validator(mode='after')
    def one_source(self) -> Self:
        if (self.credit_memo is None) == (self.payment is None):
            raise ValueError('name either credit_memo or payment on each source, not both')
        return self


class CustomerRefundPostInput(_Input):
    """Pay a customer back what they are owed: an issued credit, or money they overpaid.

    Every refunded cent has to come from a named source: ``sources`` is not a convenience,
    it is what makes the refund legitimate and what stops that money also being applied to an
    invoice afterwards. Each source is a credit memo or a payment carrying unapplied cash.
    ``customer``, the receivable account and the currency are taken from those sources,
    because a refund that named a different customer from the capacity it spends would pay
    the wrong person.
    """

    date: _Date
    sources: Annotated[list[RefundSourceInput], Field(min_length=1, max_length=200)]
    funding_account: _Selector
    method: _Selector
    check_number: CheckNumber | None = None
    reference: Reference | None = None
    memo: Text | None = None
    number: _Number | None = None
    class_id: _Selector | None = None
    customer: _Selector | None = Field(
        default=None,
        description='Optional guard: the customer you expect these credits to belong to. The '
                    'refund is refused when they belong to anyone else.')


class CustomerRefundShowInput(_Input):
    refund: _Selector
    revision_number: _Version | None = Field(
        default=None,
        description='Read a superseded revision of this refund instead of the current one; '
                    'omit for what the refund says now.')


class CustomerRefundVoidInput(_Input):
    refund: _Selector
    expected_version: _Version | None = None


class CustomerRefundUpdateInput(_Input):
    """A correction of a saved customer refund: what changes is what is supplied.

    ``sources`` replaces the whole list of documents the refund pays out, each with how much of
    it goes; leave it out and the captured sources stand exactly as they were, which is how the
    date, the memo, the bank account or the check number alone is corrected. ``customer`` stays
    what it always was -- an optional guard on the sources, never a choice -- because a refund
    that paid somebody else is a different refund and not a correction of this one.
    """

    refund: _Selector
    expected_version: _Version | None = None
    date: _Date | None = None
    sources: Annotated[list[RefundSourceInput], Field(min_length=1, max_length=200)] | None = None
    funding_account: _Selector | None = None
    method: _Selector | None = None
    check_number: CheckNumber | None = None
    reference: Reference | None = None
    memo: Text | None = None
    number: _Number | None = None
    class_id: _Selector | None = None
    customer: _Selector | None = Field(
        default=None,
        description='Optional guard: the customer you expect these credits to belong to. The '
                    'correction is refused when they belong to anyone else.')

    @model_validator(mode='after')
    def required_values(self) -> Self:
        for field in ('date', 'sources', 'funding_account', 'method', 'number'):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f'{field} cannot be null')
        return self


class CustomerRefundQueryInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)
    date_from: _Date | None = None
    date_to: _Date | None = None
    customer: _Selector | None = None
    funding_account: _Selector | None = None
    method: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    check_number: str | None = Field(default=None, max_length=64)
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest refund '
                    'first, desc the most recent first. A cursor belongs to the direction that minted '
                    'it; changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class CustomerRefundConsumptionOutput(CreatedOutput):
    """One capacity this refund spent, or the exact release that gave it back.

    Exactly one of the two source halves is filled: the credit memo it drew on, or the payment
    whose unapplied cash it paid back. Both are optional on the wire because neither is present
    on every row, the same way an ``applications`` row names one source of two.
    """

    kind: Literal['consume', 'release']
    reverses_consumption_id: str | None
    transaction_id: str
    revision_id: str
    credit_memo_id: str | None
    credit_memo_number: str | None
    credit_source_key_id: str | None
    credit_source_component_id: str | None
    payment_id: str | None
    payment_number: str | None
    payment_source_key_id: str | None
    payment_source_component_id: str | None
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    effective_date: str


class CustomerRefundLineOutput(CreatedOutput):
    """The refunded amount as one entered line, so the document reads like every other one."""

    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['refund']
    customer_id: str
    customer_name: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    class_id: str | None
    class_name: str | None
    description: str | None


class CustomerRefundRevisionOutput(CreatedOutput):
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal['customer']
    name_id: str
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str
    audit_event_id: str
    profile: CustomerRefundProfile
    issuer_snapshot: dict[str, str | None]
    lines: list[CustomerRefundLineOutput]
    batches: list[JournalBatchOutput]


class CustomerRefundSummaryOutput(CommonOut):
    type: Literal['customer_refund']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    customer_id: str
    customer_name: str
    ar_account_id: str
    funding_account_id: str
    payment_method_id: str
    payment_method_name: str
    check_number: str | None
    reference: str | None
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    currency: str


class CustomerRefundOutput(CustomerRefundSummaryOutput):
    revision: CustomerRefundRevisionOutput
    consumptions: list[CustomerRefundConsumptionOutput]


class CustomerRefundWriteOutput(CustomerRefundOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class CustomerRefundPageOutput(_Input):
    items: list[CustomerRefundSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class CustomerRefundHistoryInput(_Input):
    refund: _Selector
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class CustomerRefundHistoryEventOutput(_Input):
    """Stored attribution; labels use the company's retained principal directory."""

    id: str
    seq: int
    at: str
    command: str
    actor_id: str | None
    actor_name: str | None
    actor_kind: str | None
    on_behalf_of: str | None
    on_behalf_of_name: str | None
    interface: str
    client_name: str
    reason: str | None
    directive_id: str | None
    directive_code: str | None
    source_ref: str | None


class CustomerRefundHistoryRevisionOutput(CustomerRefundRevisionOutput):
    consumptions: list[CustomerRefundConsumptionOutput]
    events: list[CustomerRefundHistoryEventOutput]


class CustomerRefundHistoryOutput(_Input):
    """Oldest-first immutable revisions with the current header and retained effects."""

    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    items: list[CustomerRefundHistoryRevisionOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
