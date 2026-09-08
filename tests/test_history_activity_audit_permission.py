"""Audit denial withholds narrative while ordinary activity remains available."""
import pytest
from bookflow.core import registry, identity_admin_binding as binding
from bookflow.core.config import Config,os_login
from bookflow.core.context import Context,client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from bookflow.adapters.http.execution import run_hosted
from tests.permission_admin_support import CONTEXT
from tests.test_audit_projection_activity import world


def test_audit_denial_withholds_activity_explanation_and_invalidates_retained(world):
    client=world['client'];company='Demo Plumbing Co'
    directive=client.directive.add(text='Only rename after approval',company=company)['directive']
    customer=client.customer.create(name='Activity audit permission',company=company,
        reason='Owner approved the name',directive=directive['id'])
    uncited_reason='Owner approved without a standing instruction'
    uncited=client.customer.create(name='Uncited activity reason',company=company,reason=uncited_reason)
    cid=client.company.show(company=company)['company_id']
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        raw={'record_type':'customer','record_id':customer['id'],'limit':1}
        def read(command,credential,record_id=customer['id']):
            query={**raw,'record_id':record_id}
            return run_hosted(host,registry.get(command),query,Context.new('http','Activity audit permission'),credential,cid,'option',False)
        before=read('activity',cred)
        assert before['items'][0]['explanation']['directive']['text']==directive['text']
        uncited_before=read('activity',cred,uncited['id'])
        assert uncited_before['items'][0]['explanation']=={
            'reason':uncited_reason,'directive_status':'not_cited','directive':None}
        uncited_retained=PublicationPermit.from_retained(uncited_before.permit.retained())
        retained=PublicationPermit.from_retained(before.permit.retained())
        uid=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
        def reduce():
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                with binding.hosted_operation(host,cred,request_id='REQUEST',purpose='apply') as operation:
                    operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('audit',)),audit=CONTEXT)
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
        host.submit(reduce)
        current=OSBinding.capture(host,os_login())
        with pytest.raises(BookflowError) as error:read('audit list',current)
        assert error.value.code=='E_PERMISSION'
        after=read('activity',current)
        assert after['count']==1 and after['items'][0]['record_id']==customer['id']
        assert after['items'][0]['explanation'] is None
        after.check()
        uncited_after=read('activity',current,uncited['id'])
        assert uncited_after['count']==1 and uncited_after['items'][0]['record_id']==uncited['id']
        assert uncited_after['items'][0]['explanation'] is None
        uncited_after.check()
        with pytest.raises(BookflowError) as uncited_stale:uncited_retained.check(host,current)
        assert uncited_stale.value.code=='E_PERMISSION'
        with pytest.raises(BookflowError) as stale:retained.check(host,current)
        assert stale.value.code=='E_PERMISSION'
        assert host._readers_attached==0
    finally:host.stop()
