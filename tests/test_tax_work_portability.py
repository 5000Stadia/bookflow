"""Frozen co15 work lineage, upgrade and portable company-local basis evidence."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import bookflow
import pytest
from tests.test_tax_work_migration import co15_root
from tests.test_tax_policy_portability import attach
from tests.test_tax_policy_sales import sale, tax_sale


@pytest.fixture(scope='module')
def legacy_work(co15_root,tmp_path_factory):
    root=tmp_path_factory.mktemp('legacy-work')/'root';shutil.copytree(co15_root,root)
    script='''import bookflow,json,sys
c=bookflow.connect(data_root=sys.argv[1]); company='Demo Plumbing Co'
run=lambda command,values:c.run(command,values,company=company,reason='Frozen legacy tax work')
customer=run('customer create',dict(name='Frozen legacy tax work customer'))['id']
income=run('account create',dict(name='Frozen legacy tax work income',type='income'))['id']
code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if row['taxable'])
item=run('item create',dict(name='Frozen legacy tax work labor',type='service',description='Frozen legacy labor',sales_enabled=True,sales_tax_code_id=code,income_account_id=income,price='0.10'))['id']
source=run('estimate create',dict(customer=customer,date='2026-11-01',title='Frozen legacy partially billed work',sales_tax_item='Tax Rounding Example 10%',lines=[dict(item=item,net_amount='0.10'),dict(item=item,net_amount='0.20')]))
source=run('estimate update',dict(estimate=source['id'],expected_version=1,status='accepted',decision_note='Legacy agreement'))
request=dict(estimate=source['id'],expected_version=2,conversion_key='legacy-work-first',date='2026-11-02',selections=[dict(line_id=source['revision']['lines'][0]['line_id'],percent='50')])
bill=run('estimate invoice',request)
print(json.dumps(dict(source=source,bill=bill,request=request,item=item)))
'''
    result=subprocess.run([sys.executable,'-c',script,str(root)],cwd=root,
        env=dict(os.environ,PYTHONPATH=str(co15_root.parent/'source'/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    return root,json.loads(result.stdout)


def test_legacy_partial_basis_and_work_order_survive_upgrade_attach(legacy_work,tmp_path):
    root,saved=legacy_work;target_root=tmp_path/'upgraded';shutil.copytree(root,target_root)
    c=bookflow.connect(data_root=str(target_root));c.run('upgrade',{})
    target,company=attach(c,'Demo Plumbing Co',tmp_path)
    run=lambda command,data:target.run(command,data,company=company,reason='Portable legacy work')
    run('company update',dict(sales_tax_calculation='invoice_combined_half_up'))
    source=run('estimate show',dict(estimate=saved['source']['id']))
    assert source['revision']['facts']['schema_version']==1
    assert source['revision']['tax_calculation_details']['origin']['kind']=='legacy_implicit'
    assert all(line['facts']['schema_version']==1 for line in source['revision']['lines'])
    from copy import deepcopy
    from pydantic import ValidationError
    from bookflow.company.work_tax_facts import read_facts
    malformed=deepcopy(source['revision']['facts'])
    malformed['profile'].update(schema_version=2,sales_tax_calculation='invoice_combined_half_up',tax_policy_origin={'kind':'explicit','source_id':None})
    with pytest.raises(ValidationError):read_facts(malformed)
    old_lines={line['line_id']:line['facts'] for line in source['revision']['lines']}
    before=run('estimate billing',dict(estimate=source['id']))
    assert before['remaining_net_minor_units']==25
    order=run('estimate work-order',dict(estimate=source['id'],expected_version=source['version'],date='2026-11-03',conversion_key='portable-legacy-order'))
    assert order['revision']['facts']['schema_version']==2
    assert order['revision']['tax_calculation_details']['origin']['kind']=='legacy_implicit'
    assert [line['facts'] for line in order['revision']['lines']]==list(old_lines.values())
    order=run('work-order update',dict(work_order=order['id'],expected_version=order['version'],lines=[dict(line_id=line['line_id'],item=saved['item']) for line in reversed(order['revision']['lines'])]))
    assert all(line['facts']['schema_version']==1 for line in order['revision']['lines'])
    state=run('work-order billing',dict(work_order=order['id']))
    assert state['remaining_net_minor_units']==25
    final=run('work-order invoice',dict(work_order=order['id'],expected_version=order['version'],date='2026-11-04',conversion_key='portable-legacy-finish'))
    assert final['subtotal_minor_units']==25
    assert {row['allocation_version'] for row in final['revision']['billing_sources']}=={1,2}
    assert run('estimate invoice',saved['request'])['idempotent_replay']
    historical=run('estimate show',dict(estimate=source['id'],revision_number=2))
    assert historical['revision']['facts']==saved['source']['revision']['facts']
    assert [line['facts'] for line in historical['revision']['lines']]==[line['facts'] for line in saved['source']['revision']['lines']]
    path=target.company.show(company=company)['path']
    from pathlib import Path
    with sqlite3.connect(Path(path)/'company.db') as db:
        row=db.execute('SELECT source_basis_hash,facts_snapshot,spans_json FROM work_billing_allocations WHERE id=?',(saved['bill']['revision']['billing_sources'][0]['id'],)).fetchone()
    with sqlite3.connect(next(root.glob('organizations/*/Demo Plumbing Co/company.db'))) as db:
        assert db.execute('SELECT source_basis_hash,facts_snapshot,spans_json FROM work_billing_allocations WHERE id=?',(saved['bill']['revision']['billing_sources'][0]['id'],)).fetchone()==row


def test_combined_partial_basis_and_rebill_survive_fresh_hub(client, tax_sale, tmp_path):
    from tests.test_tax_policy_sales import request, COMPANY
    from tests.test_work_billing_lifecycle import accepted, bill
    source = accepted(client, tax_sale, **request(tax_sale))
    first = bill(client, source, selections=[dict(line_id=source['revision']['lines'][0]['line_id'],percent='100')])
    original = client.run('invoice show', dict(invoice=first['id']), company=COMPANY)['revision']
    target, company = attach(client, COMPANY, tmp_path)
    run = lambda command, data: target.run(command, data, company=company, reason='Portable captured work basis')
    run('company update', dict(sales_tax_calculation='line_component_half_even'))
    copied = run('estimate show', dict(estimate=source['id']))
    assert copied['revision']['facts'] == source['revision']['facts']
    state = run('estimate billing', dict(estimate=source['id']))
    assert state['remaining_net_minor_units'] == 10 and state['remaining_tax_minor_units'] == 1
    order = run('estimate work-order', dict(estimate=source['id'], expected_version=copied['version'], date='2026-06-03', conversion_key='portable-combined-order'))
    assert order['revision']['tax_calculation_details']['policy'] == 'invoice_combined_half_up'
    remaining = run('work-order billing', dict(work_order=order['id']))
    final = run('work-order invoice', dict(work_order=order['id'], expected_version=order['version'], date='2026-06-04', conversion_key='portable-combined-finish'))
    assert final['revision']['tax_calculation_details']['attribution'] == remaining['forecast_tax_attribution']
    assert final['tax_minor_units'] == 1
    assert all(row['allocation_version'] == 3 and row['allocation_proof']['basis_version'] == 2 for row in final['revision']['billing_sources'])
    run('invoice void', dict(invoice=final['id'], expected_version=1))
    order = run('work-order show', dict(work_order=order['id']))
    previous = final['revision']['billing_sources'][0]
    rebill = run('work-order invoice', dict(work_order=order['id'], expected_version=order['version'], date='2026-06-05', conversion_key='portable-combined-rebill', selections=[dict(line_id=order['revision']['lines'][1]['line_id'], rebill_allocation_id=previous['id'])]))
    proof = rebill['revision']['billing_sources'][0]['allocation_proof']
    assert proof == dict(previous['allocation_proof'], source_revision_id=order['revision']['id'], source_line_id=order['revision']['lines'][1]['id'])
    assert rebill['tax_minor_units'] == 1
    assert run('invoice show', dict(invoice=first['id']))['revision'] == original
    assert client.run('estimate billing', dict(estimate=source['id']), company=COMPANY)['remaining_net_minor_units'] == 10
