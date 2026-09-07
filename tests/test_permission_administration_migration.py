"""Hub0012 preservation, conditional trigger admission and rollback witnesses."""
import importlib
from pathlib import Path
import json
import hashlib
import pytest
import sqlalchemy as sa
from alembic import command
from bookflow import BookflowError
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config, migrate_to_head
from bookflow.hub import schema as h
from tests.permission_storage_support import create_hub, snapshot, digest

REV=importlib.import_module('bookflow.storage.hub_migrations.versions.0012_permission_administration')


@pytest.mark.parametrize('suspended',[False,True])
def test_full_old_storage_and_custom_objects_survive_inert_upgrade(tmp_path,suspended):
    path=tmp_path/'hub.db';create_hub(path,suspended=suspended,malformed=True)
    config=tmp_path/'config.toml';config.write_bytes(b'custom = "untouched"\n')
    company=tmp_path/'company.db'
    with open_database(company,writable=True,create=True) as db:migrate_to_head(db,'company',None)
    other={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (config,company)}
    with open_database(path,writable=True) as db:
        before=snapshot(db.raw)
        assert migrate_to_head(db,'hub',tmp_path/'backups')==('hub0011','hub0012')
        after=snapshot(db.raw,before['columns'])
        assert {k:v for k,v in before['tables'].items() if k!='alembic_version'}=={k:v for k,v in after['tables'].items() if k!='alembic_version'}
        changed={'memberships','agent_authority'}
        assert {x for x in before['ddl'] if x[1] not in changed} <= set(after['ddl'])
        # Removing exactly the declared new column definitions must recover
        # every original CREATE TABLE definition, including its local columns,
        # constraints and original column order. No old-table DDL exemption.
        for table in changed:
            original=' '.join(next(x[3] for x in before['ddl'] if x[0]=='table' and x[1]==table).split())
            upgraded=' '.join(next(x[3] for x in after['ddl'] if x[0]=='table' and x[1]==table).split())
            added=[column for column in h.metadata.tables[table].c if column.name not in before['columns'][table]]
            for column in added:
                definition=' '.join(str(sa.schema.CreateColumn(column).compile(dialect=db.engine.dialect)).split())
                assert upgraded.count(', '+definition)==1
                upgraded=upgraded.replace(', '+definition,'',1)
            assert upgraded==original
        assert db.raw.execute('SELECT version,updated_at,updated_by,updated_via FROM memberships').fetchall()==[(1,None,None,None)]*4
        assert db.raw.execute('SELECT epoch,version,fresh_context_required,authorized_at,authorized_by,permitted_use_at,fresh_context_ack_at FROM agent_authority ORDER BY agent_user_id').fetchall()==[(9,1,int(suspended),None,None,None,None),(2,1,int(suspended),None,None,None,None)]
        assert db.raw.execute('SELECT * FROM permission_state').fetchall()==[(1,1,'legacy',None,None,None,None,None,None)]
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchone()==('ok',)
        for table in ('memberships','agent_authority'):
            for name in ('version',):
                definition=str(sa.schema.CreateColumn(h.metadata.tables[table].c[name]).compile(dialect=db.engine.dialect))
                ddl=db.raw.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
                assert ' '.join(definition.split()) in ' '.join(ddl.split())
        (tmp_path/'preservation.json').write_text(json.dumps(dict(before=digest(before),after_old_columns=digest(after),old_rows_equal=True,other_files=other),indent=2))
    assert other=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (config,company)}
    with open_database(next((tmp_path/'backups').glob('*.db')),writable=False) as db:assert snapshot(db.raw)==before


@pytest.mark.parametrize('event',['UPDATE','INSERT','DELETE'])
@pytest.mark.parametrize('temporary',[False,True])
@pytest.mark.parametrize('spelling',['agent_authority','AgEnT_AuThOrItY'])
def test_needed_backfill_rejects_any_attached_trigger_before_ddl(tmp_path,temporary,spelling,event):
    path=tmp_path/'hub.db';create_hub(path)
    with open_database(path,writable=True) as db:
        db.raw.execute('CREATE '+('TEMP ' if temporary else '')+'TRIGGER local_authority_counter AFTER '+event+' ON '+spelling+' BEGIN UPDATE local_counter SET value=value+1; END')
        before=snapshot(db.raw);temp_before=db.raw.execute('SELECT * FROM sqlite_temp_master').fetchall()
        with pytest.raises(BookflowError) as caught:migrate_to_head(db,'hub',None if temporary else tmp_path/'backups')
        assert caught.value.code=='E_MIGRATION_FAILED'
        assert caught.value.details['cause']=='unsupported_authority_backfill_trigger'
        assert 'local_authority_counter' not in str(caught.value)
        assert snapshot(db.raw)==before and db.raw.execute('SELECT * FROM sqlite_temp_master').fetchall()==temp_before
        assert db.raw.execute('PRAGMA foreign_keys').fetchone()==(1,)


