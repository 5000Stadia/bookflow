"""Disposable additive migration, ownership and immutable work history witnesses."""
import ast
import importlib
import inspect
import sqlite3
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema, records, units
from bookflow.core.ids import new_id
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head, current_revision_raw
from tests.test_migration_chain import _make_revision, _normalized_schema
from tests.test_service_sales_migration import _populate, _rows, _sale, COMMON, CREATED

MODULE = 'bookflow.storage.company_migrations.versions.0010_customer_work'
NEW = ('work_documents', 'work_revisions', 'work_line_identities', 'work_lines', 'work_links')
CHANGED = ('custom_field_scopes',)


@pytest.fixture
def old(tmp_path, monkeypatch):
    path = tmp_path / 'co9.db'
    from bookflow.storage.migrate import HEADS
    with open_database(path, writable=True, create=True) as db:
        with monkeypatch.context() as patch:
            patch.setitem(HEADS, 'company', 'co0009')
            migrate_to_head(db, 'company', None)
        _populate(db)
        db.raw.execute('BEGIN')
        _sale(db.raw)
        _sale(db.raw, 'receipt', 'sales_receipt')
        db.raw.execute('COMMIT')
        from tests.test_row5_custom_fields import _definition
        from bookflow.company.custom_fields import plan_owner_value_patch, apply_owner_value_plan
        definition = _definition(db.conn, name='Retained field', scopes=('customer','estimate'))
        apply_owner_value_plan(db, plan_owner_value_patch(db, record_type='customer',
            record_id=new_id(), patch={definition['id']: 'Original café'}, creating=True))
        _definition(db.conn, name='Inactive field', scopes=('vendor',), active=False)
    return path


@pytest.fixture
def db(old):
    with open_database(old, writable=True) as db:
        migrate_to_head(db, 'company', None)
        yield db


def document(db, kind='proposal', group=None, status='draft'):
    did, rid = new_id(), new_id()
    if kind == 'estimate' and group is None:
        group = did
    db.raw.execute('BEGIN')
    db.conn.execute(schema.work_documents.insert().values(**COMMON, id=did, kind=kind,
        number=did, current_revision_id=rid, status=status, active=True, estimate_group_id=group))
    revision(db, did, rid, status=status)
    db.raw.execute('COMMIT')
    return did, rid


def revision(db, did, rid=None, number=1, status='draft', **changes):
    rid = rid or new_id()
    values = dict(CREATED, id=rid, document_id=did, revision_number=number,
        date='2026-09-05', number=did, title='Captured work', status=status, active=True,
        customer_id='customer', currency='USD', net_minor_units=0, tax_minor_units=0,
        gross_minor_units=0, facts_snapshot='{}', custom_fields_snapshot='{}', audit_event_id='event')
    if status == 'accepted':
        values.update(accepted_revision_id=rid, accepted_at=CREATED['created_at'], accepted_by='U1')
    values.update(changes)
    db.conn.execute(schema.work_revisions.insert().values(**values))
    return rid


def identity(db, did, source=None, root=None):
    lid = new_id()
    db.conn.execute(schema.work_line_identities.insert().values(id=lid, document_id=did,
        root_document_id=root[0] if root else did, root_line_id=root[1] if root else lid,
        source_line_id=source))
    return lid


def line(db, did, rid, lid=None, **changes):
    lid = lid or identity(db, did)
    values = dict(CREATED, id=new_id(), document_id=did, revision_id=rid, line_id=lid,
        position=1, item_id='service', quantity_microunits=1_000_000, completed_quantity_microunits=0,
        unit_factor_nanounits=1_000_000_000, base_quantity_microunits=1_000_000,
        unit_price_minor_units=0, net_minor_units=0, tax_minor_units=0, gross_minor_units=0,
        pricing_basis='catalog', billable=True, facts_snapshot='{}')
    values.update(changes)
    db.conn.execute(schema.work_lines.insert().values(**values))
    return values['id'], lid


