"""Authenticated registered Delete: exact family/read conjunction and current replay admission."""
from pathlib import Path
import sqlite3
import pytest
from tests.test_bill_item_lines import books, _inventory_part
from tests.test_identity_commands import office as established_office
from tests.payment_raw_evidence import database


@pytest.fixture
def office(books):
    books['delete_stock']=_inventory_part(books)
    # Reuse the existing host/login harness over this bounded fresh company.
    yield from established_office.__wrapped__(Path(books['client'].data_root))


def test_standard_user_delete_without_post_and_revoked_permanent_retry(office,books):
    company=books['company'];reason={'X-Bookflow-Reason':'Remove duplicate purchase'}
    person=office.admin('user.add',dict(username='delete-clerk',password='clerk fixture',company=company,role='standard'))
    clerk=office.login_as('delete-clerk','clerk fixture')
    post=office.admin('check.post',dict(account=books['bank'],date='2017-01-02',amount='16',
        items=[dict(item=books['delete_stock'],quantity='2',unit_cost='8')]),company=company)
    state=office.admin('permission.show')
    office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    path=Path(office.admin('company.show',company=company)['path'])/'company.db'
    raw=dict(check=post['id'],expected_version=post['version'],operation_key='http-delete')
    before=database(path)
    assert office.call(clerk,'check.delete',raw,company=company,headers=reason).json()['code']=='E_PERMISSION'
    def grant(version,grants,denies):
        return office.admin('membership.grant',dict(user=person['user_id'],company=company,
            expected_version=version,grants=grants,denies=denies))
    grant(1,['transaction.card_charge.delete'],['ledger.post'])
    assert office.call(clerk,'check.delete',raw,company=company,headers=reason).json()['code']=='E_PERMISSION'
    both=['transaction.check.delete','transaction.card_charge.delete']
    grant(2,both,['ledger.post','ledger.read'])
    assert office.call(clerk,'check.delete',raw,company=company,headers=reason).json()['code']=='E_PERMISSION'
    grant(3,both,['ledger.post'])
    wrong=office.call(clerk,'card-charge.delete',dict(card_charge=post['id'],expected_version=1),company=company,headers=reason)
    assert wrong.json()['code']=='E_RECORD_NOT_FOUND',wrong.text
    for name in ('check.update','check.void'):
        assert office.call(clerk,name,dict(check=post['id'],expected_version=1),company=company,headers=reason).json()['code']=='E_PERMISSION'
    assert database(path)==before
    deleted=office.ok(clerk,'check.delete',raw,company=company,headers=reason)
    assert deleted['status']=='deleted' and deleted['cancelled_stock_movements']==1
    after=database(path)
    replay=office.ok(clerk,'check.delete',raw,company=company,headers=reason)
    assert replay['idempotent_replay'] and not replay['changed']
    assert database(path)==after
    office.admin('membership.revoke',dict(user=person['user_id'],company=company,expected_version=4))
    refused=office.call(clerk,'check.delete',raw,company=company,headers=reason)
    assert refused.json()['code']=='E_COMPANY_NOT_FOUND',refused.text
    assert database(path)==after
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM purchase_deletions').fetchone()==(1,)
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?',(books['delete_stock'],)).fetchone()==(0,0)


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
    post=office.admin('check.post',dict(account=books['bank'],date='2017-01-01',amount='1',expenses=[dict(account=books['freight'],amount='1')]),company=books['company'])
    state=office.admin('permission.show')
    office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    memberships=office.admin('membership.list',dict(company=books['company']))['items']
    for user in (principal,agent):
        member=next(x for x in memberships if x['user_id']==user and x['scope_type']=='company')
        office.admin('membership.grant',dict(user=user,company=books['company'],role=member['role'],expected_version=member['version'],grants=['transaction.check.delete'],denies=['ledger.post']))
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
    bearer=office.browser();raw=dict(check=post['id'],expected_version=1,operation_key='bound-delete')
    deleted=office.ok(bearer,'check.delete',raw,company=books['company'],headers=headers)
    assert deleted['status']=='deleted'
    path=Path(office.admin('company.show',company=books['company'])['path'])/'company.db'
    before=database(path)
    assert office.ok(bearer,'check.delete',raw,company=books['company'],headers=headers)['idempotent_replay']
    assert database(path)==before
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT created_by,principal_id FROM purchase_deletions').fetchone()==(agent,principal)
    member=next(x for x in office.admin('membership.list',dict(company=books['company']))['items'] if x['user_id']==principal and x['scope_type']=='company')
    office.admin('membership.grant',dict(user=principal,company=books['company'],role=member['role'],expected_version=member['version'],grants=[],denies=['ledger.post']))
    with sqlite3.connect(office.root/'hub.db') as db:
        assert db.execute('SELECT revoked_at FROM api_tokens WHERE id=?',(issued['token_id'],)).fetchone()[0] is not None
    refused=office.call(bearer,'check.delete',raw,company=books['company'],headers=headers)
    assert refused.status_code==401,refused.text
    assert database(path)==before


def test_same_version_public_grant_revoke_race_has_one_authoritative_outcome(office,books):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    company=books['company']
    person=office.admin('user.add',dict(username='race-clerk',password='race fixture',company=company,role='standard'))
    clerk=office.login_as('race-clerk','race fixture')
    post=office.admin('check.post',dict(account=books['bank'],date='2017-01-01',amount='1',expenses=[dict(account=books['freight'],amount='1')]),company=company)
    state=office.admin('permission.show')
    office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    path=Path(office.admin('company.show',company=company)['path'])/'company.db';before=database(path)
    ready=Barrier(2)
    grant=dict(user=person['user_id'],company=company,role='standard',expected_version=1,grants=['transaction.check.delete'],denies=['ledger.post'])
    revoke=dict(user=person['user_id'],company=company,expected_version=1)
    def attempt(name,payload):
        ready.wait();return office.call(office.installer,name,payload)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(attempt,name,payload) for name,payload in (('membership.grant',grant),('membership.revoke',revoke))]
        results=[f.result() for f in futures]
    assert sum(r.status_code==200 for r in results)==1,[r.text for r in results]
    loser=next(i for i,r in enumerate(results) if r.status_code!=200)
    assert results[loser].json()['code'] in ('E_VERSION_CONFLICT','E_DB_BUSY'),results[loser].text
    repeat=office.call(office.installer,('membership.grant','membership.revoke')[loser],(grant,revoke)[loser])
    assert repeat.json()['code']=='E_VERSION_CONFLICT',repeat.text
    assert database(path)==before
    outcome=office.call(clerk,'check.delete',dict(check=post['id'],expected_version=1),company=company,headers={'X-Bookflow-Reason':'After policy race'})
    if results[0].status_code==200:
        assert outcome.status_code==200 and outcome.json()['status']=='deleted',outcome.text
    else:
        assert outcome.json()['code']=='E_COMPANY_NOT_FOUND',outcome.text
        assert database(path)==before
