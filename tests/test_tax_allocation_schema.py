"""Old raw proof oracles applied to co16's exact widened discriminator."""
import json
import shutil
import sqlite3
import pytest
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.test_tax_work_migration import co15_root
from tests.test_progress_billing_schema import rows, proof, insert, raw_open
from tests import test_progress_billing_schema as old


@pytest.fixture
def migrated(co15_root,tmp_path):
    path=tmp_path/'company.db';shutil.copyfile(next(co15_root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:migrate_to_head(db,'company',None)
    return path


@pytest.mark.parametrize('version',[2,3])
def test_shape_and_overlap_oracles(migrated,rows,version):
    variants=next(mark.args[1] for mark in old.test_v2_rejects_invalid_shape_as_integrity_error.pytestmark if mark.name=='parametrize')
    with raw_open(migrated) as db:
        for changes in variants:
            if changes=={'allocation_version':3}:changes={'allocation_version':4}
            row=rows[0](**proof(allocation_version=version,**{k:v for k,v in changes.items() if k!='allocation_version'}))
            if 'allocation_version' in changes:row['allocation_version']=changes['allocation_version']
            before=db.execute('SELECT * FROM work_billing_allocations ORDER BY rowid').fetchall()
            with pytest.raises(sqlite3.IntegrityError):insert(db,'work_billing_allocations',row)
            assert db.execute('SELECT * FROM work_billing_allocations ORDER BY rowid').fetchall()==before
        one=rows[0](**proof(allocation_version=version));insert(db,'work_billing_allocations',one)
        insert(db,'work_billing_allocations',rows[0](1,**proof(((40,100),),allocation_version=version)))
        for change in [proof(((39,41),),allocation_version=version),proof(((0,100),),allocation_version=version),{}]:
            with pytest.raises(sqlite3.IntegrityError,match='already consumed'):
                insert(db,'work_billing_allocations',rows[0](2,**change))
        for statement in ('UPDATE work_billing_allocations SET spans_json=spans_json','DELETE FROM work_billing_allocations'):
            with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_legacy_shape_root_and_activation_oracles(migrated,rows):
    with raw_open(migrated) as db:
        for change in ({'quantity_microunits':None},{'source_basis_hash':'a'*64},{'spans_json':'[]'}):
            with pytest.raises(sqlite3.IntegrityError):insert(db,'work_billing_allocations',rows[0](**change))
        for change in ({'root_line_id':'foreign'},{'document_line_id':rows[1][1]['document_line_id']},{'source_revision_id':'foreign'}):
            with pytest.raises(sqlite3.IntegrityError):insert(db,'work_billing_allocations',rows[0](**proof(allocation_version=3),**change))
        row=rows[0]();insert(db,'work_billing_allocations',row)
        with pytest.raises(sqlite3.IntegrityError,match='already consumed'):
            insert(db,'work_billing_allocations',rows[0](1,**proof(allocation_version=3)))
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):insert(db,'work_billing_allocations',row,replace=True)


@pytest.mark.parametrize('version',[2,3])
@pytest.mark.parametrize('conflict',['overlap','basis','denominator','legacy'])
def test_activation_preserves_old_constraints(migrated,rows,monkeypatch,version,conflict):
    # Run the frozen oracle unchanged with each supported interval discriminator.
    original=old.proof
    monkeypatch.setattr(old,'proof',lambda *a,**kw:original(*a,allocation_version=version,**kw))
    old.test_revision_activation_and_own_old_revision_exclusion(migrated,rows,conflict)
