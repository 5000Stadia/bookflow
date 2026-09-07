from contextlib import contextmanager
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
import tomllib
import pytest
from bookflow import BookflowError
from bookflow.hub import identity_admin as b, permission_catalog as c, credentials
from bookflow.storage.engine import open_database
from tests.permission_admin_support import *


@pytest.fixture
def path(tmp_path):return make_root(tmp_path/'hub.db')


def test_two_same_version_writers_one_winner_and_stale_noop(path):
    ready=threading.Barrier(2)
    def writer():
        with open_database(path,writable=True) as db:
            ready.wait();db.raw.execute('BEGIN IMMEDIATE')
            try:
                apply(db,b.PutMembership('R',c.ScopeKey('organization','O'),b.Version(1),'readonly'))
                db.raw.execute('COMMIT');return 'won'
            except b.AdministrationError as exc:
                db.raw.execute('ROLLBACK');return exc.category
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:writer(),range(2)))
    assert sorted(results)==['conflict','won']
    with open_database(path,writable=False) as db:
        assert db.raw.execute("SELECT role,version FROM memberships WHERE id='M-R'").fetchone()==('readonly',2)
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(2,)
        assert len(rows(db.raw,'audit_events'))==1


def test_concurrent_last_admin_deactivation_leaves_one(path):
    with open_database(path,writable=True) as db:db.raw.execute("UPDATE users SET hub_admin=1 WHERE id='R'")
    ready=threading.Barrier(2)
    def writer(person):
        with open_database(path,writable=True) as db:
            ready.wait();db.raw.execute('BEGIN IMMEDIATE')
            try:
                apply(db,b.SetUserActive(person,5,False),person)
                db.raw.execute('COMMIT');return 'won'
            except b.AdministrationError as exc:
                db.raw.execute('ROLLBACK');return exc.category
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(writer,('H','R')))
    assert sorted(results)==['protected_identity','won']
    with open_database(path,writable=False) as db:
        admins=db.raw.execute("SELECT id,version,active FROM users WHERE id IN ('H','R') ORDER BY id").fetchall()
        assert sorted((version,active) for _,version,active in admins)==[(5,1),(6,0)]
        tokenrows=tokens(db.raw)
        for person,version,active in admins:
            assert tokenrows['T-'+person]['revoked_at']==(None if active else AT)
        assert len(rows(db.raw,'audit_events'))==1


def test_issuance_before_suspension_included_then_existing_issuer_refuses(path):
    acquired=threading.Event();release=threading.Event()
    def issuer():
        with open_database(path,writable=True) as db:
            db.raw.execute('BEGIN IMMEDIATE')
            token,_=credentials.issue_token(db,user_id='G',on_behalf_of='Q',kind='bearer',label='new',days=None,via='cli',actor_id='H')
            acquired.set();assert release.wait(5);db.raw.execute('COMMIT');return token['id']
    def reducer():
        assert acquired.wait(5);release.set()
        with open_database(path,writable=True) as db:
            db.raw.execute('BEGIN IMMEDIATE');apply(db,b.SetAssignments('G',4,7,('Q','I'),False));db.raw.execute('COMMIT')
    with ThreadPoolExecutor(2) as pool:
        issued=pool.submit(issuer);reduced=pool.submit(reducer);token_id=issued.result();reduced.result()
    with open_database(path,writable=True) as db:
        token=tokens(db.raw)[token_id]
        assert token['version']==2 and token['revoked_at']==AT and token['authority_epoch']==7
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(BookflowError):credentials.issue_token(db,user_id='G',on_behalf_of='Q',kind='bearer',label='late',days=None,via='cli',actor_id='H')
        assert snapshot(db.raw)==before


