"""co0034 widens three CHECK constraints and rewrites one trigger, and adds no table.

A statement charge is stored in the sales tables an invoice already uses, so this revision
creates nothing: it widens ``transactions.ck_transaction_type``,
``sales_profiles.ck_sales_profile_type_due`` and
``custom_field_scopes.ck_custom_field_scopes_record_type``, and teaches the document-line
kind guard that a statement charge carries a ``sale`` envelope. SQLite widens a CHECK only
by rebuilding the table, so the whole question this file asks is the preservation one: a
company database already at the previous revision carries rows, local tables, indexes,
views and triggers, and all three rebuilds have to happen underneath them without touching
a byte of what is stored.

``document_lines`` itself is deliberately not rebuilt -- a charge's line is a ``sale``
envelope, which the stored kind CHECK already admits -- and neither is any settlement table,
because nothing settles a charge.
"""
import importlib
import sqlite3

from bookflow.company import schema as c
from bookflow.company.credit_schema import settlement_guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after
from tests.test_vendor_credit_migration import _at, _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0034_statement_charges')
PREVIOUS = M.down_revision


def test_the_migration_sits_on_the_chain_and_creates_nothing():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.revision in known_revisions('company') and M.revision == HEADS['company']
    assert M.down_revision in known_revisions('company')
    assert M.NEW_TABLES == () and M.DDL == () and M.OBJECTS == ()


def test_the_rewritten_guard_is_the_schema_module_and_the_widened_checks_are_the_metadata():
    superseded = _superseded_after(M.revision) & {s.split()[2] for s in M.GUARDS}
    current = [s for s in settlement_guard_statements() if s.split()[2] in M.REPLACED]
    assert tuple(s for s in M.GUARDS if s.split()[2] not in superseded) == tuple(
        s for s in current if s.split()[2] not in superseded)
    assert {s.split()[2] for s in M.GUARDS} >= set(M.REPLACED)
    # Each replacement's new text is today's metadata, and its old text is not: a pair whose
    # halves were both stale would rebuild a table into a constraint nobody wrote.
    for name, pairs in M.REPLACEMENTS.items():
        table_ = c.metadata.tables[name]
        for old, new in pairs:
            constraint = new.split('CONSTRAINT ')[1].split(' ')[0]
            expression = str(next(x for x in table_.constraints
                                  if x.name == constraint).sqltext)
            assert expression in new and expression not in old, (name, constraint)


def test_a_fresh_database_reaches_the_head_and_a_second_run_changes_nothing(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_widened_shapes_admit_a_charge_and_still_refuse_an_undeclared_type(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)

        def sql(name):
            return db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        assert "'vendor_credit', 'statement_charge'" in sql('transactions')
        assert "(type IN ('sales_receipt', 'statement_charge') AND due_date IS NULL)" in sql('sales_profiles')
        assert "'item_receipt','statement','statement_charge'" in sql('custom_field_scopes')
        assert "(type IN ('invoice', 'sales_receipt', 'statement_charge') AND NEW.kind <> 'sale')" in sql(
            'document_lines_type_insert')
        # `document_lines` itself is untouched: a charge's line is a sale envelope.
        assert 'statement_charge' not in sql('document_lines')
        # And a charge is not in the invoice/credit-memo number series, so its own numbering
        # is free of theirs.
        assert "type IN ('invoice', 'credit_memo')" in sql('uq_transaction_receivable_number')
        # Every index on all three rebuilt tables survived the rebuild.
        names = {row[0] for row in db.raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            " AND tbl_name IN ('transactions','sales_profiles','custom_field_scopes')")}
        assert names == {'ix_co17_transactions_current', 'ix_co17_transactions_type',
                         'ix_transactions_status_number', 'uq_transaction_receivable_number',
                         'ix_co17_sales_party', 'ix_co17_sales_revision',
                         'ix_custom_field_scopes_definition_id',
                         'ux_custom_field_scopes_definition_record',
                         'ux_custom_field_scopes_record_name'}

        try:
            db.raw.execute("INSERT INTO transactions (id, version, created_at, created_by,"
                           " created_via, updated_at, updated_by, updated_via, type, number,"
                           " current_revision_id, status, voided_at, voided_by, void_reason,"
                           " void_posting_batch_id) VALUES ('T9', 1, 'now', 'U', 'cli', 'now',"
                           " 'U', 'cli', 'statement_credit', 'X-1', NULL, 'posted', NULL, NULL,"
                           " NULL, NULL)")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('the document type check must refuse an undeclared type')


