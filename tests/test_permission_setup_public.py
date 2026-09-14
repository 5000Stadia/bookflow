"""Registered setup and ordinary financial admission, using a safe demo fixture."""
import sqlite3
import time
import pytest
from bookflow.core.errors import BookflowError
from bookflow.core.config import Config, os_login
from tests.payment_raw_evidence import database as snapshot


def test_public_activation_grant_deny_and_version(root,client):
    company = client.company.list()['items'][0]['company_id']
    person = client.user.add(username='purchase-clerk',password='fixture password',company=company)
    state = client.permission.show()
    before = snapshot(root/'hub.db')
    preview = client.permission.activate(expected_generation=state['generation'],
        expected_catalog_sha256=state['catalog_sha256'],dry_run=True)
    assert preview['changed'] and preview['dry_run']
    assert snapshot(root/'hub.db') == before
    result = client.permission.activate(expected_generation=state['generation'],
        expected_catalog_sha256=state['catalog_sha256'])
    assert result['mode']=='policy_v1'
    before = snapshot(root/'hub.db')
    with pytest.raises(BookflowError) as blind:
        client.membership.grant(user=person['user_id'],company=company,grants=['transaction.check.delete'])
    assert blind.value.code=='E_VALIDATION' and snapshot(root/'hub.db')==before
    start=time.monotonic()
    granted = client.membership.grant(user=person['user_id'],company=company,expected_version=1,
        grants=['transaction.check.delete'],denies=['ledger.post'])
    print('membership grant seconds',time.monotonic()-start)
    assert granted['version']==2 and granted['grants']==['transaction.check.delete'] and granted['denies']==['ledger.post']
    before = snapshot(root/'hub.db')
    with pytest.raises(BookflowError) as stale:
        client.membership.grant(user=person['user_id'],company=company,expected_version=1,role='readonly')
    assert stale.value.code=='E_VERSION_CONFLICT'
    assert snapshot(root/'hub.db')==before
    effective = client.membership.effective(company=company,user=person['user_id'])
    bits = {x['requirement']['capability']:x['admitted'] for x in effective['permissions']}
    assert bits['transaction.check.delete'] and not bits['transaction.card_charge.delete'] and not bits['ledger.post']
    # Role-only edit preserves both explicitly configured grids.
    preserved = client.membership.grant(user=person['user_id'],company=company,expected_version=2,role='admin')
    assert preserved['grants']==granted['grants'] and preserved['denies']==granted['denies']
