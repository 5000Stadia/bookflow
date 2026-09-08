"""Strict private deposit report inputs, arithmetic and typed runtime outputs.

Constructors do not confer authority. Session entry points load and authorize
complete populations through the accepted read and source-history owners.
"""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from bookflow.company.deposit_models import Effect, Frozen, ID, Additional
from bookflow.company.bank_effects import BankEffect
from bookflow.company.deposit_read_authority import ReadEvidence
from bookflow.company.journal_models import _Date
from bookflow.company.sales_models import Selector
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MIN, INT64_MAX
from bookflow.core.money import is_currency

Signed = Annotated[int, Field(ge=INT64_MIN, le=INT64_MAX)]
Status = Literal['posted', 'voided', 'deleted']
Projection = Literal['current', 'effective']


class DepositReportPeriod(Frozen):
    date_from: _Date
    date_to: _Date

    @model_validator(mode='after')
    def ordered(self):
        if self.date_from > self.date_to:
            raise ValueError('date_from must not follow date_to')
        return self


class DepositDetailFilter(DepositReportPeriod):
    deposit_to: Selector | None = None
    status: Status | None = None
    include_deleted: bool = False
    projection: Projection = 'effective'
    include_uf_bridge: bool = False

    @model_validator(mode='after')
    def omission(self):
        if 'status' in self.model_fields_set and self.status is None:
            raise ValueError('explicit status must name a state')
        if self.status == 'deleted' and 'include_deleted' in self.model_fields_set and not self.include_deleted:
            raise ValueError('deleted conflicts with include_deleted=false')
        return self

    def statuses(self) -> tuple[Status, ...]:
        states = (self.status,) if self.status else (
            ('posted', 'voided', 'deleted') if self.include_deleted else ('posted', 'voided'))
        if 'deleted' in states:
            raise BookflowError('E_VALIDATION', details={'field': 'include_deleted', 'problem': 'deposit deletion is not available'})
        return states


class DepositDetailInput(DepositDetailFilter):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, min_length=1)


class Currency(Frozen):
    currency: str

    @model_validator(mode='after')
    def currency_valid(self):
        if not is_currency(self.currency):
            raise ValueError('unknown currency')
        return self


class Composition(Frozen):
    source: Signed
    positive_additional: Signed
    negative_additional: Signed
    posting_total: Signed
    subtotal: Signed
    bank_total: Signed
    cash_back: Signed


class ReportRevision(Frozen):
    """Conversion target for one read-owner validated captured revision."""
    revision_id: ID
    number: str
    effect: Effect
    bank_effects: tuple[BankEffect, ...]


class ReportBatch(Frozen):
    id: ID
    revision_id: ID
    kind: Literal['original', 'replacement', 'reversal']
    effective_date: _Date
    reverses_batch_id: ID | None = None
    replaces_batch_id: ID | None = None

    @model_validator(mode='after')
    def links(self):
        if (self.kind == 'reversal') != (self.reverses_batch_id is not None):
            raise ValueError('reversal link mismatch')
        return self


class ReportDeposit(Frozen):
    """Arithmetic input, not a substitute for ValidatedDeposit or ReadEvidence."""
    id: ID
    current_revision_id: ID
    status: Literal['posted', 'voided']
    revisions: tuple[ReportRevision, ...]
    batches: tuple[ReportBatch, ...]


class BankRoleMovement(Frozen):
    role: Literal['main_bank', 'cash_back', 'additional']
    row_id: ID
    account_id: ID
    signed_debit: Signed
    statement_amount: Signed
    # Historical accounting movement, not a certified current statement entry.
    kind: Literal['captured_business_role', 'accounting_movement']


class ReportRow(Currency):
    transaction_id: ID
    revision_id: ID
    current_revision_id: ID
    current_status: Literal['posted', 'voided']
    number: str
    date: _Date
    destination_id: ID
    destination_name: str
    batch: ReportBatch | None
    composition: Composition
    bank_roles: tuple[BankRoleMovement, ...]
    source_count: int = Field(ge=0)
    additional_count: int = Field(ge=0)
    cell_count: int = Field(ge=0)
    effective_current_bank_total: Signed | None


