"""co0029 widens one CHECK and the guard that mirrors it, preserving everything else.

The DDL this migration edits is not its own: it reads the stored `work_documents` definition,
respells one enumeration inside it, and rebuilds the table underneath whatever rows, indexes,
views and triggers a real company already has. So the questions this file asks are: does the
widened constraint end up in the shipped schema, does the revision guard move with it, and
does a populated co0028 database come out the other side with every value and every local
object exactly as it went in.
"""
import importlib
import sqlite3

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import table

M = importlib.import_module('bookflow.storage.company_migrations.versions.0029_estimate_void')

WIDENED = "status IN ('draft','open','accepted','declined','superseded','cancelled','voided')"


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


def test_the_frozen_replacement_is_the_constraint_the_metadata_now_declares():
    """The migration's frozen text and the live metadata have to say the same thing."""
    before, after = M.REPLACEMENTS['work_documents']
    assert after == WIDENED and before == WIDENED.replace(",'voided'", '')
    declared = next(constraint for constraint in c.work_documents.constraints
                    if getattr(constraint, 'name', None) == 'ck_work_status')
    assert WIDENED in str(declared.sqltext)
    assert 'voided' not in str(declared.sqltext).split('work_order')[1], (
        'only the quote kinds take the new state')


def test_a_fresh_database_reaches_the_head_carrying_both_widened_objects(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        assert HEADS['company'] == M.revision
        stored = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='work_documents'").fetchone()[0]
        guard = db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='work_revision_kind'").fetchone()[0]
        assert WIDENED in stored
        assert "NEW.status IN ('draft','open','accepted','declined','superseded','cancelled','voided')" in guard
        # The work-order clause is untouched in both.
        assert "'on_hold','complete','cancelled')" in stored and "'on_hold','complete','cancelled')" in guard
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before


def test_the_widened_check_admits_a_voided_quote_and_still_refuses_anything_else(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')

        def document(identity, kind, status, group):
            raw.execute("INSERT INTO work_documents (id, version, created_at, created_by,"
                        " created_via, updated_at, updated_by, updated_via, kind, number,"
                        " current_revision_id, status, active, estimate_group_id)"
                        " VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', ?, ?, 'R'||?,"
                        " ?, 0, ?)", (identity, kind, 'N-' + identity, identity, status, group))

        document('E1', 'estimate', 'voided', 'E1')
        document('P1', 'proposal', 'voided', None)
        for kind, group in (('estimate', 'E2'), ('proposal', None)):
            try:
                document('X' + kind, kind, 'withdrawn', group)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError('an unknown quote state must still be refused')
        try:
            document('W1', 'work_order', 'voided', None)
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError('a work order must not take the quote-only state')
        raw.commit()


def test_a_populated_co0028_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0028')
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0028',)
        raw.execute('PRAGMA foreign_keys=OFF')
        # Referentially whole rows in the rebuilt table, one of them carrying an embedded NUL
        # that a quote()-only copy would truncate, plus local objects of every kind.
        raw.execute("INSERT INTO audit_events (id, seq, at, command, actor_id, actor_kind,"
                    " on_behalf_of, interface, client_name, client_version, client_host,"
                    " session_id, request_id, idempotency_key, reason, directive_id,"
                    " directive_code, source_ref, undo_of_event_id, summary)"
                    " VALUES ('E1', 1, 'now', 'estimate create', 'U', 'user', NULL, 'cli', 'x',"
                    " '1', 'h', 'S1', 'Q1', NULL, NULL, NULL, NULL, NULL, NULL, 'quote Q-1')")
        raw.execute("INSERT INTO customers (id, version, created_at, created_by, created_via,"
                    " updated_at, updated_by, updated_via, active, name, name_key, full_name,"
                    " full_name_key, depth, path, job_status, address_mode, contact_mode)"
                    " VALUES ('C1', 1, 'now', 'U', 'cli', 'now', 'U', 'cli', 1, 'Ada',"
                    " 'ada', 'Ada', 'ada', 1, 'C1', 'none', 'own', 'own')")
        for identity, kind, number, revision, status, active, group, created in (
                ('D1', 'estimate', 'Q-1', 'R1', 'accepted', 1, 'D1', 'A\x00B'),
                ('D2', 'work_order', 'WO-1', 'R2', 'complete', 0, None, 'now')):
            raw.execute("INSERT INTO work_documents (id, version, created_at, created_by,"
                        " created_via, updated_at, updated_by, updated_via, kind, number,"
                        " current_revision_id, status, active, estimate_group_id)"
                        " VALUES (?, 4, ?, 'U', 'cli', ?, 'U', 'cli', ?, ?, ?, ?, ?, ?)",
                        (identity, created, created, kind, number, revision, status, active, group))
            raw.execute("INSERT INTO work_revisions (id, document_id, revision_number,"
                        " supersedes_revision_id, date, number, title, status, active,"
                        " customer_id, currency, net_minor_units, tax_minor_units,"
                        " gross_minor_units, accepted_revision_id, accepted_at, accepted_by,"
                        " decision_note, facts_snapshot, custom_fields_snapshot, audit_event_id,"
                        " created_at, created_by, created_via)"
                        " VALUES (?, ?, 1, NULL, '2026-07-01', ?, 'Re-pipe', ?, ?, 'C1', 'USD',"
                        " 100, 0, 100, ?, ?, ?, NULL, '{}', '{}', 'E1', 'now', 'U', 'cli')",
                        (revision, identity, number, status, active,
                         revision if status == 'accepted' else None,
                         'now' if status == 'accepted' else None,
                         'U' if status == 'accepted' else None))
        raw.execute('CREATE TABLE local_work_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_work_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_work_expression ON local_work_bytes (length(t))')
        raw.execute('CREATE INDEX local_work_on_documents ON work_documents (number, kind)')
        raw.execute('CREATE VIEW local_work_view AS SELECT id, number FROM work_documents')
        raw.execute("CREATE TRIGGER local_work_trigger AFTER INSERT ON local_work_bytes"
                    " BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        objects = set(raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            " AND name NOT IN ('work_documents', 'work_revision_kind')").fetchall())

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0028', HEADS['company'])
        assert {name: table(db.raw, name) for name in names} == before
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= after, 'a local table, index, view or trigger was lost'
        assert WIDENED in db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='work_documents'").fetchone()[0]
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


def test_an_unexpected_revision_state_guard_stops_the_migration(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0028')
    with sqlite3.connect(path) as raw:
        raw.execute('DROP TRIGGER work_revision_kind')
        raw.execute("CREATE TRIGGER work_revision_kind BEFORE INSERT ON work_revisions"
                    " BEGIN SELECT 1; END")
        raw.commit()
    with open_database(path, writable=True) as db:
        try:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        except Exception as exc:  # the runner wraps whatever the migration raised
            assert 'co0029' in str(exc) or 'co0029' in str(getattr(exc, '__cause__', ''))
        else:
            raise AssertionError('a rewritten revision state guard must stop co0029')
