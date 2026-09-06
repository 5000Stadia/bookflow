"""Hand-fixed work, forecast and release/rebill document-tax witnesses."""
from copy import deepcopy
import pytest
from bookflow import BookflowError
from tests.test_tax_policy_sales import sale, tax_sale, request, cells, COMPANY, snapshot
from tests.test_customer_work_lifecycle import run
from tests.test_work_billing_lifecycle import accepted, bill


@pytest.mark.parametrize('policy,expected', [('invoice_combined_half_up',(2,0,1,1)),('line_combined_half_up',(2,1,2,1))])
def test_release_rebill_order_and_complete_forecast(client,tax_sale,policy,expected):
    a,z=tax_sale['rules']
    rule=client.run('item show',dict(item=a),company=COMPANY)
    agency_a=client.vendor.create(name='Separate rebill A agency',is_tax_agency=True,company=COMPANY)['id']
    client.run('item update',dict(item=a,expected_version=rule['version'],tax_percent='10',tax_agency_vendor_id=agency_a),company=COMPANY)
    source=accepted(client,tax_sale,**request(tax_sale,policy,nets=('0.05','0.10')))
    assert source['revision']['facts']['schema_version']==2
    assert all(line['facts']['schema_version']==2 for line in source['revision']['lines'])
    state=run(client,'estimate','billing',estimate=source['id'])
    assert state['can_bill_together'] and state['forecast_basis']=='all_remaining_together'
    first=bill(client,source)
    assert first['revision']['tax_calculation_details']['attribution']==state['forecast_tax_attribution']
    totals=lambda result: tuple(sum(v for (i,r),v in cells(result).items() if r==key) for key in (a,z))
    assert totals(first)==expected[:2]
    assert all(row['allocation_version']==3 and row['allocation_proof']['basis_version']==2 for row in first['revision']['billing_sources'])
    saved=deepcopy(client.run('invoice show',dict(invoice=first['id']),company=COMPANY)['revision'])
    client.run('invoice void',dict(invoice=first['id'],expected_version=1),company=COMPANY,reason='Release exact work')
    source=run(client,'estimate','show',estimate=source['id'])
    source=run(client,'estimate','update',estimate=source['id'],expected_version=source['version'],
        lines=[dict(line_id=line['line_id'],item=tax_sale['item']) for line in reversed(source['revision']['lines'])])
    source=run(client,'estimate','update',estimate=source['id'],expected_version=source['version'],status='accepted',decision_note='Accept reordered quote')
    old={row['root_line_id']:row for row in saved['billing_sources']}
    second=bill(client,source,'reverse-rebill',selections=[dict(line_id=line['line_id'],rebill_allocation_id=old[line['line_id']]['id']) for line in source['revision']['lines']])
    assert totals(second)==expected[2:]
    import sqlite3
    from tests.test_row8_journal import database_path,assert_oracle
    with sqlite3.connect(database_path(client)) as db:
        actual=dict(db.execute('SELECT agency_id,sum(tax_minor_units) FROM sales_tax_components WHERE transaction_id=? GROUP BY agency_id',(second['id'],)))
    assert len(actual)==2 and actual[agency_a]==expected[2] and sum(v for k,v in actual.items() if k!=agency_a)==expected[3]
    assert_oracle(client,second['id'],{('2026-01-13',second['revision']['profile']['control_account']['id']):15+sum(expected[2:]),('2026-01-13',tax_sale['income']):-15,('2026-01-13',tax_sale['liability']):-sum(expected[2:])})
    assert second['tax_minor_units']==first['tax_minor_units']
    for row in second['revision']['billing_sources']:
        previous=old[row['root_line_id']]
        assert row['allocation_proof']['spans']==previous['allocation_proof']['spans']
        assert row['net_minor_units']==previous['net_minor_units']
    historical=client.run('invoice show',dict(invoice=first['id'],revision_number=1),company=COMPANY)
    assert {k:v for k,v in historical['revision'].items() if k!='batches'}=={k:v for k,v in saved.items() if k!='batches'}
    assert [row['kind'] for row in historical['revision']['batches']]==['original','reversal']


