"""Typed input and output for billing groups and for one batch of invoices.

**The batch input deliberately cannot carry commercial defaults.** There is no ``terms``, no
``price_level``, no ``customer_tax_code``, no ``sales_tax_item``, no ``sales_rep``, no
``class_id``, no ``billing_address`` and no ``number`` on a batch, because every one of those
is a fact of the customer being invoiced and not of the run. Resolving one of them once and
stamping it on twenty invoices is the defect this feature would otherwise ship with, and the
cheapest way to make it impossible is to give the request no place to put it. What a batch
carries is the date, the lines, and the two words that are genuinely the same on every invoice:
the memo and the customer message.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookflow.company.journal_models import _Date, _Version
from bookflow.company.sales_models import Lines, Selector, StrictModel, Text
from bookflow.core.models import WriteOutput


Name = Annotated[str, Field(min_length=1, max_length=200)]
# A group is invoiced one customer at a time, and a run resolves every customer's own defaults,
# so the bound is what a person can stand to review in one preview rather than a storage limit.
Members = Annotated[list[Selector], Field(min_length=1, max_length=200)]


class GroupSelector(StrictModel):
    billing_group: Selector = Field(description="Billing group id, or its exact name.")


class BillingGroupCreateInput(StrictModel):
    name: Name = Field(description="Name for the new billing group; unique within the company, ignoring case.")
    customers: list[Selector] | None = Field(
        default=None, max_length=200,
        description="Customers or jobs to place in the group, in order; omit to create it empty.")

    @model_validator(mode="after")
    def distinct_members(self):
        _distinct(self.customers)
        return self


class BillingGroupRenameInput(GroupSelector):
    name: Name = Field(description="New name for the group; unique within the company, ignoring case.")
    expected_version: _Version | None = Field(default=None, description="Version returned by billing-group show; a stale rename is rejected.")


class BillingGroupDeleteInput(GroupSelector):
    expected_version: _Version | None = Field(default=None, description="Version returned by billing-group show; a stale delete is rejected.")


class BillingGroupMembersInput(GroupSelector):
    customers: Members = Field(description="Customers or jobs to add to or remove from the group.")

    @model_validator(mode="after")
    def distinct_members(self):
        _distinct(self.customers)
        return self


class BillingGroupListInput(StrictModel):
    query: str | None = Field(default=None, max_length=200, description="Case-insensitive substring of the group name.")
    limit: int = Field(50, strict=True, ge=1, le=200, description="Maximum groups returned, from 1 through 200.")
    cursor: str | None = Field(default=None, max_length=26, description="Id of the last group on the previous page; omit to start at the first.")


def _distinct(values: list[str] | None) -> None:
    if not values:
        return
    seen = {value.strip().casefold() for value in values}
    if len(seen) != len(values):
        raise ValueError("name a customer once; a customer cannot appear twice in one list")


class MemberOutput(BaseModel):
    customer_id: str
    customer_label: str
    active: bool
    position: int
    created_at: str
    created_by: str
    created_via: str


class BillingGroupOutput(BaseModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    name: str
    member_count: int


class BillingGroupShowOutput(BillingGroupOutput):
    members: list[MemberOutput]


class BillingGroupWriteOutput(WriteOutput):
    billing_group: BillingGroupShowOutput | None = None
    deleted: bool = False
    changed: bool = True


class BillingGroupPageOutput(BaseModel):
    items: list[BillingGroupOutput]
    count: int
    next_cursor: str | None


class BatchInvoicePostInput(StrictModel):
    date: _Date = Field(description="Invoice date for every invoice in this batch.")
    billing_group: Selector | None = Field(default=None, description="Billing group whose members are invoiced, in member order.")
    customers: list[Selector] | None = Field(
        default=None, max_length=200,
        description="Explicit customers or jobs to invoice, in the order given; use instead of billing_group.")
    lines: Lines = Field(description="The same lines on every invoice; each customer's own price level, tax code and class still resolve per invoice.")
    memo: Text | None = Field(default=None, description="Memo written on every invoice in this batch.")
    customer_message: Text | None = Field(default=None, description="Message written on every invoice in this batch; omit to take each customer's own default message.")

    @model_validator(mode="after")
    def one_source(self):
        if (self.billing_group is None) == (self.customers is None):
            raise ValueError("give either billing_group or customers, not both and not neither")
        _distinct(self.customers)
        if self.customers is not None and not self.customers:
            raise ValueError("customers must name at least one customer")
        return self


class BatchInvoiceRetryInput(StrictModel):
    batch: Selector = Field(description="Recorded batch whose failed customers are invoiced again.")
    date: _Date | None = Field(default=None, description="Invoice date for the retry; omit to reuse the original batch date.")


class BatchSelector(StrictModel):
    batch: Selector = Field(description="Recorded batch id.")


class BatchInvoiceQueryInput(StrictModel):
    billing_group: Selector | None = Field(default=None, description="Only batches addressed to this billing group.")
    date_from: _Date | None = Field(default=None, description="Earliest invoice date, inclusive.")
    date_to: _Date | None = Field(default=None, description="Latest invoice date, inclusive.")
    limit: int = Field(25, strict=True, ge=1, le=200, description="Maximum batches returned, from 1 through 200.")
    cursor: str | None = Field(default=None, max_length=26, description="Id of the last batch on the previous page; omit to start at the newest.")


class BatchMoneyOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    amount: str
    currency: str
    minor_units: int


class BatchRowOutput(BaseModel):
    """One customer of one batch, previewed or recorded.

    ``number`` is null in a preview and never guessed: a document number is taken from the
    allocator at the moment the invoice is written, so a preview that printed one would print
    the same number against every row.
    """

    position: int
    customer_id: str
    customer_label: str
    status: Literal["created", "failed", "will_create"]
    transaction_id: str | None = None
    number: str | None = None
    total: BatchMoneyOutput | None = None
    subtotal: BatchMoneyOutput | None = None
    tax: BatchMoneyOutput | None = None
    terms: str | None = None
    due_date: str | None = None
    price_level: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class BatchInvoiceOutput(WriteOutput):
    """The batch itself. ``batch_id`` is null while this is only a preview."""

    batch_id: str | None = None
    date: str
    billing_group_id: str | None = None
    billing_group_name: str | None = None
    retry_of_batch_id: str | None = None
    requested_count: int
    created_count: int
    failed_count: int
    created_total: BatchMoneyOutput
    currency: str
    batch_rows: list[BatchRowOutput]
    created_at: str | None = None
    created_by: str | None = None
    created_via: str | None = None


class BatchSummaryOutput(BaseModel):
    batch_id: str
    date: str
    billing_group_id: str | None
    billing_group_name: str | None
    retry_of_batch_id: str | None
    requested_count: int
    created_count: int
    failed_count: int
    created_total: BatchMoneyOutput
    currency: str
    created_at: str
    created_by: str
    created_via: str


class BatchShowOutput(BatchSummaryOutput):
    batch_rows: list[BatchRowOutput]


class BatchPageOutput(BaseModel):
    items: list[BatchSummaryOutput]
    count: int
    next_cursor: str | None
