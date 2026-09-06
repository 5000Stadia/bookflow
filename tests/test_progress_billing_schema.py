"""Independent raw-SQL proof boundaries and exact-base co0011 preservation."""
import importlib
import inspect
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
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head

MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0012_progress_billing')
BASE = '45cfd75e95eccdc41881bea82fbafc17c8e604b9'
TABLES = ('sales_line_profiles', 'work_billing_allocations')


@pytest.fixture(autouse=True)
def historical_co12_target(monkeypatch):
    """This module witnesses co0011→co0012, including unchanged unrelated rows."""
    monkeypatch.setitem(HEADS, 'company', 'co0012')


def insert(raw, table, row, replace=False):
    raw.execute(f'INSERT {"OR REPLACE " if replace else ""}INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))


def hex40(value):
    return format(value, '040x')


def proof(spans=((0, 40),), denominator=100, **changes):
    return dict(dict(allocation_version=2, source_basis_hash='a'*64,
        denominator_hex=hex40(denominator), spans_json=json.dumps([[hex40(a), hex40(b)] for a, b in spans]),
        quantity_microunits=None), **changes)


@pytest.fixture(scope='module')
def co11_template(tmp_path_factory):
    """Execute the published base, before any current HEADS/metadata can affect it.

    This fixture deliberately contains co0011 amount sales and linked histories.
    No restoration into an older table shape or truncation of demo history occurs.
    """
    directory = tmp_path_factory.mktemp('co11-base')
    source = directory / 'source'
    source.mkdir()
    repository = Path(__file__).resolve().parents[1]
    archive = subprocess.check_output(['git', 'archive', BASE, 'src'], cwd=repository)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    root = directory / 'data'
    env = dict(os.environ, PYTHONPATH=str(source / 'src'), BOOKFLOW_DATA_ROOT=str(root))
    script = """import bookflow
from bookflow.storage.migrate import HEADS
assert HEADS['company'] == 'co0011'
c = bookflow.connect(data_root=__import__('os').environ['BOOKFLOW_DATA_ROOT'])
c.init()
c.demo.reset()
company = 'Demo Plumbing Co'
income = c.account.create(name='Schema witness income',type='income',company=company)['id']
customer = c.customer.create(name='Schema witness customer',company=company)['id']
code = next(r['id'] for r in c.run('sales-tax-code list',{},company=company)['items'] if not r['taxable'])
item = c.run('item create',dict(name='Schema witness service',type='service',description='Witness service',sales_enabled=True,income_account_id=income,price='1.00',sales_tax_code_id=code),company=company)['id']
for n in range(3):
 c.run('invoice post',dict(date='2026-01-12',customer=customer,lines=[dict(item=item,quantity='1')]),company=company)
"""
    result = subprocess.run([sys.executable, '-c', script], cwd=source, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    path = next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0011',)
        assert raw.execute("SELECT count(*) FROM sales_line_profiles WHERE pricing_basis='amount'").fetchone()[0] > 0
        assert raw.execute('SELECT count(*) FROM work_billing_allocations').fetchone()[0] > 0
    return path


@pytest.fixture
def prior(tmp_path, co11_template):
    path = tmp_path / 'company.db'
    shutil.copyfile(co11_template, path)
    return path


@pytest.fixture
def migrated(prior):
    with open_database(prior, writable=True) as db:
        assert migrate_to_head(db, 'company', None) == ('co0011', 'co0012')
    return prior


@pytest.fixture
def rows(migrated):
    with sqlite3.connect(migrated) as raw:
        raw.row_factory = sqlite3.Row
        source = dict(raw.execute('''SELECT s.*, i.root_document_id, i.root_line_id FROM work_lines s
 JOIN work_line_identities i ON i.document_id=s.document_id AND i.id=s.line_id
 WHERE NOT EXISTS (SELECT 1 FROM work_billing_allocations a
 WHERE a.root_document_id=i.root_document_id AND a.root_line_id=i.root_line_id)
 ORDER BY s.id LIMIT 1''').fetchone())
        sales = [dict(row) for row in raw.execute('''SELECT s.* FROM sales_line_profiles s
 JOIN transactions t ON t.id=s.transaction_id AND t.current_revision_id=s.revision_id
 WHERE t.status='posted' AND t.type='invoice'
 GROUP BY t.id ORDER BY t.id LIMIT 3''')]
    assert len(sales) == 3
    counter = 0
    def allocation(index=0, **changes):
        nonlocal counter
        counter += 1
        sale = sales[index]
        row = dict(id=f'{counter:026d}', transaction_id=sale['transaction_id'], revision_id=sale['revision_id'],
            document_line_id=sale['document_line_id'], source_document_id=source['document_id'],
            source_revision_id=source['revision_id'], source_line_id=source['id'],
            root_document_id=source['root_document_id'], root_line_id=source['root_line_id'],
            quantity_microunits=source['quantity_microunits'], net_minor_units=source['net_minor_units'],
            tax_minor_units=source['tax_minor_units'], gross_minor_units=source['gross_minor_units'],
            facts_snapshot=source['facts_snapshot'], created_at=source['created_at'],
            created_by=source['created_by'], created_via=source['created_via'])
        return dict(row, **changes)
    return allocation, sales


def raw_open(path):
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute('PRAGMA foreign_keys=ON')
    return raw


@pytest.mark.parametrize('changes', [{}, {'quantity_microunits':1}, {'quantity_microunits':2**63-1}])
def test_v2_valid_nullable_or_exact_quantity(migrated, rows, changes):
    with raw_open(migrated) as raw:
        row = rows[0](**proof(**changes))
        insert(raw, 'work_billing_allocations', row)
        assert raw.execute('SELECT quantity_microunits FROM work_billing_allocations WHERE id=?', (row['id'],)).fetchone() == (row['quantity_microunits'],)
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('changes', [
    {'allocation_version':0}, {'allocation_version':3}, {'allocation_version':1.5}, {'allocation_version':None},
    {'source_basis_hash':None}, {'source_basis_hash':'A'*64}, {'source_basis_hash':'g'*64},
    {'source_basis_hash':'a'*63}, {'source_basis_hash':'a'*64+'\0x'}, {'source_basis_hash':b'a'*64},
    {'denominator_hex':None}, {'denominator_hex':hex40(0)}, {'denominator_hex':'F'*40},
    {'denominator_hex':'g'*40}, {'denominator_hex':'1'*39}, {'denominator_hex':'1'*41},
    {'denominator_hex':hex40(100)+'\0x'}, {'denominator_hex':b'1'*40},
    {'quantity_microunits':0}, {'quantity_microunits':-1}, {'quantity_microunits':1.5},
    {'quantity_microunits':'9223372036854775808'},
    {'spans_json':None}, {'spans_json':b'[]'}, {'spans_json':'not json'}, {'spans_json':'['},
    *({'spans_json':json.dumps(value)} for value in [None, True, 1, 'text', {}, [], [None], [True],
       [1], ['text'], [{}], [[]], [[hex40(0)]], [[hex40(0),hex40(1),hex40(2)]],
       [[None,hex40(1)]], [[0,hex40(1)]], [[hex40(0),False]], [[hex40(0),{}]],
       [['0',hex40(1)]], [[hex40(0),'A'*40]], [[hex40(0),'g'*40]],
       [[hex40(0),hex40(1)+'\0x']], [[hex40(1),hex40(1)]], [[hex40(2),hex40(1)]],
       [[hex40(0),hex40(101)]], [[hex40(10),hex40(20)],[hex40(0),hex40(5)]],
       [[hex40(0),hex40(20)],[hex40(10),hex40(30)]],
       [[hex40(0),hex40(20)],[hex40(20),hex40(30)]],
       [[hex40(0),hex40(1)]]*201]),
])
def test_v2_rejects_invalid_shape_as_integrity_error(migrated, rows, changes):
    with raw_open(migrated) as raw:
        before = raw.execute('SELECT * FROM work_billing_allocations ORDER BY id').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            insert(raw, 'work_billing_allocations', rows[0](**proof(**changes)))
        assert raw.execute('SELECT * FROM work_billing_allocations ORDER BY id').fetchall() == before


@pytest.mark.parametrize('change', [{'quantity_microunits':None}, {'quantity_microunits':0},
    {'quantity_microunits':1.2}, {'source_basis_hash':'a'*64}, {'denominator_hex':hex40(100)}, {'spans_json':'[]'}])
def test_v1_requires_positive_integer_and_null_proof(migrated, rows, change):
    with raw_open(migrated) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            insert(raw, 'work_billing_allocations', rows[0](**change))


def test_legacy_default_full_root_and_v2_full_root(migrated, rows):
    with raw_open(migrated) as raw:
        row = rows[0]()
        insert(raw, 'work_billing_allocations', row)
        assert raw.execute('SELECT allocation_version,source_basis_hash,denominator_hex,spans_json FROM work_billing_allocations WHERE id=?', (row['id'],)).fetchone() == (1,None,None,None)
        with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
            insert(raw, 'work_billing_allocations', rows[0](1, **proof(((40,100),))))


def test_adjacent_across_sales_valid_overlap_and_legacy_rejected(migrated, rows):
    with raw_open(migrated) as raw:
        insert(raw, 'work_billing_allocations', rows[0](**proof()))
        insert(raw, 'work_billing_allocations', rows[0](1, **proof(((40,100),))))
        for changes in [proof(((39,41),)), proof(((0,100),)), {}]:
            with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
                insert(raw, 'work_billing_allocations', rows[0](2, **changes))


@pytest.mark.parametrize('changes', [{'source_basis_hash':'b'*64}, {'denominator_hex':hex40(101)}])
def test_disjoint_cross_basis_rejected(migrated, rows, changes):
    with raw_open(migrated) as raw:
        insert(raw, 'work_billing_allocations', rows[0](**proof()))
        with pytest.raises(sqlite3.IntegrityError, match='incompatible basis'):
            insert(raw, 'work_billing_allocations', rows[0](1, **proof(((40,100),), **changes)))


def test_maximum_span_count_and_unsigned_hex_order(migrated, rows):
    with raw_open(migrated) as raw:
        spans = [(n*2,n*2+1) for n in range(200)]
        spans[-1] = (398, 2**160-1)
        insert(raw, 'work_billing_allocations', rows[0](**proof(spans, denominator=2**160-1)))
        with pytest.raises(sqlite3.IntegrityError):
            insert(raw, 'work_billing_allocations', rows[0](1, **proof(((2**159,2**160-1),), denominator=2**160-1)))


@pytest.mark.parametrize('field', ['net_minor_units','tax_minor_units','gross_minor_units'])
@pytest.mark.parametrize('value', [-1, 1.25, '9223372036854775808', None])
def test_money_integer_bounds_unchanged(migrated, rows, field, value):
    with raw_open(migrated) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            insert(raw, 'work_billing_allocations', rows[0](**proof(), **{field:value}))


def test_money_zero_maximum_and_total(migrated, rows):
    with raw_open(migrated) as raw:
        insert(raw, 'work_billing_allocations', rows[0](**proof(),net_minor_units=0,tax_minor_units=0,gross_minor_units=0))
        insert(raw, 'work_billing_allocations', rows[0](1, **proof(((40,100),)),net_minor_units=2**63-1,tax_minor_units=0,gross_minor_units=2**63-1))
        ddl=raw.execute("SELECT sql FROM sqlite_schema WHERE name='work_billing_allocations'").fetchone()[0]
        with sqlite3.connect(':memory:') as isolated:
            isolated.execute(ddl)
            for net,tax,gross in [(1,1,1),(2**63-1,1,2**63-1)]:
                with pytest.raises(sqlite3.IntegrityError,match='ck_work_billing_total'):
                    insert(isolated,'work_billing_allocations',rows[0](2,**proof(),net_minor_units=net,tax_minor_units=tax,gross_minor_units=gross))


def test_root_destination_and_immutability_guards(migrated, rows):
    allocation, sales = rows
    with raw_open(migrated) as raw:
        for changes in [{'root_line_id':'foreign'}, {'document_line_id':sales[1]['document_line_id']},
                        {'source_revision_id':'foreign'}, {'source_line_id':'foreign'}]:
            with pytest.raises(sqlite3.IntegrityError):
                insert(raw,'work_billing_allocations',allocation(**proof(), **changes))
        row = allocation(**proof())
        insert(raw,'work_billing_allocations',row)
        for statement in ["UPDATE work_billing_allocations SET spans_json=spans_json", 'DELETE FROM work_billing_allocations']:
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                raw.execute(statement)
        for changes in [{}, {'id':'x'*26}, {'revision_id':sales[1]['revision_id'],'document_line_id':sales[1]['document_line_id']}]:
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                insert(raw,'work_billing_allocations',dict(row, **changes), replace=True)
        raw.execute('PRAGMA recursive_triggers=OFF')
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            insert(raw,'work_billing_allocations',row,replace=True)


def snapshot(raw):
    tables = [r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: raw.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid').fetchall() for name in tables}


def local_schema(path):
    """Add local stored/virtual expressions, checks and cross-table dependents."""
    with sqlite3.connect(path, isolation_level=None) as raw:
        raw.execute('PRAGMA foreign_keys=OFF')
        objects = raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE type IN ('trigger','view') OR (type='index' AND tbl_name IN ('sales_line_profiles','work_billing_allocations') AND sql IS NOT NULL)").fetchall()
        for kind, name, _ in objects:
            raw.execute(f'DROP {kind} "{name}"')
        for table in TABLES:
            ddl = raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (table,)).fetchone()[0]
            # Table suffix, local checks and a quoted fake target definition are
            # deliberately outside the known fields that co0012 may widen.
            pos = ddl.index('(') + 1
            extra = """
 local_note TEXT NOT NULL DEFAULT 'quantity_microunits BIGINT NOT NULL' CHECK(length(local_note)>0),
 local_blob BLOB DEFAULT X'0061ff',
 local_stored BIGINT GENERATED ALWAYS AS (net_minor_units * 2) STORED,
 local_virtual TEXT GENERATED ALWAYS AS (local_note || ',)!') VIRTUAL,
"""
            new = ddl[:pos] + extra + ddl[pos:]
            columns = [r[1] for r in raw.execute(f'PRAGMA table_info({table})')]
            names = ','.join(['rowid']+columns)
            data = raw.execute(f'SELECT {names} FROM {table}').fetchall()
            raw.execute(f'DROP TABLE {table}')
            raw.execute(new)
            raw.executemany(f'INSERT INTO {table} ({names}) VALUES ({",".join("?" for _ in range(len(columns)+1))})',data)
            raw.execute(f"UPDATE {table} SET local_note='saved:' || rowid")
            raw.execute(f'CREATE UNIQUE INDEX local_{table}_note ON {table}(local_note) WHERE net_minor_units>=0')
            raw.execute(f'CREATE VIEW local_{table} AS SELECT *,local_stored+1 AS next_value FROM {table}')
            raw.execute(f"CREATE TRIGGER local_{table}_view INSTEAD OF DELETE ON local_{table} BEGIN SELECT RAISE(ABORT,'local preserved'); END")
            raw.execute(f"CREATE TRIGGER local_{table}_insert BEFORE INSERT ON {table} WHEN NEW.local_note='reject' BEGIN SELECT RAISE(ABORT,'local preserved'); END")
        for _, _, sql in objects:
            raw.execute(sql)
        raw.execute('CREATE VIEW local_join AS SELECT s.local_note AS sale_note,a.local_note AS allocation_note FROM sales_line_profiles s JOIN work_billing_allocations a ON a.document_line_id=s.document_line_id')
        raw.execute('CREATE TRIGGER local_external AFTER INSERT ON classes BEGIN SELECT count(*) FROM local_join; END')
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []


def test_populated_co11_preserves_both_tables_every_old_row_and_local_object(prior):
    local_schema(prior)
    with open_database(prior, writable=True) as db:
        raw = db.raw
        before = snapshot(raw)
        columns = {t:[r[1] for r in raw.execute(f'PRAGMA table_xinfo({t})')] for t in TABLES}
        ddl = {t:raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(t,)).fetchone()[0] for t in TABLES}
        objects = raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE type IN ('view','trigger','index') AND sql IS NOT NULL ORDER BY name").fetchall()
        migrate_to_head(db,'company',None)
        after = snapshot(raw)
        for table,data in before.items():
            if table in TABLES:
                old = raw.execute(f'SELECT rowid,{",".join(columns[table])} FROM {table} ORDER BY rowid').fetchall()
                assert [r[1] for r in raw.execute(f'PRAGMA table_xinfo({table})')][:len(columns[table])] == columns[table]
                assert old == data
                stored = raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(table,)).fetchone()[0]
                # Independent preservation oracle for every original definition
                # other than the specifically approved changes.
                for fragment in ['local_note TEXT NOT NULL', "CHECK(length(local_note)>0)",
                    "local_blob BLOB DEFAULT X'0061ff'", 'local_stored BIGINT GENERATED ALWAYS AS (net_minor_units * 2) STORED',
                    "local_virtual TEXT GENERATED ALWAYS AS (local_note || ',)!') VIRTUAL"]:
                    assert fragment in stored and fragment in ddl[table]
            elif table != 'alembic_version':
                assert after[table] == data
        assert raw.execute('SELECT DISTINCT allocation_version,source_basis_hash,denominator_hex,spans_json FROM work_billing_allocations').fetchall() == [(1,None,None,None)]
        after_objects = {row[1]:row for row in raw.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL")}
        replaced = {'work_billing_allocation_active','work_billing_transaction_insert','work_billing_transaction_update'}
        for row in objects:
            if row[1] not in replaced:
                assert after_objects[row[1]] == row
        for table in TABLES:
            with pytest.raises(sqlite3.IntegrityError, match='local preserved'):
                raw.execute(f'DELETE FROM local_{table}')
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert raw.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('failure', ['sales_unknown','allocation_unknown','guard_changed','guard_missing','late','generated_changed'])
def test_unknown_schema_or_late_failure_rolls_back_atomically(prior, monkeypatch, failure):
    local_schema(prior)
    with open_database(prior,writable=True) as db:
        raw = db.raw
        if failure == 'sales_unknown':
            # A reserved target with local semantics cannot be adopted.
            raw.execute('PRAGMA writable_schema=ON')
            raw.execute("UPDATE sqlite_schema SET sql=replace(sql,'quantity_microunits BIGINT NOT NULL','quantity_microunits INTEGER NOT NULL') WHERE name='sales_line_profiles'")
            raw.execute('PRAGMA writable_schema=OFF')
        elif failure == 'allocation_unknown':
            raw.execute('ALTER TABLE work_billing_allocations ADD COLUMN allocation_version INTEGER DEFAULT 9')
        elif failure in ('guard_changed','guard_missing'):
            raw.execute('DROP TRIGGER work_billing_allocation_active')
            if failure == 'guard_changed':
                raw.execute("CREATE TRIGGER work_billing_allocation_active BEFORE INSERT ON work_billing_allocations BEGIN SELECT RAISE(ABORT,'local owner'); END")
        elif failure == 'generated_changed':
            original = sa.engine.Connection.exec_driver_sql
            def corrupt(connection, statement, *args, **kwargs):
                if statement.startswith('CREATE TABLE "_co0012_work_billing_allocations"'):
                    statement = statement.replace('(net_minor_units * 2) STORED','(net_minor_units * 3) STORED')
                return original(connection,statement,*args,**kwargs)
            monkeypatch.setattr(sa.engine.Connection,'exec_driver_sql',corrupt)
        else:
            original = sa.engine.Connection.exec_driver_sql
            def fail(connection, statement, *args, **kwargs):
                if statement == 'PRAGMA foreign_key_check':
                    assert connection.connection.driver_connection.execute("SELECT count(*) FROM sqlite_schema WHERE name='work_billing_allocation_shape'").fetchone()[0] == 1
                    raise RuntimeError('injected final verification failure')
                return original(connection,statement,*args,**kwargs)
            monkeypatch.setattr(sa.engine.Connection,'exec_driver_sql',fail)
        before = snapshot(raw)
        schema = raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name').fetchall()
        with pytest.raises(BookflowError) as caught:
            migrate_to_head(db,'company',None)
        assert caught.value.code == 'E_MIGRATION_FAILED'
        assert snapshot(raw) == before
        assert raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name').fetchall() == schema
        assert raw.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_fresh_schema_matches_declarations_and_frozen_migration(tmp_path):
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    # Preservation appends new columns after existing columns. Compare columns and complete named
    # constraints independently of their order, plus foreign keys and indexes.
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db, sqlite3.connect(':memory:') as declared:
        assert migrate_to_head(db,'company',None) == (None,'co0012')
        for table in (c.sales_line_profiles,c.work_billing_allocations):
            declared.execute(str(CreateTable(table).compile(dialect=dialect())))
            normalize = lambda rows: sorted(tuple(row)[1:] for row in rows)
            assert normalize(db.raw.execute(f'PRAGMA table_info({table.name})')) == normalize(declared.execute(f'PRAGMA table_info({table.name})'))
            actual = db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(table.name,)).fetchone()[0]
            for constraint in table.constraints:
                if isinstance(constraint,sa.CheckConstraint):
                    assert f'CONSTRAINT {constraint.name} CHECK ({constraint.sqltext})' in actual
            fks = lambda raw: sorted(tuple(row)[2:] for row in raw.execute(f'PRAGMA foreign_key_list({table.name})'))
            assert fks(db.raw) == fks(declared)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    source = inspect.getsource(MIGRATION)
    assert 'bookflow.company' not in source and '0011_work_billing' not in source
    assert HEADS['company'] == 'co0012'


