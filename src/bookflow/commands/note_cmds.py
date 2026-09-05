"""Versioned company-record comments using the ordinary command/audit pipeline."""

from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bookflow.core.context import Context
from bookflow.core.lazy import LazyModule
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, command

sa = LazyModule("sqlalchemy")
c = LazyModule("bookflow.company.schema")
records = LazyModule("bookflow.company.records")
info = LazyModule("bookflow.company.info")
clock = LazyModule("bookflow.core.clock")


class NoteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    record_type: str = Field(min_length=1, max_length=64, description="Canonical annotation type, e.g. customer, account, or company_info.")
    record_id: str = Field(min_length=1, max_length=26, description="Stable id of the target inside the selected company.")


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    body: str = Field(min_length=1, max_length=65536, description="Nonblank comment text, preserved as entered; at most 65,536 UTF-8 bytes.")

    @field_validator("body")
    @classmethod
    def bounded_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain non-whitespace text")
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("must be at most 65,536 UTF-8 bytes")
        return value


class NoteAddInput(NoteTarget, NoteBody):
    pass


class NoteSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    note: str = Field(min_length=1, max_length=26, description="Stable note id in the selected company.")


class NoteEditInput(NoteSelector, NoteBody):
    expected_version: int = Field(strict=True, ge=1, description="Positive version returned by note show; stale edits are rejected.")


class NoteListInput(NoteTarget):
    limit: int = Field(50, strict=True, ge=1, le=200, description="Maximum notes returned, from 1 through 200.")
    cursor: str | None = Field(None, max_length=2048, description="Continuation from the preceding page; omit to refresh newest notes.")


