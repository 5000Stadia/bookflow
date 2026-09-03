"""Audit writer and snapshot codec over either database (blueprint 7)."""

from __future__ import annotations

import hashlib
import json
import zlib
from typing import Any

import sqlalchemy as sa

from bookflow.core.context import Context
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import Session, now_iso

SECRET_FIELDS = {"password_hash", "token_hash", "tax_id"}
RAW, ZIP = b"\x00", b"\x01"


def _mask(v: Any) -> Any:
    """Secrets are stored as a short hash so a change is visible and the value is not."""
    if v is None:
        return None
    return "sha256:" + hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:12]


def encode_snapshot(data: dict[str, Any] | None) -> bytes | None:
    if data is None:
        return None
    clean = {k: (_mask(v) if k in SECRET_FIELDS else v) for k, v in data.items()}
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


def _tables(db):
    """The (audit_events, audit_entries) tables for the given database."""
    from bookflow.company import schema as c
    from bookflow.hub import schema as h
    if db.path.name == "hub.db":
        return h.audit_events, h.audit_entries
    return c.audit_events, c.audit_entries


def next_seq(db, events_table) -> int:
    current = db.conn.execute(sa.select(sa.func.max(events_table.c.seq))).scalar()
    return (current or 0) + 1


def write_event_to(db, ctx: Context, command: str, summary: str, touched: list[Touched], *, actor_id: str | None,
                   actor_kind: str | None, directive_code: str | None = None) -> str:
    """Insert one event and its entries into ``db`` inside the caller's open transaction."""
    events, entries = _tables(db)
    event_id = new_id()
    db.conn.execute(events.insert().values(
        id=event_id, seq=next_seq(db, events), at=now_iso(), command=command,
        actor_id=actor_id, actor_kind=actor_kind,
        on_behalf_of=ctx.on_behalf_of, interface=ctx.interface.value, client_name=ctx.client_name,
        client_version=ctx.client_version, client_host=ctx.client_host, session_id=ctx.session_id,
        request_id=ctx.request_id, idempotency_key=ctx.idempotency_key, reason=ctx.reason,
        directive_id=ctx.directive_id, directive_code=directive_code, source_ref=ctx.source_ref, summary=summary[:512],
    ))
    for t in touched:
        db.conn.execute(entries.insert().values(
            id=new_id(), event_id=event_id, record_type=t.record_type, record_id=t.record_id, action=t.action,
            version_before=t.version_before, version_after=t.version_after,
            after=encode_snapshot(t.after), before=encode_snapshot(t.before),
        ))
    return event_id


def write_event(s: Session, ctx: Context, command: str, summary: str, touched: list[Touched], actor_id: str | None = None,
                actor_kind: str | None = None, db=None) -> str:
    """Hub event by default; pass ``db=s.company`` for a company event."""
    target = db if db is not None else s.hub
    assert target is not None
    return write_event_to(target, ctx, command, summary, touched,
                          actor_id=actor_id if actor_id is not None else (s.actor.id if s.actor else None),
                          actor_kind=actor_kind if actor_kind is not None else (s.actor.kind if s.actor else None),
                          directive_code=getattr(s, "directive_code", None))
