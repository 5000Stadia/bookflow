"""Actual source/deposit date slices and explicit zero-main-bank persistence."""
import pytest
from bookflow import BookflowError
from bookflow.core.context import Context,Interface
from bookflow.company import deposit_coordinate_persistence as persistence
from bookflow.company.deposit_coordinate_models import CoordinateInput
from tests.test_deposit_coordinate_persistence import sale,driver,prepare
from tests.test_deposit_lifecycle import additional_document,replacement
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method
from tests.test_service_sales_lifecycle import COMPANY


@pytest.mark.parametrize('mode',['date','zero_bank'])
def test_exact_dated_cash_and_zero_main_role(client,sale,driver,mode):
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-05',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],quantity='1',unit_price='10')]),company=COMPANY)
    doc=additional_document(client,sale,'1',cash='1')
    doc.update(date='2026-06-06',additional=[],sources=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)])
    if mode=='date':doc.pop('cash_back')
    initial=driver.run('post',dict(operation_key='C-date-post',document=doc))
    body=replacement(initial,doc)
    body['sources']=[dict(source_result=True,source=receipt['id'])]
    action=dict(sales_receipt=receipt['id'],expected_version=2)
    if mode=='date':action['date']='2026-06-08';body['date']='2026-06-09'
    else:body['cash_back']['amount']='10'
    inp=CoordinateInput(deposit=initial.current.id,expected_version=1,operation_key='C-date-coordinate',
        source_action=dict(kind='sales_receipt_update',input=action),replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'C dates',reason='Correct date or cash disposition')
    with driver.session() as s:
        p=prepare(s,ctx,inp);output=persistence.execute(s,ctx,p)
        bank=doc['deposit_to'];ufid=s.company.raw.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
        def balance(date,account):
            return s.company.raw.execute('SELECT coalesce(sum(p.debit_minor_units-p.credit_minor_units),0) FROM posting_lines p JOIN posting_batches b ON b.id=p.batch_id WHERE p.transaction_id IN (?,?) AND b.effective_date<=? AND p.account_id=?',(receipt['id'],initial.current.id,date,account)).fetchone()[0]
        if mode=='date':
            assert [(balance(date,ufid),balance(date,bank)) for date in ('2026-06-07','2026-06-08','2026-06-09')]==[(0,0),(1000,0),(0,1000)]
            assert output.current.revision_date=='2026-06-09'
            assert output.effect.source.after_header.version==3
        else:
            assert output.current.revision_bank_total==0 and output.current.revision_cash_back==1000
            rows=s.company.raw.execute('SELECT p.account_id,p.debit_minor_units,p.credit_minor_units FROM posting_lines p JOIN posting_batches b ON b.id=p.batch_id WHERE p.transaction_id=? AND b.kind!=? AND b.revision_id=?',(initial.current.id,'reversal',output.current.revision_id)).fetchall()
            assert all(d+c>0 for _,d,c in rows) and all(a!=bank for a,_,_ in rows)
            assert balance('2026-06-09',doc['cash_back']['account'])==1000
            current=s.company.raw.execute('SELECT k.role,v.active,v.signed_debit FROM bank_effect_keys k JOIN bank_effect_current p ON p.key_id=k.id JOIN bank_effect_versions v ON v.id=p.version_id WHERE k.transaction_id=?',(initial.current.id,)).fetchall()
            assert current==[('main_bank',0,0)]
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_zero_sales_component_presence_and_durable_ordinal_restore(client,sale,driver):
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],quantity='1',unit_price='1'),dict(item=sale['item'],quantity='1',unit_price='1')]),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['additional']=[]
    doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    posted=driver.run('post',dict(operation_key='C-zero-presence-post',document=doc))
    original=posted.effect.financial.intent.sources[0]
    expected={v.key:v.ordinal for v in original.occurrences}
    line_ids=[v['line_id'] for v in receipt['revision']['lines']]
    for step,price,total in [(1,'0',100),(2,'1',200)]:
        body=replacement(posted,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
        source=dict(sales_receipt=receipt['id'],expected_version=step+1,amount_received=str(total//100),
            lines=[dict(line_id=line_ids[0],item=sale['item'],quantity='1',unit_price=price),
                   dict(line_id=line_ids[1],item=sale['item'],quantity='1',unit_price='1')])
        inp=CoordinateInput(deposit=posted.current.id,expected_version=step,operation_key='C-zero-presence-'+str(step),
            source_action=dict(kind='sales_receipt_update',input=source),replacement=dict(mode='document',document=body))
        ctx=Context.new(Interface.python,'C semantic zero',reason='Correct one captured line')
        with driver.session() as s:
            result=persistence.execute(s,ctx,prepare(s,ctx,inp))
            current=result.effect.deposit.financial.intent.sources[0]
            assert current.row_id==original.row_id and {v.key:v.ordinal for v in current.occurrences}==expected
            assert result.current.revision_bank_total==total
            assert current.source.semantic_presence==original.source.semantic_presence
            capacities={v.key:v.capacity for v in current.source.components}
            assert {key:capacities.get(key,0) for key in current.source.semantic_presence}=={
                key:(0 if step==1 and key.identity==line_ids[0] else 100) for key in original.source.semantic_presence}
            assert all(v['debit_minor_units']+v['credit_minor_units']>0 for v in result.effect.source.inserted.model_dump()['posting_lines'])
            assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
