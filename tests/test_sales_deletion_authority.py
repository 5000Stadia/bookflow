"""Actual authenticated sale deletion and retained read under current principal authority."""
from pathlib import Path
import sqlite3
import pytest
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion_http import office
from tests.payment_raw_evidence import database

def test_revoked_bound_principal_cannot_replay_permanent_agent_delete(office,books,monkeypatch):
    from tests.conftest import make_actor
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h, identity_admin as admin
    from bookflow.core import clock, identity_admin_binding as producer
    from bookflow.core.config import Config
    from bookflow.core.publication import OSBinding
    from bookflow.hub.permission_admin_audit import AuditContext
    principal=Config.load(office.root/'config.toml').user_table(office.login)['user_id']
    agent=make_actor(office.root,'delete-agent',kind='agent',owner_user_id=principal,company_role=(books['company'],'standard'))
    with writer(office.root) as db:
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent,epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent,principal_user_id=principal,assigned_by=principal,assigned_at=clock.now_iso()))
    office.admin('bill.post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=books['delete_stock'],quantity='2',unit_cost='8')]),company=books['company'])
    post=office.admin('invoice.post',dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=books['delete_stock'],quantity='1',unit_price='12')]),company=books['company'])
    state=office.admin('permission.show')
    office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    memberships=office.admin('membership.list',dict(company=books['company']))['items']
    for user in (principal,agent):
        member=next(x for x in memberships if x['user_id']==user and x['scope_type']=='company')
        office.admin('membership.grant',dict(user=user,company=books['company'],role=member['role'],expected_version=member['version'],grants=['transaction.invoice.delete'],denies=['ledger.post']))
    with sqlite3.connect(office.root/'hub.db') as db:
        version,epoch=db.execute('SELECT version,epoch FROM agent_authority WHERE agent_user_id=?',(agent,)).fetchone()
    bound=OSBinding.capture(office.handle.host,office.login)
    def authorize_agent(session):
        request='delete-agent-fixture'
        audit=AuditContext(clock.now_iso(),'python','delete fixture','1','owned','fixture',request,reason='Confirm scoped fixture agent use')
        with session.commits.operation('dispatch.apply',session.hub):
            with producer.hosted_operation(office.handle.host,bound,request_id=request,purpose='apply') as operation:
                operation.apply(admin.AuthorizeAgent(agent,version,epoch,True,True),audit=audit)
                session.commits.commit(session.hub,'dispatch.apply')
    office.handle.host.run_write(principal,office.login,authorize_agent)
    issued=office.admin('token.issue',dict(user=agent,principal=principal,label='Delete principal witness'))
    headers={'Authorization':'Bearer '+issued['secret'],'X-Bookflow-Reason':'Delete by scoped agent'}
    # A separate unauthenticated HTTP client ensures the bearer is the actual producer.
    bearer=office.browser();raw=dict(invoice=post['id'],expected_version=1,operation_key='bound-delete')
    deleted=office.ok(bearer,'invoice.delete',raw,company=books['company'],headers=headers)
    assert deleted['status']=='deleted'
    path=Path(office.admin('company.show',company=books['company'])['path'])/'company.db'
    before=database(path)
    retained=office.ok(bearer,'invoice.show',dict(invoice=post['id'],include_deleted=True),company=books['company'],headers={'Authorization':headers['Authorization']})
    assert retained['status']=='deleted' and retained['deletion']['principal_id']==principal
    assert office.ok(bearer,'invoice.delete',raw,company=books['company'],headers=headers)['idempotent_replay']
    assert database(path)==before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT created_by,principal_id FROM sales_deletions').fetchone()==(agent,principal)
    member=next(x for x in office.admin('membership.list',dict(company=books['company']))['items'] if x['user_id']==principal and x['scope_type']=='company')
    office.admin('membership.grant',dict(user=principal,company=books['company'],role=member['role'],expected_version=member['version'],grants=[],denies=['ledger.post']))
    with sqlite3.connect(office.root/'hub.db') as db:
        assert db.execute('SELECT revoked_at FROM api_tokens WHERE id=?',(issued['token_id'],)).fetchone()[0] is not None
    refused=office.call(bearer,'invoice.delete',raw,company=books['company'],headers=headers)
    assert refused.status_code==401,refused.text
    assert office.call(bearer,'invoice.show',dict(invoice=post['id'],include_deleted=True),company=books['company'],headers={'Authorization':headers['Authorization']}).status_code==401
    assert database(path)==before



def test_deleted_invoice_retains_settlement_and_application_pages(office,books):
    company=books['company']
    exempt=next(row['id'] for row in office.admin('sales-tax-code.list',{},company=company)['items'] if not row['taxable'])
    item=office.admin('item.create',dict(name='Historical settlement service',type='service',description='Service',sales_enabled=True,income_account_id=books['income'],price='12',sales_tax_code_id=exempt),company=company)['id']
    invoice=office.admin('invoice.post',dict(customer=books['customer'],date='2017-01-01',lines=[dict(item=item)]),company=company)
    payment=office.admin('payment.receive',dict(customer=books['customer'],date='2017-01-02',amount='12',payment_method=books['methods']['Cash'],applications=dict(mode='inline',items=[dict(invoice=invoice['id'],amount='12',expected_version=1)]),operation_key='historical-payment'),company=company)
    application=payment['effect']['applications'][0]['application_id']
    version=office.admin('invoice.show',dict(invoice=invoice['id']),company=company)['version']
    office.admin('payment.unapply',dict(payment=payment['id'],expected_version=payment['version'],applications=[dict(application_id=application,invoice_expected_version=version)],operation_key='historical-unapply'),company=company,headers={'X-Bookflow-Reason':'Explicitly unapply before deleting invoice'})
    state=office.admin('permission.show');office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    member=next(x for x in office.admin('membership.list',dict(company=company))['items'] if x['scope_type']=='company' and x['scope_id']==company)
    office.admin('membership.grant',dict(user=member['user_id'],company=company,role=member['role'],expected_version=member['version'],grants=['transaction.invoice.delete'],denies=['ledger.post']))
    version=office.admin('invoice.show',dict(invoice=invoice['id']),company=company)['version']
    office.admin('invoice.delete',dict(invoice=invoice['id'],expected_version=version),company=company,headers={'X-Bookflow-Reason':'Delete unapplied invoice'})
    path=Path(office.admin('company.show',company=company)['path'])/'company.db';before=database(path)
    for route in (f'/invoice/{invoice["id"]}/settlement',f'/application/{application}/history'):
        page=office.installer.get(f'/c/{company}'+route)
        assert page.status_code==200,page.text
        assert f'/invoice/{invoice["id"]}?include_deleted=1' in page.text
    assert database(path)==before
