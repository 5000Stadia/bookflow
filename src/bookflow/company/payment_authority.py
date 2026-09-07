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
    'deposit_operation': ('deposit_operations', 'id'),
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
    if record_type == 'deposit_operation':
        targets = _evidence_rows(db, c.deposit_operation_targets, 'operation_id', record_id, cache)
        owned = {r['transaction_id'] for r in targets}
        if row['transaction_id'] not in owned:
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
        ids.update(owned)
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
        headers = [row] if record_type == 'payment_selection' else _evidence_rows(db, c.payment_selections, 'id', selection_id, cache)
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


_BATCH_SIZE = 200
_TRANSACTION_FIELDS = ('transaction_id', 'paying_transaction_id', 'paid_transaction_id',
                       'source_transaction_id', 'target_transaction_id')


def _groups(values):
    values = list(values)
    for start in range(0, len(values), _BATCH_SIZE):
        yield values[start:start + _BATCH_SIZE]


class _EventCohort:
    """Projected facts for at most 200 events, including their complete closures.

    Graph facts are shared, traversal results are not: each root has its own seen
    set. Errors stay on their node until that root reaches it in scalar order.
    Nothing here retains audit snapshots or survives the owning operation.
    """
    def __init__(self, db, events):
        self.db = db
        self.entries = self._read(c.audit_entries, 'event_id', events,
                                  ('event_id', 'record_type', 'record_id'))
        self.nodes = {}
        frontier = list(dict.fromkeys((r['record_type'], r['record_id'])
                    for rows in self.entries.values() for r in rows))
        while frontier:
            by_kind = {}
            for kind, identifier in frontier:
                by_kind.setdefault(kind, []).append(identifier)
            pending = []
            for kind, identifiers in by_kind.items():
                self._load(kind, identifiers)
                for identifier in identifiers:
                    node = self.nodes[(kind, identifier)]
                    if not isinstance(node, Exception):
                        pending.extend(node[1])
            frontier = [key for key in dict.fromkeys(pending) if key not in self.nodes]
        # Compute full roots in entry order before work membership. Errors are
        # retained per event, so a later event cannot preempt an earlier error.
        self.resolved = {}
        all_ids = set()
        for event in events:
            try:
                ids = set()
                for row in self.entries.get(event, ()):
                    ids.update(self._walk((row['record_type'], row['record_id'])))
                self.resolved[event] = ids
                all_ids.update(ids)
            except Exception as exc:
                self.resolved[event] = exc
        self.applications = self._read(c.applications, 'paying_transaction_id', all_ids,
                                      ('paying_transaction_id', 'paid_transaction_id'))
        work_ids = all_ids | {r['paid_transaction_id'] for rows in self.applications.values() for r in rows}
        self.work = self._read(c.work_billing_allocations, 'transaction_id', work_ids, ('transaction_id',))

    def _read(self, table, field, identifiers, columns):
        groups = {}
        for cohort in _groups(dict.fromkeys(identifiers)):
            query = sa.select(*(table.c[name] for name in columns)).where(table.c[field].in_(cohort))
            with self.db.conn.execute(query) as result:
                for rows in result.partitions(_BATCH_SIZE):
                    for values in rows:
                        row = dict(zip(columns, values))
                        groups.setdefault(row[field], []).append(row)
        return groups

    @staticmethod
    def _missing():
        from bookflow.core.errors import BookflowError
        return BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})

    def _load(self, kind, identifiers):
        from bookflow.company.records import _TARGETS
        if kind == 'attachment':
            rows = self._read(c.attachment_links, 'attachment_id', identifiers, ('attachment_id', 'id'))
            for identifier in identifiers:
                self.nodes[(kind, identifier)] = (set(), [('attachment_link', r['id']) for r in rows.get(identifier, ())])
            return
        if kind == 'payment_selection_recovery_active':
            # This identity is persistent S, even after its physical barrier is removed.
            for identifier in identifiers:
                self.nodes[(kind, identifier)] = (set(), [('payment_selection', identifier)])
            return
        target = PAYMENT_TARGETS.get(kind)
        if kind == 'transaction':
            target = ('transactions', 'id')
        elif target is None and kind in ('transaction_revision', 'document_line', 'document_line_identity',
                'posting_batch', 'posting_line', 'posting_line_source', 'note', 'attachment_link'):
            target = _TARGETS[kind]
        if target is None:
            for identifier in identifiers:
                self.nodes[(kind, identifier)] = (set(), [])
            return
        table = c.metadata.tables[target[0]]
        columns = [target[1]]
        columns.extend(field for field in _TRANSACTION_FIELDS if field in table.c and field not in columns)
        extra = {'payment_operation': ('request_snapshot',), 'payment_operation_item': ('operation_id',),
                 'note': ('record_type', 'record_id'), 'attachment_link': ('record_type', 'record_id')}.get(kind, ())
        if kind.startswith('payment_selection'):
            extra = ('id', 'consumed_operation_id') if kind == 'payment_selection' else ('selection_id',)
        columns = list(dict.fromkeys((*columns, *extra)))
        found = self._read(table, target[1], identifiers, columns)
        items, revisions, attempts, headers = {}, {}, {}, {}
        deposit_targets = self._read(c.deposit_operation_targets, 'operation_id', identifiers, ('operation_id','transaction_id')) if kind == 'deposit_operation' else {}
        if kind.startswith('payment_selection'):
            owners = [r['id'] if kind == 'payment_selection' else r['selection_id']
                      for rows in found.values() if len(rows) == 1 for r in rows]
            items = self._read(c.payment_selection_items, 'selection_id', owners, ('selection_id', 'invoice_id'))
            revisions = self._read(c.payment_selection_revisions, 'selection_id', owners, ('selection_id', 'context_snapshot'))
            attempts = self._read(c.payment_selection_recovery_items, 'selection_id', owners, ('selection_id', 'invoice_id'))
            headers = found if kind == 'payment_selection' else self._read(
                c.payment_selections, 'id', owners, ('id', 'consumed_operation_id'))
        # Keep the existing loaded-evidence seam over projected, bounded facts.
        cache = {(table.name, target[1]): found}
        for identifier in identifiers:
            rows = _evidence_rows(self.db, table, target[1], identifier, cache)
            if kind == 'transaction':
                self.nodes[(kind, identifier)] = ({identifier}, []) if rows else self._missing()
                continue
            if len(rows) != 1:
                self.nodes[(kind, identifier)] = self._missing()
                continue
            row = rows[0]
            try:
                ids = {row[field] for field in _TRANSACTION_FIELDS if row.get(field)}
                edges = []
                if kind == 'deposit_operation':
                    owned = {r['transaction_id'] for r in deposit_targets.get(identifier, ())}
                    if row['transaction_id'] not in owned:
                        raise self._missing()
                    ids.update(owned)
                if kind == 'payment_operation':
                    try:
                        resolved = json.loads(row['request_snapshot'])['resolved_transaction_ids']
                        if not isinstance(resolved, list) or any(not isinstance(value, str) for value in resolved):
                            raise ValueError()
                        ids.update(resolved)
                    except (ValueError, KeyError, TypeError):
                        raise self._missing() from None
                if kind == 'payment_operation_item':
                    edges.append(('payment_operation', row['operation_id']))
                if kind.startswith('payment_selection'):
                    owner = row['id'] if kind == 'payment_selection' else row['selection_id']
                    ids.update(r['invoice_id'] for r in items.get(owner, ()) if r['invoice_id'])
                    ids.update(r['invoice_id'] for r in attempts.get(owner, ()))
                    selection = headers.get(owner, ())
                    if selection and selection[0]['consumed_operation_id']:
                        edges.append(('payment_operation', selection[0]['consumed_operation_id']))
                    try:
                        for revision in revisions.get(owner, ()):
                            payment = json.loads(revision['context_snapshot'])['payment_id']
                            if payment:
                                ids.add(payment)
                    except (ValueError, KeyError, TypeError):
                        raise self._missing() from None
                if kind in ('note', 'attachment_link') and row.get('record_type') and row.get('record_id'):
                    edges.append((row['record_type'], row['record_id']))
                self.nodes[(kind, identifier)] = (ids, edges)
            except Exception as exc:
                self.nodes[(kind, identifier)] = exc

    def _walk(self, root):
        seen, ids, pending = set(), set(), [root]
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            node = self.nodes[key]
            if isinstance(node, Exception):
                raise node
            ids.update(node[0])
            pending.extend(reversed(node[1]))
        return ids

    def requirements(self, event):
        ids = self.resolved[event]
        if isinstance(ids, Exception):
            raise ids
        if not ids and not any(r['record_type'] in PAYMENT_TARGETS for r in self.entries.get(event, ())):
            return ()
        expanded = ids | {r['paid_transaction_id'] for identifier in ids for r in self.applications.get(identifier, ())}
        result = [('ledger.read', 'member')]
        if any(identifier in self.work for identifier in expanded):
            result.append(('customer-work', 'member'))
        return tuple(result)