def link(db, source, dest, relation='copy', **changes):
    values = dict(CREATED, id=new_id(), source_document_id=source[0], source_revision_id=source[1],
        destination_document_id=dest[0], destination_revision_id=dest[1], relation=relation, source_version=1)
    if relation != 'copy':
        values.update(conversion_key_hash=new_id().ljust(64,'a'), request_hash='b'*64)
    values.update(changes)
    db.conn.execute(schema.work_links.insert().values(**values))
    return values['id']


def assert_scope_only_schema_change(before, after):
    """All old SQL is identical except the two enum entries and RENAME quoting."""
    expected = []
    for obj in before:
        obj = dict(obj)
        if obj['type'] == 'table' and obj['name'] == 'custom_field_scopes':
            obj['sql'] = obj['sql'].replace('CREATE TABLE custom_field_scopes ',
                'CREATE TABLE "custom_field_scopes" ').replace(
                "'vendor_credit','estimate','sales_order'",
                "'vendor_credit','proposal','work_order','estimate','sales_order'")
        expected.append(obj)
    assert after == expected


def test_co9_upgrade_preserves_every_old_row_and_schema_object(old, tmp_path):
    with open_database(old, writable=True) as db:
        db.raw.execute('CREATE VIEW local_work_view AS SELECT id, number FROM transactions')
        db.raw.execute('CREATE INDEX local_customer_name ON customers(name)')
        before, objects = _rows(db.raw), _normalized_schema(db.raw)
        assert migrate_to_head(db, 'company', tmp_path / 'backup') == ('co0009', 'co0010')
        after = _rows(db.raw)
        assert set(after) - set(before) == set(NEW)
        assert {k: after[k] for k in before} == before
        remaining = [o for o in _normalized_schema(db.raw) if o['table'] not in NEW]
        assert_scope_only_schema_change(objects, remaining)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert migrate_to_head(db, 'company', None) == ('co0010', 'co0010')
    backup, = (tmp_path / 'backup').glob('*-from-co0009.db')
    assert current_revision_raw(backup) == 'co0009'
    with open_database(backup, writable=False) as db:
        assert _rows(db.raw) == before


def test_fresh_frozen_ddl_matches_metadata_and_ignores_future_metadata(old, tmp_path, monkeypatch):
    fresh = tmp_path / 'fresh.db'
    with open_database(fresh, writable=True, create=True) as db:
        migrate_to_head(db, 'company', None)
        fresh_objects = _normalized_schema(db.raw)
        for name in NEW + CHANGED:
            actual = sa.Table(name, sa.MetaData(), autoload_with=db.conn)
            expected = schema.metadata.tables[name]
            assert [(c.name,str(c.type),c.nullable,c.primary_key) for c in actual.c] == [
                (c.name,str(c.type),c.nullable,c.primary_key) for c in expected.c]
            def fks(t):
                return {(tuple(c.column_keys),tuple(e.target_fullname for e in c.elements),c.deferrable,c.initially)
                        for c in t.foreign_key_constraints}
            assert fks(actual) == fks(expected)
            for cls, key in [(sa.CheckConstraint, lambda c:str(c.sqltext)),
                             (sa.UniqueConstraint, lambda c:tuple(c.columns.keys()))]:
                assert {key(c) for c in actual.constraints if isinstance(c,cls)} == {
                    key(c) for c in expected.constraints if isinstance(c,cls)}
            assert {i.name for i in actual.indexes} == {i.name for i in expected.indexes}
    source = inspect.getsource(importlib.import_module(MODULE))
    tree = ast.parse(source)
    ddl = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id == 'DDL' for t in n.targets))
    assert all(isinstance(x,str) for x in ddl)
    assert 'bookflow.company' not in source
    monkeypatch.setattr(schema, 'metadata', sa.MetaData())
    for name in NEW + CHANGED:
        monkeypatch.setattr(schema, name, None)
    with open_database(old, writable=True) as db:
        migrate_to_head(db, 'company', None)
        assert [o for o in _normalized_schema(db.raw) if o['table'] in NEW + CHANGED] == [
            o for o in fresh_objects if o['table'] in NEW + CHANGED]


