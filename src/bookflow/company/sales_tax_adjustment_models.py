"""What adjusting sales tax due takes and what it gives back.

``sales-tax adjust`` is the anchor's Adjust Sales Tax Due: one agency, one date, one adjustment
account, a direction -- increase what the agency is owed, or reduce it -- and one amount. It
writes one document whose only effect is the sales tax payable account against the adjustment
account, attributed to that agency, so the liability read shows it under the agency and
``sales-tax pay`` can settle it.

There is no ``update``. An adjustment is one amount for one agency on one date; correcting any
of those is a different adjustment, so the correction path is void and write again and the
document carries exactly one revision for its whole life.
"""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.bill_facts import Account, Reference
from bookflow.company.journal_models import MoneyInput, _Date, _Input, _Number, _Selector, _Version
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.sales_models import StrictModel
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput
Text = Annotated[str, Field(max_length=2000)]
Direction = Literal['increase', 'reduce']


class Agency(Reference):
    """The tax agency as the adjustment captured it, with the flag that made it eligible."""

    is_tax_agency: bool = True


class SalesTaxAdjustmentProfile(StrictModel):
    """The header of an adjustment revision, frozen as it was written."""

    agency: Agency
    liability_account: Account
    adjustment_account: Account
    direction: Direction
    amount_minor_units: int
    currency: str
    # What the agency was owed on the adjustment date before and after it, read from the
    # liability derivation inside the write.
    owed_before_minor_units: int
    owed_after_minor_units: int


class SalesTaxAdjustInput(_Input):
    """Increase or reduce what one tax agency is owed, against an adjustment account.

    ``direction`` is ``increase`` when the books owe the agency more than the tax recorded on
    sales says -- a rounding difference, a penalty or interest the agency charged, or a balance
    brought in from earlier books -- and ``reduce`` when they owe it less, such as a timely-filing
    discount the agency allows. ``adjustment_account`` is the other side: an income or expense
    account, or an equity or clearing account for a balance brought in. ``number`` is the entry
    number; it defaults to the next one in the adjustment series.
    """

    agency: _Selector = Field(description='Tax agency vendor ID or name; the vendor must be flagged as a tax agency.')
    date: _Date = Field(description='Adjustment date, YYYY-MM-DD: the accounting date of the entry.')
    adjustment_account: _Selector = Field(description='Account on the other side of the sales tax payable account, ID or name: usually an income or expense account. Not the sales tax payable account itself, a bank or credit card account, or accounts receivable or payable.')
    direction: Direction = Field(description='increase: the agency is owed more (sales tax payable is credited). reduce: the agency is owed less (sales tax payable is debited).')
    amount: str | MoneyInput = Field(description='Positive amount of the adjustment, as a decimal string in the home currency or exact minor units.')
    memo: Text | None = None
    number: _Number | None = Field(default=None, description='Entry number; defaults to the next free number in the sales tax adjustment series.')
    class_id: _Selector | None = None


class SalesTaxAdjustmentShowInput(_Input):
    adjustment: _Selector


class SalesTaxAdjustmentVoidInput(_Input):
    adjustment: _Selector
    expected_version: _Version | None = None


class SalesTaxAdjustmentQueryInput(_Input):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)
    date_from: _Date | None = None
    date_to: _Date | None = None
    agency: _Selector | None = None
    adjustment_account: _Selector | None = None
    number: str | None = Field(default=None, max_length=64)
    status: Literal['posted', 'voided'] | None = None
    direction: Literal['asc', 'desc'] = Field(default='asc',
        description='Order of the accounting-date then stable-id page: asc pages the oldest adjustment first, '
                    'desc the most recent first. A cursor belongs to the direction that minted it; '
                    'changing direction rejects it, so restart without a cursor.')

    @model_validator(mode='after')
    def dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from cannot follow date_to')
        return self


class SalesTaxAdjustmentLineOutput(CreatedOutput):
    """The adjusted amount as one entered line, so the document reads like every other one."""

    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal['tax_adjustment']
    agency_id: str
    agency_name: str
    direction: Direction
    amount: MoneyOutput
    amount_minor_units: int
    currency: str
    class_id: str | None
    class_name: str | None
    description: str | None


class SalesTaxAdjustmentRevisionOutput(CreatedOutput):
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
    profile: SalesTaxAdjustmentProfile
    lines: list[SalesTaxAdjustmentLineOutput]
    batches: list[JournalBatchOutput]


class SalesTaxAdjustmentSummaryOutput(CommonOut):
    type: Literal['sales_tax_adjustment']
    number: str
    current_revision_id: str
    status: Literal['posted', 'voided']
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    agency_id: str
    agency_name: str
    liability_account_id: str
    adjustment_account_id: str
    adjustment_account_name: str
    direction: Direction
    memo: str | None
    total: MoneyOutput
    total_minor_units: int
    # Signed in the liability's own sense: positive when the agency is owed more.
    signed_amount: MoneyOutput
    currency: str
    owed_before: MoneyOutput
    owed_after: MoneyOutput


class SalesTaxAdjustmentOutput(SalesTaxAdjustmentSummaryOutput):
    revision: SalesTaxAdjustmentRevisionOutput


class SalesTaxAdjustmentWriteOutput(SalesTaxAdjustmentOutput, WriteOutput):
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)


class SalesTaxAdjustmentPageOutput(_Input):
    items: list[SalesTaxAdjustmentSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
