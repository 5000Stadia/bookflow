"""Invoice settlement corrections have a full invoice receipt, not a payment DTO."""
from pathlib import Path
import copy,json,sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.core.host import Host
from bookflow.core.config import os_login
from bookflow.core.context import Context,client_version
from bookflow.core.publication import OSBinding,PublicationPermit
from bookflow.adapters.http.execution import run_history
from bookflow.hub.audit_projection import HistorySelection
from bookflow.hub.audit_projection_legacy import decode_company_snapshot
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def corrected(world):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_payment_receipts import posted,method
    client=world['client'];company='Demo Plumbing Co';sales=sale.__wrapped__(client)
    invoice=posted(client,sales['customer'],sales['item'],'1','Invoice audit correction')
    payment=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',amount='0.50',
        operation_key='invoice-audit-receive',payment_method=method(client),applications=dict(mode='inline',items=[
            dict(invoice=invoice['id'],expected_version=1,amount='0.50')])),company=company)
    args=dict(invoice=invoice['id'],expected_version=2,operation_key='invoice-audit-correct',
        settlement_versions=[dict(payment=payment['id'],expected_version=1)],lines=[
            dict(line_id=invoice['revision']['lines'][0]['line_id'],item=sales['item'],quantity='1',unit_price='1'),
            dict(item=sales['item'],quantity='1',unit_price='1')])
    result=client.run('invoice update',args,reason='Add omitted work',company=company)
    client.run('invoice update',dict(invoice=invoice['id'],expected_version=3,operation_key='invoice-audit-later',
        settlement_versions=[dict(payment=payment['id'],expected_version=2)],memo='Later display'),reason='Update memo',company=company)
    row=client.company.show(company=company);path=Path(row['path'])/'company.db';event=result['settlement']['effect']['audit_event_id']
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=[(kind,decode_snapshot(blob)) for kind,blob in db.execute('SELECT record_type,after FROM audit_entries WHERE event_id=? AND record_type IN (?,?)',(event,'payment_operation','payment_operation_item'))]
    host=Host(world['root'],version=client_version());host.start()
    try:yield host,OSBinding.capture(host,os_login()),row['company_id'],path,event,rows,result,sales
    finally:host.stop()


def test_invoice_correction_item_contains_invoice_and_payment_balances(corrected):
    _,_,_,path,_,rows,_,_=corrected;before=storage(path)
    documents=[]
    for kind,raw in rows:
        if kind!='payment_operation_item':continue
        view=decode_company_snapshot(producer='invoice update',record_type=kind,action='create',snapshot=raw)
        expected=copy.deepcopy(raw)
        if raw['kind']=='document_changes':
            for key in ('settlement_guard','audit_watermark'):expected['item_snapshot'].pop(key,None)
            documents.append(view.item_snapshot.model_dump(mode='json'))
        actual=view.model_dump(mode='json');actual.pop('tag');assert actual==expected
    assert len(documents)==2
    invoice=next(x for x in documents if 'invoice_id' in x);payment=next(x for x in documents if 'payment_id' in x)
    assert (invoice['gross_minor_units'],invoice['applied_minor_units'],invoice['due_minor_units'])==(200,50,150)
    assert (payment['received_minor_units'],payment['applied_minor_units'],payment['available_minor_units'])==(50,50,0)
    assert storage(path)==before


