"""Retained co24 row ownership proofs, called only after whole-event admission."""
import sqlalchemy as sa

from bookflow.company import schema
from bookflow.core.errors import BookflowError
from . import audit_projection_deposit_drafts as codec


def require(value):
    if not value:
        raise BookflowError('E_VALIDATION', details={'reason': 'audit_format'})


def validate(db, *, event, entry, snapshot, side):
    """Prove a decoded raw image against retained keys, event and owner relations.

    Mutable before/after headers are historical images, never compared with the
    current mutable values. Immutable rows use their complete composite key.
    This function makes no permission decision; it returns the validated capture
    for the caller's subsequent disclosure filtering.
    """
    kind = entry['record_type']
    require(kind in codec.TABLES and side in ('before', 'after'))
    decoded = codec.decode_snapshot(producer=event['command'], record_type=kind,
                                    action=entry['action'], record_id=entry['record_id'], snapshot=snapshot)
    table_name, keys = codec.TABLES[kind]
    table = schema.metadata.tables[table_name]
    row = db.conn.execute(sa.select(table).where(*(
        table.c[key] == snapshot[key] for key in keys))).mappings().one_or_none()
    require(row is not None)
    mutable = kind in ('deposit_draft', 'deposit_selection')
    if mutable:
        for field in ('id', 'created_at', 'created_by', 'created_via'):
            require(row[field] == snapshot[field])
        require(snapshot['version'] == entry['version_' + side])
        revision_table = schema.metadata.tables[
            'deposit_draft_revisions' if kind == 'deposit_draft' else 'deposit_selection_revisions']
        revision = db.conn.execute(sa.select(revision_table).where(
            revision_table.c.id == snapshot['current_revision_id'])).mappings().one_or_none()
        require(revision is not None)
        # Consumption increments the mutable draft header while retaining the
        # exact draft revision that was consumed. Other changes create a revision.
        expected = revision['version'] + int(kind == 'deposit_draft' and snapshot['state'] == 'consumed')
        require(snapshot['version'] == expected)
    else:
        require(dict(row) == snapshot)
        require(side == 'after' and entry['action'] == 'create')
    if side == 'after':
        require(snapshot['audit_event_id'] == event['id'])
    else:
        captured_seq = db.conn.execute(sa.select(schema.audit_events.c.seq).where(
            schema.audit_events.c.id == snapshot['audit_event_id'])).scalar_one_or_none()
        event_seq = db.conn.execute(sa.select(schema.audit_events.c.seq).where(
            schema.audit_events.c.id == event['id'])).scalar_one_or_none()
        require(captured_seq is not None and event_seq is not None and captured_seq < event_seq)
    # All declared co24 relational constraints apply to the historical image,
    # including revision/header and permanent-row/header composite ownership.
    for constraint in table.foreign_key_constraints:
        elements = tuple(constraint.elements)
        values = tuple(snapshot[e.parent.name] for e in elements)
        if any(value is None for value in values):
            continue  # SQL MATCH SIMPLE; typed capture guards validate option pairs.
        target = elements[0].column.table
        found = db.conn.execute(sa.select(sa.literal(1)).select_from(target).where(*(
            element.column == value for element, value in zip(elements, values, strict=True)
        )).limit(1)).first()
        require(found is not None)
    return decoded