def authorize_event(s, event_id, resolved=None):
    facts = resolved[event_id] if resolved is not None and event_id in resolved else event_requirements(s.company, event_id)
    for resource, role in facts:
        require_resource(s, resource, role)


def authorize_events(s, event_ids):
    """Check explicit event occurrences in order within this snapshot only.

    Deferred graph errors and permissions retain scalar order. Operational
    prefetch errors may precede an earlier denial; they abort without fallback.
    """
    from itertools import islice
    events = iter(event_ids)
    while cohort := list(islice(events, _BATCH_SIZE)):
        reader = _EventCohort(s.company, cohort)
        for event in cohort:
            for resource, role in reader.requirements(event):
                require_resource(s, resource, role)
        del reader


def denied_events(s, resolved=None):
    from bookflow.core.errors import BookflowError
    events = s.company.conn.execute(sa.select(c.audit_entries.c.event_id).where(
        c.audit_entries.c.record_type.in_((*PAYMENT_TARGETS, 'transaction', 'transaction_revision', 'document_line',
            'document_line_identity', 'posting_batch', 'posting_line', 'posting_line_source', 'note', 'attachment', 'attachment_link'))).distinct()).scalars()
    denied = []
    for cohort in events.partitions(_BATCH_SIZE):
        reader = _EventCohort(s.company, cohort)
        for event in cohort:
            try:
                facts = reader.requirements(event)
                if resolved is not None:
                    resolved[event] = facts
                for resource, role in facts:
                    require_resource(s, resource, role)
            except BookflowError as exc:
                if exc.code != 'E_PERMISSION':
                    raise
                denied.append(event)
        del reader
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


