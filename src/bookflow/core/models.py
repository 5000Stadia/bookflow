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
