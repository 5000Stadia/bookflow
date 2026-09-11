"""What remitting sales tax takes and what it gives back.

``sales-tax pay`` names one agency, one amount and one funding account, and writes one
document. There is no selection of things to settle, because there is nothing to settle
against: sales tax is a balance, not a set of invoices, so a partial remittance leaves the
remainder owed by arithmetic rather than by an edge. That is the whole difference between this
and ``bill pay``, and it is why the input is flat.

``through_date`` is the period end the amount was measured against -- what the anchor's
window calls showing tax due through a date. It defaults to the payment date and is captured
on the document, so a remittance says which period it answered even when the check was
written weeks later.

There is no ``update``. A remittance is one amount to one agency on one date; correcting any
of those three is a different remittance, so the correction path is void and write again, and
the document carries exactly one revision for its whole life.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.journal_models import MoneyInput, _Date, _Input, _Number, _Selector, _Version
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.sales_tax_payment_facts import SalesTaxPaymentProfile
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
Reference = Annotated[str, Field(max_length=128)]
CheckNumber = Annotated[str, Field(min_length=1, max_length=64)]


class SalesTaxPayInput(_Input):
    """Remit some or all of what one tax agency is owed.

    ``amount`` is optional and defaults to everything owed to that agency through
    ``through_date``, which is what selecting the agency's whole line on a Pay Sales Tax
    screen means. Supplying less is a partial remittance and leaves the remainder owed;
    supplying more than is owed is refused rather than posted, because paying an agency more
    than the books say it is owed is an adjustment, and an adjustment is its own document.
    """

    agency: _Selector
    date: _Date
    through_date: _Date | None = None
    amount: str | MoneyInput | None = None
    funding_account: _Selector
    method: _Selector
    check_number: CheckNumber | None = None
    reference: Reference | None = None
    memo: Text | None = None
    number: _Number | None = None
    class_id: _Selector | None = None

    @model_validator(mode='after')
    def period_precedes_payment(self) -> Self:
        if self.through_date is not None and self.through_date > self.date:
            raise ValueError('through_date cannot follow the payment date')
        return self


class SalesTaxPaymentShowInput(_Input):
    payment: _Selector


class SalesTaxPaymentVoidInput(_Input):
    payment: _Selector
    expected_version: _Version | None = None


class SalesTaxPaymentQueryInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)
    date_from: _Date | None = None
    date_to: _Date | None = None
    agency: _Selector | None = None
    funding_account: _Selector | None = None
    method: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    check_number: str | None = Field(default=None, max_length=64)
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest remittance first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class SalesTaxPaymentLineOutput(CreatedOutput):
    """The remitted amount as one entered line, so the document reads like every other one."""

    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['sales_tax_payment']
    agency_id: str
    agency_name: str
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    class_id: str | None
    class_name: str | None
    description: str | None


class SalesTaxPaymentRevisionOutput(CreatedOutput):
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
    profile: SalesTaxPaymentProfile
    issuer_snapshot: dict[str, str | None]
    lines: list[SalesTaxPaymentLineOutput]
    batches: list[JournalBatchOutput]


class SalesTaxPaymentSummaryOutput(CommonOut):
    type: Literal['sales_tax_payment']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    through_date: str
    agency_id: str
    agency_name: str
    liability_account_id: str
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
    # What the agency was owed through `through_date` when the remittance was written, and
    # what that figure became once this document posted. Both are captured rather than
    # recomputed on read, so the document says what the books said at the moment it was made.
    liability_at_posting: MoneyOutput
    remainder_at_posting: MoneyOutput


class SalesTaxPaymentOutput(SalesTaxPaymentSummaryOutput):
    revision: SalesTaxPaymentRevisionOutput


class SalesTaxPaymentWriteOutput(SalesTaxPaymentOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class SalesTaxPaymentPageOutput(_Input):
    items: list[SalesTaxPaymentSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
