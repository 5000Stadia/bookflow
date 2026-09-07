"""Preserving co22→co23 with actual ordinary receipts and extension values."""
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.payment_raw_evidence import table,attachments

BASE='703c002945b2ccbd564d98ea20b0a0d5f690f300'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0023_deposit_coordinate')


@pytest.fixture(scope='module')
def old_co22(tmp_path_factory):
    parent=tmp_path_factory.mktemp('coordinate-co22');source=parent/'source';source.mkdir()
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    root=parent/'root'
    code='''import bookflow,sys,io,pytest,sqlite3,json
from tests.test_deposit_lifecycle import driver
from tests.test_row8_journal import database_path
c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset();co="Demo Plumbing Co"
p=c.customer.create(name="C migration",company=co)
b=c.account.create(name="C migration bank",type="bank",company=co)
i=c.account.create(name="C migration income",type="other_income",company=co)
path=database_path(c)
with sqlite3.connect(path) as db:
 db.execute("ALTER TABLE deposit_operations ADD COLUMN local_blob BLOB DEFAULT X'410042FF'")
 db.execute("ALTER TABLE deposit_operations ADD COLUMN local_real REAL DEFAULT 0.10000000000000002")
 db.execute("ALTER TABLE deposit_operation_items ADD COLUMN local_text TEXT DEFAULT 'local text'")
 db.execute("ALTER TABLE deposit_operation_items ADD COLUMN local_nul TEXT GENERATED ALWAYS AS ('A'||char(0)||'B') VIRTUAL")
 db.execute("CREATE TABLE local_raw(id INTEGER PRIMARY KEY,t TEXT,b BLOB,r REAL)")
 db.execute("INSERT INTO local_raw VALUES (97,?,?,?)",('A\\0B',b'\\0\\x80\\xff',0.10000000000000002))
 db.execute("CREATE VIEW local_coordinate_view AS SELECT id,local_blob FROM deposit_operations")
 db.execute("CREATE INDEX local_coordinate_index ON deposit_operations(substr(operation_key,1,2)) WHERE local_real IS NOT NULL")
 db.execute("CREATE TABLE local_external(id INTEGER PRIMARY KEY,op TEXT REFERENCES deposit_operations(id))")
patch=pytest.MonkeyPatch();d=driver.__wrapped__(c,patch)
post=d.run('post',dict(operation_key='C-legacy',document=dict(mode='inline',date='2026-06-03',deposit_to=b['id'],memo='Portable\\0memo',additional=[dict(received_from=dict(kind='customer',id=p['id']),from_account=i['id'],amount='12.34')])))
with sqlite3.connect(path) as db: db.execute('INSERT INTO local_external VALUES (1,?)',(post.operation_id,))
c.attachment.add(record_type='customer',record_id=p['id'],original_filename='C-bytes.bin',input_stream=io.BytesIO(b'A\\0B\\x80\\xff'),company=co)
print(json.dumps(dict(path=str(path),operation=post.operation_id)))
'''
    run=subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src')+':'+str(Path(__file__).parents[1]),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (parent/'seed.log').write_text(run.stdout+run.stderr)
    assert run.returncode==0,run.stderr
    return parent,root,source


def raw(db):
    names=[r[0] for r in db.execute("SELECT name FROM main.sqlite_schema WHERE type='table' AND name <> 'alembic_version' ORDER BY name")]
    return {name:table(db,name) for name in names}


