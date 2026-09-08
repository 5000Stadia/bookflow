"""Actual shared report commands over ordinary private deposit lifecycle writes."""
import copy
import json
import pytest
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_service_sales_lifecycle import sale, COMPANY


@pytest.mark.parametrize('command',['report general-ledger','register query'])
def test_deposit_lifecycle_in_existing_reports(client,sale,driver,tmp_path,command):
    document=additional_document(client,sale,'10')
    bank=document['deposit_to']
    # Existing public register producer supplies an unrelated journal control.
    client.run('register post',dict(account=bank,date='2026-06-01',
        direction='increase',amount='3',category=sale['income'],memo='Journal control'),company=COMPANY)
    baseline=client.run(command,dict(account=bank,date_from='2026-06-01',date_to='2026-06-30',limit=200),company=COMPANY)
    control_before=[r for r in baseline['rows'] if r['kind']=='posting']
    assert len(control_before)==1
    posted=driver.run('post',dict(operation_key='report-deposit',document=document))
    identity=posted.current.id;original=posted.effect.batch_ids[0]
    captured=[]
    def observe(stage,expected,closing):
        before=driver.dump()
        out=client.run(command,dict(account=bank,date_from='2026-06-01',date_to='2026-06-30',limit=200),company=COMPANY)
        assert driver.dump()==before
        captured.append(dict(stage=stage,output=out))
        (tmp_path/'runtime-outputs.json').write_text(json.dumps(captured,indent=2))
        rows=[r for r in out['rows'] if r['kind']=='posting']
        own=[r for r in rows if r['transaction_id']==identity]
        assert len(own)==len(expected)
        actual={(r['batch_kind'],r['revision_id'],r['debit']['minor_units'],r['credit']['minor_units'],r['effective_date'],r['reverses_batch_id'],r['replaces_batch_id']) for r in own}
        assert actual==set(expected)
        assert all(r['transaction_type']=='deposit' and r['transaction_number']==posted.current.number for r in own)
        control=[r for r in rows if r['transaction_type']=='journal_entry']
        assert control==control_before
        assert (control[0]['debit']['minor_units'],control[0]['credit']['minor_units'],control[0]['effective_date'])==(300,0,'2026-06-01')
        totals=out['ledger_totals'] if command=='register query' else out['totals']
        assert totals['opening']['minor_units']==0 and totals['closing']['minor_units']==closing
        assert totals['period_debits']['minor_units']==300+sum(r[2] for r in expected)
        assert totals['period_credits']['minor_units']==sum(r[3] for r in expected)
        if command=='register query':
            assert all(r['category_label']=='Deposit' for r in own)
            assert control[0]['category_label'] is not None and control[0]['category_label']!='Deposit'
            assert out['current_balance']['balance']['minor_units']==closing
            for r in own:
                assert r['increase']==r['debit'] and r['decrease']==r['credit']
                assert r['revision_number']==(1 if r['revision_id']==posted.current.revision_id else 2)
                assert r['running_balance']==r['signed_balance']
        return own
    rev1=posted.current.revision_id
    expected=[('original',rev1,1000,0,'2026-06-03',None,None)]
    assert observe('posted',expected,1300)[0]['batch_id']==original
    changed=copy.deepcopy(replacement(posted,document));changed['date']='2026-06-04';changed['additional'][0]['amount']='12'
    corrected=driver.run('update',dict(operation_key='report-correct',deposit=identity,expected_version=1,document=changed),reason='Correct amount and date')
    rev2=corrected.current.revision_id
    expected += [('reversal',rev1,0,1000,'2026-06-03',original,None),('replacement',rev2,1200,0,'2026-06-04',None,original)]
    rows=observe('corrected',expected,1500)
    replacement_id=next(r['batch_id'] for r in rows if r['batch_kind']=='replacement')
    assert {r['batch_id'] for r in rows if r['batch_id']!=original}==set(corrected.effect.batch_ids)
    canceled=driver.run('void',dict(operation_key='report-void',deposit=identity,expected_version=2),reason='Cancel corrected deposit')
    assert canceled.current.status=='voided'
    expected += [('reversal',rev2,0,1200,'2026-06-04',replacement_id,None)]
    rows=observe('voided',expected,300)
    assert {r['batch_id'] for r in rows if r['reverses_batch_id']==replacement_id}==set(canceled.effect.batch_ids)
