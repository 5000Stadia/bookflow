"""Preserving actual hub migration and database-owned signing-key lifetime."""
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from bookflow.hub.history_cursor_keys import read
from bookflow.core.errors import BookflowError
from tests.permission_storage_support import create_hub, snapshot


def test_upgrade_preserves_old_storage_and_private_key_across_reopen(tmp_path):
    path = tmp_path/'hub.db'
    create_hub(path, revision='hub0012')
    with open_database(path, writable=True) as db:
        before = snapshot(db.raw)
        assert migrate_to_head(db, 'hub', None) == ('hub0012', 'hub0013')
        after = snapshot(db.raw, before['columns'])
        assert {k:v for k,v in after['tables'].items() if k != 'alembic_version'} == {
            k:v for k,v in before['tables'].items() if k != 'alembic_version'}
        assert set(before['ddl']) <= set(after['ddl'])
        key = read(db)
        assert type(key) is bytes and len(key) == 32
        assert migrate_to_head(db, 'hub', None) == ('hub0013', 'hub0013')
        assert read(db) == key
    with open_database(path, writable=False) as db:
        original = snapshot(db.raw)
        assert read(db) == key and snapshot(db.raw) == original


def test_fresh_hubs_have_independent_keys(tmp_path):
    keys = []
    for name in ('one','two'):
        with open_database(tmp_path/name, writable=True, create=True) as db:
            assert migrate_to_head(db, 'hub', None) == (None, 'hub0013')
            keys.append(read(db))
    assert keys[0] != keys[1]


def test_temp_names_cannot_redirect_key_creation_or_reads(tmp_path):
    path = tmp_path/'hub.db'
    create_hub(path, revision='hub0012')
    with open_database(path, writable=True) as db:
        db.raw.execute('CREATE TEMP TABLE history_cursor_keys(key_id,key_material)')
        db.raw.execute('INSERT INTO temp.history_cursor_keys VALUES (1,?)', (b'x'*32,))
        migrate_to_head(db, 'hub', None)
        assert read(db) != b'x'*32
        assert db.raw.execute('SELECT * FROM temp.history_cursor_keys').fetchall() == [(1,b'x'*32)]


def test_failed_key_creation_rolls_back_complete_migration(tmp_path):
    path = tmp_path/'hub.db'
    create_hub(path, revision='hub0012')
    with open_database(path, writable=True) as db:
        before = snapshot(db.raw)
        def fail(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO main.history_cursor_keys'):
                raise RuntimeError('owned failure after table creation')
        sa.event.listen(db.conn, 'before_cursor_execute', fail)
        try:
            with pytest.raises(BookflowError):
                migrate_to_head(db, 'hub', None)
        finally:
            sa.event.remove(db.conn, 'before_cursor_execute', fail)
        assert snapshot(db.raw) == before
        assert migrate_to_head(db, 'hub', None) == ('hub0012','hub0013')


def test_key_constraints_and_missing_key_fail_closed(tmp_path):
    with open_database(tmp_path/'hub.db', writable=True, create=True) as db:
        migrate_to_head(db, 'hub', None)
        for key_id, material in ((2,b'x'*32),(1,b'x'*31),(1,'x'*32)):
            with pytest.raises(sqlite3.IntegrityError):
                db.raw.execute('INSERT OR REPLACE INTO main.history_cursor_keys VALUES (?,?)',(key_id,material))
        original = read(db)
        assert len(original) == 32
        db.raw.execute('DELETE FROM main.history_cursor_keys')
        with pytest.raises(BookflowError) as caught:read(db)
        assert caught.value.code == 'E_INTERNAL'
        assert db.raw.execute('SELECT * FROM main.history_cursor_keys').fetchall() == []
