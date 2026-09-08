"""Strict private G3 inputs and immutable complete composition snapshots."""
from typing import Annotated, Literal
from pydantic import Field, model_validator, model_serializer
from bookflow.company.sales_models import StrictModel, Selector, Fingerprint
from bookflow.company.journal_models import _Date, _Number, _Version
from bookflow.company.deposit_models import CashSource, ComponentOccurrence, Account, Amount, Party, Text
from bookflow.company.sales_facts import Reference
from bookflow.company.custom_fields import CustomFieldValuePatch, CustomFieldKindExpectations, CustomFieldKind

ID = Annotated[str, Field(pattern=r'^[0-9A-HJKMNP-TV-Z]{26}$')]
Ordinal = Annotated[int, Field(gt=0, le=9223372036854775807)]
Origin = Literal['entered','default','source','unresolved']

class MemoPatch(StrictModel):
    memo_override: Text | None = None
    memo_action: Literal['restore_source'] | None = None
    @model_validator(mode='after')
    def exclusive(self):
        if 'memo_action' in self.model_fields_set and self.memo_action is None:raise ValueError('memo_action must be restore_source')
        if self.memo_action is not None and 'memo_override' in self.model_fields_set:
            raise ValueError('memo_action and memo_override are exclusive')
        return self

class SourcePatch(MemoPatch):
    source_type: Literal['payment','sales_receipt']
    source: ID
    expected_version: _Version

class AdditionalPatch(StrictModel):
    line_id: ID | None = None
    received_from: Party | None = None
    from_account: Selector | None = None
    amount: Amount | None = None
    memo: Text | None = None
    check_number: Annotated[str,Field(max_length=128)] | None = None
    payment_method: Selector | None = None
    class_id: Selector | None = Field(default=None,alias='class')

class CashBackPatch(StrictModel):
    account: Selector | None = None
    amount: Amount | None = None
    memo: Text | None = None

class HeaderPatch(StrictModel):
    deposit_to: Selector | None = None
    date: _Date | None = None
    number: _Number | None = None
    memo: Text | None = None
    label: Annotated[str,Field(max_length=128)] | None = None
    cash_back: CashBackPatch | None = None
    custom_fields: CustomFieldValuePatch = Field(default_factory=lambda:CustomFieldValuePatch({}))
    expected_custom_field_kinds: CustomFieldKindExpectations = Field(default_factory=lambda:CustomFieldKindExpectations({}))

class DraftCreate(StrictModel):
    copy_from_voided: ID | None = None
    from_deposit: ID | None = None
    expected_version: _Version | None = None
    header: HeaderPatch = Field(default_factory=HeaderPatch)
    @model_validator(mode='after')
    def edit_pair(self):
        if self.from_deposit is not None and self.copy_from_voided is not None:raise ValueError('copy and edit are distinct')
        if ((self.from_deposit or self.copy_from_voided) is None)!=(self.expected_version is None):raise ValueError('source target and version required together')
        return self

class DraftRef(StrictModel):
    draft: ID
    expected_version: _Version

class SourceChanges(StrictModel):
    set_sources: list[SourcePatch] = Field(default_factory=list,max_length=200)
    remove_sources: list[ID] = Field(default_factory=list,max_length=200)
    @model_validator(mode='after')
    def disjoint(self):
        sets=[v.source for v in self.set_sources]
        if len(set(sets))!=len(sets) or len(set(self.remove_sources))!=len(self.remove_sources) or set(sets)&set(self.remove_sources):raise ValueError('source targets must be unique and disjoint')
        return self

class DraftUpdate(DraftRef,SourceChanges):
    header: HeaderPatch = Field(default_factory=HeaderPatch)
    set_additional: list[AdditionalPatch] = Field(default_factory=list,max_length=200)
    remove_lines: list[ID] = Field(default_factory=list,max_length=200)
    @model_validator(mode='after')
    def disjoint_lines(self):
        keys=[v.line_id for v in self.set_additional if v.line_id is not None]
        if len(set(keys))!=len(keys) or len(set(self.remove_lines))!=len(self.remove_lines) or set(keys)&set(self.remove_lines):raise ValueError('line targets must be unique and disjoint')
        return self

class SelectionCreate(DraftRef): pass
class SelectionRef(StrictModel):
    selection: ID
    expected_version: _Version
class SelectionUpdate(SelectionRef,SourceChanges): pass
class SelectionAccept(SelectionRef):
    draft: ID
    expected_draft_version: _Version

class Page(StrictModel):
    limit: int = Field(default=50,ge=1,le=200)
    cursor: str | None = Field(default=None,max_length=2048)
class DraftShow(StrictModel):
    draft: ID
    revision_number: _Version | None = None
class SelectionShow(StrictModel):
    selection: ID
    revision_number: _Version | None = None
class DraftItems(DraftShow,Page):
    kind: Literal['all','source','additional']='all'
class SelectionItems(SelectionShow,Page): pass
class DraftQuery(Page):
    state: Literal['open','consumed','abandoned'] | None = None
class SelectionQuery(Page):
    state: Literal['open','accepted','abandoned'] | None = None
    draft: ID | None = None
