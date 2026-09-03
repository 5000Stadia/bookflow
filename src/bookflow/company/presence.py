"""Advisory presence (blueprint 6.4): never audited, never blocking, 90-second expiry."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core import clock

TTL_SECONDS = 90


def _cutoff() -> str:
    return (clock.now() - timedelta(seconds=TTL_SECONDS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def prune(db) -> None:
    db.conn.execute(c.presence.delete().where(c.presence.c.heartbeat_at < _cutoff()))


def set_presence(db, *, record_type: str, record_id: str, user_id: str, interface: str) -> None:
    prune(db)
    now = clock.now_iso()
    key = (c.presence.c.record_type == record_type) & (c.presence.c.record_id == record_id) & (c.presence.c.user_id == user_id) & (c.presence.c.interface == interface)
    if db.conn.execute(sa.select(c.presence.c.started_at).where(key)).first():
        db.conn.execute(c.presence.update().where(key).values(heartbeat_at=now))
    else:
        db.conn.execute(c.presence.insert().values(record_type=record_type, record_id=record_id, user_id=user_id, interface=interface, started_at=now, heartbeat_at=now))


def clear_presence(db, *, record_type: str, record_id: str, user_id: str, interface: str) -> None:
    db.conn.execute(c.presence.delete().where(c.presence.c.record_type == record_type, c.presence.c.record_id == record_id, c.presence.c.user_id == user_id, c.presence.c.interface == interface))


def live_for(db, record_type: str, record_id: str) -> list[dict[str, Any]]:
    rows = db.conn.execute(sa.select(c.presence).where(c.presence.c.record_type == record_type, c.presence.c.record_id == record_id, c.presence.c.heartbeat_at >= _cutoff()).order_by(c.presence.c.started_at)).mappings().all()
    return [dict(r) for r in rows]
