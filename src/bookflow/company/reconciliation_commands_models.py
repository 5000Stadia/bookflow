"""Private successor contracts. Nothing in this module registers a command."""
from datetime import date
from typing import Annotated, Literal, get_origin
from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, model_validator
from bookflow.company.sales_models import StrictModel, Fingerprint
from bookflow.company.payment_models import OperationKey
from bookflow.company.reconciliation_storage_validation import Header, Evidence, Preferences, ProposalInput, Display, Population
from bookflow.core.exact import INT64_MAX
from bookflow.company.reconciliation_models import MovementKey, PRODUCER_ROLES

ID = Annotated[str, Field(pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')]
Version = Annotated[int, Field(strict=True, ge=1)]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63-1)]
Units = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63-1)]
Currency = Annotated[str, Field(pattern=r'^[A-Z]{3}$')]

class Model(StrictModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)

    @model_validator(mode='before')
    @classmethod
    def json_arrays(cls,value):
        # Shared dispatch validates parsed JSON/Python lists, while domain
        # collections remain immutable tuples. Element validation stays strict.
        if type(value) is dict:
            return {k:tuple(v) if type(v) is list and k in cls.model_fields and get_origin(cls.model_fields[k].annotation) is tuple else v for k,v in value.items()}
        return value

class Dated(Model):
    @model_validator(mode='after')
    def dates(self):
        for name in type(self).model_fields:
            value=getattr(self,name)
            if value is not None and name in ('date','from_date','to_date','opening_date','statement_date'):
                if date.fromisoformat(value).isoformat()!=value:raise ValueError('canonical ISO date required')
        return self

class TransactionEvidence(Model):
    kind: Literal['transaction']
    transaction_id: ID

class AttachmentEvidence(Model):
    kind: Literal['transaction_attachment']
    transaction_id: ID
    attachment_id: ID
    attachment_link_id: ID

EvidenceRef=Annotated[TransactionEvidence|AttachmentEvidence,Field(discriminator='kind')]

class StatementMoney(Model):
    """Integer money that may be zero or negative, which a statement balance often is.

    `journal_models.MoneyInput` is the same shape pinned strictly positive, which is right for
    an amount somebody is paying and wrong for a balance: an overdrawn account, a credit card,
    and an account adopted at nothing are all ordinary, and none of them is a positive number.
    """
    minor_units: int=Field(strict=True,ge=-INT64_MAX,le=INT64_MAX)
    currency: Currency
    amount: str|None=None


# The amounts a person types with their own hands, and the only ones in this family that are
# not derived from the ledger. One declaration owns all three, because a statement balance is a
# statement balance whichever end of the reconciliation it sits at, and because the last time
# this was written out per field the product asked a bookkeeper for cents.
StatementAmount = str|StatementMoney
STATEMENT_BALANCE = Field(description='A statement balance, as money: "290.00", or "-15.00" when '
                                      'the account is overdrawn.',
                          json_schema_extra={'math':{'currency':'company'}})
STATEMENT_AMOUNT_FIELDS = ('entered_balance','ending_balance')


def statement_balance(value, currency, field='entered_balance'):
    """Parse one of those into exact minor units of the company's own currency.

    Signed and zero are allowed; foreign currency is not, which is the same refusal every other
    amount in the product makes and the same one `account_population` makes about the account.
    """
    from bookflow.core.exact import _parse_scaled_decimal
    from bookflow.core.money import CURRENCIES, is_currency
    from bookflow.core.errors import BookflowError
    def invalid(problem):
        return BookflowError('E_VALIDATION',details={'fields':[{'field':field,'problem':problem}]})
    if not is_currency(currency):raise invalid('unknown home currency code')
    if isinstance(value,StatementMoney):
        if value.currency!=currency:raise invalid('foreign currency amounts are not supported; use home currency')
        if value.amount is not None and statement_balance(value.amount,currency,field)!=value.minor_units:
            raise invalid('amount contradicts minor_units')
        return value.minor_units
    if not isinstance(value,str):raise invalid('must be a decimal string or integer money object')
    text,_,given=value.strip().partition(' ')
    if given and given.strip()!=currency:raise invalid('foreign currency amounts are not supported; use home currency')
    places=CURRENCIES[currency][0]
    fraction=text.partition('.')[2]
    if len(fraction)>places:
        raise BookflowError('E_AMOUNT_PRECISION',details={'field':field,'currency':currency,
                                                          'allowed_places':places,'given_places':len(fraction)})
    return _parse_scaled_decimal(text,scale=places,field=field)


