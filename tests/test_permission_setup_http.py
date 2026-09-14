"""Two-company authenticated setup, write/replay refusal and explicit membership."""
from pathlib import Path
import sqlite3
import time
import pytest
from tests.test_identity_commands import office
from tests.payment_raw_evidence import database


def activate(office):
    state=office.admin('permission.show')
    return office.admin('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))


@pytest.mark.timeout(180)
def test_company_admin_grants_independently_of_post_and_revocation_blocks_replay(office):
    administrator=office.admin('user.add',dict(username='company-admin',password='admin fixture',company=office.first,role='admin'))
    clerk=office.admin('user.add',dict(username='clerk',password='clerk fixture',company=office.first,role='standard'))
    installer=office.admin('user.add',dict(username='install-only',password='installation fixture',hub_admin=True))
    bank=office.admin('account.create',dict(name='Scoped bank',type='bank'),company=office.first)['id']
    expense=office.admin('account.create',dict(name='Scoped supplies',type='expense'),company=office.first)['id']
    activate(office)
    admin_client=office.login_as('company-admin','admin fixture')
    clerk_client=office.login_as('clerk','clerk fixture')
    install_client=office.login_as('install-only','installation fixture')
    assert office.ok(install_client,'company.list')['items']==[]
    blocked=office.call(install_client,'company.show',company=office.first)
    assert blocked.json()['code']=='E_COMPANY_NOT_FOUND'
    raw=dict(account=bank,date='2017-01-01',amount='1.00',expenses=[dict(account=expense,amount='1.00')])
    posted=office.ok(clerk_client,'check.post',raw,company=office.first,headers={'Idempotency-Key':'clerk-once'})
    start=time.monotonic()
    grant=office.ok(admin_client,'membership.grant',dict(user=clerk['user_id'],company=office.first,
        expected_version=1,role='standard',grants=['transaction.check.delete'],denies=['ledger.post']))
    print('HTTP scoped grant seconds',time.monotonic()-start)
    assert grant['grants']==['transaction.check.delete'] and grant['version']==2
    effective=office.ok(admin_client,'membership.effective',dict(company=office.first,user=clerk['user_id']))
    bits={x['requirement']['capability']:x['admitted'] for x in effective['permissions']}
    assert bits['transaction.check.delete'] and bits['ledger.read'] and not bits['ledger.post']
    assert not bits['transaction.card_charge.delete']
    with sqlite3.connect(office.root/'hub.db') as db:
        rel=db.execute('SELECT path FROM companies WHERE id=?',(office.first,)).fetchone()[0]
    path=office.root/rel/'company.db';before=database(path)
    for name,payload,headers in [('check.post',raw,{'Idempotency-Key':'clerk-once'}),
                                 ('check.void',dict(check=posted['id']),{}),
                                 ('check.update',dict(check=posted['id'],memo='must refuse'),{})]:
        refused=office.call(clerk_client,name,payload,company=office.first,headers=headers)
        assert refused.json()['code']=='E_PERMISSION',refused.text
        assert database(path)==before
    assert office.ok(clerk_client,'check.show',dict(check=posted['id']),company=office.first)['id']==posted['id']
    refused=office.call(admin_client,'membership.grant',dict(user=clerk['user_id'],company=office.second,role='standard'))
    assert refused.json()['code']=='E_COMPANY_NOT_FOUND'
    stale=office.call(admin_client,'membership.revoke',dict(user=clerk['user_id'],company=office.first,expected_version=1))
    assert stale.json()['code']=='E_VERSION_CONFLICT'
    office.ok(admin_client,'membership.revoke',dict(user=clerk['user_id'],company=office.first,expected_version=2))
    assert office.call(clerk_client,'check.show',dict(check=posted['id']),company=office.first).json()['code']=='E_COMPANY_NOT_FOUND'
    assert database(path)==before
