"""N is empty, additive and off; these are not activation/backfill tests."""
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
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema
from bookflow.company.deposit_dependencies import RECONCILIATION
from bookflow.company.reconciliation_schema import guards
from bookflow.company.reconciliation_storage_validation import validate
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head, feature_admission
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import table, attachments

BASE='a041caa2b98365649112eac55d600c586ca22b08'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0022_reconciliation_storage')


def empty(raw):
    assert all(raw.execute('SELECT count(*) FROM "'+n+'"').fetchone()==(0,) for n in M.NEW_TABLES)
    validate({n.removeprefix('reconciliation_'):[] for n in M.NEW_TABLES})


def test_exact_metadata_fresh_current_empty_off_and_composite_fk_targets(tmp_path):
    ddl=[]
    for name in M.NEW_TABLES:
        t=schema.metadata.tables[name]
        ddl.append(str(CreateTable(t).compile(dialect=dialect())).strip())
        ddl.extend(str(CreateIndex(i).compile(dialect=dialect())) for i in sorted(t.indexes,key=lambda i:i.name))
        assert all(i.table.name.startswith('reconciliation_') for i in t.indexes)
        for fk in t.foreign_key_constraints:
            remote=fk.referred_table
            columns=tuple(e.column.name for e in fk.elements)
            targets=[tuple(c.name for c in remote.primary_key)]
            targets += [tuple(c.name for c in x.columns) for x in remote.constraints if x.__class__.__name__=='UniqueConstraint']
            assert columns in targets,(name,columns,remote.name)
    assert M.DDL==ddl and M.GUARDS==guards(M.NEW_TABLES)
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None)==(None,'co0022')
        empty(db.raw)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert feature_admission(db,RECONCILIATION,resolver=None) is None
        before=list(db.raw.iterdump())
        assert migrate_to_head(db,'company',None)==('co0022','co0022')
        assert list(db.raw.iterdump())==before


