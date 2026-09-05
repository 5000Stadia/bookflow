"""The ledger migration preserves populated company files and freezes history DDL."""

import importlib
import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.company import schema as c
from bookflow.storage.engine import open_database, sqlite_uri
from bookflow.storage.migrate import HEADS, _config, current_revision_raw, migrate_to_head
from tests.test_attachment_migration import _old_data
from tests.test_migration_chain import _make_revision, _normalized_schema
from tests.test_row6_note_migration import _schema_semantics

TABLES = ('transactions', 'transaction_revisions', 'document_line_identities',
          'document_lines', 'posting_batches', 'posting_lines', 'posting_line_sources')


def test_populated_co6_upgrades_without_changing_notes_or_existing_history(tmp_path, monkeypatch):
    monkeypatch.setitem(HEADS, 'company', 'co0007')
    old, fresh = tmp_path / 'old.db', tmp_path / 'fresh.db'
    _make_revision(old, 'company', 'co0006', _old_data)
    _make_revision(fresh, 'company', 'co0007', _old_data)
    with sqlite3.connect(old) as conn:
        before_schema = _normalized_schema(conn)
        before = {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
                  for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    with open_database(old, writable=True) as db:
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0006', 'co0007')
        for name, rows in before.items():
            if name not in ('sequences', 'alembic_version'):
                assert db.raw.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall() == rows
        assert db.raw.execute("SELECT name,next_number,prefix FROM sequences WHERE name='journal_entry'").fetchone() == ('journal_entry', 1, '')
        for name, number in before['sequences']:
            assert db.raw.execute('SELECT next_number,prefix FROM sequences WHERE name=?', (name,)).fetchone() == (number, '')
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0007', 'co0007')
    assert _schema_semantics(old) == _schema_semantics(fresh)
    saved, = (tmp_path / 'backups').glob('*-from-co0006.db')
    assert current_revision_raw(saved) == 'co0006'
    with sqlite3.connect(sqlite_uri(saved, 'ro'), uri=True) as conn:
        assert _normalized_schema(conn) == before_schema
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_current_ledger_metadata_matches_head(tmp_path):
    fresh = tmp_path / 'head.db'
    with open_database(fresh, writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, HEADS['company'])
        for name in TABLES:
            expected = c.metadata.tables[name]
            reflected = sa.Table(name, sa.MetaData(), autoload_with=db.conn)
            assert [(x.name, str(x.type), x.nullable, x.primary_key) for x in reflected.c] == [
                (x.name, str(x.type), x.nullable, x.primary_key) for x in expected.c]
            assert {(f.parent.name, f.target_fullname) for f in reflected.foreign_keys} == {
                (f.parent.name, f.target_fullname) for f in expected.foreign_keys}


@pytest.mark.parametrize('revision', ['co0007', 'co0008'])
def test_ledger_revision_uses_frozen_ddl(tmp_path, monkeypatch, revision):
    old, fresh = tmp_path / 'old.db', tmp_path / 'fresh.db'
    _make_revision(old, 'company', 'co0006', _old_data)
    _make_revision(fresh, 'company', revision, _old_data)
    with open_database(fresh, writable=True) as db:
        # Historical journal-only schemas require these columns, unlike co0009's
        # shared commercial envelope. Keep this witness at its original version.
        reflected = sa.Table('document_lines', sa.MetaData(), autoload_with=db.conn)
        for name in ('account_id', 'side', 'amount_minor_units', 'account_snapshot'):
            assert reflected.c[name].nullable is False
        assert 'tax_component_id' not in sa.Table(
            'posting_line_sources', sa.MetaData(), autoload_with=db.conn,
        ).c
        triggers = {row[0] for row in db.raw.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")}
        assert 'transactions_no_delete' in triggers
        assert {f'{name}_no_{operation}' for name in TABLES[1:] for operation in ('update', 'delete')} <= triggers
    unrelated = sa.MetaData()
    future = sa.Table('future_only', unrelated, sa.Column('id', sa.Integer, primary_key=True))
    monkeypatch.setattr(c, 'metadata', unrelated)
    for name in TABLES:
        monkeypatch.setattr(c, name, future)
    with open_database(old, writable=True) as db:
        command.upgrade(_config('company', db.conn), revision)
    assert _schema_semantics(old) == _schema_semantics(fresh)
    module = importlib.import_module('bookflow.storage.company_migrations.versions.0007_ledger')
    assert all(type(statement) is str for statement in module.DDL)


def test_ledger_history_cannot_be_downgraded(tmp_path):
    path = tmp_path / 'company.db'
    _make_revision(path, 'company', 'co0007', _old_data)
    with open_database(path, writable=True) as db:
        before = _normalized_schema(db.raw)
        with pytest.raises(NotImplementedError):
            command.downgrade(_config('company', db.conn), 'co0006')
        assert _normalized_schema(db.raw) == before
        assert current_revision_raw(path) == 'co0007'


def test_posted_history_rejects_sql_update_and_delete(client):
    from pathlib import Path
    company = 'Demo Plumbing Co'
    accounts = client.account.list(company=company)['items']
    bank = next(a for a in accounts if a['active'] and a['type'] == 'bank')
    income = next(a for a in accounts if a['active'] and a['type'] == 'income')
    result = client.journal.post(date='2026-01-01', number='SQL-SAFE', lines=[
        {'account': bank['id'], 'side': 'debit', 'amount': '10.00'},
        {'account': income['id'], 'side': 'credit', 'amount': '10.00'},
    ], company=company)
    path = Path(client.company.show(company=company)['path']) / 'company.db'
    with open_database(path, writable=True) as db:
        for name in TABLES[1:]:
            assert db.raw.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] > 0
            for statement in (f'UPDATE "{name}" SET id=id', f'DELETE FROM "{name}"'):
                with pytest.raises(sqlite3.IntegrityError, match='immutable ledger history'):
                    db.raw.execute(statement)
        with pytest.raises(sqlite3.IntegrityError, match='business documents are retained'):
            db.raw.execute('DELETE FROM transactions WHERE id=?', (result['id'],))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
