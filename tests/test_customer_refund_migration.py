"""co0031 adds the refund header and its consumptions, and widens two CHECK constraints.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing that keeps the two in step is this test compiling the metadata and comparing. The
rest of the file is the preservation question -- a company database already at co0030 carries
rows, local tables, indexes, views and triggers, and this migration has to rebuild
``transactions`` and ``document_lines`` underneath them without touching a byte of what is
stored.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.credit_schema import settlement_guard_statements
from bookflow.company.refund_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_credit_memo_migration import _superseded_after

M = importlib.import_module('bookflow.storage.company_migrations.versions.0031_customer_refunds')
PREVIOUS = 'co0030'


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


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_modules():
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    # Frozen text can only equal today's metadata for the objects no later revision has
    # rewritten. Which those are is derived from the later migrations themselves, never
    # listed here: a literal list is what makes the next migration falsify this test.
    superseded = _superseded_after(M.revision)
    rewritten = next(statement for statement in settlement_guard_statements()
                     if statement.split()[2] == 'document_lines_type_insert')
    current = tuple(guard_statements()) + (rewritten,)
    assert tuple(s for s in M.GUARDS if s.split()[2] not in superseded) == tuple(
        s for s in current if s.split()[2] not in superseded)
    assert {statement.split()[2] for statement in M.GUARDS} >= set(M.REPLACED)
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_the_migration_follows_the_remittance_revision():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company')


def test_a_fresh_database_reaches_the_head_with_empty_refund_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_widened_shapes_admit_the_refund_and_refuse_anything_else(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)

        def sql(name):
            return db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        assert "'sales_tax_payment', 'customer_refund'" in sql('transactions')
        assert "'sales_tax_payment', 'refund'" in sql('document_lines')
        assert "(type = 'customer_refund' AND NEW.kind <> 'refund')" in sql(
            'document_lines_type_insert')
        header = sql('customer_refund_profiles')
        assert "CHECK (type = 'customer_refund')" in header
        assert 'REFERENCES posting_line_sources (transaction_id, id)' in header
        consumption = sql('customer_refund_consumptions')
        assert 'CONSTRAINT uq_customer_refund_release UNIQUE (reverses_consumption_id)' in consumption
        assert 'REFERENCES credit_source_keys (id)' in consumption
        assert 'REFERENCES credit_components (id)' in consumption
        # The document type check refuses a type nothing declares, in storage.
        db.raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                       " on_behalf_of, interface, client_name, client_version, client_host,"
                       " session_id, request_id, idempotency_key, reason, directive_id,"
                       " directive_code, source_ref, undo_of_event_id, summary)"
                       " VALUES ('E1', 1, 'now', 'x', 'U', 'user', NULL, 'cli', 'x', '1', 'h',"
                       " 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'x')")
        try:
            db.raw.execute("INSERT INTO transactions (id, version, created_at, created_by,"
                           " created_via, updated_at, updated_by, updated_via, type, number,"
                           " current_revision_id, status, voided_at, voided_by, void_reason,"
                           " void_posting_batch_id) VALUES ('T9', 1, 'now', 'U', 'cli', 'now',"
                           " 'U', 'cli', 'customer_rebate', 'X-1', NULL, 'posted', NULL,"
                           " NULL, NULL, NULL)")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('an undeclared document type must be refused by storage')


def test_a_populated_previous_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        # Rows in the rebuilt tables, including bytes and an embedded NUL that a quote()-only
        # copy would truncate.
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'invoice post', 'U', 'user', NULL, 'cli', 'x', '1',"
                    " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post invoice INV-1')")
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'credit_memo',"
                    " 'CM-1', 'R1', 'posted', NULL, NULL, NULL, NULL)",
                    ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2017-03-03', 'CM-1',"
                    " 'customer', 'C1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        raw.execute("INSERT INTO document_line_identities (id, created_at, created_by,"
                    " created_via, transaction_id) VALUES ('I1', 'now', 'U', 'cli', 'T1')")
        raw.execute("INSERT INTO document_lines (id, created_at, created_by, created_via,"
                    " transaction_id, revision_id, line_id, position, kind, account_id, side,"
                    " amount_minor_units, currency, account_snapshot, name_type, name_id,"
                    " party_name, class_id, class_name, description, original_minor_units,"
                    " original_currency, rate_used, rate_source)"
                    " VALUES ('L1', 'now', 'U', 'cli', 'T1', 'R1', 'I1', 1, 'credit', NULL,"
                    " NULL, NULL, 'USD', NULL, 'customer', 'C1', 'N', NULL, NULL, ?, NULL, NULL,"
                    " NULL, NULL)", (b'\x00\xff\x80',))
        raw.execute('CREATE TABLE local_refund_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_refund_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_refund_expression ON local_refund_bytes (length(t))')
        raw.execute('CREATE INDEX local_refund_on_document_lines ON document_lines (currency, kind)')
        raw.execute('CREATE VIEW local_refund_view AS SELECT id, number FROM transactions')
        raw.execute('CREATE TRIGGER local_refund_trigger AFTER INSERT ON local_refund_bytes'
                    ' BEGIN SELECT 1; END')
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        objects = set(raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            " AND name NOT IN ('transactions', 'document_lines') AND name NOT IN ({})".format(
                ','.join(repr(name) for name in M.REPLACED))).fetchall())

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
        # Byte for byte, including the embedded NUL and the raw blob, and at the same rowids:
        # a rebuilt table that reordered or re-encoded a value fails here. This revision adds
        # no column to a rebuilt table, so nothing is allowed to differ at all.
        for name in names:
            assert table(db.raw, name) == before[name], name
        objects_after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= objects_after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        assert db.raw.execute('SELECT count(*) FROM customer_refund_profiles').fetchone() == (0,)
        assert db.raw.execute('SELECT count(*) FROM customer_refund_consumptions').fetchone() == (0,)


def test_a_rewritten_document_type_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'rewritten.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER document_lines_type_insert')
        raw.execute('CREATE TRIGGER document_lines_type_insert BEFORE INSERT ON document_lines'
                    ' BEGIN SELECT 1; END')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0031' in str(exc) or 'co0031' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a rewritten document type guard must stop co0031')


def test_a_competing_local_document_type_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'competing.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TRIGGER local_type_rule BEFORE INSERT ON transactions'
                    " WHEN NEW.type = 'invoice' BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0031' in str(exc) or 'co0031' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('an unknown competing document type guard must stop co0031')


def test_a_reserved_refund_name_already_in_use_stops_the_migration(tmp_path):
    path = tmp_path / 'reserved.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE customer_refund_consumptions (id INTEGER PRIMARY KEY)')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0031' in str(exc) or 'co0031' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a reserved refund object name must stop co0031')