@pytest.mark.parametrize(('basis','quantity','base','price','valid'), [
    ('allocated',None,None,None,True), ('allocated',1,None,0,True), ('allocated',None,1,42,True),
    ('allocated',1,1,2**63-1,True), ('allocated',0,None,1,False), ('allocated',None,0,1,False),
    ('allocated',-1,1,1,False), ('allocated',1,1,-1,False), ('allocated',1.5,1,1,False),
    ('allocated',1,1.5,1,False), ('allocated',1,1,1.5,False),
    ('unit',None,1,1,False), ('unit',1,None,1,False), ('unit',1,1,None,False),
    ('unit',1,1,0,True), ('amount',1,1,None,True), ('amount',None,1,None,False),
    ('amount',1,None,None,False), ('amount',1,1,0,False), (None,1,1,None,False),
    ('other',1,1,None,False),
])
def test_sales_allocated_mode_and_ordinary_contract(migrated, basis, quantity, base, price, valid):
    with sqlite3.connect(migrated) as source:
        source.row_factory=sqlite3.Row
        ddl=source.execute("SELECT sql FROM sqlite_schema WHERE name='sales_line_profiles'").fetchone()[0]
        row=dict(source.execute('SELECT * FROM sales_line_profiles LIMIT 1').fetchone())
    with sqlite3.connect(':memory:') as raw:
        raw.execute(ddl)
        row.update(pricing_basis=basis,quantity_microunits=quantity,base_quantity_microunits=base,unit_price_minor_units=price)
        if valid:
            insert(raw,'sales_line_profiles',row)
        else:
            with pytest.raises(sqlite3.IntegrityError):
                insert(raw,'sales_line_profiles',row)


