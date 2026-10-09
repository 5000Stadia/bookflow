"""co0067 adds the sales tax adjustment header and widens two CHECKs and the kind guard.

The DDL in the migration is frozen text, so this compares it with today's metadata; the rest
checks that a populated co0066 file keeps every stored row and that storage refuses what the
widened shapes still do not admit.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.ledger_schema import TRANSACTION_TYPE_CHECK
from bookflow.company.sales_tax_adjustment_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_sales_tax_payment_migration import _at

M = importlib.import_module('bookflow.storage.company_migrations.versions.0067_sales_tax_adjustments')

EVENT = ("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind, on_behalf_of, interface,"
         " client_name, client_version, client_host, session_id, request_id, idempotency_key, reason,"
         " directive_id, directive_code, source_ref, undo_of_event_id, summary) VALUES ('E1', 1, 'now', 'x',"
         " 'U', 'user', NULL, 'cli', 'x', '1', 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'x')")


def _transaction(identity, kind):
    return ("INSERT INTO transactions (id, version, created_at, created_by, created_via, updated_at,"
            " updated_by, updated_via, type, number, current_revision_id, status, voided_at, voided_by,"
            " void_reason, void_posting_batch_id) VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', ?,"
            " ?, 'R1', 'posted', NULL, NULL, NULL, NULL)", (identity, kind, identity))


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module():
    table = c.metadata.tables['sales_tax_adjustment_profiles']
    compiled = (str(CreateTable(table).compile(dialect=dialect())).strip(),) + tuple(
        str(CreateIndex(index).compile(dialect=dialect())).strip()
        for index in sorted(table.indexes, key=lambda index: index.name))
    assert M.DDL == compiled
    assert M.GUARDS == tuple(guard_statements())
    assert M.down_revision == 'co0066' and M.revision == 'co0067'  # co0068 (party merges) sits on it
    # The widened type CHECK is exactly what the ledger now declares.
    assert M.REPLACEMENTS['transactions'][0][1] == 'CONSTRAINT ck_transaction_type CHECK (' + TRANSACTION_TYPE_CHECK + ')'


def test_a_co0066_file_keeps_its_rows_and_admits_the_new_type_only(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0066')
    with sqlite3.connect(path) as conn:
        conn.execute(EVENT)
        before = conn.execute('SELECT * FROM audit_events').fetchall()
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0066', HEADS['company'])
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT * FROM audit_events').fetchall() == before
        guard = conn.execute("SELECT sql FROM sqlite_schema WHERE name='document_lines_type_insert'").fetchone()[0]
        assert "(type = 'sales_tax_adjustment' AND NEW.kind <> 'tax_adjustment')" in guard
        assert {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE tbl_name='sales_tax_adjustment_profiles' AND type='trigger'")} == {
            statement.split()[2] for statement in M.GUARDS}
        conn.execute(*_transaction('T2', 'sales_tax_adjustment'))
        try:
            conn.execute(*_transaction('T3', 'sales_tax_rebate'))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('an undeclared document type must be refused by storage')