class Page(Model):
    limit: int=Field(default=50,ge=1,le=200)
    cursor: str|None=Field(default=None,max_length=2048)

class Mutation(Model):
    operation_key: OperationKey

class DraftRef(Model):
    draft: ID

class DraftChange(Mutation,DraftRef):
    expected_version: Version

class PreparedChange(DraftChange):
    expected_facts_fingerprint: Fingerprint
    dependency_guard: str

class OpeningStart(Mutation,Dated):
    account: ID
    opening_date: str
    # Minor units, like every other amount this system stores. Said out loud because this is one
    # of the two amounts a person types by hand, and a form that silently wanted cents would take
    # 290.00 as an error and 29000 as two hundred and ninety dollars without ever saying so.
    entered_balance: StatementAmount=STATEMENT_BALANCE
    evidence: Evidence
    references: tuple[EvidenceRef,...]=Field(default=(),max_length=200)

class Start(Mutation,Dated):
    account: ID
    statement_date: str
    ending_balance: StatementAmount=STATEMENT_BALANCE
    opening_id: ID|None=None
    opening_draft_id: ID|None=None
    @model_validator(mode='after')
    def opening(self):
        if (self.opening_id is None)==(self.opening_draft_id is None):raise ValueError('exactly one opening required')
        return self

class DraftUpdate(DraftChange,Dated):
    opening_date: str|None=None
    statement_date: str|None=None
    entered_balance: StatementAmount|None=None
    preferences: Preferences|None=None
    evidence: Evidence|None=None
    @model_validator(mode='after')
    def supplied(self):
        if any(getattr(self,n) is None for n in self.model_fields_set-{'operation_key','draft','expected_version'}):
            raise ValueError('supplied header values cannot be null')
        return self

class CandidateFilter(Dated):
    from_date: str|None=None
    to_date: str|None=None
    side: Literal['positive','negative']|None=None
    # Every producer a statement effect can come from, read from the one declaration rather
    # than retyped: this list shipped naming five while the adapters covered eight, so a
    # bookkeeper could not filter for the bill payments sitting in their own candidate list.
    producer: Literal[tuple(PRODUCER_ROLES)]|None=None
    number: str|None=None
    payee: str|None=None
    memo: str|None=None
    amount: Units|None=None
    hide_after_date: bool=True
    sort: Literal['date','number','payee','amount','type']='date'
    descending: bool=False
    @model_validator(mode='after')
    def interval(self):
        if self.from_date and self.to_date and self.from_date>self.to_date:raise ValueError('date interval')
        return self

class Candidates(DraftRef,Page):
    filters: CandidateFilter=Field(default_factory=CandidateFilter)

class GroupRef(Model):
    movement: MovementKey
    group_fingerprint: Fingerprint

class MarkEntry(GroupRef):
    action: Literal['mark','unmark','covered','outstanding']

class Mark(DraftChange):
    entries: tuple[MarkEntry,...]=Field(min_length=1,max_length=200)

class MarkAll(DraftChange):
    filters: CandidateFilter
    query_fingerprint: Fingerprint
    action: Literal['mark','unmark']

class AcceptEntry(Model):
    saved_group_fingerprint: Fingerprint
    current: tuple[GroupRef,...]=Field(max_length=200)

class AcceptCurrent(DraftChange):
    entries: tuple[AcceptEntry,...]=Field(min_length=1,max_length=200)

class ProposalSet(DraftChange):
    proposal_id: ID|None=None
    expected_proposal_version: Version|None=None
    role: Literal['charge','earned_credit','force_adjustment']
    input: ProposalInput
    @model_validator(mode='after')
    def shape(self):
        if (self.proposal_id is None)!=(self.expected_proposal_version is None):raise ValueError('proposal version required')
        if self.input.amount_minor_units==0 or (self.role!='force_adjustment' and self.input.amount_minor_units<0):raise ValueError('nonzero amount; positive ordinary fee')
        if self.role=='force_adjustment' and not self.input.reason:raise ValueError('adjustment reason required')
        return self

class ProposalRemove(DraftChange):
    proposal_id: ID
    expected_proposal_version: Version

class Adjustment(Dated):
    date: str
    offset_account_id: ID
    class_id: ID|None=None
    reason: str=Field(min_length=1)

class Preview(DraftRef):
    expected_version: Version
    adjustment: Adjustment|None=None

