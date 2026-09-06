"""Disposable co0008 preservation, frozen sales DDL, ownership and rollback witnesses."""
import ast
import importlib
import inspect
import shutil
import sqlite3

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import current_revision_raw, migrate_to_head
from tests.test_attachment_migration import _old_data, _insert
from tests.test_migration_chain import _make_revision, _normalized_schema
from tests.test_row6_note_migration import _schema_semantics

MODULE = 'bookflow.storage.company_migrations.versions.0009_service_sales'
NEW = ('sales_profiles', 'sales_line_profiles', 'sales_tax_components')
CHANGED = ('transactions', 'document_lines', 'posting_line_sources')
CREATED = dict(created_at='2026-03-11T12:13:14Z', created_by='U1', created_via='python')
COMMON = dict(CREATED, version=1, updated_at=CREATED['created_at'], updated_by='U1', updated_via='python')
SNAPSHOT = '{ "name": "Original café", "zero": 0, "false": false, "empty": "" }'


@pytest.fixture(autouse=True)
def sales_migration_target(monkeypatch):
    """Keep these frozen co8→co9 witnesses scoped to their owning migration."""
    from bookflow.storage.migrate import HEADS
    monkeypatch.setitem(HEADS, 'company', 'co0009')


def _populate(db):
    """Write real co0008 columns, including foreign replacement and void history."""
    conn = db.raw
    _old_data(conn)
    conn.execute('BEGIN IMMEDIATE')
    for id_, type_ in [('bank', 'bank'), ('income', 'income'), ('ar', 'accounts_receivable'), ('tax', 'other_current_liability')]:
        db.conn.execute(schema.accounts.insert().values(**COMMON, id=id_, name=id_, name_key=id_,
            full_name=id_, full_name_key=id_, depth=1, path=id_, type=type_, currency='USD'))
    db.conn.execute(schema.customers.insert().values(**COMMON, id='customer', name='Customer', name_key='customer',
        full_name='Customer', full_name_key='customer', depth=1, path='customer'))
    db.conn.execute(schema.vendors.insert().values(**COMMON, id='agency', name='Agency', name_key='agency', is_tax_agency=True))
    for id_, type_ in [('service', 'service'), ('tax-item', 'sales_tax_item')]:
        db.conn.execute(schema.items.insert().values(**COMMON, id=id_, name=id_, name_key=id_,
            full_name=id_, full_name_key=id_, depth=1, path=id_, type=type_))
    _insert(conn, 'audit_events', dict(id='event', at=CREATED['created_at'], command='journal post', actor_id='U1',
        actor_kind='human', interface='python', client_name='migration fixture', client_version='1', client_host='local',
        session_id='session', request_id='request', summary='Retained foreign history'))
    _insert(conn, 'audit_entries', dict(id='entry', event_id='event', record_type='transaction', record_id='journal',
        action='create', after=SNAPSHOT.encode()))
    _insert(conn, 'idempotency_keys', dict(actor_id='U1', key='retained-key', command='journal post',
        input_hash='a'*64, state='committed', request_id='request', created_at=CREATED['created_at']))
    _insert(conn, 'exchange_rates', dict(id='rate', version=2, date='2026-03-11', from_currency='JPY',
        to_currency='USD', rate='0.0068', source='manual', entered_by='U1', entered_at=CREATED['created_at']))
    _insert(conn, 'transactions', dict(COMMON, id='journal', type='journal_entry', number='OLD-1',
        current_revision_id='r2', status='voided', voided_at=CREATED['created_at'], voided_by='U1',
        void_reason='Retained void', void_posting_batch_id='b4'))
    for line in (1, 2):
        _insert(conn, 'document_line_identities', dict(CREATED, id=f'identity{line}', transaction_id='journal'))
    foreign = dict(original_minor_units=2345, original_currency='JPY', rate_used='0.0068', rate_source='table:manual')
    for revision in (1, 2):
        _insert(conn, 'transaction_revisions', dict(CREATED, id=f'r{revision}', transaction_id='journal',
            revision_number=revision, supersedes_revision_id='r1' if revision == 2 else None, date='2026-03-11',
            number='OLD-1', total_minor_units=1595, currency='USD', issuer_snapshot=SNAPSHOT,
            custom_fields_snapshot=SNAPSHOT, audit_event_id='event', memo=f'Captured revision {revision}'))
        for line, account, side in [(1, 'bank', 'debit'), (2, 'income', 'credit')]:
            _insert(conn, 'document_lines', dict(CREATED, **foreign, id=f'd{revision}{line}', transaction_id='journal',
                revision_id=f'r{revision}', line_id=f'identity{line}', position=line, kind='journal', account_id=account,
                side=side, amount_minor_units=1595, currency='USD', account_snapshot=SNAPSHOT))
    for batch, revision, kind, reverse, replace in [(1, 1, 'original', None, None), (2, 1, 'reversal', 'b1', None),
            (3, 2, 'replacement', None, 'b1'), (4, 2, 'reversal', 'b3', None)]:
        _insert(conn, 'posting_batches', dict(CREATED, id=f'b{batch}', transaction_id='journal', revision_id=f'r{revision}',
            kind=kind, effective_date='2026-03-11', reverses_batch_id=reverse, replaces_batch_id=replace, audit_event_id='event'))
        for line, account in [(1, 'bank'), (2, 'income')]:
            debit = (line == 1) != (reverse is not None)
            _insert(conn, 'posting_lines', dict(CREATED, **foreign, id=f'p{batch}{line}', transaction_id='journal',
                batch_id=f'b{batch}', line_no=line, account_id=account, debit_minor_units=1595 if debit else 0,
                credit_minor_units=0 if debit else 1595, currency='USD', account_snapshot=SNAPSHOT,
                reversed_line_id=f'p{batch-1}{line}' if reverse else None))
            _insert(conn, 'posting_line_sources', dict(CREATED, id=f's{batch}{line}', transaction_id='journal',
                posting_line_id=f'p{batch}{line}', revision_id=f'r{revision}', document_line_id=f'd{revision}{line}',
                amount_minor_units=1595, currency='USD', reversed_source_id=f's{batch-1}{line}' if reverse else None))
    conn.execute('COMMIT')
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.fixture
def old(tmp_path):
    path = tmp_path / 'co0008.db'
    _make_revision(path, 'company', 'co0008', lambda _: None)
    with open_database(path, writable=True) as db:
        _populate(db)
    return path


