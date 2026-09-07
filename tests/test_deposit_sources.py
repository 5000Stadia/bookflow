"""Actual source producers, full-plan preservation and stored cash ownership."""
import copy
import sqlite3
import pytest
from bookflow.company import deposit_sources, deposit_composition, deposits
from bookflow.company.deposit_models import Intent, SourceRow, CashBack
from bookflow.core import registry
from bookflow.core.errors import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path
from tests.test_deposit_g1 import account, additional, nets


def uf(client):
    with sqlite3.connect(database_path(client)) as db:
        return db.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]


def observe(client, monkeypatch, noun, identity):
    command=registry.get(noun+' show');original=command.plan;found=[]
    def read(inp,ctx,s):
        found.append(deposit_sources.load(s,identity))
        return original(inp,ctx,s)
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',read)
        client.run(noun+' show',{noun.replace('-','_'):identity},company=COMPANY)
    return found[0]


def source_row(source,ordinal):
    return SourceRow(row_id='row'+str(ordinal),ordinal=ordinal,source=source,occurrences=deposits.occurrences(source),memo=source.source_memo,memo_origin='source')


def test_n1_actual_fully_applied_payment_and_sales_cash(client,sale,monkeypatch):
    invoice=posted(client,sale['customer'],sale['item'],'100.00','DEPOSIT-N1')
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=method(client),
        operation_key='deposit-n1-payment',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=uf(client),payment_method=client.run('payment-method list',{},company=COMPANY)['items'][0]['id'],date='2026-06-02',lines=[dict(item=sale['item'],quantity='1',unit_price='60')]),company=COMPANY)
    before=snapshots(client)
    payment=observe(client,monkeypatch,'payment',paid['id']);sales=observe(client,monkeypatch,'sales-receipt',receipt['id'])
    assert payment.cash_minor_units==10000 and paid['current']['available_minor_units']==0
    assert sales.cash_minor_units==6000 and payment.uf_account==sales.uf_account
    effect=deposits.prepare(Intent(deposit_id='n1',date='2026-06-03',currency='USD',bank=account('bank'),sources=(source_row(payment,1),source_row(sales,2)),
        additional=(additional('income',3,2000),additional('fee',4,-300,'expense')),cash_back=CashBack(account=account('cash','other_current_asset'),units=500)))
    assert (effect.posting_total,effect.subtotal,effect.bank_total)==(18000,17700,17200)
    assert nets(effect)=={'bank':17200,'cash':500,'feeacct':300,payment.uf_account:-16000,'incomeacct':-2000}
    assert snapshots(client)==before
    # Independently enumerate source GL + proposed deposit; applications remain intact.
    with sqlite3.connect(database_path(client)) as db:
        ar=invoice['revision']['profile']['control_account']['id']
        amounts={a:db.execute('SELECT coalesce(sum(debit_minor_units-credit_minor_units),0) FROM posting_lines WHERE transaction_id IN (?,?,?) AND account_id=?',
                 (invoice['id'],paid['id'],receipt['id'],a)).fetchone()[0] for a in (ar,payment.uf_account,sale['income'])}
        assert amounts=={ar:0,payment.uf_account:16000,sale['income']:-16000}
        assert db.execute('SELECT amount_minor_units FROM applications WHERE paying_transaction_id=?',(paid['id'],)).fetchall()==[(10000,)]
    amounts.update({k:amounts.get(k,0)+v for k,v in nets(effect).items()})
    assert amounts=={ar:0,payment.uf_account:0,sale['income']:-16000,'bank':17200,'cash':500,'feeacct':300,'incomeacct':-2000}


