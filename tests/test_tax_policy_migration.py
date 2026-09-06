"""Frozen reviewed co14 histories preserve their raw bytes through additive co15."""
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
import bookflow
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.test_payment_migration import raw_snapshot

BASE = '15f3e034a773f0b227150403e491f2c5a5edcf5e'
MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0015_sales_tax_policy')


@pytest.fixture(scope='module')
def co14_root(tmp_path_factory):
    parent = tmp_path_factory.mktemp('tax-co14')
    source = parent/'source'; source.mkdir()
    archive = subprocess.check_output(['git','archive',BASE,'src'], cwd=Path(__file__).resolve().parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    root = parent/'data'
    script = '''import bookflow,sys,json
c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset()
company='Demo Plumbing Co'
customer=c.customer.create(name='Legacy tax payer', company=company)['id']
item=c.run('item show',dict(item='Mainline Clearing'),company=company)['id']
invoice=c.run('invoice post',dict(customer=customer,date='2026-06-01',lines=[dict(item=item,net_amount='1.00',tax_code='Non'),dict(item=item,net_amount='1.00',tax_code='Non')]), company=company,idempotency_key='legacy-tax-post')
payment=c.run('payment receive',dict(customer=customer,date='2026-06-02',amount='0.50',payment_method='Check',operation_key='legacy-tax-cash',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='0.50')])),company=company)
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps(dict(invoice=invoice,payment=payment)))
'''
    process = subprocess.run([sys.executable,'-c',script,str(root),str(parent/'results.json')], cwd=source,
        env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1'), capture_output=True,text=True)
    assert process.returncode == 0, process.stdout+process.stderr
    return root, json.loads((parent/'results.json').read_text())


def test_preserve_all_columns_rowids_local_ddl_and_legacy_policy(co14_root, tmp_path):
    root, results = co14_root
    path = tmp_path/'company.db'
    shutil.copyfile(next(root.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        raw=db.raw
        raw.execute("ALTER TABLE company_info ADD COLUMN local_blob BLOB DEFAULT X'00FF80'")
        raw.execute('ALTER TABLE company_info ADD COLUMN local_length INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL')
        raw.execute('CREATE INDEX local_tax_index ON company_info(local_blob)')
        raw.execute('CREATE VIEW local_tax_view AS SELECT local_blob,local_length FROM company_info')
        raw.execute("CREATE TRIGGER local_tax_guard BEFORE UPDATE OF local_blob ON company_info BEGIN SELECT RAISE(ABORT,'local guard'); END")
        names=[r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'alembic_version'")]
        before={name:raw_snapshot(raw,name) for name in names}
        objects=raw.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT IN ('company_info','alembic_version') ORDER BY type,name").fetchall()
        assert migrate_to_head(db,'company',None)==('co0014','co0015')
        for name,(columns,selected,rows) in before.items():
            assert [r[1] for r in raw.execute(f'PRAGMA table_xinfo("{name}")')][:len(columns)]==columns
            assert raw.execute(f'SELECT {selected} FROM "{name}" ORDER BY rowid').fetchall()==rows,name
        after=raw.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT IN ('company_info','alembic_version') ORDER BY type,name").fetchall()
        assert set(objects)<=set(after)
        assert raw.execute('SELECT sales_tax_calculation FROM company_info').fetchall()==[('line_component_half_even',)]
        assert all(raw.execute(f'SELECT count(*) FROM {name}').fetchone()==(0,) for name in MIGRATION.NAMES)
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert raw.execute('PRAGMA integrity_check').fetchone()==('ok',)


def test_legacy_paid_noop_keys_and_historical_rendering(co14_root,tmp_path):
    source,results=co14_root
    root=tmp_path/'root';shutil.copytree(source,root)
    client=bookflow.connect(data_root=str(root));company='Demo Plumbing Co'
    client.run('upgrade',{})
    invoice=results['invoice']; identity=invoice['id']
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as db:
        historical=db.execute('SELECT profile_snapshot FROM sales_profiles WHERE transaction_id=? ORDER BY rowid',(identity,)).fetchall()
        keys=db.execute('SELECT * FROM settlement_line_keys WHERE transaction_id=? ORDER BY rowid',(identity,)).fetchall()
    output=client.run('invoice show',dict(invoice=identity,revision_number=1),company=company)
    assert output['revision']['profile']==invoice['revision']['profile']
    assert output['revision']['tax_calculation_details']['origin']['kind']=='legacy_implicit'
    client.run('company update',dict(sales_tax_calculation='invoice_combined_half_up'),company=company)
    replay=client.run('invoice post',dict(customer=invoice['customer_id'],date='2026-06-01',
        lines=[dict(item=line['item_id'],net_amount='1.00',tax_code='Non') for line in invoice['revision']['lines']]),
        company=company,idempotency_key='legacy-tax-post')
    assert replay['idempotent_replay'] and replay['revision']==invoice['revision']

    noop=client.run('invoice update',dict(invoice=identity,expected_version=2,operation_key='legacy-tax-noop',settlement_versions=[dict(payment=results['payment']['id'],expected_version=1)]),reason='Preserve legacy policy',company=company)
    assert not noop['changed']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM sales_tax_line_keys WHERE transaction_id=?',(identity,)).fetchone()==(0,)
    changed=client.run('invoice update',dict(invoice=identity,expected_version=2,memo='Retain legacy tax knowledge',operation_key='legacy-tax-memo',settlement_versions=[dict(payment=results['payment']['id'],expected_version=1)]),reason='Preserve legacy policy',company=company)
    assert changed['revision']['tax_calculation_details']['origin']['kind']=='legacy_implicit'
    assert changed['revision']['tax_calculation_details']['policy']=='line_component_half_even'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT profile_snapshot FROM sales_profiles WHERE transaction_id=? ORDER BY rowid',(identity,)).fetchall()[:len(historical)]==historical
        assert db.execute('SELECT * FROM settlement_line_keys WHERE transaction_id=? ORDER BY rowid',(identity,)).fetchall()==keys
        ids=[r[0] for r in db.execute('SELECT id FROM document_line_identities WHERE transaction_id=? ORDER BY id COLLATE BINARY',(identity,))]
        assert db.execute('SELECT line_id,tax_ordinal FROM sales_tax_line_keys WHERE transaction_id=? ORDER BY tax_ordinal',(identity,)).fetchall()==list(zip(ids,range(1,len(ids)+1)))


def test_frozen_tax_ddl_matches_metadata():
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema
    assert tuple(str(CreateTable(schema.metadata.tables[name]).compile(dialect=dialect())).strip() for name in MIGRATION.NAMES)==MIGRATION.DDL


@pytest.mark.parametrize('conflict',['column','table'])
def test_unsupported_local_shape_rolls_back(co14_root,tmp_path,conflict):
    source,_=co14_root;path=tmp_path/'company.db'
    shutil.copyfile(next(source.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        db.raw.execute('ALTER TABLE company_info ADD COLUMN sales_tax_calculation TEXT' if conflict=='column' else 'CREATE TABLE sales_tax_line_keys(local TEXT)')
        before=db.raw.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        with pytest.raises(Exception):migrate_to_head(db,'company',None)
        assert db.raw.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()==before
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0014',)