def _rows(conn):
    return {name: (tuple(r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')),
                   conn.execute(f'SELECT * FROM "{name}" ORDER BY 1, 2').fetchall())
            for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'")}


def test_populated_upgrade_preserves_all_old_values_objects_and_copy(old, tmp_path):
    with open_database(old, writable=True) as db:
        db.raw.execute('CREATE INDEX local_journal_number ON transactions(number) WHERE type=\'journal_entry\'')
        db.raw.execute("CREATE TRIGGER local_journal_keep_number BEFORE UPDATE OF number ON transactions WHEN NEW.number = '' BEGIN SELECT RAISE(ABORT, 'local rule'); END")
        before, original_schema = _rows(db.raw), _normalized_schema(db.raw)
        objects = db.raw.execute("SELECT name,sql FROM sqlite_schema WHERE type IN ('index','trigger') AND sql IS NOT NULL ORDER BY name").fetchall()
        assert migrate_to_head(db, 'company', tmp_path / 'backups') == ('co0008', 'co0009')
        for name, (columns, values) in before.items():
            actual = db.raw.execute(f'SELECT {", ".join(columns)} FROM "{name}" ORDER BY 1, 2').fetchall()
            assert actual == values if name != 'sequences' else set(values) <= set(actual)
        assert set(_rows(db.raw)) - set(before) == set(NEW)
        for name, sql in objects:
            assert db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone() == (sql,)
        assert db.raw.execute('SELECT DISTINCT tax_component_id FROM posting_line_sources').fetchall() == [(None,)]
        assert db.raw.execute("SELECT name,next_number,prefix FROM sequences WHERE name IN ('invoice','sales_receipt') ORDER BY name").fetchall() == [('invoice', 1, ''), ('sales_receipt', 1, '')]
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert migrate_to_head(db, 'company', None) == ('co0009', 'co0009')
        after = _rows(db.raw)
    backup, = (tmp_path / 'backups').glob('*-from-co0008.db')
    with open_database(backup, writable=False) as db:
        assert _rows(db.raw) == before
        assert _normalized_schema(db.raw) == original_schema
    copied = tmp_path / 'copied.db'
    shutil.copyfile(old, copied)
    with open_database(copied, writable=False) as db:
        assert _rows(db.raw) == after
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
    assert current_revision_raw(copied) == 'co0009'


def test_frozen_ddl_matches_declared_columns_keys_checks(old, tmp_path, monkeypatch):
    fresh = tmp_path / 'fresh.db'
    with open_database(fresh, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        inspector = sa.inspect(db.conn)
        for name in CHANGED + NEW:
            actual = sa.Table(name, sa.MetaData(), autoload_with=db.conn)
            expected = schema.metadata.tables[name]
            if name == 'sales_line_profiles':
                # co0011 introduced amount pricing; this witness owns co0009.
                # Undo only that declared delta, retaining every other assertion.
                expected = expected.to_metadata(sa.MetaData())
                expected._columns.remove(expected.c.pricing_basis)
                expected.c.unit_price_minor_units.nullable = False
                price_check, = (c for c in expected.constraints
                                if c.name == 'ck_sales_pricing_basis')
                expected.constraints.remove(price_check)
                expected.append_constraint(sa.CheckConstraint(
                    "typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0",
                    name='ck_sales_unit_price_minor_units_nonnegative'))
            assert [(c.name, str(c.type), c.nullable, c.primary_key) for c in actual.c] == [
                (c.name, str(c.type), c.nullable, c.primary_key) for c in expected.c]
            assert {(tuple(c.column_keys), tuple(e.target_fullname for e in c.elements), c.deferrable, c.initially)
                    for c in actual.foreign_key_constraints} == {
                    (tuple(c.column_keys), tuple(e.target_fullname for e in c.elements), c.deferrable, c.initially)
                    for c in expected.foreign_key_constraints}
            assert {str(c.sqltext) for c in actual.constraints if isinstance(c, sa.CheckConstraint)} == {
                str(c.sqltext) for c in expected.constraints if isinstance(c, sa.CheckConstraint)}
            assert {tuple(x['column_names']) for x in inspector.get_unique_constraints(name)} == {
                tuple(c.columns.keys()) for c in expected.constraints if isinstance(c, sa.UniqueConstraint)}
    module = importlib.import_module(MODULE)
    tree = ast.parse(inspect.getsource(module))
    ddl = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'DDL' for t in n.targets))
    assert all(type(statement) is str for statement in ddl)
    monkeypatch.setattr(schema, 'metadata', sa.MetaData())
    for name in CHANGED + NEW:
        monkeypatch.setattr(schema, name, None)
    with open_database(old, writable=True) as db:
        migrate_to_head(db, 'company', None)
    assert _schema_semantics(old) == _schema_semantics(fresh)


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('backups', [False, True])
def test_dependent_views_and_external_triggers_survive_upgrade_or_rollback(old, tmp_path, fail, backups):
    with open_database(old, writable=True) as db:
        db.raw.execute('CREATE VIEW z_local_sales AS SELECT id, number FROM transactions')
        # Alphabetical restoration sees this view before its dependency. Its
        # quoted identifier also prevents using unquoted schema names in DROP.
        db.raw.execute('CREATE VIEW "a local ""sales" AS SELECT * FROM z_local_sales')
        db.raw.execute('''CREATE TRIGGER local_accounts_guard AFTER UPDATE ON accounts
            WHEN NEW.name = 'local-blocked'
            BEGIN SELECT number FROM "a local ""sales";
            SELECT RAISE(ABORT, 'local account guard'); END''')
        db.raw.execute('''CREATE TRIGGER local_view_guard INSTEAD OF UPDATE ON "a local ""sales"
            BEGIN SELECT RAISE(ABORT, 'local view guard'); END''')
        before, original_schema = _rows(db.raw), _normalized_schema(db.raw)
        view_rows = db.raw.execute('SELECT * FROM "a local ""sales" ORDER BY id').fetchall()
        objects = db.raw.execute("SELECT name,sql FROM sqlite_schema WHERE type IN ('view','trigger') ORDER BY name").fetchall()
        if fail:
            denied = []
            def interrupt(action, name, *args):
                if action == sqlite3.SQLITE_CREATE_VIEW and name == 'a local "sales' and not denied:
                    denied.append(name)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.raw.set_authorizer(interrupt)
            try:
                with pytest.raises(BookflowError) as raised:
                    migrate_to_head(db, 'company', tmp_path / 'backups' if backups else None)
            finally:
                db.raw.set_authorizer(None)
            assert denied == ['a local "sales']
            assert raised.value.code == 'E_MIGRATION_FAILED'
            assert _rows(db.raw) == before
            assert _normalized_schema(db.raw) == original_schema
            assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0008',)
        else:
            assert migrate_to_head(db, 'company', tmp_path / 'backups' if backups else None) == ('co0008', 'co0009')
            for table, (columns, values) in before.items():
                actual = db.raw.execute(f'SELECT {", ".join(columns)} FROM "{table}" ORDER BY 1, 2').fetchall()
                assert actual == values if table != 'sequences' else set(values) <= set(actual)
        for name, sql in objects:
            assert db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?', (name,)).fetchone() == (sql,)
        assert db.raw.execute('SELECT * FROM "a local ""sales" ORDER BY id').fetchall() == view_rows
        with pytest.raises(sqlite3.IntegrityError, match='local account guard'):
            db.raw.execute("UPDATE accounts SET name='local-blocked' WHERE id='bank'")
        with pytest.raises(sqlite3.IntegrityError, match='local view guard'):
            db.raw.execute('UPDATE "a local ""sales" SET number=number')
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        after = _rows(db.raw)
    copied = tmp_path / 'local-schema-copy.db'
    shutil.copyfile(old, copied)
    with open_database(copied, writable=False) as db:
        assert _rows(db.raw) == after
        assert db.raw.execute('SELECT * FROM "a local ""sales" ORDER BY id').fetchall() == view_rows
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)


