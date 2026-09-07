"""Real B2 producer -> trusted hosted reader -> fresh retained publication."""
from dataclasses import replace
import pytest
from bookflow.core import registry, identity_admin_binding as binding
from bookflow.core.context import Context, client_version
from bookflow.core.config import os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding, PublicationPermit
from bookflow.core.errors import BookflowError
from bookflow.adapters.http.execution import run_history
from bookflow.hub import identity_admin as b
from bookflow.hub.audit_projection import HistorySelection
from bookflow.core.config import Config
from bookflow.storage.engine import open_database
from bookflow.hub.permission_runtime import catalog_bundle
from tests.test_permission_snapshots import install_fixture_policy
import shutil
import bookflow
from bookflow.hub.permission_catalog import ScopeKey
from tests.permission_admin_support import CONTEXT, snapshot


@pytest.fixture(scope='module')
def world(_seeded_template,tmp_path_factory):
    root=tmp_path_factory.mktemp('semantic-world')/'root'
    shutil.copytree(_seeded_template,root)
    client=bookflow.connect(data_root=str(root))
    cid=client.company.list()['items'][0]['company_id']
    uid=Config.load(root/'config.toml').user_table(os_login())['user_id']
    with open_database(root/'hub.db',writable=True) as db:
        install_fixture_policy(db.raw,catalog_bundle())
    registry.load_all()
    host=Host(root,version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        def edit():
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                with binding.hosted_operation(host,cred,request_id='REQUEST',purpose='apply') as operation:
                    result=operation.apply(b.PutMembership(uid,ScopeKey('company',cid),b.Version(1),'readonly'),audit=CONTEXT)
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
                    return result.private.audit.event.id
        event=host.submit(edit)
        yield host,cred,event
    finally:host.stop()


def test_real_b2_producer(world):
    host,cred,event=world
    stored=host.submit(lambda:host._hub.raw.execute('SELECT record_type FROM audit_entries WHERE event_id=? ORDER BY record_type',(event,)).fetchall())
    assert stored==[('membership',),('permission_state',)]


@pytest.fixture(scope='module')
def semantic_reply(world):
    host,cred,event=world
    return run_history(host,HistorySelection(mode='show',event=event),Context.new('python','projection-test').model_copy(update={'request_id':'REQUEST'}),cred)


def test_real_semantic_execution(world,semantic_reply):
    host,cred,event=world
    before=host.submit(lambda:snapshot(host._hub.raw))
    reply=semantic_reply
    assert len(reply['events'])==1
    projected=reply['events'][0]
    assert projected['id']==event
    assert [x['identity']['kind'] for x in projected['entries']]==['membership']
    entry=projected['entries'][0]
    assert entry['before']['role']=='owner' and entry['after']['role']=='readonly'
    assert 'role' in entry['changed_fields']
    assert 'permission_state' not in str(reply)
    assert not any(secret in str(reply) for secret in ('password_hash','source_inventory','generation','request_id'))
    assert host.submit(lambda:snapshot(host._hub.raw))==before
    assert host._readers_attached==0


def test_retained_proof_fresh_reader(world,semantic_reply):
    host,cred,event=world
    before=host.submit(lambda:snapshot(host._hub.raw))
    restored=PublicationPermit.from_retained(semantic_reply.permit.retained())
    restored.check(host,cred)
    assert host.submit(lambda:snapshot(host._hub.raw))==before
    assert host._readers_attached==0