def compare_plans(old,new):
    """Compare every plan-data field, mapping only freshly allocated ULIDs/times."""
    from pydantic import BaseModel
    from dataclasses import is_dataclass,asdict
    def plain(x):
        if isinstance(x,BaseModel):return plain(x.model_dump(mode='json'))
        if is_dataclass(x):return plain(asdict(x))
        if isinstance(x,dict):return {k:plain(v) for k,v in x.items()}
        if isinstance(x,(tuple,list)):return [plain(v) for v in x]
        return x
    old=plain(old);new=plain(new)
    mapping={}
    for name,rows in old['pending'].items():
        assert len(rows)==len(new['pending'][name])
        for a,b in zip(rows,new['pending'][name]):
            if 'id' in a:mapping[b['id']]=a['id']
    for a,b in zip(old.get('billing_allocations',()),new.get('billing_allocations',())):
        mapping[b['id']]=a['id']
    for key in ('event','operation_id','at'):
        if old.get(key) is not None:mapping[new[key]]=old[key]
    def rewrite(x):
        if isinstance(x,dict):return {mapping.get(k,k):rewrite(v) for k,v in x.items()}
        if isinstance(x,list):return [rewrite(v) for v in x]
        if isinstance(x,str):
            if x in mapping:return mapping[x]
            # Snapshots contain generated references; parse without discarding any field.
            import json
            try:
                value=json.loads(x)
            except (ValueError,TypeError):return x
            if isinstance(value,(dict,list)):return rewrite(value)
        return x
    assert rewrite(new)==rewrite(old)


def compose_observer(monkeypatch,noun,args,callback):
    command=registry.get(noun+' update');original=command.plan
    def witness(inp,ctx,s):
        from bookflow.core import clock
        moment=clock.now_iso()
        monkeypatch.setattr(clock,'now_iso',lambda:moment)
        plan=original(inp,ctx,s)
        composed=deposit_composition.prepare(s,ctx,inp,noun.replace('-','_')+'_update',expected_fingerprint=plan.preview.facts_fingerprint)
        compare_plans(plan.data,composed.plan.data)
        with pytest.raises(BookflowError) as caught:
            deposit_composition.prepare(s,ctx,inp,noun.replace('-','_')+'_update',expected_fingerprint='0'*64)
        assert caught.value.code=='E_PREVIEW_STALE'
        callback(plan,composed,s)
        return plan
    monkeypatch.setattr(command,'plan',witness)


def test_actual_payment_correction_composes_full_plan_and_stored_projection(client,sale,monkeypatch):
    invoice=posted(client,sale['customer'],sale['item'],'100','DEPOSIT-COMPOSE')
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=method(client),operation_key='deposit-compose-pay',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    args=dict(payment=payment['id'],expected_version=1,amount='120',operation_key='deposit-compose-update',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)])
    before=snapshots(client);seen=[]
    def check(plan,composed,s):
        assert composed.cash.cash_minor_units==12000
        assert len(composed.plan.data['changed_headers'])==1
        assert len(composed.plan.data['pending']['application_allocations'])==2
        seen.append(composed.cash)
    with monkeypatch.context() as patch:
        compose_observer(patch,'payment',args,check)
        client.run('payment update',args,company=COMPANY,reason='Correct captured cash',dry_run=True)
    assert snapshots(client)==before and len(seen)==1
    committed=client.run('payment update',args,company=COMPANY,reason='Correct captured cash')
    stored=observe(client,monkeypatch,'payment',payment['id'])
    assert stored.cash_minor_units==seen[0].cash_minor_units==12000
    assert [(c.key,c.capacity,c.cash,c.credit_owner_party,c.credit_owner_ar) for c in stored.components]==[(c.key,c.capacity,c.cash,c.credit_owner_party,c.credit_owner_ar) for c in seen[0].components]