@pytest.mark.parametrize('backups', [False, True])
@pytest.mark.parametrize('failure', ['mid_rebuild', 'late', 'foreign_key'])
def test_failed_upgrade_restores_schema_data_and_fk_mode(old, tmp_path, backups, failure):
    with open_database(old, writable=True) as db:
        if failure == 'late':
            db.raw.execute("CREATE TRIGGER fail_revision BEFORE UPDATE ON alembic_version WHEN NEW.version_num='co0009' BEGIN SELECT RAISE(ABORT, 'late failure'); END")
        elif failure == 'foreign_key':
            # A pre-existing orphan must fail the final FK check, after rebuilds.
            db.raw.execute('PRAGMA foreign_keys=OFF')
            db.raw.execute("INSERT INTO document_line_identities (id,transaction_id,created_at,created_by,created_via) VALUES ('orphan','missing','t','U1','python')")
            db.raw.execute('PRAGMA foreign_keys=ON')
        before, original_schema = _rows(db.raw), _normalized_schema(db.raw)
        if failure == 'mid_rebuild':
            denied = []

            def interrupt(action, name, *args):
                if action == sqlite3.SQLITE_DROP_TABLE and name == 'document_lines' and not denied:
                    denied.append(name)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.raw.set_authorizer(interrupt)
        with pytest.raises(BookflowError) as exc:
            migrate_to_head(db, 'company', tmp_path / 'backups' if backups else None)
        assert exc.value.code == 'E_MIGRATION_FAILED'
        if failure == 'mid_rebuild':
            db.raw.set_authorizer(None)
            assert denied == ['document_lines']
        assert _rows(db.raw) == before
        assert _normalized_schema(db.raw) == original_schema
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert not db.raw.in_transaction
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0008',)


