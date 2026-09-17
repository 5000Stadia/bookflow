"""The authority reuse token follows native transaction changes, not request IDs."""
import sqlite3

import pytest
import sqlalchemy as sa

from bookflow.storage.engine import open_database


def token(db):
    return db.authority_snapshot_key()


@pytest.fixture
def db(tmp_path):
    with open_database(tmp_path / 'hub.db', writable=True, create=True) as value:
        value.raw.execute('CREATE TABLE facts(value INTEGER)')
        value.raw.execute('INSERT INTO facts VALUES(1)')
        yield value


def test_only_live_unchanged_transaction_has_reusable_key(db):
    assert token(db) is None
    db.raw.execute('BEGIN')
    first = token(db)
    assert first is not None
    db.raw.execute('SELECT * FROM facts').fetchall()
    db.raw.execute('SAVEPOINT harmless')
    db.raw.execute('RELEASE harmless')
    assert token(db) == first
    db.raw.commit()
    assert token(db) is None
    db.raw.execute('BEGIN')
    assert token(db) != first


@pytest.mark.parametrize('api', ['connection', 'cursor', 'sqlalchemy'])
def test_mutation_and_rollback_to_savepoint_change_key(db, api):
    db.raw.execute('BEGIN')
    db.raw.execute('SAVEPOINT before_write')
    first = token(db)
    if api == 'sqlalchemy':
        db.conn.execute(sa.text('UPDATE facts SET value=2'))
    else:
        owner = db.raw if api == 'connection' else db.raw.cursor()
        owner.execute('UPDATE facts SET value=2')
    changed = token(db)
    assert changed != first
    changes = db.raw.total_changes
    db.raw.execute('ROLLBACK TO before_write')
    assert db.raw.total_changes == changes
    assert db.raw.execute('SELECT value FROM facts').fetchone() == (1,)
    assert token(db) != changed


def test_reused_prepared_rollback_and_transaction_restart(db):
    db.raw.execute('BEGIN')
    keys = []
    for _ in range(2):
        db.raw.execute('SAVEPOINT p')
        db.raw.execute('UPDATE facts SET value=2')
        keys.append(token(db))
        db.raw.execute('ROLLBACK TO p')
        keys.append(token(db))
        db.raw.execute('RELEASE p')
    assert len(set(keys)) == len(keys)
    old = token(db)
    db.raw.rollback()
    db.raw.execute('BEGIN')
    assert token(db) != old


@pytest.mark.parametrize('api', ['connection', 'cursor'])
def test_batch_and_script_invalidate(db, api):
    owner = db.raw if api == 'connection' else db.raw.cursor()
    db.raw.execute('BEGIN')
    before = token(db)
    owner.executemany('INSERT INTO facts VALUES(?)', [(3,), (4,)])
    assert token(db) != before
    before = token(db)
    owner.executescript('BEGIN; ROLLBACK; BEGIN;')
    assert token(db) != before


def test_custom_cursor_disables_reuse_without_changing_native_api(db):
    class CustomCursor(sqlite3.Cursor):
        pass
    db.raw.execute('BEGIN')
    assert token(db) is not None
    cursor = db.raw.cursor(factory=CustomCursor)
    assert type(cursor) is CustomCursor
    assert token(db) is None
    cursor.execute('UPDATE facts SET value=2')
    assert token(db) is None


def test_trace_callback_not_replaced_and_closed_handle_not_reused(db):
    statements = []
    db.raw.set_trace_callback(statements.append)
    db.raw.execute('BEGIN')
    assert token(db) is not None
    db.raw.execute('SELECT value FROM facts').fetchall()
    assert any('SELECT value' in statement for statement in statements)
    db.close()
    assert token(db) is None


@pytest.mark.parametrize('fails', [False, True])
def test_connection_context_exit_and_outer_savepoint(db, fails):
    db.raw.execute('SAVEPOINT outermost')
    before = token(db)
    db.raw.execute('RELEASE outermost')
    assert token(db) is None
    db.raw.execute('BEGIN')
    assert token(db) != before
    before = token(db)
    try:
        with db.raw:
            if fails:
                raise ValueError('rollback')
    except ValueError:
        pass
    assert token(db) is None
    db.raw.execute('BEGIN')
    assert token(db) != before


@pytest.mark.parametrize('script', [False, True])
def test_partial_failure_invalidates(db, script):
    db.raw.execute('CREATE UNIQUE INDEX unique_value ON facts(value)')
    db.raw.execute('BEGIN')
    before = token(db)
    with pytest.raises(sqlite3.IntegrityError):
        if script:
            db.raw.executescript('BEGIN; INSERT INTO facts VALUES(2); INSERT INTO facts VALUES(2);')
        else:
            db.raw.executemany('INSERT INTO facts VALUES(?)', [(2,), (2,)])
    assert token(db) != before
    assert db.raw.execute('SELECT count(*) FROM facts').fetchone() == (2,)