def test_linked_billing_receipt_complete_source_plan(client,sale,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    source=accepted(client,sale)
    receipt=bill(client,source,verb='sales-receipt',deposit_to=uf(client),payment_method=method(client),amount_received='24.68')
    args=dict(sales_receipt=receipt['id'],expected_version=1,memo='Captured billing deposit source')
    before=snapshots(client);seen=[]
    def check(plan,composed,s):
        assert composed.cash.cash_minor_units==2468
        assert composed.plan.data['billing_allocations']
        assert composed.plan.preview.revision.billing_sources
        assert any(c.sale_line.allocation_proof is not None for c in composed.cash.components)
        seen.append(composed.cash)
    with monkeypatch.context() as patch:
        compose_observer(patch,'sales-receipt',args,check)
        client.run('sales-receipt update',args,company=COMPANY,reason='Preserve billed work',dry_run=True)
    assert len(seen)==1 and snapshots(client)==before


@pytest.mark.timeout(600)
def test_complete_403_settlement_source_plan_retains_billing_graph(client,sale,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    first=bill(client,accepted(client,sale))
    invoices=[first]+[posted(client,sale['customer'],sale['item'],'1',f'DEPOSIT-403-{i}') for i in range(402)]
    selection=client.run('payment selection create',dict(mode='new_receipt',customer=sale['customer'],date='2026-06-02',amount='4.03'),company=COMPANY)
    for offset in range(0,403,200):
        selection=client.run('payment selection update',dict(selection=selection['id'],expected_version=selection['version'],set_items=[
            dict(invoice=i['id'],expected_version=1,amount='0.01') for i in invoices[offset:offset+200]]),company=COMPANY)
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='4.03',payment_method=method(client),operation_key='deposit-full403',
        applications=dict(mode='selection',selection=selection['id'],expected_version=selection['version'])),company=COMPANY)
    guard=client.run('payment show',dict(payment=paid['id']),company=COMPANY)['settlement_guard']
    args=dict(payment=paid['id'],expected_version=1,amount='5.03',settlement_guard=guard,operation_key='deposit-full403-correction')
    before=snapshots(client);seen=[]
    def check(plan,composed,s):
        data=composed.plan.data
        assert composed.cash.cash_minor_units==503
        assert {old['id'] for old,new in data['changed_headers']}=={i['id'] for i in invoices}
        assert len(data['funding']['applications'])==403
        assert len(data['pending']['application_allocations'])==806
        assert sum(r['amount_minor_units'] for r in data['pending']['application_allocations'] if r['kind']=='allocation')==403
        assert sum(r['amount_minor_units'] for r in data['pending']['application_allocations'] if r['kind']=='reversal')==403
        assert {r['target_transaction_id'] for r in data['pending']['application_allocations']}=={i['id'] for i in invoices}
        seen.append(True)
    with monkeypatch.context() as patch:
        compose_observer(patch,'payment',args,check)
        client.run('payment update',args,company=COMPANY,reason='Whole source settlement graph',dry_run=True)
    assert seen==[True] and snapshots(client)==before


def test_parent_cash_dimensions_and_zero_capacity_occurrence(client,sale,monkeypatch):
    jobs=[client.customer.create(name='Deposit '+n,parent_id=sale['customer'],company=COMPANY)['id'] for n in ('Pine','Oak')]
    invoices=[posted(client,p,sale['item'],a,'DEPOSIT-'+str(i)) for i,(p,a) in enumerate(zip(jobs,('600','200')))]
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1000',payment_method=method(client),operation_key='deposit-family',
        applications=dict(mode='inline',items=[dict(invoice=i['id'],expected_version=1,amount=a) for i,a in zip(invoices,('600','200'))])),company=COMPANY)
    source=observe(client,monkeypatch,'payment',paid['id'])
    assert {c.credit_owner_party:c.capacity for c in source.components}=={sale['customer']:20000,jobs[0]:60000,jobs[1]:20000}
    assert {c.cash.party_id for c in source.components}=={sale['customer']}
    old=deposits.occurrences(source);payer=next(c.key for c in source.components if c.credit_owner_party==sale['customer'])
    for version,value in ((1,'800'),(2,'1000')):
        client.run('payment update',dict(payment=paid['id'],expected_version=version,amount=value,operation_key='deposit-zero-'+str(version),
            invoice_versions=[dict(invoice=i['id'],expected_version=version+1) for i in invoices]),company=COMPANY,reason='Correct payer cash')
        source=observe(client,monkeypatch,'payment',paid['id'])
        assert payer in source.semantic_presence
        current=deposits.occurrences(source,old)
        assert next(o.ordinal for o in current if o.key==payer and o.present)==next(o.ordinal for o in old if o.key==payer)
        assert any(c.key==payer for c in source.components)==(version==2)
        old=current
    effect=deposits.prepare(Intent(deposit_id='family',date='2026-06-03',currency='USD',bank=account('bank'),sources=(source_row(source,1),),additional=()))
    assert sum(l.signed_debit for l in effect.legs if l.account_id==source.uf_account)==-100000
    assert {l.dimensions.party_id for l in effect.legs}=={sale['customer']}


