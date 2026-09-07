"""Frozen co13 public upgrade with exact old rows, local DDL and attachments."""
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

from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head

BASE = '14a871a31268cbb5c0311ae5352d9965a587e492'
MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0014_customer_payments')


@pytest.fixture(scope='module')
def historical_root(tmp_path_factory):
    parent = tmp_path_factory.mktemp('co13-payment-base')
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


@pytest.fixture(scope='module')
def frozen_co14(tmp_path_factory):
    source = tmp_path_factory.mktemp('frozen-co14-source')
    archive = subprocess.check_output(['git', 'archive',
        '38891355d31ea379f4d1f76797523e17471e289e', 'src'], cwd=Path(__file__).resolve().parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    return source


@pytest.fixture(autouse=True)
def historical_target(monkeypatch):
    # This module owns co13→co14. New head/full-chain acceptance is separate.
    monkeypatch.setitem(HEADS, 'company', 'co0014')


def raw_snapshot(raw, table):
    columns = [row[1] for row in raw.execute(f'PRAGMA table_xinfo("{table}")')]
    expressions = ['rowid']
    for column in columns:
        quoted = '"' + column.replace('"', '""') + '"'
        expressions += [f'typeof({quoted})', f'quote({quoted})', f'CAST({quoted} AS BLOB)']
    return columns, ','.join(expressions), raw.execute(f'SELECT {",".join(expressions)} FROM "{table}" ORDER BY rowid').fetchall()


def test_documented_cli_upgrade_preserves_every_old_raw_column(historical_root, tmp_path, frozen_co14):
    root = tmp_path / 'data'
    shutil.copytree(historical_root, root)
    path = next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0013',)
        for table in ('transactions', 'document_lines', 'posting_line_sources', 'company_info'):
            raw.execute(f"ALTER TABLE {table} ADD COLUMN local_blob BLOB DEFAULT X'00FF80'")
            raw.execute(f'ALTER TABLE {table} ADD COLUMN local_length INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL')
            raw.execute(f'CREATE INDEX local_{table}_index ON {table}(local_blob)')
            raw.execute(f'CREATE VIEW local_{table}_view AS SELECT * FROM {table}')
            raw.execute(f"CREATE TRIGGER local_{table}_guard BEFORE UPDATE OF local_blob ON {table} BEGIN SELECT RAISE(ABORT, 'local guard'); END")
        tables = [row[0] for row in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'")]
        before = {table: raw_snapshot(raw, table) for table in tables}
        objects = raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name LIKE 'local_%' ORDER BY type,name").fetchall()
    files = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file() and 'attachments' in p.parts}
    result = subprocess.run([str(Path(sys.executable).parent / 'bookflow'), 'upgrade', '--data-root', str(root),
                             '--reason', 'Disposable co14 preserving witness', '--json'], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=str(frozen_co14 / 'src')))
    assert result.returncode == 0, result.stdout + result.stderr
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0014',)
        for table, (columns, selected, rows) in before.items():
            assert [r[1] for r in raw.execute(f'PRAGMA table_xinfo("{table}")')][:len(columns)] == columns
            if table == 'principals':
                # The documented upgrade publishes its system identity and
                # refreshes the caller heartbeat. All OLD rows/other columns
                # retain their exact rowid, storage class and bytes.
                heartbeat = 1 + columns.index('last_seen_at') * 3
                after = raw.execute(f'SELECT {selected} FROM "principals" ORDER BY rowid').fetchall()
                by_id = {row[0]: row for row in after}
                for old in rows:
                    current = by_id[old[0]]
                    assert current[:heartbeat] + current[heartbeat + 3:] == old[:heartbeat] + old[heartbeat + 3:]
                    assert current[heartbeat] == 'text' and current[heartbeat + 2] >= old[heartbeat + 2]
                added = [row for row in after if row[0] not in {old[0] for old in rows}]
                assert len(added) == 1
                assert raw.execute('SELECT username,display_name,kind FROM principals WHERE rowid=?', (added[0][0],)).fetchone() == ('system', 'System', 'system')
                continue
            if table in ('audit_events', 'audit_entries'):
                after = raw.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall()
                assert after[:len(rows)] == rows
                assert len(after) == len(rows) + 1
                if table == 'audit_events':
                    assert raw.execute('SELECT command,actor_kind,summary FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone() == ('upgrade', 'system', 'migrated from co0013 to co0014')
                else:
                    from bookflow.core.audit import decode_snapshot
                    entry = raw.execute('SELECT record_type,action,version_before,version_after,after FROM audit_entries ORDER BY rowid DESC LIMIT 1').fetchone()
                    assert entry[:4] == ('company_info', 'migrate', None, None)
                    assert decode_snapshot(entry[4]) == {'schema_revision': 'co0014', 'from': 'co0013'}
                continue
            assert raw.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid').fetchall() == rows, table
        assert raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name LIKE 'local_%' ORDER BY type,name").fetchall() == objects
        assert raw.execute('SELECT automatically_apply_payments,automatically_calculate_payments,use_undeposited_funds_for_payments FROM company_info').fetchall() == [(0, 0, 1)]
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert {name: (root / name).read_bytes() for name in files} == files


def test_frozen_new_ddl_matches_metadata_and_guards(tmp_path, frozen_co14):
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company.payment_guards import statements
    import json
    script = """
import importlib,json
from bookflow.company import schema
from bookflow.company.payment_guards import statements
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.sqlite import dialect
m=importlib.import_module('bookflow.storage.company_migrations.versions.0014_customer_payments')
expected=[]
for name in m.NEW_TABLES:
    table=schema.metadata.tables[name]
    expected.append(str(CreateTable(table).compile(dialect=dialect())).strip())
    expected.extend(str(CreateIndex(i).compile(dialect=dialect())) for i in sorted(table.indexes,key=lambda i:i.name))
print(json.dumps([expected,list(statements())]))
"""
    expected, guards = json.loads(subprocess.check_output([sys.executable, '-c', script],
        cwd=frozen_co14, env=dict(os.environ, PYTHONPATH=str(frozen_co14 / 'src')), text=True))
    assert tuple(expected) == MIGRATION.DDL
    assert tuple(guards) == MIGRATION.GUARDS
    with open_database(tmp_path / 'company.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, 'co0014')
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        for name in MIGRATION.NEW_TABLES:
            assert set(sa.inspect(db.conn).get_pk_constraint(name)['constrained_columns']) == set(schema.metadata.tables[name].primary_key.columns.keys())


@pytest.mark.parametrize('attack', ['reserved_column', 'reserved_table', 'shadow_rowid', 'unknown_guard'])
def test_unsupported_local_shape_rolls_back(historical_root, tmp_path, attack):
    path = tmp_path / 'company.db'
    shutil.copyfile(next(historical_root.glob('organizations/*/Demo Plumbing Co/company.db')), path)
    with open_database(path, writable=True) as db:
        if attack == 'reserved_column':
            db.raw.execute('ALTER TABLE posting_line_sources ADD COLUMN payment_component_id TEXT')
        elif attack == 'reserved_table':
            db.raw.execute('CREATE TABLE applications (local TEXT)')
        elif attack == 'shadow_rowid':
            db.raw.execute('ALTER TABLE transactions ADD COLUMN rowid TEXT')
        else:
            db.raw.execute("CREATE TRIGGER custom_type_guard BEFORE INSERT ON transactions WHEN NEW.type <> 'invoice' BEGIN SELECT RAISE(ABORT, 'local type contract'); END")
        before = db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        with pytest.raises(Exception) as caught:
            migrate_to_head(db, 'company', None)
        assert getattr(caught.value, 'code', None) == 'E_MIGRATION_FAILED'
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0013',)
        assert db.raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall() == before
