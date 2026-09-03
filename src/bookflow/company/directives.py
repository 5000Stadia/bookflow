"""Standing instructions (blueprint 5.8)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import is_ulid, new_id, normalize_ulid
from bookflow.core.registry import Touched
from bookflow.hub.users import common


def next_code(db) -> str:
    n = db.conn.execute(sa.select(c.sequences.c.next_number).where(c.sequences.c.name == "directive")).scalar_one()
    db.conn.execute(c.sequences.update().where(c.sequences.c.name == "directive").values(next_number=n + 1))
    return f"SI-{n}"


def add(db, *, text: str, given_by: str, recorded_by: str, via: str) -> tuple[dict[str, Any], Touched]:
    row = {"id": new_id(), "code": next_code(db), "text": text, "given_by": given_by, "recorded_by": recorded_by, "active": True,
           "deactivated_at": None, "deactivated_by": None, **common(recorded_by, via)}
    db.conn.execute(c.directives.insert().values(**row))
    return row, Touched("directive", row["id"], "create", None, 1, row)


def resolve(db, selector: str, *, include_inactive: bool = True) -> dict[str, Any]:
    """By id or code, case-insensitively. Raises E_DIRECTIVE_NOT_FOUND with suggestions."""
    q = sa.select(c.directives)
    row = None
    if is_ulid(selector):
        row = db.conn.execute(q.where(c.directives.c.id == normalize_ulid(selector))).mappings().first()
    if row is None:
        row = db.conn.execute(q.where(sa.func.upper(c.directives.c.code) == selector.upper())).mappings().first()
    if row is None:
        rows = db.conn.execute(sa.select(c.directives.c.code, c.directives.c.text).order_by(c.directives.c.code)).all()
        needle = selector.lower()
        suggestions = [r.code for r in rows if needle in r.code.lower() or needle in r.text.lower()][:3]
        raise BookflowError("E_DIRECTIVE_NOT_FOUND", details={"selector": selector, "suggestions": suggestions})
    row = dict(row)
    if not include_inactive and not row["active"]:
        raise BookflowError("E_DIRECTIVE_INACTIVE", details={"code": row["code"], "deactivated_at": row["deactivated_at"], "deactivated_by": row["deactivated_by"]})
    return row


def deactivate(db, row: dict[str, Any], actor_id: str, via: str) -> tuple[dict[str, Any], Touched]:
    new = {**row, "active": False, "deactivated_at": clock.now_iso(), "deactivated_by": actor_id, "version": row["version"] + 1,
           "updated_at": clock.now_iso(), "updated_by": actor_id, "updated_via": via}
    db.conn.execute(c.directives.update().where(c.directives.c.id == row["id"]).values(active=False, deactivated_at=new["deactivated_at"], deactivated_by=actor_id, version=new["version"], updated_at=new["updated_at"], updated_by=actor_id, updated_via=via))
    return new, Touched("directive", row["id"], "deactivate", row["version"], new["version"], new)


def list_all(db, include_inactive: bool) -> list[dict[str, Any]]:
    q = sa.select(c.directives).order_by(c.directives.c.created_at)
    if not include_inactive:
        q = q.where(c.directives.c.active.is_(True))
    return [dict(r) for r in db.conn.execute(q).mappings().all()]