@pytest.mark.parametrize('backups', [False, True])
def test_failed_migration_rolls_back_all_ddl_and_rows(old, tmp_path, monkeypatch, backups):
    with open_database(old, writable=True) as db:
        db.raw.execute("CREATE TRIGGER fail_work_migration BEFORE UPDATE ON alembic_version WHEN NEW.version_num='co0010' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        before, objects = _rows(db.raw), _normalized_schema(db.raw)
        with pytest.raises(BookflowError) as caught:
            migrate_to_head(db, 'company', tmp_path / 'backup' if backups else None)
        assert caught.value.code == 'E_MIGRATION_FAILED'
        assert _rows(db.raw) == before
        assert _normalized_schema(db.raw) == objects
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)
    assert current_revision_raw(old) == 'co0009'


def test_deferred_current_owner_and_self_roots(db):
    a, ar = document(db)
    b, br = document(db)
    identity(db, a)  # a self-reference is valid in its birth INSERT
    db.raw.execute('BEGIN')
    db.raw.execute('UPDATE work_documents SET current_revision_id=? WHERE id=?', (br,a))
    with pytest.raises(sqlite3.IntegrityError):
        db.raw.execute('COMMIT')
    db.raw.execute('ROLLBACK')
    assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []


def test_revision_line_and_acceptance_foreign_owner_rejected(db):
    a, ar = document(db)
    b, br = document(db)
    lid = identity(db, a)
    with pytest.raises(sa.exc.IntegrityError):
        line(db,b,br,lid)
    with pytest.raises(sa.exc.IntegrityError):
        line(db,a,br,lid)
    with pytest.raises(sa.exc.IntegrityError):
        revision(db,a,number=2,supersedes_revision_id=br)
    with pytest.raises(sa.exc.IntegrityError):
        revision(db,a,number=2,status='accepted',accepted_revision_id=br)


def test_estimate_group_ownership_and_acceptance_uniqueness(db):
    proposal = document(db)
    a = document(db,'estimate',group=proposal[0],status='accepted')
    b = document(db,'estimate',group=proposal[0])
    independent = document(db,'estimate')
    document(db,'estimate',group=independent[0])
    for group in (a[0], document(db,'work_order')[0], new_id()):
        with pytest.raises(sa.exc.IntegrityError):
            document(db,'estimate',group=group)
        db.raw.execute('ROLLBACK')
    with pytest.raises(sqlite3.IntegrityError):
        db.raw.execute("UPDATE work_documents SET status='accepted' WHERE id=?",(b[0],))
    with pytest.raises(sqlite3.IntegrityError):
        db.raw.execute('UPDATE work_documents SET estimate_group_id=id WHERE id=?',(b[0],))
    db.raw.execute("UPDATE work_documents SET status='superseded' WHERE id=?",(a[0],))
    db.raw.execute("UPDATE work_documents SET status='accepted' WHERE id=?",(b[0],))


def test_roots_sources_and_conversion_cardinality(db):
    proposal = document(db)
    pl, pi = line(db,*proposal)
    estimate = document(db,'estimate',group=proposal[0],status='accepted')
    ei = identity(db,estimate[0],source=pl)
    el, _ = line(db,*estimate,ei)
    link(db,proposal,estimate,'proposal_estimate')
    order = document(db,'work_order')
    oi = identity(db,order[0],source=el,root=(estimate[0],ei))
    line(db,*order,oi)
    key = 'a'*64
    link(db,estimate,order,'estimate_work_order',conversion_key_hash=key)
    another = document(db,'work_order')
    with pytest.raises(sa.exc.IntegrityError):
        link(db,estimate,another,'estimate_work_order')
    with pytest.raises(sa.exc.IntegrityError):
        link(db,proposal,document(db,'estimate',group=proposal[0]),'proposal_estimate',conversion_key_hash=key)
    with pytest.raises(sa.exc.IntegrityError):
        link(db,proposal,estimate,'proposal_estimate')
    with pytest.raises(sa.exc.IntegrityError):
        identity(db,proposal[0],source=el,root=(estimate[0],ei))
    with pytest.raises(sa.exc.IntegrityError):
        identity(db,order[0],source=pl,root=(proposal[0],pi))
    with pytest.raises(sa.exc.IntegrityError):
        identity(db,order[0],source=new_id())
    assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('table', NEW[1:])
