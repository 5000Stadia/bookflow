"""Actual OS/host producers; policy_v1 installation is owned fixture setup only."""
from dataclasses import replace
import threading
import pytest

from bookflow.core import identity_admin_binding as producer
from bookflow.core.config import Config, os_login, dump
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.hub import identity_admin as b, permission_runtime as runtime, permission_catalog as c
from bookflow.hub.permission_admin_audit import AuditContext
from bookflow.storage.engine import open_database
from tests.permission_storage_support import snapshot
from tests.test_permission_snapshots import install_fixture_policy

REQUEST='binding-request'
AUDIT=AuditContext('2026-09-07T18:00:00Z','python','binding-test','1','owned','binding-session',REQUEST,reason='owned membership change')


@pytest.fixture
def prepared(root, client):
    item=client.company.list()['items'][0]
    uid=Config.load(root/'config.toml').user_table(os_login())['user_id']
    with open_database(root/'hub.db',writable=True) as db:
        install_fixture_policy(db.raw,runtime.catalog_bundle())
    return root,uid,item['company_id']


def intent(uid,cid):return b.PutMembership(uid,c.ScopeKey('company',cid),b.Version(1),'readonly')


def storage(root):
    with open_database(root/'hub.db',writable=False) as db:return snapshot(db.raw)


def test_offline_real_process_preview_and_full_preservation(prepared):
    root,uid,cid=prepared;before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='preview') as operation:
        preview=operation.preview(intent(uid,cid))
        assert preview.visible.changed and preview.visible.prospective
        assert preview.visible.target_id is not None and preview.audit is None
        assert preview.old.stamp==preview.initiating.base_stamp==preview.final.base_stamp
        assert preview.old.keys.users==preview.final.root.keys.users
    assert storage(root)==before


def test_offline_real_process_apply_and_full_rollback(prepared):
    root,uid,cid=prepared;before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        result=operation.apply(intent(uid,cid),audit=AUDIT)
        assert result.visible.changed and not result.visible.prospective
        assert result.private.audit.event.actor_id==uid
        assert {x.record_type for x in result.private.audit.entries}=={'membership','permission_state'}
        assert len(result.private.audit.entries)==2
        member=next(x for x in result.private.final.root.memberships if x.id==result.visible.target_id)
        assert (member.user_id,member.scope_type,member.scope_id,member.role,member.version)==(uid,'company',cid,'readonly',2)
        assert member.grants=='[]' and member.denies=='[]' and member.granted_by==uid
        assert result.private.final.root.stamp.generation==result.private.old.stamp.generation+1
        saved=operation
    assert storage(root)==before
    with pytest.raises(b.AdministrationError):saved.apply(intent(uid,cid),audit=AUDIT)


