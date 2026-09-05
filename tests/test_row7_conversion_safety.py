"""Credential conversion survives abrupt exit and retains issuer boundaries."""
import subprocess
import sys
import sqlite3
import pytest
from bookflow.hub import credentials
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head, current_revision_raw
from bookflow.core.errors import BookflowError
from tests.test_row7_identity_migration import _make, _dump
from tests.test_row7_credentials import authority, writer
from tests.conftest import make_actor


@pytest.fixture(autouse=True)
def _revision_under_test(monkeypatch):
    monkeypatch.setitem(HEADS, "hub", "hub0009")


def test_old_human_secrets_authenticate_after_conversion(tmp_path):
    path = tmp_path / 'hub.db'
    _make(path)
    with sqlite3.connect(path) as conn:
        for ident in ('TH', 'SH'):
            conn.execute('UPDATE api_tokens SET token_hash=? WHERE id=?',
                         (credentials.token_hash('legacy-' + ident), ident))
    with open_database(path, writable=True) as db:
        migrate_to_head(db, 'hub', None)
        for ident in ('TH', 'SH'):
            row = credentials.resolve_token(db, 'legacy-' + ident)
            assert row['id'] == ident
            assert row['on_behalf_of'] is None and row['authority_epoch'] is None


def test_abrupt_process_exit_after_token_conversion_rolls_back(tmp_path):
    path = tmp_path / 'hub.db'
    _make(path)
    with sqlite3.connect(path) as conn:
        before = _dump(conn)
    code = '''
import os, sys
from pathlib import Path
import sqlalchemy as sa
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
HEADS['hub'] = 'hub0009'
def interrupt(conn, cursor, statement, parameters, context, executemany):
    if 'INSERT INTO audit_entries' in statement and 'api_token' in parameters:
        os._exit(73)
with open_database(Path(sys.argv[1]), writable=True) as db:
    sa.event.listen(db.conn, 'after_cursor_execute', interrupt)
    migrate_to_head(db, 'hub', None)
'''
    result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
    assert result.returncode == 73, result.stderr.decode()
    assert current_revision_raw(path) == 'hub0008'
    with sqlite3.connect(path) as conn:
        assert _dump(conn) == before
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    with open_database(path, writable=True) as db:
        migrate_to_head(db, 'hub', None)
        assert db.raw.execute("SELECT count(*) FROM audit_events WHERE reason='migration_requires_authorization'").fetchone() == (1,)


@pytest.mark.parametrize('issuer_kind', ['agent', 'system', 'ordinary', 'missing'])
def test_direct_service_issuer_cannot_bypass_authorization(root, authority, issuer_kind):
    human, agent = authority
    issuer = {'agent': agent, 'missing': 'missing'}.get(issuer_kind)
    if issuer is None:
        issuer = make_actor(root, 'ineligible-' + issuer_kind,
                            kind='human' if issuer_kind == 'ordinary' else 'system')
    with writer(root) as db:
        before = db.raw.execute('SELECT count(*) FROM api_tokens').fetchone()
        with pytest.raises(BookflowError) as caught:
            credentials.issue_token(db, user_id=agent, on_behalf_of=human, kind='bearer',
                                    label='bypass', days=None, via='python', actor_id=issuer)
        assert caught.value.code == 'E_PERMISSION'
        assert db.raw.execute('SELECT count(*) FROM api_tokens').fetchone() == before
