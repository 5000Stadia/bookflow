"""Private statement population contracts; no certificate or activation API."""
from types import MappingProxyType
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from bookflow.core.exact import INT64_MAX
from bookflow.company.ledger_schema import TRANSACTION_TYPES

# Read from the ledger's own declaration rather than retyped: a closed literal here is what
# made a reference to a real posted document unrepresentable, which reads as a corrupt
# population rather than as the missing adapter it actually is.
Producer = Literal[TRANSACTION_TYPES]
Role = Literal['entered', 'cash', 'control', 'net', 'main_bank', 'cash_back', 'additional', 'funding']

# Which statement roles each producer's adapter can name, and what a stored key for it looks
# like. One declaration, read by the storage CHECK constraints in `reconciliation_schema` and
# by `reconciliation_storage_validation`, because the same set written in both places is what
# left the money-out family adaptable and unstorable: `_funding` shipped with the adapters and
# the storage CHECK admitted five producers, so a bill payment could be derived and then
# refused at the table. `tests/test_reconciliation_materialization.py` fails when a producer
# gains an adapter without gaining an entry here.
PRODUCER_ROLES = MappingProxyType({
    'journal_entry': ('entered',),
    'payment': ('cash',),
    'sales_receipt': ('control', 'net'),
    'invoice': ('net',),
    'deposit': ('main_bank', 'cash_back', 'additional'),
    'bill_payment': ('funding',),
    'customer_refund': ('funding',),
    'sales_tax_payment': ('funding',),
})

# The three shapes a stored key takes, the other two derived from that one declaration rather
# than listed again. A deposit names a bank effect key; a funding document names nothing but
# itself, because its statement line is the document and not a row on it; everything else names
# the entered commercial line the movement came from. The deposit family is one name and its
# subtype table's frozen CHECK spells that name out, so widening it means rebuilding
# `reconciliation_deposit_versions` too.
DEPOSIT_PRODUCERS = ('deposit',)
FUNDING_PRODUCERS = tuple(name for name, roles in PRODUCER_ROLES.items() if roles == ('funding',))
COMMERCIAL_PRODUCERS = tuple(name for name in PRODUCER_ROLES
                             if name not in DEPOSIT_PRODUCERS and name not in FUNDING_PRODUCERS)
# The account types a statement is written about. Every other leg of a document is the other
# side of one of these and is never a line a statement shows. Declared once here because the
# adapters, the proof and the version contract all have to agree on it.
STATEMENT_ACCOUNTS = ('bank', 'credit_card')


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
    account_type: Literal[STATEMENT_ACCOUNTS]
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