def test_preview_is_recomputed_after_hidden_generation_and_binding_revocation(path):
    intent=b.PutMembership('R',c.ScopeKey('organization','O'),b.Version(1),'readonly')
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        preview=b.preview_edit(db,binding=binding(path),intent=intent,catalog=BUNDLE,visibility=VISIBILITY,request_id='REQUEST')
        assert preview.visible.prospective and snapshot(db.raw)==before
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');apply(db,b.PutMembership('U',c.ScopeKey('organization','Z'),b.Version(1),'admin'));db.raw.execute('COMMIT')
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');result=apply(db,intent)
        assert result.visible.changed and result.private.old.stamp.generation==2
        assert result.private.final.root.stamp.generation==3
        db.raw.execute('COMMIT')
        db.raw.execute("UPDATE api_tokens SET revoked_at=? WHERE id='T-H'",(AT,))
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(BookflowError):apply(db,b.SetUserActive('R',5,False))
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('case',['expired','changed_binding','wrong_root','cached_admin','agent','system','context'])
def test_current_token_and_actor_authentication(path,case):
    with open_database(path,writable=True) as db:
        supplied=binding(path)
        if case=='expired':db.raw.execute("UPDATE api_tokens SET expires_at='2000-01-01T00:00:00Z' WHERE id='T-H'")
        if case=='changed_binding':db.raw.execute("UPDATE api_tokens SET user_id='A' WHERE id='T-H'")
        if case=='wrong_root':supplied=replace(supplied,root=path.parent/'other.db')
        if case=='cached_admin':db.raw.execute("UPDATE users SET hub_admin=0 WHERE id='H'")
        if case=='system':
            token=tokens(db.raw)['T-H'];insert(db.raw,'api_tokens',dict(token,id='T-S',user_id='S',token_hash=credentials.token_hash('secret-S')))
            supplied=b.TokenBinding('secret-S','T-S','S','bearer',None,path,'REQUEST')
        if case=='agent':supplied=b.TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
        if case=='context':supplied=CONTEXT
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises((BookflowError,b.AdministrationError)) as caught:
            b.apply_edit(db,binding=supplied,intent=b.SetUserActive('R',5,False),catalog=BUNDLE,visibility=VISIBILITY,audit=CONTEXT)
        assert 'secret-' not in str(caught.value) and snapshot(db.raw)==before
        assert 'secret-H' not in repr(binding(path))


@contextmanager
def supplied_os(db,*,purpose='apply',admitted='H',captured='H'):
    """Explicit test-only producer evidence; no actual OS/serialization claim.

    Simulates after-dequeue mapping resolution, using same-connection pending
    contents over the captured file mapping. Production supplier remains absent.
    """
    pending=db.raw.execute('SELECT contents FROM main.pending_config WHERE id=1').fetchone()
    mapped=tomllib.loads(pending[0]).get('users',{}).get('owned-login',{}).get('user_id') if pending else captured
    with b.OSOperation(db,request_id='REQUEST',purpose=purpose) as operation:
        yield b.OSBinding(operation,admitted,mapped)


def test_conditional_os_pending_mapping_precedence_and_success(path):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        db.raw.execute("INSERT INTO pending_config VALUES(1,'PENDING','REQUEST',?)",('[users.owned-login]\nuser_id="H"\n',))
        with supplied_os(db,captured='A') as os_binding:
            result=b.apply_edit(db,binding=os_binding,intent=b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'standard'),catalog=BUNDLE,visibility=VISIBILITY,audit=CONTEXT)
            assert result.visible.changed
        assert db.raw.in_transaction


