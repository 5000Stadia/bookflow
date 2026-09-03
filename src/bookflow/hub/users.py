"""Users: bootstrap and lookups."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.core.ids import new_id
from bookflow.core.session import Session, now_iso
from bookflow.hub import schema as h


def common(actor_id: str, via: str, at: str | None = None) -> dict[str, Any]:
    at = at or now_iso()
    return {"version": 1, "created_at": at, "created_by": actor_id, "created_via": via, "updated_at": at, "updated_by": actor_id, "updated_via": via}


def find_user(s: Session, **where: Any) -> dict[str, Any] | None:
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


def create_human(s: Session, *, username: str, display_name: str, created_by: str, via: str, hub_admin: bool) -> dict[str, Any]:
    row = {"id": new_id(), "kind": "human", "username": username, "display_name": display_name, "owner_user_id": None,
           "password_hash": None, "hub_admin": hub_admin, "timezone": _machine_zone(), "active": True, **common(created_by, via)}
    s.hub.conn.execute(h.users.insert().values(**row))
    return row