def test_populated_preservation_and_old_binary_refusal(old_co22,tmp_path):
    _,original,source=old_co22
    root=tmp_path/'root';shutil.copytree(original,root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with open_database(path,writable=True) as db:
        before=raw(db.raw)
        ddl=db.raw.execute('SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name').fetchall()
        files=attachments(root)
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0022','co0023')
        assert raw(db.raw)==before
        changed={name:sql for kind,name,owner,sql in db.raw.execute('SELECT type,name,tbl_name,sql FROM main.sqlite_schema') if kind=='table'}
        stored={(kind,name):(owner,sql) for kind,name,owner,sql in db.raw.execute('SELECT type,name,tbl_name,sql FROM main.sqlite_schema')}
        for kind,name,owner,sql in ddl:
            if kind=='table' and name in M.CHANGES:
                # SQLite quotes the renamed table identifier; definitions match.
                parser=importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
                _,oldparts,suffix=parser._definitions(sql,name)
                _,newparts,newsuffix=parser._definitions(changed[name],name)
                old,new=M.CHANGES[name]
                assert [v.replace(old,new) for v in oldparts]==newparts
                assert suffix==newsuffix
            else:assert stored[kind,name]==(owner,sql),(kind,name)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0023','co0023')
    assert attachments(root)==files
    co21=tmp_path/'co21-source';co21.mkdir()
    archive=subprocess.check_output(['git','archive','162193259399f0554db07840e26c93444051664b','src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(co21,filter='data')
    for binary,verb in [(binary,verb) for binary in (source,co21) for verb in ('read','write')]:
        code="""import sys
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import require_head_readonly,migrate_to_head
from pathlib import Path
with open_database(Path(sys.argv[1]),writable=sys.argv[2]=='write') as db:
 try:
  if sys.argv[2]=='read':require_head_readonly(Path(sys.argv[1]),'company')
  else:migrate_to_head(db,'company',None)
 except Exception as e:
  print(getattr(e,'code',type(e).__name__))
 else: raise AssertionError('old binary admitted successor')
"""
        run=subprocess.run([sys.executable,'-c',code,str(path),verb],cwd=binary,env=dict(os.environ,PYTHONPATH=str(binary/'src')),capture_output=True,text=True)
        assert run.returncode==0 and 'E_SCHEMA_UNKNOWN' in run.stdout,run.stdout+run.stderr
    with sqlite3.connect(path) as db:assert raw(db)==before
    (tmp_path/'preservation.json').write_text(json.dumps(dict(before=before,attachments=files),indent=2))


@pytest.mark.parametrize('problem',['reserved_case','temp_shadow','temp_trigger','attached_shadow','unknown_guard','copy','raw_mismatch','recreate','fk','publish'])
def test_rejection_preserves_complete_state(old_co22,tmp_path,monkeypatch,problem):
    _,original,_=old_co22
    root=tmp_path/'root';shutil.copytree(original,root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with open_database(path,writable=True) as db:
        if problem=='reserved_case':db.raw.execute('CREATE TABLE _CO0023_DEPOSIT_OPERATIONS(value TEXT)')
        if problem=='temp_shadow':db.raw.execute('CREATE TEMP TABLE deposit_operations(value TEXT)')
        if problem=='temp_trigger':db.raw.execute("CREATE TEMP TRIGGER temp_c AFTER INSERT ON main.deposit_operations BEGIN SELECT 1; END")
        if problem=='attached_shadow':
            db.raw.execute("ATTACH DATABASE ':memory:' AS owned_aux")
            db.raw.execute('CREATE TABLE owned_aux.deposit_operations(value TEXT)')
            db.raw.execute("INSERT INTO owned_aux.deposit_operations VALUES ('owned attached sentinel')")
        if problem=='unknown_guard':
            # Deliberate legacy local extension, only within the owned migration fixture.
            db.raw.execute('DROP TRIGGER deposit_operations_no_update')
            db.raw.execute("CREATE TRIGGER deposit_operations_no_update BEFORE UPDATE ON deposit_operations BEGIN SELECT RAISE(ABORT,'local guard'); END")
        before=tuple(db.raw.iterdump());temp=db.raw.execute('SELECT * FROM temp.sqlite_schema').fetchall()
        attached=db.raw.execute('SELECT * FROM owned_aux.deposit_operations').fetchall() if problem=='attached_shadow' else None
        original_exec=db.conn.exec_driver_sql
        def fault(statement,*args,**kw):
            if problem=='raw_mismatch' and statement.startswith('INSERT INTO main."_co0023_deposit_operations"'):
                return original_exec(statement+" WHERE operation_key <> 'C-legacy'",*args,**kw)
            if ((problem=='copy' and statement.startswith('INSERT INTO main."_co0023_')) or
                (problem=='recreate' and statement.startswith('CREATE INDEX local_coordinate_index')) or
                (problem=='publish' and statement.startswith('UPDATE alembic_version'))):
                raise RuntimeError('owned migration fault')
            if problem=='fk' and statement=='PRAGMA main.foreign_key_check' and '_co0023_' in str(getattr(fault,'phase','')):
                raise RuntimeError('owned final FK fault')
            if statement.startswith('ALTER TABLE main."_co0023_'):fault.phase=statement
            return original_exec(statement,*args,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(db.conn,'exec_driver_sql',fault)
            original_execute=db.conn.execute
            def execute(statement,*args,**kwargs):
                if problem=='publish' and str(statement).startswith('UPDATE alembic_version'):
                    raise RuntimeError('owned publication fault')
                return original_execute(statement,*args,**kwargs)
            patch.setattr(db.conn,'execute',execute)
            from bookflow import BookflowError
            with pytest.raises((BookflowError,RuntimeError)):
                migrate_to_head(db,'company',tmp_path/'backups')
        assert tuple(db.raw.iterdump())==before
        assert db.raw.execute('SELECT * FROM temp.sqlite_schema').fetchall()==temp
        if problem=='attached_shadow':assert db.raw.execute('SELECT * FROM owned_aux.deposit_operations').fetchall()==attached
