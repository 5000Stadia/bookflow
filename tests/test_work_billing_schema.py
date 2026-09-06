"""Raw billing ownership boundaries and preserving co0011 migration witnesses."""
import ast
import importlib
import inspect
import sqlite3

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.test_customer_work_lifecycle import make
from tests.test_service_sales_lifecycle import sale, post, COMPANY  # noqa: F401
from tests.test_row8_journal import database_path

MIGRATION = importlib.import_module('bookflow.storage.company_migrations.versions.0011_work_billing')
OLD = importlib.import_module('bookflow.storage.company_migrations.versions.0009_service_sales')


def insert(raw, table, values, replace=False):
    raw.execute(f"INSERT {'OR REPLACE ' if replace else ''}INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


@pytest.fixture
def records(client, sale):
    work = make(client, sale)
    other = make(client, sale)
    first, second = post(client, sale), post(client, sale)
    with open_database(database_path(client), writable=True) as db:
        source = dict(db.conn.execute(sa.select(c.work_lines).where(c.work_lines.c.document_id == work['id'])).mappings().one())
        identity = dict(db.conn.execute(sa.select(c.work_line_identities).where(c.work_line_identities.c.id == source['line_id'])).mappings().one())
        lines = [dict(db.conn.execute(sa.select(c.sales_line_profiles).where(c.sales_line_profiles.c.transaction_id == doc['id'])).mappings().one()) for doc in (first, second)]
    def allocation(index=0, **changes):
        line = lines[index]
        result = dict(id=new_id(), transaction_id=line['transaction_id'], revision_id=line['revision_id'],
            document_line_id=line['document_line_id'], source_document_id=source['document_id'],
            source_revision_id=source['revision_id'], source_line_id=source['id'],
            root_document_id=identity['root_document_id'], root_line_id=identity['root_line_id'],
            quantity_microunits=source['quantity_microunits'], net_minor_units=source['net_minor_units'],
            tax_minor_units=source['tax_minor_units'], gross_minor_units=source['gross_minor_units'],
            facts_snapshot=source['facts_snapshot'], created_at=source['created_at'],
            created_by=source['created_by'], created_via=source['created_via'])
        return dict(result, **changes)
    def conversion(index=0, **changes):
        line = lines[index]
        return dict(dict(id=new_id(), source_document_id=source['document_id'], source_revision_id=source['revision_id'],
            source_version=1, destination_transaction_id=line['transaction_id'], destination_revision_id=line['revision_id'],
            destination_type='invoice', relation='estimate_invoice', conversion_key_hash='a'*64, request_hash='b'*64,
            created_at=source['created_at'], created_by=source['created_by'], created_via=source['created_via']), **changes)
    return work, other, first, second, allocation, conversion


def test_immutable_and_active_root_insertion(client, records):
    *_, allocation, conversion = records
    with open_database(database_path(client), writable=True) as db:
        a, link = allocation(), conversion()
        insert(db.raw, 'work_billing_allocations', a)
        insert(db.raw, 'work_billing_conversions', link)
        for table, row in [('work_billing_allocations', a), ('work_billing_conversions', link)]:
            for sql in [f'UPDATE {table} SET created_via=created_via', f'DELETE FROM {table}']:
                with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                    db.raw.execute(sql)
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                insert(db.raw, table, row, replace=True)
        with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
            insert(db.raw, 'work_billing_allocations', allocation(1))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('change', [
    {'quantity_microunits': 0}, {'quantity_microunits': 1.5}, {'net_minor_units': -1},
    {'tax_minor_units': 1.2}, {'gross_minor_units': 0}, {'facts_snapshot': '[]'},
    {'facts_snapshot': 'broken'}, {'source_revision_id': 'foreign'},
    {'root_line_id': 'foreign'}, {'document_line_id': 'foreign'}, {'revision_id': 'foreign'},
])
def test_allocation_bounds_and_composite_ownership(client, records, change):
    allocation = records[-2]
    with open_database(database_path(client), writable=True) as db:
        before = db.raw.execute('SELECT * FROM work_billing_allocations ORDER BY id').fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            insert(db.raw, 'work_billing_allocations', allocation(**change))
        assert db.raw.execute('SELECT * FROM work_billing_allocations ORDER BY id').fetchall() == before


@pytest.mark.parametrize('change', [
    {'source_version': 0}, {'source_version': 1.5}, {'request_hash': 'short'},
    {'conversion_key_hash': 'short'}, {'relation': 'work_order_invoice'},
    {'destination_type': 'sales_receipt'}, {'source_revision_id': 'foreign'},
])
def test_conversion_bounds_and_lineage(client, records, change):
    with open_database(database_path(client), writable=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            insert(db.raw, 'work_billing_conversions', records[-1](**change))


def test_cross_store_key_collision_both_directions(client, records):
    work, _, _, _, _, conversion = records
    accepted = client.run('estimate update', dict(estimate=work['id'], expected_version=1, status='accepted', decision_note='Agreed'), company=COMPANY)
    client.run('estimate work-order', dict(estimate=work['id'], expected_version=accepted['version'], conversion_key='operational-key', date='2026-01-13'), company=COMPANY)
    with open_database(database_path(client), writable=True) as db:
        operational = dict(db.conn.execute(sa.select(c.work_links).where(c.work_links.c.source_document_id == work['id'])).mappings().one())
        insert(db.raw, 'work_billing_conversions', conversion())
        with pytest.raises(sqlite3.IntegrityError, match='key already used'):
            insert(db.raw, 'work_links', dict(operational, id=new_id(), conversion_key_hash='a'*64))
        with pytest.raises(sqlite3.IntegrityError, match='key already used'):
            insert(db.raw, 'work_billing_conversions', conversion(1, conversion_key_hash=operational['conversion_key_hash']))


def test_final_pointer_and_status_boundary_and_own_replacement(client, records):
    _, _, first, second, allocation, _ = records
    # Prepare a second revision before billing, so the raw pointer can activate history.
    revised = client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Correction'), company=COMPANY)
    with open_database(database_path(client), writable=True) as db:
        old = allocation()
        insert(db.raw, 'work_billing_allocations', old)  # historical and inactive
        insert(db.raw, 'work_billing_allocations', allocation(1))
        with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
            db.raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?', (old['revision_id'], first['id']))
    client.run('invoice void', dict(invoice=second['id'], expected_version=1), company=COMPANY, reason='Release work')
    with open_database(database_path(client), writable=True) as db:
        db.raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?', (old['revision_id'], first['id']))
        new_line = db.conn.execute(sa.select(c.sales_line_profiles).where(c.sales_line_profiles.c.revision_id == revised['revision']['id'])).mappings().one()
        insert(db.raw, 'work_billing_allocations', allocation(revision_id=new_line['revision_id'], document_line_id=new_line['document_line_id']))
        db.raw.execute('UPDATE transactions SET current_revision_id=? WHERE id=?', (new_line['revision_id'], first['id']))
        with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
            db.raw.execute("UPDATE transactions SET status='posted', voided_at=NULL, voided_by=NULL, void_reason=NULL, void_posting_batch_id=NULL WHERE id=?", (second['id'],))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.fixture
def historical_client(tmp_path, monkeypatch):
    """Populate only the pre-billing demo prefix before restoring co0010 DDL.

    A historical company cannot contain the amount-priced sales first added by
    co0011. Build its actual old workload; never fake rates to fit newer facts.
    """
    import bookflow
    from bookflow.commands import hub_cmds
    load = hub_cmds._load_seed
    def historical_seed(resource='seed.toml'):
        seed = load(resource)
        seed['commands'] = seed['commands'][:201 if resource == 'seed.toml' else 107]
        return seed
    monkeypatch.setattr(hub_cmds, '_load_seed', historical_seed)
    root = tmp_path / 'historical-root'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.demo.reset()
    return client


def old_company(client, local=True):
    """Restore the frozen co0010 price table on this disposable populated company."""
    path = database_path(client)
    with open_database(path, writable=True) as db:
        raw = db.raw
        raw.execute('PRAGMA foreign_keys=OFF')
        objects = raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE type IN ('view','trigger')").fetchall()
        for kind, name, _ in objects:
            raw.execute(f'DROP {kind} "{name}"')
        ddl = next(s for s in OLD.DDL if s.startswith('CREATE TABLE sales_line_profiles'))
        if local:
            ddl = ddl.replace('document_line_id VARCHAR', "local_note TEXT NOT NULL DEFAULT 'kept' CHECK(length(local_note)>0),\n local_double BIGINT GENERATED ALWAYS AS (net_minor_units*2) STORED,\n local_virtual TEXT GENERATED ALWAYS AS (local_note || '!') VIRTUAL,\n document_line_id VARCHAR", 1)
        cols = [r[1] for r in raw.execute('PRAGMA table_info(sales_line_profiles)') if r[1] != 'pricing_basis']
        names = ','.join(cols)
        rows = raw.execute(f'SELECT {names} FROM sales_line_profiles').fetchall()
        raw.execute('DROP TABLE sales_line_profiles')
        raw.execute(ddl)
        raw.executemany(f'INSERT INTO sales_line_profiles ({names}) VALUES ({",".join("?" for _ in cols)})', rows)
        if local:
            raw.execute("UPDATE sales_line_profiles SET local_note='saved:' || document_line_id")
        raw.execute('DROP TABLE work_billing_allocations')
        raw.execute('DROP TABLE work_billing_conversions')
        for _, name, sql in objects:
            if not name.startswith('work_billing_') and name != 'work_link_billing_key':
                raw.execute(sql)
        if local:
            raw.execute('CREATE INDEX local_sale_note ON sales_line_profiles(local_note) WHERE net_minor_units>0')
            raw.execute('CREATE VIEW local_sale AS SELECT document_line_id,local_note,local_double FROM sales_line_profiles')
            raw.execute("CREATE TRIGGER local_sale_view INSTEAD OF DELETE ON local_sale BEGIN SELECT RAISE(ABORT,'keep local view'); END")
            raw.execute("CREATE TRIGGER local_other AFTER INSERT ON classes BEGIN SELECT count(*) FROM local_sale; END")
        raw.execute("UPDATE alembic_version SET version_num='co0010'")
        raw.execute('PRAGMA foreign_keys=ON')
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
    return path


def snapshot(raw):
    tables = raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return {name: raw.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for (name,) in tables}


def test_populated_local_columns_generated_values_and_objects_survive(historical_client):
    path = old_company(historical_client)
    with open_database(path, writable=True) as db:
        before = snapshot(db.raw)
        columns = [r[1] for r in db.raw.execute('PRAGMA table_xinfo(sales_line_profiles)')]
        objects = db.raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name LIKE 'local_%' ORDER BY name").fetchall()
        assert migrate_to_head(db, 'company', None) == ('co0010', 'co0011')
        after = snapshot(db.raw)
        for table, rows in before.items():
            if table not in ('sales_line_profiles', 'alembic_version'):
                assert after[table] == rows
        assert db.raw.execute(f'SELECT {",".join(columns)} FROM sales_line_profiles ORDER BY rowid').fetchall() == before['sales_line_profiles']
        assert db.raw.execute("SELECT type,name,sql FROM sqlite_schema WHERE name LIKE 'local_%' ORDER BY name").fetchall() == objects
        assert {r[0] for r in db.raw.execute('SELECT pricing_basis FROM sales_line_profiles')} == {'unit'}
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('failure', ['unknown', 'late'])
def test_migration_failure_rolls_back_every_object_and_row(historical_client, monkeypatch, failure):
    path = old_company(historical_client)
    with open_database(path, writable=True) as db:
        if failure == 'unknown':
            db.raw.execute("ALTER TABLE sales_line_profiles ADD COLUMN pricing_basis TEXT DEFAULT 'local'")
        else:
            original = sa.engine.Connection.exec_driver_sql
            def fail_after_rebuild(connection, statement, *args, **kwargs):
                if statement == 'PRAGMA foreign_key_check':
                    assert connection.connection.driver_connection.execute(
                        "SELECT count(*) FROM sqlite_schema WHERE name='work_billing_allocations'"
                    ).fetchone()[0] == 1
                    raise RuntimeError('injected final migration verification failure')
                return original(connection, statement, *args, **kwargs)
            monkeypatch.setattr(sa.engine.Connection, 'exec_driver_sql', fail_after_rebuild)
        before = snapshot(db.raw)
        objects = db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name').fetchall()
        with pytest.raises(BookflowError) as exc:
            migrate_to_head(db, 'company', None)
        assert exc.value.code == 'E_MIGRATION_FAILED'
        assert snapshot(db.raw) == before
        assert db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY name').fetchall() == objects
        assert db.raw.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_fresh_schema_and_revision_local_ddl(tmp_path):
    with open_database(tmp_path / 'fresh.db', writable=True, create=True) as db:
        assert migrate_to_head(db, 'company', None) == (None, 'co0011')
        for table in (c.work_billing_allocations, c.work_billing_conversions):
            assert {r[1] for r in db.raw.execute(f'PRAGMA table_info({table.name})')} == set(table.c.keys())
            from sqlalchemy.schema import CreateTable
            from sqlalchemy.dialects.sqlite import dialect
            stored = db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (table.name,)).fetchone()[0]
            assert ' '.join(stored.split()) == ' '.join(str(CreateTable(table).compile(dialect=dialect())).split())
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    tree = ast.parse(inspect.getsource(MIGRATION))
    ddl = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DDL' for t in n.targets))
    assert ast.literal_eval(ddl) == MIGRATION.DDL
    assert 'bookflow.company' not in inspect.getsource(MIGRATION)
    assert HEADS == {'company': 'co0011', 'hub': 'hub0011'}


@pytest.mark.parametrize(('basis', 'price', 'valid'), [
    ('unit', 1234, True), ('unit', 0, True), ('unit', None, False),
    ('unit', -1, False), ('unit', 1.25, False), ('amount', None, True),
    ('amount', 0, False), ('other', None, False), (None, None, False),
])
def test_price_mode_sql_constraints(client, sale, basis, price, valid):
    post(client, sale)
    with open_database(database_path(client), writable=False) as db:
        ddl = db.raw.execute("SELECT sql FROM sqlite_schema WHERE name='sales_line_profiles'").fetchone()[0]
        row = dict(db.conn.execute(sa.select(c.sales_line_profiles).limit(1)).mappings().one())
    # Isolate the migrated table's CHECK contract; cross-table FKs are tested above.
    with sqlite3.connect(':memory:') as raw:
        raw.execute(ddl)
        row.update(pricing_basis=basis, unit_price_minor_units=price)
        if valid:
            insert(raw, 'sales_line_profiles', row)
            assert raw.execute('SELECT unit_price_minor_units,pricing_basis FROM sales_line_profiles').fetchone() == (price, basis)
        else:
            with pytest.raises(sqlite3.IntegrityError):
                insert(raw, 'sales_line_profiles', row)


def test_real_foreign_roots_and_cross_owned_sales_rejected(client, records):
    _, other, _, _, allocation, conversion = records
    with open_database(database_path(client), writable=True) as db:
        root = db.raw.execute('SELECT root_document_id,root_line_id FROM work_line_identities WHERE document_id=?', (other['id'],)).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match='allocation root'):
            insert(db.raw, 'work_billing_allocations', allocation(root_document_id=root[0], root_line_id=root[1]))
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            insert(db.raw, 'work_billing_allocations', allocation(document_line_id=allocation(1)['document_line_id']))
        with pytest.raises(sqlite3.IntegrityError):
            insert(db.raw, 'work_billing_conversions', conversion(destination_revision_id=conversion(1)['destination_revision_id']))


