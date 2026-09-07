"""Owned private aggregate execution with actual source producers and SQL storage."""
import copy
from contextlib import contextmanager
import json
import sqlite3
import pytest
import sqlalchemy as sa
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.company import schema as c, deposit_lifecycle as lifecycle, deposit_persistence as persistence
from bookflow.company.deposit_lifecycle_models import LifecycleOutput
from bookflow.storage.engine import open_database
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path
from tests.test_deposit_sources import uf


@pytest.fixture
def driver(client,monkeypatch):
    captured=[];command=registry.get('company show');original=command.plan
    def capture(inp,ctx,s):
        captured.append(copy.copy(s))
        return original(inp,ctx,s)
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',capture)
        client.run('company show',{},company=COMPANY)
    seed=captured[0];path=database_path(client)
    class Driver:
        @contextmanager
        def session(self):
            from bookflow.core.locks import RootLock
            with RootLock(seed.data_root, 'owned-private-deposit'), open_database(seed.data_root/'hub.db', writable=False) as hub, open_database(path,writable=True) as db:
                s=copy.copy(seed);s.company=db;s.hub=hub;s.dry_run=False
                db.raw.execute('BEGIN IMMEDIATE')
                s.company_info_row=dict(db.conn.execute(sa.select(c.company_info)).mappings().one())
                try:
                    yield s
                    db.raw.execute('COMMIT')
                except BaseException:
                    if db.raw.in_transaction:db.raw.execute('ROLLBACK')
                    raise
        def run(self,verb,body,reason=None):
            ctx=Context.new(Interface.python,'private G2 witness',reason=reason)
            with self.session() as s:
                inp=lifecycle.INPUTS[verb].model_validate(body)
                plan=lifecycle.prepare(s,ctx,inp,verb)
                if isinstance(plan,LifecycleOutput):return plan
                if verb!='post' and inp.dependency_guard is None:
                    inp=inp.model_copy(update={'dependency_guard':plan.dependency_guard})
                    plan=lifecycle.prepare(s,ctx,inp,verb)
                return persistence.execute(s,ctx,plan)
        def dump(self):
            with sqlite3.connect(path) as db:return tuple(db.iterdump())
    return Driver()


def additional_document(client,sale,amount='10.00',cash=None):
    bank=client.account.create(name='G2 bank',type='bank',company=COMPANY)['id']
    body=dict(mode='inline',deposit_to=bank,date='2026-06-03',memo='G2 entered memo',
        additional=[dict(received_from=dict(kind='customer',id=sale['customer']),from_account=sale['income'],amount=amount,memo='Extra cash')])
    if cash:
        destination=client.account.create(name='G2 till',type='other_current_asset',company=COMPANY)['id']
        body['cash_back']=dict(account=destination,amount=cash,memo='Till cash')
    return body


def replacement(output,document):
    return dict(document,number=output.current.number,memo=document.get('memo'),cash_back=document.get('cash_back'),
        custom_fields={},expected_custom_field_kinds={},sources=document.get('sources',[]),
        additional=[dict(row,line_id=saved.row_id) for row,saved in zip(document['additional'],output.effect.financial.intent.additional)])


def test_additional_post_noeffect_update_void_permanent_replay(client,sale,driver):
    document=additional_document(client,sale)
    request=dict(operation_key='g2-post',document=document)
    posted=driver.run('post',request)
    assert posted.changed and posted.effect.financial.bank_total==1000
    before=driver.dump()
    assert driver.run('post',request).effect==posted.effect
    assert driver.dump()==before
    update=dict(operation_key='g2-noeffect',deposit=posted.current.id,expected_version=1,document=replacement(posted,document))
    noop=driver.run('update',update,reason='Confirm unchanged deposit')
    assert not noop.changed and noop.current==posted.current and noop.effect.batch_ids==()
    before=driver.dump()
    assert driver.run('update',update,reason='Confirm unchanged deposit').idempotent_replay
    assert driver.dump()==before
    changed=copy.deepcopy(update);changed.update(operation_key='g2-memo');changed['document']['memo']='Corrected memo'
    edited=driver.run('update',changed,reason='Correct deposit memo')
    assert edited.current.version==2 and len(edited.effect.batch_ids)==2
    void=dict(operation_key='g2-void',deposit=posted.current.id,expected_version=2)
    canceled=driver.run('void',void,reason='Cancel retained deposit')
    assert canceled.current.status=='voided' and canceled.current.version==3
    assert all(not b.active for b in canceled.effect.bank_effects)
    before=driver.dump();replay=driver.run('post',request)
    assert replay.effect==posted.effect and replay.current==canceled.current
    assert driver.dump()==before
    repeated=driver.run('void',dict(void,operation_key='g2-second-void',expected_version=3),reason='Already canceled')
    assert not repeated.changed and repeated.current==canceled.current and repeated.effect.batch_ids==()
    with driver.session() as s:
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=?',(posted.current.id,)).fetchone()==(0,)
        assert s.company.raw.execute('SELECT count(*) FROM deposit_operations WHERE transaction_id=?',(posted.current.id,)).fetchone()==(5,)
        assert s.company.raw.execute('SELECT count(*) FROM transaction_revisions WHERE transaction_id=?',(posted.current.id,)).fetchone()==(2,)


