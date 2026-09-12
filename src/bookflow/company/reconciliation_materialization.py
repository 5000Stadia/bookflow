"""Write the statement effects of a posted document into reconciliation storage.

The adapters in `reconciliation_adapters` derive a document's statement effects from the
ledger every time they are asked. That derivation is the truth; this module is the only thing
that turns it into stored rows, so that a reconciliation command reads one representation
instead of re-walking the whole ledger, and so that a certificate can name the exact version
of a movement it cleared.

The two representations must agree exactly, not eventually. `reconciliation_preparation`
refuses an account whose stored current heads do not sum to the general ledger, so a stored
row that drifts is not a stale cache -- it takes the whole account out of service. Everything
here is therefore derived from the same adapters at write time and never computed a second
way: the only new information this module invents is identity (a ULID per key and per
version), and identity is the one thing the adapters do not supply.

What runs when
--------------
`drain` empties `statement_effect_pending`, which `reconciliation_materialization_schema`
fills from a SQLite trigger on every `posting_batches` insert. `core.dispatch._apply` calls it
once before a company-writing command applies, so the command sees a materialized store, and
once after, so its own writes are materialized inside the same transaction. `migrate_company`
calls it after a company reaches a new head, which is what backfills a file whose history
predates this storage: `co0044` enqueues every document that ever posted, and the drain that
follows is the backfill. Nothing else has to know.

A document the adapters cannot represent
----------------------------------------
`enumerate_graph` refuses a graph containing a producer with no adapter, or a document whose
attribution it cannot read. Such a document is skipped rather than stored, and the posting
command that produced it still succeeds: `reconciliation_adapters.population` already reports
an account holding one as unsupported, derived from the source, so refusing the write would
take away a posting the books are entitled to and give nothing back. What must never happen is
the other failure -- a document that *is* representable being quietly left out -- and that is
what the emptiness of the queue proves.
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company.reconciliation_models import (
    COMMERCIAL_PRODUCERS, DEPOSIT_PRODUCERS, FUNDING_PRODUCERS)
from bookflow.company.reconciliation_storage_validation import canonical
from bookflow.core.ids import new_id

# Insertion order. `reconciliation_effect_versions` and its two subtype tables point at each
# other through deferred foreign keys, so both sides are inserted before the enclosing
# transaction commits; everything else is ordered by its immediate owner, and `effect_sources`
# follows `effect_legs` because its own INSERT trigger looks the leg up.
ORDER = ('keys', 'effect_versions', 'commercial_versions', 'deposit_versions',
         'effect_legs', 'effect_sources')

# How many documents share one source graph load. `adapters.graph` issues a fixed number of
# queries per call whatever the batch size, so a backfill of ten thousand documents costs a
# hundred passes rather than ten thousand.
BATCH = 100

# Everything `enumerate_graph` raises when a document is not representable. `Corrupt` and the
# lookup errors mean the ledger does not say what the adapters need; `Unsupported` means it
# says something they have no rule for. Neither is repairable from here.
UNREPRESENTABLE = (adapters.Unsupported, adapters.Corrupt, KeyError, ValueError, TypeError,
                   IndexError, StopIteration)


def _component(ref):
    return (ref.producer, ref.transaction_id, ref.role, ref.component_id)


def _stored_component(row):
    """The component a stored key names, in the shape the adapters' reference uses.

    A deposit names a bank effect key, a funding document names itself -- its statement line is
    the document, not a row on it -- and every other producer names the commercial line the
    movement was entered on.
    """
    if row['producer'] in DEPOSIT_PRODUCERS:
        component = row['deposit_key_id']
    elif row['producer'] in FUNDING_PRODUCERS:
        component = row['transaction_id']
    else:
        component = row['commercial_line_id']
    return (row['producer'], row['transaction_id'], row['role'], component)


def _key_row(key_id, ref):
    return dict(id=key_id, producer=ref.producer, transaction_id=ref.transaction_id, role=ref.role,
                commercial_line_id=ref.component_id if ref.producer in COMMERCIAL_PRODUCERS else None,
                deposit_key_id=ref.component_id if ref.producer in DEPOSIT_PRODUCERS else None)


def pending_documents(db, limit=None):
    """Documents whose posting writes have not been reflected in storage, oldest id first."""
    query = sa.select(c.statement_effect_pending.c.transaction_id).order_by(
        c.statement_effect_pending.c.transaction_id)
    if limit is not None:
        query = query.limit(limit)
    return [row[0] for row in db.conn.execute(query)]


def assert_materialized(db):
    """Refuse to read statement effects while a posting write is still owed to storage.

    A non-empty queue means a posting batch reached the database without the drain that every
    command runs. The stored effects are then a partial view of the ledger, and a partial view
    is the one thing statement reconciliation may never present.

    No reconciliation command is registered yet, so nothing in production reads these rows and
    nothing calls this. The row that adds the snapshot loader calls it before it loads, which
    is the only place a stale store can reach a person.
    """
    from bookflow.company.reconciliation_preparation import ReconciliationError
    if pending_documents(db, limit=1):
        raise ReconciliationError('E_RECONCILIATION_SOURCE_INVALID')


def _representable(source, identifiers):
    """`(graph, history, current)` for the documents in `identifiers` that adapt.

    `enumerate_graph` is all-or-nothing over the graph it is given, which is what makes it
    safe: it refuses rather than returning a population missing an item. One unrepresentable
    document must not therefore cost its neighbours their storage, so a refused batch is
    halved and retried until the refusal is isolated to the single document that owns it.
    Bisecting reuses the real dispatch loop; a tolerant copy of it here would be a second
    producer registry to keep in step, and this codebase has paid for that mistake often.
    """
    graph = adapters.graph(source, identifiers)
    try:
        history, current = adapters.enumerate_graph(graph)
    except UNREPRESENTABLE:
        if len(identifiers) == 1:
            return []
        middle = len(identifiers) // 2
        return (_representable(source, identifiers[:middle])
                + _representable(source, identifiers[middle:]))
    return [(graph, history, current)]


def _existing(db, identifiers):
    keys, versions, heads = {}, {}, {}
    for offset in range(0, len(identifiers), 200):
        window = identifiers[offset:offset + 200]
        for row in db.conn.execute(sa.select(c.reconciliation_keys)
                                   .where(c.reconciliation_keys.c.transaction_id.in_(window))).mappings():
            keys[_stored_component(row)] = row['id']
        for row in db.conn.execute(
                sa.select(c.reconciliation_effect_versions.c.id, c.reconciliation_effect_versions.c.key_id,
                          c.reconciliation_effect_versions.c.source_version)
                .where(c.reconciliation_effect_versions.c.transaction_id.in_(window))).mappings():
            versions[row['key_id'], row['source_version']] = row['id']
        for row in db.conn.execute(
                sa.select(c.reconciliation_effect_heads)
                .join(c.reconciliation_keys, c.reconciliation_keys.c.id == c.reconciliation_effect_heads.c.key_id)
                .where(c.reconciliation_keys.c.transaction_id.in_(window))).mappings():
            heads[row['key_id']] = row['version_id']
    return keys, versions, heads


def _version_row(graph, value, identity, key_id):
    """The stored row for one derived version. Every field comes from the adapter or the revision.

    `display_snapshot` and `provenance_snapshot` are captured here rather than rebuilt on
    read, because what a statement shows about a movement is what the document said when it
    posted -- the number, the memo, the payees and the issuer of that revision -- and a later
    correction to any of them must not silently rewrite a certified statement line.
    """
    revision = graph.by_id('transaction_revisions')[value.revision_id]
    producer = value.ref.producer
    return dict(
        id=identity, key_id=key_id, transaction_id=value.ref.transaction_id, producer=value.ref.producer,
        source_version=value.version_id, revision_id=value.revision_id,
        business_batch_id=value.business_batch_id, transition_batch_id=value.transition_batch_id,
        source_audit_event_id=value.audit_event_id, account_id=value.account_id,
        account_type=value.account_type, currency=value.currency, effective_date=value.effective_date,
        signed_debit=value.signed_debit, active=int(value.active), format_version=1,
        movement_snapshot=canonical(value.movement_key.model_dump(mode='json')),
        display_snapshot=canonical(dict(format=1, number=value.number, memo=value.memo,
                                        payees=list(value.payees),
                                        issuer=json.loads(revision['issuer_snapshot']),
                                        custom_fields=json.loads(revision['custom_fields_snapshot']))),
        provenance_snapshot=canonical(dict(format=1, document_line_ids=list(value.document_line_ids),
                                           rows=list(value.provenance))),
        commercial_link_id=identity if producer in COMMERCIAL_PRODUCERS else None,
        deposit_link_id=identity if producer in DEPOSIT_PRODUCERS else None)


def rows_for(db, identifiers):
    """The rows that must be added for these documents, and the heads they move.

    Returns `(rows, heads)`. Nothing is written; `_apply_rows` owns that. Keeping derivation
    separate from insertion is what lets a test compare a freshly derived expectation against
    what is stored without a second implementation of either half.
    """
    identifiers = sorted(set(identifiers))
    keys, versions, heads = _existing(db, identifiers)
    rows = {name: [] for name in ORDER}
    moved = {}
    for graph, history, current in _representable(SimpleNamespace(company=db), identifiers):
        derived = {}
        for value in history:
            component = _component(value.ref)
            key_id = keys.get(component)
            if key_id is None:
                key_id = keys[component] = new_id()
                rows['keys'].append(_key_row(key_id, value.ref))
            identity = versions.get((key_id, value.version_id))
            if identity is None:
                identity = versions[key_id, value.version_id] = new_id()
                rows['effect_versions'].append(_version_row(graph, value, identity, key_id))
                # A funding document has no subtype row: what a commercial version pins is the
                # entered line, and what a deposit version pins is the bank effect version, and
                # a funding movement is the whole document, which the version already names.
                if value.ref.producer in DEPOSIT_PRODUCERS:
                    rows['deposit_versions'].append(dict(
                        id=identity, transaction_id=value.ref.transaction_id, producer=value.ref.producer,
                        bank_key_id=value.ref.component_id, bank_version_id=value.version_id))
                elif value.ref.producer in COMMERCIAL_PRODUCERS:
                    rows['commercial_versions'].append(dict(
                        id=identity, transaction_id=value.ref.transaction_id, producer=value.ref.producer,
                        line_id=value.ref.component_id))
                rows['effect_legs'].extend(
                    dict(version_id=identity, transaction_id=value.ref.transaction_id, posting_line_id=leg)
                    for leg in value.posting_line_ids)
                rows['effect_sources'].extend(
                    dict(version_id=identity, transaction_id=value.ref.transaction_id, source_id=source)
                    for source in value.source_ids)
            derived[value.ref, value.version_id] = (key_id, identity)
        for value in current:
            key_id, identity = derived[value.ref, value.version_id]
            if heads.get(key_id) != identity:
                moved[key_id] = identity
    return rows, moved


def _apply_rows(db, rows, moved):
    from sqlalchemy.dialects.sqlite import insert
    for name in ORDER:
        if rows[name]:
            db.conn.execute(c.metadata.tables['reconciliation_' + name].insert(), rows[name])
    for key_id, version_id in sorted(moved.items()):
        statement = insert(c.reconciliation_effect_heads).values(key_id=key_id, version_id=version_id)
        db.conn.execute(statement.on_conflict_do_update(index_elements=['key_id'],
                                                        set_=dict(version_id=version_id)))


def materialize(db, identifiers):
    """Store the statement effects of these documents. Idempotent; returns what it added."""
    rows, moved = rows_for(db, identifiers)
    _apply_rows(db, rows, moved)
    return rows, moved


def drain(db):
    """Materialize everything the queue owes, in batches, and clear what was handled.

    Every queued document is deleted whether or not it produced rows, because a document the
    adapters cannot represent owes nothing and would otherwise hold the queue open forever --
    and a queue that is never empty cannot answer the only question it exists to answer.
    """
    handled = 0
    while True:
        batch = pending_documents(db, limit=BATCH)
        if not batch:
            return handled
        materialize(db, batch)
        db.conn.execute(c.statement_effect_pending.delete()
                        .where(c.statement_effect_pending.c.transaction_id.in_(batch)))
        handled += len(batch)


def _has_queue(db):
    return db.raw.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='statement_effect_pending'").fetchone() is not None


def drain_in_command(db, *, commits=None, owner=None):
    """The single hook: bring storage level with the ledger inside the caller's transaction.

    Called by `core.dispatch._apply` around every company-writing command's apply, and by
    `migrate_company` once a company file has reached a new head. Opens its own transaction
    only when the caller has none, which is what a finalized command and a just-migrated file
    both look like; otherwise the work joins the caller's, so a rolled back command rolls back
    its materialization with it. A read-only database owes nothing and is left alone, as is one
    whose schema predates the queue.
    """
    if db is None or not db.writable or not _has_queue(db):
        return 0
    if db.write_transaction:
        return drain(db)
    if not pending_documents(db, limit=1):
        return 0
    db.raw.execute('BEGIN IMMEDIATE')
    try:
        handled = drain(db)
    except BaseException:
        if db.write_transaction:
            db.raw.execute('ROLLBACK')
        raise
    if commits is not None:
        commits.commit(db, owner)
    else:
        db.raw.execute('COMMIT')
    return handled