def _sale(conn, id_='sale', type_='invoice'):
    _insert(conn, 'transactions', dict(COMMON, id=id_, type=type_, number=id_, current_revision_id=f'{id_}-r', status='posted'))
    _insert(conn, 'transaction_revisions', dict(CREATED, id=f'{id_}-r', transaction_id=id_, revision_number=1,
        date='2026-03-11', number=id_, total_minor_units=10800, currency='USD', issuer_snapshot='{}', custom_fields_snapshot='{}', audit_event_id='event'))
    header = dict(CREATED, revision_id=f'{id_}-r', transaction_id=id_, type=type_, customer_id='customer',
        control_account_id='ar' if type_ == 'invoice' else 'bank', due_date='2026-03-11' if type_ == 'invoice' else None,
        subtotal_minor_units=10000, tax_minor_units=800, profile_snapshot=SNAPSHOT)
    _insert(conn, 'sales_profiles', header)
    profiles, components = [], []
    for line in (1, 2):
        identity, doc = f'{id_}-identity{line}', f'{id_}-d{line}'
        _insert(conn, 'document_line_identities', dict(CREATED, id=identity, transaction_id=id_))
        _insert(conn, 'document_lines', dict(CREATED, id=doc, transaction_id=id_, revision_id=f'{id_}-r', line_id=identity,
            position=line, kind='sale', currency='USD'))
        profile = dict(CREATED, document_line_id=doc, transaction_id=id_, revision_id=f'{id_}-r', item_id='service',
            quantity_microunits=1000000, unit_id=None, unit_factor_nanounits=1000000000, base_quantity_microunits=1000000,
            unit_price_minor_units=10000 if line == 1 else 0, net_minor_units=10000 if line == 1 else 0,
            tax_minor_units=800 if line == 1 else 0, gross_minor_units=10800 if line == 1 else 0, item_snapshot=SNAPSHOT)
        _insert(conn, 'sales_line_profiles', profile)
        component = dict(CREATED, id=f'{id_}-tax{line}', transaction_id=id_, revision_id=f'{id_}-r', document_line_id=doc,
            tax_item_id='tax-item', agency_id='agency', liability_account_id='tax', rate_percent_millionths=8000000,
            taxable_minor_units=profile['net_minor_units'], tax_minor_units=profile['tax_minor_units'], component_snapshot=SNAPSHOT)
        _insert(conn, 'sales_tax_components', component)
        profiles.append(profile)
        components.append(component)
    return header, profiles, components


