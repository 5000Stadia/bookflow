"""Idempotency keys (blueprint 6.5): lookup before plan, store after apply, in-progress rows for rollout."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.core import clock
from bookflow.core.errors import BookflowError

EXPIRY_DAYS = 30


def input_hash(validated_input: dict[str, Any], company_id: str | None) -> str:
    canonical = json.dumps({"input": validated_input, "company_id": company_id}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _table(db):
    from bookflow.company import schema as c
    from bookflow.hub import schema as h
    return h.idempotency_keys if db.path.name == "hub.db" else c.idempotency_keys


def lookup(db, actor_id: str, key: str, command: str, ihash: str) -> dict[str, Any] | None:
    """Return the stored row for a done or in-progress key, or None. Raises E_IDEMPOTENCY_MISMATCH."""
    t = _table(db)
    row = db.conn.execute(sa.select(t).where(t.c.actor_id == actor_id, t.c.key == key)).mappings().first()
    if row is None:
        return None
    row = dict(row)
    if clock.parse_iso(row["created_at"]) < clock.now() - timedelta(days=EXPIRY_DAYS):
        return None
    if row["command"] != command or row["input_hash"] != ihash:
        raise BookflowError("E_IDEMPOTENCY_MISMATCH", details={"key": key, "stored_command": row["command"]})
    return row


def store(db, actor_id: str, key: str, command: str, ihash: str, request_id: str, output: dict[str, Any] | None, state: str = "done") -> None:
    t = _table(db)
    cutoff = (clock.now() - timedelta(days=EXPIRY_DAYS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    db.conn.execute(t.delete().where(sa.or_(t.c.created_at < cutoff, sa.and_(t.c.actor_id == actor_id, t.c.key == key))))
    db.conn.execute(t.insert().values(actor_id=actor_id, key=key, command=command, input_hash=ihash, state=state,
                                      request_id=request_id, output=json.dumps(output, default=str) if output is not None else None,
                                      created_at=clock.now_iso()))
