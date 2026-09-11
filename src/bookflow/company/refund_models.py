"""What paying a customer back takes and what it gives back.

``customer-refund post`` names the customer, the credits it pays out and the bank account the
money leaves, and writes one document. It settles nothing: there is no invoice on the other
side of a refund, only credit capacity, so what stops the same credit being spent twice is a
consumption row rather than a settlement edge.

There is no ``update``. A refund is one amount to one customer on one date out of one account;
correcting any of those is a different refund, so the correction path is void and write again,
and the document carries exactly one revision for its whole life -- which is what lets its
receivable attribution name one posting row for ever rather than one per revision.
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
    """One credit memo to pay out, and how much of it."""

    credit_memo: _Selector
    amount: str | MoneyInput | None = Field(
        default=None,
        description='How much of this credit to pay out; omit to pay out everything it is '
                    'still worth.')


class CustomerRefundPostInput(_Input):
    """Pay a customer back what a credit memo says they are owed.

    Every refunded cent has to come from a named credit: ``sources`` is not a convenience,
    it is what makes the refund legitimate and what stops the credit also being applied to an
    invoice afterwards. ``customer``, the receivable account and the currency are taken from
    those credits, because a refund that named a different customer from the credit it spends
    would pay the wrong person.
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


class CustomerRefundVoidInput(_Input):
    refund: _Selector
    expected_version: _Version | None = None


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
    """One credit this refund spent, or the exact release that gave it back."""

    kind: Literal['consume', 'release']
    reverses_consumption_id: str | None
    transaction_id: str
    revision_id: str
    credit_memo_id: str
    credit_memo_number: str
    credit_source_key_id: str
    credit_source_component_id: str
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
