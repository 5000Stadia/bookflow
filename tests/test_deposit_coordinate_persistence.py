"""Actual private C producer, complete SQL effects and caller-owned rollback."""
import copy
import json
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.core.context import Context, Interface
from bookflow.core.publication import OSBinding
from bookflow.company import schema as c, deposit_coordination as coord
from bookflow.company import deposit_coordinate_persistence as persistence
from bookflow.company.deposit_coordinate_models import CoordinateInput
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_deposit_sources import uf
from tests.test_deposit_lifecycle import driver, additional_document, replacement


@pytest.fixture
def n2(client,sale,driver):
    invoice=posted(client,sale['customer'],sale['item'],'100','C-N2')
    pm=method(client)
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=pm,
        operation_key='C-receive',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=pm,
        lines=[dict(item=sale['item'],quantity='1',unit_price='60')]),company=COMPANY)
    document=additional_document(client,sale,'20',cash='5')
    fee=client.account.create(name='C fee',type='expense',company=COMPANY)['id']
    document['additional'].append(dict(received_from=dict(kind='customer',id=sale['customer']),from_account=fee,amount='-3'))
    document['sources']=[dict(source_type='payment',source=payment['id'],expected_version=1),dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    post=driver.run('post',dict(operation_key='C-deposit',document=document))
    body=replacement(post,document)
    body['sources']=[dict(source_result=True,source=payment['id']),dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    inp=CoordinateInput(deposit=post.current.id,expected_version=1,operation_key='C-coordinate',replacement=dict(mode='document',document=body),
        source_action=dict(kind='payment_update',input=dict(payment=payment['id'],expected_version=2,amount='120',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)])))
    return inp,post,document,payment,receipt,invoice


def prepare(s,ctx,inp):
    binding=OSBinding.from_session(s)
    first=coord.prepare(s,ctx,inp,binding=binding)
    return coord.prepare(s,ctx,inp.model_copy(update={'dependency_guard':first.dependency_guard}),binding=binding)


