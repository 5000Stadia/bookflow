"""Private complete deposit history contracts; no public command registration."""
from typing import Literal, Annotated
from pydantic import ConfigDict, Field, model_validator
from bookflow.company.sales_models import StrictModel
from bookflow.company.deposit_models import PostInput
from bookflow.company.deposit_lifecycle_models import UpdateInput, VoidInput


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


DepositRequest = Annotated[PostRequest | UpdateRequest | VoidRequest, Field(discriminator='command')]


class InspectionRoot(Frozen):
    kind: Literal['deposit','payment','sales_receipt']
    id: str = Field(min_length=1)


class RecordAnchor(Frozen):
    kind: str
    id: str
    version: int | None
    event_id: str | None
    semantic_json: str
    unknown: bool = False


class RelationAnchor(Frozen):
    kind: str
    owner_id: str
    members: tuple[str, ...]


class ReadSet(Frozen):
    records: tuple[RecordAnchor, ...]
    relations: tuple[RelationAnchor, ...]
    transactions: tuple[str, ...]
    endpoint: str | None
    digest: str
    unknown: tuple[str, ...]


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


class DependencyChange(Frozen):
    kind: str
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