class Finish(PreparedChange):
    adjustment: Adjustment|None=None

class MemberTarget(Model):
    draft_id: ID
    account_id: ID
    key_id: ID
    saved_version_id: ID
    current_version_id: ID|None=None
    action: Literal['mark','unmark','covered','outstanding','accept_current']
    @model_validator(mode='after')
    def current(self):
        if self.action!='accept_current' and self.current_version_id is not None:raise ValueError('current version belongs only to accept-current')
        return self

class SeedTarget(Dated):
    account_id: ID
    opening_id: ID|None=None
    certificate_id: ID|None=None
    kind: Literal['opening','statement','insert']
    date: str|None=None
    @model_validator(mode='after')
    def shape(self):
        valid=(self.kind=='opening' and self.opening_id is not None and self.certificate_id is None and self.date is None) or (self.kind=='statement' and self.certificate_id is not None and self.opening_id is None and self.date is None) or (self.kind=='insert' and self.opening_id is None and self.certificate_id is None and self.date is not None)
        if not valid:raise ValueError('seed owner shape')
        return self

class CertificateTarget(Model):
    account_id: ID
    certificate_id: ID|None=None
    predecessor_id: ID|None=None
    replacement_draft_revision_id: ID|None=None
    mode: Literal['replace','invalidate','insert']
    @model_validator(mode='after')
    def shape(self):
        if not ((self.mode=='invalidate' and self.certificate_id is not None and self.replacement_draft_revision_id is None) or (self.mode=='replace' and self.certificate_id is not None and self.replacement_draft_revision_id is not None) or (self.mode=='insert' and self.certificate_id is None and self.replacement_draft_revision_id is not None)):raise ValueError('certificate owner shape')
        return self

class ProposalTarget(Model):
    proposal_id: ID
    proposal_revision_id: ID
    draft_id: ID

class MemberItem(Model):
    kind: Literal['member']
    payload: MemberTarget
class SeedItem(Model):
    kind: Literal['seed']
    payload: SeedTarget
class CertificateItem(Model):
    kind: Literal['certificate']
    payload: CertificateTarget
class ProposalItem(Model):
    kind: Literal['proposal']
    payload: ProposalTarget
AttemptItem=Annotated[MemberItem|SeedItem|CertificateItem|ProposalItem,Field(discriminator='kind')]
ITEM=TypeAdapter(AttemptItem)

class AttemptBegin(DraftChange):
    base_revision_id: ID
    attempt_generation: str=Field(min_length=1)
    declared_count: Count
    intent_hash: Fingerprint
class AttemptRef(Model):
    attempt: ID
class AttemptChange(Mutation,AttemptRef):
    expected_version: Version
class AttemptUpload(Mutation,AttemptRef):
    chunk_index: Count
    items: tuple[AttemptItem,...]=Field(min_length=1,max_length=200)
class AttemptApply(AttemptChange):
    expected_draft_version: Version
    expected_facts_fingerprint: Fingerprint

class Selection(Model):
    key_id: ID
    version_id: ID
    action: Literal['mark','covered','outstanding']

class Draft(Model):
    id: ID
    account_id: ID
    kind: Literal['opening','statement','amendment']
    version: Version
    current_revision_id: ID
    state: Literal['open','consumed','canceled']
    terminal_operation_id: ID|None=None
    # A generated sample fills a Literal with its first member and every nullable with None,
    # which for this model is a contradiction it refuses: an opening draft with no opening date.
    # The documented sample is an opening draft, matching the `reconcile opening start` example
    # so the generated pages read as one walkthrough rather than four unrelated fragments.
    header: Header=Field(json_schema_extra={'sample':{
        'format':1,'opening_date':'2026-01-31','statement_date':None,'entered_balance':125000,
        'evidence':{'format':1,'statement_reference':'Jan 2026 checking statement','entered_text':None},
        'preferences':{'format':1,'columns':['date','number','payee','amount'],'sort':'date',
                       'descending':False,'hide_after_date':True,'view':'as_certified'}}})
    base_chain_version: Count
    base_opening_id: ID|None=None
    base_head_id: ID|None=None
    repair_of_opening_id: ID|None=None
    repair_of_certificate_id: ID|None=None
    selections: tuple[Selection,...]=()
    proposal_revision_ids: tuple[ID,...]=()
    evidence_references: tuple[EvidenceRef,...]=()
    @model_validator(mode='after')
    def shape(self):
        if (self.state=='open')!=(self.terminal_operation_id is None):raise ValueError('draft terminal identity')
        if len({v.key_id for v in self.selections})!=len(self.selections):raise ValueError('duplicate selected key')
        if self.kind=='opening':
            if self.header.opening_date is None or self.header.statement_date is not None:raise ValueError('opening date shape')
        elif self.header.statement_date is None:raise ValueError('statement date required')
        if self.header.entered_balance is None:raise ValueError('entered external balance required')
        return self

