"""Pure report/financial representation compatibility, not public GL evidence.

The public GL/register repair is independently frozen at 1ab44f7; real command
and statement compatibility remains a supplier/integration checkpoint here.
"""
from bookflow.company.deposit_models import Effect
from bookflow.company.deposit_report_models import DepositDetailInput
from bookflow.company.deposit_report_print import selected_compositions
from bookflow.company.deposit_reports import aggregate
from tests.test_deposit_reports import world


def test_complete_projection_does_not_page_or_rewrite_financial_effects():
    facts = world()
    before = tuple(r.effect.model_dump_json() for r in facts[0].revisions)
    results = []
    for size in (1,25,200):
        inp = DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',limit=size)
        result = aggregate(facts,inp,currency='USD',destination_id=None)
        results.append(result.model_dump())
        captured = selected_compositions(result,facts)
        assert all(type(r.effect) is Effect for r in captured)
        assert [r.effect.bank_total for r in captured] == [17200,17200,19200]
        assert len(result.rows) == 3
    assert results[0] == results[1] == results[2]
    assert tuple(r.effect.model_dump_json() for r in facts[0].revisions) == before

from tests.test_deposit_draft_financial import run_private, financial
from tests.test_service_sales_lifecycle import sale, COMPANY


def test_real_n1_report_and_shared_accounting_oracles(client,sale,run_private,tmp_path):
    import json
    from tests.test_deposit_sources import uf
    from tests.test_payment_receipts import method
    from tests.test_deposit_lifecycle import additional_document
    from bookflow.company import deposit_report_models as m,deposit_reports as report
    from bookflow.core.publication import OSBinding
    control=uf(client)
    source=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=control,payment_method=method(client),date='2026-06-02',
        lines=[dict(item=sale['item'],quantity='1',unit_price='160')]),company=COMPANY)
    doc=additional_document(client,sale,'20')
    expense=client.account.create(name='Report fee',type='expense',company=COMPANY)['id']
    cash=client.account.create(name='Report cash',type='other_current_asset',company=COMPANY)['id']
    doc['sources']=[dict(source=source['id'],source_type='sales_receipt',expected_version=1)]
    doc['additional'].append(dict(received_from=dict(kind='customer',id=sale['customer']),from_account=expense,amount='-3'))
    doc['cash_back']=dict(account=cash,amount='5')
    pl_before=client.run('report profit-and-loss',dict(date_from='2026-06-01',date_to='2026-06-30'),company=COMPANY)
    bs_before=client.run('report balance-sheet',dict(date_to='2026-06-30'),company=COMPANY)
    field=client.run('custom-field create',dict(name='Captured report text',kind='text',scopes=['deposit'],default='<saved & literal>'),company=COMPANY)['id']
    posted=financial(run_private,dict(operation_key='report-n1',document=doc))
    client.run('custom-field update',dict(custom_field=field,expected_version=1,name='Current field label',default='new default'),company=COMPANY)
    measurements=[]
    def read(s,ctx):
        import time
        import sqlalchemy as sa
        raw=tuple(s.company.raw.iterdump());binding=OSBinding.from_session(s)
        for projection in ('current','effective'):
            count=[0]
            def statement(*args):count[0]+=1
            sa.event.listen(s.company.conn,'before_cursor_execute',statement)
            start=time.perf_counter()
            try:
                result=report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',projection=projection,include_uf_bridge=True),binding=binding)
                encoded=result.model_dump_json().encode()
            finally:sa.event.remove(s.company.conn,'before_cursor_execute',statement)
            measurements.append(dict(projection=projection,seconds=time.perf_counter()-start,company_sql=count[0],response_bytes=len(encoded),rows=result.totals.row_count))
            assert result.totals.composition.model_dump()==dict(source=16000,positive_additional=2000,negative_additional=-300,posting_total=18000,subtotal=17700,bank_total=17200,cash_back=500)
            assert result.uf.data.closing.source_backed==result.uf.data.closing.ledger==0
            assert len(result.items[0].sources)==1 and len(result.items[0].additional)==2
            assert sum(c.captured.units for c in result.items[0].cash_allocations)==18000
        from bookflow.company import deposit_report_print as printing
        printed=printing.print_data(s,m.DepositDetailFilter(date_from='2026-06-01',date_to='2026-06-30'),binding=binding)
        custom=printed.compositions[0].document.selected.custom_fields
        assert len(custom)==1 and custom[0].captured.value=='<saved & literal>'
        assert custom[0].captured.name=='Captured report text' and custom[0].captured_print_visibility is None
        rows=s.company.raw.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id',(posted.current.id,)).fetchall()
        assert dict(rows)=={control:-16000,doc['deposit_to']:17200,cash:500,sale['income']:-2000,expense:300}
        assert tuple(s.company.raw.iterdump())==raw
    run_private(read)
    (tmp_path/'report-measurements.json').write_text(json.dumps(measurements,indent=2))
    outputs={}
    for command,params in [('report general-ledger',dict(account=doc['deposit_to'],date_from='2026-06-01',date_to='2026-06-30')),
                           ('register query',dict(account=doc['deposit_to'],date_from='2026-06-01',date_to='2026-06-30')),
                           ('report profit-and-loss',dict(date_from='2026-06-01',date_to='2026-06-30')),
                           ('report balance-sheet',dict(date_to='2026-06-30')),
                           ('report trial-balance',dict(date_to='2026-06-30'))]:
        outputs[command]=client.run(command,params,company=COMPANY)
    assert outputs['report general-ledger']['totals']['closing']['minor_units']==17200
    assert outputs['register query']['ledger_totals']['closing']['minor_units']==17200
    assert {r['category_label'] for r in outputs['register query']['rows'] if r['kind']=='posting'}=={'Deposit'}
    assert outputs['report profit-and-loss']['totals']['net_income']['minor_units']-pl_before['totals']['net_income']['minor_units']==1700
    assert outputs['report balance-sheet']['totals']['difference']['minor_units']==0
    assert outputs['report balance-sheet']['totals']['assets']['minor_units']-bs_before['totals']['assets']['minor_units']==1700
    assert client.customer.show(customer=sale['customer'],company=COMPANY)['current_balance']['minor_units']==0
    (tmp_path/'n1-public-oracle.json').write_text(json.dumps(dict(before_pl=pl_before,before_bs=bs_before,after=outputs),indent=2))
