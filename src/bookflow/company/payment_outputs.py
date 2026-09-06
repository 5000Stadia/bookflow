"""Typed shared payment drafts and settlement projections."""
from typing import Annotated, Literal
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
    funding_version: int | None = None
    funding_date: str | None = None
    funding_capacities: dict[str, int] = Field(default_factory=dict)
    funding_owners: dict[str, str] | None = None
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


class InvoiceSettlementAmounts(StrictModel):
    invoice_id: str
    version: int
    revision_id: str | None
    gross_minor_units: int
    applied_minor_units: int
    due_minor_units: int
    currency: str
    status: Literal['unpaid', 'partial', 'paid', 'voided']


class InvoiceSettlementOutput(InvoiceSettlementAmounts):
    settlement_guard: str | None = None
    as_of: str | None = None
    audit_watermark: int | None = None
    all_committed_current: InvoiceSettlementAmounts | None = None


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
    payment_id: str | None
    version: int
    revision_id: str | None
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
    settlement_guard: str | None = None
    type: Literal['payment'] = 'payment'
    number: str
    status: Literal['posted', 'voided']
    current_revision_id: str
    revision: PaymentRevisionOutput
    current: PaymentCurrentOutput


class PaymentApplicationOutput(StrictModel):
    kind: Literal['apply', 'unapply'] = 'apply'
    reverses_application_id: str | None = None
    application_id: str | None
    invoice_id: str
    invoice_version: int
    source_component_key_id: str | None
    party_id: str
    amount: JournalMoneyOutput
    effective_date: str


class PaymentAllocationOutput(StrictModel):
    kind: Literal['allocation', 'reversal'] = 'allocation'
    reverses_allocation_id: str | None = None
    allocation_id: str | None
    application_id: str | None
    invoice_id: str
    target_ordinal: int
    logical_kind: Literal['net', 'tax']
    tax_item_id: str | None
    amount: JournalMoneyOutput


class PaymentEffectHeader(StrictModel):
    id: str | None
    version: int
    revision_id: str | None
    revision_number: int
    number: str
    date: str
    amount: JournalMoneyOutput
    status: Literal['posted', 'voided']


class PaymentEffectOutput(StrictModel):
    kind: Literal['receive', 'apply', 'unapply', 'update', 'void', 'invoice_update']
    financial_changed: bool
    audit_event_id: str | None = None
    before_header: PaymentEffectHeader | None = None
    after_header: PaymentEffectHeader | None = None
    preferences: PaymentPreferencesOutput | None = None
    operation_id: str | None
    payment_id: str | None
    source_components: list[PaymentComponentOutput]
    applications: list[PaymentApplicationOutput]
    allocations: list[PaymentAllocationOutput]
    document_changes: list[InvoiceSettlementOutput]


class ReceiveEffect(PaymentEffectOutput):
    kind: Literal['receive']


class ApplyEffect(PaymentEffectOutput):
    kind: Literal['apply']


class UnapplyEffect(PaymentEffectOutput):
    kind: Literal['unapply']


class UpdateEffect(PaymentEffectOutput):
    kind: Literal['update']


class VoidEffect(PaymentEffectOutput):
    kind: Literal['void']


PaymentEffect = Annotated[ReceiveEffect | ApplyEffect | UnapplyEffect | UpdateEffect | VoidEffect, Field(discriminator='kind')]


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
    changed: bool = True
    new_effect: bool = True
    id: str | None
    version: int
    operation_key: str
    facts_fingerprint: str
    idempotent_replay: bool = False
    effect: PaymentEffect
    current: PaymentCurrentOutput
    effect_counts: PaymentEffectCounts
    prospective_pages: list[ProspectivePageOutput] = Field(default_factory=list)


class PaymentEffectItemsOutput(StrictModel):
    committed: bool = False
    kind: str | None = None
    items: list[PaymentApplicationOutput | PaymentAllocationOutput | PaymentComponentOutput | InvoiceSettlementOutput | PaymentCurrentOutput | InvoiceAmount]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
    projection: Literal['prospective', 'committed', 'current']


class PaymentSettlementOutput(PaymentEffectItemsOutput):
    as_of: str | None
    audit_watermark: int
    received_minor_units: int
    applied_minor_units: int
    unapplied_minor_units: int
    all_committed_current: PaymentCurrentOutput