class SourceFilter(StrictModel):
    date: _Date
    payment_method_type: Literal['cash','check','credit_card','debit_card','e_check','ach','gift_card','other'] | None = None
    q: str | None = Field(default=None,max_length=2000)
    date_from: _Date | None = None
    date_to: _Date | None = None
    sort: Literal['payment_method','date','name']='date'
    direction: Literal['asc','desc']='asc'
    for_deposit: ID | None = None
    include_ineligible: bool = False
    @model_validator(mode='after')
    def dates(self):
        if self.date_from and self.date_to and self.date_from>self.date_to:raise ValueError('invalid date range')
        return self
class SourceQuery(SourceFilter,Page): pass
class SelectMatching(SelectionRef):
    filter: SourceFilter
    facts_fingerprint: Fingerprint
    mode: Literal['replace','add']='replace'

class CustomCapture(StrictModel):
    definition_id: ID
    definition_version: _Version
    name: str
    kind: CustomFieldKind
    required: bool
    print_visible: bool | None
    position: int = Field(default=0,ge=0)
    original_value_id: ID | None = None
    canonical_text: str | None
    choice_id: ID | None = None
    choice_label: str | None = None
    origin: Origin
    # Caller assertion provenance, not an inferred definition kind. Absent in
    # legacy captures and when no assertion accompanied the captured value.
    expected_kind: CustomFieldKind | None = None

    @model_serializer(mode='wrap')
    def serialize_capture(self, handler):
        value = handler(self)
        if self.expected_kind is None:
            value.pop('expected_kind', None)
        return value

class CashBack(StrictModel):
    account: Account | None = None
    units: int | None = Field(default=None,ge=-9223372036854775807,le=9223372036854775807)
    memo: Text | None = None
    origins: dict[str,Origin] = Field(default_factory=dict)
class Header(StrictModel):
    bank: Account | None = None
    date: _Date | None = None
    number: _Number | None = None
    memo: Text | None = None
    label: str | None = None
    cash_back: CashBack | None = None
    custom_fields: dict[str,CustomCapture] = Field(default_factory=dict)
    origins: dict[str,Origin] = Field(default_factory=dict)

class Source(StrictModel):
    captured_header_version: _Version | None = None
    row_id: ID
    ordinal: Ordinal
    source: CashSource
    memo: Text | None
    memo_origin: Literal['source','entered']
    occurrences: tuple[ComponentOccurrence,...]
class Additional(StrictModel):
    row_id: ID
    ordinal: Ordinal
    received_from: Party | None = None
    party_name: str | None = None
    account: Account | None = None
    units: int | None = Field(default=None,ge=-9223372036854775807,le=9223372036854775807)
    memo: Text | None = None
    check_number: str | None = None
    payment_method: Reference | None = None
    class_ref: Reference | None = None
    origins: dict[str,Origin] = Field(default_factory=dict)
class Summary(StrictModel):
    source_count: int
    additional_count: int
    source_total: int
    known_additional_total: int
    subtotal: int | None
    bank_total: int | None
    posting_total: int | None
    issues: tuple[str,...]
class Manifest(StrictModel):
    currency: str
    header: Header
    sources: tuple[Source,...]=()
    additional: tuple[Additional,...]=()
    high_water: int = Field(ge=0,le=9223372036854775807)
    summary: Summary
class DraftOutput(StrictModel):
    posting_issues: tuple[str,...] = ()
    id: ID
    version: _Version
    state: Literal['open','consumed','abandoned']
    revision_id: ID
    revision_number: _Version
    manifest_hash: Fingerprint
    header: Header
    summary: Summary
    stale_source_ids: tuple[ID,...]
    edit_transaction_id: ID | None
    baseline_version: _Version | None
    copy_transaction_id: ID | None
class SelectionOutput(StrictModel):
    id: ID
    version: _Version
    state: Literal['open','accepted','abandoned']
    revision_id: ID
    revision_number: _Version
    manifest_hash: Fingerprint
    source_count: int
    source_total: int
    target_draft_id: ID
    target_revision_id: ID
    accepted_revision_id: ID | None
    stale_source_ids: tuple[ID,...]
class AcceptOutput(StrictModel):
    draft: DraftOutput
    selection: SelectionOutput
class Candidate(StrictModel):
    source: CashSource
    number: str
    recorded_at: str
    captured_name: str
    current_name: str
    payment_method_type: str | None
    payment_method_label: str | None
    membership_id: ID | None
    eligible: bool
    reason: str | None
class SourcePage(StrictModel):
    items: tuple[Candidate,...]
    total_count: int
    subtotal: int
    next_cursor: str | None
    facts_fingerprint: Fingerprint

class SourceItem(Source):
    kind: Literal['source']='source'
class AdditionalItem(Additional):
    kind: Literal['additional']='additional'
class DraftItemsOutput(StrictModel):
    items: tuple[Annotated[SourceItem|AdditionalItem,Field(discriminator='kind')],...]
    total_count: int = Field(ge=0)
    next_cursor: str | None
    facts_fingerprint: Fingerprint
class SelectionItemsOutput(StrictModel):
    items: tuple[Source,...]
    total_count: int = Field(ge=0)
    next_cursor: str | None
    facts_fingerprint: Fingerprint
class DraftQueryOutput(StrictModel):
    items: tuple[DraftOutput,...]
    total_count: int = Field(ge=0)
    next_cursor: str | None
    facts_fingerprint: Fingerprint
class SelectionQueryOutput(StrictModel):
    items: tuple[SelectionOutput,...]
    total_count: int = Field(ge=0)
    next_cursor: str | None
    facts_fingerprint: Fingerprint
