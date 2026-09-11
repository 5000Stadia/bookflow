"""co0032 adds the vendor-credit header and lines and widens two CHECK constraints.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing that keeps the two in step is this test compiling the metadata and comparing. The
rest of the file is the preservation question -- a company database already at co0031 carries
rows, local tables, indexes, views and triggers, and this migration has to rebuild
``transactions`` and ``ap_source_keys`` underneath them without touching a byte of what is
stored.

``document_lines`` is deliberately not rebuilt: a vendor credit's line is a ``purchase``
envelope, which the stored kind CHECK already admits, so only the trigger pairing a document
type with its envelope kind had to learn the new pairing.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.ap_settlement_schema import guard_statements as ap_guard_statements
from bookflow.company.credit_schema import settlement_guard_statements
from bookflow.company.vendor_credit_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import preserved, table
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after

M = importlib.import_module('bookflow.storage.company_migrations.versions.0032_vendor_credits')
PREVIOUS = 'co0031'


def _insert(raw, _table, **values):
    """Insert one row, filling every NOT NULL column the caller did not name.

    The list rows here exist only so the rebuilt table's foreign keys resolve; spelling out
    every column a list has acquired since it was written is how this fixture would go stale.
    """
    row = dict(values)
    for _, column, kind, notnull, default, _pk in raw.execute(f'PRAGMA table_info({_table})'):
        if column in row or not notnull or default is not None:
            continue
        row[column] = 0 if kind.upper().startswith(('INT', 'BIG', 'BOOL')) else 'x'
    raw.execute('INSERT INTO {} ({}) VALUES ({})'.format(
        _table, ','.join(row), ','.join('?' * len(row))), tuple(row.values()))


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
    superseded = _superseded_after(M.revision) & {statement.split()[2] for statement in M.GUARDS}
    rewritten = [statement for statement in ap_guard_statements()
                 if statement.split()[2] in M.REPLACED]
    rewritten += [statement for statement in settlement_guard_statements()
                  if statement.split()[2] == 'document_lines_type_insert']
    current = tuple(guard_statements()) + tuple(rewritten)
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


def test_the_migration_follows_the_customer_refund_revision():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company')


def test_a_fresh_database_reaches_the_head_with_empty_vendor_credit_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_widened_shapes_admit_the_credit_and_refuse_anything_else(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)

        def sql(name):
            return db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        assert "'customer_refund', 'vendor_credit'" in sql('transactions')
        assert "CHECK (source_type IN ('bill_payment', 'vendor_credit'))" in sql('ap_source_keys')
        assert "(type IN ('bill', 'vendor_credit') AND NEW.kind <> 'purchase')" in sql(
            'document_lines_type_insert')
        # A source key is checked against its own declared kind, so a vendor credit can never
        # mint a source calling itself a bill payment, and the edge admits either kind.
        assert 'type = NEW.source_type' in sql('ap_source_keys_transaction_id_type')
        assert "type IN ('bill_payment', 'vendor_credit')" in sql(
            'ap_applications_source_transaction_id_type')
        # `document_lines` itself is untouched: a credit's line is a purchase envelope.
        assert "'purchase', 'bill_payment'" in sql('document_lines')
        assert 'vendor_credit' not in sql('document_lines')
        header = sql('vendor_credit_profiles')
        assert "CHECK (type = 'vendor_credit')" in header
        assert 'REFERENCES transactions (id, type)' in header

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
                           " 'U', 'cli', 'vendor_debit', 'X-1', NULL, 'posted', NULL, NULL,"
                           " NULL, NULL)")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('the document type check must refuse an undeclared type')


def test_a_populated_previous_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        # Rows in the rebuilt table, including bytes and an embedded NUL that a quote()-only
        # copy would truncate.
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'bill post', 'U', 'user', NULL, 'cli', 'x', '1',"
                    " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post bill B-1')")
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'bill_payment',"
                    " 'P-1', 'R1', 'posted', NULL, NULL, NULL, NULL)", ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2026-05-20', 'P-1',"
                    " 'vendor', 'V1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        _insert(raw, 'vendors', id='V1', name='Alto', name_key='alto', active=1)
        _insert(raw, 'accounts', id='A1', name='AP', name_key='ap', full_name='AP',
                full_name_key='ap', depth=1, path='AP', type='accounts_payable',
                currency='USD', active=1)
        raw.execute("INSERT INTO ap_source_keys (id, transaction_id, ordinal, source_type,"
                    " vendor_id, ap_account_id, currency, created_at, created_by, created_via,"
                    " audit_event_id) VALUES ('K1', 'T1', 1, 'bill_payment', 'V1', 'A1', 'USD',"
                    " ?, 'U', 'cli', 'E1')", ('A\x00B',))
        raw.execute('CREATE TABLE local_vc_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_vc_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_vc_expression ON local_vc_bytes (length(t))')
        raw.execute('CREATE INDEX local_vc_on_sources ON ap_source_keys (currency, ordinal)')
        raw.execute('CREATE VIEW local_vc_view AS SELECT id, number FROM transactions')
        raw.execute('CREATE TRIGGER local_vc_trigger AFTER INSERT ON local_vc_bytes'
                    ' BEGIN SELECT 1; END')
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        # Everything this revision and every later one rebuilds or reissues is read from the
        # migration modules, never listed here.
        rebuilt = _rebuilt_since(PREVIOUS)
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == (PREVIOUS, HEADS['company'])
        # Byte for byte, including the embedded NUL and the raw blob, and at the same rowids.
        # A later migration that widens a table adds a column, and comparing the widened
        # reading against the old one would fail on the column list alone; `preserved` omits
        # exactly the columns that did not exist then, which keeps the assertion about values.
        for name in names:
            assert preserved(db.raw, name, before[name]) == before[name], name
        objects_after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= objects_after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        assert db.raw.execute('SELECT count(*) FROM vendor_credit_profiles').fetchone() == (0,)


def test_an_unexpected_settlement_source_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER ap_source_keys_transaction_id_type')
        raw.execute("CREATE TRIGGER ap_source_keys_transaction_id_type BEFORE INSERT"
                    " ON ap_source_keys BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0032' in str(exc) or 'co0032' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a rewritten settlement source guard must stop co0032')


def test_a_competing_local_document_type_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TRIGGER local_competing_type BEFORE INSERT ON transactions"
                    " WHEN NEW.type = 'bill' BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0032' in str(exc) or 'co0032' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('an unknown competing document type guard must stop co0032')
