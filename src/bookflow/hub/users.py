"""Users: bootstrap and lookups."""

from __future__ import annotations

from typing import Any
import unicodedata

import sqlalchemy as sa

from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.session import Session, now_iso
from bookflow.hub import schema as h


def common(actor_id: str, via: str, at: str | None = None) -> dict[str, Any]:
    at = at or now_iso()
    return {"version": 1, "created_at": at, "created_by": actor_id, "created_via": via, "updated_at": at, "updated_by": actor_id, "updated_via": via}


def username_key(username: str) -> str:
    """Unicode case-insensitive identity, preserving stored spelling and whitespace."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", username).casefold())


def username_matches(db, username: str) -> list[dict[str, Any]]:
    """At most two matches, including inactive users and non-human handles."""
    db.raw.create_function("bookflow_username_key", 1, username_key, deterministic=True)
    rows = db.conn.execute(sa.select(h.users).where(
        sa.func.bookflow_username_key(h.users.c.username) == username_key(username)
    ).limit(2)).mappings().all()
    return [dict(row) for row in rows]


def find_by_username(db, username: str) -> dict[str, Any] | None:
    """Resolve only an unambiguous username; legacy case collisions fail closed."""
    rows = username_matches(db, username)
    return rows[0] if len(rows) == 1 else None


def find_user(s: Session, **where: Any) -> dict[str, Any] | None:
    if "username" in where:
        row = find_by_username(s.hub, where.pop("username"))
        return row if row and all(row[k] == v for k, v in where.items()) else None
    q = sa.select(h.users)
    for k, v in where.items():
        q = q.where(getattr(h.users.c, k) == v)
    row = s.hub.conn.execute(q).mappings().first()
    return dict(row) if row else None


def user_names(s: Session, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = s.hub.conn.execute(sa.select(h.users.c.id, h.users.c.display_name).where(h.users.c.id.in_(ids))).all()
    return {r[0]: r[1] for r in rows}


def create_system_user(s: Session, via: str) -> dict[str, Any]:
    uid = new_id()
    row = {"id": uid, "kind": "system", "username": "system", "display_name": "System", "owner_user_id": None,
           "password_hash": None, "hub_admin": False, "timezone": None, "active": True, **common(uid, via)}
    s.hub.conn.execute(h.users.insert().values(**row))
    return row


def _machine_zone() -> str | None:
    try:
        from tzlocal import get_localzone_name
        return get_localzone_name()
    except Exception:
        return None


def create_human(s: Session, *, username: str, display_name: str, created_by: str, via: str, hub_admin: bool,
                 password_hash: str | None = None) -> dict[str, Any]:
    if username_matches(s.hub, username):
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "username", "problem": "already in use"}]})
    row = {"id": new_id(), "kind": "human", "username": username, "display_name": display_name, "owner_user_id": None,
           "password_hash": password_hash, "hub_admin": hub_admin, "timezone": _machine_zone(), "active": True, **common(created_by, via)}
    s.hub.conn.execute(h.users.insert().values(**row))
    return row
