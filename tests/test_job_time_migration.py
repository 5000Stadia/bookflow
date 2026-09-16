"""co0055 admits a fourth work kind into three CHECKs and the two guards that mirror them.

Recorded time is stored as a one-line customer-work document, so this migration creates no
table: it respells enumerations that are already there. `work_documents` has to admit the kind
and the one live state it holds, `custom_field_scopes` has to admit it as a record type beside
the three kinds standing next to it, and `work_billing_conversions` has to admit the two
relations a sale born from recorded time is named by. Two triggers carry copies of those same
enumerations one table away and have to move with them.

None of that DDL is the migration's own. It reads each stored definition, respells one
enumeration inside it, and rebuilds the table underneath whatever rows, indexes, views and
triggers a real company already has -- so the questions this file asks are the ones co0029
asked when it widened the first of these: do the widened constraints end up in the shipped
schema, do the guards move with them, does a populated co0052 database come out the other side
with every value and every local object exactly as it went in, and does the migration stop
rather than guess when it finds a constraint it does not recognize.
"""
import importlib
import sqlite3

import pytest

from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import table
from tests.test_bill_payment_migration import _rebuilt_since
from tests.test_estimate_void_migration import _at, _chain

M = importlib.import_module('bookflow.storage.company_migrations.versions.0055_job_time')

KIND = "kind IN ('proposal','estimate','work_order','time_activity')"
TIME_STATES = "(kind = 'time_activity' AND status IN ('recorded','voided'))"
RELATIONS = "'time_activity_invoice','time_activity_sales_receipt'"


def _constraint(table_, name):
    declared = next(x for x in table_.constraints if getattr(x, 'name', None) == name)
    return str(declared.sqltext)


def test_the_migration_sits_in_the_chain_where_it_says_it_does():
    assert M.revision == 'co0055' and M.down_revision == 'co0054'
    # That it is in the chain the head walks back through is the durable claim. Asserting
    # it IS the head was true only while nothing sat on top of it, and journal-entry
    # deletion now does; a head pin here would have to be edited by every later revision.
    assert M.revision in _chain()
    # Every table it rebuilds is one it declares, and every declared replacement is used.
    assert M.CHANGED == ('work_documents', 'custom_field_scopes', 'work_billing_conversions')


def test_the_frozen_replacements_are_the_constraints_the_metadata_now_declares():
    """What the migration writes and what the schema module declares have to be one text."""
    kind_before, kind_after = M.REPLACEMENTS['work_documents'][0]
    status_before, status_after = M.REPLACEMENTS['work_documents'][1]
    assert kind_after == KIND and kind_before == KIND.replace(",'time_activity'", '')
    assert status_after == status_before + ' OR ' + TIME_STATES

    stored_kind = _constraint(c.work_documents, 'ck_work_kind')
    stored_status = _constraint(c.work_documents, 'ck_work_status')
    assert KIND in stored_kind and TIME_STATES in stored_status

    scope_before, scope_after = M.REPLACEMENTS['custom_field_scopes'][0]
    assert scope_after == scope_before.replace("'estimate',", "'estimate','time_activity',")
    assert "'time_activity'" in _constraint(c.custom_field_scopes, 'ck_custom_field_scopes_record_type')

    relation_before, relation_after = M.REPLACEMENTS['work_billing_conversions'][0]
    assert relation_after == relation_before.replace(")", ',' + RELATIONS + ')')
    assert RELATIONS in _constraint(c.work_billing_conversions, 'ck_work_billing_relation')

    # Both guards are respellings of a stored statement, never rewritten from scratch.
    for name, (before, after) in M.TRIGGERS.items():
        assert before != after and before.startswith('CREATE TRIGGER ' + name + ' ')
        assert 'time_activity' in after and 'time_activity' not in before


