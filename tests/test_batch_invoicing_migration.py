"""co0036 adds billing groups and the durable record of a batch of invoices.

The migration is additive: four new tables and two pairs of immutability triggers, and no
existing table is rebuilt, because a batch writes ordinary invoices and touches nothing about
how one is stored. The DDL in the migration is frozen text that never imports the application's
metadata, so the only thing keeping the two in step is this file compiling the metadata and
comparing byte for byte.

The rest is the preservation question. A company database already at the previous revision
carries rows, its own local tables, indexes, views and triggers, and this migration has to
arrive underneath all of it without moving a byte -- or stop, when one of the names it is about
to create is already taken by something it cannot identify.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.batch_invoicing_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.test_bill_payment_migration import _at, _rebuilt_since, _superseded_after
from tests.test_vendor_credit_migration import _insert

M = importlib.import_module('bookflow.storage.company_migrations.versions.0036_batch_invoicing')
PREVIOUS = 'co0032'


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module():
    indexes = sorted([index for name in M.NEW_TABLES for index in c.metadata.tables[name].indexes],
                     key=lambda index: index.name)
    compiled = tuple(str(CreateTable(c.metadata.tables[name]).compile(dialect=dialect())).strip()
                     for name in M.NEW_TABLES)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip() for index in indexes)
    assert M.DDL == compiled
    # Frozen text can only equal today's metadata for objects no later revision has rewritten.
    # Which those are is derived from the later migrations themselves, never listed here.
    superseded = _superseded_after(M.revision) | _rebuilt_since(M.revision)
    assert not (set(M.NEW_TABLES) & superseded), superseded
    current = tuple(guard_statements())
    assert tuple(s for s in M.GUARDS if s.split()[2] not in superseded) == tuple(
        s for s in current if s.split()[2] not in superseded)
    # Every reserved name is really an object this migration creates, and every object it
    # creates is reserved: a name missing from OBJECTS is a name the preflight would not guard.
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


def test_the_migration_follows_the_vendor_credit_revision_and_is_the_chain_head():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company')
    assert HEADS['company'] == M.revision


def test_a_batch_record_names_the_group_without_referencing_it():
    """A deleted billing group must not take a recorded batch's own history with it."""
    batches = c.metadata.tables['invoice_batches']
    referenced = {key.column.table.name for key in batches.c.billing_group_id.foreign_keys}
    assert referenced == set(), referenced
    assert batches.c.billing_group_name is not None
    members = c.metadata.tables['billing_group_members']
    # Membership, by contrast, is live state and does carry the real key, which is what makes
    # an orphan membership impossible in storage.
    assert {key.column.table.name for key in members.c.customer_id.foreign_keys} == {'customers'}
    assert {key.column.table.name for key in members.c.group_id.foreign_keys} == {'billing_groups'}


