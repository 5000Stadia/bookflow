"""Exact native bill money, revision evidence and independent reviewer authority."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.conftest import make_actor, as_user
from tests.test_row8_journal import COMPANY, database_path
from tests.test_purchase_drafts import financial

SURFACES = ('python',)


def run(client, name, data, **kw):
    return client.run(name, data, company=COMPANY, reason='Bill approval witness' if name.split()[-1] not in ('show','list','history','query') else None, **kw)


def setup(root, client):
    company = client.company.show(company=COMPANY)['company_id']
    reviewer = make_actor(root,'bill_reviewer',company_role=(company,'admin'))
    other = as_user(root,'bill_reviewer')
    vendor=client.vendor.create(name='Approval supplier',company=COMPANY)['id']
    expense=client.account.create(name='Approval supplies',type='expense',company=COMPANY)['id']
    payload=dict(date='2026-06-15',vendor=vendor,expenses=[dict(account=expense,amount='80.00')])
    policy=run(client,'approval-policy create',dict(eligible_members=[reviewer],operation_key='policy'))
    return other,payload,policy


def publish(client, policy):
    return run(client,'approval-policy publish',dict(policy=policy['id'],expected_version=policy['version'],operation_key='publish-'+str(policy['version']),confirm=True))


def saved(client,payload,key='draft',**kw):
    return run(client,'purchase-draft create',dict(payload,operation_key=key,**kw))


def approve(client,other,draft,key='submit'):
    req=run(client,'purchase-draft submit',dict(draft=draft['id'],expected_version=draft['version'],operation_key=key))
    return run(other,'approval decide',dict(request=req['id'],expected_version=req['version'],decision='approve',operation_key='decide-'+key))


def commit(client,draft,req=None,key='commit'):
    current=run(client,'purchase-draft show',dict(draft=draft['id']))
    return run(client,'purchase-draft commit',dict(draft=draft['id'],expected_version=current['version'],
        expected_request_version=req['version'] if req else None,operation_key=key))


def test_simple_fixture_and_native_gate(root,client):
    other,payload,policy=setup(root,client)
    legacy=run(client,'bill post',payload)
    assert legacy['total_minor_units']==8000
    policy=run(client,'approval-policy update',dict(policy=policy['id'],expected_version=1,
        eligible_members=policy['eligible_members'],operation_key='policy-update'))
    assert run(client,'approval-policy show',dict(policy=policy['id']))['version']==2
    assert policy['id'] in [p['id'] for p in run(client,'approval-policy list',{})['items']]
    publish(client,policy)
    with pytest.raises(BookflowError) as refused:
        run(client,'bill post',payload)
    assert refused.value.code=='E_APPROVAL_REQUIRED'
    assert refused.value.details['command']=='bill post'
    with pytest.raises(BookflowError) as refused:
        run(client,'bill update',dict(bill=legacy['id'],expected_version=legacy['version'],memo='bypass'))
    assert refused.value.code=='E_APPROVAL_REQUIRED'
    before=financial(client)
    draft=saved(client,payload)
    req=approve(client,other,draft)
    assert financial(client)==before
    assert run(client,'approval show',dict(request=req['id']))['state']=='approved'
    assert run(client,'approval history',dict(request=req['id']))['events'][-1]['action']=='approve'
    current=run(client,'purchase-draft show',dict(draft=draft['id']))
    withdrawn=run(client,'purchase-draft withdraw',dict(draft=draft['id'],expected_version=current['version'],
        expected_request_version=req['version'],operation_key='withdraw',confirm=True))
    req=approve(client,other,withdrawn,'resubmit')
    before_commit=run(client,'purchase-draft show',dict(draft=draft['id']))
    result=commit(client,draft,req)
    retry=run(client,'purchase-draft commit',dict(draft=draft['id'],expected_version=before_commit['version'],expected_request_version=req['version'],operation_key='commit'))
    assert retry['idempotent_replay'] and retry['document_id']==result['document_id']
    bill=run(client,'bill show',dict(bill=result['document_id']))
    assert bill['total_minor_units']==8000 and bill['settlement_current']['open_minor_units']==8000
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM batch_effects WHERE batch_id=?',(result['batch_id'],)).fetchone()==(1,)
        legs=db.execute('SELECT debit_minor_units,credit_minor_units FROM posting_lines WHERE transaction_id=?',(bill['id'],)).fetchall()
        assert sum(x[0] for x in legs)==sum(x[1] for x in legs)==8000
    preview=run(client,'batch reverse',dict(batch=result['batch_id']),dry_run=True)
    assert preview
    run(client,'batch reverse',dict(batch=result['batch_id']))
    assert run(client,'bill show',dict(bill=bill['id']))['status']=='voided'
    assert run(client,'purchase-draft show',dict(draft=draft['id']))['state']=='approved'
    req=run(client,'approval show',dict(request=req['id']))
    result=commit(client,draft,req,key='recommit')
    bank=client.account.create(name='Approval bank',type='bank',company=COMPANY)['id']
    run(client,'bill pay',dict(date='2026-06-16',funding_account=bank,method='Check',
        bills=[dict(bill=result['document_id'],amount='30.00',expected_version=result['document_version'])]))
    bill=run(client,'bill show',dict(bill=result['document_id']))
    assert bill['settlement_current']['open_minor_units']==5000
    before=financial(client)
    with pytest.raises(BookflowError) as paid:
        run(client,'batch reverse',dict(batch=result['batch_id']))
    assert paid.value.code=='E_BATCH_BLOCKED'
    current=run(client,'purchase-draft show',dict(draft=draft['id']))
    with pytest.raises(BookflowError) as committed:
        run(client,'purchase-draft withdraw',dict(draft=draft['id'],expected_version=current['version'],
            expected_request_version=current['request_version'],operation_key='withdraw-committed',confirm=True))
    assert committed.value.code=='E_APPROVAL_STATE'
    assert financial(client)==before
    cancelled=saved(client,payload,'cancel-draft')
    before=financial(client)
    run(client,'purchase-draft cancel',dict(draft=cancelled['id'],expected_version=1,operation_key='cancel'))
    assert financial(client)==before


def test_revision_races_retry_closed_period_and_restore_dependency(root,client):
    other,payload,policy=setup(root,client)
    publish(client,policy)
    draft=saved(client,payload)
    req=run(client,'purchase-draft submit',dict(draft=draft['id'],expected_version=1,operation_key='submit'))
    args=dict(request=req['id'],expected_version=1,decision='approve',operation_key='approve')
    approved=run(other,'approval decide',args)
    assert run(other,'approval decide',args)['idempotent_replay']
    with pytest.raises(BookflowError) as race:
        run(other,'approval decide',dict(args,decision='reject',operation_key='race'))
    assert race.value.code=='E_VERSION_CONFLICT'
    with pytest.raises(BookflowError) as edit:
        run(client,'purchase-draft update',dict(payload,draft=draft['id'],expected_version=3,operation_key='edit'))
    assert edit.value.code=='E_APPROVAL_STATE'
    info=client.company.show(company=COMPANY)
    client.company.update(company=COMPANY,expected_version=info['version'],closing_date='2026-06-30',reason='Close period')
    before=financial(client)
    with pytest.raises(BookflowError) as closed:
        commit(client,draft,approved)
    assert closed.value.code=='E_PERIOD_CLOSED'
    assert financial(client)==before
    with pytest.raises(BookflowError) as restore:
        saved(client,payload,'restore',operation='restore',original_id='missing',original_version=1)
    assert restore.value.code=='E_APPROVAL_STATE'


def test_approved_correction_and_exact_inverse(root,client):
    other,payload,policy=setup(root,client)
    original=run(client,'bill post',payload)
    publish(client,policy)
    line_id=original['revision']['expenses'][0]['line_id']
    changed=dict(payload,expenses=[dict(payload['expenses'][0],amount='90.00',line_id=line_id)])
    draft=saved(client,changed,operation='correct',original_id=original['id'],original_version=original['version'])
    draft=run(client,'purchase-draft update',dict(changed,draft=draft['id'],expected_version=1,operation_key='edit-correction'))
    assert draft['operation']=='correct' and draft['original_id']==original['id']
    req=approve(client,other,draft)
    replacement=run(client,'approval-policy update',dict(policy=policy['id'],expected_version=2,
        eligible_members=policy['eligible_members'],enabled=False,operation_key='disable-policy'))
    publish(client,replacement)
    with pytest.raises(BookflowError) as bypass:
        run(client,'bill update',dict(bill=original['id'],expected_version=original['version'],memo='Bypass captured approval'))
    assert bypass.value.code=='E_APPROVAL_REQUIRED'
    alternate=saved(client,changed,'alternate-correction',operation='correct',original_id=original['id'],original_version=original['version'])
    with pytest.raises(BookflowError) as draft_bypass:
        run(client,'purchase-draft commit',dict(draft=alternate['id'],expected_version=alternate['version'],operation_key='bypass-correction'))
    assert draft_bypass.value.code=='E_APPROVAL_REQUIRED'
    result=commit(client,draft,req)
    assert run(client,'bill show',dict(bill=original['id']))['total_minor_units']==9000
    run(client,'batch reverse',dict(batch=result['batch_id']))
    restored=run(client,'bill show',dict(bill=original['id']))
    assert restored['status']=='posted' and restored['total_minor_units']==8000
    after=run(client,'purchase-draft show',dict(draft=draft['id']))
    assert after['original_version']==restored['version']