def test_work_capture_reorder_and_derived_independent_scope(client,tax_sale):
    order=run(client,'work-order','create',title='Tax work',**request(tax_sale,nets=('0.10',)))
    initial=deepcopy(order['revision'])
    first=bill(client,order,noun='work-order')
    saved_bill=client.run('invoice show',dict(invoice=first['id']),company=COMPANY)['revision']
    source=run(client,'work-order','show',work_order=order['id'])
    old=source['revision']['lines'][0]
    changed=run(client,'work-order','update',work_order=source['id'],expected_version=source['version'],
        lines=[dict(item=tax_sale['item'],line_id=old['line_id']),dict(item=tax_sale['item'],net_amount='0.10',tax_code=tax_sale['taxable'])])
    assert changed['tax_minor_units']==2
    assert changed['revision']['lines'][0]['facts']['tax_minor_units']==2
    assert old['facts']['tax_minor_units']==1
    assert changed['revision']['lines'][0]['tax_ordinal']==1
    state=run(client,'work-order','billing',work_order=source['id'])
    assert state['remaining_tax_minor_units']==1
    assert client.run('invoice show',dict(invoice=first['id']),company=COMPANY)['revision']==saved_bill
    before=snapshot(client)
    with pytest.raises(BookflowError):
        run(client,'work-order','update',work_order=source['id'],expected_version=changed['version'],sales_tax_calculation='line_combined_half_up')
    assert snapshot(client)==before
    assert run(client,'work-order','show',work_order=source['id'],revision_number=1)['revision']==initial


@pytest.mark.parametrize('fault',['cells','orphan'])
def test_work_effect_validation_rejects_coherent_tax_or_unowned_snapshot(client,tax_sale,monkeypatch,fault):
    import json
    from bookflow.company import work_validation
    original=work_validation.validate
    def corrupt(plan,s,ctx):
        pending=plan.data['pending']
        if fault=='orphan':
            row=dict(pending['work_tax_attributions'][0]);row['revision_id']='00000000000000000000000000'
            pending['work_tax_attributions'].append(row)
        else:
            row=pending['work_lines'][0];facts=json.loads(row['facts_snapshot'])
            facts['taxes'][0]['tax_minor_units'],facts['taxes'][1]['tax_minor_units']=facts['taxes'][1]['tax_minor_units'],facts['taxes'][0]['tax_minor_units']
            row['facts_snapshot']=json.dumps(facts)
            row=pending['work_tax_attributions'][0];facts=json.loads(row['facts_snapshot'])
            cells=facts['calculation']['buckets'][0]['cells']
            cells[0]['tax_minor_units'],cells[1]['tax_minor_units']=cells[1]['tax_minor_units'],cells[0]['tax_minor_units']
            row['facts_snapshot']=json.dumps(facts)
        return original(plan,s,ctx)
    monkeypatch.setattr(work_validation,'validate',corrupt)
    before=snapshot(client)
    with pytest.raises(BookflowError) as exc:
        run(client,'estimate','create',title='Reject coherent wrong cents',**request(tax_sale,nets=('0.10',)))
    assert exc.value.code=='E_INTERNAL' and snapshot(client)==before


def test_linked_receipt_same_gross_redistribution_and_permanent_confirmation_history(client,tax_sale):
    source=accepted(client,tax_sale,**request(tax_sale))
    bank=client.account.create(name='Tax linked receipt bank',type='bank',company=COMPANY)['id']
    receipt=bill(client,source,verb='sales-receipt',deposit_to=bank,payment_method='Check',amount_received='0.22')
    retained=receipt['revision']['lines'][1]
    assert retained['tax_minor_units']==0
    data=dict(sales_receipt=receipt['id'],expected_version=1,lines=[dict(line_id=retained['line_id'],item=tax_sale['item']),dict(item=tax_sale['item'],net_amount='0.10',tax_code=tax_sale['taxable'])])
    revised=client.run('sales-receipt update',data,company=COMPANY)
    assert revised['total_minor_units']==22 and revised['revision']['lines'][0]['tax_minor_units']==2
    assert revised['revision']['billing_sources'][0]['tax_minor_units']==2
    independent=revised['revision']['lines'][1]
    data=dict(sales_receipt=receipt['id'],expected_version=2,lines=[dict(line_id=independent['line_id'],item=tax_sale['item'])])
    before=snapshot(client)
    with pytest.raises(BookflowError):client.run('sales-receipt update',data,company=COMPANY)
    assert snapshot(client)==before
    revised=client.run('sales-receipt update',dict(data,amount_received='0.11'),company=COMPANY)
    assert revised['total_minor_units']==11 and not revised['revision']['billing_sources']
    data=dict(sales_receipt=receipt['id'],expected_version=3,lines=[dict(line_id=independent['line_id'],item=tax_sale['item'],net_amount='0.20')])
    before=snapshot(client)
    with pytest.raises(BookflowError):client.run('sales-receipt update',data,company=COMPANY)
    assert snapshot(client)==before
    final=client.run('sales-receipt update',dict(data,amount_received='0.22'),company=COMPANY)
    assert final['total_minor_units']==22
    assert client.account.show(account=bank,company=COMPANY)['balance']['minor_units']==22