def _publication_transaction_facts(s, identifiers):
    """Bounded root facts; retain unmatched one-step targets for validation."""
    roots, apps = c.transactions, c.applications
    target = roots.alias('publication_target')
    unresolved = sa.exists(sa.select(sa.literal(1)).select_from(apps).where(
        apps.c.paying_transaction_id == roots.c.id,
        ~sa.exists(sa.select(sa.literal(1)).select_from(target).where(
            target.c.id == apps.c.paid_transaction_id)).correlate(apps))).correlate(roots)
    statement = sa.select(roots.c.id, work_link_predicate(roots.c.id).label('linked_work'),
                          unresolved.label('unresolved_target')).where(roots.c.id.in_(identifiers))
    return {row['id']: row for row in s.company.conn.execute(statement).mappings()}


def authorize_publication_transactions(s, occurrences):
    """Authorize ordered occurrences with at most 200 roots loaded at a time."""
    from itertools import islice
    from bookflow.core.errors import BookflowError
    occurrences = iter(occurrences)
    while batch := list(islice(occurrences, _BATCH_SIZE)):
        facts = _publication_transaction_facts(s, list(dict.fromkeys(identifier for identifier, _ in batch)))
        for identifier, write in batch:
            fact = facts.get(identifier)
            if fact is None or fact['unresolved_target']:
                raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
            role = 'standard' if write else 'member'
            require_resource(s, 'ledger.post' if write else 'ledger.read', role)
            if fact['linked_work']:
                require_resource(s, 'customer-work', role)
        del facts


def _publication_payer_family(customer_id):
    family = sa.select(c.customers.c.id).where(c.customers.c.id == customer_id).cte(
        'publication_payer_family', recursive=True)
    return family.union(sa.select(c.customers.c.id).join(family, c.customers.c.parent_id == family.c.id))


def _publication_payer_transactions(customer_id):
    """All historical AR contributors, including unresolved header references."""
    family = _publication_payer_family(customer_id)
    return sa.select(c.posting_lines.c.transaction_id.label('transaction_id')).join(
        c.accounts, c.accounts.c.id == c.posting_lines.c.account_id).where(
            c.accounts.c.type == 'accounts_receivable', c.posting_lines.c.name_type == 'customer',
            c.posting_lines.c.name_id.in_(sa.select(family.c.id))).distinct()


def authorize_publication_payer(s, customer_id, *, write=False):
    """Validate the exact payer/B/H evidence, then authorize B's one-step graph."""
    from bookflow.core.errors import BookflowError
    selected = _publication_payer_transactions(customer_id)
    base = selected.cte('publication_payer_base')
    base_ids = sa.select(base.c.transaction_id)
    targets = sa.select(c.applications.c.paid_transaction_id.label('transaction_id')).where(
        c.applications.c.paying_transaction_id.in_(base_ids))
    closure = base_ids.union(targets).cte('publication_payer_closure')
    missing_payer = ~sa.exists(sa.select(sa.literal(1)).select_from(c.customers).where(c.customers.c.id == customer_id))
    missing_target = sa.exists(sa.select(sa.literal(1)).select_from(closure).where(
        ~sa.exists(sa.select(sa.literal(1)).select_from(c.transactions).where(
            c.transactions.c.id == closure.c.transaction_id)).correlate(closure)))
    if s.company.conn.execute(sa.select(sa.or_(missing_payer, missing_target))).scalar_one():
        raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
    # authorize_query adds H itself; supplying closure would add an extra hop.
    authorize_query(s, selected, write=write)


