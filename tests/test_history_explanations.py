"""Captured instructions and admitted explanations on actual company writes."""
from pathlib import Path
import pytest
from bookflow.core import registry
from bookflow.core.config import os_login
from bookflow.core.context import Context,client_version
from bookflow.core.host import Host
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from bookflow.adapters.http.execution import run_hosted
from bookflow.storage.engine import open_database
from tests.test_audit_projection_activity import world


def test_customer_reason_and_deactivated_directive_use_historical_capture(world):
    client=world['client'];company='Demo Plumbing Co'
    directive=client.directive.add(text='Keep <original> customer instruction',company=company)['directive']
    customer=client.customer.create(name='Explanation customer',company=company,
        reason='Customer called <today>',directive=directive['id'])
    client.directive.deactivate(directive=directive['id'],company=company)
    client.customer.create(name='Valid explanation endpoint',company=company)
    info=client.company.show(company=company);path=Path(info['path'])/'company.db'
    # Prove the current row is not the historical text source.
    with open_database(path,writable=True) as db:
        db.raw.execute('UPDATE directives SET text=? WHERE id=?',('Later row text',directive['id']))
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        raw={'record_type':'customer','record_id':customer['id'],'limit':1}
        out=run_hosted(host,registry.get('audit list'),raw,Context.new('http','Explanation read'),cred,info['company_id'],'option',False)
        explanation=out['items'][0]['explanation']
        assert explanation=={'reason':'Customer called <today>','directive_status':'available',
            'directive':{'id':directive['id'],'code':directive['code'],'text':'Keep <original> customer instruction'}}
        out.check()
        assert host._readers_attached==0
    finally:host.stop()

    # Corrupt only the cited provenance: the admitted reason survives and the
    # contract distinguishes unavailable citation from no citation.
    with open_database(path,writable=True) as db:
        db.raw.execute('UPDATE audit_entries SET after=? WHERE record_type=? AND record_id=? AND action=?',
            (b'broken directive','directive',directive['id'],'create'))
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        out=run_hosted(host,registry.get('audit list'),raw,Context.new('http','Unavailable directive'),cred,info['company_id'],'option',False)
        assert out['items'][0]['explanation']=={'reason':'Customer called <today>','directive_status':'unavailable','directive':None}
        out.check()
    finally:host.stop()


def test_separate_directive_denial_keeps_reason_without_reference(world):
    from bookflow.core import identity_admin_binding as binding
    from bookflow.core.config import Config
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT

    client=world['client'];company='Demo Plumbing Co'
    directive=client.directive.add(text='Private instruction',company=company)['directive']
    customer=client.customer.create(name='Separate directive admission',company=company,
        reason='Visible business explanation',directive=directive['id'])
    cid=client.company.show(company=company)['company_id']
    uid=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        def read(credential):
            return run_hosted(host,registry.get('audit list'),
                {'record_type':'customer','record_id':customer['id'],'limit':1},
                Context.new('http','Directive authority'),credential,cid,'option',False)
        before=read(cred)
        assert before['items'][0]['explanation']['directive_status']=='available'
        def reduce():
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                with binding.hosted_operation(host,cred,request_id='REQUEST',purpose='apply') as operation:
                    operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('directive',)),audit=CONTEXT)
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
        host.submit(reduce)
        after=read(OSBinding.capture(host,os_login()))
        assert after['items'][0]['explanation']=={'reason':'Visible business explanation','directive_status':'unavailable','directive':None}
        with pytest.raises(BookflowError) as caught:before.check()
        assert caught.value.code=='E_PERMISSION'
        after.check()
    finally:host.stop()