class Proposal(Model):
    id: ID
    draft_id: ID
    version: Version
    revision_id: ID
    role: Literal['charge','earned_credit','force_adjustment']
    input: ProposalInput
    consumed_journal_id: ID|None=None
    consumed_revision_id: ID|None=None
    @model_validator(mode='after')
    def consumption(self):
        if self.input.amount_minor_units==0 or (self.role!='force_adjustment' and self.input.amount_minor_units<0):raise ValueError('proposal amount')
        if self.role=='force_adjustment' and not self.input.reason:raise ValueError('adjustment reason required')
        if (self.consumed_journal_id is None)!=(self.consumed_revision_id is None):raise ValueError('consumed journal pair')
        return self

class Movement(Model):
    movement: MovementKey
    group_fingerprint: Fingerprint
    component_count: Count
    amount: Units
    amount_decimal: str
    date: str
    number: str
    payees: tuple[str,...]
    memo: str|None
    eligible: bool
    stale: bool
    claimed: bool
    selected: bool

class Totals(Model):
    positive_count: Count
    positive_sum: Units
    negative_count: Count
    negative_sum: Units
    selected_sum: Units
    beginning_balance: Units
    ending_balance: Units
    cleared_balance: Units
    difference: Units
    decimal_units: dict[str,str]

