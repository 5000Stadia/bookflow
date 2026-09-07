"""Private typed deposited-source actions and complete prospective composition."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from bookflow.core.publication import OSBinding
    from bookflow.adapters.http.app import Credential
from typing import Annotated, Literal

from pydantic import Field, model_validator, field_validator

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