@pytest.mark.parametrize('operation', ['UPDATE','DELETE'])
def test_history_is_rejection_only_immutable(db, table, operation):
    source = document(db)
    line(db,*source)
    link(db,source,document(db))
    before = db.raw.execute(f'SELECT * FROM {table}').fetchall()
    assert before
    sql = f'UPDATE {table} SET id=id' if operation == 'UPDATE' else f'DELETE FROM {table}'
    with pytest.raises(sqlite3.IntegrityError, match='immutable work history'):
        db.raw.execute(sql)
    assert db.raw.execute(f'SELECT * FROM {table}').fetchall() == before


@pytest.mark.parametrize('changes', [dict(quantity_microunits=1.5),dict(net_minor_units=-1),
    dict(gross_minor_units=1),dict(completed_quantity_microunits=1),dict(pricing_basis='amount'),
    dict(pricing_basis='markup'),dict(estimated_cost_minor_units=1),dict(item_id='missing'),dict(unit_id='missing')])
def test_exact_line_constraints(db,changes):
    d,r = document(db)
    with pytest.raises(sa.exc.IntegrityError):
        line(db,d,r,**changes)


def test_line_position_identity_and_link_owner_constraints(db):
    a,ar = document(db)
    b,br = document(db)
    _, lid = line(db,a,ar)
    with pytest.raises(sa.exc.IntegrityError):
        line(db,a,ar,position=1)
    with pytest.raises(sa.exc.IntegrityError):
        line(db,a,ar,lid,position=2)
    with pytest.raises(sa.exc.IntegrityError):
        link(db,(a,br),(b,br))
    with pytest.raises(sa.exc.IntegrityError):
        link(db,(a,ar),(b,ar))
    with pytest.raises(sa.exc.IntegrityError):
        link(db,(a,ar),document(db,'work_order'),'proposal_estimate')


def test_saved_work_units_guard_and_annotation_targets(db):
    d,r = document(db)
    uid = new_id()
    db.conn.execute(schema.units_of_measure.insert().values(**COMMON,id=uid,name='Hours',name_key='hours',active=True))
    conversion = new_id()
    db.conn.execute(schema.unit_conversions.insert().values(id=conversion,unit_of_measure_id=uid,
        position=1,active=True,name='Hour',name_key='hour',abbreviation='hr',abbreviation_key='hr',
        is_base=True,base_factor_nanounits=1_000_000_000))
    l,_ = line(db,d,r,unit_id=conversion)
    before=[dict(id=conversion,base_factor_nanounits=1_000_000_000)]
    units.validate_factor_changes(db.conn,before,before)
    with pytest.raises(BookflowError) as caught:
        units.validate_factor_changes(db.conn,before,[dict(id=conversion,base_factor_nanounits=2_000_000_000)])
    assert caught.value.code == 'E_ACTIVE_DEPENDENTS'
    assert caught.value.details['dependents'] == [dict(record_type='work_lines',count=1)]
    for typ,ident in [('work_document',d),('work_revision',r),('work_line',l)]:
        assert records.resolve(SimpleNamespace(company=db),typ,ident) == ident
        with pytest.raises(BookflowError):
            records.resolve(SimpleNamespace(company=db),typ,new_id())


def test_hub_capabilities_additive_and_fresh(tmp_path):
    path=tmp_path/'hub.db'
    _make_revision(path,'hub','hub0010',lambda _:None)
    with open_database(path,writable=True) as db:
        before=_rows(db.raw)
        migrate_to_head(db,'hub',None)
        after=_rows(db.raw)
        for name in before:
            if name != 'role_capabilities':
                assert after[name] == before[name]
        new=set(after['role_capabilities'][1])-set(before['role_capabilities'][1])
        assert new == {(role,'customer-work','member') for role in ('owner','admin','hub_admin','standard','readonly')}
    with open_database(tmp_path/'fresh-hub.db',writable=True,create=True) as db:
        migrate_to_head(db,'hub',None)
        assert _rows(db.raw) == after


