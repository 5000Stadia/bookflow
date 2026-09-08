"""Invoice-edit captures retain real taxed billing and custom-value facts."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.hub.audit_projection_legacy import decode_company_snapshot
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def billed_edit(world):
    from tests.test_service_sales_lifecycle import sale,COMPANY
    from tests.test_tax_policy_sales import tax_sale,request
    from tests.test_work_billing_lifecycle import accepted,bill
    from tests.test_payment_receipts import method
    client=world['client'];sales=tax_sale.__wrapped__(client,sale.__wrapped__(client))
    field=client.run('custom-field create',dict(name='Audit billing approval',kind='bool',scopes=['estimate','invoice']),company=COMPANY)
    source=accepted(client,sales,**request(sales,'invoice_combined_half_up',nets=('1.50','1.50')),custom_fields={field['id']:False})
    invoice=bill(client,source,'audit-tax-bill')
    payment=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',amount='1.00',
        operation_key='audit-tax-payment',payment_method=method(client),applications=dict(mode='inline',items=[
            dict(invoice=invoice['id'],expected_version=1,amount='1.00')])),company=COMPANY)
    edited=client.run('invoice update',dict(invoice=invoice['id'],expected_version=2,operation_key='audit-tax-edit',
        settlement_versions=[dict(payment=payment['id'],expected_version=1)],memo='Approved billing',custom_fields={field['id']:True}),reason='Confirm approval',company=COMPANY)
    company=client.company.show(company=COMPANY);path=Path(company['path'])/'company.db'
    event=edited['settlement']['effect']['audit_event_id']
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT record_type,action,before,after FROM audit_entries WHERE event_id=?',(event,)).fetchall()
    return world['root'],company['company_id'],path,event,rows,edited,sales,field['id']


def test_real_tax_billing_and_custom_edit_captures_decode(billed_edit):
    _,_,path,_,rows,edited,_,field=billed_edit;before=storage(path)
    assert edited['tax_minor_units']==30 and edited['total_minor_units']==330
    assert edited['revision']['billing_sources']
    assert edited['revision']['custom_fields_snapshot'][field]['value'] is True
    for kind,action,old,new in rows:
        for blob in (old,new):
            if blob is not None:
                decode_company_snapshot(producer='invoice update',record_type=kind,action=action,snapshot=decode_snapshot(blob))
    assert storage(path)==before


def test_tax_billing_custom_history_retains_original_values(billed_edit):
    from bookflow.core.host import Host
    from bookflow.core.config import os_login
    from bookflow.core.context import Context,client_version
    from bookflow.core.publication import OSBinding,PublicationPermit
    from bookflow.hub.audit_projection import HistorySelection
    from bookflow.adapters.http.execution import run_history
    root,cid,path,event,_,edited,_,field=billed_edit;before=storage(path)
    host=Host(root,version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','tax-billing-history'),cred)
        result=next(x['after']['effect_snapshot'] for x in out['events'][0]['entries'] if x['identity']['kind']=='payment_operation')
        assert result['tax_minor_units']==30 and result['total_minor_units']==330
        assert result['settlement']['current']['due_minor_units']==230
        assert result['revision']['tax_calculation_details']==edited['revision']['tax_calculation_details']
        assert result['revision']['billing_sources']
        assert next(v for v in result['revision']['custom_fields_snapshot']['values'] if v['definition_id']==field)['value'] is True
        for actual,original in zip(result['revision']['lines'],edited['revision']['lines'],strict=True):
            assert actual['tax_components']==original['tax_components']
        PublicationPermit.from_retained(out.permit.retained()).check(host,cred)
        assert storage(path)==before and host._readers_attached==0
    finally:host.stop()


def test_tax_custom_references_follow_current_denials(billed_edit):
    from bookflow.core.host import Host
    from bookflow.core.config import os_login,Config
    from bookflow.core.context import Context,client_version
    from bookflow.core.publication import OSBinding,PublicationPermit
    from bookflow.hub.audit_projection import HistorySelection
    from bookflow.adapters.http.execution import run_history
    from bookflow.core import identity_admin_binding as binding
    from bookflow.hub import identity_admin as admin
    from bookflow.hub.permission_catalog import ScopeKey
    from tests.permission_admin_support import CONTEXT
    root,cid,path,event,_,_,_,_=billed_edit;before=storage(path)
    host=Host(root,version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        uid=Config.load(root/'config.toml').user_table(os_login())['user_id']
        def deny():
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                with binding.hosted_operation(host,cred,request_id=CONTEXT.request_id,purpose='apply') as operation:
                    operation.apply(admin.PutMembership(uid,ScopeKey('company',cid),admin.Version(1),'owner',denies=('vendor','item','custom-field','account')),audit=CONTEXT)
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
        host.submit(deny)
        current=OSBinding.capture(host,os_login())
        out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','tax-custom-denied'),current)
        result=next(x['after']['effect_snapshot'] for x in out['events'][0]['entries'] if x['identity']['kind']=='payment_operation')
        assert result['total_minor_units']==330 and result['tax_minor_units']==30
        revision=result['revision']
        assert revision['custom_fields'] is None and revision['custom_fields_snapshot']['values'] is None
        for line in revision['lines']:
            assert line['item_id'] is None
            for component in line['tax_components']:
                assert component['tax_item_id'] is None and component['agency_id'] is None and component['liability_account_id'] is None
        tax=revision['tax_calculation_details']['attribution']['calculation']
        assert tax['tax_minor_units']==30
        assert all(t['rule']['agency'] is None for source in revision['billing_sources'] for t in source['facts_snapshot']['line']['taxes'])
        assert all(cell['rule']['agency'] is None for bucket in tax['buckets'] for cell in bucket['cells'])
        assert all(v['agency_id'] is None and v['liability_account_id'] is None for v in tax['liabilities'])
        assert all(v['liability_account_id'] is None for v in tax['accounts'])
        PublicationPermit.from_retained(out.permit.retained()).check(host,current)
        assert storage(path)==before and host._readers_attached==0
    finally:host.stop()


@pytest.mark.parametrize('family',['work_tax','calculated_tax'])
def test_actual_tax_agency_capture_rejects_explicit_null(billed_edit,family):
    import copy,json
    from pydantic import ValidationError
    from bookflow.hub.audit_projection_legacy import TaxRule,CalculatedTaxRule
    _,_,path,_,rows,_,_,_=billed_edit;before=storage(path)
    raw=decode_snapshot(next(new for kind,_,_,new in rows if kind=='payment_operation'))
    revision=raw['effect_snapshot']['revision']
    if family=='work_tax':
        captured=revision['billing_sources'][0]['facts_snapshot']['line']['taxes'][0]['rule'];model=TaxRule
    else:
        captured=revision['tax_calculation_details']['attribution']['calculation']['buckets'][0]['cells'][0]['rule'];model=CalculatedTaxRule
    model.model_validate_json(json.dumps(captured))
    damaged=copy.deepcopy(captured);damaged['agency']=None
    with pytest.raises(ValidationError):model.model_validate_json(json.dumps(damaged))
    assert storage(path)==before
