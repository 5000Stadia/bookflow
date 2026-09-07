"""Real reader EQPs and lossless maintenance through ordinary public writes."""
import json
import time
import pytest
import sqlalchemy as sa
from bookflow.company import payment_authority as pa, schema
from bookflow.core import registry
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method, posted
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_payment_publication_freshness import raw_snapshot


def test_actual_covering_relations_populated_work_and_write_maintenance(client,sale,root,monkeypatch,tmp_path):
    parent=sale['customer'];pm=method(client)
    work=bill(client,accepted(client,sale));ordinary=posted(client,parent,sale['item'],'10','CO19-ORDINARY')
    traces=[];writes=[];expectB={work['id'],ordinary['id']};expectH=set()
    cmd=registry.get('payment query');planner=cmd.plan;calls=[]
    def witness(inp,ctx,s):
        calls.append(True)
        statements=[]
        def capture(conn,cursor,sql,parameters,context,many):statements.append((sql,parameters))
        sa.event.listen(s.company.conn,'before_cursor_execute',capture)
        try:pa.authorize_publication_payer(s,parent)
        finally:sa.event.remove(s.company.conn,'before_cursor_execute',capture)
        assert len(statements)==2
        assert s.company.raw.execute('SELECT count(*) FROM work_billing_allocations').fetchone()[0]>0
        selected=pa._publication_payer_transactions(parent)
        B=set(s.company.conn.execute(selected).scalars())
        H=set(s.company.conn.execute(sa.select(schema.applications.c.paid_transaction_id).where(schema.applications.c.paying_transaction_id.in_(selected))).scalars())
        assert B==expectB and H==expectH
        assert pa.linked_work_required(s.company,B)
        plans=[]
        for sql,args in statements:
            eqp=s.company.raw.execute('EXPLAIN QUERY PLAN '+sql,args).fetchall()
            descriptions=[r[3] for r in eqp]
            assert any('COVERING INDEX ix_co19_posting_party_transactions' in d for d in descriptions)
            assert any('COVERING INDEX ix_co19_applications_targets' in d for d in descriptions)
            plans.append(dict(sql=sql,args=args,eqp=eqp))
        traces.append(dict(B=sorted(B),H=sorted(H),C=sorted(B|H),plans=plans))
        return planner(inp,ctx,s)
    def check():
        co=client.company.show(company=COMPANY)['company_id'];before=raw_snapshot(root,co)
        with monkeypatch.context() as patch:
            patch.setattr(cmd,'plan',witness);client.run('payment query',dict(limit=1),company=COMPANY)
        assert raw_snapshot(root,co)==before
    def write(command,args,**kw):
        start=time.perf_counter_ns();cpu=time.thread_time_ns();out=client.run(command,args,company=COMPANY,**kw)
        writes.append(dict(command=command,wall_ms=(time.perf_counter_ns()-start)/1e6,cpu_ms=(time.thread_time_ns()-cpu)/1e6))
        return out
    check()
    paid=write('payment receive',dict(customer=parent,date='2026-06-03',amount='2',payment_method=pm,operation_key='covering-receive',applications=dict(mode='inline',items=[])))
    expectB.add(paid['id']);check()
    applied=write('payment apply',dict(payment=paid['id'],expected_version=1,date='2026-06-03',operation_key='covering-apply',applications=dict(mode='inline',items=[dict(invoice=work['id'],expected_version=1,amount='1')])))
    expectH.add(work['id']);check()
    app=applied['effect']['applications'][0]['application_id']
    write('payment unapply',dict(payment=paid['id'],expected_version=2,operation_key='covering-unapply',applications=[dict(application_id=app,invoice_expected_version=2)]),reason='Preserve old edge')
    check()
    write('invoice update',dict(invoice=work['id'],expected_version=3,lines=[dict(item=sale['item'],quantity='1',unit_price='10')]),reason='Preserve old work allocation')
    check()
    write('payment update',dict(payment=paid['id'],expected_version=3,operation_key='covering-update',memo='Correction'),reason='Correction witness')
    check()
    write('payment void',dict(payment=paid['id'],expected_version=4,operation_key='covering-void'),reason='Void witness')
    check()
    assert len(calls)==len(traces)==7 and len(writes)==6
    final=client.run('payment show',dict(payment=paid['id']),company=COMPANY)
    assert final['status']=='voided' and final['current']['available_minor_units']==0
    (tmp_path/'covering-access.json').write_text(json.dumps(dict(traces=traces,writes=writes),indent=2))