@pytest.fixture(scope='module')
def old(tmp_path_factory):
    parent=tmp_path_factory.mktemp('co22-old');source=parent/'source';source.mkdir();root=parent/'root'
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    code='import bookflow,sys,io;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset();co="Demo Plumbing Co";a=c.customer.create(name="N preservation",company=co);c.attachment.add(record_type="customer",record_id=a["id"],original_filename="N.bin",input_stream=io.BytesIO(b"A\\0\\xff"),company=co)'
    result=subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (parent/'old-seed.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr
    return root,source


def test_old_upgrade_preserves_all_rows_rowids_storage_classes_custom_ddl_attachments_backup(old,tmp_path):
    root=tmp_path/'root';shutil.copytree(old[0],root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0021',)
        raw.execute('ALTER TABLE transactions ADD COLUMN local_text TEXT')
        raw.execute('CREATE TABLE local_N (id INTEGER PRIMARY KEY,t TEXT,b BLOB,f REAL)')
        raw.execute('INSERT INTO local_N VALUES (77,?,?,?)',('a\0b',b'\0\xff',0.1))
        raw.execute('CREATE VIEW local_N_view AS SELECT * FROM local_N')
        raw.execute('CREATE INDEX local_N_expression ON local_N(length(t)) WHERE t IS NOT NULL')
        raw.execute('CREATE TRIGGER local_N_trigger AFTER INSERT ON local_N BEGIN SELECT 1; END')
        raw.commit()
        names=[v[0] for v in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name<>'alembic_version'")]
        before={n:table(raw,n) for n in names}
        ddl=raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
    files=attachments(root)
    with open_database(path,writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0021','co0022')
        assert {n:table(db.raw,n) for n in names}==before
        assert set(ddl)<=set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema').fetchall())
        empty(db.raw)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    assert attachments(root)==files
    backups=list((tmp_path/'backups').rglob('*.db'))
    assert backups
    with sqlite3.connect(backups[0]) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0021',)
        assert {n:table(raw,n) for n in names}==before
    (tmp_path/'preservation.json').write_text(json.dumps(dict(base=BASE,tables=before,attachments=files),indent=2))
    # Actual immediately preceding binary rejects the N schema. A remains future.
    script='import bookflow,sys;c=bookflow.connect(data_root=sys.argv[1]);\ntry:c.run(sys.argv[2],{} if sys.argv[2]=="company show" else dict(date="2026-01-01",lines=[dict(account="Checking",side="debit",amount="1"),dict(account="Opening Balance Equity",side="credit",amount="1")]),company="Demo Plumbing Co")\nexcept bookflow.BookflowError as e:print(e.code);sys.exit(0 if e.code=="E_SCHEMA_UNKNOWN" else 2)\nsys.exit(3)'
    for command in ('company show','journal post'):
        result=subprocess.run([sys.executable,'-c',script,str(root),command],cwd=old[1],env=dict(os.environ,PYTHONPATH=str(old[1]/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
        (tmp_path/(command.replace(' ','-')+'-old.log')).write_text(result.stdout+result.stderr)
        assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('kind',['table','view','index','trigger','temp','attached'])
def test_complete_case_insensitive_collision_preflight_rolls_back(old,tmp_path,kind):
    path=tmp_path/'collision.db';shutil.copy2(next(old[0].glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        name=M.OBJECTS[-1].upper()
        if kind=='table':sql=f'CREATE TABLE "{name}" (n TEXT)'
        elif kind=='view':sql=f'CREATE VIEW "{name}" AS SELECT 1'
        elif kind=='index':sql=f'CREATE INDEX "{name}" ON accounts(name)'
        elif kind=='trigger':sql=f'CREATE TRIGGER "{name}" AFTER INSERT ON accounts BEGIN SELECT 1; END'
        elif kind=='attached':
            db.raw.execute('ATTACH DATABASE ? AS local_N',(str(tmp_path/'attached.db'),))
            sql=f'CREATE TABLE local_N."{name}" (n TEXT)'
        else:sql=f'CREATE TEMP TABLE "{name}" (n TEXT)'
        db.raw.execute(sql)
        before=list(db.raw.iterdump())
        with pytest.raises(BookflowError) as caught:migrate_to_head(db,'company',tmp_path/'backups')
        assert caught.value.code=='E_MIGRATION_FAILED'
        assert list(db.raw.iterdump())==before
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0021',)
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name='reconciliation_keys'").fetchall()==[]


def test_late_migration_failure_rolls_back_complete_new_ddl(old,tmp_path,monkeypatch):
    from alembic import command
    path=tmp_path/'late.db';shutil.copy2(next(old[0].glob('organizations/*/Demo Plumbing Co/company.db')),path)
    original=command.upgrade
    def fail_after_ddl(*a,**kw):
        original(*a,**kw)
        raise RuntimeError('isolated failure after all N DDL')
    with open_database(path,writable=True) as db:
        before=list(db.raw.iterdump())
        with monkeypatch.context() as patch:
            patch.setattr(command,'upgrade',fail_after_ddl)
            with pytest.raises(BookflowError) as caught:migrate_to_head(db,'company',tmp_path/'backups')
        assert caught.value.code=='E_MIGRATION_FAILED'
        assert list(db.raw.iterdump())==before
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name='reconciliation_keys'").fetchall()==[]
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0021','co0022')
        empty(db.raw)


def test_public_upgrade_publication_exact_old_prefix_and_noop(old,tmp_path):
    import bookflow
    from bookflow.storage.paths import read_company_marker
    root=tmp_path/'root';shutil.copytree(old[0],root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        names=[v[0] for v in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version'")]
        before={n:table(raw,n) for n in names}
    c=bookflow.connect(data_root=str(root))
    with pytest.raises(BookflowError) as caught:c.run('company show',{},company='Demo Plumbing Co')
    assert caught.value.code=='E_SCHEMA_BEHIND'
    output=c.run('upgrade',{})
    assert output['companies_migrated'] and not output['companies_failed']
    marker=read_company_marker(path.parent)
    assert marker['schema_revision']=='co0022'
    with sqlite3.connect(root/'hub.db') as raw:
        assert raw.execute('SELECT schema_revision FROM companies WHERE id=?',(marker['company_id'],)).fetchone()==('co0022',)
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0022',)
        assert {n:table(raw,n,through_rowid=v['max_rowid']) for n,v in before.items()}==before
        assert raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]==before['audit_events']['count']+1
        empty(raw)
        first=list(raw.iterdump())
    result=c.run('upgrade',{})
    assert not result['companies_migrated'] and not result['companies_failed']
    with sqlite3.connect(path) as raw:assert list(raw.iterdump())==first
    (tmp_path/'public-upgrade.json').write_text(json.dumps(dict(first=output,second=result,marker=marker,old_rows=before),indent=2))
