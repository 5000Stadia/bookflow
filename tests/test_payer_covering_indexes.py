"""co18→co19 complete admission, preserving rollback and index maintenance."""
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
import sqlalchemy as sa
from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects.sqlite import dialect

from bookflow.company import schema
from bookflow import BookflowError
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS, migrate_to_head
from tests.payment_raw_evidence import database, attachments
from tests import provenance

M = importlib.import_module('bookflow.storage.company_migrations.versions.0019_payment_payer_covering_indexes')
BASE = '296f57352aae5887c49b7eb294c7a7fc2e00f9bd'


def snapshot(raw):
    result = {}
    for ns in ('main', 'temp'):
        ddl = raw.execute(f'SELECT type,name,tbl_name,sql FROM {ns}.sqlite_schema ORDER BY type,name').fetchall()
        tables = {}
        for kind, name, _, _ in ddl:
            if kind != 'table':
                continue
            q = '"' + name.replace('"', '""') + '"'
            columns = raw.execute(f'PRAGMA {ns}.table_xinfo({q})').fetchall()
            expressions = ['rowid']
            for col in columns:
                key = '"' + col[1].replace('"', '""') + '"'
                expressions.extend((f'typeof({key})', key, f'CAST({key} AS BLOB)'))
            tables[name] = (columns, raw.execute(f'SELECT {",".join(expressions)} FROM {ns}.{q} ORDER BY rowid').fetchall())
        result[ns] = (ddl, tables)
    return result


def shape(db, change=None):
    db.raw.execute('CREATE TABLE alembic_version(version_num TEXT)')
    db.raw.execute("INSERT INTO alembic_version VALUES ('co0018')")
    for _, name, keys in M.INDEXES:
        cols = []
        for key in keys:
            if name == 'posting_lines' and key == 'transaction_id':
                if change == 'missing':
                    continue
                if change == 'generated':
                    cols.append(key + " TEXT GENERATED ALWAYS AS ('x') VIRTUAL")
                    continue
            kind = 'VARCHAR(26)'
            if name == 'posting_lines' and key == 'name_type':
                kind = {'affinity': 'BLOB', 'int_affinity': 'CHARINT', 'collation': 'TEXT COLLATE NOCASE'}.get(change, kind)
            cols.append(key + ' ' + kind)
        local = ", local_blob BLOB DEFAULT X'0080FF', local_length INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL"
        if name == 'posting_lines' and change == 'function':
            db.raw.create_function('private_length', 1, len, deterministic=True)
            local += ', private_value INTEGER GENERATED ALWAYS AS (private_length(local_blob)) VIRTUAL'
        if name == 'posting_lines' and change == 'custom_collation':
            db.raw.create_collation('PRIVATE_ORDER', lambda a, b: (a > b) - (a < b))
            local += ', private_text TEXT COLLATE PRIVATE_ORDER'
        db.raw.execute('CREATE TABLE ' + name + '(' + ','.join(cols) + local + ')')
    db.raw.execute('CREATE TEMP TABLE local_temp(value BLOB)')
    db.raw.execute("INSERT INTO local_temp VALUES (X'0080FF')")


@pytest.mark.parametrize('change,message', [
    ('missing','incompatible managed column'), ('generated','incompatible managed column'),
    ('affinity','incompatible managed column'), ('int_affinity','incompatible managed column'),
    ('collation','incompatible index collation'), ('function','unsupported local DDL'),
    ('custom_collation','unsupported local DDL'), ('view','missing managed table'),
    ('temp_shadow','temporary managed target shadow')])