def test_paid_linked_invoice_derived_tax_edit_preserves_cash(client,tax_sale):
    import sqlite3
    from tests.test_row8_journal import database_path
    source=accepted(client,tax_sale,**request(tax_sale,nets=('0.10',)))
    invoice=bill(client,source)
    paid=client.run('payment receive',dict(customer=tax_sale['customer'],date='2026-06-02',amount='0.11',payment_method='Check',operation_key='linked-tax-payment',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='0.11')])),company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        cash=db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()
    linked=invoice['revision']['lines'][0]
    fields=dict(invoice=invoice['id'],expected_version=2,operation_key='linked-tax-extra',settlement_versions=[dict(payment=paid['id'],expected_version=1)],lines=[dict(line_id=linked['line_id'],item=tax_sale['item']),dict(item=tax_sale['item'],net_amount='0.10',tax_code=tax_sale['taxable'])])
    revised=client.run('invoice update',fields,company=COMPANY,reason='Add independent taxable work')
    assert revised['total_minor_units']==22 and revised['revision']['lines'][0]['tax_minor_units']==2
    assert revised['revision']['billing_sources'][0]['tax_minor_units']==2
    assert revised['settlement']['current']['due_minor_units']==11
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT * FROM payment_components WHERE transaction_id=? ORDER BY rowid',(paid['id'],)).fetchall()==cash
    assert client.run('invoice update',fields,company=COMPANY,reason='Add independent taxable work')['idempotent_replay']


@pytest.mark.parametrize('noun',['proposal','estimate','work-order'])
def test_work_policy_origin_refresh_and_stable_retired_ordinals(client,tax_sale,noun):
    first=run(client,noun,'create',title='Captured work defaults',**request(tax_sale))
    assert first['revision']['tax_calculation_details']['origin']['kind']=='default'
    ids=[line['line_id'] for line in first['revision']['lines']]
    run(client,'company','update',sales_tax_calculation='line_combined_half_up')
    unchanged=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=1)
    assert not unchanged['changed']
    updated=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=1,refresh_defaults=True)
    assert updated['revision']['tax_calculation_details']['policy']=='line_combined_half_up'
    explicit=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=2,sales_tax_calculation='invoice_combined_half_up')
    reordered=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=3,refresh_defaults=True,
        lines=[dict(line_id=key,item=tax_sale['item']) for key in reversed(ids)])
    assert reordered['revision']['tax_calculation_details']['origin']['kind']=='explicit'
    assert [line['tax_ordinal'] for line in reordered['revision']['lines']]==[2,1]
    assert [line['facts']['tax_minor_units'] for line in reordered['revision']['lines']]==[0,2]
    appended=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=4,
        lines=[dict(line_id=ids[1],item=tax_sale['item']),dict(item=tax_sale['item'],net_amount='0.10',tax_code=tax_sale['taxable'])])
    assert [line['tax_ordinal'] for line in appended['revision']['lines']]==[2,3]
    import sqlite3
    from tests.test_row8_journal import database_path
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT tax_ordinal FROM work_tax_line_keys WHERE document_id=? ORDER BY tax_ordinal',(first['id'],)).fetchall()==[(1,),(2,),(3,)]
    reset=run(client,noun,'update',**{noun.replace('-','_'):first['id']},expected_version=5,use_defaults=['sales_tax_calculation'])
    assert reset['revision']['tax_calculation_details']['policy']=='line_combined_half_up'
    assert reset['revision']['tax_calculation_details']['origin']['kind']=='default'


@pytest.mark.parametrize('target',['root','profile','line'])
def test_malformed_work_effect_version_is_controlled_and_atomic(client,tax_sale,monkeypatch,target):
    import json
    from bookflow.company import work_validation
    original=work_validation.validate
    def changed(plan,s,ctx):
        pending=plan.data['pending'];row=pending['work_lines' if target=='line' else 'work_revisions'][0]
        facts=json.loads(row['facts_snapshot'])
        (facts['profile'] if target=='profile' else facts)['schema_version']=True
        row['facts_snapshot']=json.dumps(facts)
        return original(plan,s,ctx)
    monkeypatch.setattr(work_validation,'validate',changed)
    before=snapshot(client)
    with pytest.raises(BookflowError) as exc:run(client,'proposal','create',title='Reject malformed persisted version',**request(tax_sale))
    assert exc.value.code=='E_INTERNAL' and snapshot(client)==before


def test_forecast_preserves_captured_knowledge_when_current_tax_is_ineligible(client,tax_sale):
    source=accepted(client,tax_sale,**request(tax_sale))
    before=run(client,'estimate','billing',estimate=source['id'])
    run(client,'company','update',sales_tax_enabled=False)
    after=run(client,'estimate','billing',estimate=source['id'])
    assert not after['can_bill_together']
    assert after['forecast_tax_attribution']==before['forecast_tax_attribution']
    assert 'posting_ineligible' in {row['code'] for row in after['forecast_eligibility_reasons']}
    assert after['forecast_fingerprint']!=before['forecast_fingerprint']
