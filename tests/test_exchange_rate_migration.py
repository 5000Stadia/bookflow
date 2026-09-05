"""Frozen co0008 upgrade, declared schema, copied rates and migration rollback."""
import importlib
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic import command

from bookflow.company import rates, schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config, current_revision_raw, migrate_to_head
from tests.test_attachment_migration import _old_data
from tests.test_migration_chain import _make_revision, _normalized_schema
from tests.test_row6_note_migration import _schema_semantics
from tests.test_exchange_rates import path, set_rate, snapshot


def table_rows(db):
    return {name: db.raw.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
        for (name,) in db.raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'")}


def test_upgrade_preserves_all_old_rows_and_matches_fresh(tmp_path):
    old, fresh = tmp_path / 'old.db', tmp_path / 'fresh.db'
    _make_revision(old, 'company', 'co0007', _old_data)
    _make_revision(fresh, 'company', 'co0008', _old_data)
    with open_database(old, writable=True) as db:
        before, old_schema = table_rows(db), _normalized_schema(db.raw)
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0007', 'co0008')
        after = table_rows(db)
        assert all(after[name] == rows for name, rows in before.items())
        assert set(after) - set(before) == {'exchange_rates'}
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0008', 'co0008')
    assert _schema_semantics(old) == _schema_semantics(fresh)
    saved, = (tmp_path / 'backups').glob('*-from-co0007.db')
    with open_database(saved, writable=False) as db:
        assert table_rows(db) == before and _normalized_schema(db.raw) == old_schema


def test_declared_schema_and_frozen_migration(tmp_path, monkeypatch):
    old, fresh = tmp_path / 'old.db', tmp_path / 'fresh.db'
    _make_revision(old, 'company', 'co0007', _old_data)
    _make_revision(fresh, 'company', 'co0008', _old_data)
    with open_database(fresh, writable=False) as db:
        actual = sa.Table('exchange_rates', sa.MetaData(), autoload_with=db.conn)
        expected = schema.exchange_rates
        assert [(c.name, str(c.type), c.nullable, c.primary_key) for c in actual.c] == [
            (c.name, str(c.type), c.nullable, c.primary_key) for c in expected.c]
        assert {(f.parent.name, f.target_fullname) for f in actual.foreign_keys} == {
            (f.parent.name, f.target_fullname) for f in expected.foreign_keys}
        assert {c.sqltext.text for c in actual.constraints if isinstance(c, sa.CheckConstraint)} == {
            c.sqltext.text for c in expected.constraints if isinstance(c, sa.CheckConstraint)}
    monkeypatch.setattr(schema, 'metadata', sa.MetaData())
    monkeypatch.setattr(schema, 'exchange_rates', None)
    with open_database(old, writable=True) as db:
        command.upgrade(_config('company', db.conn), 'co0008')
    assert _schema_semantics(old) == _schema_semantics(fresh)


def test_copy_reopen_retains_rates_audit_and_principals(client, tmp_path):
    first = set_rate(client)
    set_rate(client, rate='0.007', expected_version=1)
    original = path(client)
    expected = snapshot(original)
    destination = tmp_path / 'copied-company'
    shutil.copytree(original.parent, destination)
    copied = destination / 'company.db'
    assert snapshot(copied) == expected
    with open_database(copied, writable=False) as db:
        assert rates.lookup(SimpleNamespace(company=db), '2026-01-12', 'JPY', 'USD')['id'] == first['id']
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    assert current_revision_raw(copied) == 'co0008'


def test_failed_migration_restores_rows_and_schema(tmp_path):
    old = tmp_path / 'old.db'
    _make_revision(old, 'company', 'co0007', _old_data)
    module = importlib.import_module('bookflow.storage.company_migrations.versions.0008_exchange_rates')
    with open_database(old, writable=True) as db:
        # Fail after the real CREATE TABLE has executed, inside the runner transaction.
        db.raw.execute("CREATE TABLE migration_failure_marker (id INTEGER)")
        db.raw.execute("CREATE TRIGGER fail_revision BEFORE UPDATE ON alembic_version WHEN NEW.version_num = 'co0008' BEGIN SELECT RAISE(ABORT, 'late migration failure'); END")
        before, old_schema = table_rows(db), _normalized_schema(db.raw)
        from bookflow import BookflowError
        with pytest.raises(BookflowError) as err:
            migrate_to_head(db, 'company', tmp_path / 'backups')
        assert err.value.code == 'E_MIGRATION_FAILED'
        assert table_rows(db) == before and _normalized_schema(db.raw) == old_schema
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0007',)
    assert 'exchange_rates' in module.DDL


@pytest.mark.parametrize('column,value', [('version', 0), ('version', 1.5), ('from_currency', 'usd'),
    ('from_currency', 'US'), ('from_currency', 'USD'), ('source', 'fetched'), ('rate', ''), ('rate', '1' * 32), ('entered_by', 'missing')])
def test_storage_constraints(client, column, value):
    row = set_rate(client)
    with open_database(path(client), writable=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.raw.execute(f'UPDATE exchange_rates SET {column}=? WHERE id=?', (value, row['id']))


def test_rate_identity_pair_unique_and_downgrade_rejected(client):
    row = set_rate(client)
    with open_database(path(client), writable=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.raw.execute("INSERT INTO exchange_rates SELECT 'other', version,date,from_currency,to_currency,rate,source,entered_by,entered_at FROM exchange_rates WHERE id=?", (row['id'],))
        before = table_rows(db)
        with pytest.raises(NotImplementedError):
            command.downgrade(_config('company', db.conn), 'co0007')
        assert table_rows(db) == before