class Population(Frozen):
    scope: Literal['selected_deposit_population'] = 'selected_deposit_population'
    projection: Projection
    destination_id: ID | None
    current_status_filter: Status | None
    is_account_balance: Literal[False] = False


class ScopedAmount(Currency):
    population: Population
    minor_units: Signed


class MovementTotals(Frozen):
    opening: ScopedAmount
    period: ScopedAmount
    closing: ScopedAmount


class AccountRoleTotal(Frozen):
    account_id: ID
    role: Literal['main_bank', 'cash_back', 'additional']
    signed_debit: ScopedAmount
    statement_amount: ScopedAmount


class ReportTotals(Currency):
    population: Population
    row_count: int = Field(ge=0)
    deposit_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    additional_count: int = Field(ge=0)
    cell_count: int = Field(ge=0)
    composition: Composition
    movement: MovementTotals | None
    account_roles: tuple[AccountRoleTotal, ...]
    effective_current_bank_total: Signed | None


class CompleteRelation(Frozen):
    rows: tuple[ReportRow, ...]
    totals: ReportTotals


class UFEvent(Frozen):
    """Validated signed effect at its accounting date, never its audit date.

    R: payment/sales-receipt UF effect. D: claim positive/release negative.
    ledger: every actual signed UF posting line including non-cash sources.
    Each stream identity identifies its owned occurrence (not just document).
    """
    identity: str = Field(min_length=1)
    transaction_id: ID
    batch_id: ID
    effective_date: _Date
    units: Signed


class UFAmounts(Frozen):
    receipts: Signed
    deposited: Signed
    source_backed: Signed
    ledger: Signed
    unexplained: Signed


class UFBridge(Currency):
    scope: Literal['company_uf'] = 'company_uf'
    deposit_filters_applied: Literal[False] = False
    account_id: ID
    period: DepositReportPeriod
    opening: UFAmounts
    change: UFAmounts
    closing: UFAmounts
    receipt_effect_count: int = Field(ge=0)
    membership_effect_count: int = Field(ge=0)
    ledger_line_count: int = Field(ge=0)


class UFComplete(Frozen):
    state: Literal['complete'] = 'complete'
    data: UFBridge
    evidence: tuple[ReadEvidence, ...] = ()


class UFUnavailable(Frozen):
    state: Literal['unavailable'] = 'unavailable'
    reason: Literal['not_requested', 'not_authorized', 'no_uf_account']


UFBridgeSection = Annotated[UFComplete | UFUnavailable, Field(discriminator='state')]

# Runtime output uses the accepted read owner's finite captured/link models.
from bookflow.company.deposit_read_models import Selected, EvidenceLink, Navigation, DepositPrintData, SourceItem, CellItem

class RuntimeRow(Frozen):
    movement: ReportRow
    selected: Selected
    sources: tuple[SourceItem, ...]
    additional: tuple[Additional, ...]
    cash_allocations: tuple[CellItem, ...]
    current_version: int = Field(gt=0)
    recorded_at: str | None
    links: tuple[EvidenceLink, ...]
    current_references: tuple[Navigation, ...]


class ReportMetadata(Currency):
    company_id: ID
    display_name: str
    legal_name: str
    period: DepositReportPeriod
    projection: Projection
    projection_version: Literal[1] = 1
    schema_revision: str
    knowledge_basis: Literal['all_current_knowledge'] = 'all_current_knowledge'
    knowledge_observed_at: str
    generated_at: str
    cutoff_after_evaluation_date: bool
    snapshot_reference: str


class DepositDetailPage(Frozen):
    metadata: ReportMetadata
    items: tuple[RuntimeRow, ...]
    totals: ReportTotals
    uf: UFBridgeSection
    next_cursor: str | None
    previous_cursor: str | None
    # Private provenance only; publication must revalidate actual fields/roots.
    evidence: tuple[ReadEvidence, ...]


class DepositReportPrintData(Frozen):
    metadata: ReportMetadata
    rows: tuple[RuntimeRow, ...]
    compositions: tuple[DepositPrintData, ...]
    totals: ReportTotals
    uf: UFBridgeSection
    evidence: tuple[ReadEvidence, ...]
