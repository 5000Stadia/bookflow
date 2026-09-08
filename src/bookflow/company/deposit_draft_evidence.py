"""Retained deposit composition graph evidence, without identity admission."""
from dataclasses import dataclass
import sqlalchemy as sa
from bookflow.company import schema as c
from bookflow.core.errors import BookflowError


@dataclass(frozen=True)
class DraftGraph:
    transactions: tuple[str, ...]
    drafts: tuple[str, ...]
    selections: tuple[str, ...]


def collect(db, *, sources=(), draft=None, selection=None, _evidence=None):
    # Audit prefetch is this module's concrete snapshot-local reader, never an
    # identity/provider callback. Ordinary admission retains its scalar reads.
    if _evidence is not None and (not isinstance(_evidence, _AuditEvidence) or _evidence.db is not db):
        raise TypeError('foreign graph reader')
    def rows(table, field, value):
        if _evidence is not None:
            return _evidence.get(table.name, field, (value,))[value]
        columns = {
            'deposit_drafts': ('id', 'edit_transaction_id', 'copy_transaction_id', 'consumed_operation_id'),
            'deposit_selections': ('id', 'target_draft_id'),
            'deposit_draft_sources': ('source_transaction_id',),
            'deposit_selection_sources': ('source_transaction_id',),
            'deposit_operation_targets': ('transaction_id',),
        }[table.name]
        return list(db.conn.execute(sa.select(*(table.c[k] for k in columns)).where(table.c[field] == value)).mappings())
    targets=set(sources);drafts=set([draft] if draft else []);selections=set([selection] if selection else [])
    visited_drafts=set();visited_selections=set()
    # Full retained graph, including accepted/abandoned children and operation-only targets.
    while drafts-visited_drafts or selections-visited_selections:
        for identity in sorted(drafts-visited_drafts):
            visited_drafts.add(identity)
            found=rows(c.deposit_drafts, 'id', identity)
            header=found[0] if len(found)==1 else None
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            if header['edit_transaction_id']:targets.add(header['edit_transaction_id'])
            if header['copy_transaction_id']:targets.add(header['copy_transaction_id'])
            targets.update(r['source_transaction_id'] for r in rows(c.deposit_draft_sources, 'draft_id', identity))
            selections.update(r['id'] for r in rows(c.deposit_selections, 'target_draft_id', identity))
            if header['consumed_operation_id']:
                targets.update(r['transaction_id'] for r in rows(c.deposit_operation_targets, 'operation_id', header['consumed_operation_id']))
        for identity in sorted(selections-visited_selections):
            visited_selections.add(identity)
            found=rows(c.deposit_selections, 'id', identity)
            header=found[0] if len(found)==1 else None
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            drafts.add(header['target_draft_id'])
            targets.update(r['source_transaction_id'] for r in rows(c.deposit_selection_sources, 'selection_id', identity))
    # Walk complete deposit membership and permanent-operation participants.
    # A removed receipt still connects its historical deposit and sibling sources.
    visited=set()
    while targets-visited:
        frontier=sorted(targets-visited);visited.update(frontier)
        for offset in range(0,len(frontier),200):
            group=frontier[offset:offset+200];members=c.deposit_memberships
            if _evidence is not None:
                targets.update(_evidence.connected(group))
                continue
            for source,deposit in db.conn.execute(sa.select(members.c.source_transaction_id,members.c.transaction_id).where(
                    sa.or_(members.c.source_transaction_id.in_(group),members.c.transaction_id.in_(group)))):
                targets.update((source,deposit))
            op=c.deposit_operations;ot=c.deposit_operation_targets
            targets.update(db.conn.execute(sa.select(ot.c.transaction_id).join(op,op.c.id==ot.c.operation_id).where(op.c.transaction_id.in_(group))).scalars())
    return DraftGraph(tuple(sorted(targets)), tuple(sorted(visited_drafts)), tuple(sorted(visited_selections)))


