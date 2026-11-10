"""Whole-event visibility for private intercompany setup facts.

Setup events require current reads in both companies, even when the reader owns
the event or administers the hub. These checks consult registry and authority
metadata only; they never open a counterpart company's file.
"""
from copy import copy

import sqlalchemy as sa

from bookflow.core.errors import BookflowError
from bookflow.hub import schema as h


def _exists(db, table):
    return db.raw.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _pair_visible(s, company_id, peer_id):
    from bookflow.company.intercompany import require_pair

    if not company_id or not peer_id or s.hub is None:
        return False
    row = s.hub.conn.execute(sa.select(h.companies).where(
        h.companies.c.id == company_id)).mappings().first()
    if row is None:
        return False
    view = copy(s)
    view.company_row = dict(row)
    view.company_id = company_id
    view.company = None
    view.company_info_row = None
    # Resource caches are scoped to a single company, not the entire invocation.
    view._resource_checks = None
    try:
        require_pair(view, peer_id, manage=False)
    except BookflowError as exc:
        if exc.code not in ('E_COMPANY_NOT_FOUND', 'E_PERMISSION'):
            raise
        return False
    return True


def visible_relationship_ids(s):
    """Hub audit entry identities, admitted without the hub-admin shortcut."""
    if s.hub is None or not _exists(s.hub, 'intercompany_relationships'):
        return set()
    return {identifier for identifier, company_a, company_b in s.hub.raw.execute(
        'SELECT id, company_a, company_b FROM intercompany_relationships'
    ).fetchall() if _pair_visible(s, company_a, company_b)}


def hidden_events(s, hub=False, *, event_id=None):
    """Private setup event IDs excluded before pagination and watermark selection.

    Unknown mapping/relationship identities fail closed. Older schemas have no
    such audit entries, so migration admission needs no new tables to read them.
    """
    db = s.hub if hub else s.company
    if db is None:
        return set()
    record_type = 'intercompany_relationship' if hub else 'intercompany_mapping'
    sql = 'SELECT DISTINCT event_id, record_id FROM audit_entries WHERE record_type=?'
    parameters = [record_type]
    if event_id is not None:
        sql += ' AND event_id=?'
        parameters.append(event_id)
    entries = db.raw.execute(sql, parameters).fetchall()
    local_denied = set()
    if not hub:
        drafts = db.raw.execute("SELECT DISTINCT event_id FROM audit_entries WHERE record_type='intercompany_draft'" + (' AND event_id=?' if event_id is not None else ''), (event_id,) if event_id is not None else ()).fetchall()
        if drafts:
            from bookflow.company.intercompany_local import require_read
            try:
                require_read(s)
            except BookflowError as exc:
                if exc.code != 'E_PERMISSION':
                    raise
                local_denied = {row[0] for row in drafts}
    if not entries:
        return local_denied
    if hub:
        admitted = visible_relationship_ids(s)
    else:
        mappings = dict(db.raw.execute('SELECT id, peer_id FROM intercompany_mappings').fetchall()) if _exists(
            db, 'intercompany_mappings') else {}
        company_id = s.company_row['id'] if s.company_row else None
        from bookflow.company.intercompany import resolve_peer_reference
        mappings = {record: resolve_peer_reference(s, ref) for record, ref in mappings.items()}
        peers = {mappings[record] for _, record in entries if record in mappings}
        admitted_peers = {peer for peer in peers if _pair_visible(s, company_id, peer)}
        admitted = {record for record, peer in mappings.items() if peer in admitted_peers}
    return local_denied | {event for event, record in entries if record not in admitted}


def authorize_event(s, event_id, hub=False):
    if hidden_events(s, hub, event_id=event_id):
        raise BookflowError('E_EVENT_NOT_FOUND')
