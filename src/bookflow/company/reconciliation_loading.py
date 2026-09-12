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


def _rows_for(db, accounts, identifiers):
    """Stored rows owned by these accounts or derived from these documents, plus their closure.

    The closure follows the foreign keys the schema already declares, in both directions: a row
    is kept when something kept points at it, and when it is a child of something kept. Written
    that way rather than as a list of tables per owner because the list is the thing that goes
    stale -- `validate` proves every declared foreign key, so a table this did not think to name
    would surface as a missing owner rather than as a scoping bug anyone could read.
    """
    tables = _tables()
    rows = {name: [dict(v) for v in db.conn.execute(sa.select(t)).mappings()] for name, t in tables.items()}
    keep = {name: set() for name in tables}

    def identity(name, row):
        return tuple(row[col.name] for col in tables[name].primary_key.columns)

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
    while True:
        grew = False
        for name, columns, target, remote in edges:
            wanted = set()
            for key in keep[name]:
                row = indexed[name][key]
                value = tuple(row[col] for col in columns)
                if None not in value:
                    wanted.add((target, remote, value))
            for other, remote_columns, value in wanted:
                for key, row in indexed[other].items():
                    if tuple(row[col] for col in remote_columns) == value and key not in keep[other]:
                        keep[other].add(key)
                        grew = True
            # The other direction: a kept row's own children belong with it, because what
            # `validate` checks about an operation or an attempt is the completeness of its items.
            held = {tuple(indexed[target][key][col] for col in remote) for key in keep[target]}
            for key, row in indexed[name].items():
                if key in keep[name]:
                    continue
                value = tuple(row[col] for col in columns)
                if None not in value and value in held:
                    keep[name].add(key)
                    grew = True
        if not grew:
            break
    return {name: [row for key, row in indexed[name].items() if key in keep[name]] for name in tables}


def referenced_rows(db):
    """Every non-reconciliation table a reconciliation foreign key points at, plus company_info.

    `validate` proves those keys itself rather than trusting that the database had them switched
    on, so it needs the rows to prove them against.
    """
    names = {fk.column.table.name for n, t in c.metadata.tables.items() if n.startswith(PREFIX)
             for fk in t.foreign_keys if not fk.column.table.name.startswith(PREFIX)}
    names.add('company_info')
    return {n: [dict(v) for v in db.conn.execute(sa.select(c.metadata.tables[n])).mappings()]
            for n in sorted(names)}


def load(s, account_id, *, prove_captures=()):
    """The Snapshot for one account, as the stored rows have it.

    Widens to a fixed point rather than assuming one account is self-contained: if the closure
    reaches another account's rows -- which a single operation spanning two accounts would do --
    that account's documents join the graph too, so the population proved is always the whole of
    what the rows describe.
    """
    db = s.company
    materialization.assert_materialized(db)
    accounts, identifiers, rows = {account_id}, set(), None
    while True:
        identifiers = set().union(*(statement_transactions(db, account) for account in accounts))
        rows = _rows_for(db, accounts, identifiers)
        found = accounts | {row['account_id'] for name in ACCOUNT_OWNED for row in rows[name]}
        found.update(row['account_id'] for row in rows['effect_versions'])
        if found == accounts:
            break
        accounts = found
    authorized = adapters.authority(s, identifiers)
    source = adapters.graph(SimpleNamespace(company=db), identifiers)
    return preparation.snapshot(rows, source=source,
                                captured_graphs={identity: source for identity in prove_captures},
                                referenced_rows=referenced_rows(db),
                                authority_transactions=authorized)


def prove_written(s, account_id, operation_id):
    """Reload the account and prove every capture this operation stored against the live ledger.

    The captures are taken from the operation's own target rows rather than from what the caller
    remembers writing, and `validate` separately requires those targets to be the complete set --
    so a writer cannot store a capture and leave it unproven by forgetting to name it.
    """
    written = set()
    for name, field in (('operation_openings', 'opening_id'),
                        ('operation_certificates', 'certificate_id')):
        table = c.metadata.tables[PREFIX + name]
        written.update(s.company.conn.execute(
            sa.select(table.c[field]).where(table.c.operation_id == operation_id)).scalars())
    return load(s, account_id, prove_captures=written)
