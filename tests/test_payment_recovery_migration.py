"""Frozen co17 preservation, fresh metadata parity, rollback and ordinary extensions."""
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
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from bookflow import BookflowError
from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.test_payment_migration import raw_snapshot

BASE='2dbce6683dcdd968b8c950c86f4c5e8321567027'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0018_payment_recovery')
TABLES=('payment_selection_recoveries','payment_selection_recovery_chunks','payment_selection_recovery_items','payment_selection_recovery_active')

@pytest.fixture(scope='module')
def co17(tmp_path_factory):
    parent=tmp_path_factory.mktemp('co17-frozen');source=parent/'source';source.mkdir()
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    root=parent/'root'
    result=subprocess.run([sys.executable,'-c','import bookflow,sys;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset()',str(root)],
        cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    return root


def test_metadata_literal_migration_and_four_tables(tmp_path):
    compiled=[]
    for name in TABLES:
        table=schema.metadata.tables[name]
        compiled.append(str(CreateTable(table).compile(dialect=dialect())).strip())
        compiled.extend(str(CreateIndex(index).compile(dialect=dialect())) for index in sorted(table.indexes,key=lambda i:i.name))
    assert tuple(compiled)==M.DDL
    with open_database(tmp_path/'company.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None)==(None,'co0018')
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'sqlite_stat%'").fetchall()==[]


@pytest.mark.timeout(180)
def test_every_old_row_storage_byte_ddl_attachment_and_failed_upgrade(co17,tmp_path):
    root=tmp_path/'root';shutil.copytree(co17,root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE payment_selections ADD COLUMN local_blob BLOB DEFAULT X'0080FF'")
        db.execute('CREATE VIEW local_recovery_witness AS SELECT * FROM payment_selections')
        db.execute('CREATE INDEX local_recovery_witness_index ON payment_selections(local_blob)')
        db.execute("CREATE TRIGGER local_recovery_witness_guard BEFORE UPDATE OF local_blob ON payment_selections BEGIN SELECT RAISE(ABORT,'local evidence');END")
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name<>'alembic_version'")]
        before={name:raw_snapshot(db,name) for name in names}
        ddl=db.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        db.execute('CREATE TABLE payment_selection_recovery_active (reserved_collision TEXT)')
    attachments={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and 'attachments' in p.parts}
    with open_database(path,writable=True) as db:
        with pytest.raises(BookflowError) as caught:migrate_to_head(db,'company',None)
        assert caught.value.code=='E_MIGRATION_FAILED'
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0017',)
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name='payment_selection_recoveries'").fetchall()==[]
        db.raw.execute('DROP TABLE payment_selection_recovery_active')
        assert migrate_to_head(db,'company',None)==('co0017','co0018')
        assert {name:raw_snapshot(db.raw,name) for name in names}==before
        after={row[1]:row for row in db.raw.execute('SELECT type,name,sql FROM sqlite_schema')}
        assert all(after[row[1]]==row for row in ddl)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert migrate_to_head(db,'company',None)==('co0018','co0018')
    assert {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and 'attachments' in p.parts}==attachments