# Closed stored entry identities. Composite occurrence keys are NOT reduced to
# the first row: audit record_id is row_id for all retained source/additional rows.
AUDIT_ROOTS = {
    'deposit_draft': ('deposit_drafts', 'id', ('id',)),
    'deposit_selection': ('deposit_selections', 'id', ('id',)),
    'deposit_draft_revision': ('deposit_draft_revisions', 'id', ('id',)),
    'deposit_selection_revision': ('deposit_selection_revisions', 'id', ('id',)),
    'deposit_draft_row_key': ('deposit_draft_row_keys', 'id', ('id',)),
    'deposit_draft_source': ('deposit_draft_sources', 'row_id', ('revision_id', 'row_id')),
    'deposit_selection_source': ('deposit_selection_sources', 'row_id', ('revision_id', 'row_id')),
    'deposit_draft_additional': ('deposit_draft_additional', 'row_id', ('revision_id', 'row_id')),
    'deposit_draft_consumption': ('deposit_draft_consumptions', 'operation_id', ('operation_id',)),
}


def _unresolved():
    return BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})


def _require(value):
    if not value:
        raise _unresolved()


class _AuditEvidence:
    """Snapshot-local relational facts; only keyed reads, at most200 query keys.

    No authentication, JSON interpretation, whole-table cache or retained policy.
    Failures are resolved per root, not thrown while prefetching unrelated roots.
    """
    def __init__(self, db):
        self.db = db
        self.rows = {}
        self.checked = set()
        self.graphs = {}

    def get(self, table_name, field, identifiers):
        table = c.metadata.tables[table_name]
        index = self.rows.setdefault((table_name, field), {})
        missing = [v for v in dict.fromkeys(identifiers) if v not in index]
        for offset in range(0, len(missing), 200):
            group = missing[offset:offset + 200]
            index.update((v, []) for v in group)
            # Payloads are irrelevant to authority and must not be decoded here.
            columns = [col for col in table.c if col.name not in ('snapshot', 'request_snapshot', 'effect_snapshot')]
            with self.db.conn.execute(sa.select(*columns).where(table.c[field].in_(group))) as result:
                for partition in result.mappings().partitions(200):
                    for row in partition:
                        index[row[field]].append(row)
                        primary = tuple(table.primary_key.columns)
                        if len(primary) == 1 and primary[0].name != field:
                            key = primary[0].name
                            self.rows.setdefault((table_name, key), {})[row[key]] = [row]
        return {v: index[v] for v in identifiers}

    def warm(self, drafts, selections):
        """Prefetch complete owner adjacency per cohort, not scans per root.

        Some co24 owner columns have no leading index. Group them here, before
        individual graph/error resolution; absent rows remain node-local errors.
        """
        visited_drafts, visited_selections = set(), set()
        while drafts - visited_drafts or selections - visited_selections:
            pending = sorted(drafts - visited_drafts)
            visited_drafts.update(pending)
            headers = self.get('deposit_drafts', 'id', pending)
            for table in ('deposit_draft_sources', 'deposit_draft_additional',
                          'deposit_draft_revisions', 'deposit_draft_row_keys'):
                self.get(table, 'draft_id', pending)
            children = self.get('deposit_selections', 'target_draft_id', pending)
            selections.update(r['id'] for group in children.values() for r in group)
            operations = [r['consumed_operation_id'] for group in headers.values() for r in group
                          if r['consumed_operation_id'] is not None]
            self.get('deposit_operation_targets', 'operation_id', operations)
            pending = sorted(selections - visited_selections)
            visited_selections.update(pending)
            headers = self.get('deposit_selections', 'id', pending)
            drafts.update(r['target_draft_id'] for group in headers.values() for r in group)
            self.get('deposit_selection_sources', 'selection_id', pending)
            self.get('deposit_selection_revisions', 'selection_id', pending)
        targets = set()
        headers = self.get('deposit_drafts', 'id', visited_drafts)
        for group in headers.values():
            for header in group:
                targets.update(header[field] for field in ('edit_transaction_id', 'copy_transaction_id') if header[field])
                if header['consumed_operation_id']:
                    rows = self.get('deposit_operation_targets', 'operation_id', (header['consumed_operation_id'],))
                    targets.update(r['transaction_id'] for r in rows[header['consumed_operation_id']])
        for kind, owners in (('draft', visited_drafts), ('selection', visited_selections)):
            rows = self.get('deposit_' + kind + '_sources', kind + '_id', owners)
            targets.update(r['source_transaction_id'] for group in rows.values() for r in group)
        visited = set()
        while targets - visited:
            frontier = sorted(targets - visited)
            visited.update(frontier)
            targets.update(self.connected(frontier))


    def connected(self, identifiers):
        targets = set()
        for field in ('source_transaction_id', 'transaction_id'):
            rows = self.get('deposit_memberships', field, identifiers)
            for group in rows.values():
                for row in group:
                    targets.update((row['source_transaction_id'], row['transaction_id']))
        operations = self.get('deposit_operations', 'transaction_id', identifiers)
        operation_ids = [row['id'] for group in operations.values() for row in group]
        rows = self.get('deposit_operation_targets', 'operation_id', operation_ids)
        targets.update(row['transaction_id'] for group in rows.values() for row in group)
        return targets

    def one(self, table, field, value, **matches):
        rows = self.get(table, field, (value,))[value]
        _require(len(rows) == 1)
        row = rows[0]
        _require(all(row[k] == v for k, v in matches.items()))
        return row

    def revision(self, row, kind):
        parent = kind + '_id'
        header = self.one('deposit_' + kind + 's', 'id', row[parent])
        previous = row['previous_revision_id']
        _require((row['version'] == 1) == (previous is None))
        if previous is not None:
            self.one('deposit_' + kind + '_revisions', 'id', previous,
                     **{parent: row[parent], 'version': row['version'] - 1})
        if kind == 'selection':
            _require((row['target_draft_id'], row['target_revision_id']) ==
                     (header['target_draft_id'], header['target_revision_id']))
            self.one('deposit_draft_revisions', 'id', row['target_revision_id'], draft_id=row['target_draft_id'])
        else:
            for field in ('bank_account_id', 'cashback_account_id'):
                if row[field] is not None:
                    self.one('accounts', 'id', row[field])

    def row_key(self, row):
        _require(row['kind'] in ('source', 'additional'))
        header = self.one('deposit_drafts', 'id', row['draft_id'])
        _require((row['edit_transaction_id'] is None) == (row['original_row_id'] is None))
        if row['original_row_id'] is not None:
            _require(row['edit_transaction_id'] == (header['edit_transaction_id'] or header['copy_transaction_id']))
            self.one('deposit_row_keys', 'id', row['original_row_id'],
                     transaction_id=row['edit_transaction_id'], kind=row['kind'])

    def occurrence(self, row, kind, *, additional=False):
        revision = self.one('deposit_' + kind + '_revisions', 'id', row['revision_id'],
                            **{kind + '_id': row[kind + '_id']})
        self.revision(revision, kind)
        if kind == 'draft':
            _require(row['kind'] == ('additional' if additional else 'source'))
            key = self.one('deposit_draft_row_keys', 'id', row['row_id'], draft_id=row['draft_id'],
                           ordinal=row['ordinal'], kind='additional' if additional else 'source')
            self.row_key(key)
        if not additional:
            _require(row['source_type'] in ('payment', 'sales_receipt'))
            self.one('transactions', 'id', row['source_transaction_id'], type=row['source_type'])
            self.one('transaction_revisions', 'id', row['source_revision_id'], transaction_id=row['source_transaction_id'])

    def header(self, row, kind):
        revision = self.one('deposit_' + kind + '_revisions', 'id', row['current_revision_id'],
                            **{kind + '_id': row['id']})
        self.revision(revision, kind)
        if kind == 'selection':
            _require(row['state'] in ('open', 'accepted', 'abandoned'))
            self.one('deposit_drafts', 'id', row['target_draft_id'])
            self.one('deposit_draft_revisions', 'id', row['target_revision_id'], draft_id=row['target_draft_id'])
            _require((row['state'] == 'accepted') == (row['accepted_revision_id'] is not None))
            if row['accepted_revision_id'] is not None:
                self.one('deposit_draft_revisions', 'id', row['accepted_revision_id'], draft_id=row['target_draft_id'])
        else:
            _require(row['state'] in ('open', 'consumed', 'abandoned'))
            for field, revision_field, type_field in (
                    ('edit_transaction_id', 'baseline_revision_id', 'edit_type'),
                    ('copy_transaction_id', 'copy_revision_id', 'copy_type')):
                if row[field] is not None:
                    _require(row[type_field] == 'deposit')
                    self.one('transactions', 'id', row[field], type='deposit')
                    self.one('deposit_profiles', 'revision_id', row[revision_field], transaction_id=row[field])
            if row['state'] == 'consumed':
                _require(row['consumed_revision_id'] == row['current_revision_id'])
                receipt = self.one('deposit_draft_consumptions', 'operation_id', row['consumed_operation_id'],
                                   draft_id=row['id'], revision_id=row['current_revision_id'])
                _require(receipt['manifest_hash'] == revision['manifest_hash'])
                operation = self.one('deposit_operations', 'id', receipt['operation_id'])
                targets = self.get('deposit_operation_targets', 'operation_id', (operation['id'],))[operation['id']]
                _require(operation['transaction_id'] in {r['transaction_id'] for r in targets})
            else:
                _require(row['consumed_revision_id'] is None and row['consumed_operation_id'] is None)
                _require(not self.get('deposit_draft_consumptions', 'draft_id', (row['id'],))[row['id']])

    def graph(self, kind, identifier):
        key = kind, identifier
        if key not in self.graphs:
            try:
                graph = collect(self.db, _evidence=self, **{kind: identifier})
            except BookflowError as exc:
                if exc.code == 'E_RECORD_NOT_FOUND':
                    raise _unresolved() from None
                raise
            # Validate the entire retained owner graph, including removed sources
            # and abandoned children, not only the occurrence that started it.
            for owner_kind, identifiers in (('draft', graph.drafts), ('selection', graph.selections)):
                headers = self.get('deposit_' + owner_kind + 's', 'id', identifiers)
                revisions = self.get('deposit_' + owner_kind + '_revisions', owner_kind + '_id', identifiers)
                sources = self.get('deposit_' + owner_kind + '_sources', owner_kind + '_id', identifiers)
                extras = self.get('deposit_draft_additional', 'draft_id', identifiers) if owner_kind == 'draft' else {}
                captured = [r for group in sources.values() for r in group]
                self.get('transactions', 'id', [r['source_transaction_id'] for r in captured])
                self.get('transaction_revisions', 'id', [r['source_revision_id'] for r in captured])
                if owner_kind == 'draft':
                    self.get('deposit_draft_row_keys', 'draft_id', identifiers)
                for owner in identifiers:
                    if (owner_kind, owner) in self.checked:
                        continue
                    _require(len(headers[owner]) == 1)
                    self.header(headers[owner][0], owner_kind)
                    for row in revisions[owner]:
                        self.revision(row, owner_kind)
                    for row in sources[owner]:
                        self.occurrence(row, owner_kind)
                    for row in extras.get(owner, ()):
                        self.occurrence(row, 'draft', additional=True)
                    self.checked.add((owner_kind, owner))
            for transaction in graph.transactions:
                self.one('transactions', 'id', transaction)
            self.graphs[key] = graph
        return self.graphs[key]