def test_n1_actual_mixed_sources_claim_fences_and_n4_void(client,sale,driver):
    from tests.test_payment_receipts import posted, method
    from bookflow import BookflowError
    invoice=posted(client,sale['customer'],sale['item'],'100.00','G2-N1')
    payment_method=method(client)
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=payment_method,
        operation_key='g2-n1-payment',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=uf(client),payment_method=payment_method,date='2026-06-02',
        lines=[dict(item=sale['item'],quantity='1',unit_price='60')]),company=COMPANY)
    document=additional_document(client,sale,'20.00',cash='5.00')
    fee=client.account.create(name='G2 fee',type='expense',company=COMPANY)['id']
    document['additional'].append(dict(received_from=dict(kind='customer',id=sale['customer']),from_account=fee,amount='-3.00'))
    document['sources']=[dict(source_type='payment',source=payment['id'],expected_version=payment['version']),dict(source_type='sales_receipt',source=receipt['id'],expected_version=receipt['version'])]
    request=dict(operation_key='g2-n1-deposit',document=document)
    result=driver.run('post',request)
    uf_account=uf(client)
    assert (result.effect.financial.posting_total,result.effect.financial.subtotal,result.effect.financial.bank_total)==(18000,17700,17200)
    assert len(result.effect.memberships)==2 and len(result.effect.headers)==3
    settlement_guard=client.run('payment show',dict(payment=payment['id']),company=COMPANY)['settlement_guard']
    for command,body in (
        ('payment update',dict(payment=payment['id'],expected_version=payment['version']+1,operation_key='g2-block-payment',settlement_guard=settlement_guard,memo='New receipt memo')),
        ('sales-receipt update',dict(sales_receipt=receipt['id'],expected_version=receipt['version']+1,memo='New sale memo')),
        ('sales-receipt void',dict(sales_receipt=receipt['id'],expected_version=receipt['version']+1))):
        before=driver.dump()
        with pytest.raises(BookflowError) as error:client.run(command,body,company=COMPANY,reason='Must coordinate claimed receipt')
        assert error.value.code=='E_DEPOSIT_DEPENDENCY'
        # A rejected public command may run standard maintenance; financial,
        # membership and permanent operation rows must remain untouched.
        with driver.session() as s:
            assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships').fetchone()==(2,)
            assert s.company.raw.execute('SELECT count(*) FROM deposit_operations WHERE transaction_id=?',(result.current.id,)).fetchone()==(1,)
    with driver.session() as s:
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        gl=dict(s.company.raw.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? GROUP BY account_id',(result.current.id,)).fetchall())
        assert gl=={document['deposit_to']:17200,document['cash_back']['account']:500,fee:300,sale['income']:-2000,uf_account:-16000}
        assert s.company.raw.execute('SELECT amount_minor_units FROM applications WHERE paying_transaction_id=?',(payment['id'],)).fetchall()==[(10000,)]
    canceled=driver.run('void',dict(deposit=result.current.id,expected_version=1,operation_key='g2-n1-void'),reason='Retain original receipts')
    assert len(canceled.effect.memberships)==2
    with driver.session() as s:
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships').fetchone()==(0,)
        assert s.company.raw.execute('SELECT kind,amount_minor_units FROM deposit_memberships WHERE transaction_id=? ORDER BY rowid',(result.current.id,)).fetchall()==[('claim',10000),('claim',6000),('release',10000),('release',6000)]
        assert s.company.raw.execute('SELECT status,version FROM transactions WHERE id=?',(receipt['id'],)).fetchone()==('posted',receipt['version']+2)
    fresh=copy.deepcopy(request);fresh['operation_key']='g2-redeposit'
    for source in fresh['document']['sources']:source['expected_version']+=2
    redeposited=driver.run('post',fresh)
    assert redeposited.current.id!=result.current.id
    assert driver.run('post',request).current.status=='voided'


def test_n6_zero_bank_and_key_conflict_rollback(client,sale,driver):
    from bookflow import BookflowError
    document=additional_document(client,sale,cash='10.00')
    request=dict(operation_key='g2-zero',document=document)
    result=driver.run('post',request)
    assert result.effect.financial.bank_total==0 and result.effect.bank_effects==()
    with driver.session() as s:
        assert s.company.raw.execute('SELECT count(*) FROM posting_lines WHERE transaction_id=? AND account_id=?',(result.current.id,document['deposit_to'])).fetchone()==(0,)
    before=driver.dump()
    with pytest.raises(BookflowError) as error:
        driver.run('post',dict(request,document=dict(document,memo='Different intent')))
    assert error.value.code=='E_DEPOSIT_OPERATION_KEY_REUSED' and driver.dump()==before


def test_claim_competitor_and_stale_guard_preserve_full_rows(client,sale,driver):
    from bookflow import BookflowError
    from tests.test_payment_receipts import method
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='7',payment_method=method(client),operation_key='g2-race-receipt'),company=COMPANY)
    document=additional_document(client,sale)
    document.update(additional=[],sources=[dict(source_type='payment',source=payment['id'],expected_version=1)])
    request=dict(operation_key='g2-claim-one',document=document)
    ctx=Context.new(Interface.python,'private G2 race')
    with driver.session() as s:
        waiting=lifecycle.prepare(s,ctx,lifecycle.INPUTS['post'].model_validate(dict(request,operation_key='g2-claim-two')),'post')
    winner=driver.run('post',request)
    before=driver.dump()
    with pytest.raises(BookflowError) as error:
        with driver.session() as s:persistence.execute(s,ctx,waiting)
    assert error.value.code=='E_PREVIEW_STALE' and error.value.details['history']=='known_stale' and driver.dump()==before
    with pytest.raises(BookflowError) as error:
        client.run('payment void',dict(payment=payment['id'],expected_version=2,operation_key='g2-block-void'),reason='Claimed receipt',company=COMPANY)
    assert error.value.code=='E_DEPOSIT_DEPENDENCY'
    changed=dict(operation_key='g2-change',deposit=winner.current.id,expected_version=1,document=replacement(winner,document))
    changed['document']['sources'][0]['expected_version']=2
    with driver.session() as s:
        preview=lifecycle.prepare(s,Context.new(Interface.python,'G2 guard',reason='Guarded change'),lifecycle.INPUTS['update'].model_validate(changed),'update')
    changed['dependency_guard']=preview.dependency_guard
    changed['document']['memo']='Different complete intent'
    before=driver.dump()
    with pytest.raises(BookflowError) as error:driver.run('update',changed,reason='Guarded change')
    assert error.value.code=='E_VALIDATION' and error.value.details=={'field':'dependency_guard','reason':'invalid_guard'} and driver.dump()==before


