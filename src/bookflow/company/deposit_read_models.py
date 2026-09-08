"""Private stored-deposit contracts; no command or publication registration."""
from typing import Literal
from pydantic import Field, model_validator
from bookflow.company.deposit_models import Frozen, ID, Account, CashBack, SourceRow, Additional, Cell, SignedMoney
from bookflow.company.sales_models import StrictModel, Selector
from bookflow.company.journal_models import _Date, _Version
from bookflow.company.deposit_dependency_models import PageInput
from bookflow.company.deposit_lifecycle_models import DocumentState
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.company.sales_facts import Account as CapturedAccount

class ReadInput(StrictModel):
    @model_validator(mode='before')
    @classmethod
    def omitted_not_null(cls,value):
        if isinstance(value,dict) and any(v is None for k,v in value.items() if k in cls.model_fields):
            raise ValueError('omit optional fields instead of null')
        return value

class ShowInput(ReadInput):
    deposit: ID
    revision_number: _Version | None = None
    as_of: _Date | None = None

class PrintDataInput(ReadInput):
    deposit: ID
    revision_number: _Version | None = None

class ItemsInput(PrintDataInput):
    kind: Literal['sources','additional','cash_allocations']
    page: PageInput = Field(default_factory=PageInput)

class HistoryInput(ReadInput):
    deposit: ID
    page: PageInput = Field(default_factory=PageInput)

class QueryInput(ReadInput):
    deposit_to: Selector | None = None
    status: Literal['posted','voided','deleted'] | None = None
    date_from: _Date | None = None
    date_to: _Date | None = None
    number: str | None = Field(default=None,max_length=64)
    q: str | None = Field(default=None,max_length=2000)
    sort: Literal['date','number','bank_total'] = 'date'
    direction: Literal['asc','desc'] = 'desc'
    include_deleted: bool = False
    page: PageInput = Field(default_factory=PageInput)

    @model_validator(mode='after')
    def range_and_flags(self):
        if self.date_from and self.date_to and self.date_from>self.date_to:raise ValueError('inverted date range')
        if self.status=='deleted' and 'include_deleted' in self.model_fields_set and not self.include_deleted:
            raise ValueError('contradictory deleted selection')
        return self

class Totals(Frozen):
    posting_total: SignedMoney
    source_total: SignedMoney
    positive_additional_total: SignedMoney
    negative_additional_total: SignedMoney
    subtotal: SignedMoney
    cash_back: SignedMoney
    bank_total: SignedMoney

class Counts(Frozen):
    sources: int
    additional: int
    cash_allocations: int
    components: int

class Pin(Frozen):
    deposit: ID
    revision_id: ID
    revision_number: _Version

class Issuer(Frozen):
    id: str
    display_name: str
    legal_name: str | None
    home_currency: str
    address_line1: str | None
    address_line2: str | None
    address_city: str | None
    address_state: str | None
    address_postal_code: str | None
    address_country: str | None
    legal_address_line1: str | None
    legal_address_line2: str | None
    legal_address_city: str | None
    legal_address_state: str | None
    legal_address_postal_code: str | None
    legal_address_country: str | None
    ship_address_line1: str | None
    ship_address_line2: str | None
    ship_address_city: str | None
    ship_address_state: str | None
    ship_address_postal_code: str | None
    ship_address_country: str | None

class CustomValue(Frozen):
    captured: SnapshotField
    captured_print_visibility: bool | None = None

class Selected(Frozen):
    pin: Pin
    date: str
    number: str
    memo: str | None
    issuer: Issuer
    custom_fields: tuple[CustomValue,...]
    deposit_to: Account
    cash_back: CashBack | None

class DatedState(Frozen):
    as_of: str
    basis: Literal['all_current_knowledge'] = 'all_current_knowledge'
    knowledge_observed_at: str
    cutoff_after_evaluation_date: bool
    financial_state: Literal['effective','not_effective','canceled']
    bank_movement: SignedMoney
    source_membership_total: SignedMoney

class Navigation(Frozen):
    current_type: str | None = None
    kind: str
    id: ID
    label: str | None
    active: bool | None
    version: int | None
    available: bool

class SourceCurrent(Frozen):
    id: ID
    status: Literal['posted','voided']
    version: _Version
    revision_id: ID
    claim_id: ID | None
    claimed_by: ID | None
    payment_method_type: str | None

class SourceItem(Frozen):
    captured: SourceRow
    source_number: str
    from_account: CapturedAccount
    membership_ids: tuple[ID,...]
    current: SourceCurrent

class CellItem(Frozen):
    id: ID
    captured: Cell
    component_id: ID
    bucket_row_id: ID

class EvidenceLink(Frozen):
    kind: Literal['revision','operation','draft','selection','note','attachment','bank_version']
    id: ID
    related_id: ID | None = None
    active: bool | None = None
    label: str | None = None

class DependencySummary(Frozen):
    purpose: Literal['inspection_only'] = 'inspection_only'
    source_ids: tuple[ID,...]
    guard: str | None
    history: Literal['complete','unknown_history']

class DepositShow(Frozen):
    schema_version: Literal[1] = 1
    company_id: ID
    currency: str
    selected: Selected
    selected_is_current: bool
    current: DocumentState
    current_observed_at: str
    totals: Totals
    counts: Counts
    fingerprints: dict[str,str]
    dated_state: DatedState | None
    links: tuple[EvidenceLink,...]
    current_references: tuple[Navigation,...]
    dependencies: DependencySummary

class DepositItemPage(Frozen):
    selected: Pin
    kind: Literal['sources','additional','cash_allocations']
    items: tuple[SourceItem | Additional | CellItem,...]
    total_count: int
    totals: Totals
    fingerprint: str
    next_cursor: str | None
    current: DocumentState
    current_observed_at: str
    current_references: tuple[Navigation,...]

class DepositRow(Frozen):
    selected: Selected
    current: DocumentState
    totals: Totals
    counts: Counts

class DepositPage(Frozen):
    items: tuple[DepositRow,...]
    total_count: int
    totals: Totals
    effective_bank_total: SignedMoney
    fingerprint: str
    next_cursor: str | None
    previous_cursor: str | None

class HistoryEntry(Frozen):
    id: str
    kind: Literal['revision_created','replaced','membership_claimed','membership_released','coordinated_source_change','void','no_effect_operation','draft_consumed']
    event_id: ID
    at: str
    actor_id: ID
    interface: str
    on_behalf_of: ID | None
    reason: str | None
    revision_id: ID | None = None
    previous_revision_id: ID | None = None
    operation_id: ID | None = None
    operation_key: str | None = None
    source_ids: tuple[ID,...] = ()
    membership_id: ID | None = None
    batch_ids: tuple[ID,...] = ()
    bank_version_ids: tuple[ID,...] = ()
    draft_id: ID | None = None

class DepositHistoryPage(Frozen):
    deposit: ID
    items: tuple[HistoryEntry,...]
    total_count: int
    fingerprint: str
    next_cursor: str | None

class DepositPrintData(Frozen):
    document: DepositShow
    rows: tuple[SourceItem | Additional,...]
    cash_allocations: tuple[CellItem,...]
    snapshot_reference: str
    generated_at: str