class _PublicationSelectionCohort:
    """Complete selection history, shared only within one publication snapshot."""
    def __init__(self, db, identifiers):
        self.db = db
        self.headers = self._read(c.payment_selections, 'id', identifiers,
                                  ('id', 'consumed_operation_id'))
        self.items = self._read(c.payment_selection_items, 'selection_id', identifiers,
                                ('selection_id', 'invoice_id'))
        self.attempts = self._read(c.payment_selection_recovery_items, 'selection_id', identifiers,
                                   ('selection_id', 'invoice_id'))
        self.revisions = self._read(c.payment_selection_revisions, 'selection_id', identifiers,
                                    ('selection_id', 'context_snapshot'))
        self.resolved, operations = {}, set()
        for identifier in identifiers:
            try:
                rows = self.headers.get(identifier, ())
                if len(rows) != 1:
                    raise self._missing()
                if rows[0]['consumed_operation_id']:
                    operations.add(rows[0]['consumed_operation_id'])
            except Exception as exc:
                self.resolved[identifier] = exc
        self.operations = self._read(c.payment_operations, 'id', operations, ('id', 'request_snapshot'))
        all_ids = set()
        for identifier in identifiers:
            if identifier in self.resolved:
                continue
            try:
                ids = self._resolve(identifier)
                all_ids.update(ids)
                self.resolved[identifier] = ids
            except Exception as exc:
                # Content errors belong to this root, never to an earlier gate.
                self.resolved[identifier] = exc
        self.existing = self._read(c.transactions, 'id', all_ids, ('id',))
        self.applications = self._read(c.applications, 'paying_transaction_id', all_ids,
                                       ('paying_transaction_id', 'paid_transaction_id'))
        work_ids = all_ids | {r['paid_transaction_id'] for rows in self.applications.values() for r in rows}
        self.work = self._read(c.work_billing_allocations, 'transaction_id', work_ids, ('transaction_id',))

    def _read(self, table, field, identifiers, columns):
        groups = {}
        for cohort in _groups(dict.fromkeys(identifiers)):
            statement = sa.select(*(table.c[name] for name in columns)).where(table.c[field].in_(cohort))
            # Driver failures abort this cohort; they are not content failures.
            with self.db.conn.execute(statement) as result:
                for rows in result.partitions(_BATCH_SIZE):
                    for values in rows:
                        row = dict(zip(columns, values))
                        groups.setdefault(row[field], []).append(row)
        return groups

    @staticmethod
    def _missing():
        from bookflow.core.errors import BookflowError
        return BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})

    def _resolve(self, identifier):
        ids = {row['invoice_id'] for row in self.items.get(identifier, ()) if row['invoice_id']}
        ids.update(row['invoice_id'] for row in self.attempts.get(identifier, ()))
        operation = self.headers[identifier][0]['consumed_operation_id']
        if operation:
            rows = self.operations.get(operation, ())
            if len(rows) != 1:
                raise self._missing()
            try:
                values = json.loads(rows[0]['request_snapshot'])['resolved_transaction_ids']
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    raise ValueError()
                ids.update(values)
            except (ValueError, KeyError, TypeError):
                raise self._missing() from None
        try:
            for row in self.revisions.get(identifier, ()):
                payment = json.loads(row['context_snapshot'])['payment_id']
                if payment:
                    ids.add(payment)
        except (ValueError, KeyError, TypeError):
            raise self._missing() from None
        return ids

    def requirements(self, identifier, write):
        ids = self.resolved[identifier]
        if isinstance(ids, Exception):
            raise ids
        if not ids.issubset(self.existing):
            raise self._missing()
        # Selection's scalar contract validates D, not application-only H.
        expanded = ids | {row['paid_transaction_id'] for i in ids for row in self.applications.get(i, ())}
        role = 'standard' if write else 'member'
        result = [('ledger.post' if write else 'ledger.read', role)]
        if any(i in self.work for i in expanded):
            result.append(('customer-work', role))
        return tuple(result)


def authorize_publication_selections(s, occurrences):
    """Replay ordered roots after <=200-root reads, without reusing a fence."""
    from itertools import islice
    from bookflow.core.errors import BookflowError
    occurrences = iter(occurrences)
    while batch := list(islice(occurrences, _BATCH_SIZE)):
        facts = _PublicationSelectionCohort(s.company, list(dict.fromkeys(i for i, _ in batch)))
        for identifier, write in batch:
            try:
                required = facts.requirements(identifier, write)
            except (ValueError, TypeError, KeyError, RecursionError):
                raise BookflowError('E_IO', details={'stage': 'publication', 'outcome': 'unknown',
                                                    'reason': 'invalid_authority_evidence'}) from None
            for resource, role in required:
                require_resource(s, resource, role)
        del facts
