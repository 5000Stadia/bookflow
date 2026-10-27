"""Read a company's stored reconciliation state into the Snapshot every read runs against.

`preparation.snapshot` takes rows, a source graph and an authority set, and is the only door to
every reconciliation read and every command in this family. This module is what opens it from a
database, and it is the only place that does.

Two things shape it. The scope is an account, not a company, because a document the adapters
cannot represent must cost its own account its reconciliation and not every other account's --
`prove` demands that every statement leg in the graph it is handed be represented, so a graph
drawn company-wide would let one unadaptable document refuse the whole file. And no capture is
re-proved here: a stored opening or certificate says what was true at its cutoff, which the
ledger underneath is expected to move away from, so it is proven against the live graph where it
is written and against its own stored rows where it is read.
"""
from types import SimpleNamespace

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company import reconciliation_materialization as materialization
from bookflow.company import reconciliation_preparation as preparation
from bookflow.company.reconciliation_schema import MUTABLE

MUTABLE_SHORT = frozenset(name.removeprefix('reconciliation_') for name in MUTABLE)

PREFIX = 'reconciliation_'
# Where a reconciliation row says which account it belongs to. Everything else is reached from
# these by following the schema's own foreign keys, so a table added later needs an entry here
# only if it is owned by an account directly.
ACCOUNT_OWNED = ('accounts', 'openings', 'certificates', 'claims', 'drafts', 'draft_revisions',
                 'active_certificates', 'opening_members', 'certificate_members', 'draft_members',
                 'event_accounts', 'attempt_seeds', 'attempt_certificates', 'attempt_members',
                 'report_presets')


def _tables():
    return {n.removeprefix(PREFIX): t for n, t in c.metadata.tables.items() if n.startswith(PREFIX)}


def statement_transactions(db, account_id):
    """Every document that has ever posted to this account, which is what its balance is made of.

    Taken the same way `adapters.population` takes it, because a statement is written about the
    account's own postings and the bank effects that name it, and a graph missing either would
    prove a general-ledger total that is not the account's.
    """
    ids = set(db.conn.execute(sa.select(c.posting_lines.c.transaction_id)
                              .where(c.posting_lines.c.account_id == account_id)).scalars())
    ids.update(db.conn.execute(sa.select(c.bank_effect_versions.c.transaction_id)
                               .where(c.bank_effect_versions.c.account_id == account_id)).scalars())
    return ids


def _read(db, table):
    """Every row of one reconciliation table, in table order, read through the driver.

    The same values `db.conn.execute(sa.select(table)).mappings()` gives: this family's columns
    are Text and BigInteger, which SQLAlchemy hands back from SQLite unchanged. Read this way
    because its per-row result machinery was most of the time on a table of 100,000 members.
    """
    columns = [col.name for col in table.columns]
    sql = 'SELECT ' + ', '.join('"%s"' % name for name in columns) + ' FROM "%s"' % table.name
    return [dict(zip(columns, values)) for values in db.raw.execute(sql)]


def _rows_for(db, accounts, identifiers):
    """Stored rows owned by these accounts or derived from these documents, plus their closure.

    The closure follows the foreign keys the schema already declares, in both directions: a row
    is kept when something kept points at it, and when it is a child of something kept. Written
    that way rather than as a list of tables per owner because the list is the thing that goes
    stale -- `validate` proves every declared foreign key, so a table this did not think to name
    would surface as a missing owner rather than as a scoping bug anyone could read.
    """
    tables = _tables()
    rows = {name: _read(db, t) for name, t in tables.items()}
    keep = {name: set() for name in tables}
    primary = {name: [col.name for col in t.primary_key.columns] for name, t in tables.items()}

    def identity(name, row):
        return tuple(row[col] for col in primary[name])

    indexed = {name: {identity(name, row): row for row in values} for name, values in rows.items()}
    for name in ACCOUNT_OWNED:
        for key, row in indexed[name].items():
            if row['account_id'] in accounts:
                keep[name].add(key)
    for key, row in indexed['keys'].items():
        if row['transaction_id'] in identifiers:
            keep['keys'].add(key)
    # Foreign keys inside this family, as the schema declares them: (table, columns) -> (target, columns).
    edges = []
    for name, table in tables.items():
        for constraint in table.foreign_key_constraints:
            target = constraint.referred_table.name
            if not target.startswith(PREFIX):
                continue
            edges.append((name, tuple(k.parent.name for k in constraint.elements),
                          target.removeprefix(PREFIX), tuple(k.column.name for k in constraint.elements)))
    # Each edge's target rows grouped by the columns it points at, built once: the closure asks
    # "which rows hold this value" instead of scanning the target table per kept row.
    pointed = {}
    for _, _, target, remote in edges:
        if (target, remote) not in pointed:
            groups = pointed[target, remote] = {}
            for key, row in indexed[target].items():
                groups.setdefault(tuple(row[col] for col in remote), []).append(key)
    # And each edge's source rows grouped by the columns they point with, for the other
    # direction: a kept row's own children belong with it, because what `validate` checks about
    # an operation or an attempt is the completeness of its items.
    pointing = {}
    for name, columns, _, _ in edges:
        if (name, columns) not in pointing:
            groups = pointing[name, columns] = {}
            for key, row in indexed[name].items():
                value = tuple(row[col] for col in columns)
                if None not in value:
                    groups.setdefault(value, []).append(key)
    outward, inward = {}, {}
    for edge in edges:
        outward.setdefault(edge[0], []).append(edge)
        inward.setdefault(edge[2], []).append(edge)
    # The closure is the least set closed under both directions, so one pass over a work list
    # reaches the same rows the repeated sweeps did, each kept row visited once.
    work = [(name, key) for name in tables for key in keep[name]]
    while work:
        name, key = work.pop()
        row = indexed[name][key]
        for _, columns, target, remote in outward.get(name, ()):
            value = tuple(row[col] for col in columns)
            if None in value:
                continue
            for found in pointed[target, remote].get(value, ()):
                if found not in keep[target]:
                    keep[target].add(found)
                    work.append((target, found))
        for source, columns, _, remote in inward.get(name, ()):
            value = tuple(row[col] for col in remote)
            if None in value:
                continue
            for found in pointing[source, columns].get(value, ()):
                if found not in keep[source]:
                    keep[source].add(found)
                    work.append((source, found))
    return {name: [row for key, row in indexed[name].items() if key in keep[name]] for name in tables}


