"""Explicit public deposit detail wire contracts; never a private model passthrough.

Every field here is constructed individually by the public projector in
deposit_public_reads.py. No private captured object, physical row identity,
operation recovery key or inspection guard reaches this module. Optional master
references travel only through the governed reference groups below, which are
nulled as a whole when the reading audience is not admitted to their resource.
"""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from bookflow.company.sales_models import StrictModel

ReferenceGroup = Literal['account', 'customer', 'vendor', 'employee', 'other_name',
                         'payment_method', 'class', 'custom_field', 'company']
PartyKind = Literal['customer', 'vendor', 'employee', 'other_name']
AccountType = Literal['bank', 'accounts_receivable', 'other_current_asset', 'fixed_asset', 'other_asset',
                      'accounts_payable', 'credit_card', 'other_current_liability', 'long_term_liability',
                      'equity', 'income', 'cost_of_goods_sold', 'expense', 'other_income', 'other_expense',
                      'non_posting']
Availability = Literal['available', 'unavailable']


class Public(StrictModel):
    """Closed wire shape: unknown keys are rejected in both directions."""

    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Money(Public):
    minor_units: int = Field(description='Exact signed amount in the currency minor unit; never a float.')
    currency: str = Field(description='ISO currency code of this amount.')


# --------------------------------------------------------------- reference groups


class PartyReference(Public):
    """Captured business party. A denied group keeps only its kind."""

    group: Literal['customer', 'vendor', 'employee', 'other_name']
    disclosed: bool = Field(description='False when this reader is not admitted to the party list.')
    id: str | None
    label: str | None = Field(description='Captured party name as recorded on the deposit, not the current list name.')
    version: int | None = Field(description='Captured party list version, when the source recorded one.')


class ClassReference(Public):
    group: Literal['class'] = 'class'
    disclosed: bool
    id: str | None
    label: str | None


class PaymentMethodReference(Public):
    group: Literal['payment_method'] = 'payment_method'
    disclosed: bool
    id: str | None
    label: str | None
    version: int | None


class AccountReference(Public):
    """Captured deposit-side account snapshot; the current account label is separate."""

    group: Literal['account'] = 'account'
    disclosed: bool
    id: str | None
    name: str | None
    full_name: str | None
    number: str | None
    account_type: AccountType | None
    normal_balance: Literal['debit', 'credit'] | None
    system_role: str | None
    active: bool | None
    currency: str | None


class SourceAccountReference(Public):
    """Captured posting account of the contributing receipt; a narrower snapshot."""

    group: Literal['account'] = 'account'
    disclosed: bool
    id: str | None
    name: str | None
    full_name: str | None
    number: str | None
    account_type: str | None
    normal_balance: Literal['debit', 'credit'] | None


class CurrentReference(Public):
    """Separately labelled current master, never a replacement for a captured value."""

    group: ReferenceGroup
    id: str
    label: str | None
    active: bool | None
    version: int | None
    current_type: str | None = Field(description='Current list subtype, when the master records one.')
    available: bool = Field(description='False when the master no longer exists in the company.')


class IssuerIdentity(Public):
    """Captured company identity of the deposit header; governed by the company group."""

    group: Literal['company'] = 'company'
    disclosed: bool
    id: str | None
    display_name: str | None
    legal_name: str | None
    home_currency: str | None
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


class CustomFieldValue(Public):
    """Captured custom value, governed by the custom-field group."""

    group: Literal['custom_field'] = 'custom_field'
    disclosed: bool
    definition_id: str | None
    definition_version: int | None
    name: str | None
    kind: Literal['text', 'number', 'date', 'bool', 'choice'] | None
    value: str | bool | None
    canonical_text: str | None
    position: int | None
    choice_id: str | None
    choice_label: str | None
    print_visibility: bool | None


# ------------------------------------------------------------------ shared header


class DepositPin(Public):
    deposit_id: str
    revision_id: str
    revision_number: int


class CashBackLine(Public):
    amount: Money
    memo: str | None
    account: AccountReference


