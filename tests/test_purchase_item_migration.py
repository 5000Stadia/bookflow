"""co0033 adds the bill's Items grid and widens the two totals its header carries.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing that keeps the two in step is this test compiling the metadata and comparing. The
rest of the file is the preservation question -- a company database already at co0032 carries
rows, local tables, indexes, views and triggers, and this migration has to rebuild
``purchase_profiles`` underneath them without touching a byte of what is stored, while giving
every existing bill the item total it actually has, which is zero.

``document_lines`` is deliberately not rebuilt and no trigger is rewritten. An item line is a
``purchase`` envelope, exactly as an expense line is, so the stored kind CHECK and the trigger
pairing a document type with its envelope kind already admit it.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import preserved, table
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0033_purchase_item_lines')
PREVIOUS = 'co0032'


def test_frozen_ddl_is_the_current_metadata(tmp_path):
    rebuilt = _rebuilt_since(M.revision)
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES if name not in rebuilt)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    superseded = _superseded_after(M.revision) & {statement.split()[2] for statement in M.GUARDS}
    assert not superseded
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES:
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_the_migration_follows_the_vendor_credit_revision():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company')
    assert HEADS['company'] == M.revision


def test_the_rebuilt_header_is_exactly_what_the_metadata_says_it_is(tmp_path):
    """The one table this revision rewrites has to come out of the rebuild as today's shape.

    SQLite's own rename quotes the table name in the stored text, which is the only difference
    a correct rebuild may leave; everything else -- the new column, its position, the two
    widened checks and their names -- has to match the compiled metadata to the byte.
    """
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        stored = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='purchase_profiles'").fetchone()[0]
    compiled = str(CreateTable(c.metadata.tables['purchase_profiles']).compile(dialect=dialect())).strip()
    assert stored.replace('CREATE TABLE "purchase_profiles"', 'CREATE TABLE purchase_profiles') == compiled
    assert 'item_total_minor_units BIGINT NOT NULL' in stored
    assert "ck_purchase_expense_total_minor_units_nonnegative CHECK (typeof(expense_total_minor_units) = 'integer' AND expense_total_minor_units >= 0)" in stored
    assert "ck_purchase_item_total_minor_units_nonnegative CHECK (typeof(item_total_minor_units) = 'integer' AND item_total_minor_units >= 0)" in stored


def test_a_fresh_database_reaches_the_head_with_empty_item_line_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_an_item_line_is_a_purchase_envelope_and_needs_no_new_document_shape(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)

        def sql(name):
            return db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        # `document_lines` itself is untouched and so is the type-to-kind guard: an item line
        # is the same envelope family a bill's expense line already used.
        assert "'purchase', 'bill_payment'" in sql('document_lines')
        assert "(type IN ('bill', 'vendor_credit') AND NEW.kind <> 'purchase')" in sql(
            'document_lines_type_insert')
        lines = sql('purchase_item_lines')
        assert 'REFERENCES purchase_profiles (transaction_id, revision_id)' in lines
        assert 'REFERENCES document_lines (transaction_id, revision_id, id)' in lines
        assert 'REFERENCES items (id)' in lines
        # The immutable guards are on the new table, both directions.
        for event in ('update', 'delete'):
            assert sql(f'purchase_item_lines_immutable_{event}').startswith(
                f'CREATE TRIGGER purchase_item_lines_immutable_{event} ')


def test_a_populated_previous_database_keeps_every_value_and_takes_a_zero_item_total(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'bill post', 'U', 'user', NULL, 'cli', 'x', '1',"
                    " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post bill B-1')")
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 1, ?, 'U', 'cli', ?, 'U', 'cli', 'bill', 'B-1', 'R1',"
                    " 'posted', NULL, NULL, NULL, NULL)", ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2026-05-20', 'B-1',"
                    " 'vendor', 'V1', NULL, 12345, 'USD', '{}', '{}', 'E1')")
        _insert(raw, 'vendors', id='V1', name='Alto', name_key='alto', active=1)
        _insert(raw, 'accounts', id='A1', name='AP', name_key='ap', full_name='AP',
                full_name_key='ap', depth=1, path='AP', type='accounts_payable',
                currency='USD', active=1)
        # A stored bill header, with bytes and an embedded NUL a quote()-only copy would lose.
        raw.execute("INSERT INTO purchase_profiles (revision_id, transaction_id, created_at,"
                    " created_by, created_via, type, vendor_id, ap_account_id, terms_id,"
                    " due_date, supplier_reference, supplier_reference_key,"
                    " expense_total_minor_units, profile_snapshot)"
                    " VALUES ('R1', 'T1', ?, 'U', 'cli', 'bill', 'V1', 'A1', NULL,"
                    " '2026-06-19', 'INV-1', 'inv-1', 12345, '{\"currency\": \"USD\"}')",
                    ('A\x00B',))
        raw.execute('CREATE TABLE local_item_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_item_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_item_expression ON local_item_bytes (length(t))')
        raw.execute('CREATE INDEX local_item_on_profiles ON purchase_profiles (due_date)')
        raw.execute('CREATE VIEW local_item_view AS SELECT revision_id FROM purchase_profiles')
        raw.execute('CREATE TRIGGER local_item_trigger AFTER INSERT ON local_item_bytes'
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
        # `preserved` omits the column this revision added, so the assertion stays about the
        # values rather than failing on the column list of the one table that was rebuilt.
        for name in names:
            assert preserved(db.raw, name, before[name]) == before[name], name
        # The rebuilt header keeps every stored value, including the embedded NUL, and takes
        # the item total an existing bill actually has: it bought no items.
        row = db.raw.execute('SELECT created_at, expense_total_minor_units,'
                             ' item_total_minor_units, supplier_reference_key'
                             ' FROM purchase_profiles').fetchone()
        assert row == ('A\x00B', 12345, 0, 'inv-1')
        objects_after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= objects_after
        # The local index, view and trigger on the rebuilt table came back verbatim.
        assert {row[0] for row in db.raw.execute(
            "SELECT name FROM sqlite_schema WHERE name LIKE 'local_item_%'")} == {
            'local_item_bytes', 'local_item_expression', 'local_item_on_profiles',
            'local_item_view', 'local_item_trigger'}
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        assert db.raw.execute('SELECT count(*) FROM purchase_item_lines').fetchone() == (0,)


def test_an_unexpected_stored_header_shape_stops_the_migration(tmp_path):
    """A header this migration does not recognise is not patched blind."""
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('ALTER TABLE purchase_profiles RENAME COLUMN expense_total_minor_units'
                    ' TO grand_total_minor_units')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0033' in str(exc) or 'co0033' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('an unrecognised purchase header must stop co0033')