# Ledger tables only `validate`'s foreign-key proof reads. Every reconciliation key into them
# names the target's `id`, so the rows that can answer it are the rows with those ids; the
# other referenced tables (company_info, accounts, transactions, attachments, attachment links,
# classes) are read by value elsewhere and stay whole.
KEYED_ONLY = ('audit_events', 'bank_effect_keys', 'bank_effect_versions', 'document_line_identities',
              'posting_batches', 'posting_line_sources', 'posting_lines', 'transaction_revisions')


def _pointed_ids(rows):
    """The `id` values the stored rows point at in each KEYED_ONLY table, or None for a table
    some key points at with a value that is not text (it is then read whole, as before)."""
    wanted = {name: set() for name in KEYED_ONLY}
    for name, table in _tables().items():
        for constraint in table.foreign_key_constraints:
            target = constraint.referred_table.name
            if target not in wanted:
                continue
            columns = [e.parent.name for e in constraint.elements]
            position = [e.column.name for e in constraint.elements].index('id')
            for row in rows[name]:
                value = tuple(row[col] for col in columns)
                if None in value or wanted[target] is None:
                    continue
                if type(value[position]) is not str:
                    wanted[target] = None
                    continue
                wanted[target].add(value[position])
    return wanted


def referenced_rows(db, rows=None):
    """Every non-reconciliation table a reconciliation foreign key points at, plus company_info.

    `validate` proves those keys itself rather than trusting that the database had them switched
    on, so it needs the rows to prove them against. Given the stored rows, a KEYED_ONLY table is
    read only for the ids they point at: a key is found among those rows exactly when it is found
    in the whole table, because the row that matches it has one of those ids.
    """
    names = {fk.column.table.name for n, t in c.metadata.tables.items() if n.startswith(PREFIX)
             for fk in t.foreign_keys if not fk.column.table.name.startswith(PREFIX)}
    names.add('company_info')
    wanted = _pointed_ids(rows) if rows is not None else {}
    result = {}
    for n in sorted(names):
        table = c.metadata.tables[n]
        ids = wanted.get(n)
        if ids is None:
            result[n] = [dict(v) for v in db.conn.execute(sa.select(table)).mappings()]
            continue
        ordered = sorted(ids)
        result[n] = []
        for offset in range(0, len(ordered), 500):
            result[n].extend(dict(v) for v in db.conn.execute(
                sa.select(table).where(table.c.id.in_(ordered[offset:offset + 500]))).mappings())
    return result


# How the last load in this process was proved ('full' or 'scoped'), why, and over how many
# documents: read by the tests and the bench, never by a command.
last_proof = {}


def gather(db, account_id):
    """The accounts, documents and stored rows one account's snapshot is made of.

    Widens to a fixed point rather than assuming one account is self-contained: if the closure
    reaches another account's rows -- which a single operation spanning two accounts would do --
    that account's documents join the graph too, so the population proved is always the whole of
    what the rows describe.
    """
    accounts, identifiers, rows = {account_id}, set(), None
    while True:
        identifiers = set().union(*(statement_transactions(db, account) for account in accounts))
        rows = _rows_for(db, accounts, identifiers)
        found = accounts | {row['account_id'] for name in ACCOUNT_OWNED for row in rows[name]}
        found.update(row['account_id'] for row in rows['effect_versions'])
        if found == accounts:
            break
        accounts = found
    return accounts, identifiers, rows