class SelectedHeader(Public):
    pin: DepositPin
    date: str
    number: str
    memo: str | None
    deposit_to: AccountReference
    cash_back: CashBackLine | None
    custom_fields: tuple[CustomFieldValue, ...]
    issuer: IssuerIdentity


class CurrentState(Public):
    """Freshly observed current document facts, separate from the selected revision."""

    deposit_id: str
    version: int
    revision_id: str
    number: str
    status: Literal['posted', 'voided']
    revision_date: str
    revision_posting_total: Money
    revision_subtotal: Money
    revision_bank_total: Money
    revision_cash_back: Money
    effective_bank_total: Money
    active_source_count: int = Field(description='How many receipts this deposit currently claims. Their identities are composition-sized and travel on `deposit items --kind sources`.')


class DepositTotals(Public):
    posting_total: Money
    source_total: Money
    positive_additional_total: Money
    negative_additional_total: Money
    subtotal: Money
    cash_back: Money
    bank_total: Money


class DepositCounts(Public):
    sources: int
    additional: int
    cash_allocations: int
    components: int


class DatedFinancialState(Public):
    as_of: str
    basis: Literal['all_current_knowledge'] = 'all_current_knowledge'
    knowledge_observed_at: str
    cutoff_after_evaluation_date: bool
    financial_state: Literal['effective', 'not_effective', 'canceled']
    bank_movement: Money
    source_membership_total: Money


class InspectionSummary(Public):
    """Audience-safe inspection status. The private guard is never published."""

    purpose: Literal['inspection_only'] = 'inspection_only'
    history: Literal['complete', 'unknown_history'] = Field(
        description='complete when every disclosed dependency has readable history; unknown_history otherwise. '
                    'unknown_history does not identify a cause and does not grant or withhold permission.')
    source_count: int = Field(
        description='How many receipts this deposit has ever claimed. Their identities are '
                    'composition-sized and travel on `deposit items --kind sources`.')


class AnnotationAccess(Public):
    """Capability-derived availability; identical whether zero or many annotations exist."""

    notes: Availability
    attachments: Availability


class RevisionLink(Public):
    """A callable `deposit show` destination for one immutable revision."""

    revision_number: int
    revision_id: str
    selected: bool
    current: bool


class AnnotationLink(Public):
    """An admitted note or attachment association; captions come from their own commands."""

    kind: Literal['note', 'attachment']
    id: str
    attachment_id: str | None
    active: bool | None


class DepositDetail(Public):
    """`deposit show` output: bounded summary; composition lives on `deposit items`."""

    schema_version: Literal[1] = 1
    company_id: str
    deposit_id: str
    currency: str
    selected: SelectedHeader
    selected_is_current: bool
    current: CurrentState
    current_observed_at: str
    totals: DepositTotals
    counts: DepositCounts
    dated_state: DatedFinancialState | None
    inspection: InspectionSummary
    annotations: AnnotationAccess
    revisions: tuple[RevisionLink, ...]
    links: tuple[AnnotationLink, ...]
    current_references: tuple[CurrentReference, ...] = Field(
        description='Current masters named by this summary only. Composition references travel '
                    'beside their rows on `deposit items`.')


# -------------------------------------------------------------------- item rows


class SourcePin(Public):
    """The immutable receipt revision this deposit row captured."""

    source_type: Literal['payment', 'sales_receipt']
    transaction_id: str
    revision_id: str
    expected_header_version: int
    number: str


class SourceComponent(Public):
    """Stable component ordinal a cash allocation refers to."""

    ordinal: int
    kind: Literal['payment', 'sale_net', 'sale_tax']
    present: bool


class SourceCurrentState(Public):
    """Current receipt facts; a claim by another deposit is never identified."""

    transaction_id: str
    status: Literal['posted', 'voided']
    version: int
    revision_id: str
    claimed: bool
    claimed_by_this_deposit: bool
    payment_method_type: str | None


