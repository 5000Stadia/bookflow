"""Commercial sales results shared by all command adapters."""
from typing import Literal
from pydantic import Field, model_serializer

from bookflow.commands.common import CommonOut
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.journal_outputs import CreatedOutput, JournalBatchOutput, JournalMoneyOutput
from bookflow.company.sales_facts import SalesProfile, SalesLineProfile, SalesTaxComponent
from bookflow.company.sales_models import StrictModel
from bookflow.company.billing_facts import AllocationProof, ExactFraction
from bookflow.core.models import WriteOutput
from bookflow.company.payment_outputs import InvoiceSettlementOutput, InvoiceCorrectionOutput

MoneyOutput = JournalMoneyOutput


class TaxComponentOutput(CreatedOutput):
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
    component_snapshot: SalesTaxComponent


class SalesLineOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: Literal["sale"]
    item_id: str
    description: str | None
    quantity: str
    base_quantity: str
    quantity_microunits: int | None
    base_quantity_microunits: int | None
    quantity_fraction: ExactFraction | None = None
    base_quantity_fraction: ExactFraction | None = None
    quoted_quantity: str | None = None
    unit_id: str | None
    unit_factor_nanounits: int
    unit_price: MoneyOutput | None
    pricing_basis: Literal["unit", "amount", "allocated"] = "unit"
    net: MoneyOutput
    tax: MoneyOutput
    gross: MoneyOutput
    unit_price_minor_units: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    currency: str
    item_snapshot: SalesLineProfile
    tax_components: list[TaxComponentOutput]


class BillingSourceLinkOutput(StrictModel):
    source_document_id: str
    source_revision_id: str


class BillingSourceOutput(CreatedOutput):
    transaction_id: str
    revision_id: str
    source_document_id: str
    source_revision_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    document_line_id: str
    quantity_microunits: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    facts_snapshot: dict
    allocation_version: Literal[1, 2] = 1
    allocation_proof: AllocationProof | None = None


class SalesRevisionSummaryOutput(CreatedOutput):
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
    billing_links: list[BillingSourceLinkOutput] = Field(default_factory=list)


class SalesRevisionOutput(SalesRevisionSummaryOutput):
    billing_sources: list[BillingSourceOutput] = Field(default_factory=list)
    issuer_snapshot: dict[str, str | None]
    custom_fields_snapshot: dict[str, SnapshotField]
    custom_fields: list[SnapshotField]
    profile: SalesProfile
    lines: list[SalesLineOutput]


class SalesSummaryOutput(CommonOut):
    settlement_current: InvoiceSettlementOutput | None = None

    @model_serializer(mode='wrap')
    def compatible_summary(self, handler):
        result = handler(self)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result
    type: Literal["invoice", "sales_receipt"]
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
    memo: str | None
    due_date: str | None
    subtotal: MoneyOutput
    tax: MoneyOutput
    total: MoneyOutput
    subtotal_minor_units: int
    tax_minor_units: int
    total_minor_units: int
    currency: str


class SalesOutput(SalesSummaryOutput):
    revision: SalesRevisionOutput
    settlement_current: InvoiceSettlementOutput | None = None

    @model_serializer(mode='wrap')
    def compatible_settlement(self, handler):
        result = handler(self)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result


class BillingProgressAmount(StrictModel):
    quantity: str
    quantity_fraction: ExactFraction
    scope_percent: str
    scope_percent_fraction: ExactFraction
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int


class BillingProgressLine(StrictModel):
    line_id: str
    root_document_id: str
    root_line_id: str
    previous: BillingProgressAmount
    current: BillingProgressAmount
    cumulative: BillingProgressAmount
    remaining: BillingProgressAmount


class WorkBillingSourceEffect(StrictModel):
    source_id: str
    source_kind: Literal['estimate', 'work_order']
    version_before: int
    version_after: int
    active_before: bool
    active_after: bool
    automatically_closed: bool


class WorkBillingCurrent(StrictModel):
    source_id: str
    version: int
    active: bool
    status: str


class SalesWriteOutput(SalesOutput, WriteOutput):
    settlement: InvoiceCorrectionOutput | None = None
    source_effect: WorkBillingSourceEffect | None = None
    source_current: WorkBillingCurrent | None = None
    billing_progress: list[BillingProgressLine] = Field(default_factory=list)
    facts_fingerprint: str | None = None
    changed: bool = True
    changed_fields: list[str] = Field(default_factory=list)
    merged_over_versions: list[int] = Field(default_factory=list)
    idempotent_replay: bool = False

    @model_serializer(mode='wrap')
    def compatible_payment_settlement(self, handler):
        result = handler(self)
        if self.settlement is None:
            result.pop('settlement', None)
        if self.settlement_current is None:
            result.pop('settlement_current', None)
        return result


class SalesPageOutput(StrictModel):
    items: list[SalesSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int


class SalesHistoryOutput(StrictModel):
    id: str
    version: int
    current_revision_id: str
    number: str
    status: Literal["posted", "voided"]
    items: list[SalesRevisionSummaryOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
