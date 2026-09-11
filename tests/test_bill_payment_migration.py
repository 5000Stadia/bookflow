"""co0026 widens two CHECK constraints and adds four tables, preserving everything else.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing that keeps the two in step is this test compiling the metadata and comparing. The
rest of the file is the preservation question -- a company database already at co0025 carries
rows, local tables, indexes, views and triggers that this migration has to rebuild
``transactions`` and ``document_lines`` underneath without touching.
"""
import importlib
import sqlite3
from pathlib import Path

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.ap_settlement_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import preserved, table

M = importlib.import_module('bookflow.storage.company_migrations.versions.0026_bill_payments')
BILLS = importlib.import_module('bookflow.storage.company_migrations.versions.0025_bills')


def _rebuilt_since(revision):
    """Every table and trigger the migrations after `revision` rebuild or replace."""
    versions = Path(__file__).resolve().parents[1] / 'src/bookflow/storage/company_migrations/versions'
    seen, names = set(), set()
    for path in sorted(versions.glob('[0-9]*.py')):
        module = importlib.import_module('bookflow.storage.company_migrations.versions.' + path.stem)
        guards = {statement.split()[2] for statement in getattr(module, 'GUARDS', ())}
        if module.revision > revision:
            names.update(getattr(module, 'CHANGED', ()))
            names.update(getattr(module, 'REPLACED', ()))
            # A migration that rewrites a trigger names it in TRIGGERS, which carries no
            # guard contract and so cannot be folded into CHANGED or REPLACED: CHANGED
            # drives the migration's own table rebuild, and REPLACED must be a subset of
            # its GUARDS. A rewrite this cannot see reads as an object that vanished.
            names.update(getattr(module, 'TRIGGERS', ()))
            names.update(guards & seen)
        seen.update(guards)
    return names


def _at(path, revision):
    """A company database stopped part-way along the chain, the way the runner builds one."""
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        # The runner turns foreign keys off for the duration: a rebuild recreates tables whose
        # references do not exist yet, and an insert would fail on the missing target.
        db.raw.execute('PRAGMA foreign_keys=OFF')
        try:
            command.upgrade(_config('company', db.conn), revision)
            db.raw.commit()
        finally:
            db.raw.execute('PRAGMA foreign_keys=ON')


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module(tmp_path):
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    assert M.GUARDS[:-1] == tuple(guard_statements())
    assert M.GUARDS[-1].startswith('CREATE TRIGGER document_lines_type_insert ')
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_a_fresh_database_reaches_the_head_with_empty_settlement_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        # This revision is a link in the chain, not its end: a later migration is expected to
        # follow it, so asserting it is the head is what goes stale on the next one.
        assert M.revision == 'co0026' and M.down_revision == 'co0025'
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_widened_checks_admit_the_new_type_and_kind_and_refuse_anything_else(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        transactions = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='transactions'").fetchone()[0]
        lines = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='document_lines'").fetchone()[0]
        guard = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='document_lines_type_insert'").fetchone()[0]
        assert "'deposit', 'bill', 'bill_payment'" in transactions
        assert "'deposit', 'purchase', 'bill_payment'" in lines
        assert "(type = 'bill_payment' AND NEW.kind <> 'bill_payment')" in guard


def test_a_populated_co0025_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0025')
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0025',)
        # Rows in both rebuilt tables, including bytes and an embedded NUL that a
        # quote()-only copy would truncate.
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'bill post', 'U', 'user', NULL, 'cli', 'x', '1',"
                    " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post bill B-1')")
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'bill',"
                    " 'B-1', 'R1', 'posted', NULL, NULL, NULL, NULL)",
                    ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2017-03-03', 'B-1',"
                    " 'vendor', 'V1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        raw.execute("INSERT INTO document_line_identities (id, created_at, created_by,"
                    " created_via, transaction_id) VALUES ('I1', 'now', 'U', 'cli', 'T1')")
        raw.execute("INSERT INTO document_lines (id, created_at, created_by, created_via,"
                    " transaction_id, revision_id, line_id, position, kind, account_id, side,"
                    " amount_minor_units, currency, account_snapshot, name_type, name_id,"
                    " party_name, class_id, class_name, description, original_minor_units,"
                    " original_currency, rate_used, rate_source)"
                    " VALUES ('L1', 'now', 'U', 'cli', 'T1', 'R1', 'I1', 1, 'purchase', NULL,"
                    " NULL, NULL, 'USD', NULL, 'vendor', 'V1', 'N', NULL, NULL, ?, NULL, NULL,"
                    " NULL, NULL)", (b'\x00\xff\x80',))
        raw.execute('CREATE TABLE local_ap_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_ap_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_ap_expression ON local_ap_bytes (length(t))')
        raw.execute('CREATE INDEX local_ap_on_transactions ON transactions (number, type)')
        raw.execute('CREATE VIEW local_ap_view AS SELECT id, number FROM transactions')
        raw.execute("CREATE TRIGGER local_ap_trigger AFTER INSERT ON local_ap_bytes"
                    " BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        # Everything a later migration rebuilds or reissues is read from the migration
        # modules, not listed here: a hand-written set of three names is falsified by the very
        # next revision that widens a table, and silently, because the set still looks right.
        rebuilt = _rebuilt_since(M.down_revision)
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0025', HEADS['company'])
        assert {name: preserved(db.raw, name, before[name]) for name in names} == before
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


def test_an_unexpected_document_type_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0025')
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER document_lines_type_insert')
        raw.execute("CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines"
                    " BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0026' in str(exc) or 'co0026' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a rewritten document type guard must stop co0026')
