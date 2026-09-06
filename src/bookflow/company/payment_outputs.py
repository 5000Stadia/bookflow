"""Typed shared payment drafts and settlement projections."""
from typing import Literal
from pydantic import Field

from bookflow.company.sales_models import StrictModel
from bookflow.company.journal_outputs import JournalMoneyOutput
from bookflow.core.models import WriteOutput
from bookflow.commands.common import CommonOut
from bookflow.company.sales_facts import Reference, Account
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.payment_models import PreviewRequest
from bookflow.company.payment_models import InvoiceAmount


class SelectionContextOutput(StrictModel):
    mode: Literal['new_receipt', 'existing_credit']
    customer_id: str
    ar_account_id: str
    payment_id: str | None
    date: str
    currency: str
    label: str | None
    automatically_calculate: bool


class SelectionItemOutput(StrictModel):
    invoice_id: str
    expected_version: int
    ordinal: int
    due_minor_units: int
    amount_minor_units: int | None
    amount_origin: Literal['entered', 'calculated', 'unresolved']
    currency: str


class SelectionOutput(StrictModel):
    id: str
    version: int
    revision_id: str
    revision_version: int
    state: Literal['open', 'consumed']
    consumed_operation_id: str | None
    context: SelectionContextOutput
    amount: JournalMoneyOutput | None
    amount_origin: Literal['entered', 'selection_total', 'unresolved']
    item_count: int
    manifest_hash: str
    applied_minor_units: int
    unapplied_minor_units: int | None
    problems: list[str]


class SelectionWriteOutput(SelectionOutput, WriteOutput):
    pass


class SelectionItemsOutput(StrictModel):
    items: list[SelectionItemOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str


class SelectionPageOutput(StrictModel):
    items: list[SelectionOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str


class InvoiceSettlementOutput(StrictModel):
    invoice_id: str
    version: int
    revision_id: str
    gross_minor_units: int
    applied_minor_units: int
    due_minor_units: int
    currency: str
    status: Literal['unpaid', 'partial', 'paid', 'voided']


class PaymentPreferencesOutput(StrictModel):
    automatically_apply_payments: bool
    automatically_calculate_payments: bool
    use_undeposited_funds_for_payments: bool


class PaymentProfileOutput(StrictModel):
    schema_version: Literal[1] = 1
    payer: Reference
    lineage: list[Reference]
    billing_address: dict[str, str | None]
    ar_account: Account
    deposit_account: Account
    payment_method: Reference
    preferences: PaymentPreferencesOutput


class PaymentComponentOutput(StrictModel):
    component_key_id: str | None
    component_id: str | None
    party_id: str
    party_name: str
    ar_account_id: str
    currency: str
    received_minor_units: int
    applied_minor_units: int
    available_minor_units: int


class PaymentCurrentOutput(StrictModel):
    payment_id: str
    version: int
    revision_id: str
    status: Literal['posted', 'voided']
    received_minor_units: int
    effective_received_minor_units: int
    applied_minor_units: int
    available_minor_units: int
    currency: str
    components: list[PaymentComponentOutput]
    component_count: int


class PaymentRevisionOutput(StrictModel):
    id: str
    revision_number: int
    date: str
    number: str
    memo: str | None
    reference: str | None
    total: JournalMoneyOutput
    audit_event_id: str
    profile: PaymentProfileOutput
    custom_fields_snapshot: dict[str, SnapshotField]


class PaymentOutput(CommonOut):
    type: Literal['payment'] = 'payment'
    number: str
    status: Literal['posted', 'voided']
    current_revision_id: str
    revision: PaymentRevisionOutput
    current: PaymentCurrentOutput


class PaymentApplicationOutput(StrictModel):
    application_id: str | None
    invoice_id: str
    invoice_version: int
    source_component_key_id: str | None
    party_id: str
    amount: JournalMoneyOutput
    effective_date: str


class PaymentAllocationOutput(StrictModel):
    allocation_id: str | None
    application_id: str | None
    invoice_id: str
    target_ordinal: int
    logical_kind: Literal['net', 'tax']
    tax_item_id: str | None
    amount: JournalMoneyOutput


class PaymentEffectOutput(StrictModel):
    kind: Literal['receive', 'apply', 'unapply', 'update', 'void', 'invoice_update']
    financial_changed: bool
    operation_id: str | None
    payment_id: str
    source_components: list[PaymentComponentOutput]
    applications: list[PaymentApplicationOutput]
    allocations: list[PaymentAllocationOutput]
    document_changes: list[InvoiceSettlementOutput]


class ProspectivePageOutput(StrictModel):
    command: Literal['payment preview items'] = 'payment preview items'
    request: PreviewRequest
    facts_fingerprint: str
    kind: Literal['source_components', 'applications', 'allocations', 'document_changes']
    total_count: int
    limit: int = 50
    next_cursor: str | None
    projection: Literal['prospective'] = 'prospective'
    committed: Literal[False] = False


class PaymentEffectCounts(StrictModel):
    source_components: int
    applications: int
    allocations: int
    document_changes: int


class PaymentWriteOutput(WriteOutput):
    id: str
    version: int
    operation_key: str
    facts_fingerprint: str
    idempotent_replay: bool = False
    effect: PaymentEffectOutput
    current: PaymentCurrentOutput
    effect_counts: PaymentEffectCounts
    prospective_pages: list[ProspectivePageOutput] = Field(default_factory=list)


class PaymentEffectItemsOutput(StrictModel):
    items: list[PaymentApplicationOutput | PaymentAllocationOutput | PaymentComponentOutput | InvoiceSettlementOutput | InvoiceAmount]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
    projection: Literal['prospective', 'committed', 'current']


class PaymentExecutionOutput(StrictModel):
    actor_id: str
    interface: str
    on_behalf_of: str | None
    reason: str | None
    directive_id: str | None
    directive_code: str | None


class PaymentOperationOutput(StrictModel):
    operation_id: str
    operation_key: str
    audit_event_id: str
    request_schema_version: int
    canonical_hash: str
    provided_fields: list[str]
    context_provided_fields: list[str]
    execution: PaymentExecutionOutput
    request: PreviewRequest
    original: PaymentWriteOutput
    current: PaymentCurrentOutput


class PaymentCandidateOutput(StrictModel):
    invoice_id: str
    expected_version: int
    number: str
    customer_id: str
    date: str
    due_date: str
    currency: str
    gross_minor_units: int
    applied_minor_units: int
    due_minor_units: int
    available_source_minor_units: int | None


class PaymentCandidatesOutput(StrictModel):
    items: list[PaymentCandidateOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
    customer_id: str
    payer_balance: JournalMoneyOutput
    family_balance: JournalMoneyOutput


class PaymentCalculationOutput(SelectionItemsOutput):
    amount: JournalMoneyOutput | None
    amount_origin: Literal['entered', 'selection_total', 'unresolved']
    unapplied_minor_units: int | None
    problems: list[str]


class PaymentSummaryOutput(StrictModel):
    id: str
    version: int
    number: str
    date: str
    status: Literal['posted', 'voided']
    customer_id: str
    payment_method_id: str
    currency: str
    received_minor_units: int
    applied_minor_units: int
    unapplied_minor_units: int


class PaymentPageOutput(StrictModel):
    items: list[PaymentSummaryOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