def test_host_actual_apply_commit_and_noop(prepared):
    root,uid,cid=prepared
    host=Host(root,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        def job(session):
            with session.commits.operation('dispatch.apply',session.hub):
                with producer.hosted_operation(host,admitted,request_id=REQUEST,purpose='apply') as operation:
                    result=operation.apply(intent(uid,cid),audit=AUDIT)
                    event=result.event_id
                    repeated=b.PutMembership(uid,c.ScopeKey('company',cid),b.Version(2),'readonly',None,())
                    before=snapshot(session.hub.raw)
                    assert not operation.apply(repeated,audit=AUDIT).visible.changed
                    assert snapshot(session.hub.raw)==before
                    session.commits.commit(session.hub,'dispatch.apply')
                    with pytest.raises(b.AdministrationError):operation.apply(repeated,audit=AUDIT)
                    return event
        event=host.run_write(uid,os_login(),job)
        with open_database(root/'hub.db',writable=False) as db:
            assert db.raw.execute('SELECT count(*) FROM audit_events WHERE id=?',(event,)).fetchone()==(1,)
            assert db.raw.execute("SELECT role,version,granted_by FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",(uid,cid)).fetchone()==('readonly',2,uid)
    finally:host.stop()


@pytest.mark.parametrize('change',['different','missing','invalid'])
def test_after_dequeue_pending_mapping_denial_no_write(prepared,change):
    root,uid,cid=prepared;host=Host(root,version=client_version());host.start()
    entered=threading.Event();release=threading.Event();result=[]
    try:
        admitted=OSBinding.capture(host,os_login())
        # First real writer job parks, then publishes a pending config. The second
        # job must observe it after dequeue, not use its earlier admitted mapping.
        def first(session):
            entered.set();assert release.wait(5)
            with session.commits.operation('dispatch.apply',session.hub):
                session.hub.raw.execute('BEGIN IMMEDIATE')
                contents={'different':dump({'users':{os_login():{'user_id':'missing'}}}),
                          'missing':dump({'users':{}}),'invalid':'not [toml'}[change]
                session.hub.raw.execute('INSERT INTO pending_config(id,token,request_id,contents) VALUES(1,?,?,?)',('pending','pending-request',contents))
                session.commits.commit(session.hub,'dispatch.apply')
        def caller():
            try:host.run_write(uid,os_login(),first)
            except BaseException as exc:result.append(exc)
        thread=threading.Thread(target=caller);thread.start();assert entered.wait(5)
        def second():
            before=snapshot(host._hub.raw)
            with pytest.raises(b.AdministrationError) as caught:
                with producer.hosted_operation(host,admitted,request_id=REQUEST,purpose='apply') as operation:
                    operation.apply(intent(uid,cid),audit=AUDIT)
            assert caught.value.args==('not_administrator','binding')
            assert snapshot(host._hub.raw)==before and not host._hub.raw.in_transaction
        # Submit uses the actual queue; avoid creating Session via run_write after
        # deliberately invalid config, because that existing owner parses config.
        release.set();host.submit(second)
        thread.join(5);assert not thread.is_alive() and result==[]
    finally:release.set();host.stop()


def test_pending_config_wins_over_stale_file_on_exact_connection(prepared):
    root,uid,cid=prepared
    # Model accepted failed file projection: raw file is stale, durable pending is valid.
    with open_database(root/'hub.db',writable=True) as db:
        contents=(root/'config.toml').read_text()
        db.raw.execute('INSERT INTO pending_config(id,token,request_id,contents) VALUES(1,?,?,?)',('pending','previous',contents))
    (root/'config.toml').write_text(dump({'users':{os_login():{'user_id':'missing'}}}))
    before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        assert operation.apply(intent(uid,cid),audit=AUDIT).private.audit.event.actor_id==uid
    assert storage(root)==before
    assert 'missing' in (root/'config.toml').read_text()


def test_wrong_purpose_request_root_thread_and_transaction(prepared):
    root,uid,cid=prepared;before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='preview') as operation:
        with pytest.raises(b.AdministrationError):operation.apply(intent(uid,cid),audit=AUDIT)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        with pytest.raises(b.AdministrationError):operation.apply(intent(uid,cid),audit=replace(AUDIT,request_id='wrong'))
    assert storage(root)==before
    host=Host(root,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        with pytest.raises(b.AdministrationError):
            with producer.hosted_operation(host,admitted,request_id=REQUEST,purpose='apply'):pass
        def job():
            for wrong in (replace(admitted,root=root/'wrong'),os_login(),object()):
                before=snapshot(host._hub.raw)
                with pytest.raises(b.AdministrationError):
                    with producer.hosted_operation(host,wrong,request_id=REQUEST,purpose='apply'):pass
                assert snapshot(host._hub.raw)==before
            host._hub.raw.execute('BEGIN IMMEDIATE')
            with pytest.raises(b.AdministrationError):
                with producer.hosted_operation(host,admitted,request_id=REQUEST,purpose='apply'):pass
            assert host._hub.raw.in_transaction
            host._hub.raw.execute('ROLLBACK')
        host.submit(job)
    finally:host.stop()


def test_legacy_mode_stays_closed(root,client):
    cid=client.company.list()['items'][0]['company_id'];uid=Config.load(root/'config.toml').user_table(os_login())['user_id']
    before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        with pytest.raises(b.AdministrationError,match='activation_required'):operation.apply(intent(uid,cid),audit=AUDIT)
    assert storage(root)==before


def test_actual_b2_audit_failure_rolls_back_complete_storage(prepared,monkeypatch):
    from bookflow.hub import permission_admin_audit
    root,uid,cid=prepared;before=storage(root)
    def fail(*args,**kwargs):raise RuntimeError('owned audit failure')
    monkeypatch.setattr(permission_admin_audit,'insert_audit',fail)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        with pytest.raises(RuntimeError,match='owned audit failure'):operation.apply(intent(uid,cid),audit=AUDIT)
        assert snapshot(operation._tx.raw)==before
    assert storage(root)==before


def test_same_transaction_mapping_change_is_not_cached(prepared):
    root,uid,cid=prepared;before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='apply') as operation:
        operation._tx.raw.execute('INSERT INTO pending_config(id,token,request_id,contents) VALUES(1,?,?,?)',('pending','changed',dump({'users':{}})))
        after=snapshot(operation._tx.raw)
        with pytest.raises(b.AdministrationError,match='not_administrator'):operation.apply(intent(uid,cid),audit=AUDIT)
        assert snapshot(operation._tx.raw)==after
    assert storage(root)==before



def test_offline_current_company_authority_after_owned_membership_state(prepared):
    root,uid,cid=prepared
    # The actual B2 commit witness independently verifies this complete semantic
    # membership change. This separate read witness isolates current admission
    # from two expensive full-catalog evaluations in one 60-second case.
    with open_database(root/'hub.db',writable=True) as db:
        db.raw.execute("UPDATE memberships SET role='readonly',grants='[]',denies='[]',version=2 WHERE user_id=? AND scope_type='company' AND scope_id=?",(uid,cid))
    before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='preview') as operation:
        assert operation.require_company(cid,c.Requirement('ledger.read','member')).intersection_admitted
    assert storage(root)==before


def test_offline_ended_handle_refuses_before_closed_db_access(prepared):
    root,uid,cid=prepared;before=storage(root)
    with producer.offline_operation(root,request_id=REQUEST,purpose='preview') as operation:
        saved=operation
    with pytest.raises(b.AdministrationError) as caught:saved.preview(intent(uid,cid))
    assert caught.value.args==('invalid_input','binding')
    assert storage(root)==before