@pytest.mark.parametrize('attached,suspended',[(True,False),(False,True)])
def test_no_backfill_or_unrelated_trigger_preserves_all_old_data(tmp_path,attached,suspended):
    path=tmp_path/'hub.db';create_hub(path,suspended=suspended)
    with open_database(path,writable=True) as db:
        db.raw.execute('CREATE TRIGGER untouched AFTER UPDATE ON '+('agent_authority' if attached else 'memberships')+' BEGIN UPDATE local_counter SET value=value+1; END')
        before=snapshot(db.raw);statements=[];db.raw.set_trace_callback(statements.append)
        migrate_to_head(db,'hub',None);db.raw.set_trace_callback(None)
        after=snapshot(db.raw,before['columns'])
        assert before['tables']['local_counter']==after['tables']['local_counter']
        assert before['tables']['agent_authority']==after['tables']['agent_authority']
        assert before['tables']['memberships']==after['tables']['memberships']
        if not suspended:assert not any(x.startswith('UPDATE agent_authority') for x in statements)


@pytest.mark.parametrize('step',range(1,17))
def test_every_add_backfill_and_state_step_rolls_back(tmp_path,monkeypatch,step):
    path=tmp_path/'hub.db';create_hub(path)
    with open_database(path,writable=True) as db:
        before=snapshot(db.raw);seen=[]
        def fail(connection,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith(('ALTER TABLE','UPDATE AGENT_AUTHORITY','CREATE TABLE PERMISSION_STATE','INSERT INTO PERMISSION_STATE')):
                seen.append(statement)
                if len(seen)==step:raise RuntimeError('controlled boundary failure')
        sa.event.listen(db.engine,'before_cursor_execute',fail)
        try:
            with pytest.raises(BookflowError) as caught:migrate_to_head(db,'hub',tmp_path/'backups')
        finally:sa.event.remove(db.engine,'before_cursor_execute',fail)
        assert len(seen)==step and caught.value.code=='E_MIGRATION_FAILED'
        assert snapshot(db.raw)==before


def test_fresh_chain_upgrade_physical_parity_and_non_destructive_downgrade(tmp_path):
    first=tmp_path/'fresh.db';old=tmp_path/'old.db'
    for path,target in ((first,'hub0012'),(old,'hub0011')):
        with open_database(path,writable=True,create=True) as db:command.upgrade(_config('hub',db.conn),target)
    with open_database(old,writable=True) as db:migrate_to_head(db,'hub',None)
    with open_database(first,writable=False) as left,open_database(old,writable=False) as right:
        assert snapshot(left.raw)==snapshot(right.raw)
    with pytest.raises(NotImplementedError):REV.downgrade()


def test_pre_head_actor_membership_and_binding_readers_keep_legacy_projection(tmp_path):
    from types import SimpleNamespace
    from bookflow.hub.access import load_memberships
    from bookflow.hub.credentials import _binding
    path=tmp_path/'hub.db';create_hub(path,suspended=False)
    with open_database(path,writable=True) as db:
        before=snapshot(db.raw)
        session=SimpleNamespace(hub=db,actor=SimpleNamespace(id='H'))
        members=load_memberships(session)
        assert [row['id'] for row in members]==['M'] and 'version' not in members[0]
        assert _binding(db,'G','H')==(True,9)
        assert snapshot(db.raw)==before
        migrate_to_head(db,'hub',None)
        assert load_memberships(session)==members and _binding(db,'G','H')==(True,9)


def test_new_constraints_are_physical_and_match_metadata(tmp_path):
    path=tmp_path/'hub.db';create_hub(path,'hub0012')
    with open_database(path,writable=True) as db:
        for table,names in [('memberships',('version',)),('agent_authority',('version','fresh_context_required'))]:
            ddl=db.raw.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
            for name in names:
                definition=str(sa.schema.CreateColumn(h.metadata.tables[table].c[name]).compile(dialect=db.engine.dialect))
                assert ' '.join(definition.split()) in ' '.join(ddl.split())
        expected=str(sa.schema.CreateTable(h.permission_state).compile(dialect=db.engine.dialect)).strip()
        actual=db.raw.execute("SELECT sql FROM sqlite_master WHERE name='permission_state'").fetchone()[0]
        assert ' '.join(expected.split())==' '.join(actual.split())
        for sql in ('UPDATE memberships SET version=0','UPDATE agent_authority SET version=0','UPDATE agent_authority SET fresh_context_required=2',"UPDATE permission_state SET generation=0","UPDATE permission_state SET mode='policy_v1'","UPDATE permission_state SET id=2"):
            with pytest.raises(__import__('sqlite3').IntegrityError):db.raw.execute(sql)


def test_real_reserved_name_collision_rolls_back_earlier_additions(tmp_path):
    path=tmp_path/'hub.db';create_hub(path)
    with open_database(path,writable=True) as db:
        db.raw.execute('ALTER TABLE agent_authority ADD COLUMN authorized_by BLOB')
        db.raw.execute('UPDATE agent_authority SET authorized_by=?',(b'custom-original',))
        before=snapshot(db.raw)
        with pytest.raises(BookflowError) as caught:migrate_to_head(db,'hub',tmp_path/'backups')
        assert caught.value.code=='E_MIGRATION_FAILED' and snapshot(db.raw)==before