def expected_receipt(raw):
    value=copy.deepcopy(raw)
    for key in ('request_hash','created_at','created_by','created_via'):value.pop(key)
    request=value['request_snapshot'];request.pop('expanded_selection_hash')
    original=request['original_request'];original['context']={};original['context_provided_fields']=[]
    for key in ('expected_facts_fingerprint','settlement_guard'):
        original['input'].pop(key,None)
        if key in original['provided_fields']:original['provided_fields'].remove(key)
    for key in ('directive_code','directive_id','reason'):value['execution_snapshot'].pop(key)
    effect=value['effect_snapshot']
    for key in ('void_reason','facts_fingerprint'):effect.pop(key,None)
    # Existing audit custom-value projection is a closed ordered collection.
    fields=effect['revision']['custom_fields_snapshot']
    effect['revision']['custom_fields_snapshot']={'values':[fields[k] for k in sorted(fields)]}
    for section in (effect.get('settlement_current'),effect['settlement']['current'],*effect['settlement']['effect']['document_changes']):
        if section is not None:
            for key in ('settlement_guard','audit_watermark'):section.pop(key,None)
    for capture in (effect['revision']['profile'], *(line['item_snapshot'] for line in effect['revision']['lines'])):
        origins=capture['origins']
        capture['origins']={'values':[{'field':key,'origin':origins[key]} for key in sorted(origins)]}
    effect['settlement'].pop('facts_fingerprint')
    return value


def test_invoice_correction_history_retains_full_original_invoice(corrected):
    host,cred,cid,path,event,rows,result,_=corrected;before=storage(path)
    out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','invoice-history'),cred)
    view=next(x['after'] for x in out['events'][0]['entries'] if x['identity']['kind']=='payment_operation')
    raw=next(raw for kind,raw in rows if kind=='payment_operation')
    actual=dict(view);actual.pop('tag');assert actual==expected_receipt(raw)
    assert actual['effect_snapshot']['version']==3 and actual['effect_snapshot']['revision']['revision_number']==2
    assert len(actual['effect_snapshot']['revision']['lines'])==2
    assert actual['effect_snapshot']['total']['minor_units']==200
    PublicationPermit.from_retained(out.permit.retained()).check(host,cred)
    assert storage(path)==before and host._readers_attached==0


@pytest.mark.parametrize('damage', ['unknown_input','wrong_invoice','wrong_operation','null_item'])
def test_invoice_correction_rejects_invalid_capture(corrected,damage):
    from bookflow.core.errors import BookflowError
    raw=copy.deepcopy(next(raw for kind,raw in corrected[5] if kind=='payment_operation'))
    if damage=='unknown_input':raw['request_snapshot']['original_request']['input']['unowned_field']='bad'
    elif damage=='wrong_invoice':raw['effect_snapshot']['settlement']['effect']['invoice_id']='different-invoice'
    elif damage=='wrong_operation':raw['effect_snapshot']['settlement']['effect']['operation_id']='different-operation'
    else:raw['effect_snapshot']['revision']['lines'][0]['item_id']=None
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer='invoice update',record_type='payment_operation',action='create',snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}


def test_invoice_edit_history_masks_currently_denied_customer_and_item(corrected,world):
    from bookflow.core import identity_admin_binding as binding
    from bookflow.core.config import Config
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    host,cred,cid,path,event,_,_,_=corrected;before=storage(path)
    uid=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
    def deny():
        with host._commit_hooks.operation('dispatch.apply',host._hub):
            with binding.hosted_operation(host,cred,request_id=CONTEXT.request_id,purpose='apply') as operation:
                operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('customer','item')),audit=CONTEXT)
                host._commit_hooks.commit(host._hub,'dispatch.apply')
    host.submit(deny)
    current=OSBinding.capture(host,os_login())
    out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','masked-invoice-edit'),current)
    view=next(x['after'] for x in out['events'][0]['entries'] if x['identity']['kind']=='payment_operation')
    result=view['effect_snapshot']
    assert result['customer_id'] is None and result['customer_name'] is None
    assert result['total']['minor_units']==200
    assert result['settlement']['current']['due_minor_units']==150
    assert len(result['revision']['lines'])==2
    for line in result['revision']['lines']:
        assert line['item_id'] is None and line['item_snapshot']['item'] is None
    for line in view['request_snapshot']['original_request']['input']['lines']:
        assert line['item'] is None and line['unit_price']=='1'
    PublicationPermit.from_retained(out.permit.retained()).check(host,current)
    assert storage(path)==before and host._readers_attached==0