@pytest.mark.parametrize('case',['mapping_changed','mapping_missing','wrong_request','wrong_database','preview_to_apply','ended','commit_then_begin','rollback_then_begin'])
def test_conditional_os_lifetime_and_association_rejects(path,tmp_path,case):
    other=make_root(tmp_path/'other.db') if case=='wrong_database' else None
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        if case.startswith('mapping_'):
            contents='[users.owned-login]\nuser_id="A"\n' if case=='mapping_changed' else '[users]\n'
            db.raw.execute("INSERT INTO pending_config VALUES(1,'PENDING','REQUEST',?)",(contents,))
        with supplied_os(db,purpose='preview' if case=='preview_to_apply' else 'apply') as os_binding:
            if case=='ended':os_binding.operation.__exit__(None,None,None)
            if case in ('commit_then_begin','rollback_then_begin'):
                db.raw.execute('COMMIT' if case=='commit_then_begin' else 'ROLLBACK');db.raw.execute('BEGIN IMMEDIATE')
            before=snapshot(db.raw)
            context=replace(CONTEXT,request_id='OTHER') if case=='wrong_request' else CONTEXT
            if other:
                with open_database(other,writable=True) as target:
                    target.raw.execute('BEGIN IMMEDIATE');target_before=snapshot(target.raw)
                    with pytest.raises(b.AdministrationError):b.apply_edit(target,binding=os_binding,intent=b.SetUserActive('R',5,False),catalog=BUNDLE,visibility=VISIBILITY,audit=context)
                    assert snapshot(target.raw)==target_before
            else:
                with pytest.raises(b.AdministrationError):b.apply_edit(db,binding=os_binding,intent=b.SetUserActive('R',5,False),catalog=BUNDLE,visibility=VISIBILITY,audit=context)
            assert snapshot(db.raw)==before


@pytest.mark.parametrize('name',['users','API_TOKENS','agent_principals','agent_authority','permission_state'])
def test_temp_shadow_rejected_before_existing_credential_owner(path,name):
    with open_database(path,writable=True) as db:
        db.raw.execute('CREATE TEMP TABLE '+name+' (private TEXT)')
        db.raw.execute('INSERT INTO temp.'+name+" VALUES ('untouched')")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw);local=snapshot(db.raw,schema='temp')
        with pytest.raises(b.AdministrationError,match='unsupported_schema'):apply(db,b.SetUserActive('R',5,False))
        assert snapshot(db.raw)==before and snapshot(db.raw,schema='temp')==local


def test_serialized_reauthorization_and_assignment_revoke_recheck_same_authority(path,tmp_path):
    with open_database(path,writable=True) as db:
        db.raw.execute("UPDATE agent_authority SET suspended_at=?,suspension_reason='earlier',fresh_context_required=1 WHERE agent_user_id='G'",(OLD,))
        original=tokens(db.raw)
    acquired=threading.Event();release=threading.Event()
    def authorizer():
        with open_database(path,writable=True) as db:
            db.raw.execute('BEGIN IMMEDIATE')
            apply(db,b.AuthorizeAgent('G',4,7,True,True));acquired.set();assert release.wait(5)
            db.raw.execute('COMMIT')
    def remover():
        assert acquired.wait(5);release.set()
        with open_database(path,writable=True) as db:
            db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
            with pytest.raises(b.AdministrationError,match='conflict'):apply(db,b.SetAssignments('G',4,7,('Q','I'),False))
            assert snapshot(db.raw)==before
            apply(db,b.SetAssignments('G',5,7,('Q','I'),False));db.raw.execute('COMMIT')
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(authorizer);second=pool.submit(remover);first.result();second.result()
    with open_database(path,writable=False) as db:
        assert db.raw.execute("SELECT epoch,version,suspended_at,suspension_reason,fresh_context_required FROM agent_authority WHERE agent_user_id='G'").fetchone()==(8,6,AT,'binding_loss',1)
        assert tokens(db.raw)==expected_tokens(original,G_REVOKED)
        assert db.raw.execute('SELECT generation FROM permission_state').fetchone()==(3,)
        assert len(rows(db.raw,'audit_events'))==2
        receipt(tmp_path/'serialized-reauthorization.json',tokens=tokens(db.raw),authority=rows(db.raw,'agent_authority'))


def test_token_preview_allocates_no_durable_identity_or_audit_time(path,monkeypatch):
    def forbidden():raise AssertionError('preview allocated durable identity')
    monkeypatch.setattr(b,'new_id',forbidden)
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
        result=b.preview_edit(db,binding=binding(path),intent=b.PutMembership('R',c.ScopeKey('company','C'),b.Absent(),'readonly'),catalog=BUNDLE,visibility=VISIBILITY,request_id='REQUEST')
        assert result.visible.target_id is None and result.audit is None
        assert snapshot(db.raw)==before
