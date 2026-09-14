"""Bound principals and retained output recheck the activated public policy."""
import json
import sqlite3
import pytest
from bookflow.core.config import Config
from bookflow.core.errors import BookflowError
from tests.test_row3_host import hosted
from tests.payment_raw_evidence import database


def activate(hosted):
    state=hosted.ok('permission.show')
    hosted.ok('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))


def company_path(hosted):
    with sqlite3.connect(hosted.root/'hub.db') as db:
        return hosted.root/db.execute('SELECT path FROM companies WHERE id=?',(hosted.company_id,)).fetchone()[0]/'company.db'


@pytest.mark.timeout(120)
def test_retained_publication_rechecks_current_resource_without_replanning(hosted,monkeypatch):
    from bookflow.adapters.mcp.runtime import Runtime
    from bookflow.core import registry
    from bookflow.core.context import Context,Interface
    from tests.test_mcp_runtime import credential
    activate(hosted)
    cred=credential(hosted,monkeypatch)
    runtime=Runtime.for_host(hosted.handle.host)
    cmd=registry.get('journal query')
    intent=runtime.admit(cmd,Context.new(Interface.mcp,'permission publication'),cred,hosted.company_id,'option',False)
    runtime.prepare_json(intent,{},cred)
    result=runtime.execute_json(intent,cred)
    runtime.intents.delivery(intent)
    runtime.intents.finish(intent,receipt=json.dumps(result).encode(),publication=result.permit.retained())
    principal=Config.load(hosted.root/'config.toml').user_table(hosted.login)['user_id']
    membership=next(x for x in hosted.ok('membership.list',dict(company=hosted.company_id))['items']
        if x['user_id']==principal and x['scope_type']=='company')
    hosted.ok('membership.grant',dict(company=hosted.company_id,user=principal,role=membership['role'],
        expected_version=membership['version'],denies=['ledger.read']))
    before=database(company_path(hosted))
    def forbidden(*a,**kw):raise AssertionError('Retained output must not replan')
    monkeypatch.setattr(cmd,'plan',forbidden)
    with pytest.raises(BookflowError) as denied:runtime.lookup(intent.reference,cred)
    assert denied.value.code=='E_PERMISSION'
    assert denied.value.details['outcome']=='unknown'
    assert database(company_path(hosted))==before


@pytest.mark.timeout(120)
def test_agent_actor_floor_and_human_reduction_revoke_actual_bearer(hosted,monkeypatch):
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.core import clock
    principal=Config.load(hosted.root/'config.toml').user_table(hosted.login)['user_id']
    agent=make_actor(hosted.root,'permission-agent',kind='agent',owner_user_id=principal,
        company_role=(hosted.company_id,'readonly'))
    with writer(hosted.root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent,epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent,principal_user_id=principal,
            assigned_by=principal,assigned_at=clock.now_iso()))
    bank=hosted.ok('account.create',dict(name='Agent bank',type='bank'),company=hosted.company_id)['id']
    expense=hosted.ok('account.create',dict(name='Agent expense',type='expense'),company=hosted.company_id)['id']
    activate(hosted)
    # Activation can reduce the old executable footprint and suspend this
    # existing agent. Reauthorize through the existing private owner as fixture
    # preparation; this slice adds no new public agent-administration endpoint.
    from bookflow.core import identity_admin_binding as producer
    from bookflow.core.publication import OSBinding
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_admin_audit import AuditContext
    with sqlite3.connect(hosted.root/'hub.db') as db:
        version,epoch,suspended=db.execute('SELECT version,epoch,suspended_at FROM agent_authority WHERE agent_user_id=?',(agent,)).fetchone()
    assert suspended is not None
    bound=OSBinding.capture(hosted.handle.host,hosted.login)
    def prepare_agent(session):
        request='permission-agent-fixture'
        audit=AuditContext(clock.now_iso(),'python','permission fixture','1','owned','fixture',request,reason='Confirm fixture agent use')
        with session.commits.operation('dispatch.apply',session.hub):
            with producer.hosted_operation(hosted.handle.host,bound,request_id=request,purpose='apply') as operation:
                operation.apply(admin.AuthorizeAgent(agent,version,epoch,True,True),audit=audit)
                session.commits.commit(session.hub,'dispatch.apply')
    hosted.handle.host.run_write(principal,hosted.login,prepare_agent)
    issued=hosted.ok('token.issue',dict(user=agent,principal=principal,label='permission intersection'))
    headers={'Authorization':'Bearer '+issued['secret']}
    assert hosted.call('journal.query',company=hosted.company_id,headers=headers).status_code==200
    before=database(company_path(hosted))
    # The bound human is owner; that cannot supply the agent's missing Standard role.
    refused=hosted.call('journal.post',dict(date='2017-01-01',lines=[dict(account=bank,side='debit',amount='1.00'),dict(account=expense,side='credit',amount='1.00')]),company=hosted.company_id,headers=headers)
    assert refused.json()['code']=='E_PERMISSION',refused.text
    assert database(company_path(hosted))==before
    membership=next(x for x in hosted.ok('membership.list',dict(company=hosted.company_id))['items']
        if x['user_id']==principal and x['scope_type']=='company')
    reduction=dict(company=hosted.company_id,user=principal,role=membership['role'],
        expected_version=membership['version'],denies=['ledger.read'])
    from bookflow.hub import permission_admin_audit
    observed=[]
    def late_failure(tx,prepared):
        assert tx.raw.execute('SELECT revoked_at FROM api_tokens WHERE id=?',(issued['token_id'],)).fetchone()[0] is not None
        observed.append(True)
        raise BookflowError('E_IO',message='Owned late-audit fault')
    hub_before=database(hosted.root/'hub.db')
    with monkeypatch.context() as patch:
        patch.setattr(permission_admin_audit,'insert_audit',late_failure)
        failed=hosted.call('membership.grant',reduction)
    assert failed.json()['code']=='E_IO' and observed==[True],failed.text
    assert database(hosted.root/'hub.db')==hub_before
    assert database(company_path(hosted))==before
    hosted.ok('membership.grant',reduction)
    with sqlite3.connect(hosted.root/'hub.db') as db:
        assert db.execute('SELECT revoked_at FROM api_tokens WHERE id=?',(issued['token_id'],)).fetchone()[0] is not None
        assert db.execute('SELECT epoch FROM agent_authority WHERE agent_user_id=?',(agent,)).fetchone()[0]>1
    refused=hosted.call('journal.query',company=hosted.company_id,headers=headers)
    assert refused.status_code==401,refused.text
    assert database(company_path(hosted))==before
