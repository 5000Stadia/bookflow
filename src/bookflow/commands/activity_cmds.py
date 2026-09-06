"""Bounded chronological company-record history from immutable audit entries."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.commands.note_cmds import NoteTarget
from bookflow.core.context import Context
from bookflow.core.lazy import LazyModule
from bookflow.core.registry import Plan, command

sa = LazyModule("sqlalchemy")
c = LazyModule("bookflow.company.schema")
audit = LazyModule("bookflow.core.audit")

ActivityKind = Literal["audit", "note", "attachment"]
PAGE_BYTES = 262144


class ActivityInput(NoteTarget):
    since: str | None = Field(None, max_length=64, description="ISO timestamp or date in the viewer's zone; inclusive lower bound.")
    until: str | None = Field(None, max_length=64, description="Exclusive timestamp upper bound, or inclusive calendar date in the viewer's zone.")
    kinds: list[ActivityKind] | None = Field(None, max_length=3)
    limit: int = Field(50, strict=True, ge=1, le=200)
    cursor: str | None = Field(None, max_length=2048)


class ActivityItem(BaseModel):
    kind: ActivityKind
    at: str
    event_id: str
    entry_id: str
    seq: int
    record_type: str
    record_id: str
    action: str
    command: str
    summary: str
    actor_id: str | None
    actor_name: str | None
    on_behalf_of: str | None
    on_behalf_of_name: str | None
    interface: str
    version_before: int | None
    version_after: int | None
    body: str | None = None
    caption: str | None = None
    attachment_id: str | None = None
    active: bool | None = None
    text_truncated: bool = Field(False, description="Text shortened to fit the response ceiling; event_id and entry_id identify the complete immutable audit snapshot.")


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    count: int
    has_more: bool
    next_cursor: str | None
    high_water: int


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: Literal[1] = 1
    scope: str
    high_water: int = Field(ge=0, le=9223372036854775807)
    at: str = Field(max_length=32)
    event_id: str = Field(min_length=26, max_length=26)
    entry_id: str = Field(min_length=26, max_length=26)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _encode(cursor: _Cursor) -> str:
    data = cursor.model_dump()
    return base64.urlsafe_b64encode(json.dumps([data, _digest(data)], separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(value: str, scope: str) -> _Cursor:
    from bookflow.company.query import _invalid_cursor
    from bookflow.core.ids import is_ulid
    try:
        encoded = value.encode("ascii")
        raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        data, checksum = json.loads(raw)
        previous = _Cursor.model_validate(data)
        if checksum != _digest(data) or previous.scope != scope:
            raise ValueError()
        if not is_ulid(previous.event_id) or not is_ulid(previous.entry_id):
            raise ValueError()
        return previous
    except (ValueError, TypeError, UnicodeError):
        raise _invalid_cursor() from None


def _candidates(inp, key):
    """UNION deduplicates direct annotations; immutable target columns select history."""
    entries = c.audit_entries
    branches = []
    kinds = set(inp.kinds) if inp.kinds is not None else {"audit", "note", "attachment"}
    direct_kind = "note" if inp.record_type == "note" else "attachment" if inp.record_type in {"attachment", "attachment_link"} else "audit"
    if direct_kind in kinds:
        branches.append(sa.select(entries.c.id).where(entries.c.record_type == inp.record_type, entries.c.record_id == key))
    for kind, record_type, table in (("note", "note", c.notes), ("attachment", "attachment_link", c.attachment_links)):
        if kind in kinds:
            branches.append(sa.select(entries.c.id).select_from(table.join(entries,
                sa.and_(entries.c.record_type == record_type, entries.c.record_id == table.c.id))).where(
                    table.c.record_type == inp.record_type, table.c.record_id == key))
    if not branches:
        return sa.select(entries.c.id).where(sa.false())
    return sa.union(*branches) if len(branches) > 1 else branches[0]


def _item(s, row, names):
    from bookflow.core.session import localize
    kind = "note" if row["record_type"] == "note" else "attachment" if row["record_type"] in {"attachment", "attachment_link"} else "audit"
    fields = {key: row[key] for key in ("event_id", "entry_id", "seq", "record_type", "record_id", "action", "command", "summary", "actor_id", "on_behalf_of", "interface", "version_before", "version_after")}
    item = ActivityItem(**fields, kind=kind, at=localize(s, row["at"]), actor_name=names.get(row["actor_id"]), on_behalf_of_name=names.get(row["on_behalf_of"]))
    if kind != "audit":
        snapshot = audit.decode_snapshot(row["after"] if row["after"] is not None else row["before"]) or {}
        if kind == "note":
            item.body = snapshot.get("body")
        else:
            item.caption = snapshot.get("caption")
            item.attachment_id = snapshot.get("attachment_id")
            item.active = snapshot.get("active")
    # ASCII JSON escaping can expand a legal 64-KiB note past the entire page ceiling.
    # Preserve an explicit historical excerpt and the immutable audit reference.
    while len(json.dumps(item.model_dump()).encode()) > PAGE_BYTES - 8192:
        key = "body" if item.body is not None else "caption"
        value = getattr(item, key)
        if not value:
            break
        setattr(item, key, value[:len(value) // 2])
        item.text_truncated = True
    return item


activity = command("activity", scope="company", required_role="member", capability="activity",
    description="Page immutable record, note and file-link actions chronologically, with a fixed audit cutoff.",
    input_model=ActivityInput, output_model=ActivityPage, positional=["record_type", "record_id"],
    error_codes=["E_RECORD_NOT_FOUND", "E_VALIDATION"])


@activity
def plan_activity(inp: ActivityInput, ctx: Context, s) -> Plan:
    from bookflow.company import info, records
    from bookflow.company.query import _invalid_cursor, permission_fingerprint
    from bookflow.core.dispatch import parse_when
    from bookflow.core.errors import BookflowError

    key = records.resolve(s, inp.record_type, inp.record_id)
    zone = s.actor.timezone or s.company_tz
    since = parse_when(inp.since, zone) if inp.since is not None else None
    until = parse_when(inp.until, zone, end=True) if inp.until is not None else None
    if since is not None and until is not None and since >= until:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "until", "problem": "must follow since"}]})
    scope = _digest(dict(company=s.company_row["id"], actor=s.actor.id,
        permissions=permission_fingerprint(s, ctx.on_behalf_of), record_type=inp.record_type,
        record_id=key, since=since, until=until, kinds=sorted(set(inp.kinds)) if inp.kinds is not None else ["attachment", "audit", "note"], limit=inp.limit))
    previous = _decode(inp.cursor, scope) if inp.cursor is not None else None
    events, entries = c.audit_events, c.audit_entries
    newest = s.company.conn.execute(sa.select(sa.func.max(events.c.seq))).scalar() or 0
    high_water = previous.high_water if previous else newest
    if high_water > newest:
        raise _invalid_cursor()
    q = sa.select(entries.c.id.label("entry_id"), entries.c.event_id, entries.c.record_type,
        entries.c.record_id, entries.c.action, entries.c.version_before, entries.c.version_after,
        entries.c.before, entries.c.after, events.c.seq, events.c.at, events.c.command,
        events.c.summary, events.c.actor_id, events.c.on_behalf_of, events.c.interface).select_from(
            entries.join(events, entries.c.event_id == events.c.id)).where(
                entries.c.id.in_(_candidates(inp, key)), events.c.seq <= high_water)
    if since is not None:
        q = q.where(events.c.at >= since)
    from bookflow.company.payment_authority import denied_events
    denied = denied_events(s)
    if denied:
        q = q.where(events.c.id.not_in(denied))
    if until is not None:
        q = q.where(events.c.at < until)
    if previous:
        q = q.where(sa.tuple_(events.c.at, events.c.id, entries.c.id) > sa.tuple_(previous.at, previous.event_id, previous.entry_id))
    q = q.order_by(events.c.at, events.c.id, entries.c.id).limit(inp.limit + 1)
    # Materialize only lightweight metadata for a bounded SQL page; blobs stay encoded
    # until an item is considered. Actor labels use a single set lookup.
    rows = list(s.company.conn.execute(q).mappings())
    names = info.principal_names(s.company, {r[k] for r in rows for k in ("actor_id", "on_behalf_of") if r[k]})
    items, last = [], None
    size = 0
    for row in rows[:inp.limit]:
        item = _item(s, row, names)
        item_size = len(json.dumps(item.model_dump()).encode()) + 2
        if size + item_size > PAGE_BYTES - 4096:
            break
        items.append(item)
        size += item_size
        last = row
    more = len(rows) > len(items)
    cursor = _encode(_Cursor(scope=scope, high_water=high_water, at=last["at"], event_id=last["event_id"], entry_id=last["entry_id"])) if more and last else None
    return Plan(ActivityPage(items=items, count=len(items), has_more=more, next_cursor=cursor, high_water=high_water))