class ApplicationRecordOutput(StrictModel):
    id: str
    kind: Literal['apply', 'unapply']
    paying_transaction_id: str
    paid_transaction_id: str
    source_component_key_id: str
    amount_minor_units: int
    currency: str
    effective_date: str
    reverses_application_id: str | None
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class InvoiceSettlementReadOutput(InvoiceSettlementOutput):
    applications: list[ApplicationRecordOutput]
    application_count: int
    next_cursor: str | None
    facts_fingerprint: str
    net_applied_minor_units: int
    tax_applied_minor_units: int


class AllocationHistoryOutput(StrictModel):
    id: str
    application_id: str
    kind: Literal['allocation', 'reversal']
    reverses_allocation_id: str | None
    source_transaction_id: str
    source_revision_id: str
    source_component_id: str
    source_posting_source_id: str
    target_transaction_id: str
    target_revision_id: str
    target_document_line_id: str
    target_line_id: str
    target_ordinal: int
    logical_kind: Literal['net', 'tax']
    tax_item_id: str | None
    tax_component_id: str | None
    target_ar_source_id: str
    target_recognition_source_id: str
    recognition_role: Literal['sales_net', 'tax_liability']
    amount_minor_units: int
    currency: str
    effective_date: str
    facts_snapshot: str
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class ApplicationOutput(StrictModel):
    record: ApplicationRecordOutput
    original_application_id: str
    active: bool
    reverse_application_id: str | None
    current_payment: PaymentCurrentOutput
    current_invoice: InvoiceSettlementOutput
    current_allocations: list[AllocationHistoryOutput]
    current_allocation_count: int


class SettlementHistoryEntry(StrictModel):
    id: str
    audit_event_id: str
    audit_sequence: int
    kind: Literal['application', 'allocation', 'receipt_revision', 'operation']
    application: ApplicationRecordOutput | None = None
    allocation: AllocationHistoryOutput | None = None
    revision: PaymentRevisionOutput | None = None
    operation_key: str | None = None
    command: str | None = None


class SettlementHistoryOutput(StrictModel):
    items: list[SettlementHistoryEntry]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
    audit_watermark: int


class InvoiceCorrectionEffect(StrictModel):
    kind: Literal['invoice_update'] = 'invoice_update'
    operation_id: str | None
    invoice_id: str
    audit_event_id: str | None = None
    before_header: PaymentEffectHeader | None = None
    after_header: PaymentEffectHeader | None = None
    source_components: list[PaymentComponentOutput] = Field(default_factory=list)
    applications: list[PaymentApplicationOutput] = Field(default_factory=list)
    allocations: list[PaymentAllocationOutput]
    document_changes: list[InvoiceSettlementOutput | PaymentCurrentOutput]
    payment_changes: list[PaymentCurrentOutput]


class InvoiceCorrectionOutput(StrictModel):
    operation_key: str
    facts_fingerprint: str
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    effect: InvoiceCorrectionEffect
    current: InvoiceSettlementOutput
    effect_counts: PaymentEffectCounts
    prospective_pages: list[ProspectivePageOutput] = Field(default_factory=list)


class PaymentExecutionOutput(StrictModel):
    actor_id: str
    interface: str
    on_behalf_of: str | None
    reason: str | None
    directive_id: str | None
    directive_code: str | None


class SettlementChangeOutput(StrictModel):
    record_id: str
    event_id: str
    at: str
    actor_id: str | None
    on_behalf_of: str | None
    interface: str
    version_before: int | None
    version_after: int | None
    baseline_version: int | None
    current_version: int | None
    fields: list[str] | None
    unknown_fields: bool
    settlement_fields: list[str]
    latest_writer_id: str | None
    age_seconds: int


class SettlementChangesOutput(StrictModel):
    items: list[SettlementChangeOutput]
    total_count: int
    next_cursor: str | None
    facts_fingerprint: str
    unknown_history: bool
    unknown_record_ids: list[str]
    settlement_guard: str


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
    original: PaymentWriteOutput | InvoiceCorrectionOutput
    current: PaymentCurrentOutput | InvoiceSettlementOutput


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