def test_sales_tax_semantic_presence_and_malformed_pure_graph(client,sale,monkeypatch):
    from bookflow.company import schema as c
    agency=client.vendor.create(name='Deposit tax agency',is_tax_agency=True,company=COMPANY)['id']
    taxable=next(row['id'] for row in client.run('sales-tax-code list',{},company=COMPANY)['items'] if row['taxable'])
    with sqlite3.connect(database_path(client)) as db:
        liability=db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    info=client.run('company show',{},company=COMPANY)
    client.run('company update',dict(expected_version=info['info_version'],sales_tax_enabled=True),company=COMPANY)
    tax=client.run('item create',dict(name='Deposit eight percent',type='sales_tax_item',tax_percent='8',tax_agency_vendor_id=agency,liability_account_id=liability),company=COMPANY)['id']
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),sales_tax_item=tax,
        lines=[dict(item=sale['item'],unit_price='1',tax_code=taxable),dict(item=sale['item'],unit_price='0.01',tax_code=taxable)]),company=COMPANY)
    source=observe(client,monkeypatch,'sales-receipt',receipt['id'])
    assert source.cash_minor_units==109
    assert sorted(c.capacity for c in source.components)==[1,8,100]
    assert len(source.semantic_presence)==4 and len(source.components)==3
    assert len({c.key.identity for c in source.components})==2
    orders=deposits.occurrences(source)
    assert len(orders)==3
    command=registry.get('sales-receipt show');original=command.plan
    before=snapshots(client)
    def probe(inp,ctx,s):
        graph=deposit_sources.graph(s,receipt['id'])
        for mutation in ('duplicate','foreign_revision','wrong_tax','negative'):
            bad=copy.deepcopy(graph)
            cash_ids={r['posting_source_id'] if 'posting_source_id' in r else r['id'] for r in bad['posting_line_sources'] if r['id'] in {c.posting_source_id for c in source.components}}
            row=next(r for r in bad['posting_line_sources'] if r['id'] in cash_ids)
            if mutation=='duplicate':bad['posting_line_sources'].append(dict(row))
            elif mutation=='foreign_revision':row['revision_id']='foreign'
            elif mutation=='wrong_tax':row['tax_component_id']='foreign'
            else:row['amount_minor_units']=-1
            with pytest.raises(BookflowError) as caught:deposit_sources.project(bad,uf_account=source.uf_account,home_currency='USD')
            assert caught.value.code=='E_DEPOSIT_SOURCE_INVALID'
        return original(inp,ctx,s)
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',probe)
        client.run('sales-receipt show',dict(sales_receipt=receipt['id']),company=COMPANY)
    assert snapshots(client)==before
    # Retained zero identities get no ordinal until first positive. Existing
    # zero identities retain their old ordinal; absent/reintroduced appends.
    key=source.components[0].key
    removed=source.model_copy(update={'semantic_presence':tuple(k for k in source.semantic_presence if k!=key),'components':tuple(c for c in source.components if c.key!=key)})
    old=deposits.occurrences(removed,orders)
    returned=deposits.occurrences(source,old)
    assert next(o.ordinal for o in returned if o.key==key and o.present)>max(o.ordinal for o in orders)


def test_real_resolution_current_facts_and_explicit_memo_origin(client,sale,monkeypatch):
    from bookflow.company.deposit_models import InlineDocument
    from bookflow.company.deposit_resolution import resolve
    from bookflow.company.deposit_validation import validate_current
    bank=client.account.create(name='G1 resolved bank',type='bank',company=COMPANY)['id']
    cls=client.run('class create',dict(name='G1 resolved class'),company=COMPANY)['id']
    pm=method(client)
    receipt=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='10',payment_method=pm,operation_key='g1-resolver'),company=COMPANY)
    entered=InlineDocument.model_validate(dict(deposit_to=bank,date='2026-06-03',sources=[dict(source_type='payment',source=receipt['id'],expected_version=1,memo_override=None)],
        additional=[dict(received_from=dict(kind='customer',id=sale['customer']),from_account=sale['income'],amount='2',memo='Cash receipt',check_number='42',payment_method=pm,**{'class':cls})]))
    command=registry.get('payment show');original=command.plan;seen=[];before=snapshots(client)
    def probe(inp,ctx,s):
        effect=resolve(s,entered,deposit_id='resolved')
        assert effect.bank_total==1200 and effect.posting_total==1200
        assert effect.intent.sources[0].memo_origin=='entered' and effect.intent.sources[0].memo is None
        assert effect.intent.additional[0].check_number=='42'
        assert effect.intent.additional[0].payment_method.id==pm
        assert effect.intent.additional[0].dimensions.class_id==cls
        bad=copy.deepcopy(effect)
        bad.intent.sources[0].source.profile.payer.label='Tampered captured label'
        with pytest.raises(BookflowError) as caught:validate_current(bad,s)
        assert caught.value.code=='E_PREVIEW_STALE'
        counterfeit=effect.model_copy(update={'intent':effect.intent.model_copy(update={'bank':effect.intent.bank.model_copy(update={'name':'Forged account label'})})})
        with pytest.raises(BookflowError):validate_current(counterfeit,s)
        entered_row=effect.intent.additional[0]
        counterfeit=effect.model_copy(update={'intent':effect.intent.model_copy(update={'additional':(entered_row.model_copy(update={'dimensions':entered_row.dimensions.model_copy(update={'party_name':'Forged cash party'})}),)})})
        with pytest.raises(BookflowError):validate_current(counterfeit,s)
        stale=entered.model_copy(update={'sources':[entered.sources[0].model_copy(update={'expected_version':2})]})
        with pytest.raises(BookflowError) as caught:resolve(s,stale,deposit_id='stale')
        assert caught.value.code=='E_PREVIEW_STALE'
        seen.append(True)
        return original(inp,ctx,s)
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',probe)
        client.run('payment show',dict(payment=receipt['id']),company=COMPANY)
    assert seen==[True] and snapshots(client)==before