def load(s, account_id, *, prove_captures=(), advance=False, full=False):
    """The Snapshot for one account, as the stored rows have it, proved.

    With a holding verified watermark (`reconciliation_watermark`) only what was written after it
    is proved again, with every older closure it touches; a refusal there falls back to the full
    proof, which decides. With `advance` -- a load inside a command's own write transaction -- a
    successful proof moves the account's watermark to the state just proved. `full` proves in full
    whatever the watermark says.
    """
    from bookflow.company import reconciliation_watermark as watermark
    db = s.company
    materialization.assert_materialized(db)
    accounts, identifiers, rows = gather(db, account_id)
    authorized = adapters.authority(s, identifiers)
    try:
        reason, scope = ('asked', None) if full else watermark.state(db, account_id, accounts, identifiers)
    except Exception:  # any doubt about the watermark is a full proof
        reason, scope = 'broken', None
    found = None
    if scope is not None:
        try:
            found = _scoped(db, rows, identifiers, scope, prove_captures, authorized)
            last_proof.update(proof='scoped', reason=reason, documents=len(scope.documents))
        except Exception as exc:
            # A scoped refusal is never the answer: the full proof decides, refusing or not.
            found = None
            reason = 'scoped refusal: ' + str(getattr(exc, 'details', None) or exc)
    if found is None:
        last_proof.update(proof='full', reason=reason, documents=len(identifiers))
        found = _full(db, rows, identifiers, prove_captures, authorized)
    if advance and db.write_transaction:
        current = scope.current if scope is not None else watermark.fingerprints(db, identifiers)
        watermark.advance(db, account_id, accounts, current)
    return found


def _full(db, rows, identifiers, prove_captures, authorized):
    source = adapters.graph(SimpleNamespace(company=db), identifiers)
    # Every argument below was built by this call and is referenced nowhere else, so the
    # snapshot can own it without the defensive copy `preparation.snapshot` makes for a caller
    # that keeps its arguments.
    return preparation.owned_snapshot(rows, source=source,
                                      captured_graphs={identity: source for identity in prove_captures},
                                      referenced_rows=referenced_rows(db, rows),
                                      authority_transactions=authorized)


def prove_whole(db, rows, identifiers):
    """The full proof of these rows, with no record-level authority (`reconciliation_watermark.verify`)."""
    source = adapters.graph(SimpleNamespace(company=db), identifiers)
    required = preparation.authority_required(rows, source, {})
    return preparation.owned_snapshot(rows, source=source, captured_graphs={},
                                      referenced_rows=referenced_rows(db, rows), authority_transactions=required)


def _scoped(db, rows, identifiers, scope, prove_captures, authorized):
    """The same snapshot, proved on top of a holding watermark (`reconciliation_watermark`)."""
    from bookflow.company import reconciliation_watermark as watermark
    from bookflow.company.reconciliation_storage_validation import Suffix
    documents = scope.documents
    source = adapters.graph(SimpleNamespace(company=db), identifiers, only=documents,
                            verified=watermark.verified(db, rows, identifiers, documents))
    suffix = Suffix(scope.written)
    # The foreign keys a scoped validation proves into the keyed-only ledger tables are those of
    # the rows it re-proves, so only their targets are read.
    tables = _tables()
    proving = {}
    for name, values in rows.items():
        written = suffix.rows.get(name)
        columns = [col.name for col in tables[name].primary_key.columns]
        proving[name] = (list(values) if name in MUTABLE_SHORT else
                         [row for row in values if written and tuple(row[col] for col in columns) in written])
    references = referenced_rows(db, proving)
    return preparation.owned_snapshot(rows, source=source,
                                      captured_graphs={identity: source for identity in prove_captures},
                                      referenced_rows=references, authority_transactions=authorized,
                                      suffix=suffix)


def prove_written(s, account_id, operation_id):
    """Reload the account and prove every capture this operation stored against the live ledger.

    New captures are the operation targets authored by its audit event. Referenced historical
    openings remain validated against their stored rows, not today's changed ledger.
    `validate` separately requires the targets to be the complete set --
    so a writer cannot store a capture and leave it unproven by forgetting to name it.
    """
    written = set()
    operation = c.reconciliation_operations
    audit_event_id = s.company.conn.execute(
        sa.select(operation.c.audit_event_id).where(operation.c.id == operation_id)).scalar_one()
    for name, field in (('operation_openings', 'opening_id'),
                        ('operation_certificates', 'certificate_id')):
        table = c.metadata.tables[PREFIX + name]
        capture = c.metadata.tables[PREFIX + name.removeprefix('operation_')]
        written.update(s.company.conn.execute(
            sa.select(table.c[field]).join(capture, capture.c.id == table.c[field])
            .where(table.c.operation_id == operation_id,
                   capture.c.audit_event_id == audit_event_id)).scalars())
    return load(s, account_id, prove_captures=written, advance=True)