def test_complete_preflight_rejection_preserves_main_temp_and_head(tmp_path, change, message):
    with open_database(tmp_path/'shape.db', writable=True, create=True) as db:
        shape(db, change)
        if change == 'view':
            db.raw.execute('DROP TABLE posting_lines')
            db.raw.execute('CREATE VIEW posting_lines AS SELECT 1 AS name_type')
        if change == 'temp_shadow':
            db.raw.execute('CREATE TEMP TABLE posting_lines(value TEXT)')
            db.raw.execute("INSERT INTO temp.posting_lines VALUES ('preserve')")
        before = snapshot(db.raw)
        with pytest.raises(BookflowError if message=='unsupported local DDL' else RuntimeError) as caught:
            M._preflight(db.conn)
        if message=='unsupported local DDL':
            assert caught.value.code=='E_MIGRATION_FAILED'
            assert caught.value.details==dict(chain='company',**{'from':'co0018','to':'co0019','cause':'unsupported_local_ddl'})
        else:
            assert str(caught.value) == 'co0019 ' + message
        assert snapshot(db.raw) == before
        for name, _, _ in M.INDEXES:
            assert not db.raw.execute('SELECT 1 FROM main.sqlite_schema WHERE name=?', (name,)).fetchall()


@pytest.mark.parametrize('ns', ['main', 'temp'])
@pytest.mark.parametrize('kind', ['table','view','index','trigger'])
@pytest.mark.parametrize('reservation', [0,1])
def test_all_reserved_names_reject_before_ddl(tmp_path, ns, kind, reservation):
    with open_database(tmp_path/'shape.db', writable=True, create=True) as db:
        shape(db)
        name = M.INDEXES[reservation][0].upper()
        if ns == 'temp':
            db.raw.execute('CREATE TEMP TABLE target(value TEXT)')
        else:
            db.raw.execute('CREATE TABLE target(value TEXT)')
        sql = {'table': f'CREATE TABLE {ns}.{name}(value TEXT)',
               'view': f'CREATE VIEW {ns}.{name} AS SELECT 1',
               'index': f'CREATE INDEX {ns}.{name} ON target(value)',
               'trigger': f'CREATE TRIGGER {ns}.{name} BEFORE INSERT ON target BEGIN SELECT 1; END'}[kind]
        db.raw.execute(sql)
        before = snapshot(db.raw)
        with pytest.raises(RuntimeError, match='reserved index name'):
            M._preflight(db.conn)
        assert snapshot(db.raw) == before


def test_supported_complete_local_ddl_and_exact_metadata(tmp_path):
    with open_database(tmp_path/'shape.db', writable=True, create=True) as db:
        shape(db)
        db.raw.execute("INSERT INTO posting_lines(name_type,name_id,account_id,transaction_id) VALUES ('customer','p','ar','t')")
        db.raw.execute("INSERT INTO applications(paying_transaction_id,paid_transaction_id) VALUES ('t','x')")
        db.raw.execute('CREATE VIEW local_view AS SELECT * FROM posting_lines')
        db.raw.execute('CREATE INDEX local_index ON posting_lines(local_blob)')
        db.raw.execute("CREATE TRIGGER local_guard BEFORE UPDATE OF local_blob ON posting_lines BEGIN SELECT RAISE(ABORT,'local guard'); END")
        before = snapshot(db.raw)
        M._preflight(db.conn)
        assert snapshot(db.raw) == before
        for statement in M.DDL:
            db.raw.execute(statement)
        after = snapshot(db.raw)
        assert after['temp'] == before['temp'] and after['main'][1] == before['main'][1]
        assert [r for r in after['main'][0] if not r[1].startswith('ix_co19_')] == before['main'][0]
        for name, table, keys in M.INDEXES:
            x = db.raw.execute(f'PRAGMA main.index_xinfo("{name}")').fetchall()
            assert [(r[2],r[3],r[4]) for r in x if r[5]] == [(k,0,'BINARY') for k in keys]
            entry = next(r for r in db.raw.execute(f'PRAGMA main.index_list("{table}")') if r[1] == name)
            assert entry[2:] == (0,'c',0)
            index = next(i for i in schema.metadata.tables[table].indexes if i.name == name)
            assert [c.name for c in index.columns] == list(keys) and not index.unique
            compiled = str(CreateIndex(index).compile(dialect=dialect()))
            assert compiled == 'CREATE INDEX ' + name + ' ON ' + table + ' (' + ', '.join(keys) + ')'


