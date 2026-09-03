"""Audit commands over either database: company-scope `audit *` and hub-scope `hub audit *` (blueprint 7)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid
from bookflow.core.lazy import lazy
from bookflow.core.models import redact_paths
from bookflow.core.registry import Plan, command
from bookflow.core.session import Session, localize

sa = lazy("sqlalchemy")
h = lazy("bookflow.hub.schema")
c = lazy("bookflow.company.schema")
audit = lazy("bookflow.core.audit")
hub_audit = lazy("bookflow.hub.audit")
users = lazy("bookflow.hub.users")
cinfo = lazy("bookflow.company.info")
directives = lazy("bookflow.company.directives")

KINDS = Literal["human", "agent", "system"]
VIAS = Literal["cli", "http", "mcp", "gui", "python", "system"]


class AuditFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    since: str | None = Field(None, description="ISO timestamp or date in your zone; events at or after")
    until: str | None = Field(None, description="ISO timestamp or date in your zone; events before")
    actor: str | None = Field(None, description="Actor user id or username")
    kind: KINDS | None = Field(None, description="Actor kind")
    via: VIAS | None = Field(None, description="Interface the write came through")
    principal: str | None = Field(None, description="On-behalf-of user id")
    command: str | None = Field(None, description="Command name, e.g. 'company update'")
    record_type: str | None = Field(None, description="Record type: company_info, directive, organization, company, membership, user")
    record_id: str | None = None
    limit: int = Field(50, ge=1, le=1000)


class AuditListInput(AuditFilters):
    before: int | None = Field(None, description="Cursor: page older than this seq")


class AuditTailInput(AuditFilters):
    after: int | None = Field(None, description="Cursor: events newer than this seq; default the newest, so only new events")
    limit: int = Field(100, ge=1, le=1000)


class AuditEntryOut(BaseModel):
    id: str
    record_type: str
    record_id: str
    action: str
    version_before: int | None
    version_after: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    diff: dict[str, Any] | None = None


class AuditEventOut(BaseModel):
    id: str
    seq: int | None
    at: str
    command: str
    actor_id: str | None
    actor_name: str | None
    actor_kind: str | None
    on_behalf_of: str | None
    on_behalf_of_name: str | None
    interface: str
    client_name: str
    client_version: str
    client_host: str
    session_id: str
    request_id: str
    reason: str | None
    directive_id: str | None
    directive_code: str | None
    directive_text: str | None
    source_ref: str | None
    summary: str
    entry_count: int
    entries: list[AuditEntryOut] | None = None


class AuditListOutput(BaseModel):
    items: list[AuditEventOut]
    count: int
    next_before: int | None


class AuditTailOutput(BaseModel):
    items: list[AuditEventOut]
    count: int
    next_after: int | None


class EventSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event: str = Field(description="Audit event id")


def _scope(s: Session, hub: bool):
    """(db, events, entries, visible_ids, name_resolver, directive_text_resolver)."""
    if hub:
        vis = hub_audit.visible_record_ids(s)
        return s.hub, h.audit_events, h.audit_entries, vis, (lambda ids: users.user_names(s, ids)), (lambda did: None)
    def texts(did):
        row = s.company.conn.execute(sa.select(c.directives.c.text).where(c.directives.c.id == did)).first()
        return row[0] if row else None
    return s.company, c.audit_events, c.audit_entries, None, (lambda ids: cinfo.principal_names(s.company, ids)), texts


def _resolve_actor(s: Session, hub: bool, value: str) -> str:
    if is_ulid(value):
        return value.upper()
    if hub:
        row = users.find_user(s, username=value)
        return row["id"] if row else value
    row = s.company.conn.execute(sa.select(c.principals.c.user_id).where(c.principals.c.username == value)).first()
    return row[0] if row else value


def _apply_filters(q, events, s: Session, hub: bool, inp: AuditFilters, entries):
    from bookflow.core.dispatch import parse_when
    zone = (s.actor.timezone if s.actor else None) or s.company_tz
    if inp.since:
        q = q.where(events.c.at >= parse_when(inp.since, zone))
    if inp.until:
        q = q.where(events.c.at < parse_when(inp.until, zone, end=True))
    if inp.actor:
        q = q.where(events.c.actor_id == _resolve_actor(s, hub, inp.actor))
    if inp.kind:
        q = q.where(events.c.actor_kind == inp.kind)
    if inp.via:
        q = q.where(events.c.interface == inp.via)
    if inp.principal:
        q = q.where(events.c.on_behalf_of == inp.principal.upper())
    if inp.command:
        q = q.where(events.c.command == inp.command)
    if inp.record_type or inp.record_id:
        sub = sa.select(entries.c.event_id)
        if inp.record_type:
            sub = sub.where(entries.c.record_type == inp.record_type)
        if inp.record_id:
            sub = sub.where(entries.c.record_id == inp.record_id)
        q = q.where(events.c.id.in_(sub))
    return q


def _event_out(s: Session, hub: bool, e: dict[str, Any], names: dict[str, str], with_entries: bool) -> AuditEventOut:
    db, events, entries, visible, _, texts = _scope(s, hub)
    entry_q = sa.select(entries).where(entries.c.event_id == e["id"])
    if visible is not None:
        entry_q = entry_q.where(entries.c.record_id.in_(visible))
    rows = db.conn.execute(entry_q).mappings().all()
    out_entries = None
    if with_entries:
        out_entries = []
        for r in rows:
            before, after = audit.decode_snapshot(r["before"]), audit.decode_snapshot(r["after"])
            if before is None and r["action"] not in ("create", "baseline", "migrate", "delete") and r["version_before"] is not None:
                prev = db.conn.execute(sa.select(entries.c.after).where(entries.c.record_type == r["record_type"], entries.c.record_id == r["record_id"], entries.c.version_after == r["version_before"]).order_by(entries.c.id.desc())).first()
                before = audit.decode_snapshot(prev[0]) if prev else None
            before, after = redact_paths(before, s.is_hub_admin), redact_paths(after, s.is_hub_admin)
            diff = None
            if before is not None and after is not None:
                diff = {k: {"before": before.get(k), "after": after.get(k)} for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)}
                if not s.is_hub_admin:
                    diff = {k: v for k, v in diff.items() if not (k == "path" or k.endswith("_path"))}
            out_entries.append(AuditEntryOut(id=r["id"], record_type=r["record_type"], record_id=r["record_id"], action=r["action"], version_before=r["version_before"], version_after=r["version_after"], before=before, after=after, diff=diff))
    fields = {k: e[k] for k in AuditEventOut.model_fields if k in e and k not in ("at", "directive_code", "directive_text")}
    if not s.is_hub_admin:
        import re as _re
        fields["summary"] = _re.sub(r"(organizations|trash)/.*$", "<path>", fields["summary"])
    return AuditEventOut(**fields, at=localize(s, e["at"]), actor_name=names.get(e["actor_id"]), on_behalf_of_name=names.get(e["on_behalf_of"]) if e.get("on_behalf_of") else None,
                         directive_code=e.get("directive_code"), directive_text=texts(e["directive_id"]) if e.get("directive_id") else None,
                         entry_count=len(rows), entries=out_entries)


def _list(s: Session, hub: bool, inp: AuditListInput) -> AuditListOutput:
    db, events, entries, visible, resolver, _ = _scope(s, hub)
    q = sa.select(events).order_by(events.c.seq.desc()).limit(inp.limit + 1)
    if hub:
        q = q.where(hub_audit.visible_event_ids_filter(s))
    q = _apply_filters(q, events, s, hub, inp, entries)
    if inp.before is not None:
        q = q.where(events.c.seq < inp.before)
    rows = [dict(r) for r in db.conn.execute(q).mappings().all()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    ids = {r["actor_id"] for r in rows if r["actor_id"]} | {r["on_behalf_of"] for r in rows if r.get("on_behalf_of")}
    names = resolver(ids)
    items = [_event_out(s, hub, r, names, False) for r in rows]
    return AuditListOutput(items=items, count=len(items), next_before=rows[-1]["seq"] if more and rows else None)


def _tail(s: Session, hub: bool, inp: AuditTailInput) -> AuditTailOutput:
    db, events, entries, visible, resolver, _ = _scope(s, hub)
    after = inp.after
    if after is None:
        after = db.conn.execute(sa.select(sa.func.max(events.c.seq))).scalar() or 0
    q = sa.select(events).where(events.c.seq > after).order_by(events.c.seq.asc()).limit(inp.limit)
    if hub:
        q = q.where(hub_audit.visible_event_ids_filter(s))
    q = _apply_filters(q, events, s, hub, inp, entries)
    rows = [dict(r) for r in db.conn.execute(q).mappings().all()]
    ids = {r["actor_id"] for r in rows if r["actor_id"]} | {r["on_behalf_of"] for r in rows if r.get("on_behalf_of")}
    names = resolver(ids)
    items = [_event_out(s, hub, r, names, False) for r in rows]
    return AuditTailOutput(items=items, count=len(items), next_after=rows[-1]["seq"] if rows else None)


def _show(s: Session, hub: bool, inp: EventSelector) -> AuditEventOut:
    db, events, entries, visible, resolver, _ = _scope(s, hub)
    q = sa.select(events).where(events.c.id == inp.event.upper())
    if hub:
        q = q.where(hub_audit.visible_event_ids_filter(s))
    row = db.conn.execute(q).mappings().first()
    if row is None:
        raise BookflowError("E_EVENT_NOT_FOUND")
    e = dict(row)
    ids = ({e["actor_id"]} if e["actor_id"] else set()) | ({e["on_behalf_of"]} if e.get("on_behalf_of") else set())
    return _event_out(s, hub, e, resolver(ids), True)


for hub, prefix in ((True, "hub audit"), (False, "audit")):
    scope = "hub" if hub else "company"
    role = None if hub else "member"

    def _mk(hub=hub, prefix=prefix, scope=scope, role=role):
        @command(f"{prefix} list", scope=scope, description=("List hub audit events the acting user may see, newest first." if hub else "List this company's audit events, newest first."),
                 input_model=AuditListInput, output_model=AuditListOutput, required_role=role, error_codes=["E_VALIDATION"])
        def plan_list(inp: AuditListInput, ctx: Context, s: Session) -> Plan:
            return Plan(preview=_list(s, hub, inp))

        @command(f"{prefix} show", scope=scope, description=("Show one hub audit event with its entries and field diffs." if hub else "Show one of this company's audit events with its entries and field diffs."),
                 input_model=EventSelector, output_model=AuditEventOut, required_role=role, positional=["event"], error_codes=["E_EVENT_NOT_FOUND"])
        def plan_show(inp: EventSelector, ctx: Context, s: Session) -> Plan:
            return Plan(preview=_show(s, hub, inp))

        @command(f"{prefix} tail", scope=scope, description=("Hub audit events newer than a cursor, oldest first; the event feed." if hub else "This company's audit events newer than a cursor, oldest first; the event feed."),
                 input_model=AuditTailInput, output_model=AuditTailOutput, required_role=role, streams=True, error_codes=["E_VALIDATION"])
        def plan_tail(inp: AuditTailInput, ctx: Context, s: Session) -> Plan:
            return Plan(preview=_tail(s, hub, inp))

    _mk()