@pytest.mark.parametrize('failure_table',['audit_events','transactions','deposit_profiles','deposit_memberships','bank_effect_versions','deposit_operations','deposit_operation_items'])
def test_fault_rolls_back_every_owned_row_and_key(client,sale,driver,monkeypatch,failure_table):
    from tests.test_payment_receipts import method
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='7',payment_method=method(client),operation_key='g2-fault-receipt'),company=COMPANY)
    document=additional_document(client,sale)
    document.update(additional=[],sources=[dict(source_type='payment',source=payment['id'],expected_version=1)])
    before=driver.dump();original=sa.engine.Connection.execute;hit=[]
    def fail(connection,statement,*args,**kwargs):
        if getattr(statement,'is_insert',False) and getattr(getattr(statement,'table',None),'name',None)==failure_table:
            hit.append(failure_table);raise RuntimeError('owned G2 insertion fault')
        return original(connection,statement,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(sa.engine.Connection,'execute',fail)
        with pytest.raises(RuntimeError,match='owned G2 insertion fault'):
            driver.run('post',dict(operation_key='g2-fault-key',document=document))
    assert hit==[failure_table] and driver.dump()==before
    recovered=driver.run('post',dict(operation_key='g2-fault-key',document=document))
    assert recovered.changed and not recovered.idempotent_replay


def test_metadata_origins_bank_identity_closed_dates_and_custom_values(client,sale,driver):
    from bookflow import BookflowError
    field=client.run('custom-field create',dict(name='Deposit note',kind='text',scopes=['deposit']),company=COMPANY)
    document=additional_document(client,sale)
    document['custom_fields']={field['id']:'Original value'}
    first=driver.run('post',dict(operation_key='g2-custom',document=document))
    with driver.session() as s:
        key=s.company.raw.execute('SELECT id FROM bank_effect_keys WHERE transaction_id=?',(first.current.id,)).fetchone()[0]
        original=s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(first.current.revision_id,)).fetchone()[0]
        line=s.company.raw.execute("SELECT line_id FROM deposit_row_keys WHERE transaction_id=? AND kind='additional'",(first.current.id,)).fetchone()[0]
    bank=client.account.create(name='G2 other bank',type='bank',company=COMPANY)['id']
    replacement_doc=replacement(first,document)
    replacement_doc['additional'][0]['line_id']=line
    replacement_doc.update(deposit_to=bank,custom_fields={field['id']:'Changed value'})
    revised=driver.run('update',dict(operation_key='g2-bank-change',deposit=first.current.id,expected_version=1,document=replacement_doc),reason='Move actual bank destination')
    assert revised.effect.reversal is not None and revised.effect.reversal.legs[0].signed_debit==-1000
    with driver.session() as s:
        assert s.company.raw.execute('SELECT id FROM bank_effect_keys WHERE transaction_id=?',(first.current.id,)).fetchall()==[(key,)]
        assert s.company.raw.execute('SELECT version,account_id,signed_debit FROM bank_effect_versions WHERE key_id=? ORDER BY version',(key,)).fetchall()==[(1,document['deposit_to'],1000),(2,bank,1000)]
        assert s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(first.current.revision_id,)).fetchone()[0]==original
        assert json.loads(s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(revised.current.revision_id,)).fetchone()[0])[field['id']]['value']=='Changed value'
    info=client.company.show(company=COMPANY)
    client.company.update(expected_version=info['info_version'],closing_date='2026-06-03',company=COMPANY)
    before=driver.dump()
    with pytest.raises(BookflowError) as error:
        driver.run('void',dict(operation_key='g2-closed',deposit=first.current.id,expected_version=2),reason='Closed date cannot cancel')
    assert error.value.code=='E_PERIOD_CLOSED' and driver.dump()==before
    assert driver.run('post',dict(operation_key='g2-custom',document=document)).current==revised.current


