"""Shared command inputs and translation for the opaque history cutover."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from bookflow.core.history_request import HistoryRequest
from bookflow.hub.audit_projection import HistorySelection


def bookmark_input(value):
    from bookflow.core.history_cursors import MAX_LENGTH, invalid
    if value is not None and (type(value) is not str or not 1 <= len(value) <= MAX_LENGTH or value.strip() != value):
        invalid()
    return value


class AuditFilters(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    since: str | None = Field(None, description='ISO timestamp or date in your zone; events at or after')
    until: str | None = Field(None, description='ISO timestamp or date in your zone; events before')
    actor: str | None = Field(None, description='Actor user id or username')
    kind: Literal['human','agent','system'] | None = None
    via: Literal['cli','http','mcp','gui','python','system'] | None = None
    principal: str | None = Field(None, description='On-behalf-of user id')
    command: str | None = None
    record_type: str | None = None
    record_id: str | None = None
    limit: int = Field(50, ge=1, le=1000)


class AuditListInput(AuditFilters):
    before: str | None = Field(None, description='Opaque next_before bookmark from this query; omit to restart')
    _bookmark = field_validator('before', mode='before')(bookmark_input)


class AuditTailInput(AuditFilters):
    after: str | None = Field(None, description='Opaque next_after or event resume_after bookmark; omit to watch new events')
    limit: int = Field(100, ge=1, le=1000)
    scan_limit: int | None = Field(None, ge=1, le=1000, description='Bound visible candidates examined before business filters')
    _bookmark = field_validator('after', mode='before')(bookmark_input)


class EventSelector(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    event: str = Field(description='Audit event id')


class ActivityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    record_type: str = Field(min_length=1, max_length=64)
    record_id: str = Field(min_length=1, max_length=26)
    since: str | None = Field(None, max_length=64)
    until: str | None = Field(None, max_length=64)
    kinds: list[Literal['audit','note','attachment']] | None = Field(None, max_length=3)
    limit: int = Field(50, strict=True, ge=1, le=200)
    cursor: str | None = Field(None, description='Opaque next_cursor bookmark from this activity query; omit to restart')
    _bookmark = field_validator('cursor', mode='before')(bookmark_input)


INPUTS = {'list': AuditListInput, 'tail': AuditTailInput, 'show': EventSelector, 'activity': ActivityInput}


def command_request(mode, validated, *, company=None):
    """Translate registered validated fields; company is the resolved scope ID."""
    if mode not in INPUTS or type(validated) is not INPUTS[mode]:
        raise TypeError('matching validated history command input required')
    values = validated.model_dump(exclude_none=True)
    cursor = values.pop({'list':'before','tail':'after','activity':'cursor'}.get(mode,''), None)
    if 'kind' in values:values['actor_kind'] = values.pop('kind')
    if 'via' in values:values['interface'] = values.pop('via')
    if 'kinds' in values:values['kinds'] = tuple(values['kinds'])
    return HistoryRequest.capture(HistorySelection(mode=mode, company=company, **values), cursor)
