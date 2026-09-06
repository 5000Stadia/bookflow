"""co16 exact allocation rebuild, frozen co15/co14 history, and atomic rollback."""
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
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from bookflow import BookflowError
from tests.test_tax_policy_migration import co14_root
from tests.test_payment_migration import raw_snapshot

M = importlib.import_module('bookflow.storage.company_migrations.versions.0016_tax_work_allocations')

@pytest.fixture(scope='module')
def co15_root(tmp_path_factory):
    directory=tmp_path_factory.mktemp('frozen-co15'); source=directory/'source';source.mkdir()
    archive=subprocess.check_output(['git','archive','c00b5b284698e1cd811fcdb5e10d482b574ca8a1','src'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    root=directory/'data'
    result=subprocess.run([sys.executable,'-c','import bookflow,sys;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset()',str(root)],cwd=source,
        env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    return root


def snapshots(raw):
    return {name:raw_snapshot(raw,name) for (name,) in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version'")}


def local(raw):
    raw.execute("ALTER TABLE work_billing_allocations ADD COLUMN local_blob BLOB DEFAULT X'00FF80'")
    raw.execute('ALTER TABLE work_billing_allocations ADD COLUMN local_size INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL')
    raw.execute('CREATE INDEX local_allocation_index ON work_billing_allocations(local_size,local_blob)')
    raw.execute('CREATE VIEW local_allocation_view AS SELECT * FROM work_billing_allocations')
    raw.execute("CREATE TRIGGER local_allocation_guard BEFORE UPDATE OF local_blob ON work_billing_allocations BEGIN SELECT RAISE(ABORT,'local guard'); END")


@pytest.mark.parametrize('base',['co14_root','co15_root'])
def test_preserving_allocation_rebuild(request,base,tmp_path):
    root=request.getfixturevalue(base);root=root[0] if isinstance(root,tuple) else root
    path=tmp_path/'company.db';shutil.copyfile(next(root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        raw=db.raw;local(raw);before=snapshots(raw)
        objects=raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT IN ('work_billing_allocations','work_billing_allocation_shape','company_info','alembic_version') ORDER BY type,name").fetchall()
        versions=dict(raw.execute('SELECT allocation_version,count(*) FROM work_billing_allocations GROUP BY allocation_version'))
        assert versions.keys()=={1,2}
        assert migrate_to_head(db,'company',None)[1]=='co0016'
        for name,(columns,selected,values) in before.items():
            assert raw.execute(f'SELECT {selected} FROM "{name}" ORDER BY rowid').fetchall()==values,name
        after=raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT IN ('work_billing_allocations','work_billing_allocation_shape','company_info','alembic_version') ORDER BY type,name").fetchall()
        assert set(objects)<=set(after)
        assert raw.execute("SELECT sql FROM sqlite_schema WHERE name='work_billing_allocation_shape'").fetchone()==(M.NEW_SHAPE,)
        assert raw.execute('PRAGMA foreign_keys').fetchone()==(1,)
        assert raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]


@pytest.mark.parametrize('failure',['reserved','guard','post_rebuild'])
def test_rebuild_failure_is_atomic(co15_root,tmp_path,monkeypatch,failure):
    path=tmp_path/'company.db';shutil.copyfile(next(co15_root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        raw=db.raw;local(raw)
        if failure=='reserved':raw.execute('CREATE TABLE _co0016_work_billing_allocations (x INTEGER)')
        if failure=='guard':
            raw.execute('DROP TRIGGER work_billing_allocation_shape')
            raw.execute("CREATE TRIGGER work_billing_allocation_shape BEFORE INSERT ON work_billing_allocations BEGIN SELECT RAISE(ABORT,'unsupported'); END")
        if failure=='post_rebuild':
            execute=db.conn.exec_driver_sql
            def reject(statement,*args,**kwargs):
                if statement=='PRAGMA integrity_check':raise RuntimeError('injected integrity rejection before commit')
                return execute(statement,*args,**kwargs)
            monkeypatch.setattr(db.conn,'exec_driver_sql',reject)
        before=snapshots(raw);ddl=raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        with pytest.raises(BookflowError) as exc:migrate_to_head(db,'company',None)
        assert exc.value.code=='E_MIGRATION_FAILED'
        assert snapshots(raw)==before
        assert raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()==ddl
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0015',)
        assert raw.execute('PRAGMA foreign_keys').fetchone()==(1,)


@pytest.mark.parametrize('definition', [
    'CONSTRAINT local_allocation_versions CHECK (allocation_version IN (1,2))',
    'local_version_limit INTEGER CHECK (allocation_version IN (1,2))',
])
def test_local_discriminator_check_preflight_before_rebuild(co15_root,tmp_path,definition):
    """Source-derived table/column shapes reject before even returning a DDL plan."""
    path=tmp_path/'company.db';shutil.copyfile(next(co15_root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        sql=db.raw.execute("SELECT sql FROM sqlite_schema WHERE name='work_billing_allocations'").fetchone()[0]
        preserving=importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
        _,parts,suffix=preserving._definitions(sql,M.TABLE)
        local_sql='CREATE TABLE work_billing_allocations ('+','.join(parts+[definition])+suffix
        class SourceShape:
            def exec_driver_sql(self,statement,*args):
                if statement.startswith('SELECT sql FROM sqlite_schema'):
                    class SourceSQL:
                        def scalar_one(self):return local_sql
                    return SourceSQL()
                return db.conn.exec_driver_sql(statement,*args)
        before=snapshots(db.raw)
        with pytest.raises(RuntimeError,match='competing allocation discriminator CHECK'):
            M._plan(SourceShape())
        assert snapshots(db.raw)==before


def test_local_column_discriminator_check_upgrade_rolls_back(co15_root,tmp_path):
    """Ordinary local CHECK addition in the existing preserving-DDL fixture."""
    path=tmp_path/'company.db';shutil.copyfile(next(co15_root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        raw=db.raw;local(raw)
        raw.execute('ALTER TABLE work_billing_allocations ADD COLUMN local_version_limit INTEGER CHECK (allocation_version IN (1,2))')
        before=snapshots(raw);ddl=raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        with pytest.raises(BookflowError) as exc:migrate_to_head(db,'company',None)
        assert exc.value.code=='E_MIGRATION_FAILED'
        assert snapshots(raw)==before
        assert raw.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY type,name').fetchall()==ddl
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0015',)
        assert raw.execute('PRAGMA foreign_keys').fetchone()==(1,)
        assert raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