def audit_roots(db, kind, identifiers):
    """Nine-kind batch adapter returning roots or fixed relational errors per ID.

    Unknown kinds are the caller's separate nontransaction policy. All occurrences
    of a repeated row ID participate; no event or public caller roots are invented.
    """
    table, field, _ = AUDIT_ROOTS[kind]
    identifiers = tuple(dict.fromkeys(identifiers))
    evidence = _AuditEvidence(db)
    found = evidence.get(table, field, identifiers)
    initial = {'draft': set(), 'selection': set()}
    owner_kind = 'selection' if kind.startswith('deposit_selection') else 'draft'
    for group in found.values():
        for row in group:
            initial[owner_kind].add(row['id'] if kind in ('deposit_draft', 'deposit_selection') else row[owner_kind + '_id'])
    evidence.warm(initial['draft'], initial['selection'])
    result = {}
    for identifier in identifiers:
        try:
            rows = found[identifier]
            _require(bool(rows))
            owners = set()
            for row in rows:
                owner_kind = 'selection' if kind.startswith('deposit_selection') else 'draft'
                if kind in ('deposit_draft', 'deposit_selection'):
                    owner = row['id']
                else:
                    owner = row[owner_kind + '_id']
                if kind.endswith('_revision'):
                    evidence.revision(row, owner_kind)
                elif kind.endswith('_source') or kind == 'deposit_draft_additional':
                    evidence.occurrence(row, owner_kind, additional=kind == 'deposit_draft_additional')
                elif kind == 'deposit_draft_row_key':
                    evidence.row_key(row)
                elif kind == 'deposit_draft_consumption':
                    header = evidence.one('deposit_drafts', 'id', owner, state='consumed',
                        consumed_operation_id=row['operation_id'], consumed_revision_id=row['revision_id'])
                    evidence.header(header, 'draft')
                owners.add((owner_kind, owner))
            result[identifier] = set().union(*(evidence.graph(*owner).transactions for owner in sorted(owners)))
        except BookflowError as exc:
            result[identifier] = exc
    return result
