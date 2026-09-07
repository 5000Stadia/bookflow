"""Current-fact payment disclosure requirements shared by command publishers.

These helpers only read company facts. They neither admit credentials nor add a
permission system; callers enforce the returned existing resource requirements.
"""
import sqlalchemy as sa
import json

from bookflow.company import schema as c
from bookflow.hub.access import require_resource


def work_link_predicate(transaction_id):
    """SQL equivalent of historical work linkage, for complete list filtering."""
    work, apps = c.work_billing_allocations, c.applications
    return sa.or_(
        sa.exists(sa.select(work.c.id).where(work.c.transaction_id == transaction_id)),
        sa.exists(sa.select(apps.c.id).join(work, work.c.transaction_id == apps.c.paid_transaction_id)
            .where(apps.c.paying_transaction_id == transaction_id)))


def readable_predicate(s, transaction_id):
    """Use the existing resource gate once; never disclose protected list counts."""
    from bookflow.core.errors import BookflowError
    try:
        require_resource(s, 'customer-work', 'member')
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return ~work_link_predicate(transaction_id)
    return sa.true()


def linked_work_required(db, transaction_ids, cache=None):
    ids = set(transaction_ids)
    if not ids:
        return False
    # Historical settlement edges remain evidence even after full unapply.
    apps = c.applications
    if cache is not None:
        targets = _evidence_rows(db, apps, 'paying_transaction_id', None, cache)
        for identifier in tuple(ids):
            ids.update(row['paid_transaction_id'] for row in targets.get(identifier, []))
        work = _evidence_rows(db, c.work_billing_allocations, 'transaction_id', None, cache)
        return any(identifier in work for identifier in ids)
    ids.update(db.conn.execute(sa.select(apps.c.paid_transaction_id).where(
        apps.c.paying_transaction_id.in_(ids))).scalars())
    return db.conn.execute(sa.select(c.work_billing_allocations.c.id).where(
        c.work_billing_allocations.c.transaction_id.in_(ids)).limit(1)).first() is not None


def requirements(db, transaction_ids, *, write=False, cache=None):
    result = [('ledger.post' if write else 'ledger.read', 'standard' if write else 'member')]
    if linked_work_required(db, transaction_ids, cache):
        result.append(('customer-work', 'standard' if write else 'member'))
    return tuple(result)


def authorize(s, transaction_ids, *, write=False):
    for resource, role in requirements(s.company, transaction_ids, write=write):
        require_resource(s, resource, role)


def authorize_query(s, transaction_ids, *, write=False):
    """Authorize a complete SQL-selected graph without a Python ID expansion."""
    selected = transaction_ids.cte('payment_authority_transactions')
    ids = sa.select(selected.c.transaction_id)
    targets = sa.select(c.applications.c.paid_transaction_id).where(c.applications.c.paying_transaction_id.in_(ids))
    linked = s.company.conn.execute(sa.select(c.work_billing_allocations.c.id).where(sa.or_(
        c.work_billing_allocations.c.transaction_id.in_(ids),
        c.work_billing_allocations.c.transaction_id.in_(targets))).limit(1)).first() is not None
    require_resource(s, 'ledger.post' if write else 'ledger.read', 'standard' if write else 'member')
    if linked:
        require_resource(s, 'customer-work', 'standard' if write else 'member')


