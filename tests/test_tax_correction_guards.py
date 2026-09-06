"""Reachable paid-tax date/role guards using ordinary commands and existing actors.

One closing cutoff and invoice<=application dates make an open invoice with a
closed active application unreachable. Resource permissions here are company-role
based: no fixture invents a selective customer-work denial or disables a guard.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from bookflow import BookflowError
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version
from tests.conftest import make_actor, as_user
from tests.test_tax_policy_sales import sale, tax_sale, request, COMPANY
from tests.test_service_sales_lifecycle import snapshot
from tests.test_row8_journal import database_path, assert_oracle
from tests.test_work_billing_lifecycle import accepted, bill


@pytest.mark.parametrize('case,closing,replacement,code', [
    ('old','2026-06-01',None,'E_PERIOD_CLOSED'),
    ('replacement','2026-05-31','2026-05-31','E_PERIOD_CLOSED'),
    ('application-and-old','2026-06-02',None,'E_PERIOD_CLOSED'),
    ('after-application',None,'2026-06-03','E_HAS_APPLICATIONS'),
    ('stale-invoice',None,None,'E_VERSION_CONFLICT'),
    ('stale-payment',None,None,'E_VERSION_CONFLICT'),
])
@pytest.mark.parametrize('policy',['line_combined_half_up','invoice_combined_half_up'])
def test_paid_policy_correction_guard_is_atomic(client,tax_sale,tmp_path,case,closing,replacement,code,policy):
    invoice=client.run('invoice post',request(tax_sale,'line_component_half_even'),company=COMPANY)
    paid=client.run('payment receive',dict(customer=tax_sale['customer'],date='2026-06-02',amount='0.10',
        payment_method='Check',operation_key='tax-guard-cash',applications=dict(mode='inline',items=[
            dict(invoice=invoice['id'],expected_version=1,amount='0.10')])),company=COMPANY)
    if closing:client.run('company update',dict(closing_date=closing),company=COMPANY)
    args=dict(invoice=invoice['id'],expected_version=1 if case=='stale-invoice' else 2,
        operation_key='guarded-tax-correction',sales_tax_calculation=policy,
        settlement_versions=[dict(payment=paid['id'],expected_version=99 if case=='stale-payment' else 1)])
    if replacement:args['date']=replacement
    before=snapshot(client)
    receipts=[]
    for preview in (True,False):
        with pytest.raises(BookflowError) as exc:
            client.run('invoice update',args,reason='Correct captured tax policy',company=COMPANY,dry_run=preview)
        receipts.append(dict(preview=preview,code=exc.value.code,details=exc.value.details))
        assert exc.value.code==code
        assert snapshot(client)==before  # every company table, including profiles/tax/history/audit/operations
    (tmp_path/'guard-receipts.json').write_text(json.dumps(receipts,indent=2))


def test_hosted_paid_tax_composite_roles_denial_and_authorized_restating(client,tax_sale,root,tmp_path):
    invoice=client.run('invoice post',request(tax_sale,'line_component_half_even'),company=COMPANY)
    linked=bill(client,accepted(client,tax_sale,**request(tax_sale,'line_component_half_even')),'guard-linked')
    paid=client.run('payment receive',dict(customer=tax_sale['customer'],date='2026-06-02',amount='0.40',
        payment_method='Check',operation_key='tax-composite-cash',applications=dict(mode='inline',items=[
            dict(invoice=i['id'],expected_version=1,amount='0.20') for i in (invoice,linked)])),company=COMPANY)
    company=client.company.show(company=COMPANY)['id']
    tokens={}
    for role in ('readonly','standard'):
        login='tax-composite-'+role
        make_actor(root,login,company_role=(company,role))
        tokens[role]=as_user(root,login).token.issue(label='Tax correction fixture')['secret']
    args=dict(invoice=invoice['id'],expected_version=2,operation_key='tax-composite-correction',
        sales_tax_calculation='invoice_combined_half_up',settlement_versions=[dict(payment=paid['id'],expected_version=1)])
    path_on_disk=database_path(client)
    def hosted_snapshot():
        with sqlite3.connect(path_on_disk.as_uri()+'?mode=ro',uri=True) as db:
            db.execute('BEGIN')
            names=[row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name:db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall() for name in names}
    before=hosted_snapshot()
    with sqlite3.connect(path_on_disk) as db:
        cash=db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()
    handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False,publish_descriptor=False)
    receipts=[]
    try:
        with TestClient(handle.app) as api:
            path=f'/companies/{company}/commands/invoice.update'
            for preview in (True,False):
                denied=api.post(path+('?dry_run=true' if preview else ''),json=args,
                    headers={'Authorization':'Bearer '+tokens['readonly'],'X-Bookflow-Reason':'Correct captured tax policy'})
                receipts.append({'preview':preview,'status':denied.status_code,'body':denied.json()})
                assert denied.status_code==403 and denied.json()['code']=='E_PERMISSION'
                assert hosted_snapshot()==before
            headers={'Authorization':'Bearer '+tokens['standard'],'X-Bookflow-Reason':'Correct captured tax policy'}
            preview=api.post(path+'?dry_run=true',json=args,headers=headers)
            assert preview.status_code==200,preview.text
            assert hosted_snapshot()==before
            revised=api.post(path,json=dict(args,expected_facts_fingerprint=preview.json()['facts_fingerprint']),headers=headers)
            assert revised.status_code==200,revised.text
            value=revised.json()
            assert value['subtotal_minor_units']==20 and value['tax_minor_units']==2 and value['total_minor_units']==22
            assert value['settlement']['current']['due_minor_units']==2
            receipts.append({'status':200,'tax_cents':2,'gross_cents':22,'due_cents':2})
    finally:handle.stop()
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()==cash
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert client.run('invoice show',dict(invoice=linked['id']),company=COMPANY)['current_revision_id']==linked['current_revision_id']
    application=paid['effect']['applications'][0]['application_id']
    live=client.run('application show',dict(application=application),company=COMPANY)
    allocations=live['current_allocations']
    assert sum(r['amount_minor_units'] for r in allocations if r['logical_kind']=='net')==18
    assert sum(r['amount_minor_units'] for r in allocations if r['logical_kind']=='tax')==2
    assert live['current_payment']['version']==2
    ar=invoice['revision']['profile']['control_account']['id']
    assert_oracle(client,invoice['id'],{('2026-06-01',ar):22,
        ('2026-06-01',tax_sale['income']):-20,('2026-06-01',tax_sale['liability']):-2})
    (tmp_path/'public-role-receipts.json').write_text(json.dumps(receipts,indent=2))