@pytest.fixture(scope='module')
def co18(tmp_path_factory):
    folder = tmp_path_factory.mktemp('co18-covering')
    source = folder/'source';source.mkdir()
    # The source and its own tests, so a child writing old data imports helpers of its own day.
    archive = subprocess.check_output(['git','archive',BASE,'src','tests'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    root = folder/'root'
    code = 'import bookflow,sys,io;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset();co=c.company.list()["items"][0]["company_id"];p=c.customer.create(name="Co19 attachment",company=co);c.attachment.add(record_type="customer",record_id=p["id"],original_filename="index.bin",input_stream=io.BytesIO(bytes([0,128,255])),company=co)'
    result = subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,
        env=provenance.child_env(str(source/'src'), BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return root


@pytest.fixture
def at_co19(monkeypatch):
    """The runner stopped at this migration: a claim about co0019 is made at co0019.

    Carried to today's head, every later migration's legitimate rewrite would read as this
    one failing to preserve something; the whole chain has its own retention witness
    (test_deposit_coordinate_migration.test_whole_chain_upgrade_to_head_preserves_customer_file).
    """
    monkeypatch.setitem(HEADS, 'company', M.revision)


@pytest.mark.parametrize('statistics',[False, True])
def test_revision_only_all_rows_local_objects_backup_rollback_retry(co18,tmp_path,statistics,at_co19):
    path = tmp_path/'company.db'
    shutil.copyfile(next(co18.glob('organizations/*/Demo Plumbing Co/company.db')), path)
    with sqlite3.connect(path) as raw:
        for _, table, _ in M.INDEXES:
            raw.execute(f"ALTER TABLE {table} ADD COLUMN local_blob BLOB DEFAULT X'0080FF'")
            raw.execute(f'ALTER TABLE {table} ADD COLUMN local_generated INTEGER GENERATED ALWAYS AS (length(local_blob)) VIRTUAL')
            raw.execute(f'CREATE VIEW local_{table} AS SELECT * FROM {table}')
        if statistics:
            raw.execute('ANALYZE')  # PRE-upgrade local-statistics preservation witness only.
    before = database(path)
    with open_database(path,writable=True) as db:
        def fail(conn,cursor,statement,parameters,context,many):
            if statement == M.DDL[1]:
                raise RuntimeError('second index failure witness')
        sa.event.listen(db.conn,'before_cursor_execute',fail)
        with pytest.raises(Exception) as caught:
            migrate_to_head(db,'company',tmp_path/'backups')
        assert caught.value.code == 'E_MIGRATION_FAILED'
        sa.event.remove(db.conn,'before_cursor_execute',fail)
        assert database(path) == before
        backup = next((tmp_path/'backups').glob('*from-co0018.db'))
        assert database(backup) == before
        assert migrate_to_head(db,'company',tmp_path/'backups') == ('co0018','co0019')
        after = database(path)
        assert {k:v for k,v in after['tables'].items() if k != 'alembic_version'} == {k:v for k,v in before['tables'].items() if k != 'alembic_version'}
        assert [r for r in after['ddl'] if not r[1].startswith('ix_co19_')] == before['ddl']
        assert migrate_to_head(db,'company',tmp_path/'backups') == ('co0019','co0019')
        assert database(path) == after
        assert db.raw.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    (tmp_path/'revision-preservation.json').write_text(json.dumps({'before':before,'after':after,'backup':str(backup)},indent=2))


def test_fresh_chain_and_old_index_metadata(tmp_path,at_co19):
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None) == (None,'co0019')
        names = {r[0] for r in db.raw.execute("SELECT name FROM main.sqlite_schema WHERE type='index' AND name LIKE 'ix_co%' ")}
        assert len([n for n in names if n.startswith('ix_co17_')]) == 10
        assert {n for n in names if n.startswith('ix_co19_')} == {n for n,_,_ in M.INDEXES}
        assert not db.raw.execute("SELECT name FROM main.sqlite_schema WHERE name LIKE 'sqlite_stat%'").fetchall()
        assert db.raw.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def source_call(source, root, code, *args):
    result = subprocess.run([sys.executable,'-c',code,str(root),*map(str,args)],cwd=source,
        env=provenance.child_env(str(source/'src'), BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def restricted(raw, name, columns):
    """Rows of `name` read through only the columns an earlier snapshot() saw, in its layout."""
    expressions = ['rowid']
    for col in columns:
        key = '"' + col[1].replace('"', '""') + '"'
        expressions.extend((f'typeof({key})', key, f'CAST({key} AS BLOB)'))
    q = '"' + name.replace('"', '""') + '"'
    return raw.execute(f'SELECT {",".join(expressions)} FROM main.{q} ORDER BY rowid').fetchall()


def test_public_upgrade_exact_rows_publication_and_cursors(co18,tmp_path):
    """A co0018 customer root, written by the source of its day, upgraded by today's `upgrade`.

    The public command runs the whole chain to today's head, so the claim is retention, not
    stasis: every value stored at co0018 is still stored, read through the columns that existed
    then; the migration is attributed to the system identity; and the old query recipes still
    enumerate the same receipts, with a continuation minted before the upgrade refused.
    """
    from bookflow.core.audit import decode_snapshot
    import bookflow
    head = HEADS['company']
    root = tmp_path/'root';shutil.copytree(co18,root)
    source = co18.parent/'source';receipt = tmp_path/'old-pages.json'
    code = """import bookflow,sys,json
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_payment_receipts import posted,method
c=bookflow.connect(data_root=sys.argv[1]);s=sale.__wrapped__(c);pm=method(c)
for i in range(3):
 posted(c,s['customer'],s['item'],'10',f'CO19-{i}')
 c.run('payment receive',dict(customer=s['customer'],date='2026-06-02',amount='1',payment_method=pm,operation_key=f'co19-{i}',applications=dict(mode='inline',items=[])),company=COMPANY)
co=c.company.show(company=COMPANY)['company_id']
requests=[('payment query',dict(customer=s['customer'])),('payment invoices',dict(mode='new_receipt',customer=s['customer'],date='2026-06-02'))]
rows=[]
for command,args in requests:
 for limit in (1,25,200):
  inp=dict(args,limit=limit);page=c.run(command,inp,company=co);pages=[page]
  while page['next_cursor']:
   page=c.run(command,dict(inp,cursor=page['next_cursor']),company=co);pages.append(page)
  rows.append(dict(command=command,input=inp,pages=pages))
 rows.append(dict(command=command,input=dict(args,q='no-co19-results'),pages=[c.run(command,dict(args,q='no-co19-results'),company=co)]))
json.dump(dict(company=co,rows=rows),open(sys.argv[2],'w'))
"""
    source_call(source,root,code,receipt)
    old = json.loads(receipt.read_text());co = old['company']
    path = next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        before = snapshot(raw)['main']
        seq = raw.execute('SELECT max(seq) FROM audit_events').fetchone()[0]
    old_files = attachments(root)
    with sqlite3.connect(root/'hub.db') as raw:
        hub_before_rows=snapshot(raw)['main']
        hub_before_revision=raw.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    # The old root was built by BASE's source; the same call brings its hub to head too.
    hub_seed=importlib.import_module('bookflow.storage.hub_migrations.versions.0013_identity_capabilities').ROLE_CAPABILITY_SEED
    hub_moved=hub_before_revision!=HEADS['hub']
    with sqlite3.connect(root/'hub.db') as raw:
        raw.row_factory = sqlite3.Row
        company_before = dict(raw.execute('SELECT * FROM companies WHERE id=?',(co,)).fetchone())
        system = dict(raw.execute("SELECT * FROM users WHERE kind='system'").fetchone())
    client = bookflow.connect(data_root=str(root))
    result = client.run('upgrade',{},reason='Co19 exact public migration')
    assert result['hub_revision']==HEADS['hub'] and result['hub_migrated']==hub_moved
    assert result['companies_migrated']==[co] and not result['companies_failed']
    with sqlite3.connect(path) as raw:
        after_objects = {(r[0],r[1]) for r in raw.execute('SELECT type,name FROM main.sqlite_schema')}
        after_columns = {name: [c for c in raw.execute(f'PRAGMA main.table_xinfo("{name}")').fetchall()]
                         for name in before[1]}
        after_rows = {name: restricted(raw, name, cols) for name,(cols,_) in before[1].items()}
        assert raw.execute('SELECT max(seq) FROM audit_events').fetchone()[0] == seq+1
        raw.row_factory = sqlite3.Row
        event = dict(raw.execute('SELECT * FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone())
        assert event['command']=='upgrade' and event['actor_kind']=='system' and event['actor_id']==system['id']
        assert event['summary']==f'migrated from co0018 to {head}'
        entry = dict(raw.execute('SELECT * FROM audit_entries ORDER BY rowid DESC LIMIT 1').fetchone())
        assert entry['record_type']=='company_info' and entry['action']=='migrate'
        assert decode_snapshot(entry['after']) == {'from':'co0018','schema_revision':head}
        assert event['on_behalf_of'] is not None
        permitted = {system['id'],event['on_behalf_of']}
    # Every object stored at co0018 is still there (later migrations may rebuild its text).
    assert {(r[0],r[1]) for r in before[0]} <= after_objects
    for table,(cols,rows) in before[1].items():
        # Every column that existed then still exists, with its type; later columns are new.
        assert [c[1:3] for c in cols] == [c[1:3] for c in after_columns[table] if c[1] in {x[1] for x in cols}], table
        newrows = after_rows[table]
        if table == 'alembic_version':
            continue
        if table=='audit_events':
            assert newrows[:-1]==rows and len(newrows)==len(rows)+1
        elif table=='audit_entries':
            # Every entry the chain adds hangs off the one upgrade event, and exactly one of them
            # is the company_info migrate entry asserted above.
            names=[c[1] for c in cols];event_index=2+3*names.index('event_id')
            assert newrows[:len(rows)]==rows and len(newrows)>len(rows)
            assert {r[event_index] for r in newrows[len(rows):]}=={event['id']}
        elif table == 'principals':
            # Only the two identities actually upserted may change, and only
            # fields written by upsert_principal (storage type and bytes included).
            names=[c[1] for c in cols];id_index=2+3*names.index('user_id')
            allowed={1+3*names.index(k)+j for k in ('username','display_name','kind','last_seen_at') for j in range(3)}
            old_ids={r[id_index] for r in rows}
            additions=[r for r in newrows if r[id_index] not in old_ids]
            assert len(additions)==int(system['id'] not in old_ids)
            if additions:
                added=additions[0]
                assert {k:added[2+3*names.index(k)] for k in ('user_id','username','display_name','kind')}==dict(user_id=system['id'],username=system['username'],display_name=system['display_name'],kind='system')
                assert added[2+3*names.index('first_seen_at')]==added[2+3*names.index('last_seen_at')]
            for prior,current in zip(rows,newrows):
                if prior[id_index] not in permitted:
                    assert current==prior
                else:
                    assert [v for n,v in enumerate(current) if n not in allowed]==[v for n,v in enumerate(prior) if n not in allowed]
        else:
            assert newrows==rows,table
    with sqlite3.connect(root/'hub.db') as raw:
        raw.row_factory=sqlite3.Row
        company_after=dict(raw.execute('SELECT * FROM companies WHERE id=?',(co,)).fetchone())
        assert company_after==dict(company_before,schema_revision=head)
        raw.row_factory=None
        hub_after_rows=snapshot(raw)['main']
        raw.row_factory=sqlite3.Row
        h_event=dict(raw.execute('SELECT * FROM audit_events ORDER BY seq DESC LIMIT 1').fetchone())
        assert h_event['command']=='upgrade'
        h_entry=dict(raw.execute('SELECT * FROM audit_entries ORDER BY rowid DESC LIMIT 1').fetchone())
        assert h_entry['record_id']==co and decode_snapshot(h_entry['after'])=={'from':'co0018','schema_revision':head}
    # The hub's own objects are unchanged; the one hub migration newer than this root's source
    # (h0014, authority generation; hub0015, the audit log's four append-only triggers) adds
    # those tables and triggers and nothing else.
    added=[x for x in hub_after_rows[0] if x not in hub_before_rows[0]]
    assert [x for x in hub_after_rows[0] if x in hub_before_rows[0]]==hub_before_rows[0]
    assert added and all(x[1].startswith(('authority_generation','audit_events_no_','audit_entries_no_')) for x in added),added
    event_cols=[c[1] for c in hub_before_rows[1]['audit_events'][0]]
    new_events=hub_after_rows[1]['audit_events'][1][len(hub_before_rows[1]['audit_events'][1]):]
    upgrade_events={r[2+3*event_cols.index('id')] for r in new_events}
    # Only the upgrade writes to the hub's audit: every new event is an `upgrade`, and every
    # new entry hangs off one of them (the hub's own step and the company's migrate).
    assert new_events and {r[2+3*event_cols.index('command')] for r in new_events}=={'upgrade'}
    for table,(cols,rows) in hub_before_rows[1].items():
        newcols,newrows=hub_after_rows[1][table];assert cols==newcols
        if table=='audit_events':
            assert newrows[:len(rows)]==rows
        elif table=='audit_entries':
            names=[c[1] for c in cols]
            assert newrows[:len(rows)]==rows and len(newrows)>len(rows)
            assert {r[2+3*names.index('event_id')] for r in newrows[len(rows):]}<=upgrade_events
        elif hub_moved and table=='alembic_version':
            assert len(newrows)==len(rows)
        elif hub_moved and table=='role_capabilities':
            # hub0013 appends its seed; every row already there is untouched.
            assert newrows[:len(rows)]==rows and len(newrows)==len(rows)+len(hub_seed)
        elif table=='companies':
            names=[c[1] for c in cols];identifier=2+3*names.index('id')
            allowed={1+3*names.index('schema_revision')+i for i in range(3)}
            assert len(newrows)==len(rows)
            for prior,current in zip(rows,newrows):
                if prior[identifier]==co:
                    assert [v for i,v in enumerate(prior) if i not in allowed]==[v for i,v in enumerate(current) if i not in allowed]
                else:assert current==prior
        else:assert newrows==rows,table
    from bookflow.storage.paths import read_company_marker
    marker_bytes=(path.parent/'bookflow-company.toml').read_bytes()
    marker=read_company_marker(path.parent)
    assert marker==dict(company_id=co,state='ready',display_name=company_before['display_name'],schema_revision=head)
    assert attachments(root)==old_files and old_files
    backup=next((path.parent/'backups').glob('*from-co0018.db'))
    with sqlite3.connect(backup) as raw:
        assert snapshot(raw)['main']==before
    read_before=database(path)
    pages=[]
    # The old recipes still enumerate exactly the old receipts and invoices; every field the
    # old pages carried reads the same (later releases add fields beside them); and a
    # continuation minted before the upgrade is refused, never silently resumed.
    for row in old['rows']:
        command,inp=row['command'],row['input'];current=[];cursor=None
        while True:
            page=client.run(command,dict(inp,**({'cursor':cursor} if cursor else {})),company=co)
            current.append(page);cursor=page['next_cursor']
            if cursor is None:break
        assert sum(len(p['items']) for p in current)==(0 if inp.get('q') else 3)
        previous_items=[item for page in row['pages'] for item in page['items']]
        current_items=[item for page in current for item in page['items']]
        assert len(previous_items)==len(current_items)
        for was,now in zip(previous_items,current_items):
            assert {k:now.get(k) for k in was}==was, (command,was,now)
        # An ordinary query pins the company's audit, so the upgrade's own event stales its
        # continuations. Payment preparation (`payment invoices`) pins only the facts it
        # lists, which the upgrade did not change, so its continuations still resume.
        for previous in row['pages']:
            if previous['next_cursor'] and command!='payment invoices':
                with pytest.raises(bookflow.BookflowError) as caught:
                    client.run(command,dict(inp,cursor=previous['next_cursor']),company=co)
                assert caught.value.code=='E_QUERY_STALE'
        pages.append(dict(before=row,after=current))
    assert database(path)==read_before and attachments(root)==old_files
    noop_before=database(root/'hub.db')
    again=client.run('upgrade',{},reason='Co19 no-op witness')
    assert again['companies_migrated']==[] and again['companies_skipped']==[co]
    assert database(path)==read_before and database(root/'hub.db')==noop_before
    (tmp_path/'public-upgrade.json').write_text(json.dumps(dict(result=result,noop=again,company_event=event,company_entry=entry,
        hub_event=h_event,hub_entry=h_entry,pages=pages,raw_before=read_before,attachments=old_files),indent=2,default=repr))


@pytest.mark.parametrize('identity',['present','new_company_copy','absent_hub_identity'])
def test_company_migration_system_identity_seams(co18,tmp_path,monkeypatch,identity,at_co19):
    from types import SimpleNamespace
    from bookflow.core.commit_hooks import CommitHooks
    from bookflow.storage.migrate import migrate_company
    from bookflow.core.context import Context, Interface
    from bookflow.core.audit import decode_snapshot
    path=tmp_path/'company.db';shutil.copyfile(next(co18.glob('organizations/*/Demo Plumbing Co/company.db')),path)
    with open_database(path,writable=True) as db:
        with sqlite3.connect(co18/'hub.db') as hub:
            hub.row_factory=sqlite3.Row
            user=dict(hub.execute("SELECT * FROM users WHERE kind='system'").fetchone())
        system={k:user[k] for k in ('id','username','display_name','kind')}
        if identity=='present':
            from bookflow.company.info import upsert_principal
            db.raw.execute('BEGIN IMMEDIATE')
            upsert_principal(db,user_id=system['id'],username=system['username'],display_name=system['display_name'],kind='system')
            db.raw.execute('COMMIT')
        before=snapshot(db.raw)['main']
        if identity=='absent_hub_identity':system=None
        monkeypatch.setattr('bookflow.hub.users.find_user',lambda s,kind:system)
        session=SimpleNamespace(hub=SimpleNamespace(write_transaction=False),company=None,actor=None,hub_touched=[],commits=CommitHooks())
        assert migrate_company(session,Context.new(Interface.python,'co19-system-witness'),db,tmp_path,None)==('co0018','co0019')
        event=db.conn.execute(sa.select(schema.audit_events).order_by(schema.audit_events.c.seq.desc())).mappings().first()
        assert event['actor_id']==(system['id'] if system else None) and event['actor_kind']=='system' and event['on_behalf_of'] is None
        entry=db.raw.execute('SELECT "after" FROM audit_entries ORDER BY rowid DESC LIMIT 1').fetchone()
        assert decode_snapshot(entry[0])=={'from':'co0018','schema_revision':'co0019'}
        after=snapshot(db.raw)['main']
        names=[c[1] for c in before[1]['principals'][0]]
        prior=before[1]['principals'][1];current=after[1]['principals'][1]
        assert len(current)==len(prior)+(identity=='new_company_copy')
        uid=2+3*names.index('user_id');last={1+3*names.index('last_seen_at')+i for i in range(3)}
        for a,b in zip(prior,current):
            if identity=='present' and a[uid]==system['id']:
                assert [v for i,v in enumerate(a) if i not in last]==[v for i,v in enumerate(b) if i not in last]
            else:assert a==b
        if identity=='new_company_copy':
            new=dict(db.conn.execute(sa.select(schema.principals).where(schema.principals.c.user_id==system['id'])).mappings().one())
            assert {k:new[k] for k in ('user_id','username','display_name','kind')}==dict(user_id=system['id'],username=system['username'],display_name=system['display_name'],kind='system')
            assert new['first_seen_at']==new['last_seen_at']
        for table,record in before[1].items():
            if table in ('principals','alembic_version'):continue
            if table in ('audit_events','audit_entries'):
                assert after[1][table][1][:-1]==record[1]
                assert len(after[1][table][1])==len(record[1])+1
            else:assert after[1][table]==record
        stable=snapshot(db.raw)
        assert migrate_company(session,Context.new(Interface.python,'co19-system-noop'),db,tmp_path,None)==('co0019','co0019')
        assert snapshot(db.raw)==stable
        (tmp_path/'system-publication.json').write_text(json.dumps(dict(identity=identity,system=system,event=dict(event)),indent=2))