def duplicate_revision(raw, sale):
    """Raw relational scaffolding; no billing resolver or math implementation."""
    def read(table, key, value):
        cursor=raw.execute(f'SELECT * FROM {table} WHERE {key}=?',(value,))
        return dict(zip((c[0] for c in cursor.description),cursor.fetchone()))
    revision=read('transaction_revisions','id',sale['revision_id'])
    revision.update(id='R'*26,revision_number=revision['revision_number']+1,supersedes_revision_id=revision['id'])
    insert(raw,'transaction_revisions',revision)
    profile=read('sales_profiles','revision_id',sale['revision_id'])
    profile['revision_id']=revision['id']
    insert(raw,'sales_profiles',profile)
    line=read('document_lines','id',sale['document_line_id'])
    line.update(id='L'*26,revision_id=revision['id'])
    insert(raw,'document_lines',line)
    sale=dict(sale,document_line_id=line['id'],revision_id=revision['id'])
    insert(raw,'sales_line_profiles',sale)
    return sale


@pytest.mark.parametrize('conflict', ['overlap','basis','denominator','legacy'])
def test_revision_activation_and_own_old_revision_exclusion(migrated, rows, conflict):
    allocation,sales=rows
    with raw_open(migrated) as raw:
        new=duplicate_revision(raw,sales[0])
        pending=allocation(revision_id=new['revision_id'],document_line_id=new['document_line_id'],**proof())
        insert(raw,'work_billing_allocations',pending)  # not yet current
        choices={'overlap':proof(((30,60),)), 'basis':proof(((40,100),),source_basis_hash='b'*64),
                 'denominator':proof(((40,100),),denominator=101),'legacy':{}}
        other=allocation(1,**choices[conflict])
        insert(raw,'work_billing_allocations',other)
        with pytest.raises(sqlite3.IntegrityError,match='already consumed'):
            raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?',(new['revision_id'],sales[0]['transaction_id']))
        # Release the other sale through a valid void-state row. Its existing
        # batch supplies the FK; these raw tests do not assert accounting math.
        batch=raw.execute('SELECT id FROM posting_batches WHERE transaction_id=? LIMIT 1',(sales[1]['transaction_id'],)).fetchone()[0]
        raw.execute("UPDATE transactions SET status='voided',voided_at=updated_at,voided_by=updated_by,void_reason='test release',void_posting_batch_id=? WHERE id=?",(batch,sales[1]['transaction_id']))
        insert(raw,'work_billing_allocations',allocation(**proof()))  # own old allocation
        raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?',(new['revision_id'],sales[0]['transaction_id']))
        with pytest.raises(sqlite3.IntegrityError,match='already consumed'):
            raw.execute("UPDATE transactions SET status='posted',voided_at=NULL,voided_by=NULL,void_reason=NULL,void_posting_batch_id=NULL WHERE id=?",(sales[1]['transaction_id'],))
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []


def test_disjoint_activation_and_changed_basis_after_all_released(migrated, rows):
    allocation,sales=rows
    with raw_open(migrated) as raw:
        new=duplicate_revision(raw,sales[0])
        insert(raw,'work_billing_allocations',allocation(revision_id=new['revision_id'],document_line_id=new['document_line_id'],**proof()))
        insert(raw,'work_billing_allocations',allocation(1,**proof(((40,100),))))
        raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?',(new['revision_id'],sales[0]['transaction_id']))
        for sale in sales[:2]:
            batch=raw.execute('SELECT id FROM posting_batches WHERE transaction_id=? LIMIT 1',(sale['transaction_id'],)).fetchone()[0]
            raw.execute("UPDATE transactions SET status='voided',voided_at=updated_at,voided_by=updated_by,void_reason='release',void_posting_batch_id=? WHERE id=?",(batch,sale['transaction_id']))
        insert(raw,'work_billing_allocations',allocation(2,**proof(((0,101),),denominator=101,source_basis_hash='b'*64)))


def test_retained_conversion_key_lineage_and_replace_guards(migrated):
    with raw_open(migrated) as raw:
        raw.row_factory=sqlite3.Row
        row=dict(raw.execute('SELECT * FROM work_billing_conversions LIMIT 1').fetchone())
        for table in ['work_billing_conversions']:
            saved=dict(raw.execute(f'SELECT * FROM {table} LIMIT 1').fetchone())
            with pytest.raises(sqlite3.IntegrityError):
                insert(raw,table,saved,replace=True)
        link=dict(raw.execute('SELECT * FROM work_links LIMIT 1').fetchone())
        with pytest.raises(sqlite3.IntegrityError,match='key already used'):
            insert(raw,'work_billing_conversions',dict(row,id='K'*26,conversion_key_hash=link['conversion_key_hash'],destination_transaction_id='unused'))
        with pytest.raises(sqlite3.IntegrityError,match='key already used'):
            insert(raw,'work_links',dict(link,id='K'*26,conversion_key_hash=row['conversion_key_hash']))