@pytest.mark.parametrize('failure', [None, 'drop', 'restore', 'foreign_key'])
@pytest.mark.parametrize('backups', [False, True])
def test_scope_rebuild_preserves_local_schema_and_rolls_back(old, tmp_path, failure, backups):
    with open_database(old, writable=True) as db:
        db.raw.execute('CREATE VIEW z_local_scopes AS SELECT id, record_type FROM custom_field_scopes')
        db.raw.execute('CREATE VIEW "a local ""scopes" AS SELECT * FROM z_local_scopes')
        db.raw.execute('CREATE INDEX "local scope index" ON custom_field_scopes(record_type) WHERE active=1')
        db.raw.execute('''CREATE TRIGGER local_scope_guard BEFORE UPDATE ON custom_field_scopes
            WHEN NEW.position=9876 BEGIN SELECT RAISE(ABORT, 'local scope guard'); END''')
        db.raw.execute('''CREATE TRIGGER local_external_guard AFTER UPDATE ON accounts
            WHEN NEW.name='local-blocked' BEGIN SELECT record_type FROM "a local ""scopes";
            SELECT RAISE(ABORT, 'local external guard'); END''')
        db.raw.execute('''CREATE TRIGGER local_view_guard INSTEAD OF UPDATE ON "a local ""scopes"
            BEGIN SELECT RAISE(ABORT, 'local view guard'); END''')
        if failure == 'foreign_key':
            db.raw.execute('PRAGMA foreign_keys=OFF')
            db.raw.execute("INSERT INTO custom_field_scopes SELECT 'orphan', 'missing', position, active, record_type, 'Orphan', 'orphan', definition_active FROM custom_field_scopes LIMIT 1")
            db.raw.execute('PRAGMA foreign_keys=ON')
        before, objects = _rows(db.raw), _normalized_schema(db.raw)
        view_rows = db.raw.execute('SELECT * FROM "a local ""scopes" ORDER BY id').fetchall()
        retained = db.raw.execute("SELECT name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND type IN ('view','trigger','index') ORDER BY name").fetchall()
        denied = []
        def interrupt(action, name, *args):
            if not denied and ((failure == 'drop' and action == sqlite3.SQLITE_DROP_TABLE and name == 'custom_field_scopes') or
                (failure == 'restore' and action == sqlite3.SQLITE_CREATE_VIEW and name == 'a local "scopes')):
                denied.append(name)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.raw.set_authorizer(interrupt)
        try:
            if failure:
                with pytest.raises(BookflowError) as caught:
                    migrate_to_head(db,'company',tmp_path/'backups' if backups else None)
                assert caught.value.code == 'E_MIGRATION_FAILED'
                assert _rows(db.raw) == before
                assert _normalized_schema(db.raw) == objects
                assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone() == ('co0009',)
                if failure in ('drop','restore'):
                    assert len(denied) == 1
            else:
                assert migrate_to_head(db,'company',tmp_path/'backups' if backups else None) == ('co0009','co0010')
                after = _rows(db.raw)
                assert {k:after[k] for k in before} == before
                assert_scope_only_schema_change(objects, [o for o in _normalized_schema(db.raw) if o['table'] not in NEW])
        finally:
            db.raw.set_authorizer(None)
        for name,sql in retained:
            assert db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone() == (sql,)
        assert db.raw.execute('SELECT * FROM "a local ""scopes" ORDER BY id').fetchall() == view_rows
        with pytest.raises(sqlite3.IntegrityError,match='local scope guard'):
            db.raw.execute('UPDATE custom_field_scopes SET position=9876')
        with pytest.raises(sqlite3.IntegrityError,match='local external guard'):
            db.raw.execute("UPDATE accounts SET name='local-blocked' WHERE id='bank'")
        with pytest.raises(sqlite3.IntegrityError,match='local view guard'):
            db.raw.execute('UPDATE "a local ""scopes" SET record_type=record_type')
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        foreign_keys = db.raw.execute('PRAGMA foreign_key_check').fetchall()
        if failure == 'foreign_key':
            assert len(foreign_keys) == 1  # the planted old orphan, restored unchanged
        else:
            assert foreign_keys == []
