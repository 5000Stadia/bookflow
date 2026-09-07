"""Actual token/session owners preserve sparse logout after physical expiry sweep."""
from datetime import timedelta
import pytest
from bookflow.core import clock
from bookflow.core.context import Context,client_version
from bookflow.core.host import Host
from bookflow.core.publication import PublicationPermit
from bookflow.adapters.http.app import _issue_session,_revoke,Credential
from bookflow.adapters.http.execution import run_history
from bookflow.hub.audit_projection import HistorySelection
from bookflow.hub.credentials import token_hash
from tests.conftest import make_actor
from tests.permission_admin_support import snapshot
from tests.test_audit_projection_activity import world


@pytest.fixture(scope='module')
def person(world):return make_actor(world['root'],'session-owner')


@pytest.fixture(scope='module')
def password(world,person):
    return world['client'].user.set_password(username='session-owner',password='owned-session-test-only')


def test_owned_password_producer(password):assert password['changed']


@pytest.fixture(scope='module')
def token(world,password):return world['client'].token.issue(user='session-owner',label='owned-reader')


def test_actual_token_producer(token,person):assert token['user_id']==person and token['secret']


@pytest.fixture(scope='module')
def hosted(world,token):
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,Credential(token['user_id'],token['token_id'],'human',None,secret=token['secret'])
    finally:host.stop()


@pytest.fixture(scope='module')
def session(hosted,person):
    host,_=hosted
    password_hash=host.submit(lambda:host._hub.raw.execute('SELECT password_hash FROM users WHERE id=?',(person,)).fetchone()[0])
    secret=_issue_session(host,person,username='session-owner',expected_password_hash=password_hash)
    return host.submit(lambda:host._hub.raw.execute('SELECT id,expires_at FROM api_tokens WHERE token_hash=?',(token_hash(secret),)).fetchone())


def test_actual_session_issue(session):assert session[0] and session[1]


@pytest.fixture(scope='module')
def logout(hosted,person,session):
    host,_=hosted
    assert host.run_write(person,'',lambda s:_revoke(s,session[0],'logout'))=={'ok':True}
    return host.submit(lambda:host._hub.raw.execute("SELECT id FROM audit_events WHERE command='logout' ORDER BY seq DESC LIMIT 1").fetchone()[0])


def test_sparse_logout_producer(hosted,logout):
    from bookflow.core.audit import decode_snapshot
    host,_=hosted
    before,after=host.submit(lambda:host._hub.raw.execute('SELECT before,after FROM audit_entries WHERE event_id=?',(logout,)).fetchone())
    assert before is None and decode_snapshot(after)=={'revoked':True}


@pytest.fixture(scope='module')
def swept(hosted,session,logout):
    host,_=hosted
    with pytest.MonkeyPatch.context() as m:
        instant=clock.parse_iso(session[1])+timedelta(days=2)
        m.setattr(clock,'now',lambda:instant)
        count=host.submit(host._sweep_on_writer)
    return count


def test_actual_physical_sweep(hosted,session,swept):
    host,_=hosted
    assert swept==1
    assert host.submit(lambda:host._hub.raw.execute('SELECT id FROM api_tokens WHERE id=?',(session[0],)).fetchone()) is None


@pytest.fixture(scope='module')
def receipt(hosted,logout,swept):
    host,cred=hosted;before=host.submit(lambda:snapshot(host._hub.raw))
    result=run_history(host,HistorySelection(mode='show',event=logout),Context.new('http','logout-history'),cred)
    assert host.submit(lambda:snapshot(host._hub.raw))==before
    return result


def test_sparse_history_keeps_its_historical_owner(hosted,session,receipt):
    entries=receipt['events'][0]['entries']
    assert len(entries)==1 and entries[0]['identity']['id']==session[0]
    assert entries[0]['before'] is None and entries[0]['after']=={'tag':'token_revocation','revoked':True}
    assert receipt['events'][0]['command']=='logout'
    assert 'token_hash' not in str(receipt) and 'password_hash' not in str(receipt)


def test_swept_token_history_fresh_publication(hosted,receipt):
    host,cred=hosted;before=host.submit(lambda:snapshot(host._hub.raw))
    PublicationPermit.from_retained(receipt.permit.retained()).check(host,cred)
    assert host.submit(lambda:snapshot(host._hub.raw))==before and host._readers_attached==0
