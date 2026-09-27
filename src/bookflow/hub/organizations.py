"""Organization registry (blueprint 3.0)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.core.durability import sync_directory
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import Session, now_iso
from bookflow.hub import schema as h
from bookflow.hub.permission_scopes import registry_write
from bookflow.hub.users import common
from bookflow.storage.paths import name_key, reserve_folder, write_org_marker


def name_taken(s: Session, key: str, exclude_id: str | None = None) -> bool:
    q = sa.select(h.organizations.c.id).where(h.organizations.c.name_key == key)
    if exclude_id:
        q = q.where(h.organizations.c.id != exclude_id)
    return s.hub.conn.execute(q).first() is not None


@registry_write
def create(s: Session, display_name: str, via: str, is_demo: bool = False,
           owner_membership: bool = False) -> tuple[dict[str, Any], list[Touched]]:
    key = name_key(display_name)
    if name_taken(s, key):
        raise BookflowError("E_NAME_TAKEN", details={"name": display_name})
    s.organizations_dir.mkdir(mode=0o700, exist_ok=True)
    folder = reserve_folder(s.organizations_dir, display_name)
    oid = new_id()
    try:
        write_org_marker(folder, organization_id=oid)
        sync_directory(s.organizations_dir)
        sync_directory(s.organizations_dir.parent)
        row = {"id": oid, "display_name": display_name, "name_key": key, "path": s.rel_path(folder), "pending_path": None,
               "is_demo": is_demo, **common(s.actor.id, via)}
        s.hub.conn.execute(h.organizations.insert().values(**row))
    except BaseException:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise
    touched = [Touched("organization", oid, "create", None, 1, row)]
    if owner_membership:
        # `organization new` makes its creator the organization's owner, as `company new`
        # makes its creator the company's owner; the same hub event records both rows.
        m = {"id": new_id(), "user_id": s.actor.id, "scope_type": "organization", "scope_id": oid, "role": "owner",
             "granted_by": s.actor.id, "granted_at": now_iso(), "revoked_at": None}
        s.hub.conn.execute(h.memberships.insert().values(**m))
        touched.append(Touched("membership", m["id"], "create", None, None, m))
    return row, touched


def get(s: Session, organization_id: str) -> dict[str, Any] | None:
    row = s.hub.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == organization_id)).mappings().first()
    return dict(row) if row else None


def bump(row: dict[str, Any], actor_id: str, via: str, **changes: Any) -> dict[str, Any]:
    new = {**row, **changes, "version": row["version"] + 1, "updated_at": now_iso(), "updated_by": actor_id, "updated_via": via}
    return new
