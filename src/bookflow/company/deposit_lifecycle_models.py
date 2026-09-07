"""Private ordinary lifecycle contracts; no command or draft-provider activation."""
from typing import Literal
from pydantic import Field
from bookflow.company.deposit_models import Frozen, ID, Positive, Effect, ReplaceInput
from bookflow.company.sales_models import Fingerprint, StrictModel
from bookflow.company.payment_models import OperationKey
from bookflow.company.bank_effects import BankEffect


class UpdateInput(ReplaceInput):
    dependency_guard: str | None = Field(default=None, max_length=2048)


class VoidInput(StrictModel):
    deposit: ID
    expected_version: Positive
    operation_key: OperationKey
    dependency_guard: str | None = Field(default=None, max_length=2048)
    expected_facts_fingerprint: Fingerprint | None = None


class DocumentState(Frozen):
    id: ID
    version: Positive
    revision_id: ID
    number: str
    status: Literal['posted','voided']
    revision_date: str
    currency: str
    revision_posting_total: int
    revision_subtotal: int
    revision_bank_total: int
    revision_cash_back: int
    effective_bank_total: int
    active_source_ids: tuple[ID,...]


class HeaderChange(Frozen):
    id: ID
    before_version: Positive | None
    after_version: Positive


class MembershipChange(Frozen):
    source_id: ID
    claim_id: ID
    kind: Literal['claim','release']
    reverses_membership_id: ID | None
    amount_minor_units: Positive
    currency: str


class LifecycleEffect(Frozen):
    action: Literal['post','update','void']
    before: DocumentState | None
    after: DocumentState
    financial: Effect
    reversal: Effect | None = None
    batch_ids: tuple[ID,...]
    memberships: tuple[MembershipChange,...]
    headers: tuple[HeaderChange,...]
    bank_effects: tuple[BankEffect,...]
    audit_event_id: ID


class LifecycleOutput(Frozen):
    schema_version: Literal[1] = 1
    command: Literal['deposit post','deposit update','deposit void']
    operation_key: str
    operation_id: ID | None
    changed: bool
    new_effect: bool
    idempotent_replay: bool = False
    facts_fingerprint: str
    dependency_guard: str
    effect: LifecycleEffect
    current: DocumentState
