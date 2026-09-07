"""Private typed deposited-source actions and complete prospective composition."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from bookflow.core.publication import OSBinding
    from bookflow.adapters.http.app import Credential
from typing import Annotated, Literal

from pydantic import Field, model_validator, field_validator, field_serializer

from bookflow.company.deposit_models import CashSource, ReplacementDocument, SourceInput, SourceRow
from bookflow.company.payment_models import PaymentUpdateIntent, OperationKey, EffectProvenance
from bookflow.company.sales_models import StrictModel, Fingerprint, SalesReceiptUpdateInput
from bookflow.company.journal_models import _Version
from bookflow.core.registry import Plan
from bookflow.company.reconciliation_models import ChangedEffects, UnsupportedPopulation
from bookflow.company.journal_custom_fields import JournalCustomFieldPlan
from bookflow.company.bank_effects import BankEffect


class PaymentUpdateAction(StrictModel):
    kind: Literal['payment_update']
    input: PaymentUpdateIntent

    @model_validator(mode='after')
    def original(self):
        if 'expected_facts_fingerprint' in self.input.model_fields_set:
            raise ValueError('source fingerprint is an aggregate preview anchor')
        return self


class SalesReceiptUpdateAction(StrictModel):
    kind: Literal['sales_receipt_update']
    input: SalesReceiptUpdateInput

    @model_validator(mode='after')
    def original(self):
        if self.input.expected_version is None or 'expected_facts_fingerprint' in self.input.model_fields_set:
            raise ValueError('source version is required; fingerprint is an aggregate preview anchor')
        return self


class PaymentVoidAction(StrictModel):
    kind: Literal['payment_void']
    payment: str
    expected_version: _Version
    unapply: Literal['retain_none', 'all_active']


class SalesReceiptVoidAction(StrictModel):
    kind: Literal['sales_receipt_void']
    sales_receipt: str
    expected_version: _Version


SourceAction = Annotated[PaymentUpdateAction | SalesReceiptUpdateAction | PaymentVoidAction | SalesReceiptVoidAction, Field(discriminator='kind')]


def source_identity(action):
    from bookflow.core.ids import is_ulid
    inp = action.input if action.kind.endswith('_update') else action
    identity = inp.payment if action.kind.startswith('payment_') else inp.sales_receipt
    if not is_ulid(identity):
        raise ValueError('source must be an exact stable transaction identity')
    return identity


class SourceResult(StrictModel):
    source_result: Literal[True]
    source: str
    memo_override: str | None = Field(default=None, max_length=2000)

    @field_validator('source_result', mode='before')
    @classmethod
    def exact_true(cls, value):
        if value is not True:
            raise ValueError('source_result requires boolean true')
        return value


class CoordinateDocument(ReplacementDocument):
    sources: list[SourceInput | SourceResult] = Field(default_factory=list, max_length=200)


class DocumentReplacement(StrictModel):
    mode: Literal['document']
    document: CoordinateDocument


class VoidReplacement(StrictModel):
    mode: Literal['void']


class CoordinateInput(StrictModel):
    deposit: str
    expected_version: _Version
    source_action: SourceAction
    replacement: Annotated[DocumentReplacement | VoidReplacement, Field(discriminator='mode')]
    operation_key: OperationKey
    dependency_guard: str | None = Field(default=None, max_length=2048)
    expected_facts_fingerprint: Fingerprint | None = None

    @model_validator(mode='after')
    def source_result_owner(self):
        identity = source_identity(self.source_action)
        from bookflow.core.ids import is_ulid
        if not is_ulid(self.deposit):
            raise ValueError('deposit must be an exact stable transaction identity')
        rows = self.replacement.document.sources if self.replacement.mode == 'document' else []
        ids = [row.source for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError('each source appears once')
        for row in rows:
            if isinstance(row, SourceResult) and row.source != identity:
                raise ValueError('source_result must belong to the source action')
            if isinstance(row, SourceInput) and row.source == identity:
                raise ValueError('action source requires explicit source_result or removal')
        return self


@dataclass(frozen=True)
class PreparedSource:
    action: SourceAction
    provenance: EffectProvenance
    plan: Plan
    cash: CashSource | None
    source_fingerprint: str | None
    bank_changes: ChangedEffects | UnsupportedPopulation


@dataclass(frozen=True)
class SourceResultOverlay:
    """Validated current membership and prospective replacement for one source."""
    deposit_id: str
    source_id: str
    before_header_json: str
    membership_json: str
    retained_row: SourceRow | None
    source: PreparedSource


@dataclass(frozen=True)
class CoordinateResolution:
    """Complete financial resolution, before the complete-intent guard is issued."""
    input: CoordinateInput
    source: PreparedSource
    overlay: SourceResultOverlay
    deposit_data_json: str
    deposit_custom_plan: JournalCustomFieldPlan | None
    headers_json: str
    deposit_bank_before: tuple[BankEffect, ...]
    deposit_bank_after: tuple[BankEffect, ...]
    changed: bool


@dataclass(frozen=True)
class PreparedCoordinate:
    resolution: CoordinateResolution
    facts_fingerprint: str
    dependency_guard: str
    readset_json: str
    # Existing authenticated adapter object, never a caller identity dictionary.
    binding: OSBinding | Credential


# Closed physical source receipt schemas at co0023; explicit fields are retained
# on disk. These models do not authorize a table or validate financial equations.
from bookflow.company.deposit_models import Frozen
from bookflow.company.deposit_lifecycle_models import LifecycleEffect, DocumentState
from bookflow.company.payment_outputs import PaymentSourceOutput


class CoordinateApplicationAllocationsRow(Frozen):
    id: str
    application_id: str
    kind: str
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
    logical_kind: str
    tax_item_id: str | None
    tax_component_id: str | None
    target_ar_source_id: str
    target_recognition_source_id: str
    recognition_role: str
    amount_minor_units: int
    currency: str
    effective_date: str
    facts_snapshot: str
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class CoordinateApplicationsRow(Frozen):
    id: str
    kind: str
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


class CoordinateDocumentLineIdentitiesRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str


class CoordinateDocumentLinesRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str
    revision_id: str
    line_id: str
    position: int
    kind: str
    account_id: str | None
    side: str | None
    amount_minor_units: int | None
    currency: str
    account_snapshot: str | None
    name_type: str | None
    name_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None
    description: str | None
    original_minor_units: int | None
    original_currency: str | None
    rate_used: str | None
    rate_source: str | None


class CoordinatePaymentComponentKeysRow(Frozen):
    id: str
    transaction_id: str
    line_id: str
    party_id: str
    ar_account_id: str
    currency: str
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class CoordinatePaymentComponentsRow(Frozen):
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    component_key_id: str
    amount_minor_units: int
    currency: str
    component_snapshot: str
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class CoordinatePaymentProfilesRow(Frozen):
    revision_id: str
    transaction_id: str
    type: str
    payer_id: str
    ar_account_id: str
    deposit_account_id: str
    payment_method_id: str
    reference: str | None
    profile_snapshot: str
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class CoordinatePostingBatchesRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str
    revision_id: str
    kind: str
    effective_date: str
    reverses_batch_id: str | None
    replaces_batch_id: str | None
    audit_event_id: str


class CoordinatePostingLineSourcesRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str
    posting_line_id: str
    revision_id: str
    document_line_id: str
    amount_minor_units: int
    currency: str
    reversed_source_id: str | None
    tax_component_id: str | None
    payment_component_id: str | None
    deposit_component_id: str | None


class CoordinatePostingLinesRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str
    batch_id: str
    line_no: int
    account_id: str
    debit_minor_units: int
    credit_minor_units: int
    currency: str
    account_snapshot: str
    name_type: str | None
    name_id: str | None
    party_name: str | None
    class_id: str | None
    class_name: str | None
    description: str | None
    original_minor_units: int | None
    original_currency: str | None
    rate_used: str | None
    rate_source: str | None
    reversed_line_id: str | None


class CoordinateSalesLineProfilesRow(Frozen):
    document_line_id: str
    transaction_id: str
    revision_id: str
    created_at: str
    created_by: str
    created_via: str
    item_id: str
    quantity_microunits: int | None
    unit_id: str | None
    unit_factor_nanounits: int
    base_quantity_microunits: int | None
    unit_price_minor_units: int | None
    pricing_basis: str
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    item_snapshot: str


class CoordinateSalesProfilesRow(Frozen):
    revision_id: str
    transaction_id: str
    created_at: str
    created_by: str
    created_via: str
    type: str
    customer_id: str
    control_account_id: str
    due_date: str | None
    subtotal_minor_units: int
    tax_minor_units: int
    profile_snapshot: str


class CoordinateSalesTaxAttributionLinesRow(Frozen):
    document_line_id: str
    transaction_id: str
    revision_id: str
    line_id: str
    tax_ordinal: int
    created_at: str
    created_by: str
    created_via: str


class CoordinateSalesTaxAttributionsRow(Frozen):
    revision_id: str
    transaction_id: str
    created_at: str
    created_by: str
    created_via: str
    facts_snapshot: str


class CoordinateSalesTaxComponentsRow(Frozen):
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    created_at: str
    created_by: str
    created_via: str
    tax_item_id: str
    agency_id: str
    liability_account_id: str
    rate_percent_millionths: int
    taxable_minor_units: int
    tax_minor_units: int
    component_snapshot: str


class CoordinateSalesTaxLineKeysRow(Frozen):
    line_id: str
    transaction_id: str
    tax_ordinal: int
    created_at: str
    created_by: str
    created_via: str


class CoordinateSettlementLineKeysRow(Frozen):
    id: str
    transaction_id: str
    line_id: str
    ordinal: int
    created_at: str
    created_by: str
    created_via: str
    audit_event_id: str


class CoordinateTransactionRevisionsRow(Frozen):
    id: str
    created_at: str
    created_by: str
    created_via: str
    transaction_id: str
    revision_number: int
    supersedes_revision_id: str | None
    date: str
    number: str
    name_type: str | None
    name_id: str | None
    memo: str | None
    total_minor_units: int
    currency: str
    issuer_snapshot: str
    custom_fields_snapshot: str
    audit_event_id: str


class CoordinateTransactionsRow(Frozen):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    type: str
    number: str
    current_revision_id: str
    status: str
    voided_at: str | None
    voided_by: str | None
    void_reason: str | None
    void_posting_batch_id: str | None


class CoordinateWorkBillingAllocationsRow(Frozen):
    id: str
    transaction_id: str
    revision_id: str
    document_line_id: str
    source_document_id: str
    source_revision_id: str
    source_line_id: str
    root_document_id: str
    root_line_id: str
    quantity_microunits: int | None
    net_minor_units: int
    tax_minor_units: int
    gross_minor_units: int
    facts_snapshot: str
    created_at: str
    created_by: str
    created_via: str
    allocation_version: int
    source_basis_hash: str | None
    denominator_hex: str | None
    spans_json: str | None


class SourceRows(Frozen):
    application_allocations: tuple[CoordinateApplicationAllocationsRow, ...] = ()
    applications: tuple[CoordinateApplicationsRow, ...] = ()
    document_line_identities: tuple[CoordinateDocumentLineIdentitiesRow, ...] = ()
    document_lines: tuple[CoordinateDocumentLinesRow, ...] = ()
    payment_component_keys: tuple[CoordinatePaymentComponentKeysRow, ...] = ()
    payment_components: tuple[CoordinatePaymentComponentsRow, ...] = ()
    payment_profiles: tuple[CoordinatePaymentProfilesRow, ...] = ()
    posting_batches: tuple[CoordinatePostingBatchesRow, ...] = ()
    posting_line_sources: tuple[CoordinatePostingLineSourcesRow, ...] = ()
    posting_lines: tuple[CoordinatePostingLinesRow, ...] = ()
    sales_line_profiles: tuple[CoordinateSalesLineProfilesRow, ...] = ()
    sales_profiles: tuple[CoordinateSalesProfilesRow, ...] = ()
    sales_tax_attribution_lines: tuple[CoordinateSalesTaxAttributionLinesRow, ...] = ()
    sales_tax_attributions: tuple[CoordinateSalesTaxAttributionsRow, ...] = ()
    sales_tax_components: tuple[CoordinateSalesTaxComponentsRow, ...] = ()
    sales_tax_line_keys: tuple[CoordinateSalesTaxLineKeysRow, ...] = ()
    settlement_line_keys: tuple[CoordinateSettlementLineKeysRow, ...] = ()
    transaction_revisions: tuple[CoordinateTransactionRevisionsRow, ...] = ()
    work_billing_allocations: tuple[CoordinateWorkBillingAllocationsRow, ...] = ()


class CoordinateIdentity(Frozen):
    owner_kind: str
    logical_key: str
    physical_id: str


class CoordinateHeader(Frozen):
    before: CoordinateTransactionsRow
    after: CoordinateTransactionsRow


class SourceEvidence(Frozen):
    @field_serializer('action')
    def original_action(self, value):
        # Input presence is business meaning; filling omitted optional fields
        # with null would make valid captured edits invalid on replay.
        return value.model_dump(mode='json',by_alias=True,exclude_unset=True)

    action: SourceAction
    before_header: CoordinateTransactionsRow
    after_header: CoordinateTransactionsRow
    before: SourceRows
    inserted: SourceRows
    payment_effect: PaymentSourceOutput | None
    bank_changes: ChangedEffects | UnsupportedPopulation


class CoordinateEffect(Frozen):
    source: SourceEvidence
    deposit: LifecycleEffect
    headers: tuple[CoordinateHeader, ...]
    identities: tuple[CoordinateIdentity, ...]
    target_ids: tuple[str, ...]


class CoordinateOutput(Frozen):
    schema_version: Literal[2] = 2
    command: Literal['deposit coordinate'] = 'deposit coordinate'
    operation_key: str
    operation_id: str
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    facts_fingerprint: str
    dependency_guard: str
    effect: CoordinateEffect
    current: DocumentState
    current_headers: tuple[CoordinateTransactionsRow, ...]


class SalesComponentItem(Frozen):
    kind: Literal['sale_net','sale_tax']
    line_id: str
    tax_item_id: str | None
    revision_id: str
    document_line_id: str
    physical_component_id: str | None
    capacity: int


class SalesHeaderItem(Frozen):
    kind: Literal['header']
    before: CoordinateTransactionsRow
    after: CoordinateTransactionsRow
    revisions: tuple[CoordinateTransactionRevisionsRow, ...]
    profiles: tuple[CoordinateSalesProfilesRow, ...]
    tax_attributions: tuple[CoordinateSalesTaxAttributionsRow, ...]


class SalesLineItem(Frozen):
    kind: Literal['line']
    line: CoordinateDocumentLinesRow
    profile: CoordinateSalesLineProfilesRow
    tax_components: tuple[CoordinateSalesTaxComponentsRow, ...]
    tax_keys: tuple[CoordinateSalesTaxAttributionLinesRow, ...]


class SalesWorkItem(Frozen):
    kind: Literal['work']
    allocation: CoordinateWorkBillingAllocationsRow