class NoteOut(BaseModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str
    record_type: str
    record_id: str
    body: str
    author_id: str
    author_name: str | None
    interface: str
    at: str
    edited_at: str | None
    kind: Literal["comment", "system"]
    updated_by_name: str | None
    access: str
    role: str | None


class NoteWriteOutput(WriteOutput):
    note: NoteOut
    idempotent_replay: bool = False


class NotePage(BaseModel):
    items: list[NoteOut]
    count: int
    has_more: bool
    next_cursor: str | None


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: Literal[1] = 1
    company: str
    record_type: str
    record_id: str
    permissions: str
    before: str


def _output_rows(s, rows: list[dict]) -> list[NoteOut]:
    from bookflow.core.session import localize
    from bookflow.hub.access import company_role
    names = info.principal_names(s.company, {r[key] for r in rows for key in ("author_id", "updated_by")})
    names.setdefault(s.actor.id, s.actor.display_name)
    access, role = company_role(s, s.company_row["id"], s.company_row["organization_id"])
    return [NoteOut(**{
        key: localize(s, value) if key in ("created_at", "updated_at", "at", "edited_at") else value
        for key, value in row.items()
    }, author_name=names.get(row["author_id"]), updated_by_name=names.get(row["updated_by"]),
        access=access, role=role) for row in rows]


def _get(s, note: str) -> dict:
    key = records.resolve(s, "note", note)
    return dict(s.company.conn.execute(sa.select(c.notes).where(c.notes.c.id == key)).mappings().one())


note_add = command("note add", scope="company", description="Add an attributed comment to a persistent company record without changing its version.",
    input_model=NoteAddInput, output_model=NoteWriteOutput, writes={"company"}, required_role="standard",
    accepts_idempotency_key=True, positional=["record_type", "record_id"], error_codes=["E_RECORD_NOT_FOUND"])


@note_add
def plan_add(inp: NoteAddInput, ctx: Context, s) -> Plan:
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    key = records.resolve(s, inp.record_type, inp.record_id)
    at = clock.now_iso()
    row = dict(id=new_id(), **common(s.actor.id, ctx.interface.value), record_type=inp.record_type,
        record_id=key, body=inp.body, author_id=s.actor.id, interface=ctx.interface.value,
        at=at, edited_at=None, kind="comment")
    return Plan(NoteWriteOutput(note=_output_rows(s, [row])[0]), {"row": row})


@note_add.applier
def apply_add(plan: Plan, ctx: Context, s) -> Applied:
    row = plan.data["row"]
    s.company.conn.execute(c.notes.insert().values(**row))
    return Applied(plan.preview, [Touched("note", row["id"], "create", None, 1, row, db="company")],
        f"added note to {row['record_type']} {row['record_id']}")


note_show = command("note show", scope="company", description="Show a note's current text, version and original author.",
    input_model=NoteSelector, output_model=NoteOut, required_role="member", positional=["note"], error_codes=["E_RECORD_NOT_FOUND"])


@note_show
def plan_show(inp: NoteSelector, ctx: Context, s) -> Plan:
    return Plan(_output_rows(s, [_get(s, inp.note)])[0])


note_edit = command("note edit", scope="company", description="Correct a comment with an expected version; previous text remains in audit history.",
    input_model=NoteEditInput, output_model=NoteWriteOutput, writes={"company"}, required_role="standard",
    positional=["note"], version_source=("note show", "note", "version"), error_codes=["E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT"])


@note_edit
def plan_edit(inp: NoteEditInput, ctx: Context, s) -> Plan:
    from bookflow.core.audit import decode_snapshot
    from bookflow.core.versioning import check_update, current_writer, history_from_entries
    row = _get(s, inp.note)
    writer = current_writer(s.company, "note", row["id"], row)
    if writer:
        names = info.principal_names(s.company, {v for v in (writer.updated_by, writer.on_behalf_of) if v})
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)
    check_update(current_version=row["version"], current_updated_at=row["updated_at"], current_writer=writer,
        changes={"body"}, expected_version=inp.expected_version,
        history_since=lambda v: history_from_entries(s.company, "note", row["id"], v, decode_snapshot),
        actor_id=s.actor.id, window_seconds=0)
    changed = row["body"] != inp.body
    new = dict(row)
    if changed:
        new.update(body=inp.body, version=row["version"] + 1, edited_at=clock.now_iso(),
            updated_at=clock.now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(NoteWriteOutput(note=_output_rows(s, [new])[0]), {"row": new, "changed": changed})


@note_edit.applier
def apply_edit(plan: Plan, ctx: Context, s) -> Applied:
    row = plan.data["row"]
    if not plan.data["changed"]:
        return Applied(plan.preview, [], "no change")
    s.company.conn.execute(c.notes.update().where(c.notes.c.id == row["id"]).values(**row))
    return Applied(plan.preview, [Touched("note", row["id"], "update", row["version"] - 1,
        row["version"], row, db="company")], f"edited note {row['id']}")


note_list = command("note list", scope="company", description="Page current comments for a record, newest id first; refresh to see newly added notes.",
    input_model=NoteListInput, output_model=NotePage, required_role="member", positional=["record_type", "record_id"],
    error_codes=["E_RECORD_NOT_FOUND"])


@note_list
def plan_list(inp: NoteListInput, ctx: Context, s) -> Plan:
    from bookflow.company.query import _invalid_cursor, permission_fingerprint
    from bookflow.core.ids import is_ulid
    key = records.resolve(s, inp.record_type, inp.record_id)
    scope = dict(company=s.company_row["id"], record_type=inp.record_type, record_id=key,
        permissions=permission_fingerprint(s, ctx.on_behalf_of))
    query = sa.select(c.notes).where(c.notes.c.record_type == inp.record_type, c.notes.c.record_id == key)
    if inp.cursor:
        try:
            encoded = inp.cursor.encode("ascii")
            raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            previous = _Cursor.model_validate_json(raw)
        except (ValueError, UnicodeError, ValidationError):
            raise _invalid_cursor() from None
        if previous.model_dump(exclude={"v", "before"}) != scope or not is_ulid(previous.before):
            raise _invalid_cursor()
        query = query.where(c.notes.c.id < previous.before)
    rows, body_bytes, more = [], 0, False
    # A page is bounded by bytes as well as count, including through the local
    # forwarding envelope. Fetch incrementally rather than materializing large bodies.
    result = s.company.conn.execute(query.order_by(c.notes.c.id.desc()).limit(inp.limit + 1))
    try:
        for raw_row in result.mappings():
            row = dict(raw_row)
            size = len(row["body"].encode("utf-8"))
            if len(rows) == inp.limit or body_bytes + size > 262144:
                more = True
                break
            rows.append(row)
            body_bytes += size
    finally:
        result.close()
    cursor = None
    if more:
        cursor = base64.urlsafe_b64encode(_Cursor(**scope, before=rows[-1]["id"]).model_dump_json().encode()).decode().rstrip("=")
    return Plan(NotePage(items=_output_rows(s, rows), count=len(rows), has_more=more, next_cursor=cursor))
