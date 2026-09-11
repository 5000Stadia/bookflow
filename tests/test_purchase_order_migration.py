"""co0035 adds five purchase order tables and changes nothing that was already there.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing keeping the two in step is this test compiling the metadata and comparing. The rest
of the file is the preservation question -- a company database already at the previous
revision carries rows, local tables, indexes, views and triggers, and an additive migration
has to leave every one of them untouched.

The claim worth the most here is the negative one: **no CHECK is widened and no table is
rebuilt.** A purchase order is not a ``transactions`` row, so ``ck_transaction_type`` does not
have to learn a twelfth type, ``document_lines`` does not gain a kind, and no document-type
trigger is rewritten. That is asserted directly rather than left to be inferred from the
migration having no ``REPLACEMENTS`` dictionary.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.purchase_order_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0035_purchase_orders')
PREVIOUS = M.down_revision


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module():
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    # Frozen text can only equal today's metadata for the objects no later revision rewrote.
    # Which those are is derived from the later migrations themselves, never listed here.
    superseded = _superseded_after(M.revision) & {statement.split()[2] for statement in M.GUARDS}
    assert tuple(s for s in M.GUARDS if s.split()[2] not in superseded) == tuple(
        s for s in guard_statements() if s.split()[2] not in superseded)
    assert set(M.OBJECTS) == set(M.NEW_TABLES) | {index.name for index in indexes} | {
        statement.split()[2] for statement in M.GUARDS}
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_the_revision_is_in_the_chain_and_is_the_company_head():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.revision in known_revisions('company')
    assert M.down_revision in known_revisions('company')
    assert HEADS['company'] == M.revision


def test_the_migration_adds_only_and_rebuilds_nothing():
    """An additive revision declares no rebuild, no replacement and no widened CHECK."""
    assert not getattr(M, 'CHANGED', ())
    assert not getattr(M, 'REPLACED', ())
    assert not getattr(M, 'REPLACEMENTS', {})
    assert not getattr(M, 'TRIGGERS', {})
    assert all(statement.startswith('CREATE ') for statement in (*M.DDL, *M.GUARDS))


def test_a_fresh_database_reaches_the_head_with_empty_purchase_order_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_order_is_not_a_transaction_and_nothing_financial_was_widened(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        def sql(name):
            return db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        migrate_to_head(db, 'company', None)
        # The document-type enumeration and the envelope-kind enumeration are untouched: an
        # order is neither a transaction nor a document line.
        assert 'purchase_order' not in sql('transactions')
        assert 'purchase_order' not in sql('document_lines')
        assert 'purchase_order' not in sql('document_lines_type_insert')
        header = sql('purchase_order_conversions')
        assert "CHECK (destination_type = 'bill')" in header
        assert 'REFERENCES transactions (id, type)' in header
        assert 'UNIQUE (source_document_id)' in header
        assert 'UNIQUE (destination_transaction_id)' in header
        # An order can only hold a state the document declares.
        try:
            db.raw.execute(
                "INSERT INTO purchase_orders (id, version, created_at, created_by, created_via,"
                " updated_at, updated_by, updated_via, number, current_revision_id, status,"
                " voided_at, voided_by, void_reason) VALUES ('P9', 1, 'now', 'U', 'cli', 'now',"
                " 'U', 'cli', 'PO-9', 'R9', 'received', NULL, NULL, NULL)")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('the purchase order state check must refuse an undeclared state')


def test_a_populated_previous_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        _insert(raw, 'audit_events', id='E1', seq=1, at='now', command='bill post', actor_id='U',
                actor_kind='user', interface='cli', summary='post bill B-1')
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'bill', 'B-1', 'R1',"
                    " 'posted', NULL, NULL, NULL, NULL)", ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2026-05-20', 'B-1',"
                    " 'vendor', 'V1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        _insert(raw, 'vendors', id='V1', name='Alto', name_key='alto', active=1)
        raw.execute('CREATE TABLE local_po_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_po_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_po_expression ON local_po_bytes (length(t))')
        raw.execute('CREATE INDEX local_po_on_transactions ON transactions (status, type)')
        raw.execute('CREATE VIEW local_po_view AS SELECT id, number FROM transactions')
        raw.execute('CREATE TRIGGER local_po_trigger AFTER INSERT ON local_po_bytes'
                    ' BEGIN SELECT 1; END')
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        rebuilt = _rebuilt_since(PREVIOUS)
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
        # Byte for byte, including the embedded NUL and the raw blob, at the same rowids.
        for name in names:
            assert table(db.raw, name) == before[name], name
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        assert db.raw.execute('SELECT count(*) FROM purchase_orders').fetchone() == (0,)


def test_a_local_object_holding_a_reserved_name_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY)')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0035' in str(exc) or 'co0035' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a reserved purchase order storage name must stop co0035')
