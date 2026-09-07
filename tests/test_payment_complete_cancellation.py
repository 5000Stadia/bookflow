"""Complete private cancellation preserves exact owned applications and inverses."""
import copy
import sqlite3
import pytest
from bookflow import BookflowError
from bookflow.company import payment_cancellation as cancellation
from bookflow.company.payment_models import PaymentVoidIntent, EffectProvenance
from bookflow.core.context import Context, Interface
from bookflow.core.ids import new_id
from bookflow.core import clock
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_deposit_dependency_binding import observe
from tests.test_row8_journal import database_path
from tests.test_deposit_lifecycle import driver


@pytest.mark.timeout(600)
@pytest.mark.parametrize('count', [0, 3, 403])
def test_all_active_exact_independent_application_allocation_and_invoice_sets(client, sale, monkeypatch, count, driver):
    invoices = [posted(client, sale['customer'], sale['item'], '1.00', f'COMPLETE-CANCEL-{i}') for i in range(count)]
    selection = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'],
        date='2026-06-02', amount=str(count+1)), company=COMPANY)
    for offset in range(0,count,200):
        selection = client.run('payment selection update', dict(selection=selection['id'],expected_version=selection['version'],
            set_items=[dict(invoice=row['id'],expected_version=1,amount='1.00') for row in invoices[offset:offset+200]]),company=COMPANY)
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount=str(count+1),
        payment_method=method(client),operation_key='complete-cancellation',applications=dict(mode='selection',selection=selection['id'],expected_version=selection['version'])),company=COMPANY)
    path=database_path(client)
    with sqlite3.connect(path) as db:
        before=tuple(db.iterdump()); db.row_factory=sqlite3.Row
        apps={r['id']:dict(r) for r in db.execute('SELECT * FROM applications WHERE paying_transaction_id=?',(paid['id'],))}
        allocations={r['id']:dict(r) for r in db.execute('SELECT * FROM application_allocations WHERE source_transaction_id=?',(paid['id'],))}
        oldlegs={r['id']:dict(r) for r in db.execute('SELECT * FROM posting_lines WHERE transaction_id=?',(paid['id'],))}
    assert len(apps)==len(allocations)==count
    def check(s):
        ctx=Context.new(Interface.python,'complete-cancellation',reason='Cancel every application and receipt together')
        provenance=EffectProvenance(at=clock.now_iso(),event_id=new_id(),operation_id=new_id())
        intent=PaymentVoidIntent(payment=paid['id'],expected_version=1)
        if count:
            with pytest.raises(BookflowError) as caught:cancellation.prepare_void_effect(s,ctx,intent,provenance)
            assert caught.value.code=='E_HAS_APPLICATIONS'
        plan=cancellation.prepare_all_active_void(s,ctx,intent,provenance)
        cancellation.validate(plan,s,ctx)
        rows=plan.data['pending']
        assert 'operation_key' not in plan.preview.model_dump() and not hasattr(plan.data['input'],'operation_key')
        assert plan.data['header']['version']==2 and plan.data['header']['status']=='voided'
        assert plan.preview.current.applied_minor_units==plan.preview.current.available_minor_units==plan.preview.current.effective_received_minor_units==0
        assert {r['reverses_application_id'] for r in rows['applications']}==set(apps)
        assert {r['reverses_allocation_id'] for r in rows['application_allocations']}==set(allocations)
        assert len(rows['applications'])==len(rows['application_allocations'])==count
        for table, originals, link in [('applications',apps,'reverses_application_id'),('application_allocations',allocations,'reverses_allocation_id')]:
            ignored={'id','created_at','created_by','created_via','audit_event_id','kind',link}
            for row in rows[table]:
                assert {k:v for k,v in row.items() if k not in ignored}=={k:v for k,v in originals[row[link]].items() if k not in ignored}
        assert {before['id'] for before,after in plan.data['changed_headers']}=={row['id'] for row in invoices}
        assert all(before['version']==2 and after['version']==3 for before,after in plan.data['changed_headers'])
        assert {row.invoice_id:row.due_minor_units for row in plan.preview.effect.document_changes}=={row['id']:100 for row in invoices}
        assert len(rows['posting_lines'])==len(oldlegs)
        for row in rows['posting_lines']:
            original=oldlegs[row['reversed_line_id']]
            assert (row['account_id'],row['debit_minor_units'],row['credit_minor_units'])==(original['account_id'],original['credit_minor_units'],original['debit_minor_units'])
        assert sum(r['debit_minor_units']-r['credit_minor_units'] for r in rows['posting_lines'])==0
        assert {r['effective_date'] for r in rows['posting_batches']}=={'2026-06-02'}
        if count:
            for table in ('applications','application_allocations'):
                broken=copy.deepcopy(plan);broken.data['pending'][table].pop()
                with pytest.raises(BookflowError):cancellation.validate(broken,s,ctx)
    observe(client,monkeypatch,check)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before
    if count==403:
        complete_coordinate_case(client,driver,paid['id'],{row['id'] for row in invoices})


