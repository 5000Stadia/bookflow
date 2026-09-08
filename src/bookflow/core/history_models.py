"""Shared projected history response schemas for the coordinated command cutover."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict
from bookflow.hub.audit_projection import ProjectedActivity


class Response(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class EntryIdentity(Response):
    company: str | None
    kind: str
    id: str


class HistoryEntry(Response):
    id: str
    identity: EntryIdentity
    action: str
    # Payloads have already passed the projection owner's closed typed decoders.
    # This envelope carries only those disclosed fields, never stored raw JSON.
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    changed_fields: list[str]
    version_before: int | None
    version_after: int | None


class HistoryDirective(Response):
    id: str
    code: str
    text: str


class HistoryExplanation(Response):
    reason: str | None
    directive_status: Literal['not_cited','available','unavailable']
    directive: HistoryDirective | None


class HistoryEvent(Response):
    id: str
    at: str
    command: str | None
    summary: str
    actor_id: str | None
    actor_name: str | None
    actor_kind: str | None
    principal_id: str | None
    principal_name: str | None
    interface: str
    entry_count: int
    entries: None
    explanation: HistoryExplanation | None = None


class HistoryTailEvent(HistoryEvent):
    resume_after: str


class HistoryShow(HistoryEvent):
    projection_version: Literal[2]
    entries: list[HistoryEntry]


class HistoryList(Response):
    projection_version: Literal[2]
    items: list[HistoryEvent]
    count: int
    next_before: str | None


class HistoryTail(Response):
    projection_version: Literal[2]
    items: list[HistoryTailEvent]
    count: int
    next_after: str
    scanned_count: int
    scan_more: bool


class HistoryActivity(Response):
    projection_version: Literal[2]
    items: list[ProjectedActivity]
    count: int
    has_more: bool
    next_cursor: str | None


OUTPUTS = {'list': HistoryList, 'show': HistoryShow, 'tail': HistoryTail, 'activity': HistoryActivity}


def validate_response(mode, value):
    return OUTPUTS[mode].model_validate(value).model_dump(mode='json')
