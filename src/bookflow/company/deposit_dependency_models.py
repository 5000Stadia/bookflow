"""Private complete deposit history contracts; no public command registration."""
import hashlib
import json
from typing import Literal, Annotated
from pydantic import ConfigDict, Field, model_validator
from bookflow.company.sales_models import StrictModel
from bookflow.company.deposit_lifecycle_models import PostInput, UpdateInput, VoidInput


class Frozen(StrictModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class RequestContext(Frozen):
    reason: str | None = None
    directive_id: str | None = None


class PostRequest(Frozen):
    command: Literal['deposit post']
    input: PostInput
    context: RequestContext = Field(default_factory=RequestContext)


class UpdateRequest(Frozen):
    command: Literal['deposit update']
    input: UpdateInput
    context: RequestContext = Field(default_factory=RequestContext)


class VoidRequest(Frozen):
    command: Literal['deposit void']
    input: VoidInput
    context: RequestContext = Field(default_factory=RequestContext)


from bookflow.company.deposit_coordinate_models import CoordinateInput


class CoordinateRequest(Frozen):
    command: Literal['deposit coordinate']
    input: CoordinateInput
    context: RequestContext = Field(default_factory=RequestContext)


DepositRequest = Annotated[PostRequest | UpdateRequest | VoidRequest | CoordinateRequest, Field(discriminator='command')]


def request_document(original):
    if isinstance(original, (InspectionRoot, VoidRequest)):
        return None
    if isinstance(original, CoordinateRequest):
        return original.input.replacement.document if original.input.replacement.mode == 'document' else None
    return original.input.document


class InspectionRoot(Frozen):
    kind: Literal['deposit','payment','sales_receipt']
    id: str = Field(min_length=1)


RecordKind = Literal[
    'deposit_draft', 'deposit_draft_revision', 'deposit_draft_row_key',
    'transaction',
    'deposit_number',
    'source_number',
    'deposit_number_operation',
    'company_info',
    'account',
    'customer',
    'vendor',
    'employee',
    'other_name',
    'class',
    'payment_method',
    'custom_field',
    'source_custom_field',
    'source_item',
    'source_customer',
    'source_tax_code',
    'source_unit',
    'source_price_level',
    'source_price_version',
    'source_vendor',
    'source_ship_method',
    'source_sales_rep',
    'source_message',
    'source_company',
    'work_document',
    'transaction_revision',
    'document_line_identity',
    'document_line',
    'posting_batch',
    'posting_line',
    'posting_line_source',
    'payment_profile',
    'payment_component_key',
    'payment_component',
    'sales_profile',
    'sales_line_profile',
    'sales_tax_component',
    'sales_tax_line_key',
    'sales_tax_attribution',
    'sales_tax_attribution_line',
    'settlement_line_key',
    'application',
    'application_allocation',
    'deposit_profile',
    'deposit_row_key',
    'deposit_component_key',
    'deposit_component',
    'deposit_cash_cell',
    'deposit_membership',
    'bank_effect_key',
    'bank_effect_version',
    'work_billing_conversion',
    'work_billing_allocation',
    'work_revision',
    'work_line',
    'work_revision_line',
    'work_link',
    'work_tax_line_key',
    'work_tax_attribution',
    'work_tax_attribution_line',
]


class RecordAnchor(Frozen):
    kind: RecordKind
    id: str
    version: int | None
    event_id: str | None
    entry_id: str
    semantic_json: str
    semantic_digest: str
    unknown: bool = False

    @model_validator(mode='after')
    def canonical_fact(self):
        value=json.loads(self.semantic_json)
        if not isinstance(value,dict) or json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)!=self.semantic_json:
            raise ValueError('owned fact must be a canonical object')
        if hashlib.sha256(self.semantic_json.encode()).hexdigest()!=self.semantic_digest:
            raise ValueError('owned semantic digest mismatch')
        return self


class RelationAnchor(Frozen):
    kind: str
    owner_id: str
    members: tuple[str, ...]
    count: int = Field(ge=0, strict=True)
    state: Literal['empty','populated']
    digest: str

    @model_validator(mode='after')
    def complete_members(self):
        if tuple(sorted(set(self.members)))!=self.members or self.count!=len(self.members):
            raise ValueError('relation must contain each ordered member exactly once')
        if self.state!=('populated' if self.members else 'empty'):
            raise ValueError('relation presence disagrees with members')
        expected=hashlib.sha256(json.dumps(self.members,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()
        if self.digest!=expected:raise ValueError('relation digest mismatch')
        return self


class IssuerAnchor(Frozen):
    company_id: str = Field(min_length=1)
    hub_event_id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    after_version: int = Field(gt=0, strict=True)
    display_name: str = Field(min_length=1, strict=True)


class ReadSet(Frozen):
    records: tuple[RecordAnchor, ...]
    relations: tuple[RelationAnchor, ...]
    transactions: tuple[str, ...]
    endpoint: str | None
    digest: str
    unknown: tuple[str, ...]
    issuer: IssuerAnchor | None


class BaselineRecipe(Frozen):
    v: Literal[1] = 1
    company_id: str
    mode: Literal['inspection','intent']
    root: InspectionRoot | None
    intent_digest: str
    endpoint: str | None
    read_digest: str
    actor_id: str
    actor_kind: str
    principal_id: str | None
    issuer_entry: str | None


class DependencyChange(Frozen):
    storage: Literal['company', 'hub'] = 'company'
    kind: RecordKind | Literal['issuer']
    record_id: str
    event_id: str
    actor_id: str | None
    actor_kind: str | None
    on_behalf_of: str | None
    interface: str
    at: str
    age_seconds: int
    version_before: int | None
    version_after: int | None
    fields: tuple[str, ...]
    unknown_fields: bool = False


class DependencyComparison(Frozen):
    matches: bool
    unknown_history: bool
    unknown_records: tuple[str, ...]
    changes: tuple[DependencyChange, ...]
    baseline: ReadSet | None
    current: ReadSet


class PageInput(Frozen):
    limit: int = Field(default=50, ge=1, le=200, strict=True)
    cursor: str | None = Field(default=None, max_length=2048)


class ChangePage(Frozen):
    items: tuple[DependencyChange, ...]
    total_count: int
    next_cursor: str | None
    unknown_history: bool
    unknown_records: tuple[str, ...]
