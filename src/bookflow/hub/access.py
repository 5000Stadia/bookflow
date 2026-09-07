"""Memberships, roles, and visibility (blueprint 3.0, 4.3)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.core.session import Session
from bookflow.hub import schema as h

ROLE_RANK = {"readonly": 0, "standard": 1, "admin": 2, "owner": 3}
ROLE_FOR_REQUIRED = {"member": 0, "standard": 1, "admin": 2, "owner": 3}


def load_memberships(s: Session) -> list[dict[str, Any]]:
    assert s.hub and s.actor
    # Select only the legacy fields this row consumes. A writable open loads the
    # actor before migrations run, so newly declared inert columns must not make
    # an older, otherwise upgradeable hub unreadable.
    columns = [h.memberships.c[name] for name in (
        "id", "user_id", "scope_type", "scope_id", "role", "granted_by", "granted_at", "revoked_at",
    )]
    rows = s.hub.conn.execute(sa.select(*columns).where(
        h.memberships.c.user_id == s.actor.id, h.memberships.c.revoked_at.is_(None),
    )).mappings().all()
    s.memberships = [dict(r) for r in rows]
    return s.memberships


def org_role(s: Session, organization_id: str) -> str | None:
    best = None
    for m in s.memberships:
        if m["scope_type"] == "organization" and m["scope_id"] == organization_id:
            if best is None or ROLE_RANK[m["role"]] > ROLE_RANK[best]:
                best = m["role"]
    return best


def company_role(s: Session, company_id: str, organization_id: str) -> tuple[str | None, str | None]:
    """Return (access, role): access is hub_admin, organization, company, or None."""
    best_role, access = None, None
    for m in s.memberships:
        if (m["scope_type"] == "company" and m["scope_id"] == company_id) or (m["scope_type"] == "organization" and m["scope_id"] == organization_id):
            if best_role is None or ROLE_RANK[m["role"]] > ROLE_RANK[best_role]:
                best_role, access = m["role"], m["scope_type"]
    if best_role is None and s.is_hub_admin:
        return "hub_admin", None
    return access, best_role


def visible_org_ids(s: Session) -> set[str] | None:
    """None means everything (hub admin)."""
    if s.is_hub_admin:
        return None
    ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "organization"}
    company_ids = [m["scope_id"] for m in s.memberships if m["scope_type"] == "company"]
    if company_ids:
        rows = s.hub.conn.execute(sa.select(h.companies.c.organization_id).where(h.companies.c.id.in_(company_ids))).all()
        ids |= {r[0] for r in rows}
    return ids


def visible_company_filter(s: Session):
    """SQLAlchemy criterion selecting companies the actor can see."""
    if s.is_hub_admin:
        return sa.true()
    org_ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "organization"}
    company_ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "company"}
    crit = sa.false()
    if org_ids:
        crit = sa.or_(crit, h.companies.c.organization_id.in_(org_ids))
    if company_ids:
        crit = sa.or_(crit, h.companies.c.id.in_(company_ids))
    return crit


def role_satisfies(role: str | None, access: str | None, required: str | None, hub_admin: bool) -> bool:
    if required is None:
        return True
    if required == "hub_admin":
        return hub_admin
    if hub_admin:
        return True
    if role is None:
        return False
    return ROLE_RANK[role] >= ROLE_FOR_REQUIRED[required]


def require_explicit_grant(s: Session, capability: str) -> None:
    """Live default deny; replaced only by reviewed actor/principal enforcement.

    There is deliberately no configurable provider, context override or role
    escape here. Tests may monkeypatch this owner in their own process.
    """
    from bookflow.core.errors import BookflowError
    raise BookflowError('E_PERMISSION', details={'reason': 'capability_not_activated'})


def require_command_activation(s: Session, cmd) -> None:
    """Admission before company opening, saved recovery facts or planner reads."""
    from bookflow.core.registry import EXPLICIT_GRANT_ONLY_CAPABILITIES
    if cmd.explicit_grant_only or cmd.capability in EXPLICIT_GRANT_ONLY_CAPABILITIES:
        require_explicit_grant(s, cmd.capability)
    for capability, _ in cmd.resource_requirements:
        if capability in EXPLICIT_GRANT_ONLY_CAPABILITIES:
            require_explicit_grant(s, capability)


def require_resource(s: Session, capability: str, required_role: str) -> None:
    """Common company resource check; granular grants/denies remain Row7."""
    from bookflow.core.errors import BookflowError
    from bookflow.core.registry import EXPLICIT_GRANT_ONLY_CAPABILITIES
    if capability in EXPLICIT_GRANT_ONLY_CAPABILITIES:
        require_explicit_grant(s, capability)
    access, role = company_role(s, s.company_row['id'], s.company_row['organization_id'])
    if access is None:
        raise BookflowError('E_COMPANY_NOT_FOUND')
    if not role_satisfies(role, access, required_role, s.is_hub_admin):
        raise BookflowError('E_PERMISSION', details={'capability': capability,
            'required_role': required_role, 'role': role})