def test_nonbirth_conversion_rejected_and_zero_allocation_allowed(client, records):
    _, _, first, _, allocation, conversion = records
    revised = client.run('invoice update', dict(invoice=first['id'], expected_version=1, memo='Later'), company=COMPANY)
    with open_database(database_path(client), writable=True) as db:
        with pytest.raises(sqlite3.IntegrityError, match='conversion lineage'):
            insert(db.raw, 'work_billing_conversions', conversion(destination_revision_id=revised['revision']['id']))
        insert(db.raw, 'work_billing_allocations', allocation(net_minor_units=0, tax_minor_units=0, gross_minor_units=0))


def test_estimate_and_descendant_order_share_one_full_root(client, records):
    work, _, _, _, allocation, _ = records
    accepted = client.run('estimate update', dict(estimate=work['id'], expected_version=1, status='accepted', decision_note='Agreed'), company=COMPANY)
    order = client.run('estimate work-order', dict(estimate=work['id'], expected_version=accepted['version'], conversion_key='root-descendant', date='2026-01-13'), company=COMPANY)
    with open_database(database_path(client), writable=True) as db:
        source = db.conn.execute(sa.select(c.work_lines).where(c.work_lines.c.document_id == order['id'])).mappings().one()
        insert(db.raw, 'work_billing_allocations', allocation(source_document_id=source['document_id'], source_revision_id=source['revision_id'], source_line_id=source['id']))
        with pytest.raises(sqlite3.IntegrityError, match='already consumed'):
            insert(db.raw, 'work_billing_allocations', allocation(1))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