def test_independent_validator_rejects_balanced_wrong_owner(client,sale,driver):
    from bookflow import BookflowError
    from bookflow.company.deposit_persistence_validation import validate
    document=additional_document(client,sale,cash='1.00')
    request=lifecycle.INPUTS['post'].model_validate(dict(operation_key='g2-validate',document=document))
    ctx=Context.new(Interface.python,'private validator witness')
    with driver.session() as s:
        plan=lifecycle.prepare(s,ctx,request,'post');bundle=persistence.build(s,ctx,plan)
        validate(s,ctx,plan,bundle)
        wrong=copy.deepcopy(bundle)
        source=next(r for r in wrong['pending']['posting_line_sources'] if r['deposit_component_id']==wrong['pending']['deposit_components'][0]['id'])
        source['deposit_component_id']=wrong['pending']['deposit_components'][-1]['id']
        source['document_line_id']=wrong['pending']['deposit_components'][-1]['document_line_id']
        with pytest.raises(BookflowError):validate(s,ctx,plan,wrong)
        wrong_bank=copy.deepcopy(bundle)
        wrong_bank['pending']['bank_effect_versions'][0]['signed_debit']+=1
        wrong_bank['pending']['bank_effect_versions'][0]['statement_amount']+=1
        with pytest.raises(BookflowError):validate(s,ctx,plan,wrong_bank)


