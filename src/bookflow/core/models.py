"""Shared output shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CommonFields(BaseModel):
    """Blueprint 6.1: carried by every record output."""

    id: str
    version: int
    created_at: datetime
    created_by: str
    created_via: str
    updated_at: datetime
    updated_by: str
    updated_via: str


class ListOutput(BaseModel, Generic[T]):
    items: list[T]
    count: int


class WriteOutput(BaseModel):
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


PATH_FIELD_NAMES = ("path", "trashed_path", "data_root")


def redact_paths(obj: Any, allowed: bool) -> Any:
    """Null every path-named field for non-hub-admins (blueprint 5.4); recursive."""
    if allowed:
        return obj
    if isinstance(obj, dict):
        return {k: (None if (k in PATH_FIELD_NAMES or k.endswith("_path")) else redact_paths(v, allowed)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_paths(v, allowed) for v in obj]
    return obj


# Column preferences every adapter shares when rendering a list (blueprint 2: no adapter reads another adapter's tables).
COMMON_FIELDS = {"id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"}
PREFERRED_COLUMNS = ["id", "seq", "display_name", "organization_name", "legal_name", "home_currency", "access", "role", "at", "command", "actor_name", "interface", "reason", "summary", "company_id", "organization_id", "entry_count", "code", "text", "active"]
HIDDEN_COLUMNS = COMMON_FIELDS | {"path", "schema_revision", "entries", "session_id", "request_id", "client_version", "client_host", "client_name", "actor_id", "actor_kind", "on_behalf_of", "directive_id", "source_ref", "registered_by_name", "is_demo", "entry_count", "editing_by"}


def list_columns(items: list[dict]) -> list[str] | None:
    if not items:
        return None
    hidden = HIDDEN_COLUMNS - ({"id"} if "seq" in items[0] else set())
    keys = [k for k in items[0] if k not in hidden and not isinstance(items[0][k], (dict, list))]
    return [k for k in PREFERRED_COLUMNS if k in keys] + [k for k in keys if k not in PREFERRED_COLUMNS]
