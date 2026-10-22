"""co0077 admits the sales order into the customer-work family's enumerations and guards.

The migration creates no table. It respells three CHECKs (the kind and its states on
`work_documents`, the new conversion on `work_links`, the order's invoice on
`work_billing_conversions`), four guards one table away and the one-destination index, and it
appends a cancelled quantity to `work_lines` and two preferences to `company_info`. These tests ask
what co0055's asked: does the shipped schema carry the widened objects, does a populated database
keep every value and every local object, does it stop rather than guess at a constraint it does not
recognize, and -- company migrations being forward-only -- does going down refuse rather than
half-undo.
"""
import importlib
import sqlite3

import pytest

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import preserved, table
from tests.test_bill_payment_migration import _rebuilt_since
from tests.test_estimate_void_migration import _at, _chain

M = importlib.import_module('bookflow.storage.company_migrations.versions.0077_sales_orders')

KIND = "kind IN ('proposal','estimate','work_order','time_activity','sales_order')"
ORDER_STATES = "(kind = 'sales_order' AND status IN ('open','closed','voided'))"


def _constraint(table_, name):
    return str(next(x for x in table_.constraints if getattr(x, 'name', None) == name).sqltext)


def test_the_frozen_replacements_are_what_the_metadata_declares():
    # Its own place in the chain, not that it is the head (co0080, co0079 and co0083 came after);
    # co0083 (mileage) has since sat on top and appended its own kind; what this migration wrote is the prefix.
    assert M.revision == 'co0077' and M.revision in _chain()
    assert M.CHANGED == ('work_documents', 'work_links', 'work_billing_conversions')
    assert KIND[:-1] in _constraint(c.work_documents, 'ck_work_kind')
    assert ORDER_STATES in _constraint(c.work_documents, 'ck_work_status')
    assert "'estimate_sales_order'" in _constraint(c.work_links, 'ck_work_link_key')
    assert "'sales_order_invoice'" in _constraint(c.work_billing_conversions, 'ck_work_billing_relation')
    assert 'cancelled_quantity_microunits' in c.work_lines.c
    assert {'sales_orders_enabled', 'available_deducts_sales_orders'} <= set(c.company_info.c.keys())
    for name, (before, after) in M.TRIGGERS.items():
        assert before != after and name in before and 'sales_order' in after and 'sales_order' not in before


