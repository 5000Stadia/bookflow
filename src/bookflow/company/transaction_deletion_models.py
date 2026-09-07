"""Private, non-persistable Delete evidence and proposed lifecycle values."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StrictBytes, field_validator, field_serializer, ValidationInfo
from bookflow.core.ids import is_ulid
from bookflow.company.payment_models import EffectProvenance

Family = Literal['journal_entry', 'invoice', 'sales_receipt', 'payment']
Scalar = StrictInt | StrictStr | StrictBytes | None

class Frozen(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, ser_json_bytes='base64', val_json_bytes='base64')

class DeleteIntent(Frozen):
    family: Family
    transaction_id: str
    expected_version: Annotated[int, Field(ge=1, le=9223372036854775807)]

    @field_validator('transaction_id')
    @classmethod
    def identity(cls, value):
        if not is_ulid(value):
            raise ValueError('exact transaction identity required')
        return value

class StoredRow(Frozen):
    """Closed by the facts owner's table/column/type inventory, never caller SQL."""
    table: str
    cells: tuple[tuple[str, Scalar], ...]

    @field_serializer('cells', when_used='json')
    def tagged_cells(self, cells):
        # JSON strings and blobs are distinct evidence types, even if their
        # printable/base64 contents coincide. No untagged bytes union roundtrip.
        import base64
        return tuple((key, {'$bytes': base64.b64encode(value).decode('ascii')}
            if isinstance(value, bytes) else value) for key, value in cells)

    @field_validator('cells', mode='before')
    @classmethod
    def restore_cells(cls, cells, info: ValidationInfo):
        if info.mode != 'json':
            return cells
        import base64
        return tuple((key, base64.b64decode(value['$bytes'], validate=True)
            if isinstance(value, dict) and set(value) == {'$bytes'} else value)
            for key, value in cells)

    def values(self):
        return dict(self.cells)

class DeleteFacts(Frozen):
    company_id: str
    transaction_id: str
    family: Family
    header: StoredRow
    revision: StoredRow
    business_batch: StoredRow
    prior_void_batch: StoredRow | None
    rows: tuple[StoredRow, ...]
    participants: tuple[str, ...]
    work_allocation_ids: tuple[str, ...]
    current_work_allocation_ids: tuple[str, ...]
    required_resources: tuple[tuple[str, str], ...]

class Tombstone(Frozen):
    transaction_id: str
    family: Family
    before_version: int
    after_version: int
    current_revision_id: str
    number: str
    status: Literal['deleted'] = 'deleted'
    deleted_from_status: Literal['posted', 'voided']
    deleted_at: str
    deleted_by: str
    delete_reason: str
    delete_audit_event_id: str
    delete_posting_batch_id: str | None
    retained_void_batch_id: str | None

class WorkRelease(Frozen):
    allocation_id: str
    root_document_id: str
    root_line_id: str
    event_id: str
    newly_released: bool

class PreparedDelete(Frozen):
    intent: DeleteIntent
    provenance: EffectProvenance
    actor_id: str
    actor_kind: str
    principal_id: str | None
    interface: str
    facts: DeleteFacts
    tombstone: Tombstone
    inverse_rows: tuple[StoredRow, ...]
    work_releases: tuple[WorkRelease, ...]
