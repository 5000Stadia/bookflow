"""Hub audit writer and reader (blueprint 7)."""

from __future__ import annotations

import sqlalchemy as sa

from bookflow.core.session import Session
from bookflow.hub import schema as h

from bookflow.core.audit import RAW, SECRET_FIELDS, ZIP, decode_snapshot, encode_snapshot, write_event, write_event_to  # noqa: F401


def visible_record_ids(s: Session) -> set[str] | None:
    """Visible entry IDs; private setup events also pass visible_event_ids_filter."""
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
    from bookflow.company.intercompany_visibility import visible_relationship_ids
    return org_ids | company_ids | {s.actor.id} | visible_relationship_ids(s)


def visible_event_ids_filter(s: Session):
    """Visible entry/own-event policy, intersected with private setup authority."""
    from bookflow.company.intercompany_visibility import hidden_events
    private = hidden_events(s, hub=True)
    permitted = h.audit_events.c.id.not_in(private) if private else sa.true()
    if s.is_hub_admin:
        return permitted
    ids = visible_record_ids(s) or set()
    own = h.audit_events.c.actor_id == s.actor.id
    sub = sa.select(h.audit_entries.c.event_id).where(h.audit_entries.c.record_id.in_(ids))
    return sa.and_(permitted, sa.or_(own, h.audit_events.c.id.in_(sub)))