def test_a_fresh_database_reaches_the_head_and_the_guards_admit_an_order_and_nothing_else(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')

        def document(identity, kind, status):
            raw.execute("INSERT INTO work_documents (id, version, created_at, created_by, created_via, updated_at,"
                        " updated_by, updated_via, kind, number, current_revision_id, status, active, estimate_group_id)"
                        " VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', ?, ?, 'R'||?, ?, 1, NULL)",
                        (identity, kind, 'N-' + identity, identity, status))

        for status in ('open', 'closed', 'voided'):
            document('S' + status, 'sales_order', status)
        for identity, kind, status in (('S1', 'sales_order', 'draft'), ('S2', 'sales_order', 'complete'),
                                       ('S3', 'sales_order', 'accepted'), ('W1', 'work_order', 'closed')):
            with pytest.raises(sqlite3.IntegrityError):
                document(identity, kind, status)
        sql = dict(raw.execute("SELECT name, sql FROM sqlite_schema WHERE name IN ('uq_work_estimate_order',"
                               " 'work_line_cancellation_kind', 'company_info', 'work_lines')").fetchall())
        assert "relation IN ('estimate_work_order','estimate_sales_order')" in sql['uq_work_estimate_order']
        assert "d.kind NOT IN ('work_order','sales_order')" in sql['work_line_cancellation_kind']
        assert 'sales_orders_enabled' in sql['company_info'] and 'cancelled_quantity_microunits' in sql['work_lines']
        assert raw.execute('SELECT sales_orders_enabled, available_deducts_sales_orders FROM company_info').fetchall() in ([], [(1, 1)])


def test_a_populated_co0072_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0072')
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind, on_behalf_of, interface,"
                    " client_name, client_version, client_host, session_id, request_id, idempotency_key, reason,"
                    " directive_id, directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'work-order create', 'U', 'user', NULL, 'cli', 'x', '1', 'h', 'S1', 'Q1',"
                    " NULL, NULL, NULL, NULL, NULL, NULL, 'work order W-1')")
        raw.execute("INSERT INTO work_documents (id, version, created_at, created_by, created_via, updated_at,"
                    " updated_by, updated_via, kind, number, current_revision_id, status, active, estimate_group_id)"
                    " VALUES ('D1', 3, ?, 'U', 'cli', 'now', 'U', 'cli', 'work_order', 'W-1', 'R1', 'draft', 1, NULL)",
                    ('A\x00B',))
        raw.execute("INSERT INTO customers (id, version, created_at, created_by, created_via, updated_at, updated_by,"
                    " updated_via, active, name, name_key, full_name, full_name_key, depth, path, job_status,"
                    " address_mode, contact_mode) VALUES ('C1', 1, 'now', 'U', 'cli', 'now', 'U', 'cli', 1, 'Ada',"
                    " 'ada', 'Ada', 'ada', 1, 'C1', 'none', 'own', 'own')")
        raw.execute("INSERT INTO work_revisions (id, document_id, revision_number, supersedes_revision_id, date, number,"
                    " title, status, active, customer_id, currency, net_minor_units, tax_minor_units, gross_minor_units,"
                    " accepted_revision_id, accepted_at, accepted_by, decision_note, facts_snapshot,"
                    " custom_fields_snapshot, audit_event_id, created_at, created_by, created_via)"
                    " VALUES ('R1', 'D1', 1, NULL, '2026-07-01', 'W-1', 'Re-pipe', 'draft', 1, 'C1', 'USD', 100, 0, 100,"
                    " NULL, NULL, NULL, NULL, '{}', '{}', 'E1', 'now', 'U', 'cli')")
        raw.execute('CREATE TABLE local_order_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB)')
        raw.execute('INSERT INTO local_order_bytes VALUES (7, ?, ?)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_order_on_documents ON work_documents (number, kind)')
        raw.execute('CREATE INDEX local_order_on_links ON work_links (relation)')
        raw.execute('CREATE VIEW local_order_view AS SELECT id, relation FROM work_links')
        raw.execute("CREATE TRIGGER local_order_trigger AFTER INSERT ON work_links BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        rebuilt = set(M.CHANGED) | set(M.TRIGGERS) | _rebuilt_since('co0072')
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if row[1] not in rebuilt}
    raw.close()
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0072', HEADS['company'])
        assert {name: preserved(db.raw, name, before[name]) for name in names} == before, 'a value changed'
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall())
        assert objects <= after, 'a local table, index, view or trigger was lost'
        assert {'local_order_on_links', 'local_order_view', 'local_order_trigger'} <= {row[1] for row in after}
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


def test_going_down_refuses_rather_than_half_undoing(tmp_path):
    """Company migrations are forward-only: the way back from co0077 is the backup the runner took."""
    path = tmp_path / 'company.db'
    _at(path, 'co0072')
    with open_database(path, writable=True) as db:
        migrate_to_head(db, 'company', tmp_path / 'backups')
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as refused:
            command.downgrade(_config('company', db.conn), 'co0072')
        assert 'forward-only' in str(refused.value) or 'forward-only' in str(refused.value.__cause__)
        # Nothing is undone: the database stays at the head (co0077 then, co0080 since).
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == (HEADS['company'],)
    assert list((tmp_path / 'backups').glob('*-from-co0072.db')), 'the runner kept the way back'


@pytest.mark.parametrize('name', sorted(M.TRIGGERS))
def test_a_rewritten_guard_stops_the_migration_rather_than_being_guessed_at(tmp_path, name):
    path = tmp_path / 'company.db'
    _at(path, 'co0072')
    with sqlite3.connect(path) as raw:
        kind, target = raw.execute('SELECT type, tbl_name FROM sqlite_schema WHERE name=?', (name,)).fetchone()
        raw.execute(f'DROP {kind.upper()} ' + name)
        if kind == 'trigger':
            raw.execute('CREATE TRIGGER ' + name + ' BEFORE INSERT ON ' + target + ' BEGIN SELECT 1; END')
        else:
            raw.execute('CREATE UNIQUE INDEX ' + name + ' ON ' + target + ' (source_document_id)')
        raw.commit()
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert 'co0077' in str(caught.value) or 'co0077' in str(caught.value.__cause__)
