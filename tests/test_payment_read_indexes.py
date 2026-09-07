"""co0016→co0017 additive index and public preservation witnesses."""
import importlib
import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest
import sqlalchemy as sa
from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.test_payment_migration import raw_snapshot

BASE = 'f527a7185656745a66d136e211c5a4f140c260ad'
MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0017_payment_read_indexes')


@pytest.fixture(scope='module')
def co16(tmp_path_factory):
    parent = tmp_path_factory.mktemp('co16-read-indexes')
    source = parent / 'source'
    source.mkdir()
    archive = subprocess.check_output(['git', 'archive', BASE, 'src'], cwd=Path(__file__).resolve().parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    root = parent / 'data'
    result = subprocess.run([sys.executable, '-c',
        'import bookflow,sys; c=bookflow.connect(data_root=sys.argv[1]); c.init(); c.demo.reset()', str(root)],
        env=dict(os.environ, PYTHONPATH=str(source / 'src'), PYTHONDONTWRITEBYTECODE='1'), cwd=source,
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return root


def test_exact_fresh_indexes(tmp_path):
    expected = {sql.rstrip(';') for sql in MIGRATION.DDL}
    compiled = {str(CreateIndex(index).compile(dialect=dialect()))
                for table in schema.metadata.tables.values() for index in table.indexes
                if index.name.startswith('ix_co17_')}
    assert compiled == expected
    assert len(expected) == 10
    with open_database(tmp_path / 'company.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, 'co0017')
        actual = {r[0] for r in db.raw.execute("SELECT sql FROM sqlite_schema WHERE name LIKE 'ix_co17_%'")}
        assert actual == expected
        for statement in MIGRATION.DDL:
            name = statement.split()[2]
            table = statement.split()[4]
            details = db.raw.execute(f'PRAGMA index_xinfo({name})').fetchall()
            assert all(r[3:5] == (0, 'BINARY') for r in details if r[5])
            entry = next(r for r in db.raw.execute(f'PRAGMA index_list({table})') if r[1] == name)
            assert entry[2:] == (0, 'c', 0)
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'sqlite_stat%'").fetchall() == []


@pytest.mark.parametrize('statistics', [False, True])
def test_public_preserving_upgrade(co16, tmp_path, statistics):
    root = tmp_path / 'data'
    shutil.copytree(co16, root)
    path = next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0016',)
        for table in MIGRATION.REQUIRED:
            raw.execute(f"ALTER TABLE {table} ADD COLUMN local_blob BLOB DEFAULT X'00FF80'")
            raw.execute(f'ALTER TABLE {table} ADD COLUMN local_length INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL')
            raw.execute(f'CREATE INDEX local_{table}_index ON {table}(local_blob)')
            raw.execute(f'CREATE VIEW local_{table}_view AS SELECT * FROM {table}')
            raw.execute(f"CREATE TRIGGER local_{table}_guard BEFORE UPDATE OF local_blob ON {table} BEGIN SELECT RAISE(ABORT, 'local guard'); END")
        if statistics:
            raw.execute('ANALYZE')  # Deliberate PRE-upgrade local statistics witness only.
        tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name <> 'alembic_version'")]
        before = {table: raw_snapshot(raw, table) for table in tables}
        xinfo = {table: raw.execute(f'PRAGMA table_xinfo("{table}")').fetchall() for table in tables}
        objects = raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
    files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file() and 'attachments' in p.parts}
    args = [str(Path(sys.executable).parent / 'bookflow'), 'upgrade', '--data-root', str(root),
            '--reason', 'Disposable co17 preserving witness', '--json']
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0017',)
        after_objects = raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        assert [r for r in after_objects if not r[1].startswith('ix_co17_')] == objects
        for table, (columns, selected, rows) in before.items():
            assert raw.execute(f'PRAGMA table_xinfo("{table}")').fetchall() == xinfo[table]
            after = raw.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall()
            if table == 'principals':
                heartbeat = 1 + columns.index('last_seen_at') * 3
                by_id = {r[0]: r for r in after}
                for old in rows:
                    new = by_id[old[0]]
                    assert new[:heartbeat] + new[heartbeat+3:] == old[:heartbeat] + old[heartbeat+3:]
                added = [r for r in after if r[0] not in {old[0] for old in rows}]
                assert len(added) <= 1
                for row in added:
                    assert raw.execute('SELECT username,display_name,kind FROM principals WHERE rowid=?', (row[0],)).fetchone() == ('system','System','system')
            elif table in ('audit_events', 'audit_entries'):
                assert after[:len(rows)] == rows
                assert len(after) == len(rows) + 1
                if table == 'audit_events':
                    assert raw.execute('SELECT command,actor_kind,summary FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone() == ('upgrade','system','migrated from co0016 to co0017')
                else:
                    from bookflow.core.audit import decode_snapshot
                    entry = raw.execute('SELECT record_type,action,after FROM audit_entries ORDER BY rowid DESC LIMIT 1').fetchone()
                    assert entry[:2] == ('company_info','migrate')
                    assert decode_snapshot(entry[2]) == {'schema_revision':'co0017','from':'co0016'}
            else:
                assert after == rows, table
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert {name:(root/name).read_bytes() for name in files} == files
    assert list(path.parent.glob('backups/*from-co0016.db'))
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall() == after_objects


@pytest.mark.parametrize('kind', ['table','view','index','trigger'])
def test_reserved_names_reject_before_ddl(co16, tmp_path, kind):
    path = tmp_path / 'company.db'
    shutil.copyfile(next(co16.glob('organizations/*/Demo Plumbing Co/company.db')), path)
    name = 'IX_CO17_PAYMENT_PROFILE'
    sql = {'table':f'CREATE TABLE {name}(value TEXT)', 'view':f'CREATE VIEW {name} AS SELECT 1',
           'index':f'CREATE INDEX {name} ON transactions(id)',
           'trigger':f'CREATE TRIGGER {name} BEFORE UPDATE ON transactions BEGIN SELECT 1; END'}[kind]
    with open_database(path, writable=True) as db:
        db.raw.execute(sql)
        before = db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        with pytest.raises(Exception) as caught:
            migrate_to_head(db,'company',tmp_path/'backups')
        assert getattr(caught.value,'code',None) == 'E_MIGRATION_FAILED'
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0016',)
        assert db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall() == before


def test_late_failure_rolls_back_and_retry(co16, tmp_path):
    path = tmp_path / 'company.db'
    shutil.copyfile(next(co16.glob('organizations/*/Demo Plumbing Co/company.db')), path)
    with open_database(path, writable=True) as db:
        before = db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        def fail(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('CREATE INDEX ix_co17_payment_profile'):
                raise RuntimeError('injected final-index failure')
        sa.event.listen(db.conn, 'before_cursor_execute', fail)
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', tmp_path/'backups')
        assert getattr(caught.value,'code',None) == 'E_MIGRATION_FAILED'
        sa.event.remove(db.conn, 'before_cursor_execute', fail)
        assert db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall() == before
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0016',)
        assert list((tmp_path/'backups').glob('*from-co0016.db'))
        assert migrate_to_head(db,'company',tmp_path/'backups') == ('co0016','co0017')
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)


@pytest.mark.parametrize('change', ['affinity','collation','missing'])
def test_incompatible_required_shape_preflight(tmp_path, change):
    # A focused local-shape witness; no historical guard is removed.
    with open_database(tmp_path/'shape.db', writable=True, create=True) as db:
        for table, columns in MIGRATION.REQUIRED.items():
            definitions = []
            for column, affinity in columns.items():
                if table == 'transactions' and column == 'number':
                    if change == 'missing':
                        continue
                    if change == 'affinity':
                        affinity = 'BLOB'
                    if change == 'collation':
                        affinity += ' COLLATE NOCASE'
                definitions.append(f'"{column}" {affinity}')
            db.raw.execute(f'CREATE TABLE "{table}" ({",".join(definitions)})')
        before = db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        with pytest.raises(RuntimeError, match='co0017 incompatible'):
            MIGRATION._preflight(db.conn)
        assert db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall() == before