def test_simultaneous_writers_have_one_complete_claim(client,sale,driver):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bookflow import BookflowError
    from tests.test_payment_receipts import method
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='7',payment_method=method(client),operation_key='g2-concurrent-receipt'),company=COMPANY)
    document=additional_document(client,sale)
    document.update(additional=[],sources=[dict(source_type='payment',source=payment['id'],expected_version=1)])
    barrier=Barrier(2)
    def writer(key):
        barrier.wait(timeout=10)
        try:return driver.run('post',dict(operation_key=key,document=document))
        except BookflowError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(writer,('g2-simultaneous-a','g2-simultaneous-b')))
    # Real RootLock admission may reject the overlapping caller before SQL.
    # After the winning writer releases it, retry that SAME losing request once;
    # retain the business claim oracle and prove its retry performs no writes.
    for index,value in enumerate(values):
        if value=='E_DB_BUSY':
            before=driver.dump()
            try:values[index]=driver.run('post',dict(operation_key=('g2-simultaneous-a','g2-simultaneous-b')[index],document=document))
            except BookflowError as error:values[index]=error.code
            assert driver.dump()==before
    winners=[v for v in values if isinstance(v,LifecycleOutput)]
    assert len(winners)==1 and [v for v in values if isinstance(v,str)]==['E_DEPOSIT_SOURCE_CLAIMED']
    with driver.session() as s:
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships').fetchone()==(1,)
        assert s.company.raw.execute('SELECT count(*) FROM deposit_operations').fetchone()==(1,)
        assert s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=?',(document['deposit_to'],)).fetchone()==(700,)


def test_source_memo_origins_and_failed_replacement_release_are_atomic(client,sale,driver,monkeypatch):
    from tests.test_payment_receipts import method
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='7',memo='Captured source memo',payment_method=method(client),operation_key='g2-memo-receipt'),company=COMPANY)
    document=additional_document(client,sale)
    document.update(additional=[],sources=[dict(source_type='payment',source=payment['id'],expected_version=1,memo_override=None)])
    first=driver.run('post',dict(operation_key='g2-source-memo',document=document))
    row=first.effect.financial.intent.sources[0]
    assert (row.memo_origin,row.memo)==('entered',None)
    replacement_doc=replacement(first,document)
    replacement_doc['sources']=[dict(source_type='payment',source=payment['id'],expected_version=2)]
    request=dict(operation_key='g2-source-restore',deposit=first.current.id,expected_version=1,document=replacement_doc)
    original=sa.engine.Connection.execute;hit=[];before=driver.dump()
    def fail(connection,statement,*args,**kw):
        if getattr(statement,'is_insert',False) and getattr(getattr(statement,'table',None),'name',None)=='deposit_current_memberships':
            hit.append(True);raise RuntimeError('replacement projection insertion fault')
        return original(connection,statement,*args,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(sa.engine.Connection,'execute',fail)
        with pytest.raises(RuntimeError,match='replacement projection'):
            driver.run('update',request,reason='Use captured source memo')
    assert hit==[True] and driver.dump()==before
    changed=driver.run('update',request,reason='Use captured source memo')
    after=changed.effect.financial.intent.sources[0]
    assert after.row_id==row.row_id and after.ordinal==row.ordinal
    assert (after.memo_origin,after.memo)==('source','Captured source memo')
    assert len(changed.effect.memberships)==2 and changed.current.version==2
    with driver.session() as s:
        from bookflow.company.payment_authority import record_transactions,_EventCohort,event_requirements
        expected={first.current.id,payment['id']}
        assert record_transactions(s.company,'deposit_operation',changed.operation_id)==expected
        cohort=_EventCohort(s.company,[changed.effect.audit_event_id])
        assert cohort._walk(('deposit_operation',changed.operation_id))==expected
        assert cohort.requirements(changed.effect.audit_event_id)==event_requirements(s.company,changed.effect.audit_event_id)
        assert s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(payment['id'],)).fetchone()==(3,)


