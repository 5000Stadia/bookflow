"""co0027 widens two CHECK constraints and two settlement tables, preserving everything else.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing that keeps the two in step is this test compiling the metadata and comparing. The
rest of the file is the preservation question -- a company database already at co0026 carries
rows, local tables, indexes, views and triggers, and this migration has to rebuild
``transactions``, ``document_lines``, ``applications`` and ``application_allocations``
underneath them without touching a byte of what is stored.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.credit_schema import guard_statements, settlement_guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import table

M = importlib.import_module('bookflow.storage.company_migrations.versions.0027_customer_credits')


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


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module():
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    shared = next(index for index in c.metadata.tables['transactions'].indexes
                  if index.name == 'uq_transaction_receivable_number')
    compiled += (str(CreateIndex(shared).compile(dialect=dialect())).strip(),)
    assert M.DDL == compiled
    assert M.GUARDS == tuple(guard_statements()) + tuple(settlement_guard_statements())
    assert {statement.split()[2] for statement in M.GUARDS} & set(M.REPLACED) == set(M.REPLACED)
    # Every composite foreign key names a real key of its target, not an arbitrary column pair.
    for name in M.NEW_TABLES + ('applications', 'application_allocations'):
        for key in c.metadata.tables[name].foreign_key_constraints:
            target = key.referred_table
            columns = tuple(element.column.name for element in key.elements)
            allowed = [tuple(target.primary_key.columns.keys())] + [
                tuple(constraint.columns.keys()) for constraint in target.constraints
                if constraint.__class__.__name__ == 'UniqueConstraint']
            assert columns in allowed, (name, columns)


def test_a_fresh_database_reaches_the_head_with_empty_credit_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert HEADS['company'] == M.revision
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
            return db.raw.execute("SELECT sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()[0]

        assert "'bill_payment', 'credit_memo'" in sql('transactions')
        assert "'bill_payment', 'credit'" in sql('document_lines')
        assert "(type = 'credit_memo' AND NEW.kind <> 'credit')" in sql('document_lines_type_insert')
        # The settlement pair: the receipt column is nullable now, the credit column exists,
        # and both foreign keys are declared rather than left to a trigger.
        applications = sql('applications')
        assert 'source_component_key_id VARCHAR(26), ' in applications
        assert 'credit_source_key_id VARCHAR(26)' in applications
        assert 'REFERENCES credit_source_keys (transaction_id, id)' in applications
        allocations = sql('application_allocations')
        assert 'source_component_id VARCHAR(26), ' in allocations
        assert 'credit_source_component_id VARCHAR(26)' in allocations
        assert 'REFERENCES credit_components (transaction_id, revision_id, id)' in allocations
        # Every pre-existing column keeps its ordinal position; the new one is appended.
        names = [row[1] for row in db.raw.execute('PRAGMA table_xinfo(applications)')]
        assert names[-1] == 'credit_source_key_id'
        assert names[:-1] == ['id', 'kind', 'paying_transaction_id', 'paid_transaction_id',
                              'source_component_key_id', 'amount_minor_units', 'currency',
                              'effective_date', 'reverses_application_id', 'created_at',
                              'created_by', 'created_via', 'audit_event_id']
        # An invoice and a credit memo share one number series, and storage says so.
        assert "WHERE type IN ('invoice', 'credit_memo')" in sql('uq_transaction_receivable_number')


def test_a_populated_co0026_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0026')
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0026',)
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
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'invoice',"
                    " 'INV-1', 'R1', 'posted', NULL, NULL, NULL, NULL)",
                    ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2017-03-03', 'INV-1',"
                    " 'customer', 'C1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        raw.execute("INSERT INTO document_line_identities (id, created_at, created_by,"
                    " created_via, transaction_id) VALUES ('I1', 'now', 'U', 'cli', 'T1')")
        raw.execute("INSERT INTO document_lines (id, created_at, created_by, created_via,"
                    " transaction_id, revision_id, line_id, position, kind, account_id, side,"
                    " amount_minor_units, currency, account_snapshot, name_type, name_id,"
                    " party_name, class_id, class_name, description, original_minor_units,"
                    " original_currency, rate_used, rate_source)"
                    " VALUES ('L1', 'now', 'U', 'cli', 'T1', 'R1', 'I1', 1, 'sale', NULL,"
                    " NULL, NULL, 'USD', NULL, 'customer', 'C1', 'N', NULL, NULL, ?, NULL, NULL,"
                    " NULL, NULL)", (b'\x00\xff\x80',))
        raw.execute('CREATE TABLE local_credit_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_credit_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_credit_expression ON local_credit_bytes (length(t))')
        raw.execute('CREATE INDEX local_credit_on_applications ON applications (currency, kind)')
        raw.execute('CREATE VIEW local_credit_view AS SELECT id, number FROM transactions')
        raw.execute("CREATE TRIGGER local_credit_trigger AFTER INSERT ON local_credit_bytes"
                    " BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        objects = set(raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            " AND name NOT IN ('transactions', 'document_lines', 'applications',"
            " 'application_allocations') AND name NOT IN ({})".format(
                ','.join(repr(name) for name in M.REPLACED))).fetchall())

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0026', HEADS['company'])
        after = {name: table(db.raw, name) for name in names}
        # The four rebuilt tables keep every stored value; two of them also gain a column,
        # which is exactly the difference the columns list is allowed to show.
        for name in names:
            added = [column for column in after[name]['columns'] if column not in before[name]['columns']]
            assert added == [part.strip().split()[0] for part in M.ADDITIONS.get(name, ())]
            # Byte for byte, including the embedded NUL and the raw blob, and at the same
            # rowids: a rebuilt table that reordered or re-encoded a value fails here.
            assert table(db.raw, name, omit_columns=added) == before[name], name
        objects_after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= objects_after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        # The settlement pair came through the rebuild with its widened shape and nothing in
        # it. A populated settlement graph is eight composite parents deep, so the proof that
        # its rows survive is the migration's own both-direction typeof/quote/blob EXCEPT,
        # which runs on every rebuilt table and raises rather than continuing.
        for name, added in (('applications', 'credit_source_key_id'),
                            ('application_allocations', 'credit_source_component_id')):
            columns = [row[1] for row in db.raw.execute(f'PRAGMA table_xinfo({name})')]
            assert columns[-1] == added and columns[:-1] == before[name]['columns']
            assert db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)


def test_an_unexpected_settlement_guard_stops_the_migration(tmp_path):
    for name in ('document_lines_type_insert', 'applications_exact_party', 'applications_no_update'):
        path = tmp_path / (name + '.db')
        _at(path, 'co0026')
        with sqlite3.connect(path) as raw:
            raw.execute('DROP TRIGGER ' + name)
            target = 'document_lines' if name.startswith('document_lines') else 'applications'
            raw.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {target} BEGIN SELECT 1; END")
            raw.commit()
        with open_database(path, writable=True) as db:
            try:
                migrate_to_head(db, 'company', tmp_path / 'backups')
            except Exception as exc:
                assert 'co0027' in str(exc) or 'co0027' in str(getattr(exc, '__cause__', '')), name
            else:
                raise AssertionError('a rewritten settlement guard must stop co0027: ' + name)


def test_a_competing_local_guard_on_the_settlement_pair_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0026')
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TRIGGER local_settlement_rule BEFORE INSERT ON application_allocations"
                    " BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0027' in str(exc) or 'co0027' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('an unknown local settlement guard must stop co0027')
