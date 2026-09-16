"""co0058 widens the refund's consumption table to admit a payment overage as a source.

The DDL in the migration is frozen text: it never imports the application's metadata, so the
only thing keeping the two in step is this test compiling the metadata and comparing. The rest
of the file is the preservation question -- a company database already at co0052 carries refund
consumptions, local tables, indexes and triggers, and this rebuild has to carry every stored
byte across and leave what it does not own exactly where it found it.
"""
import importlib
import sqlite3

from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from bookflow.company import schema as c
from bookflow.company.refund_schema import guard_statements
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, known_revisions, migrate_to_head
from tests.test_bill_payment_migration import _rebuilt_since, _superseded_after

M = importlib.import_module(
    'bookflow.storage.company_migrations.versions.0058_refund_payment_overage')
PREVIOUS = 'co0057'
TABLE = 'customer_refund_consumptions'


def _at(path, revision):
    """A company database stopped part-way along the chain, the way the runner builds one."""
    from alembic import command
    from bookflow.storage.migrate import _config
    with open_database(path, writable=True, create=True) as db:
        db.raw.execute('PRAGMA foreign_keys=OFF')
        try:
            command.upgrade(_config('company', db.conn), revision)
            db.raw.commit()
        finally:
            db.raw.execute('PRAGMA foreign_keys=ON')


def test_the_migration_follows_the_recosting_revision():
    """Derived, never a second copy of the number: the chain is the only authority."""
    assert M.down_revision == PREVIOUS
    assert M.revision in known_revisions('company') and HEADS['company'] == M.revision


def test_frozen_ddl_is_the_current_metadata_and_the_guards_are_the_schema_module():
    rebuilt = _rebuilt_since(M.revision)
    assert TABLE not in rebuilt, 'a later revision rebuilt this table; widen this test as co0031 was'
    indexes = sorted(c.metadata.tables[TABLE].indexes, key=lambda index: index.name)
    compiled = (str(CreateTable(c.metadata.tables[TABLE]).compile(dialect=dialect())).strip(),)
    compiled += tuple(str(CreateIndex(index).compile(dialect=dialect())).strip()
                      for index in indexes)
    assert M.DDL == compiled
    superseded = _superseded_after(M.revision)
    current = tuple(statement for statement in guard_statements()
                    if statement.split()[2].startswith(TABLE))
    assert tuple(s for s in M.GUARDS if s.split()[2] not in superseded) == tuple(
        s for s in current if s.split()[2] not in superseded)
    assert set(M.REPLACED) <= {statement.split()[2] for statement in M.GUARDS}
    assert set(M.COPIED) == set(c.metadata.tables[TABLE].c.keys()) - {
        'payment_source_key_id', 'payment_source_component_id'}


def test_the_widened_shape_admits_either_source_and_refuses_both_or_neither(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        stored = db.raw.execute(
            'SELECT sql FROM sqlite_schema WHERE name=?', (TABLE,)).fetchone()[0]
        assert 'REFERENCES payment_component_keys (id)' in stored
        assert 'REFERENCES payment_components (id)' in stored
        assert 'ck_customer_refund_consumption_one_source' in stored
        # The exactly-one rule is storage's, not the service's: a row naming both sources and
        # a row naming neither are both refused here, with no Python in the way.
        columns = ('id, kind, reverses_consumption_id, transaction_id, revision_id,'
                   ' credit_source_key_id, credit_source_component_id, payment_source_key_id,'
                   ' payment_source_component_id, amount_minor_units, currency, effective_date,'
                   ' created_at, created_by, created_via, audit_event_id')
        for keys in (('K1', 'C1', 'P1', 'PC1'), (None, None, None, None),
                     ('K1', None, None, None), (None, None, 'P1', None)):
            try:
                db.raw.execute(
                    f'INSERT INTO {TABLE} ({columns}) VALUES'
                    " ('X1','consume',NULL,'T1','R1',?,?,?,?,1,'USD','2026-03-20','now','U',"
                    "'cli','E1')", keys)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f'storage admitted a consumption naming {keys}')


# Not tested here: a *stored* consumption carried across the rebuild byte for byte. Building
# one at co0052 needs eleven parent rows across eight tables -- the profile alone reaches
# accounts, customers, payment methods, transactions, revisions and a posting attribution --
# and a fixture that skips them fails the migration's own `PRAGMA foreign_key_check`, which is
# the fence doing its job rather than a defect. What guards the copy in the field is the
# migration itself: it compares every copied column in both directions with `EXCEPT` and aborts
# with "co0058 rebuilt values differ" before it drops anything, and
# `test_customer_refund_migration.test_populated_raw_fixture_to_head_preserves_values_and_unreplaced_ddl`
# walks a populated database through this revision to head and checks every value and the
# foreign keys afterwards.


def test_a_trigger_elsewhere_that_names_the_table_survives_the_rebuild(tmp_path):
    """SQLite re-parses every trigger during a rename, so one naming this table must come down.

    co0053's `credit_deletions_owner` sits on `credit_deletions` and reads this table, to refuse
    deleting a credit memo a refund still stands on. Left in place it aborts the rename outright
    -- "no such table: main.customer_refund_consumptions" -- because the table is momentarily
    gone; dropped and not put back, the refusal it carries would vanish without a word. A
    trigger that merely names the table is not the same thing as one attached to it, which the
    test below refuses outright.
    """
    path = tmp_path / 'foreign.db'
    _at(path, PREVIOUS)
    body = ("CREATE TRIGGER local_reads_consumptions BEFORE INSERT ON transactions"
            " WHEN EXISTS (SELECT 1 FROM customer_refund_consumptions)"
            " BEGIN SELECT RAISE(ABORT,'no'); END")
    with sqlite3.connect(path) as raw:
        raw.execute(body)
        raw.commit()
    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups')[1] == HEADS['company']
        stored = db.raw.execute("SELECT sql FROM sqlite_schema WHERE name=?",
                                ('local_reads_consumptions',)).fetchone()
        assert stored is not None, 'a trigger that names the rebuilt table was not put back'
        assert stored[0] == body, 'it came back changed'
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


def test_a_local_trigger_on_the_rebuilt_table_stops_the_migration(tmp_path):
    """A rule this migration did not write is not something it may rebuild out from under.

    A trigger on the table is a fence somebody put there, and a rebuild either drops it or
    recreates it against a shape it was never written for. co0028 refuses on the settlement
    tables for exactly this reason, and so does this.
    """
    path = tmp_path / 'local.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TRIGGER local_overage_note AFTER INSERT ON ' + TABLE
                    + ' BEGIN SELECT 1; END')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0058' in str(exc) or 'co0058' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a local trigger on the rebuilt table must stop co0058')


def test_a_rewritten_consumption_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'rewritten.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER customer_refund_consumptions_exact_party')
        raw.execute('CREATE TRIGGER customer_refund_consumptions_exact_party BEFORE INSERT ON '
                    + TABLE + ' BEGIN SELECT 1; END')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0058' in str(exc) or 'co0058' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a rewritten consumption guard must stop co0058')


def test_a_reserved_name_already_in_use_stops_the_migration(tmp_path):
    path = tmp_path / 'reserved.db'
    _at(path, PREVIOUS)
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE ix_customer_refund_consumptions_payment_source (id INTEGER'
                    ' PRIMARY KEY)')
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:
            assert 'co0058' in str(exc) or 'co0058' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a reserved co0058 object name must stop the migration')