def test_exact_hook_recovery_and_mismatch_reason_gate_are_readonly(client,sale,driver):
    from bookflow import BookflowError
    from bookflow.company import deposit_operations
    document=additional_document(client,sale)
    body=dict(operation_key='g2-hook',document=document)
    posted=driver.run('post',body)
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'Exact permanent recovery')
        result=deposit_operations.permanent_recovery(lifecycle.INPUTS['post'].model_validate(body),ctx,s,'post')
        assert isinstance(result,registry.MatchedRecovery) and result.output.effect==posted.effect
        assert result.output.idempotent_replay and not result.output.new_effect
        saved=s.company.raw.execute('SELECT request_snapshot FROM deposit_operations WHERE id=?',(posted.operation_id,)).fetchone()[0]
        saved=json.loads(saved)
        assert saved['input']['document']==body['document']
        assert saved['resolved_transaction_ids']==[posted.current.id]
        assert saved['resolved_identity_map']['document']==posted.current.id
        assert saved['resolved_identity_map']['bank']==document['deposit_to']
        assert saved['resolved_identity_map']['additional']==[dict(row=posted.effect.financial.intent.additional[0].row_id,account=sale['income'],party=sale['customer'])]
        mismatch=lifecycle.INPUTS['void'].model_validate(dict(operation_key='g2-hook',deposit=posted.current.id,expected_version=1))
        assert deposit_operations.permanent_recovery(mismatch,ctx,s,'void') is None
        with pytest.raises(BookflowError) as error:lifecycle.prepare(s,ctx,mismatch,'void')
        assert error.value.code=='E_REASON_REQUIRED'
        ctx=Context.new(Interface.python,'New write gates',reason='A different command')
        with pytest.raises(BookflowError) as error:lifecycle.prepare(s,ctx,mismatch,'void')
        assert error.value.code=='E_DEPOSIT_OPERATION_KEY_REUSED'
    assert driver.dump()==before


def test_all_custom_kinds_exact_snapshots_clear_and_history(client,sale,driver):
    values={'text':'Keep this','number':'123456789.123456789','date':'2026-06-01','bool':False,'choice':'Web'}
    fields={kind:client.run('custom-field create',dict(name='G2 '+kind,kind=kind,scopes=['deposit'],
        **({'choices':[{'value':'Web'},{'value':'Phone'}]} if kind=='choice' else {})),company=COMPANY) for kind in values}
    document=additional_document(client,sale)
    document['custom_fields']={fields[k]['id']:v for k,v in values.items()}
    posted=driver.run('post',dict(operation_key='g2-all-custom',document=document))
    with driver.session() as s:
        original=s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(posted.current.revision_id,)).fetchone()[0]
        snapshot=json.loads(original)
        assert {k:snapshot[f['id']]['value'] for k,f in fields.items()}==values
    changed=replacement(posted,document);changed['memo']='Clear optional values; retain financial facts'
    updated=driver.run('update',dict(operation_key='g2-clear-custom',deposit=posted.current.id,expected_version=1,document=changed),reason='Clear optional custom fields')
    assert updated.effect.financial.legs==posted.effect.financial.legs
    with driver.session() as s:
        assert s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(posted.current.revision_id,)).fetchone()==(original,)
        assert json.loads(s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(updated.current.revision_id,)).fetchone()[0])=={}


