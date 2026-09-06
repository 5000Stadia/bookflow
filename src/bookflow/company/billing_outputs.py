"""Bounded billing state and source attribution, separate from completion/payment."""
from typing import Literal
from pydantic import Field
from bookflow.company.sales_models import StrictModel
from bookflow.company.sales_outputs import SalesSummaryOutput, MoneyOutput
from bookflow.company.billing_facts import ExactFraction
from bookflow.company.work_preferences import WorkBillingPreferences


class BillingLineOutput(StrictModel):
    requires_bounded_recovery: bool
    recommended_net_amount: MoneyOutput | None
    line_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    item_id: str
    description: str | None
    billable: bool
    state: Literal['unbilled', 'partially_billed', 'billed', 'nonbillable', 'no_charge']
    quantity: str
    completed_quantity: str
    billed_quantity: str
    remaining_quantity: str
    billed_quantity_fraction: ExactFraction | None = None
    remaining_quantity_fraction: ExactFraction | None = None
    billed_scope_percent_fraction: ExactFraction | None = None
    billed_scope_percent: str = '0'
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    billed_net_minor_units: int
    billed_tax_minor_units: int
    remaining_net_minor_units: int
    remaining_tax_minor_units: int
    destination_id: str | None = None
    destination_type: str | None = None


class BillingDestinationOutput(SalesSummaryOutput):
    amount_due_minor_units: int
    amount_due: MoneyOutput


class BillingOutput(StrictModel):
    preferences: WorkBillingPreferences
    closes_on_remaining_bill: bool
    source_id: str
    source_kind: str
    source_version: int
    source_revision_id: str
    owner_id: str
    owner_kind: str
    owner_version: int
    currency: str
    lines: list[BillingLineOutput]
    destinations: list[BillingDestinationOutput]
    count: int
    has_more: bool
    next_cursor: str | None
    audit_watermark: int
    can_invoice: bool
    can_sales_receipt: bool
    remaining_net_minor_units: int
    remaining_tax_minor_units: int
    warnings: list[str] = Field(default_factory=list)