def complete_coordinate_case(client, driver, payment, invoice_ids):
    """Add ordinary persisted seed rows inside one rollback-only owned writer."""
    import json
    from bookflow.company import deposit_lifecycle,deposit_persistence,deposit_coordination
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.publication import OSBinding
    class RollbackSeed(Exception):pass
    original=driver.dump()
    with pytest.raises(RollbackSeed):
        with driver.session() as s:
            bank=s.company.raw.execute("SELECT id FROM accounts WHERE type='bank' AND active=1 ORDER BY id LIMIT 1").fetchone()
            assert bank is not None
            ctx=Context.new(Interface.python,'complete coordinate fixture',reason='Cancel the complete source and deposit together')
            binding=OSBinding.from_session(s)
            inp=deposit_lifecycle.INPUTS['post'].model_validate(dict(operation_key='B-full-coordinate-seed',document=dict(
                mode='inline',deposit_to=bank[0],date='2026-06-03',sources=[dict(source_type='payment',source=payment,expected_version=1)])))
            seeded=deposit_lifecycle.prepare(s,ctx,inp,'post',binding=binding)
            posted=deposit_persistence.execute(s,ctx,seeded)
            assert posted.effect.financial.bank_total==(len(invoice_ids)+1)*100
            before=tuple(s.company.raw.iterdump())
            request=CoordinateInput(deposit=posted.current.id,expected_version=1,operation_key='B-full-coordinate',
                source_action=dict(kind='payment_void',payment=payment,expected_version=2,unapply='all_active'),replacement=dict(mode='void'))
            result=deposit_coordination.prepare(s,ctx,request,binding=binding)
            source=result.resolution.source
            assert set(source.plan.data['cancellation_set'].invoice_versions)=={(identity,2) for identity in invoice_ids}
            assert {row.invoice_id:row.due_minor_units for row in source.plan.preview.effect.document_changes}=={identity:100 for identity in invoice_ids}
            assert len(source.plan.preview.effect.document_changes)==len(invoice_ids)
            assert len(source.plan.data['pending']['applications'])==len(invoice_ids)
            assert len(source.plan.data['pending']['application_allocations'])==len(invoice_ids)
            assert set(json.loads(result.readset_json)['transactions'])==set(invoice_ids)|{payment,posted.current.id}
            headers=json.loads(result.resolution.headers_json)
            assert {old['id']:(old['version'],new['version']) for old,new in headers}=={
                **{identity:(2,3) for identity in invoice_ids},payment:(2,3),posted.current.id:(1,2)}
            assert len(headers)==len(invoice_ids)+2
            assert not any(effect.active for effect in result.resolution.deposit_bank_after)
            assert sum(row['debit_minor_units']-row['credit_minor_units'] for row in source.plan.data['pending']['posting_lines'])==0
            deposit_coordination.validate(s,ctx,result)
            assert tuple(s.company.raw.iterdump())==before
            raise RollbackSeed
    assert driver.dump()==original