@pytest.mark.parametrize('noun',['payment','sales-receipt'])
def test_actual_source_void_slice_no_fake_fingerprint(client,sale,monkeypatch,noun):
    pm=method(client)
    if noun=='payment':
        result=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='7',payment_method=pm,operation_key='deposit-void-source'),company=COMPANY)
        args=dict(payment=result['id'],expected_version=1,operation_key='deposit-void-source-now')
    else:
        result=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=pm,lines=[dict(item=sale['item'],unit_price='7')]),company=COMPANY)
        args=dict(sales_receipt=result['id'],expected_version=1)
    command=registry.get(noun+' void');original=command.plan;seen=[];before=snapshots(client)
    def probe(inp,ctx,s):
        from bookflow.core import clock
        moment=clock.now_iso();monkeypatch.setattr(clock,'now_iso',lambda:moment)
        plan=original(inp,ctx,s)
        composed=deposit_composition.prepare(s,ctx,inp,noun.replace('-','_')+'_void',expected_fingerprint=plan.preview.facts_fingerprint)
        compare_plans(plan.data,composed.plan.data)
        assert composed.cash is None and composed.plan.data['header']['status']=='voided'
        assert len(composed.plan.data['pending']['posting_batches'])==1
        assert composed.plan.data['pending']['posting_batches'][0]['kind']=='reversal'
        seen.append(True)
        return plan
    with monkeypatch.context() as patch:
        patch.setattr(command,'plan',probe)
        client.run(noun+' void',args,company=COMPANY,reason='Explicit source cancellation',dry_run=True)
    assert seen==[True] and snapshots(client)==before


def test_actual_same_receipt_component_cent_order(client,sale,monkeypatch):
    """One receipt, two credit owners: component ordinal breaks the second tie."""
    job=client.customer.create(name='Deposit cent job',parent_id=sale['customer'],company=COMPANY)['id']
    invoice=posted(client,job,sale['item'],'0.02','DEPOSIT-COMPONENT-CENTS')
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='0.03',payment_method=method(client),operation_key='deposit-component-cents',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='0.02')])),company=COMPANY)
    source=observe(client,monkeypatch,'payment',paid['id'])
    assert [c.capacity for c in source.components]==[1,2]
    effect=deposits.prepare(Intent(deposit_id='cent-order',date='2026-06-03',currency='USD',bank=account('bank'),sources=(source_row(source,1),),
        additional=(additional('fee',2,-1,'expense'),),cash_back=CashBack(account=account('cash','other_current_asset'),units=1)))
    assert {(c.component_ordinal,c.bucket):c.units for c in effect.cells}=={(2,'cash_back'):1,(1,'additional:fee'):1,(2,'main_bank'):1}
    assert {l.dimensions.party_id for l in effect.legs if not l.key.startswith('additional:fee/')}=={sale['customer']}
    assert nets(effect)=={'cash':1,'feeacct':1,'bank':1,source.uf_account:-3}
