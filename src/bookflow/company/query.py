"""Bounded read contracts and snapshot-checked opaque continuation cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import sqlalchemy as sa

from bookflow.core.errors import BookflowError


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, defer_build=True)
    query: str | None = None
    include_inactive: bool = False
    filter: list[str] = Field(default_factory=list)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)
    projection: Literal["summary", "reference"] = "summary"


class ReferenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, defer_build=True)
    id: str
    version: int
    label: str
    active: bool


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, defer_build=True)
    v: Literal[1] = 1
    company: str
    noun: str
    fingerprint: str
    permissions: str
    sequence: int = Field(ge=0)
    offset: int = Field(ge=1, le=9223372036854775807)


def _invalid_cursor() -> BookflowError:
    return BookflowError("E_VALIDATION", details={"fields": [{
        "field": "cursor", "problem": "invalid cursor or a different query contract; restart without a cursor",
    }]})


def fingerprint(inp: QueryInput) -> str:
    contract = inp.model_dump(exclude={"cursor"})
    # Normalization matches the shared containment search; filters remain ordered.
    from bookflow.company.list_service import normalize_lookup_key
    contract["query"] = normalize_lookup_key(inp.query) if inp.query and inp.query.strip() else None
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def permission_fingerprint(session, principal_id: str | None) -> str:
    """Current authorization/redaction inputs; expand when capability grants land."""
    from bookflow.hub.access import company_role
    access, role = company_role(session, session.company_row["id"], session.company_row["organization_id"])
    values = [session.actor.id, principal_id, session.is_hub_admin, access, role]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def page_state(session, noun: str, inp: QueryInput, principal_id: str | None = None) -> Cursor:
    from bookflow.company import schema
    db, company_id = session.company, str(session.company_row["id"])
    sequence = int(db.conn.execute(sa.select(sa.func.coalesce(sa.func.max(schema.audit_events.c.seq), 0))).scalar_one())
    current = Cursor(company=company_id, noun=noun, fingerprint=fingerprint(inp),
        permissions=permission_fingerprint(session, principal_id), sequence=sequence, offset=1)
    if inp.cursor is None:
        return current.model_copy(update={"offset": 0})
    try:
        encoded = inp.cursor.encode("ascii")
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        previous = Cursor.model_validate_json(raw)
    except (ValueError, UnicodeError, ValidationError):
        raise _invalid_cursor() from None
    if (previous.company, previous.noun, previous.fingerprint) != (current.company, current.noun, current.fingerprint):
        raise _invalid_cursor()
    if previous.permissions != current.permissions:
        raise _invalid_cursor()
    if previous.sequence != sequence:
        raise BookflowError("E_QUERY_STALE", details={"restart": "Repeat the query without cursor.", "previous_sequence": previous.sequence, "current_sequence": sequence})
    return previous


def continuation(state: Cursor, count: int, more: bool) -> str | None:
    if not more:
        return None
    raw = state.model_copy(update={"offset": state.offset + count}).model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