def test_a_charge_header_needs_a_null_due_date_and_an_invoice_still_needs_one(tmp_path):
    """The widened due-date CHECK says what it means and is not merely permissive."""
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        raw = db.raw
        raw.execute('PRAGMA foreign_keys=OFF')

        def profile(revision, kind, due):
            raw.execute("INSERT INTO sales_profiles (revision_id, transaction_id, created_at,"
                        " created_by, created_via, type, customer_id, control_account_id,"
                        " due_date, subtotal_minor_units, tax_minor_units, profile_snapshot)"
                        " VALUES (?, 'T1', 'now', 'U', 'cli', ?, 'C1', 'A1', ?, 1, 0, '{}')",
                        (revision, kind, due))

        profile('R1', 'statement_charge', None)
        for revision, kind, due in (('R2', 'statement_charge', '2026-07-01'),
                                    ('R3', 'invoice', None)):
            try:
                profile(revision, kind, due)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f'{kind} with due_date {due!r} must be refused')


def test_a_populated_previous_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'invoice post', 'U', 'user', NULL, 'cli', 'x', '1',"
                    " 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'post invoice I-1')")
        # A row in every rebuilt table, including bytes and an embedded NUL that a quote()-only
        # copy would truncate.
        raw.execute("INSERT INTO transactions (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, type, number, current_revision_id,"
                    " status, voided_at, voided_by, void_reason, void_posting_batch_id)"
                    " VALUES ('T1', 3, ?, 'U', 'cli', ?, 'U', 'cli', 'invoice',"
                    " 'I-1', 'R1', 'posted', NULL, NULL, NULL, NULL)", ('A\x00B', 'A\x00B'))
        raw.execute("INSERT INTO transaction_revisions (id, created_at, created_by, created_via,"
                    " transaction_id, revision_number, supersedes_revision_id, date, number,"
                    " name_type, name_id, memo, total_minor_units, currency, issuer_snapshot,"
                    " custom_fields_snapshot, audit_event_id)"
                    " VALUES ('R1', 'now', 'U', 'cli', 'T1', 1, NULL, '2026-05-20', 'I-1',"
                    " 'customer', 'C1', NULL, 100, 'USD', '{}', '{}', 'E1')")
        _insert(raw, 'customers', id='C1', name='Acme', name_key='acme', full_name='Acme',
                full_name_key='acme', active=1, depth=1, path='Acme', address_mode='inherit',
                contact_mode='inherit', preferred_delivery_method='none', job_status='none')
        _insert(raw, 'accounts', id='A1', name='AR', name_key='ar', full_name='AR',
                full_name_key='ar', depth=1, path='AR', type='accounts_receivable',
                currency='USD', active=1)
        raw.execute("INSERT INTO sales_profiles (revision_id, transaction_id, created_at,"
                    " created_by, created_via, type, customer_id, control_account_id, due_date,"
                    " subtotal_minor_units, tax_minor_units, profile_snapshot)"
                    " VALUES ('R1', 'T1', ?, 'U', 'cli', 'invoice', 'C1', 'A1', '2026-06-19',"
                    " 100, 0, '{}')", ('A\x00B',))
        _insert(raw, 'custom_field_defs', id='D1', name='Matter', name_key='matter', kind='text',
                active=1)
        raw.execute("INSERT INTO custom_field_scopes (id, definition_id, position, active,"
                    " record_type, definition_name, definition_name_key, definition_active)"
                    " VALUES ('S1', 'D1', 0, 1, 'invoice', ?, 'matter', 1)", ('A\x00B',))
        raw.execute('CREATE TABLE local_sc_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_sc_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_sc_expression ON local_sc_bytes (length(t))')
        raw.execute('CREATE INDEX local_sc_on_profiles ON sales_profiles (due_date, type)')
        raw.execute('CREATE INDEX local_sc_on_scopes ON custom_field_scopes (definition_name_key)')
        raw.execute('CREATE VIEW local_sc_view AS SELECT id, number FROM transactions')
        raw.execute('CREATE TRIGGER local_sc_trigger AFTER INSERT ON local_sc_bytes'
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
        for name in names:
            assert table(db.raw, name) == before[name], name
        objects_after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= objects_after
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


def test_a_competing_local_document_type_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TRIGGER local_competing_type BEFORE INSERT ON sales_profiles"
                    " WHEN NEW.type = 'invoice' BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0034' in str(exc) or 'co0034' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('an unknown competing document type guard must stop co0034')


def test_a_rewritten_document_line_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
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
            assert 'co0034' in str(exc) or 'co0034' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a locally rewritten line-kind guard must stop co0034')
