"""Actual B2 commits and current credentials across private semantic publication."""
import pytest
from bookflow.core import registry,identity_admin_binding as binding
from bookflow.core.context import Context,client_version
from bookflow.core.host import Host
from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit
from bookflow.adapters.http.execution import run_history
from bookflow.adapters.http.app import Credential
from bookflow.hub import identity_admin as b,credentials
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.hub.audit_projection import HistorySelection
from tests.test_permission_runtime import path
from tests.permission_admin_support import CONTEXT,binding as token_binding,snapshot


def apply(host,path,intent,person):
    def job():
        with host._commit_hooks.operation('dispatch.apply',host._hub):
            with binding.hosted_operation(host,token_binding(path,person),request_id='REQUEST',purpose='apply') as operation:
                result=operation.apply(intent,audit=CONTEXT)
                host._commit_hooks.commit(host._hub,'dispatch.apply')
                return result.private.audit.event.id
    return host.submit(job)


def credential(host,who):
    row=host.submit(lambda:credentials.resolve_token(host._hub,'secret-'+who))
    return Credential(row['user_id'],row['id'],row['kind'],None,
        on_behalf_of=row.get('on_behalf_of'),secret='secret-'+who)


@pytest.fixture(scope='module',params=['unrelated','disable_subject'])
def scenario(request,tmp_path_factory):
    from tests.test_permission_runtime import path as root_fixture
    rootpath=root_fixture.__wrapped__(tmp_path_factory.mktemp('proof-'+request.param))
    registry.load_all();host=Host(rootpath.parent,version=client_version());host.start()
    try:
        event=apply(host,rootpath,b.PutMembership('R',ScopeKey('organization','O'),b.Version(1),'readonly'),'A')
        yield host,rootpath,event,request.param
    finally:host.stop()


def test_actual_membership_producer(scenario):
    host,_,event,_=scenario
    kinds=host.submit(lambda:host._hub.raw.execute('SELECT record_type FROM audit_entries WHERE event_id=? ORDER BY record_type',(event,)).fetchall())
    assert kinds==[('membership',),('permission_state',)]


@pytest.fixture(scope='module')
def retained(scenario):
    host,_,event,_=scenario
    cred=credential(host,'R');ctx=Context.new('http','semantic-proof').model_copy(update={'request_id':'REQUEST'})
    before=host.submit(lambda:snapshot(host._hub.raw))
    reply=run_history(host,HistorySelection(mode='show',event=event),ctx,cred)
    assert host.submit(lambda:snapshot(host._hub.raw))==before
    entries=reply['events'][0]['entries']
    assert len(entries)==1 and entries[0]['identity']['kind']=='membership'
    assert entries[0]['before']['role']=='standard' and entries[0]['after']['role']=='readonly'
    return PublicationPermit.from_retained(reply.permit.retained()),cred


def test_actual_semantic_receipt(scenario,retained):
    host,_,_,_=scenario
    permit,cred=retained
    assert permit.audit_proof.identity.actor=='R' and cred.user_id=='R'
    assert host._readers_attached==0


@pytest.fixture(scope='module')
def changed(scenario,retained):
    host,rootpath,_,change=scenario
    event=apply(host,rootpath,b.SetUserActive('U' if change=='unrelated' else 'R',5,False),'H')
    return event,host.submit(lambda:snapshot(host._hub.raw))


def test_actual_following_commit(scenario,changed):
    host,_,_,change=scenario
    event,_=changed
    who='U' if change=='unrelated' else 'R'
    assert host.submit(lambda:host._hub.raw.execute('SELECT active FROM users WHERE id=?',(who,)).fetchone())==(0,)
    assert host.submit(lambda:host._hub.raw.execute('SELECT id FROM audit_events WHERE id=?',(event,)).fetchone())==(event,)


def test_fresh_publication_authority_and_full_preservation(scenario,retained,changed):
    host,_,_,change=scenario
    permit,cred=retained
    _,after_commit=changed
    if change=='unrelated':permit.check(host,cred)
    else:
        with pytest.raises(BookflowError) as caught:permit.check(host,cred)
        assert caught.value.code in ('E_UNAUTHENTICATED','E_PERMISSION')
    assert host.submit(lambda:snapshot(host._hub.raw))==after_commit
    assert host._readers_attached==0