def test_n5_closed_source_open_deposit_and_removed_source_readd(client,sale,driver):
    from tests.test_payment_receipts import method
    from bookflow import BookflowError
    source=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=uf(client),payment_method=method(client),date='2026-06-01',
        lines=[dict(item=sale['item'],quantity='1',unit_price='6')]),company=COMPANY)
    info=client.company.show(company=COMPANY)
    client.company.update(expected_version=info['info_version'],closing_date='2026-06-01',company=COMPANY)
    document=additional_document(client,sale,'1.00')
    document['sources']=[dict(source_type='sales_receipt',source=source['id'],expected_version=1)]
    posted=driver.run('post',dict(operation_key='g2-old-source',document=document))
    oldrow=posted.effect.financial.intent.sources[0]
    removed=replacement(posted,document);removed['sources']=[]
    changed=driver.run('update',dict(operation_key='g2-remove-source',deposit=posted.current.id,expected_version=1,document=removed),reason='Remove receipt from this deposit')
    added=replacement(changed,removed);added['sources']=[dict(source_type='sales_receipt',source=source['id'],expected_version=3)]
    readded=driver.run('update',dict(operation_key='g2-readd-source',deposit=posted.current.id,expected_version=2,document=added),reason='Include released receipt again')
    newrow=readded.effect.financial.intent.sources[0]
    assert newrow.row_id!=oldrow.row_id and newrow.ordinal>oldrow.ordinal
    assert readded.effect.financial.bank_total==700
    with driver.session() as s:
        assert s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(source['id'],)).fetchone()==(4,)
        assert set(s.company.raw.execute('SELECT transaction_id FROM deposit_operation_targets WHERE operation_id=?',(changed.operation_id,)).fetchall())=={(source['id'],),(posted.current.id,)}
    voided=driver.run('void',dict(operation_key='g2-old-source-void',deposit=posted.current.id,expected_version=3),reason='Void open-date deposit only')
    assert voided.current.effective_bank_total==0
    with driver.session() as s:
        assert s.company.raw.execute('SELECT status FROM transactions WHERE id=?',(source['id'],)).fetchone()==('posted',)


def test_inactive_bank_requires_new_posting_account_but_allows_exact_void(client,sale,driver):
    from bookflow import BookflowError
    document=additional_document(client,sale)
    original=driver.run('post',dict(operation_key='g2-inactive-bank',document=document))
    with pytest.raises(BookflowError) as blocked:
        client.account.deactivate(account=document['deposit_to'],company=COMPANY)
    assert blocked.value.code=='E_RECORD_IN_USE'
    # Same owned legacy/import seam as test_row8_journal's inactive reversal:
    # normal account admission correctly forbids manufacturing this state.
    with driver.session() as s:
        s.company.conn.execute(c.accounts.update().where(c.accounts.c.id==document['deposit_to']).values(active=False))
    changed=replacement(original,document);changed['memo']='Would create a replacement'
    before=driver.dump()
    with pytest.raises(BookflowError):
        driver.run('update',dict(operation_key='g2-inactive-update',deposit=original.current.id,expected_version=1,document=changed),reason='Must not post to inactive bank')
    assert driver.dump()==before
    voided=driver.run('void',dict(operation_key='g2-inactive-void',deposit=original.current.id,expected_version=1),reason='Exact inverse of inactive historical bank')
    assert voided.current.status=='voided' and voided.effect.reversal.bank_total==1000
    assert sum(leg.signed_debit for leg in voided.effect.reversal.legs)==0


def test_complete_inverse_rejects_balanced_omission_before_dml(client,sale,driver):
    from bookflow import BookflowError
    from bookflow.company.deposit_persistence_validation import validate
    document=additional_document(client,sale)
    posted=driver.run('post',dict(operation_key='g2-whole-inverse',document=document))
    ctx=Context.new(Interface.python,'Complete inverse oracle',reason='Cancel the full original effect')
    with driver.session() as s:
        inp=lifecycle.INPUTS['void'].model_validate(dict(operation_key='g2-whole-inverse-void',deposit=posted.current.id,expected_version=1))
        plan=lifecycle.prepare(s,ctx,inp,'void');bundle=persistence.build(s,ctx,plan)
        validate(s,ctx,plan,bundle)
        wrong=copy.deepcopy(bundle)
        wrong['pending']['posting_lines']=[]
        wrong['pending']['posting_line_sources']=[]
        with pytest.raises(BookflowError):validate(s,ctx,plan,wrong)


def test_inaccessible_claim_discloses_no_owner_or_claim_details(monkeypatch):
    from bookflow.company import deposit_dependencies
    from bookflow import BookflowError
    def denied(*args,**kwargs):
        raise BookflowError('E_PERMISSION',details={'capability':'hidden-work'})
    monkeypatch.setattr(deposit_dependencies,'authorize',denied)
    claim={'transaction_id':'undisclosed-owner','membership_id':'undisclosed-claim'}
    with pytest.raises(BookflowError) as error:
        deposit_dependencies.claim_details(object(),'authorized-source',claim)
    assert error.value.code=='E_DEPOSIT_DEPENDENCY'
    assert not error.value.details