@pytest.fixture
def sales(old):
    with open_database(old, writable=True) as db:
        migrate_to_head(db, 'company', None)
        db.raw.execute('BEGIN IMMEDIATE')
        values = _sale(db.raw)
        _sale(db.raw, 'receipt', 'sales_receipt')
        db.raw.execute('COMMIT')
        yield db.raw, values


def test_sale_zero_lines_guards_and_deferred_header_fk(sales):
    conn, _ = sales
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    for name in NEW + ('transaction_revisions', 'document_line_identities', 'document_lines', 'posting_batches', 'posting_lines', 'posting_line_sources'):
        key = 'revision_id' if name == 'sales_profiles' else 'document_line_id' if name == 'sales_line_profiles' else 'id'
        for sql in (f'UPDATE {name} SET {key}={key}', f'DELETE FROM {name}'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable ledger history'):
                conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError, match='business documents are retained'):
        conn.execute('DELETE FROM transactions')
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("UPDATE transactions SET current_revision_id='missing' WHERE id='sale'")
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        conn.execute('COMMIT')
    conn.execute('ROLLBACK')
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('change', [dict(account_id=None), dict(side=None), dict(amount_minor_units=None),
    dict(account_snapshot=None), dict(amount_minor_units=0), dict(amount_minor_units=-1), dict(amount_minor_units=1.5),
    dict(account_snapshot='[]'), dict(side='other'), dict(account_id='missing')])
def test_journal_required_fields_cannot_pass_sql_unknown(sales, change):
    conn, _ = sales
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM document_lines WHERE id='d11'").fetchone())
    conn.row_factory = None
    row.update(id='bad-journal', line_id='bad-identity', position=3, **change)
    _insert(conn, 'document_line_identities', dict(CREATED, id='bad-identity', transaction_id='journal'))
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, 'document_lines', row)


@pytest.mark.parametrize('change', [dict(account_id='income'), dict(side='credit'), dict(amount_minor_units=0),
    dict(account_snapshot='{}'), dict(original_currency='JPY'), dict(kind='other'), dict(kind='journal')])
