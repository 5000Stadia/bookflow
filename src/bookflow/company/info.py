"""company_info and principals."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.session import Session, now_iso
from bookflow.storage.engine import Database


def read_info(db: Database) -> dict[str, Any]:
    row = db.conn.execute(sa.select(c.company_info)).mappings().first()
    return dict(row) if row else {}


def upsert_principal(db: Database, *, user_id: str, username: str, display_name: str, kind: str) -> None:
    at = now_iso()
    existing = db.conn.execute(sa.select(c.principals.c.user_id).where(c.principals.c.user_id == user_id)).first()
    if existing:
        db.conn.execute(c.principals.update().where(c.principals.c.user_id == user_id).values(username=username, display_name=display_name, kind=kind, last_seen_at=at))
    else:
        db.conn.execute(c.principals.insert().values(user_id=user_id, username=username, display_name=display_name, kind=kind, first_seen_at=at, last_seen_at=at))


def upsert_actor(s: Session) -> None:
    assert s.company and s.actor
    upsert_principal(s.company, user_id=s.actor.id, username=s.actor.username, display_name=s.actor.display_name, kind=s.actor.kind)


def principal_names(db: Database, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = db.conn.execute(sa.select(c.principals.c.user_id, c.principals.c.display_name).where(c.principals.c.user_id.in_(ids))).all()
    return {r[0]: r[1] for r in rows}


def write_display_name_copy(db: Database, display_name: str) -> None:
    """Raw column write: leaves version and updated_* untouched (blueprint 3.1)."""
    db.conn.execute(c.company_info.update().values(display_name=display_name))