PAYMENT_TARGETS = {
    'payment_selection_recovery': ('payment_selection_recoveries', 'id'),
    'payment_selection_recovery_chunk': ('payment_selection_recovery_chunks', 'id'),
    'payment_selection_recovery_item': ('payment_selection_recovery_items', 'id'),
    'payment_selection_recovery_active': ('payment_selection_recovery_active', 'selection_id'),
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


def _evidence_rows(db, table, field, value, cache):
    """Batch reads only within one disclosure decision and its DB snapshot."""
    if cache is None:
        return list(db.conn.execute(sa.select(table).where(table.c[field] == value)).mappings())
    key = (table.name, field)
    if key not in cache:
        groups = {}
        for row in db.conn.execute(sa.select(table)).mappings():
            groups.setdefault(row[field], []).append(row)
        cache[key] = groups
    return cache[key] if value is None else cache[key].get(value, [])


def record_transactions(db, record_type, record_id, seen=None, cache=None):
    """Owned reference traversal for composite evidence; unknown links fail closed."""
    seen = set() if seen is None else seen
    identity = (record_type, record_id)
    if identity in seen:
        return set()
    seen.add(identity)
    if record_type == 'attachment':
        links = [row['id'] for row in _evidence_rows(db, c.attachment_links, 'attachment_id', record_id, cache)]
        # Historical associations remain authority-bearing after unlink. An
        # attachment with no associations is ordinary unattached evidence.
        ids = set()
        for identifier in links:
            ids.update(record_transactions(db, 'attachment_link', identifier, seen, cache))
        return ids
    if record_type == 'transaction':
        if not _evidence_rows(db, c.transactions, 'id', record_id, cache):
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
        return {record_id}
    if record_type == 'payment_selection_recovery_active':
        # Barrier rows are deliberately removed; their primary identity is S.
        return record_transactions(db, 'payment_selection', record_id, seen, cache)
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
    found = _evidence_rows(db, table, target[1], record_id, cache)
    row = found[0] if len(found) == 1 else None
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
        ids.update(record_transactions(db, 'payment_operation', row['operation_id'], seen, cache))
    if record_type.startswith('payment_selection'):
        selection_id = row['id'] if record_type == 'payment_selection' else row['selection_id']
        ids.update(item['invoice_id'] for item in _evidence_rows(db, c.payment_selection_items, 'selection_id', selection_id, cache) if item['invoice_id'])
        ids.update(item['invoice_id'] for item in _evidence_rows(db, c.payment_selection_recovery_items, 'selection_id', selection_id, cache))
        headers = _evidence_rows(db, c.payment_selections, 'id', selection_id, cache)
        if headers and headers[0]['consumed_operation_id']:
            ids.update(record_transactions(db, 'payment_operation', headers[0]['consumed_operation_id'], seen, cache))
        contexts = [revision['context_snapshot'] for revision in _evidence_rows(db, c.payment_selection_revisions, 'selection_id', selection_id, cache)]
        try:
            for context in contexts:
                payment = json.loads(context)['payment_id']
                if payment:
                    ids.add(payment)
        except (ValueError, KeyError, TypeError):
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'}) from None
    if record_type in ('note', 'attachment_link') and row.get('record_type') and row.get('record_id'):
        ids.update(record_transactions(db, row['record_type'], row['record_id'], seen, cache))
    return ids


def event_requirements(db, event_id, cache=None):
    """One disclosure decision for the complete mixed payment audit event."""
    entries = [(row['record_type'], row['record_id']) for row in _evidence_rows(db, c.audit_entries, 'event_id', event_id, cache)]
    ids = set()
    for kind, identifier in entries:
        ids.update(record_transactions(db, kind, identifier, cache=cache))
    return requirements(db, ids, cache=cache) if ids or any(kind in PAYMENT_TARGETS for kind, _ in entries) else ()


def authorize_event(s, event_id):
    for resource, role in event_requirements(s.company, event_id):
        require_resource(s, resource, role)


def denied_events(s):
    from bookflow.core.errors import BookflowError
    events = s.company.conn.execute(sa.select(c.audit_entries.c.event_id).where(
        c.audit_entries.c.record_type.in_((*PAYMENT_TARGETS, 'transaction', 'transaction_revision', 'document_line',
            'document_line_identity', 'posting_batch', 'posting_line', 'posting_line_source', 'note', 'attachment', 'attachment_link'))).distinct()).scalars()
    denied, cache = [], {}
    for event in events:
        try:
            for resource, role in event_requirements(s.company, event, cache):
                require_resource(s, resource, role)
        except BookflowError as exc:
            if exc.code != 'E_PERMISSION':
                raise
            denied.append(event)
    return denied


def payer_transactions(db, customer_id):
    """Exact AR-contributing family graph shared with balance disclosure."""
    family = [row[0] for row in db.raw.execute("""WITH RECURSIVE family(id) AS (
        SELECT id FROM customers WHERE id=? UNION SELECT c.id FROM customers c JOIN family f ON c.parent_id=f.id)
        SELECT id FROM family""", (customer_id,))]
    return set(db.conn.execute(sa.select(c.posting_lines.c.transaction_id).join(c.accounts,
        c.accounts.c.id == c.posting_lines.c.account_id).where(c.accounts.c.type == 'accounts_receivable',
        c.posting_lines.c.name_type == 'customer', c.posting_lines.c.name_id.in_(family)).distinct()).scalars())


def disclosure_transactions(db, kind, identifier):
    """Current complete owning graph, independent of returned page bounds.

    The result is fed to authorize/requirements; no new role policy or execution
    path exists here. Operation snapshots and selection histories use the same
    record traversal as annotations and composite audit evidence.
    """
    if kind == 'payer':
        return payer_transactions(db, identifier)
    if kind not in ('payment_history', 'invoice_settlement'):
        return record_transactions(db, kind, identifier)
    ids = record_transactions(db, 'transaction', identifier)
    field = c.applications.c.paying_transaction_id if kind == 'payment_history' else c.applications.c.paid_transaction_id
    rows = db.conn.execute(sa.select(c.applications.c.paying_transaction_id,
        c.applications.c.paid_transaction_id).where(field == identifier))
    for paying, paid in rows:
        ids.update((paying, paid))
    if kind == 'payment_history':
        # The core history command discloses every operation whose complete
        # resolved intent includes this receipt, even after applications vanish.
        for row in db.conn.execute(sa.select(c.payment_operations.c.id, c.payment_operations.c.request_snapshot)).mappings():
            try:
                values = json.loads(row['request_snapshot'])['resolved_transaction_ids']
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                from bookflow.core.errors import BookflowError
                raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'}) from None
            if identifier in values:
                ids.update(record_transactions(db, 'payment_operation', row['id']))
    return ids
