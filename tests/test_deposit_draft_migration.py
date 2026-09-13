"""Additive co24 preservation, actual predecessor and literal metadata convergence."""
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
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema as c
from bookflow.company.deposit_draft_schema import guards
from bookflow.company.deposit_dependencies import RECONCILIATION
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS,migrate_to_head,feature_admission
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import preserved,table,attachments,upgrade_to

BASE='5fa513dd4bdb31517673e1510e7ddf2fc636c827'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0024_deposit_drafts')


def test_literal_ddl_metadata_fk_targets_fresh_and_noop(tmp_path):
    indexes=sorted([i for name in M.NEW_TABLES for i in c.metadata.tables[name].indexes],key=lambda i:i.name)
    ddl=tuple(str(CreateTable(c.metadata.tables[n]).compile(dialect=dialect())) for n in M.NEW_TABLES)+tuple(str(CreateIndex(i).compile(dialect=dialect())) for i in indexes)
    assert M.DDL==ddl and M.GUARDS==guards()
    for name in M.NEW_TABLES:
        for fk in c.metadata.tables[name].foreign_key_constraints:
            target=fk.referred_table;columns=tuple(e.column.name for e in fk.elements)
            assert columns in [tuple(target.primary_key.columns.keys())]+[tuple(x.columns.keys()) for x in target.constraints if x.__class__.__name__=='UniqueConstraint']
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None)==(None,HEADS['company'])
        assert all(db.raw.execute('SELECT count(*) FROM '+n).fetchone()==(0,) for n in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall()==[('ok',)]
        assert feature_admission(db,RECONCILIATION,resolver=None) is None
        before=list(db.raw.iterdump())
        assert migrate_to_head(db,'company',None)==(HEADS['company'],HEADS['company'])
        assert list(db.raw.iterdump())==before

@pytest.fixture(scope='module')
def predecessor(tmp_path_factory):
    parent=tmp_path_factory.mktemp('g3-predecessor');source=parent/'source';source.mkdir();root=parent/'root'
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    code='''import bookflow,sys,io,pytest,json
from tests.test_deposit_lifecycle import driver
from tests.test_row8_journal import database_path
c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset();co='Demo Plumbing Co'
p=c.customer.create(name='G3 predecessor',company=co);b=c.account.create(name='G3 predecessor bank',type='bank',company=co);i=c.account.create(name='G3 predecessor income',type='other_income',company=co)
patch=pytest.MonkeyPatch();d=driver.__wrapped__(c,patch)
r=d.run('post',dict(operation_key='g3-preserved',document=dict(mode='inline',deposit_to=b['id'],date='2026-06-03',additional=[dict(received_from=dict(kind='customer',id=p['id']),from_account=i['id'],amount='12.34',memo='A\\0B')])))
c.attachment.add(record_type='customer',record_id=p['id'],original_filename='G3.bin',input_stream=io.BytesIO(b'A\\0\\xff'),company=co)
print(json.dumps(dict(path=str(database_path(c)),operation=r.operation_id)))
'''
    result=subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src')+':'+str(Path(__file__).parents[1]),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (parent/'seed.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr
    return parent,root,source


# The local objects a company file may carry that no migration may disturb: a table, a partial
# index on an expression, a view, a trigger, and a column added to a product table.
LOCAL_OBJECTS=('local_g3','local_g3_ix','local_g3_v','local_g3_t')


def prepared(predecessor,tmp_path,name='root'):
    """A populated co0023 root carrying local objects, and a reading of it before any upgrade.

    Every claim below starts from its own copy of this. They were one test because they shared
    this setup, and sharing setup is what a fixture is for -- bundling them meant the first
    assertion to go stale stopped the other four from running at all.
    """
    root=tmp_path/name;shutil.copytree(predecessor[1],root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        raw.execute("ALTER TABLE deposit_operations ADD COLUMN local_blob BLOB DEFAULT X'00FF'")
        raw.execute('CREATE TABLE local_g3(id INTEGER PRIMARY KEY,t TEXT,b BLOB,f REAL)')
        raw.execute('INSERT INTO local_g3 VALUES (73,?,?,?)',('x\0y',b'\xff\0',0.1))
        raw.execute('CREATE INDEX local_g3_ix ON local_g3(length(t)) WHERE t IS NOT NULL')
        raw.execute('CREATE VIEW local_g3_v AS SELECT * FROM local_g3')
        raw.execute('CREATE TRIGGER local_g3_t AFTER INSERT ON local_g3 BEGIN SELECT 1; END')
        raw.commit()
        names=[r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name<>'alembic_version'")]
        before={n:table(raw,n) for n in names}
        ddl=raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema').fetchall()
    return dict(root=root,path=path,names=names,before=before,ddl=ddl,files=attachments(root))


def test_the_one_transition_keeps_every_value_and_every_local_object(predecessor,tmp_path):
    """co0023 to co0024 and no further, which is what this migration can be held to.

    Through the whole chain a later revision's legitimate rebuild reads as this migration
    disturbing something -- and because that assertion came first, the foreign-key and integrity
    checks behind it stopped running. co0024 declares no CHANGED, REPLACED, TRIGGERS or REBUILT,
    so the claim here is the strongest one available: nothing that existed before it may differ
    afterwards, and the only new objects are the ones it names.
    """
    world=prepared(predecessor,tmp_path)
    with open_database(world['path'],writable=True) as db:
        assert upgrade_to(db,M.revision)==M.revision
        assert {n:preserved(db.raw,n,world['before'][n]) for n in world['names']}==world['before']
        current=set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        assert set(world['ddl'])<=current, 'an additive migration changed something that was already there'
        added=current-set(world['ddl'])
        # Everything new belongs to a table this migration creates -- including the implicit
        # indexes SQLite makes for their unique constraints, which are the migration's own and
        # are named by nobody.
        assert {row[2] for row in added}<=set(M.NEW_TABLES), sorted({row[2] for row in added})
        assert set(M.OBJECTS)<={row[1] for row in current}
        assert all(db.raw.execute('SELECT count(*) FROM '+n).fetchone()==(0,) for n in M.NEW_TABLES)
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall()==[('ok',)]


def test_the_whole_chain_backs_up_what_it_started_from_and_keeps_local_objects(predecessor,tmp_path):
    """The upgrade witness: run to the head, and hold the backup and the local objects.

    This is the claim the scoped transition above cannot make, and the reason it is a separate
    test rather than more assertions in one: the backup is evidence of the point the upgrade
    started from, so it only means co0023 while the upgrade starts at co0023. What survives to
    the head is asserted about the local objects by name, not about every product table, because
    later migrations rebuild product tables legitimately and this claim is not about them.
    """
    world=prepared(predecessor,tmp_path)
    with open_database(world['path'],writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0023',HEADS['company'])
        assert {n:preserved(db.raw,n,world['before'][n]) for n in world['names']}==world['before']
        survivors={row[1]:row[3] for row in db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema')}
        for name in LOCAL_OBJECTS:
            assert name in survivors, name
            assert survivors[name]==next(r[3] for r in world['ddl'] if r[1]==name), name
        assert 'local_blob' in [r[1] for r in db.raw.execute('PRAGMA table_xinfo(deposit_operations)')]
        assert db.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA main.integrity_check').fetchall()==[('ok',)]
    assert attachments(world['root'])==world['files']
    backup=next((tmp_path/'backups').rglob('*.db'))
    with sqlite3.connect(backup) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0023',)
        assert {n:table(raw,n) for n in world['names']}==world['before']
        assert raw.execute('PRAGMA main.integrity_check').fetchall()==[('ok',)]
        # The backup is of the file as it stood, local objects included -- which is what makes
        # it a restore point rather than a copy of the shipped schema.
        backed={r[0] for r in raw.execute('SELECT name FROM sqlite_schema')}
        assert set(LOCAL_OBJECTS)<=backed
    copied=tmp_path/'copied.db';shutil.copy2(world['path'],copied)
    with open_database(copied,writable=False) as db:
        assert {n:preserved(db.raw,n,world['before'][n]) for n in world['names']}==world['before']
    (tmp_path/'preservation.json').write_text(json.dumps(
        dict(base=BASE,tables=world['before'],attachments=world['files']),sort_keys=True))


def test_the_old_binary_refuses_a_file_this_migration_upgraded(predecessor,tmp_path):
    """Its own test because it is its own claim, and because it needs the upgrade to have run."""
    world=prepared(predecessor,tmp_path)
    with open_database(world['path'],writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0023',HEADS['company'])
    code='''import sys,bookflow
c=bookflow.connect(data_root=sys.argv[1])
try:c.run('company show',{},company='Demo Plumbing Co')
except bookflow.BookflowError as e:print(e.code);sys.exit(0 if e.code=='E_SCHEMA_UNKNOWN' else 2)
sys.exit(3)
'''
    result=subprocess.run([sys.executable,'-c',code,str(world['root'])],cwd=predecessor[2],
        env=dict(os.environ,PYTHONPATH=str(predecessor[2]/'src'),BOOKFLOW_DATA_ROOT=str(world['root'])),
        capture_output=True,text=True)
    (tmp_path/'old-refusal.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('kind',['table','index','view','trigger','temp','attached'])
def test_all_namespace_collisions_preflight_without_partial_ddl(predecessor,tmp_path,kind):
    path=tmp_path/'collision.db';shutil.copy2(next(predecessor[1].glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        name=M.OBJECTS[-1].upper()
        if kind=='table':sql=f'CREATE TABLE "{name}"(n TEXT)'
        elif kind=='index':sql=f'CREATE INDEX "{name}" ON accounts(name)'
        elif kind=='view':sql=f'CREATE VIEW "{name}" AS SELECT 1'
        elif kind=='trigger':sql=f'CREATE TRIGGER "{name}" AFTER INSERT ON accounts BEGIN SELECT 1; END'
        elif kind=='temp':sql=f'CREATE TEMP TABLE "{name}"(n TEXT)'
        else:
            db.raw.execute('ATTACH DATABASE ? AS local_g3',(str(tmp_path/'attached.db'),));sql=f'CREATE TABLE local_g3."{name}"(n TEXT)'
        db.raw.execute(sql);before=list(db.raw.iterdump())
        with pytest.raises(BookflowError) as e:migrate_to_head(db,'company',tmp_path/'backups')
        assert e.value.code=='E_MIGRATION_FAILED'
        assert list(db.raw.iterdump())==before
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0023',)


def test_failure_after_ddl_restores_old_database(predecessor,tmp_path,monkeypatch):
    from alembic import command
    path=tmp_path/'late.db';shutil.copy2(next(predecessor[1].glob('organizations/*/Demo Plumbing Co/company.db')),path)
    original=command.upgrade
    def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('G3 witness after DDL')
    with open_database(path,writable=True) as db:
        before=list(db.raw.iterdump())
        with monkeypatch.context() as patch:
            patch.setattr(command,'upgrade',fail)
            with pytest.raises(BookflowError):migrate_to_head(db,'company',tmp_path/'backups')
        assert list(db.raw.iterdump())==before