@pytest.mark.parametrize('conflict', ['overlap','basis','legacy','disjoint'])
def test_transaction_insert_activation_guard(migrated, rows, conflict):
    allocation,sales=rows
    with sqlite3.connect(migrated) as source, sqlite3.connect(':memory:') as raw:
        source.row_factory=sqlite3.Row
        for table in ['transactions','work_billing_allocations']:
            raw.execute(source.execute('SELECT sql FROM sqlite_schema WHERE name=?',(table,)).fetchone()[0])
        raw.execute(source.execute("SELECT sql FROM sqlite_schema WHERE name='work_billing_transaction_insert'").fetchone()[0])
        first=dict(source.execute('SELECT * FROM transactions WHERE id=?',(sales[0]['transaction_id'],)).fetchone())
        second=dict(source.execute('SELECT * FROM transactions WHERE id=?',(sales[1]['transaction_id'],)).fetchone())
        insert(raw,'transactions',first)
        insert(raw,'work_billing_allocations',allocation(**proof()))
        other={'overlap':proof(((30,60),)), 'basis':proof(((40,100),),source_basis_hash='b'*64),
               'legacy':{},'disjoint':proof(((40,100),))}[conflict]
        insert(raw,'work_billing_allocations',allocation(1,**other))
        if conflict == 'disjoint':
            insert(raw,'transactions',second)
        else:
            with pytest.raises(sqlite3.IntegrityError,match='already consumed'):
                insert(raw,'transactions',second)