def test_n2_full_source_deposit_rows(client,sale,driver,n2):
    inp,post,document,payment,receipt,invoice=n2
    ctx=Context.new(Interface.python,'C owned witness',reason='Correct captured cash and deposit')
    with pytest.raises(BookflowError) as caught:
        client.run('payment update',dict(payment=payment['id'],expected_version=2,amount='120',operation_key='ordinary-blocked',
            invoice_versions=[dict(invoice=invoice['id'],expected_version=2)]),company=COMPANY,reason=ctx.reason)
    assert caught.value.code=='E_DEPOSIT_DEPENDENCY'
    before=driver.dump()
    with driver.session() as s:
        p=prepare(s,ctx,inp)
        assert tuple(s.company.raw.iterdump())==before
        count=s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]
        prior_balances=dict(s.company.raw.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines GROUP BY account_id'))
        candidate=persistence.build(s,ctx,p)
        for table,values in candidate.deposit_bundle['pending'].items():
            for value in values:
                missing=set(getattr(c,table).columns.keys())-set(value)
                assert not missing or (table=='posting_line_sources' and missing<={'tax_component_id','payment_component_id','deposit_component_id'}), (table,missing)
        result=persistence.execute(s,ctx,p)
        assert (result.effect.deposit.financial.posting_total,result.effect.deposit.financial.subtotal,result.current.revision_bank_total,result.current.revision_cash_back)==(20000,19700,19200,500)
        expected={payment['id']:(2,3),receipt['id']:(2,3),invoice['id']:(2,3),post.current.id:(1,2)}
        assert {h.after.id:(h.before.version,h.after.version) for h in result.effect.headers}==expected
        assert s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()[0]==count+1
        assert s.company.raw.execute('SELECT count(*) FROM deposit_operations WHERE command=?',('deposit coordinate',)).fetchone()[0]==1
        assert s.company.raw.execute('SELECT count(*) FROM payment_operations WHERE operation_key=?',('C-coordinate',)).fetchone()[0]==0
        balances=dict(s.company.raw.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines GROUP BY account_id'))
        assert balances[document['deposit_to']]==19200
        uf_id=s.company.raw.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
        assert balances[uf_id]==0
        ar=invoice['revision']['profile']['control_account']['id']
        assert balances[ar]-prior_balances[ar]==-2000
        controlled=[payment['id'],receipt['id'],invoice['id'],post.current.id]
        owned=dict(s.company.raw.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id IN (?,?,?,?) GROUP BY account_id',controlled))
        assert owned[ar]==-2000 and owned[uf_id]==0 and owned[document['deposit_to']]==19200
        claims=s.company.raw.execute('SELECT kind,amount_minor_units FROM deposit_memberships WHERE source_transaction_id=? ORDER BY rowid',(payment['id'],)).fetchall()
        assert claims==[('claim',10000),('release',10000),('claim',12000)]
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        final=tuple(s.company.raw.iterdump())
        replay=persistence.recover(s,ctx,inp,p.binding)
        assert replay.effect==result.effect and replay.idempotent_replay and not replay.new_effect
        assert tuple(s.company.raw.iterdump())==final
    original=driver.run('post',dict(operation_key='C-deposit',document=document))
    assert original.effect==post.effect and original.effect.after.revision_bank_total==17200
    assert original.current.revision_bank_total==19200
    later=driver.run('post',dict(operation_key='C-later-number',document=dict(document,sources=[])))
    assert later.current.number==str(int(post.current.number)+1)
    assert later.current.revision_bank_total==1200


@pytest.mark.parametrize('mode',['noop','payment_void','receipt_void','receipt_update','direct_bank'])
def test_complete_action_and_noeffect_paths(client,sale,driver,n2,mode):
    inp,post,document,payment,receipt,invoice=n2
    ctx=Context.new(Interface.python,'C action witness',reason='Correct one owned source')
    wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    if mode=='noop':wire['source_action']['input']['amount']='100'
    elif mode=='payment_void':
        wire['source_action']=dict(kind='payment_void',payment=payment['id'],expected_version=2,unapply='all_active')
        wire['replacement']['document']['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    elif mode in ('receipt_void','receipt_update'):
        wire['source_action']=dict(kind='sales_receipt_void',sales_receipt=receipt['id'],expected_version=2) if mode=='receipt_void' else dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,memo='Correct receipt memo'))
        wire['replacement']['document']['sources']=[dict(source_type='payment',source=payment['id'],expected_version=2)]
        if mode=='receipt_update':wire['replacement']['document']['sources'].append(dict(source_result=True,source=receipt['id']))
    else:
        direct=client.account.create(name='C direct bank',type='bank',company=COMPANY)['id']
        wire['source_action']['input']['deposit_to']=direct
        wire['replacement']['document']['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    inp=CoordinateInput.model_validate(wire)
    with driver.session() as s:
        p=prepare(s,ctx,inp)
        before={name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in ('transactions','posting_lines','deposit_memberships','bank_effect_versions','custom_field_values','sequences')}
        from bookflow.company import deposit_composition
        original=deposit_composition.preview_source_effect(s,ctx,p.resolution.source.action,provenance=p.resolution.source.provenance)
        payment_mode=mode not in ('receipt_void','receipt_update')
        assert coord.canonical_source_data(original.plan,payment=payment_mode)==coord.canonical_source_data(p.resolution.source.plan,payment=payment_mode)
        untouched=copy.deepcopy(coord.canonical_source_data(p.resolution.source.plan,payment=payment_mode))
        result=persistence.execute(s,ctx,p)
        assert coord.canonical_source_data(p.resolution.source.plan,payment=payment_mode)==untouched
        from bookflow.company import document_effects
        for table in type(result.effect.source.inserted).model_fields:
            owner=getattr(c,table)
            for value in getattr(result.effect.source.inserted,table):
                # All source owners have explicit complete physical row keys.
                keys=[column.name for column in owner.primary_key]
                actual=[dict(row) for row in s.company.conn.execute(sa.select(owner).where(*(owner.c[key]==getattr(value,key) for key in keys))).mappings()]
                assert actual==[value.model_dump()]
        if mode=='noop':
            assert not result.changed and not result.new_effect and result.effect.headers==()
            assert result.effect.deposit.batch_ids==() and result.effect.deposit.memberships==()
            assert before=={name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in before}
        else:
            assert result.changed and result.effect.headers
            assert result.current.revision_bank_total=={'payment_void':7200,'receipt_void':11200,'receipt_update':17200,'direct_bank':7200}[mode]
            if mode=='payment_void':
                assert [(v.kind,v.amount_minor_units) for v in result.effect.source.inserted.applications]==[('unapply',10000)]
                assert result.effect.source.after_header.status=='voided'
            if mode=='receipt_void':assert result.effect.source.after_header.status=='voided'
            if mode=='direct_bank':
                assert result.effect.source.bank_changes.kind=='changed_effects'
                assert sum(v.signed_debit for v in result.effect.source.bank_changes.after)==12000
                from bookflow.company import reconciliation_adapters as r0
                _,actual=r0.enumerate_graph(r0.graph(s,[payment['id']]))
                assert actual==result.effect.source.bank_changes.after

        dump=tuple(s.company.raw.iterdump())
        replay=persistence.recover(s,ctx,inp,p.binding)
        assert replay.effect==result.effect and tuple(s.company.raw.iterdump())==dump
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_retain_none_requires_actual_prior_unapply_and_preserves_history(client,sale,driver,n2):
    inp,post,document,payment,receipt,invoice=n2
    ctx=Context.new(Interface.python,'C retain-none',reason='Cancel the unapplied deposited receipt')
    wire=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
    wire['source_action']=dict(kind='payment_void',payment=payment['id'],expected_version=2,unapply='retain_none')
    wire['replacement']['document']['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    before=driver.dump()
    with driver.session() as s:
        with pytest.raises(BookflowError):prepare(s,ctx,CoordinateInput.model_validate(wire))
        application=s.company.raw.execute("SELECT id FROM applications WHERE paying_transaction_id=? AND kind='apply'",(payment['id'],)).fetchone()[0]
    assert driver.dump()==before
    client.run('payment unapply',dict(payment=payment['id'],expected_version=2,operation_key='C-prior-explicit-unapply',
        applications=[dict(application_id=application,invoice_expected_version=2)]),company=COMPANY,reason='Explicitly remove the allocation first')
    wire['source_action']['expected_version']=3
    request=CoordinateInput.model_validate(wire)
    with driver.session() as s:
        result=persistence.execute(s,ctx,prepare(s,ctx,request))
        assert result.current.revision_bank_total==7200
        assert result.effect.source.after_header.status=='voided'
        assert result.effect.source.inserted.applications==() and result.effect.source.inserted.application_allocations==()
        assert len(result.effect.source.before.applications)==2
        assert invoice['id'] in result.effect.target_ids
        assert {v.after.id for v in result.effect.headers}=={payment['id'],receipt['id'],post.current.id}
        assert s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(invoice['id'],)).fetchone()==(3,)
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        frozen=tuple(s.company.raw.iterdump())
        assert persistence.recover(s,ctx,request,OSBinding.from_session(s)).effect==result.effect
        assert tuple(s.company.raw.iterdump())==frozen