def test_a_fresh_database_reaches_the_head_with_empty_batch_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_a_recorded_batch_cannot_be_rewritten_or_deleted(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        db.raw.execute('PRAGMA foreign_keys=OFF')
        db.raw.execute(
            "INSERT INTO invoice_batches (id, created_at, created_by, created_via, audit_event_id,"
            " date, billing_group_id, billing_group_name, retry_of_batch_id, requested_count,"
            " created_count, failed_count, created_total_minor_units, currency, request_snapshot)"
            " VALUES ('B','2026-09-01T00:00:00Z','U','cli','E','2026-09-01',NULL,NULL,NULL,"
            "1,1,0,1000,'USD','{}')")
        for statement in ("UPDATE invoice_batches SET created_count = 2 WHERE id = 'B'",
                          "DELETE FROM invoice_batches WHERE id = 'B'"):
            with pytest.raises(sqlite3.IntegrityError) as refused:
                db.raw.execute(statement)
            assert 'immutable invoice batch history' in str(refused.value)


def test_the_counts_and_the_outcome_shape_are_enforced_by_storage(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        db.raw.execute('PRAGMA foreign_keys=OFF')

        def batch(**values):
            row = dict(id='B', created_at='2026-09-01T00:00:00Z', created_by='U', created_via='cli',
                       audit_event_id='E', date='2026-09-01', billing_group_id=None,
                       billing_group_name=None, retry_of_batch_id=None, requested_count=2,
                       created_count=1, failed_count=1, created_total_minor_units=1000,
                       currency='USD', request_snapshot='{}')
            row.update(values)
            db.raw.execute('INSERT INTO invoice_batches ({}) VALUES ({})'.format(
                ','.join(row), ','.join('?' * len(row))), tuple(row.values()))

        # Counts that do not add up, and a group named on one side only, are refused.
        with pytest.raises(sqlite3.IntegrityError):
            batch(created_count=2)
        with pytest.raises(sqlite3.IntegrityError):
            batch(billing_group_id='G')
        batch()

        def result(**values):
            row = dict(id='R', batch_id='B', position=1, customer_id='C', customer_label='Someone',
                       status='created', transaction_id='T', number='1', total_minor_units=1000,
                       currency='USD', error_code=None, error_message=None,
                       created_at='2026-09-01T00:00:00Z', created_by='U', created_via='cli')
            row.update(values)
            db.raw.execute('INSERT INTO invoice_batch_results ({}) VALUES ({})'.format(
                ','.join(row), ','.join('?' * len(row))), tuple(row.values()))

        # A created outcome without its invoice, and a failure without a code, are both refused:
        # a row that says an invoice exists has to name it.
        with pytest.raises(sqlite3.IntegrityError):
            result(transaction_id=None)
        with pytest.raises(sqlite3.IntegrityError):
            result(status='failed', transaction_id=None, number=None, total_minor_units=None,
                   currency=None)
        result()
        result(id='R2', position=2, customer_id='C2', status='failed', transaction_id=None,
               number=None, total_minor_units=None, currency=None, error_code='E_INACTIVE_REFERENCE',
               error_message='A new or changed reference must name an active record.')
        assert db.raw.execute('SELECT count(*) FROM invoice_batch_results').fetchone() == (2,)


def test_a_populated_previous_revision_upgrades_without_touching_what_is_stored(tmp_path):
    path = tmp_path / 'populated.db'
    _at(path, PREVIOUS)
    with open_database(path, writable=True) as db:
        db.raw.execute('PRAGMA foreign_keys=OFF')
        db.raw.execute('CREATE TABLE local_notes (id INTEGER PRIMARY KEY, body TEXT)')
        db.raw.execute("INSERT INTO local_notes (id, body) VALUES (1, 'kept verbatim')")
        db.raw.execute('CREATE INDEX local_notes_body ON local_notes (body)')
        db.raw.execute('CREATE VIEW local_view AS SELECT id FROM local_notes')
        db.raw.execute("CREATE TRIGGER local_guard BEFORE DELETE ON local_notes "
                       "BEGIN SELECT RAISE(ABORT, 'local'); END")
        _insert(db.raw, 'customers', id='C', name='Kept', name_key='kept', full_name='Kept',
                full_name_key='kept', active=1, depth=1, path='C', job_status='none',
                address_mode='own', contact_mode='own', preferred_delivery_method='none')
        db.raw.commit()
        before = {row[0]: row[1] for row in db.raw.execute(
            "SELECT name, sql FROM sqlite_schema WHERE name LIKE 'local%' OR name = 'customers'")}
        assert migrate_to_head(db, 'company', None) == (PREVIOUS, HEADS['company'])
        after = {row[0]: row[1] for row in db.raw.execute(
            "SELECT name, sql FROM sqlite_schema WHERE name LIKE 'local%' OR name = 'customers'")}
        assert after == before
        assert db.raw.execute('SELECT id, body FROM local_notes').fetchall() == [(1, 'kept verbatim')]
        assert db.raw.execute('SELECT name FROM customers').fetchall() == [('Kept',)]
        assert all(db.raw.execute('SELECT count(*) FROM ' + name).fetchone() == (0,)
                   for name in M.NEW_TABLES)


@pytest.mark.parametrize('name', ['billing_groups', 'invoice_batch_results',
                                  'ix_invoice_batches_group', 'invoice_batches_immutable_update'])
def test_a_name_this_migration_reserves_stops_it_rather_than_shadowing_a_local_object(tmp_path, name):
    path = tmp_path / 'collision.db'
    _at(path, PREVIOUS)
    with open_database(path, writable=True) as db:
        db.raw.execute('CREATE TABLE local_target (id INTEGER PRIMARY KEY)')
        if name.startswith('ix_'):
            db.raw.execute(f'CREATE INDEX {name} ON local_target (id)')
        elif name.endswith('_immutable_update'):
            db.raw.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON local_target "
                           "BEGIN SELECT RAISE(ABORT, 'local'); END")
        else:
            db.raw.execute(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY)')
        db.raw.commit()
        with pytest.raises(Exception) as refused:
            migrate_to_head(db, 'company', None)
        assert 'co0036' in str(refused.value) or 'E_MIGRATION_FAILED' in str(refused.value)
