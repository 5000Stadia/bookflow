"""Hidden-only reference edits from actual producers and a real B2 deny."""
import sqlite3
import pytest
from bookflow.core import identity_admin_binding as binding
from bookflow.core.context import Context,client_version
from bookflow.core.config import Config,os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.hub import identity_admin as b,audit_projection as p
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_history
from tests.permission_admin_support import CONTEXT
from tests.test_audit_projection_activity import world,customer,storage


def test_owned_customer(customer):assert customer['version']==1


@pytest.fixture(scope='module')
def vendor(world):
    return world['client'].vendor.create(name='Hidden link supplier',company='Demo Plumbing Co')


def test_owned_vendor(vendor):assert vendor['version']==1


@pytest.fixture(scope='module')
def linked(world,customer,vendor):
    result=world['client'].run('customer link-vendor',dict(customer=customer['id'],vendor=vendor['id'],
        expected_customer_version=1,expected_vendor_version=1),company='Demo Plumbing Co')
    return result


def test_real_link_has_expected_endpoints(linked,customer,vendor):
    assert linked['customer_id']==customer['id'] and linked['vendor_id']==vendor['id']
    assert linked['customer_version']==2 and linked['vendor_version']==2


@pytest.fixture(scope='module')
def hosted(world,linked):
    from pathlib import Path
    row=world['client'].company.show(company='Demo Plumbing Co');path=Path(row['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        event=db.execute("SELECT id FROM audit_events WHERE command='customer link-vendor' ORDER BY seq DESC LIMIT 1").fetchone()[0]
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,row['company_id'],path,event,OSBinding.capture(host,os_login())
    finally:host.stop()


def test_full_authority_preserves_the_link(hosted,customer,vendor):
    host,cid,path,event,cred=hosted;before=storage(path)
    result=run_history(host,p.HistorySelection(mode='show',company=cid,event=event),Context.new('http','full-link'),cred)
    kinds={x['identity']['kind'] for x in result['events'][0]['entries']}
    assert kinds=={'customer','vendor','customer_vendor_link'}
    customer_entry=next(x for x in result['events'][0]['entries'] if x['identity']['kind']=='customer')
    assert customer_entry['before']['counterparty_link'] is None
    assert customer_entry['after']['counterparty_link']['vendor_id']==vendor['id']
    assert storage(path)==before


@pytest.fixture(scope='module')
def denied(hosted,world):
    host,cid,path,event,cred=hosted
    uid=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
    def apply():
        with host._commit_hooks.operation('dispatch.apply',host._hub):
            with binding.hosted_operation(host,cred,request_id='REQUEST',purpose='apply') as operation:
                operation.apply(b.PutMembership(uid,ScopeKey('company',cid),b.Version(1),'owner',denies=('vendor',)),audit=CONTEXT)
                host._commit_hooks.commit(host._hub,'dispatch.apply')
    host.submit(apply)
    return OSBinding.capture(host,os_login())


def test_real_current_deny(denied):assert denied is not None


def test_link_only_event_disappears_without_version_leak(hosted,denied):
    host,cid,path,event,_=hosted;before=storage(path)
    with pytest.raises(BookflowError) as caught:
        run_history(host,p.HistorySelection(mode='show',company=cid,event=event),Context.new('http','hidden-link'),denied)
    assert caught.value.code=='E_EVENT_NOT_FOUND' and caught.value.details=={}
    assert storage(path)==before and host._readers_attached==0