def test_sale_envelope_rejects_journal_and_foreign_values(sales, change):
    conn, _ = sales
    _insert(conn, 'document_line_identities', dict(CREATED, id='extra-identity', transaction_id='sale'))
    row = dict(CREATED, id='extra-line', transaction_id='sale', revision_id='sale-r', line_id='extra-identity',
        position=3, kind='sale', currency='USD')
    row.update(change)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, 'document_lines', row)


@pytest.mark.parametrize('table,column', [('sales_profiles', 'subtotal_minor_units'), ('sales_profiles', 'tax_minor_units'),
    ('sales_line_profiles', 'quantity_microunits'), ('sales_line_profiles', 'unit_factor_nanounits'),
    ('sales_line_profiles', 'base_quantity_microunits'), ('sales_line_profiles', 'unit_price_minor_units'),
    ('sales_line_profiles', 'net_minor_units'), ('sales_line_profiles', 'tax_minor_units'), ('sales_line_profiles', 'gross_minor_units'),
    ('sales_tax_components', 'rate_percent_millionths'), ('sales_tax_components', 'taxable_minor_units'), ('sales_tax_components', 'tax_minor_units')])
def test_integer_storage_rejects_negative_fractional_and_overflow(sales, table, column):
    conn, (header, profiles, components) = sales
    # Savepoint delete guards are removed only in this disposable constraint test;
    # reuse valid owners so failures cannot be masked by uniqueness or wrong FKs.
    conn.execute(f'DROP TRIGGER {table}_no_delete')
    row = dict(header if table == NEW[0] else profiles[0] if table == NEW[1] else components[0])
    key = 'revision_id' if table == NEW[0] else 'document_line_id' if table == NEW[1] else 'id'
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute(f'DELETE FROM {table} WHERE {key}=?', (row[key],))
    conn.execute('PRAGMA foreign_keys=ON')
    for value in (-1, 0.5, '9223372036854775808', None):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK|NOT NULL'):
            _insert(conn, table, dict(row, **{column: value}))
    if column in ('quantity_microunits', 'unit_factor_nanounits', 'base_quantity_microunits'):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
            _insert(conn, table, dict(row, **{column: 0}))
    _insert(conn, table, dict(row, **{column: 9223372036854775807}))
    assert conn.execute(f'SELECT typeof({column}) FROM {table} WHERE {key}=?', (row[key],)).fetchone() == ('integer',)


@pytest.mark.parametrize('table,column', list(zip(NEW, ('profile_snapshot', 'item_snapshot', 'component_snapshot'))))
def test_snapshots_are_objects(sales, table, column):
    conn, (header, profiles, components) = sales
    row = dict(header if table == NEW[0] else profiles[0] if table == NEW[1] else components[0])
    key = 'revision_id' if table == NEW[0] else 'document_line_id' if table == NEW[1] else 'id'
    conn.execute(f'DROP TRIGGER {table}_no_delete')
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute(f'DELETE FROM {table} WHERE {key}=?', (row[key],))
    conn.execute('PRAGMA foreign_keys=ON')
    for value in ('[]', 'null', '1', '"text"', 'broken', None):
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, table, dict(row, **{column: value}))
    _insert(conn, table, row)


def test_tax_source_ownership_and_nullable_journal_compatibility(sales):
    conn, (_, _, components) = sales
    _insert(conn, 'posting_batches', dict(CREATED, id='sale-b', transaction_id='sale', revision_id='sale-r',
        kind='original', effective_date='2026-03-11', audit_event_id='event'))
    _insert(conn, 'posting_lines', dict(CREATED, id='sale-p', transaction_id='sale', batch_id='sale-b', line_no=1,
        account_id='tax', debit_minor_units=0, credit_minor_units=800, currency='USD', account_snapshot='{}'))
    source = dict(CREATED, id='sale-source', transaction_id='sale', revision_id='sale-r', posting_line_id='sale-p',
        document_line_id='sale-d1', tax_component_id='sale-tax1', amount_minor_units=800, currency='USD')
    for change in (dict(tax_component_id='missing'), dict(tax_component_id='sale-tax2'),
                   dict(tax_component_id='receipt-tax1'), dict(revision_id='receipt-r'), dict(transaction_id='receipt')):
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            _insert(conn, 'posting_line_sources', dict(source, **change))
    _insert(conn, 'posting_line_sources', source)
    _insert(conn, 'posting_line_sources', dict(source, id='sale-net', tax_component_id=None))
    journal = dict(CREATED, id='journal-compatible', transaction_id='journal', revision_id='r1', posting_line_id='p11',
        document_line_id='d11', amount_minor_units=1595, currency='USD')
    _insert(conn, 'posting_line_sources', journal)
    assert conn.execute("SELECT tax_component_id FROM posting_line_sources WHERE id='journal-compatible'").fetchone() == (None,)
    component = dict(components[0], id='wrong-owner', revision_id='receipt-r')
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        _insert(conn, 'sales_tax_components', component)
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_no_downgrade(sales):
    conn, _ = sales
    before, original_schema = _rows(conn), _normalized_schema(conn)
    with pytest.raises(NotImplementedError):
        importlib.import_module(MODULE).downgrade()
    assert _rows(conn) == before and _normalized_schema(conn) == original_schema


