"""Private statement population contracts; no certificate or activation API."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from bookflow.core.exact import INT64_MAX

Producer = Literal['journal_entry', 'payment', 'sales_receipt', 'invoice', 'deposit']
Role = Literal['entered', 'cash', 'control', 'net', 'main_bank', 'cash_back', 'additional']


class Frozen(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class StatementEffectRef(Frozen):
    producer: Producer
    transaction_id: str
    component_id: str
    role: Role


class MovementKey(Frozen):
    producer: Producer
    transaction_id: str
    revision_id: str
    account_id: str
    role: Role
    component_id: str | None = None


class StatementEffectVersion(Frozen):
    ref: StatementEffectRef
    version_id: str
    revision_id: str
    business_batch_id: str
    transition_batch_id: str | None = None
    audit_event_id: str
    movement_key: MovementKey
    account_id: str
    account_type: Literal['bank', 'credit_card']
    currency: str
    effective_date: str
    signed_debit: int = Field(ge=-INT64_MAX, le=INT64_MAX)
    active: bool
    number: str
    memo: str | None
    payees: tuple[str, ...] = ()
    posting_line_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    document_line_ids: tuple[str, ...] = ()
    # Canonical owned immutable rows, including original currency and work facts.
    provenance: tuple[str, ...] = ()

    @model_validator(mode='after')
    def consistent(self):
        if self.active != (self.signed_debit != 0):
            raise ValueError('active statement effects must be nonzero')
        if (self.movement_key.producer, self.movement_key.transaction_id, self.movement_key.revision_id,
            self.movement_key.account_id, self.movement_key.role) != (self.ref.producer, self.ref.transaction_id,
                self.revision_id, self.account_id, self.ref.role):
            raise ValueError('movement owner mismatch')
        return self

    @property
    def statement_amount(self):
        return -self.signed_debit if self.account_type == 'credit_card' else self.signed_debit


class CompletePopulation(Frozen):
    kind: Literal['complete'] = 'complete'
    account_id: str
    currency: str
    cutoff: str
    current: tuple[StatementEffectVersion, ...]
    eligible: tuple[StatementEffectVersion, ...]
    history: tuple[StatementEffectVersion, ...]
    authority_transactions: tuple[str, ...]
    signed_total: int
    dated_gl_total: int


class UnsupportedPopulation(Frozen):
    kind: Literal['account_currency_unsupported', 'population_unsupported']
    account_id: str
    account_currency: str
    home_currency: str
    reason: str = 'unsupported_producer_or_attribution'


class InvalidPopulation(Frozen):
    kind: Literal['corrupt_population'] = 'corrupt_population'
    reason: str


class StaleReference(Frozen):
    kind: Literal['stale_source_reference'] = 'stale_source_reference'


class ChangedEffects(Frozen):
    kind: Literal['changed_effects'] = 'changed_effects'
    before: tuple[StatementEffectVersion, ...]
    after: tuple[StatementEffectVersion, ...]
    authority_transactions: tuple[str, ...]
    # The future B/C coordinator supplies all participants; no wiring claimed.
    scope: Literal['present_source_aggregate'] = 'present_source_aggregate'
