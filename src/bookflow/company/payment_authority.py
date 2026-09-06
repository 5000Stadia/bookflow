"""Current-fact payment disclosure requirements shared by command publishers.

These helpers only read company facts. They neither admit credentials nor add a
permission system; callers enforce the returned existing resource requirements.
"""
import sqlalchemy as sa
import json

from bookflow.company import schema as c
from bookflow.hub.access import require_resource


def linked_work_required(db, transaction_ids):
    ids = set(transaction_ids)
    if not ids:
        return False
    # Historical settlement edges remain evidence even after full unapply.
    apps = c.applications
    ids.update(db.conn.execute(sa.select(apps.c.paid_transaction_id).where(
        apps.c.paying_transaction_id.in_(ids))).scalars())
    return db.conn.execute(sa.select(c.work_billing_allocations.c.id).where(
        c.work_billing_allocations.c.transaction_id.in_(ids)).limit(1)).first() is not None


def requirements(db, transaction_ids, *, write=False):
    result = [('ledger.post' if write else 'ledger.read', 'standard' if write else 'member')]
    if linked_work_required(db, transaction_ids):
        result.append(('customer-work', 'standard' if write else 'member'))
    return tuple(result)


def authorize(s, transaction_ids, *, write=False):
    for resource, role in requirements(s.company, transaction_ids, write=write):
        require_resource(s, resource, role)


PAYMENT_TARGETS = {
    'payment_profile': ('payment_profiles', 'revision_id'),
    'payment_component_key': ('payment_component_keys', 'id'),
    'payment_component': ('payment_components', 'id'),
    'application': ('applications', 'id'),
    'application_allocation': ('application_allocations', 'id'),
    'settlement_line_key': ('settlement_line_keys', 'id'),
    'payment_operation': ('payment_operations', 'id'),
    'payment_operation_item': ('payment_operation_items', 'id'),
    'payment_selection': ('payment_selections', 'id'),
    'payment_selection_revision': ('payment_selection_revisions', 'id'),
    'payment_selection_item': ('payment_selection_items', 'id'),
}


def record_transactions(db, record_type, record_id, seen=None):
    """Owned reference traversal for composite evidence; unknown links fail closed."""
    seen = set() if seen is None else seen
    identity = (record_type, record_id)
    if identity in seen:
        return set()
    seen.add(identity)
    if record_type == 'attachment':
        links = db.conn.execute(sa.select(c.attachment_links.c.id).where(c.attachment_links.c.attachment_id == record_id)).scalars().all()
        # Historical associations remain authority-bearing after unlink. An
        # attachment with no associations is ordinary unattached evidence.
        ids = set()
        for identifier in links:
            ids.update(record_transactions(db, 'attachment_link', identifier, seen))
        return ids
    if record_type == 'transaction':
        if db.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id == record_id)).first() is None:
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
        return {record_id}
    target = PAYMENT_TARGETS.get(record_type)
    if target is None:
        if record_type in ('transaction_revision', 'document_line', 'document_line_identity', 'posting_batch', 'posting_line', 'posting_line_source'):
            from bookflow.company.records import _TARGETS
            target = _TARGETS[record_type]
        elif record_type in ('note', 'attachment_link'):
            from bookflow.company.records import _TARGETS
            target = _TARGETS[record_type]
        else:
            return set()
    table = c.metadata.tables[target[0]]
    row = db.conn.execute(sa.select(table).where(table.c[target[1]] == record_id)).mappings().one_or_none()
    if row is None:
        from bookflow.core.errors import BookflowError
        raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
    row = dict(row)
    ids = {row[field] for field in ('transaction_id', 'paying_transaction_id', 'paid_transaction_id', 'source_transaction_id', 'target_transaction_id') if row.get(field)}
    if record_type == 'payment_operation':
        try:
            payload = json.loads(row['request_snapshot'])
            resolved = payload['resolved_transaction_ids']
            if not isinstance(resolved, list) or any(not isinstance(value, str) for value in resolved):
                raise ValueError()
            ids.update(resolved)
        except (ValueError, KeyError, TypeError):
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'}) from None
    if record_type == 'payment_operation_item':
        ids.update(record_transactions(db, 'payment_operation', row['operation_id'], seen))
    if record_type.startswith('payment_selection'):
        selection_id = row['id'] if record_type == 'payment_selection' else row['selection_id']
        ids.update(db.conn.execute(sa.select(c.payment_selection_items.c.invoice_id).where(
            c.payment_selection_items.c.selection_id == selection_id, c.payment_selection_items.c.invoice_id.is_not(None))).scalars())
        contexts = db.conn.execute(sa.select(c.payment_selection_revisions.c.context_snapshot).where(
            c.payment_selection_revisions.c.selection_id == selection_id)).scalars()
        try:
            for context in contexts:
                payment = json.loads(context)['payment_id']
                if payment:
                    ids.add(payment)
        except (ValueError, KeyError, TypeError):
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'}) from None
    if record_type in ('note', 'attachment_link') and row.get('record_type') and row.get('record_id'):
        ids.update(record_transactions(db, row['record_type'], row['record_id'], seen))
    return ids


def event_requirements(db, event_id):
    """One disclosure decision for the complete mixed payment audit event."""
    entries = list(db.conn.execute(sa.select(c.audit_entries.c.record_type, c.audit_entries.c.record_id).where(
        c.audit_entries.c.event_id == event_id)))
    ids = set()
    for kind, identifier in entries:
        ids.update(record_transactions(db, kind, identifier))
    return requirements(db, ids) if ids or any(kind in PAYMENT_TARGETS for kind, _ in entries) else ()


def authorize_event(s, event_id):
    for resource, role in event_requirements(s.company, event_id):
        require_resource(s, resource, role)


def denied_events(s):
    from bookflow.core.errors import BookflowError
    events = s.company.conn.execute(sa.select(c.audit_entries.c.event_id).where(
        c.audit_entries.c.record_type.in_((*PAYMENT_TARGETS, 'transaction', 'transaction_revision', 'document_line',
            'document_line_identity', 'posting_batch', 'posting_line', 'posting_line_source', 'note', 'attachment', 'attachment_link'))).distinct()).scalars()
    denied = []
    for event in events:
        try:
            authorize_event(s, event)
        except BookflowError as exc:
            if exc.code != 'E_PERMISSION':
                raise
            denied.append(event)
    return denied