@pytest.mark.parametrize('table', NEW[:2])
def test_profiles_require_exact_header_line_and_type_owners(sales, table):
    conn, (header, profiles, _) = sales
    row = dict(header if table == 'sales_profiles' else profiles[0])
    key = 'revision_id' if table == 'sales_profiles' else 'document_line_id'
    # Keep every referenced parent present. Updates isolate FK behavior from
    # immutable guards without introducing pre-existing orphans in this fixture.
    conn.execute(f'DROP TRIGGER {table}_no_update')
    changes = [dict(transaction_id='receipt'), dict(revision_id='missing')]
    if table == 'sales_profiles':
        changes.extend([dict(type='sales_receipt', due_date=None), dict(customer_id='missing'), dict(control_account_id='missing')])
    else:
        changes.extend([dict(document_line_id='missing'), dict(item_id='missing'), dict(unit_id='missing')])
    for change in changes:
        assignments = ', '.join(f'{column}=?' for column in change)
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            conn.execute(f'UPDATE {table} SET {assignments} WHERE {key}=?', (*change.values(), row[key]))
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
        _insert(conn, table, row)


def test_invoice_receipt_due_date_and_creation_provenance(sales):
    conn, (header, _, _) = sales
    conn.execute('DROP TRIGGER sales_profiles_no_delete')
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute("DELETE FROM sales_profiles WHERE revision_id='sale-r'")
    conn.execute('PRAGMA foreign_keys=ON')
    for change in (dict(due_date=None), dict(type='journal_entry'), dict(type=None),
                   dict(created_at=None), dict(created_by=None), dict(created_via=None)):
        with pytest.raises(sqlite3.IntegrityError, match='CHECK|NOT NULL'):
            _insert(conn, 'sales_profiles', dict(header, **change))
    _insert(conn, 'sales_profiles', header)
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute("DELETE FROM sales_profiles WHERE revision_id='receipt-r'")
    conn.execute('PRAGMA foreign_keys=ON')
    receipt = dict(header, revision_id='receipt-r', transaction_id='receipt', type='sales_receipt')
    with pytest.raises(sqlite3.IntegrityError, match='CHECK'):
        _insert(conn, 'sales_profiles', receipt)
    _insert(conn, 'sales_profiles', dict(receipt, due_date=None))
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_journal_envelope_remains_insertable_but_cannot_become_sale(sales):
    conn, _ = sales
    _insert(conn, 'document_line_identities', dict(CREATED, id='new-journal-line', transaction_id='journal'))
    row = dict(CREATED, id='new-journal-envelope', transaction_id='journal', revision_id='r2',
        line_id='new-journal-line', position=3, kind='journal', account_id='bank', side='debit',
        amount_minor_units=1, currency='USD', account_snapshot='{}')
    wrong_kind = dict(row, kind='sale', account_id=None, side=None, amount_minor_units=None, account_snapshot=None)
    with pytest.raises(sqlite3.IntegrityError, match='kind does not match'):
        _insert(conn, 'document_lines', wrong_kind)
    _insert(conn, 'document_lines', row)
    assert conn.execute("SELECT amount_minor_units FROM document_lines WHERE id='new-journal-envelope'").fetchone() == (1,)