@pytest.mark.parametrize(('field','value'), [('rate_percent_millionths',-1),
    ('rate_percent_millionths',1.5),('taxable_minor_units',-1),('taxable_minor_units',1.5),
    ('tax_minor_units',-1),('tax_minor_units',1.5),('tax_minor_units','9223372036854775808')])
def test_existing_component_tax_bounds(migrated,field,value):
    with sqlite3.connect(migrated) as source, sqlite3.connect(':memory:') as raw:
        source.row_factory=sqlite3.Row
        raw.execute(source.execute("SELECT sql FROM sqlite_schema WHERE name='sales_tax_components'").fetchone()[0])
        row=dict(source.execute('SELECT * FROM sales_tax_components LIMIT 1').fetchone())
        with pytest.raises(sqlite3.IntegrityError):
            insert(raw,'sales_tax_components',dict(row,**{field:value}))


def test_malformed_json_rejects_with_conflict_trigger_running_first(migrated, rows):
    with raw_open(migrated) as raw:
        insert(raw,'work_billing_allocations',rows[0](**proof()))
        ddl=raw.execute("SELECT sql FROM sqlite_schema WHERE name='work_billing_allocation_active'").fetchone()[0]
        raw.execute('DROP TRIGGER work_billing_allocation_active')
        raw.execute(ddl)
        for value in ['broken','["not a document"]','{"value": "text"}', '[null]', '"string"']:
            with pytest.raises(sqlite3.IntegrityError):
                insert(raw,'work_billing_allocations',rows[0](1,**proof(spans_json=value)))


def test_local_index_sharing_replaced_trigger_name_survives(prior):
    with open_database(prior,writable=True) as db:
        sql='CREATE INDEX work_billing_transaction_update ON work_billing_allocations(created_at)'
        db.raw.execute(sql)
        migrate_to_head(db,'company',None)
        assert db.raw.execute("SELECT sql FROM sqlite_schema WHERE type='index' AND name='work_billing_transaction_update'").fetchone()[0] == sql
