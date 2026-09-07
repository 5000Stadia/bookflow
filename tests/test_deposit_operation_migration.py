"""Append-only co0021: exact frozen DDL and preservation of accepted co0020."""
import importlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.payment_raw_evidence import table

BASE='1e7ae551f29eb3e5ecbbecc5055db80c060452ce'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0021_deposit_operations')


def test_operation_ddl_matches_fresh_migration(tmp_path):
    successor=importlib.import_module('bookflow.storage.company_migrations.versions.0023_deposit_coordinate')
    expected=[]
    for statement in M.DDL:
        for old,new in successor.CHANGES.values():statement=statement.replace(old,new)
        expected.append(statement)
    assert expected==[str(CreateTable(schema.metadata.tables[name]).compile(dialect=dialect())).strip() for name in M.NEW_TABLES]
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None)==(None,'co0023')
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert migrate_to_head(db,'company',None)==('co0023','co0023')


def test_co20_complete_raw_rows_and_local_ddl_preserved(tmp_path):
    source=tmp_path/'source';source.mkdir();root=tmp_path/'root'
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    script='import bookflow,sys;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset()'
    result=subprocess.run([sys.executable,'-c',script,str(root)],cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (tmp_path/'co20-seed.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0020',)
        raw.execute('CREATE TABLE local_g2_bytes (id INTEGER PRIMARY KEY, t TEXT, b BLOB)')
        raw.execute('INSERT INTO local_g2_bytes VALUES (7,?,?)',('A\x00B',b'\x00\xff\x80'))
        raw.execute('CREATE INDEX local_g2_expr ON local_g2_bytes (length(t))')
        raw.execute('CREATE VIEW local_g2_view AS SELECT id,b FROM local_g2_bytes')
        raw.execute('CREATE TRIGGER local_g2_trigger AFTER INSERT ON local_g2_bytes BEGIN SELECT 1; END')
        raw.commit()
        names=[r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version' ORDER BY name")]
        before={name:table(raw,name) for name in names}
        ddl=raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    with open_database(path,writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0020','co0023')
        assert {name:table(db.raw,name) for name in names}==before
        after=set(db.raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall())
        assert set(ddl)<=after
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
