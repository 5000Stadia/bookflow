"""R161: the audit log is append-only in storage (co0065, hub0015), as the ledger is.

A direct UPDATE or DELETE on an audit table is refused by the database itself, from any
connection. Ordinary writes still append, a backup round trip and a demo reset still work,
and existing files pick the triggers up when they upgrade.
"""
import importlib
import sqlite3
from pathlib import Path

import pytest

import bookflow
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_migration_chain import _make_revision

MESSAGE = 'audit history is append-only'
TABLES = ('audit_events', 'audit_entries')
COMPANY_MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0065_audit_append_only')
HUB_MIGRATION = importlib.import_module('bookflow.storage.hub_migrations.versions.0015_audit_append_only')


def _company_db(client):
    company = client.company.list()['items'][0]['company_id']
    return company, Path(client.run('company show', {}, company=company)['path']) / 'company.db'


def _refused(path, sql):
    with sqlite3.connect(path) as conn:
        before = [conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES]
        with pytest.raises(sqlite3.DatabaseError, match=MESSAGE):
            conn.execute(sql)
        conn.rollback()
        assert [conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES] == before


def _counts(path):
    with sqlite3.connect(path) as conn:
        return [conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in TABLES]


def _triggers(path):
    with sqlite3.connect(path) as conn:
        return {n for (n,) in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'audit_%'")}


EXPECTED = {f'{t}_no_{e}' for t in TABLES for e in ('update', 'delete')}
STATEMENTS = (
    "UPDATE audit_events SET summary = 'changed'",
    "UPDATE audit_entries SET action = 'migrate'",
    "DELETE FROM audit_events",
    "DELETE FROM audit_entries",
)


@pytest.mark.parametrize('sql', STATEMENTS)
def test_company_audit_rows_cannot_be_updated_or_deleted(client, sql):
    _, path = _company_db(client)
    assert _triggers(path) == EXPECTED
    _refused(path, sql)


@pytest.mark.parametrize('sql', STATEMENTS)
def test_hub_audit_rows_cannot_be_updated_or_deleted(root, sql):
    _refused(root / 'hub.db', sql)
    assert _triggers(root / 'hub.db') == EXPECTED


def test_ordinary_writes_still_append_to_both_logs(client, root):
    company, path = _company_db(client)
    company_before, hub_before = _counts(path), _counts(root / 'hub.db')
    client.run('customer create', {'name': 'Append Only Customer'}, company=company)
    client.run('organization new', {'name': 'Append Only Org'})
    assert all(a > b for a, b in zip(_counts(path), company_before))
    assert all(a > b for a, b in zip(_counts(root / 'hub.db'), hub_before))
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM audit_events WHERE summary LIKE ?',
                            ('%Append Only Customer%',)).fetchone()[0] >= 1


def test_backup_and_restore_round_trip_keeps_the_log_and_the_triggers(client, tmp_path):
    company, path = _company_db(client)
    with sqlite3.connect(path) as conn:
        original = conn.execute('SELECT * FROM audit_events ORDER BY id').fetchall()
    archive = Path(client.run('company backup', {}, company=company)['path'])
    copy = client.run('company restore', {'archive': str(archive), 'as_copy': True, 'name': 'Audit Copy'})
    restored = Path(client.run('company show', {}, company=copy['company_id'])['path']) / 'company.db'
    assert _triggers(restored) == EXPECTED
    _refused(restored, STATEMENTS[0])
    with sqlite3.connect(restored) as conn:
        restored_rows = conn.execute('SELECT * FROM audit_events ORDER BY id').fetchall()
    assert restored_rows[:len(original)] == original  # the copy carries every event, then its own restore event


def test_demo_reset_still_works_with_the_triggers(client, root):
    before = _counts(root / 'hub.db')
    client.run('demo reset', {})
    assert all(a > b for a, b in zip(_counts(root / 'hub.db'), before))
    _, path = _company_db(client)
    assert _triggers(path) == EXPECTED and _triggers(root / 'hub.db') == EXPECTED


def _add_event(path):
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO audit_events (id, at, command, interface, client_name, client_version, client_host,"
                     " session_id, request_id, summary) VALUES ('E1','2026-01-01T00:00:00Z','init','cli','t','1','h','S','R','s')")
        conn.execute("INSERT INTO audit_entries (id, event_id, record_type, record_id, action) VALUES ('N1','E1','x','1','create')")


@pytest.mark.parametrize('chain, previous, head, migration', (
    ('company', 'co0064', 'co0065', COMPANY_MIGRATION), ('hub', 'hub0014', 'hub0015', HUB_MIGRATION)))
def test_existing_files_gain_the_triggers_on_upgrade(tmp_path, monkeypatch, chain, previous, head, migration):
    assert HEADS[chain] == head
    old = tmp_path / 'old.db'
    with monkeypatch.context() as patch:
        patch.setitem(HEADS, chain, previous)
        with open_database(old, writable=True, create=True) as db:
            assert migrate_to_head(db, chain, None) == (None, previous)
    assert _triggers(old) == set()
    _add_event(old)
    with open_database(old, writable=True) as db:
        assert migrate_to_head(db, chain, tmp_path / 'backups') == (previous, head)
    assert _triggers(old) == EXPECTED
    for statement in STATEMENTS:
        _refused(old, statement)
    saved, = (tmp_path / 'backups').glob(f'*-from-{previous}.db')
    assert _triggers(saved) == set()  # the pre-upgrade backup is the untouched earlier file
    with pytest.raises(NotImplementedError):
        migration.downgrade()


def test_hub_history_with_events_replays_through_hub0002_then_gains_triggers(tmp_path):
    """hub0002 numbers existing events with UPDATE; it ran before hub0015, so replay still works."""
    old = tmp_path / 'old.db'
    def populate(raw):
        raw.execute("INSERT INTO audit_events (id, at, command, interface, client_name, client_version, client_host,"
                    " session_id, request_id, summary) VALUES ('E1','2026-01-01T00:00:00Z','init','cli','t','1','h','S','R','s')")
        raw.commit()
    _make_revision(old, 'hub', 'hub0001', populate)
    with open_database(old, writable=True) as db:
        assert migrate_to_head(db, 'hub', tmp_path / 'backups')[1] == HEADS['hub']
    with sqlite3.connect(old) as conn:
        assert conn.execute('SELECT seq FROM audit_events').fetchall() == [(1,)]
    assert _triggers(old) == EXPECTED
