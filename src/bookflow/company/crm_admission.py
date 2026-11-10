"""CRM source admission follows annotation and audit ancestry before disclosure."""
import sqlalchemy as sa
from bookflow.company import schema as c
from bookflow.core.ids import is_ulid, normalize_ulid


def lead_sources(s, kind, identifier):
    pending, seen, leads = [(kind, identifier)], set(), set()
    while pending:
        kind, identifier = pending.pop()
        if is_ulid(identifier):
            identifier = normalize_ulid(identifier)
        if (kind, identifier) in seen:
            continue
        seen.add((kind, identifier))
        if kind == 'lead':
            leads.add(identifier)
        elif kind in ('lead_contact', 'lead_contact_point'):
            table = c.crm_lead_contacts if kind == 'lead_contact' else c.crm_lead_contact_points
            field = table.c.lead_id if kind == 'lead_contact' else table.c.contact_id
            owner = s.company.conn.execute(sa.select(field).where(table.c.id == identifier)).scalar_one_or_none()
            if owner:
                pending.append(('lead' if kind == 'lead_contact' else 'lead_contact', owner))
        elif kind in ('note', 'attachment_link', 'audit_entry'):
            table = {'note': c.notes, 'attachment_link': c.attachment_links, 'audit_entry': c.audit_entries}[kind]
            row = s.company.conn.execute(sa.select(table.c.record_type, table.c.record_id).where(table.c.id == identifier)).first()
            if row:
                pending.append(tuple(row))
        elif kind in ('attachment', 'audit_event'):
            table = c.attachment_links if kind == 'attachment' else c.audit_entries
            field = table.c.attachment_id if kind == 'attachment' else table.c.event_id
            pending.extend(tuple(row) for row in s.company.conn.execute(
                sa.select(table.c.record_type, table.c.record_id).where(field == identifier)))
    return leads


def authorize_target(s, kind, identifier):
    # Even an unknown direct CRM ID requires target access before existence checks.
    if kind in ('lead', 'lead_contact', 'lead_contact_point') or lead_sources(s, kind, identifier):
        from bookflow.company.crm import require_read
        require_read(s)


def reference_touches(s, kind, identifier):
    """Immutable admission provenance prevents later relinking from opening old audit."""
    from bookflow.core.registry import Touched
    return [Touched('lead', lead_id, 'reference', None, None,
        {'record_type': kind, 'record_id': identifier}, db='company')
        for lead_id in sorted(lead_sources(s, kind, identifier))]


def authorize_annotation(inp, ctx, s):
    if getattr(inp, 'record_type', None) and getattr(inp, 'record_id', None):
        authorize_target(s, inp.record_type, inp.record_id)
    for field, kind in (('note', 'note'), ('attachment', 'attachment'), ('link', 'attachment_link')):
        if getattr(inp, field, None):
            authorize_target(s, kind, getattr(inp, field))
