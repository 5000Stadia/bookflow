"""Typed credit memo input and results shared by every command adapter."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_serializer, model_validator

from bookflow.commands.common import CommonOut
from bookflow.company.custom_fields import CustomFieldKindExpectations, CustomFieldValuePatch
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.sales_facts import SalesLineProfile, SalesProfile, SalesTaxComponent
from bookflow.company.sales_models import (
    Fingerprint, LineDefault, Quantity, SalesMoneyInput, Selector, StrictModel, Text,
)
from bookflow.company.tax_attribution import TaxDetails
from bookflow.company.tax_policy import Policy
from bookflow.core.models import WriteOutput

MoneyOutput = JournalMoneyOutput

# A header field a credit memo can be told to take from the customer record instead of
# carrying over. Terms, due dates, shipping and payment methods are not here because a credit
# memo has none of them.
CreditHeaderDefault = Literal["sales_tax_calculation", "billing_address", "class_id",
                              "customer_tax_code", "sales_tax_item"]


class CreditLineInput(StrictModel):
    """One credited line: either an item you name, or a quantity you send back.

    A standalone line prices itself exactly as an invoice line does. A returned line names a
    source invoice line instead and prices nothing: its net and every tax cent come from what
    that invoice captured, which is why it refuses a price, an amount and a tax code.
    """

    item: Selector | None = None
    source_invoice: Selector | None = None
    source_line: Selector | None = None
    quantity: Quantity = "1"
    unit: Selector | None = None
    unit_price: str | SalesMoneyInput | None = None
    net_amount: str | SalesMoneyInput | None = None
    description: Text | None = None
    class_id: Selector | None = None
    tax_code: Selector | None = None
    use_defaults: list[LineDefault] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def one_kind_of_line(self):
        linked = {"source_invoice", "source_line"} & self.model_fields_set
        if linked and self.item is not None:
            raise ValueError("a line either names an item or returns a source invoice line, not both")
        if linked and len(linked) != 2:
            raise ValueError("a returned line needs both source_invoice and source_line")
        if not linked and self.item is None:
            raise ValueError("name an item, or name source_invoice and source_line to return one")
        if linked:
            priced = self.model_fields_set & {"unit", "unit_price", "net_amount", "tax_code", "use_defaults"}
            if priced:
                raise ValueError("a returned line takes its price and tax from the source invoice; "
                                 "remove " + ", ".join(sorted(priced)))
        if "net_amount" in self.model_fields_set and self.model_fields_set & {"unit_price"}:
            raise ValueError("net_amount conflicts with unit_price")
        for field in ("unit_price", "net_amount"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null; use use_defaults for a default price")
        return self


class CreditMemoPostInput(StrictModel):
    @model_serializer(mode="wrap")
    def omitted_tax_request(self, handler):
        values = handler(self)
        if "sales_tax_calculation" not in self.model_fields_set:
            values.pop("sales_tax_calculation", None)
        return values

    date: _Date
    customer: Selector
    lines: Annotated[list[CreditLineInput], Field(min_length=1, max_length=200)]
    ar_account: Selector | None = None
    number: _Number | None = None
    memo: Text | None = None
    class_id: Selector | None = None
    customer_tax_code: Selector | None = None
    sales_tax_item: Selector | None = None
    sales_tax_calculation: Policy = Field(None, description="Captured tax calculation; omission selects the company default; null rejects")
    customer_message: Text | None = None
    customer_purchase_order: str | None = Field(default=None, max_length=128)
    use_defaults: list[CreditHeaderDefault] = Field(default_factory=list, max_length=5)
    refresh_defaults: bool = False
    expected_facts_fingerprint: Fingerprint | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda: CustomFieldValuePatch({}))
    custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda: CustomFieldKindExpectations({}))

    @model_validator(mode="after")
    def one_origin(self):
        linked = [line for line in self.lines if line.source_invoice is not None]
        if linked and len(linked) != len(self.lines):
            raise ValueError("a credit memo either returns source invoice lines or names its own items: "
                             "the tax calculation rounds across the whole document, so a document holding "
                             "both would have a tax total that is neither captured nor calculated")
        if linked and len({line.source_invoice for line in self.lines}) != 1:
            raise ValueError("every returned line must come from the same source invoice")
        for field in self.use_defaults:
            if field in self.model_fields_set:
                raise ValueError(f"{field} is both supplied and returned to its default")
        return self


class CreditMemoShowInput(StrictModel):
    credit_memo: Selector
    revision_number: _Version | None = None


class CreditMemoHistoryInput(StrictModel):
    credit_memo: Selector
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=8192)


class CreditTaxComponentOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    document_line_id: str
    tax_item_id: str
    agency_id: str
    liability_account_id: str
    rate_percent_millionths: int
    taxable_minor_units: int
    tax_minor_units: int
    taxable: MoneyOutput
    tax: MoneyOutput
    posting_source_id: str | None
    source_tax_component_id: str | None
    component_snapshot: SalesTaxComponent


class CreditClaimOutput(CreatedOutput):
    source_transaction_id: str
    source_revision_id: str
    source_line_id: str
    start_microunits: int
    end_microunits: int
    start_quantity: str
    end_quantity: str
    source_base_quantity_microunits: int
    source_net_minor_units: int


class CreditLineOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal["credit"]
    item_id: str
    description: str | None
    quantity: str
    quantity_microunits: int
    unit_id: str | None
    unit_factor_nanounits: int
    base_quantity: str
    base_quantity_microunits: int
    unit_price: MoneyOutput | None
    pricing_basis: Literal["unit", "amount"]
    net: MoneyOutput
    tax: MoneyOutput
    gross: MoneyOutput
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    class_id: str | None
    class_name: str | None
    source_transaction_id: str | None
    source_revision_id: str | None
    source_line_id: str | None
    item_snapshot: SalesLineProfile
    tax_components: list[CreditTaxComponentOutput]
    claims: list[CreditClaimOutput] = Field(default_factory=list)


class CreditRevisionSummaryOutput(CreatedOutput):
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: Literal["customer"]
    name_id: str
    memo: str | None
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str
    audit_event_id: str
    line_count: int
    batches: list[JournalBatchOutput]
    tax_calculation_details: TaxDetails | None = None


class CreditRevisionOutput(CreditRevisionSummaryOutput):
    issuer_snapshot: dict[str, str | None]
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    profile: SalesProfile
    lines: list[CreditLineOutput]


class CreditSourceOutput(StrictModel):
    """What this credit is worth and what has been done with it so far."""

    credit_source_key_id: str
    party_id: str
    ar_account_id: str
    currency: str
    capacity_minor_units: int
    applied_minor_units: int
    available_minor_units: int
    capacity: MoneyOutput
    applied: MoneyOutput
    available: MoneyOutput


class CreditMemoSummaryOutput(CommonOut):
    type: Literal["credit_memo"]
    number: str
    current_revision_id: str
    status: Literal["posted", "voided"]
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None
    date: str
    customer_id: str
    customer_name: str
    ar_account_id: str
    origin: Literal["standalone", "return"]
    memo: str | None
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str


class CreditMemoOutput(CreditMemoSummaryOutput):
    revision: CreditRevisionOutput
    source_current: CreditSourceOutput


class CreditMemoWriteOutput(CreditMemoOutput, WriteOutput):
    facts_fingerprint: str | None = None
    changed: bool = True
    warnings: list[str] = Field(default_factory=list)
    idempotent_replay: bool = False


class CreditMemoHistoryOutput(StrictModel):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal["posted", "voided"]
    items: list[CreditRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
