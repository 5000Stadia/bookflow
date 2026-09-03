"""Hub audit writer and reader (blueprint 7)."""

from __future__ import annotations

import json
import zlib
from typing import Any

import sqlalchemy as sa

from bookflow.core.context import Context
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import Session, now_iso
from bookflow.hub import schema as h

SECRET_FIELDS = {"password_hash", "token_hash"}
RAW, ZIP = b"\x00", b"\x01"


def encode_snapshot(data: dict[str, Any] | None) -> bytes | None:
    if data is None:
        return None
    clean = {k: v for k, v in data.items() if k not in SECRET_FIELDS}
    raw = json.dumps(clean, sort_keys=True, default=str).encode("utf-8")
    if len(raw) > 512:
        return ZIP + zlib.compress(raw)
    return RAW + raw


def decode_snapshot(blob: bytes | None) -> dict[str, Any] | None:
    if blob is None:
        return None
    if blob[:1] == ZIP:
        return json.loads(zlib.decompress(blob[1:]))
    return json.loads(blob[1:])


def write_event(s: Session, ctx: Context, command: str, summary: str, touched: list[Touched], actor_id: str | None = None, actor_kind: str | None = None) -> str:
    assert s.hub is not None
    event_id = new_id()
    s.hub.conn.execute(h.audit_events.insert().values(
        id=event_id, at=now_iso(), command=command,
        actor_id=actor_id if actor_id is not None else (s.actor.id if s.actor else None),
        actor_kind=actor_kind if actor_kind is not None else (s.actor.kind if s.actor else None),
        on_behalf_of=ctx.on_behalf_of, interface=ctx.interface.value, client_name=ctx.client_name,
        client_version=ctx.client_version, client_host=ctx.client_host, session_id=ctx.session_id,
        request_id=ctx.request_id, idempotency_key=ctx.idempotency_key, reason=ctx.reason,
        directive_id=ctx.directive_id, source_ref=ctx.source_ref, summary=summary[:512],
    ))
    for t in touched:
        s.hub.conn.execute(h.audit_entries.insert().values(
            id=new_id(), event_id=event_id, record_type=t.record_type, record_id=t.record_id, action=t.action,
            version_before=t.version_before, version_after=t.version_after,
            after=encode_snapshot(t.after), before=encode_snapshot(t.before),
        ))
    return event_id


def visible_event_ids_filter(s: Session):
    """Criterion over audit_events for what the actor may see."""
    if s.is_hub_admin:
        return sa.true()
    from bookflow.hub.access import visible_org_ids
    org_ids = visible_org_ids(s) or set()
    company_ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "company"}
    if org_ids:
        rows = s.hub.conn.execute(sa.select(h.companies.c.id).where(h.companies.c.organization_id.in_(org_ids))).all()
        company_ids |= {r[0] for r in rows}
    record_ids = org_ids | company_ids
    if not record_ids:
        return sa.false()
    sub = sa.select(h.audit_entries.c.event_id).where(h.audit_entries.c.record_id.in_(record_ids))
    return h.audit_events.c.id.in_(sub)