def test_a_fresh_database_reaches_the_head_carrying_every_widened_object(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])

        def sql(name):
            return db.raw.execute(
                'SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]

        assert KIND in sql('work_documents') and TIME_STATES in sql('work_documents')
        assert "'time_activity'" in sql('custom_field_scopes')
        assert RELATIONS in sql('work_billing_conversions')
        assert "(d.kind = 'time_activity' AND NEW.status IN ('recorded','voided'))" in sql('work_revision_kind')
        assert "s.kind IN ('estimate','work_order','time_activity')" in sql('work_billing_conversion_birth')
        # The clauses it did not touch are untouched in all of them.
        assert "'on_hold','complete','cancelled')" in sql('work_documents')
        assert "'on_hold','complete','cancelled')" in sql('work_revision_kind')

        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]
        before = list(db.raw.iterdump())
        assert migrate_to_head(db, 'company', None) == (HEADS['company'], HEADS['company'])
        assert list(db.raw.iterdump()) == before, 'reaching the head twice must change nothing'


def test_the_widened_checks_admit_recorded_time_and_still_refuse_what_they_refused(tmp_path):
    path = tmp_path / 'fresh.db'
    with open_database(path, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
    with sqlite3.connect(path) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')

        def document(identity, kind, status, group=None):
            raw.execute("INSERT INTO work_documents (id, version, created_at, created_by,"
                        " created_via, updated_at, updated_by, updated_via, kind, number,"
                        " current_revision_id, status, active, estimate_group_id)"
                        " VALUES (?, 1, 'now', 'U', 'cli', 'now', 'U', 'cli', ?, ?, 'R'||?,"
                        " ?, 0, ?)", (identity, kind, 'N-' + identity, identity, status, group))

        def refused(*args):
            try:
                document(*args)
            except sqlite3.IntegrityError:
                return
            raise AssertionError('the check admitted ' + repr(args))

        # The two states recorded time holds, and nothing else.
        document('T1', 'time_activity', 'recorded')
        document('T2', 'time_activity', 'voided')
        for status in ('draft', 'open', 'accepted', 'cancelled', 'complete', 'in_progress'):
            refused('X' + status, 'time_activity', status)
        # `recorded` belongs to the new kind alone, and the other kinds are unchanged.
        refused('W1', 'work_order', 'recorded')
        refused('E1', 'estimate', 'recorded')
        refused('K1', 'invoice_time', 'recorded')
        # ck_work_group already read correctly for the new kind: recorded time has no group.
        refused('T3', 'time_activity', 'recorded', 'T3')
        raw.commit()

    with sqlite3.connect(path) as raw:
        def scope(identity, record_type):
            raw.execute('INSERT INTO custom_field_scopes (id, definition_id, position, active,'
                        ' record_type, definition_name, definition_name_key, definition_active)'
                        " VALUES (?, 'DEF-'||?, 1, 1, ?, 'Job number', 'job number '||?, 1)",
                        (identity, identity, record_type, identity))

        scope('S-time', 'time_activity')
        scope('S-work', 'work_order')
        with pytest.raises(sqlite3.IntegrityError):
            scope('S-bad', 'time_entry')
        raw.commit()


def test_a_populated_co0052_database_keeps_every_value_and_every_local_object(tmp_path):
    path = tmp_path / 'company.db'
    _at(path, 'co0052')
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0052',)
        raw.execute('PRAGMA foreign_keys=OFF')
        # Rows in all three rebuilt tables, one carrying an embedded NUL that a quote()-only
        # copy would truncate, plus local objects of every kind including ones that name the
        # rebuilt tables and would disappear with them if they were not recreated.
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
        raw.execute("INSERT INTO work_documents (id, version, created_at, created_by,"
                    " created_via, updated_at, updated_by, updated_via, kind, number,"
                    " current_revision_id, status, active, estimate_group_id)"
                    " VALUES ('D1', 4, ?, 'U', 'cli', 'now', 'U', 'cli', 'estimate',"
                    " 'Q-1', 'R1', 'accepted', 1, 'D1')", ('A\x00B',))
        raw.execute("INSERT INTO work_revisions (id, document_id, revision_number,"
                    " supersedes_revision_id, date, number, title, status, active,"
                    " customer_id, currency, net_minor_units, tax_minor_units,"
                    " gross_minor_units, accepted_revision_id, accepted_at, accepted_by,"
                    " decision_note, facts_snapshot, custom_fields_snapshot, audit_event_id,"
                    " created_at, created_by, created_via)"
                    " VALUES ('R1', 'D1', 1, NULL, '2026-07-01', 'Q-1', 'Re-pipe', 'accepted',"
                    " 1, 'C1', 'USD', 100, 0, 100, 'R1', 'now', 'U', NULL, '{}', '{}', 'E1',"
                    " 'now', 'U', 'cli')")
        raw.execute("INSERT INTO custom_field_defs (id, version, created_at, created_by,"
                    " created_via, updated_at, updated_by, updated_via, active, seed_key, name,"
                    " name_key, kind, position, required, default_canonical_text)"
                    " VALUES ('DEF1', 1, 'now', 'U', 'cli', 'now', 'U', 'cli', 1, NULL,"
                    " 'Job number', 'job number', 'text', 1, 0, NULL)")
        raw.execute('INSERT INTO custom_field_scopes (id, definition_id, position, active,'
                    ' record_type, definition_name, definition_name_key, definition_active)'
                    " VALUES ('S1', 'DEF1', 1, 1, 'work_order', 'Job number', 'job number', 1)")
        raw.execute('CREATE TABLE local_time_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_time_bytes VALUES (7, ?, ?, 1.5)', ('A\x00B', b'\x00\xff'))
        raw.execute('CREATE INDEX local_time_expression ON local_time_bytes (length(t))')
        raw.execute('CREATE INDEX local_time_on_documents ON work_documents (number, kind)')
        raw.execute('CREATE INDEX local_time_on_scopes ON custom_field_scopes (record_type)')
        raw.execute('CREATE VIEW local_time_view AS SELECT id, number FROM work_documents')
        raw.execute("CREATE TRIGGER local_time_trigger AFTER INSERT ON local_time_bytes"
                    " BEGIN SELECT 1; END")
        raw.commit()
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " AND name <> 'alembic_version' ORDER BY name")]
        before = {name: table(raw, name) for name in names}
        # What this migration rewrites, plus whatever a later revision rebuilds - derived,
        # because a hand-listed exclusion goes stale the moment another revision lands.
        # Everything rebuilt between the revision this database sits at and the head --
        # not between this migration and the head, which would miss every revision that
        # lands in front of it, as co0053 and co0054 now do.
        rebuilt = set(M.CHANGED) | set(M.TRIGGERS) | _rebuilt_since('co0052')
        objects = {row for row in raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() if row[1] not in rebuilt}

    with open_database(path, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0052', HEADS['company'])
        assert {name: table(db.raw, name) for name in names} == before, 'a value changed'
        after = set(db.raw.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall())
        assert objects <= after, 'a local table, index, view or trigger was lost'
        assert KIND in db.raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='work_documents'").fetchone()[0]
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall() == [('ok',)]


@pytest.mark.parametrize('name', ['work_revision_kind', 'work_billing_conversion_birth'])
def test_a_rewritten_guard_stops_the_migration_rather_than_being_guessed_at(tmp_path, name):
    path = tmp_path / 'company.db'
    _at(path, 'co0052')
    with sqlite3.connect(path) as raw:
        target = raw.execute('SELECT tbl_name FROM sqlite_schema WHERE name=?', (name,)).fetchone()[0]
        raw.execute('DROP TRIGGER ' + name)
        raw.execute('CREATE TRIGGER ' + name + ' BEFORE INSERT ON ' + target + ' BEGIN SELECT 1; END')
        raw.commit()
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:  # the runner wraps whatever the migration raised
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert 'co0055' in str(caught.value) or 'co0055' in str(caught.value.__cause__)


def test_an_unrecognized_constraint_stops_the_migration(tmp_path):
    """A respelling the migration cannot find is somebody's local edit, not a thing to guess at."""
    path = tmp_path / 'company.db'
    _at(path, 'co0052')
    with sqlite3.connect(path) as raw:
        stored = raw.execute(
            "SELECT sql FROM sqlite_schema WHERE name='custom_field_scopes'").fetchone()[0]
        before, _ = M.REPLACEMENTS['custom_field_scopes'][0]
        assert before in stored
        raw.execute('PRAGMA writable_schema=ON')
        raw.execute("UPDATE sqlite_schema SET sql=? WHERE name='custom_field_scopes'",
                    (stored.replace(before, before.replace("'estimate',", "'estimate' ,")),))
        raw.execute('PRAGMA writable_schema=OFF')
        raw.commit()
    with open_database(path, writable=True) as db:
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert 'co0055' in str(caught.value) or 'co0055' in str(caught.value.__cause__)