class CandidatePage(Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    items: tuple[Movement,...]
    count: Count
    component_count: Count
    positive_sum: Units
    negative_sum: Units
    fingerprint: Fingerprint
    next_offset: Count|None
    # This is a private slice, not an authenticated public cursor.

class Manifest(Model):
    seeds: tuple[SeedTarget,...]
    certificates: tuple[CertificateTarget,...]
    account_versions: dict[ID,Count]

class OperationTarget(Model):
    kind: Literal['transactions','accounts','drafts','openings','certificates']
    id: ID

class Report(Model):
    certificate_id: ID
    account_id: ID
    currency: Currency
    convention: Literal['bank','card_debt']
    cutoff: str
    as_certified: Totals
    local_replacement_impact: Units
    cumulative_reconstruction: Units
    cumulative_difference: Units
    decimal_units: dict[str,str]
    captured_member_ids: tuple[ID,...]
    current_outstanding_ids: tuple[ID,...]

# Closed source arms retain the real owning input models. This is intent only;
# B/C composition, Delete and not-yet-registered producers have no substitute arm.
from bookflow.company.journal_models import JournalUpdateInput, JournalVoidInput
from bookflow.company.sales_models import InvoiceUpdateInput, InvoiceVoidInput, SalesReceiptUpdateInput, SalesReceiptVoidInput
from bookflow.company.payment_models import PaymentUpdateInput, PaymentVoidInput, PaymentUnapplyInput
from bookflow.company.deposit_lifecycle_models import UpdateInput as DepositUpdateInput, VoidInput as DepositVoidInput
class JournalUpdateAction(Model):
    kind: Literal['journal_update']
    input: JournalUpdateInput
class JournalVoidAction(Model):
    kind: Literal['journal_void']
    input: JournalVoidInput
class InvoiceUpdateAction(Model):
    kind: Literal['invoice_update']
    input: InvoiceUpdateInput
class InvoiceVoidAction(Model):
    kind: Literal['invoice_void']
    input: InvoiceVoidInput
class ReceiptUpdateAction(Model):
    kind: Literal['sales_receipt_update']
    input: SalesReceiptUpdateInput
class ReceiptVoidAction(Model):
    kind: Literal['sales_receipt_void']
    input: SalesReceiptVoidInput
class PaymentUpdateAction(Model):
    kind: Literal['payment_update']
    input: PaymentUpdateInput
class PaymentVoidAction(Model):
    kind: Literal['payment_void']
    input: PaymentVoidInput
class PaymentUnapplyAction(Model):
    kind: Literal['payment_unapply']
    input: PaymentUnapplyInput
class DepositUpdateAction(Model):
    kind: Literal['deposit_update']
    input: DepositUpdateInput
class DepositVoidAction(Model):
    kind: Literal['deposit_void']
    input: DepositVoidInput
SourceAction=Annotated[JournalUpdateAction|JournalVoidAction|InvoiceUpdateAction|InvoiceVoidAction|ReceiptUpdateAction|ReceiptVoidAction|PaymentUpdateAction|PaymentVoidAction|PaymentUnapplyAction|DepositUpdateAction|DepositVoidAction,Field(discriminator='kind')]

class AmendmentStart(Mutation):
    source_action: SourceAction|None=None
    seeds: tuple[SeedTarget,...]=Field(default=(),max_length=200)
    sealed_attempt_id: ID|None=None
    chain_versions: dict[ID,Count]
    @model_validator(mode='after')
    def intent(self):
        if self.sealed_attempt_id and self.seeds:raise ValueError('inline or sealed seeds, never both')
        if self.source_action is None and not self.seeds and self.sealed_attempt_id is None:raise ValueError('explicit amendment intent required')
        return self
class AmendmentApply(PreparedChange):
    manifest_attempt_id: ID
class AmendmentPreview(DraftRef):
    expected_version: Version
    manifest_attempt_id: ID
class Undo(Mutation):
    account: ID
    head_certificate: ID
    expected_chain_version: Version
    dependency_guard: str
class DraftQuery(Page):
    account: ID|None=None
    state: Literal['open','consumed','canceled']|None=None
class DraftHistory(DraftRef,Page):pass
class CandidateItems(DraftRef,Page,GroupRef):pass
class AttemptItems(AttemptRef,Page):pass
class CertificateRef(Model):
    certificate: ID
class CertificateQuery(Page,Dated):
    account: ID|None=None
    from_date: str|None=None
    to_date: str|None=None
    state: Literal['active','historical']|None=None
class CertificateItems(CertificateRef,Page):
    kind: Literal['selected','outstanding','coverage']='coverage'
class Discrepancy(CertificateRef,Page):pass
class OperationRef(Model):
    operation_id: ID|None=None
    operation_key: OperationKey|None=None
    @model_validator(mode='after')
    def selector(self):
        if (self.operation_id is None)==(self.operation_key is None):raise ValueError('exactly one operation selector')
        return self
class OperationItems(OperationRef,Page):
    kind: Literal['request','effects','targets','generated']
class ReportInput(CertificateRef,Page):
    view: Literal['as_certified','current_discrepancy']='as_certified'
class ReportExport(ReportInput):
    format: Literal['PDF','CSV','XLSX']
class PresetCreate(Mutation):
    name: str=Field(min_length=1)
    account_id: ID|None=None
    parameters: Preferences
class PresetUpdate(PresetCreate):
    preset: ID
    expected_version: Version
class PresetRef(Model):
    preset: ID

INPUTS={
 'reconcile opening start':OpeningStart,'reconcile start':Start,
 'reconcile draft show':DraftRef,'reconcile draft query':DraftQuery,'reconcile draft history':DraftHistory,
 'reconcile draft update':DraftUpdate,'reconcile candidates':Candidates,'reconcile candidate items':CandidateItems,
 'reconcile mark':Mark,'reconcile mark-all':MarkAll,'reconcile accept-current':AcceptCurrent,
 'reconcile proposal set':ProposalSet,'reconcile proposal remove':ProposalRemove,
 'reconcile preview':Preview,'reconcile finish':Finish,'reconcile opening finish':PreparedChange,
 'reconcile leave':DraftChange,'reconcile resume':DraftChange,'reconcile cancel':DraftChange,
 'reconcile amendment start':AmendmentStart,'reconcile amendment preview':AmendmentPreview,'reconcile amendment apply':AmendmentApply,
 'reconcile undo':Undo,'reconcile attempt begin':AttemptBegin,'reconcile attempt upload':AttemptUpload,
 'reconcile attempt seal':AttemptChange,'reconcile attempt apply':AttemptApply,'reconcile attempt abort':AttemptChange,
 'reconcile attempt show':AttemptRef,'reconcile attempt items':AttemptItems,
 'reconcile certificate query':CertificateQuery,'reconcile certificate show':CertificateRef,'reconcile certificate items':CertificateItems,
 'reconcile discrepancy':Discrepancy,'reconcile operation show':OperationRef,'reconcile operation items':OperationItems,
 'report reconciliation-summary':ReportInput,'report reconciliation-detail':ReportInput,'reconcile report export':ReportExport,
 'reconcile report-preset create':PresetCreate,'reconcile report-preset update':PresetUpdate,'reconcile report-preset show':PresetRef,'reconcile report-preset query':Page,
}

class RecoveryState(Model):
    changed: bool
    new_effect: bool
    idempotent_replay: bool
    original_effect: dict[str,JsonValue]
    current: dict[str,JsonValue]

class CapturedMember(Model):
    key_id: ID
    version_id: ID
    transaction_id: ID
    source_version: str
    movement: MovementKey
    date: str
    account_id: ID
    currency: Currency
    amount: Units
    amount_decimal: str
    classification: Literal['covered','outstanding','opening_covered','prior_cleared','selected']
    eligible_at_cutoff: bool
    display: Display

class MemberPage(Model):
    items: tuple[CapturedMember,...]
    count: Count
    next_offset: Count|None
    fingerprint: Fingerprint

class DraftRevision(Model):
    id: ID
    draft_id: ID
    account_id: ID
    revision_number: Version
    previous_revision_id: ID|None
    header: Header
    base_chain_version: Count
    base_opening_id: ID|None
    base_head_id: ID|None
    repair_of_opening_id: ID|None
    repair_of_certificate_id: ID|None
    audit_event_id: ID
    created_at: str
    created_by: ID
    created_via: str

class DraftRevisionPage(Model):
    items: tuple[DraftRevision,...]
    count: Count
    next_offset: Count|None
    fingerprint: Fingerprint

class Certificate(Model):
    id: ID
    account_id: ID
    generation: Version
    statement_date: str
    opening_id: ID
    previous_certificate_id: ID|None
    supersedes_certificate_id: ID|None
    origin_draft_revision_id: ID
    beginning_balance: Units
    ending_balance: Units
    selected_sum: Units
    original_difference: Units
    final_difference: Literal[0]
    currency: Currency
    convention: Literal['bank','card_debt']
    positive_count: Count
    positive_sum: Units
    negative_count: Count
    negative_sum: Units
    captured_source_snapshot: Population
    issuer_snapshot: dict[str,JsonValue]
    created_at: str
    created_by: ID
    created_via: str
    audit_event_id: ID

class CertificatePage(Model):
    items: tuple[Certificate,...]
    count: Count
    next_offset: Count|None
    fingerprint: Fingerprint


class AttemptPage(Model):
    items: tuple[AttemptItem,...]
    count: Count
    next_offset: Count|None
    fingerprint: Fingerprint

class OperationPage(Model):
    items: tuple[dict[str,JsonValue],...]
    count: Count
    next_offset: Count|None
    fingerprint: Fingerprint


class DraftOutput(Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    draft: Draft

class FinishOutput(Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    draft: Draft
    account_id: ID
    opening_id: ID
    certificate_id: ID
    totals: Totals


# A generated documentation sample fills an unconstrained string with "value", which a
# pattern-constrained field then refuses. These four are the first top-level ID/Fingerprint
# fields in any output model, so they carry their own samples rather than teaching the sampler
# about field names -- that would silently change the generated JSON of 32 unrelated commands.
DRAFT_SAMPLE=Field(json_schema_extra={'sample':'01ARZ3NDEKTSV4RRFFQ69G5FAV'})
FINGERPRINT_SAMPLE=Field(json_schema_extra={'sample':'0'*64})

class CandidatesOutput(Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    draft: ID=DRAFT_SAMPLE
    account_id: ID
    currency: Currency
    cutoff: str
    items: tuple[Movement,...]
    count: Count
    component_count: Count
    positive_sum: Units
    negative_sum: Units
    # The freshness token the private core computes, returned so a client can prove it acted on
    # the page it was shown; `next_cursor` is the signed continuation the private offset is not.
    fingerprint: Fingerprint=FINGERPRINT_SAMPLE
    next_cursor: str|None=None

class PreviewOutput(Model):
    contract: Literal['reconciliation.private.v1']='reconciliation.private.v1'
    draft: ID=DRAFT_SAMPLE
    account_id: ID
    currency: Currency
    kind: Literal['opening','statement','amendment']
    version: Version
    totals: Totals
    # Everything `reconcile finish` demands, so a caller never has to compute either of them.
    expected_facts_fingerprint: Fingerprint=FINGERPRINT_SAMPLE
    dependency_guard: str
    balanced: bool
