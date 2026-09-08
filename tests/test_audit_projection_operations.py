"""Real permanent payment operations retain their recorded intent and result."""
from pathlib import Path
import copy,sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.core.context import Context,client_version
from bookflow.core.config import os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.hub.audit_projection import HistorySelection
from bookflow.adapters.http.execution import run_history
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def operations(world):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_payment_receipts import method,posted
    client=world['client'];company='Demo Plumbing Co';sales=sale.__wrapped__(client)
    invoice=posted(client,sales['customer'],sales['item'],'2','Operation header invoice')
    received=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',amount='1.50',
        payment_method=method(client),operation_key='history-receive',applications=dict(mode='inline',items=[
            dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=company)
    applied=client.run('payment apply',dict(payment=received['id'],expected_version=1,date='2026-06-03',
        operation_key='history-apply',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=2,amount='0.50')])),company=company)
    unapplied=client.run('payment unapply',dict(payment=received['id'],expected_version=applied['version'],
        operation_key='history-unapply',applications=[dict(application_id=result['effect']['applications'][0]['application_id'],
            invoice_expected_version=3) for result in (received,applied)]),reason='Correct allocation',company=company)
    corrected=client.run('payment update',dict(payment=received['id'],expected_version=unapplied['version'],
        operation_key='history-update',amount='2',memo='Correct remittance'),reason='Correct remittance',company=company)
    client.run('payment void',dict(payment=received['id'],expected_version=corrected['version'],operation_key='history-void'),reason='Cancel receipt',company=company)
    row=client.company.show(company=company);path=Path(row['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        captures={command:(event,decode_snapshot(blob)) for command,event,blob in db.execute("SELECT a.command,a.id,e.after FROM audit_events a JOIN audit_entries e ON e.event_id=a.id WHERE e.record_type='payment_operation' AND a.command LIKE 'payment %'")}
    assert set(captures)=={'payment '+kind for kind in ('receive','apply','unapply','update','void')}
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,OSBinding.capture(host,os_login()),row['company_id'],path,captures
    finally:host.stop()


def public_capture(raw):
    value=copy.deepcopy(raw)
    value.pop('request_hash')
    for key in ('created_at','created_by','created_via'):value.pop(key)
    value['request_snapshot'].pop('expanded_selection_hash')
    value['effect_snapshot'].pop('facts_fingerprint')
    for key in ('directive_code','directive_id','reason'):value['execution_snapshot'].pop(key)
    request=value['request_snapshot']['original_request']
    request['context'].pop('reason',None)
    request['context_provided_fields']=[]
    inp=value['request_snapshot']['original_request']['input']
    for key in ('expected_facts_fingerprint','settlement_guard'):inp.pop(key,None)
    for doc in value['effect_snapshot']['effect']['document_changes']:
        for key in ('settlement_guard','audit_watermark'):doc.pop(key,None)
    return value


@pytest.mark.parametrize('kind',['receive','apply','unapply','update','void'])
def test_actual_operation_history_preserves_original_after_later_changes(operations,kind):
    host,cred,cid,path,captures=operations;before=storage(path)
    event,raw=captures['payment '+kind]
    result=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','operation-history'),cred)
    entry=next(x for x in result['events'][0]['entries'] if x['identity']['kind']=='payment_operation')
    actual=dict(entry['after']);actual.pop('tag')
    assert actual==public_capture(raw)
    assert actual['effect_snapshot']['effect']['kind']==kind
    assert actual['effect_snapshot']['current']['status']==('voided' if kind=='void' else 'posted')
    PublicationPermit.from_retained(result.permit.retained()).check(host,cred)
    assert storage(path)==before and host._readers_attached==0


@pytest.mark.parametrize('fault',['unknown_input','wrong_presence','wrong_command','wrong_operation','invalid_date'])
def test_corrupt_operation_request_is_not_a_valid_history(operations,fault):
    from bookflow.hub.audit_projection_legacy import decode_company_snapshot
    from bookflow.core.errors import BookflowError
    _,_,_,path,captures=operations;before=storage(path)
    raw=copy.deepcopy(captures['payment receive'][1]);request=raw['request_snapshot']['original_request']
    if fault=='unknown_input':request['input']['invented']=True
    elif fault=='wrong_presence':request['provided_fields'].append('memo')
    elif fault=='wrong_command':request['command']='payment apply'
    elif fault=='wrong_operation':raw['effect_snapshot']['effect']['operation_id']='foreign'
    else:request['input']['date']='not-a-date'
    with pytest.raises(BookflowError) as caught:
        decode_company_snapshot(producer='payment receive',record_type='payment_operation',action='create',snapshot=raw)
    assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    assert storage(path)==before


def test_real_customer_denial_masks_operation_intent_and_effect(operations,world):
    from bookflow.core import identity_admin_binding as binding
    from bookflow.core.config import Config
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    host,cred,cid,path,captures=operations;before=storage(path)
    uid=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
    def deny():
        with host._commit_hooks.operation('dispatch.apply',host._hub):
            with binding.hosted_operation(host,cred,request_id=CONTEXT.request_id,purpose='apply') as operation:
                operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('customer',)),audit=CONTEXT)
                host._commit_hooks.commit(host._hub,'dispatch.apply')
    host.submit(deny)
    current=OSBinding.capture(host,os_login());event,raw=captures['payment receive']
    result=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','masked-operation'),current)
    entry=next(x for x in result['events'][0]['entries'] if x['identity']['kind']=='payment_operation')['after']
    request=entry['request_snapshot']['original_request']
    assert request['input']['customer'] is None and 'customer' not in request['provided_fields']
    assert request['input']['amount']=='1.50'
    component=entry['effect_snapshot']['current']['components'][0]
    assert component['party_id'] is None and component['party_name'] is None
    assert component['received_minor_units']==150 and component['available_minor_units']==50
    PublicationPermit.from_retained(result.permit.retained()).check(host,current)
    assert storage(path)==before and host._readers_attached==0


def test_nullable_projection_does_not_accept_null_original_profiles(operations):
    from bookflow.hub.audit_projection_legacy import decode_company_snapshot
    from bookflow.core.errors import BookflowError
    _,_,_,path,captures=operations;before=storage(path)
    event,_=captures['payment receive']
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT record_type,after FROM audit_entries WHERE event_id=? AND record_type IN ('payment_profile','payment_component')",(event,)).fetchall()
    assert {kind for kind,_ in rows}=={'payment_profile','payment_component'}
    for kind,blob in rows:
        raw=decode_snapshot(blob)
        field,required=('profile_snapshot',('payer','lineage','billing_address','payment_method')) if kind=='payment_profile' else ('component_snapshot',('party','lineage'))
        decode_company_snapshot(producer='payment receive',record_type=kind,action='create',snapshot=raw)
        for name in required:
            bad=copy.deepcopy(raw);bad[field][name]=None
            with pytest.raises(BookflowError) as caught:
                decode_company_snapshot(producer='payment receive',record_type=kind,action='create',snapshot=bad)
            assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    assert storage(path)==before
