"""Company registry (blueprint 3.0, 3.1)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.core.session import Session, now_iso
from bookflow.hub import access, schema as h
from bookflow.hub.users import common
from bookflow.storage.paths import name_key


def name_taken(s: Session, organization_id: str, key: str, exclude_id: str | None = None) -> bool:
    q = sa.select(h.companies.c.id).where(h.companies.c.organization_id == organization_id, h.companies.c.name_key == key)
    if exclude_id:
        q = q.where(h.companies.c.id != exclude_id)
    return s.hub.conn.execute(q).first() is not None


def register(s: Session, *, company_id: str, organization_id: str, display_name: str, rel_path: str, legal_name: str,
             home_currency: str, schema_revision: str, via: str, is_demo: bool = False, owner_membership: bool = True) -> tuple[dict[str, Any], list[Touched]]:
    key = name_key(display_name)
    if name_taken(s, organization_id, key):
        raise BookflowError("E_NAME_TAKEN", details={"name": display_name})
    row = {"id": company_id, "organization_id": organization_id, "display_name": display_name, "name_key": key,
           "path": rel_path, "pending_path": None, "legal_name": legal_name, "home_currency": home_currency,
           "schema_revision": schema_revision, "is_demo": is_demo, **common(s.actor.id, via)}
    s.hub.conn.execute(h.companies.insert().values(**row))
    touched = [Touched("company", company_id, "create", None, 1, row)]
    if owner_membership:
        m = {"id": new_id(), "user_id": s.actor.id, "scope_type": "company", "scope_id": company_id, "role": "owner",
             "granted_by": s.actor.id, "granted_at": now_iso(), "revoked_at": None}
        s.hub.conn.execute(h.memberships.insert().values(**m))
        touched.append(Touched("membership", m["id"], "create", None, None, m))
    return row, touched


def get(s: Session, company_id: str) -> dict[str, Any] | None:
    row = s.hub.conn.execute(sa.select(h.companies).where(h.companies.c.id == company_id)).mappings().first()
    return dict(row) if row else None


def list_visible(s: Session) -> list[dict[str, Any]]:
    q = (sa.select(h.companies, h.organizations.c.display_name.label("organization_name"))
         .join(h.organizations, h.organizations.c.id == h.companies.c.organization_id)
         .where(access.visible_company_filter(s)).order_by(h.organizations.c.name_key, h.companies.c.name_key))
    return [dict(r) for r in s.hub.conn.execute(q).mappings().all()]


def update(s: Session, row: dict[str, Any], via: str, **changes: Any) -> tuple[dict[str, Any], Touched]:
    new = {**row, **changes, "version": row["version"] + 1, "updated_at": now_iso(), "updated_by": s.actor.id, "updated_via": via}
    s.hub.conn.execute(h.companies.update().where(h.companies.c.id == row["id"]).values(**{k: v for k, v in new.items() if k in h.companies.c}))
    return new, Touched("company", row["id"], "update", row["version"], new["version"], {k: v for k, v in new.items() if k in h.companies.c})


def delete_company_rows(s: Session, company_id: str) -> list[Touched]:
    """Hard delete with entries carrying the rows (blueprint 3.1)."""
    touched: list[Touched] = []
    row = get(s, company_id)
    if row is None:
        return touched
    for m in s.hub.conn.execute(sa.select(h.memberships).where(h.memberships.c.scope_type == "company", h.memberships.c.scope_id == company_id)).mappings().all():
        touched.append(Touched("membership", m["id"], "delete", None, None, None, before=dict(m)))
    s.hub.conn.execute(h.memberships.delete().where(h.memberships.c.scope_type == "company", h.memberships.c.scope_id == company_id))
    s.hub.conn.execute(h.companies.delete().where(h.companies.c.id == company_id))
    touched.append(Touched("company", company_id, "delete", row["version"], None, None, before=row))
    s.config.clear_default_everywhere({company_id})
    s.pending_config = True
    return touched


def delete_organization_rows(s: Session, organization_id: str) -> list[Touched]:
    touched: list[Touched] = []
    for c in s.hub.conn.execute(sa.select(h.companies.c.id).where(h.companies.c.organization_id == organization_id)).all():
        touched += delete_company_rows(s, c[0])
    for m in s.hub.conn.execute(sa.select(h.memberships).where(h.memberships.c.scope_type == "organization", h.memberships.c.scope_id == organization_id)).mappings().all():
        touched.append(Touched("membership", m["id"], "delete", None, None, None, before=dict(m)))
    s.hub.conn.execute(h.memberships.delete().where(h.memberships.c.scope_type == "organization", h.memberships.c.scope_id == organization_id))
    org = s.hub.conn.execute(sa.select(h.organizations).where(h.organizations.c.id == organization_id)).mappings().first()
    s.hub.conn.execute(h.organizations.delete().where(h.organizations.c.id == organization_id))
    if org:
        touched.append(Touched("organization", organization_id, "delete", org["version"], None, None, before=dict(org)))
    return touched
