"""Hub audit writer and reader (blueprint 7)."""

from __future__ import annotations

import sqlalchemy as sa

from bookflow.core.session import Session
from bookflow.hub import schema as h

from bookflow.core.audit import RAW, SECRET_FIELDS, ZIP, decode_snapshot, encode_snapshot, write_event, write_event_to  # noqa: F401


def visible_record_ids(s: Session) -> set[str] | None:
    """Ids of organizations and companies the actor may see; None for hub admins (everything)."""
    if s.is_hub_admin:
        return None
    org_ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "organization"}
    company_ids = {m["scope_id"] for m in s.memberships if m["scope_type"] == "company"}
    if org_ids:
        rows = s.hub.conn.execute(sa.select(h.companies.c.id).where(h.companies.c.organization_id.in_(org_ids))).all()
        company_ids |= {r[0] for r in rows}
    if company_ids:
        rows = s.hub.conn.execute(sa.select(h.companies.c.organization_id).where(h.companies.c.id.in_(company_ids))).all()
        org_ids |= {r[0] for r in rows}
    return org_ids | company_ids | {s.actor.id}


def visible_event_ids_filter(s: Session):
    """Criterion over audit_events: hub admins see all; others see events with a visible entry or their own."""
    if s.is_hub_admin:
        return sa.true()
    ids = visible_record_ids(s) or set()
    own = h.audit_events.c.actor_id == s.actor.id
    sub = sa.select(h.audit_entries.c.event_id).where(h.audit_entries.c.record_id.in_(ids))
    return sa.or_(own, h.audit_events.c.id.in_(sub))