class SourceRow(Public):
    row: Literal['source'] = 'source'
    row_id: str
    ordinal: int
    source: SourcePin
    receipt_date: str
    amount: Money
    memo: str | None = Field(description='Effective deposit-row memo.')
    memo_origin: Literal['source', 'entered']
    source_memo: str | None
    check_reference: str | None = Field(description='Customer-supplied reference, distinct from the receipt number.')
    payer: PartyReference
    from_account: SourceAccountReference
    payment_method: PaymentMethodReference | None
    allocation_parties: tuple[PartyReference, ...]
    allocation_classes: tuple[ClassReference, ...]
    components: tuple[SourceComponent, ...]
    current: SourceCurrentState


class AdditionalRow(Public):
    row: Literal['additional'] = 'additional'
    row_id: str
    ordinal: int
    amount: Money = Field(description='Signed amount; negative rows reduce the bank effect.')
    memo: str | None
    check_number: str | None
    account: AccountReference
    party: PartyReference | None
    class_reference: ClassReference | None
    payment_method: PaymentMethodReference | None


class AllocationRow(Public):
    row: Literal['allocation'] = 'allocation'
    row_id: str = Field(description='Business row identity of the contributing source or additional row.')
    component_ordinal: int
    bucket: Literal['main_bank', 'cash_back', 'additional']
    additional_row_id: str | None = Field(description='Business row identity of the offsetting additional row.')
    amount: Money


ItemRow = Annotated[SourceRow | AdditionalRow | AllocationRow, Field(discriminator='row')]


class DepositItemsPage(Public):
    """`deposit items` output: one page of the selected revision's composition."""

    schema_version: Literal[1] = 1
    company_id: str
    deposit_id: str
    selected: DepositPin
    kind: Literal['sources', 'additional', 'cash_allocations']
    items: tuple[ItemRow, ...]
    total_count: int
    totals: DepositTotals
    fingerprint: str
    next_cursor: str | None
    current: CurrentState
    current_observed_at: str
    current_references: tuple[CurrentReference, ...] = Field(
        description='Current masters named by the rows on this page; bounded by the page limit.')


class DepositQueryRow(Public):
    selected: SelectedHeader
    current: CurrentState
    totals: DepositTotals
    counts: DepositCounts
    received_from: tuple[PartyReference, ...]


class DepositQueryPage(Public):
    schema_version: Literal[1] = 1
    company_id: str
    currency: str
    items: tuple[DepositQueryRow, ...]
    total_count: int
    totals: DepositTotals
    effective_bank_total: Money
    fingerprint: str
    next_cursor: str | None
    previous_cursor: str | None


# ---------------------------------------------------------------------- history


class HistoryEntry(Public):
    """One thing that happened to this deposit, in the order the audit recorded it.

    A revision created or replaced, a receipt claimed or released, a coordinated change to a
    source, a void, an operation that changed nothing, or a draft consumed. The recovery key
    an operation was submitted under never appears here: it is a caller's own idempotency
    secret, not a fact about the books. Neither does the reader's own ordering key: ``kind``
    and the identity that entry names already tell two rows apart.
    """

    kind: Literal['revision_created', 'replaced', 'membership_claimed', 'membership_released',
                  'coordinated_source_change', 'void', 'no_effect_operation', 'draft_consumed']
    audit_event_id: str
    at: str
    actor_id: str
    interface: str
    on_behalf_of: str | None
    reason: str | None
    revision_id: str | None
    previous_revision_id: str | None
    operation_id: str | None
    source_ids: tuple[str, ...]
    membership_id: str | None
    batch_ids: tuple[str, ...]
    bank_version_ids: tuple[str, ...]
    draft_id: str | None


class DepositHistoryPage(Public):
    """`deposit history` output: one page of the deposit's own recorded events."""

    schema_version: Literal[1] = 1
    company_id: str
    deposit_id: str
    items: tuple[HistoryEntry, ...]
    total_count: int
    fingerprint: str
    next_cursor: str | None
