"""Real producer graphs and independent statement/GL expectations for private R0a."""
import copy
import json
import pytest
from bookflow import BookflowError
from bookflow.company import reconciliation_adapters as r
from bookflow.company.reconciliation_models import CompletePopulation, StaleReference
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_payment_receipts import method
from tests.test_deposit_sources import uf


def run(client,name,data=None,**ctx):
    if registry.get(name).is_write:ctx.setdefault('reason','R0 retained source witness')
    return client.run(name,data or {},company=COMPANY,**ctx)


def account(client,name,kind='bank',**kw):
    return run(client,'account create',dict(name=name,type=kind,**kw))['id']


def population(driver,acct,date='2026-12-31'):
    before=driver.dump()
    with driver.session() as s:out=r.population(s,acct,date)
    assert driver.dump()==before
    assert isinstance(out,CompletePopulation),out
    assert out.signed_total==out.dated_gl_total
    return out


def journal(client,lines,date='2026-01-10'):
    return run(client,'journal post',dict(date=date,lines=lines))


def pair(a,b,amount='100'):
    return [dict(account=a,side='debit',amount=amount),dict(account=b,side='credit',amount=amount)]


def test_journal_cutoff_move_metadata_void_and_history(client,driver):
    a=account(client,'R0 A');b=account(client,'R0 B');equity=account(client,'R0 Equity','equity')
    doc=journal(client,pair(a,equity))
    first=population(driver,a,'2026-01-31');assert first.signed_total==10000 and len(first.eligible)==1
    old=first.eligible[0]
    lines=[dict(account=a if line['side']=='debit' else equity,side=line['side'],amount='120',line_id=line['line_id']) for line in doc['revision']['lines']]
    doc=run(client,'journal update',dict(journal=doc['id'],expected_version=1,lines=lines))
    current=population(driver,a,'2026-01-31');assert current.signed_total==12000
    assert current.eligible[0].ref==old.ref and current.eligible[0].version_id!=old.version_id
    doc=run(client,'journal update',dict(journal=doc['id'],expected_version=2,date='2026-02-10'))
    assert population(driver,a,'2026-01-31').signed_total==0
    assert population(driver,a).signed_total==12000
    lines[0]['account']=b
    doc=run(client,'journal update',dict(journal=doc['id'],expected_version=3,lines=lines))
    assert population(driver,a).signed_total==0
    moved=population(driver,b);assert moved.signed_total==12000 and moved.eligible[0].ref==old.ref
    doc=run(client,'journal update',dict(journal=doc['id'],expected_version=4,memo='Replacement metadata'))
    metadata=population(driver,b);assert metadata.eligible[0].version_id!=moved.eligible[0].version_id
    unchanged=run(client,'journal update',dict(journal=doc['id'],expected_version=5))
    assert not unchanged['changed'] and population(driver,b).history==metadata.history
    with driver.session() as s:
        assert r.resolve(s,old.ref,old.version_id)==old
        assert isinstance(r.resolve(s,old.ref,'missing'),StaleReference)
    run(client,'journal void',dict(journal=doc['id'],expected_version=5))
    last=population(driver,b);assert last.signed_total==0 and all(not v.active for v in last.current)
    assert last.current[0].transition_batch_id and last.current[0].business_batch_id!=last.current[0].transition_batch_id


@pytest.mark.parametrize('mutation',['omit','duplicate'])
def test_net_zero_split_omission_is_caught_even_when_totals_match(client,driver,monkeypatch,mutation):
    a=account(client,'R0 zero pair')
    doc=journal(client,pair(a,a,'1'))
    values=population(driver,a);assert values.signed_total==0 and len(values.eligible)==2
    assert len({v.movement_key for v in values.eligible})==2
    assert sorted(v.signed_debit for v in values.eligible)==[-100,100]
    # Isolated deliberate adapter mutation: raw graph/GL remain unchanged.
    with monkeypatch.context() as patch:
        original=r.REGISTRY
        def broken(g,h):
            history,current=original['journal_entry'](g,h)
            return ((),()) if mutation=='omit' else (history*2,current*2)
        patch.setattr(r,'REGISTRY',dict(original,journal_entry=broken))
        with driver.session() as s:bad=r.population(s,a,'2026-12-31')
        assert bad.kind=='corrupt_population' and bad.reason=='business_leg_coverage'
    assert population(driver,a).eligible==values.eligible


def test_receipt_control_group_separate_from_retained_bank_net(legacy_world,legacy_driver,tmp_path):
    client,case=legacy_world;driver=legacy_driver
    bank,target=case['bank'],case['target']
    out=population(driver,target)
    assert sorted(v.signed_debit for v in out.eligible)==[-500,500,1234]
    control=[v for v in out.eligible if v.ref.role=='control'];net=[v for v in out.eligible if v.ref.role=='net']
    assert len(control)==2 and len({v.movement_key for v in control})==1 and len(net)==1
    assert control[0].movement_key!=net[0].movement_key
    assert out.signed_total==1234 and population(driver,bank).signed_total==0
    (tmp_path/'retained-receipt.json').write_text(out.model_dump_json(indent=2))


def test_retained_invoice_card_credit_remains_reconcilable(legacy_world,legacy_driver,tmp_path):
    client,case=legacy_world;driver=legacy_driver
    target,doc=case['target'],case['doc']
    out=population(driver,target);assert out.signed_total==-500 and out.eligible[0].statement_amount==500
    assert out.eligible[0].ref.producer=='invoice' and out.eligible[0].ref.role=='net'
    (tmp_path/'retained-invoice.json').write_text(out.model_dump_json(indent=2))
    run(client,'invoice void',dict(invoice=doc['id'],expected_version=2))
    assert population(driver,target).signed_total==0


def test_payment_single_cash_multiple_sources_and_header_only_application(client,sale,driver):
    bank=account(client,'R0 payment cash')
    child=run(client,'customer create',dict(name='R0 job',parent_id=sale['customer']))['id']
    inv=run(client,'invoice post',dict(customer=child,date='2026-01-10',lines=[dict(item=sale['item'],unit_price='5')]))
    p=run(client,'payment receive',dict(customer=sale['customer'],date='2026-01-11',amount='10',deposit_to=bank,payment_method=method(client),operation_key='r0-payment',applications=dict(mode='inline',items=[dict(invoice=inv['id'],expected_version=1,amount='5')])) )
    out=population(driver,bank);assert len(out.eligible)==1 and out.signed_total==1000
    cash=out.eligible[0];assert len(cash.posting_line_ids)==1 and len(cash.source_ids)==2
    application=p['effect']['applications'][0]
    run(client,'payment unapply',dict(payment=p['id'],expected_version=p['version'],operation_key='r0-unapply',applications=[dict(application_id=application['application_id'],invoice_expected_version=2)]))
    assert population(driver,bank).eligible==(cash,)
    run(client,'payment update',dict(payment=p['id'],expected_version=p['version']+1,operation_key='r0-uf',deposit_to=uf(client)))
    assert population(driver,bank).signed_total==0


def test_persisted_g2_roles_zero_reactivation_and_void_anchor(client,sale,driver):
    doc=additional_document(client,sale,'10',cash='10')
    b=account(client,'R0 G2 cash bank');card=account(client,'R0 G2 financing','credit_card')
    doc['cash_back']['account']=b;doc['additional'][0]['from_account']=card
    posted=driver.run('post',dict(operation_key='r0-g2',document=doc))
    a=doc['deposit_to'];assert population(driver,a).signed_total==0
    assert population(driver,b).signed_total==1000 and population(driver,card).signed_total==-1000
    new=replacement(posted,doc);new['cash_back']['amount']='5'
    edited=driver.run('update',dict(operation_key='r0-g2-bank',deposit=posted.current.id,expected_version=1,document=new),reason='Make main bank nonzero')
    main=population(driver,a);assert main.signed_total==500
    key=main.eligible[0].ref
    new['cash_back']['amount']='10'
    zero=driver.run('update',dict(operation_key='r0-g2-zero',deposit=posted.current.id,expected_version=2,document=new),reason='Zero main')
    inactive=population(driver,a);assert not inactive.current[0].active and inactive.current[0].ref==key
    new['cash_back']['amount']='5'
    live=driver.run('update',dict(operation_key='r0-g2-live',deposit=posted.current.id,expected_version=3,document=new),reason='Reactivate main')
    active=population(driver,a).eligible[0];assert active.ref==key
    driver.run('void',dict(operation_key='r0-g2-void',deposit=posted.current.id,expected_version=4),reason='Void G2')
    final=population(driver,a);value=final.current[0]
    assert not value.active and value.business_batch_id==active.business_batch_id and value.revision_id==active.revision_id
    assert value.audit_event_id!=active.audit_event_id and value.transition_batch_id
    for acct in (a,b,card):assert population(driver,acct).signed_total==0


@pytest.mark.parametrize('name',['journal post','invoice post','sales-receipt post','payment receive'])
def test_actual_prospective_plan_does_not_write(client,sale,driver,name):
    bank=account(client,'R0 prospective bank');equity=account(client,'R0 prospective equity','equity')
    if name=='journal post':body=dict(date='2026-01-10',lines=pair(bank,equity,'10'))
    elif name=='payment receive':body=dict(customer=sale['customer'],date='2026-01-10',amount='10',deposit_to=bank,payment_method=method(client),operation_key='r0-prospective')
    else:
        body=dict(customer=sale['customer'],date='2026-01-10',lines=[dict(item=sale['item'])])
        if name=='sales-receipt post':body.update(deposit_to=bank,payment_method=method(client))
    cmd=registry.get(name);ctx=Context.new(Interface.python,'R0 prospective')
    before=driver.dump()
    with driver.session() as s:
        plan=cmd.plan(cmd.input_model.model_validate(body),ctx,s)
        changed=r.prospective(s,ctx,plan)
        assert changed.kind=='changed_effects' and changed.before==()
        assert sum(v.signed_debit for v in changed.after)==(0 if name=='invoice post' else 1234 if name=='sales-receipt post' else 1000)
    assert driver.dump()==before


def test_actual_deposit_prospective_bundle(client,sale,driver):
    from bookflow.company import deposit_lifecycle as life
    body=dict(operation_key='r0-deposit-prospective',document=additional_document(client,sale))
    ctx=Context.new(Interface.python,'R0 prospective')
    before=driver.dump()
    with driver.session() as s:
        plan=life.prepare(s,ctx,life.INPUTS['post'].model_validate(body),'post')
        changed=r.prospective(s,ctx,plan)
        assert len(changed.after)==1 and changed.after[0].signed_debit==1000
    assert driver.dump()==before


def test_foreign_account_is_typed_unsupported_home_foreign_original_supported(legacy_world,legacy_driver,monkeypatch):
    client,case=legacy_world;driver=legacy_driver
    foreign,doc=case['target'],case['doc']
    home=account(client,'R0 USD bank');eq=account(client,'R0 FX equity','equity')
    # Current ordinary posting rules remain intact on the legitimate old store.
    with pytest.raises(BookflowError) as rejected:
        run(client,'journal post',dict(date='2026-01-10',rate='2',lines=pair(foreign,eq,'10 EUR')))
    assert rejected.value.code=='E_VALIDATION'
    run(client,'journal post',dict(date='2026-01-10',rate='2',lines=pair(home,eq,'10 EUR')))
    before=driver.dump()
    with pytest.raises(BookflowError) as rejected:
        run(client,'invoice update',dict(invoice=doc['id'],expected_version=2,memo='Foreign replacement'))
    assert rejected.value.code=='E_VALIDATION'
    assert rejected.value.details==dict(field='lines',reason='captured_posting_account_type')
    assert driver.dump()==before
    with driver.session() as s:
        result=r.population(s,foreign,'2026-12-31')
        assert result.model_dump()==dict(kind='account_currency_unsupported',account_id=foreign,account_currency='EUR',home_currency='USD',reason='foreign_statement_units_not_activated')
    with driver.session() as s:
        ctx=Context.new(Interface.python,'R0 foreign inverse',reason='Exact historical inverse')
        cmd=registry.get('invoice void')
        inp=cmd.input_model.model_validate(dict(invoice=doc['id'],expected_version=2))
        prospective=r.prospective(s,ctx,cmd.plan(inp,ctx,s))
        assert prospective.kind=='account_currency_unsupported' and prospective.account_id==foreign
    actual=population(driver,home);assert actual.signed_total==2000
    assert any('original_currency' in raw and 'EUR' in raw for raw in actual.eligible[0].provenance)
    # Permission precedes unsupported currency disclosure; no new live ACL policy.
    from bookflow.hub import access
    def deny(*args,**kwargs):raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(access,'require_resource',deny)
    with driver.session() as s:
        with pytest.raises(BookflowError) as error:r.population(s,foreign,'2026-12-31')
        assert error.value.code=='E_PERMISSION' and not error.value.details


def test_historical_unapplied_work_and_deposit_operations_remain_authority(client,sale,driver,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    from bookflow.company import payment_authority
    source=accepted(client,sale);invoice=bill(client,source)
    p=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='30',payment_method=method(client),operation_key='r0-hidden-payment',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='24.68')])))
    application=p['effect']['applications'][0]
    run(client,'payment unapply',dict(payment=p['id'],expected_version=1,operation_key='r0-hidden-unapply',applications=[dict(application_id=application['application_id'],invoice_expected_version=2)]))
    doc=additional_document(client,sale,'1')
    doc['sources']=[dict(source_type='payment',source=p['id'],expected_version=2)]
    dep=driver.run('post',dict(operation_key='r0-hidden-deposit',document=doc))
    initial=population(driver,doc['deposit_to'])
    assert {p['id'],invoice['id'],dep.current.id}<=set(initial.authority_transactions)
    driver.run('void',dict(operation_key='r0-hidden-void',deposit=dep.current.id,expected_version=1),reason='Retain hidden history')
    with driver.session() as s:
        assert {p['id'],invoice['id'],dep.current.id}<=set(r.authority(s,(dep.current.id,)))
    original=payment_authority.require_resource
    def deny_work(s,capability,role):
        if capability=='customer-work':raise BookflowError('E_PERMISSION',details={'hidden':'must not escape'})
        return original(s,capability,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny_work)
    with driver.session() as s:
        with pytest.raises(BookflowError) as error:r.population(s,doc['deposit_to'],'2026-12-31')
        assert error.value.code=='E_PERMISSION' and not error.value.details


def test_register_has_only_actual_inner_journal_refs_and_prospective_owner(client,driver):
    from bookflow.company import registers,journals
    a=account(client,'R0 register A');b=account(client,'R0 register B');eq=account(client,'R0 register equity','equity')
    body=dict(account=a,date='2026-01-10',direction='increase',amount='10',allocations=[dict(account=b,amount='6'),dict(account=eq,amount='4')])
    doc=run(client,'register post',body)
    assert population(driver,a).signed_total==1000 and population(driver,b).signed_total==-600
    assert all(v.ref.producer=='journal_entry' for v in population(driver,a).eligible)
    cmd=registry.get('register post');ctx=Context.new(Interface.python,'R0 inner register')
    before=driver.dump()
    with driver.session() as s:
        inp=cmd.input_model.model_validate(body)
        outer=cmd.plan(inp,ctx,s)
        with pytest.raises(r.Unsupported,match='full_inner_source_plan_required'):r.prospective(s,ctx,outer)
        inner_input,_=registers.translate(inp,s,'post')
        plan=journals.prepare(s,ctx,inner_input,'post')
        prepared=r.prepare_prospective(s,ctx,plan)
        assert prepared.aggregate is plan
        assert sorted(v.signed_debit for v in prepared.changes.after)==[-600,1000]
    assert driver.dump()==before


@pytest.mark.parametrize('noun',['journal','sales-receipt','payment'])
def test_prospective_correction_and_void_match_complete_original_legs(client,sale,driver,noun):
    a=account(client,'R0 prospective old');b=account(client,'R0 prospective new');eq=account(client,'R0 prospective offset','equity')
    if noun=='journal':doc=journal(client,pair(a,eq,'10'))
    elif noun=='payment':doc=run(client,'payment receive',dict(customer=sale['customer'],date='2026-01-10',amount='10',deposit_to=a,payment_method=method(client),operation_key='r0-change-source'))
    else:doc=run(client,'sales-receipt post',dict(customer=sale['customer'],date='2026-01-10',deposit_to=a,payment_method=method(client),lines=[dict(item=sale['item'])]))
    key=noun.replace('-','_');body={key:doc['id'],'expected_version':1}
    if noun=='journal':body['lines']=[dict(account=b if l['side']=='debit' else eq,side=l['side'],amount='10',line_id=l['line_id']) for l in doc['revision']['lines']]
    else:body['deposit_to']=b
    if noun=='payment':body['operation_key']='r0-change-payment'
    ctx=Context.new(Interface.python,'R0 correction',reason='Correct bank')
    before=driver.dump()
    with driver.session() as s:
        cmd=registry.get(noun+' update');plan=cmd.plan(cmd.input_model.model_validate(body),ctx,s)
        changes=r.prospective(s,ctx,plan)
        assert {v.account_id for v in changes.before}=={a} and {v.account_id for v in changes.after}=={b}
        assert {v.ref for v in changes.before}=={v.ref for v in changes.after}
    assert driver.dump()==before
    body={key:doc['id'],'expected_version':1}
    if noun=='payment':body['operation_key']='r0-void-preview'
    with driver.session() as s:
        cmd=registry.get(noun+' void');plan=cmd.plan(cmd.input_model.model_validate(body),ctx,s)
        changes=r.prospective(s,ctx,plan)
        assert changes.before and all(not v.active for v in changes.after)
    assert driver.dump()==before


def test_prepared_source_cash_none_does_not_hide_new_bank_effect(client,sale,driver):
    from bookflow.company import deposit_composition
    bank=account(client,'R0 composition bank')
    doc=run(client,'sales-receipt post',dict(customer=sale['customer'],date='2026-01-10',deposit_to=uf(client),payment_method=method(client),lines=[dict(item=sale['item'])]))
    cmd=registry.get('sales-receipt update');inp=cmd.input_model.model_validate(dict(sales_receipt=doc['id'],expected_version=1,deposit_to=bank))
    ctx=Context.new(Interface.python,'R0 composition',reason='Move receipt to bank')
    before=driver.dump()
    with driver.session() as s:
        preview=cmd.plan(inp,ctx,s)
        composed=deposit_composition.prepare(s,ctx,inp,'sales_receipt_update',expected_fingerprint=preview.preview.facts_fingerprint)
        assert composed.cash is None
        changes=r.prospective(s,ctx,composed.plan)
        assert not changes.before and len(changes.after)==1 and changes.after[0].signed_debit==1234
    assert driver.dump()==before


def test_work_billing_actual_plan_keeps_capture_and_permission(client,sale,driver):
    from tests.test_work_billing_lifecycle import accepted
    source=accepted(client,sale)
    bank=account(client,'R0 billing bank');cmd=registry.get('estimate sales-receipt')
    inp=cmd.input_model.model_validate(dict(estimate=source['id'],expected_version=source['version'],conversion_key='r0-bill-plan',date='2026-01-13',amount_received='24.68',deposit_to=bank,payment_method=method(client)))
    ctx=Context.new(Interface.python,'R0 actual billing')
    before=driver.dump()
    with driver.session() as s:
        plan=cmd.plan(inp,ctx,s);prepared=r.prepare_prospective(s,ctx,plan)
        assert prepared.aggregate is plan and plan.data['billing_allocations']
        assert sum(v.signed_debit for v in prepared.changes.after)==2468
        assert any(source['id'] in raw for v in prepared.changes.after for raw in v.provenance)
    assert driver.dump()==before


def test_retired_journal_components_never_reuse_old_reference(client,driver):
    a=account(client,'R0 retained A');b=account(client,'R0 removed B');eq=account(client,'R0 retired offset','equity')
    doc=journal(client,[dict(account=a,side='debit',amount='10'),dict(account=b,side='credit',amount='6'),dict(account=eq,side='credit',amount='4')])
    original=population(driver,b).eligible[0];lines=doc['revision']['lines']
    keep=[dict(account=a,side='debit',amount='10',line_id=lines[0]['line_id']),dict(account=eq,side='credit',amount='10',line_id=lines[2]['line_id'])]
    doc=run(client,'journal update',dict(journal=doc['id'],expected_version=1,lines=keep))
    gone=population(driver,b);assert gone.signed_total==0 and gone.current[0].ref==original.ref and not gone.current[0].active
    keep[1]['amount']='8';keep.append(dict(account=b,side='credit',amount='2'))
    with pytest.raises(BookflowError) as error:
        run(client,'journal update',dict(journal=doc['id'],expected_version=2,lines=[*keep[:-1],dict(keep[-1],line_id=original.ref.component_id)]))
    assert error.value.code=='E_VALIDATION'
    run(client,'journal update',dict(journal=doc['id'],expected_version=2,lines=keep))
    final=population(driver,b);assert final.signed_total==-200 and len(final.current)==2
    assert final.eligible[0].ref!=original.ref


def test_g2_metadata_noeffect_and_removed_additional_key(client,sale,driver):
    bank=account(client,'R0 removed deposit financing')
    document=additional_document(client,sale,'10')
    row=dict(received_from=dict(kind='customer',id=sale['customer']),from_account=bank,amount='5')
    document['additional'].append(row)
    posted=driver.run('post',dict(operation_key='r0-retired-deposit',document=document))
    original=population(driver,bank).eligible[0]
    unchanged=replacement(posted,document)
    noop=driver.run('update',dict(operation_key='r0-deposit-noop',deposit=posted.current.id,expected_version=1,document=unchanged),reason='No effect')
    assert not noop.changed and population(driver,bank).eligible==(original,)
    unchanged['memo']='New captured memo'
    edited=driver.run('update',dict(operation_key='r0-deposit-metadata',deposit=posted.current.id,expected_version=1,document=unchanged),reason='Metadata')
    metadata=population(driver,bank).eligible[0]
    assert metadata.ref==original.ref and metadata.version_id!=original.version_id and metadata.signed_debit==original.signed_debit
    unchanged['additional'].pop()
    removed=driver.run('update',dict(operation_key='r0-deposit-remove',deposit=posted.current.id,expected_version=2,document=unchanged),reason='Remove transfer')
    assert population(driver,bank).signed_total==0
    unchanged['additional'].append(row)
    driver.run('update',dict(operation_key='r0-deposit-readd',deposit=posted.current.id,expected_version=3,document=unchanged),reason='New transfer row')
    final=population(driver,bank);assert final.signed_total==-500 and len(final.current)==2 and final.eligible[0].ref!=original.ref
    with driver.session() as s:assert r.resolve(s,original.ref,original.version_id)==original


def test_payment_apply_prospective_is_no_cash_version(client,sale,driver):
    bank=account(client,'R0 apply bank')
    inv=run(client,'invoice post',dict(customer=sale['customer'],date='2026-01-10',lines=[dict(item=sale['item'],unit_price='5')]))
    pay=run(client,'payment receive',dict(customer=sale['customer'],date='2026-01-10',amount='10',deposit_to=bank,payment_method=method(client),operation_key='r0-apply-base'))
    cmd=registry.get('payment apply');ctx=Context.new(Interface.python,'R0 apply')
    inp=cmd.input_model.model_validate(dict(payment=pay['id'],expected_version=1,date='2026-01-11',operation_key='r0-apply-plan',applications=dict(mode='inline',items=[dict(invoice=inv['id'],expected_version=1,amount='5')])))
    before=driver.dump()
    with driver.session() as s:
        plan=cmd.plan(inp,ctx,s);changes=r.prospective(s,ctx,plan)
        assert changes.before==changes.after==() and inv['id'] in changes.authority_transactions
    assert driver.dump()==before


def test_deposit_prepared_projection_retains_exact_materialized_bundle(client,sale,driver):
    from bookflow.company import deposit_lifecycle as life
    ctx=Context.new(Interface.python,'R0 exact bundle')
    body=dict(operation_key='r0-bundle-identity',document=additional_document(client,sale))
    with driver.session() as s:
        plan=life.prepare(s,ctx,life.INPUTS['post'].model_validate(body),'post')
        prepared=r.prepare_prospective(s,ctx,plan)
        value=prepared.changes.after[0];bundle=prepared.aggregate
        assert value.version_id==bundle['pending']['bank_effect_versions'][0]['id']
        assert set(value.posting_line_ids)<=set(row['id'] for row in bundle['pending']['posting_lines'])


def test_account_history_sums_use_unbounded_intermediates(client,driver):
    a=account(client,'R0 large bank');eq=account(client,'R0 large equity','equity')
    for _ in range(2):journal(client,pair(a,eq,'92233720368547758.07'))
    result=population(driver,a)
    assert result.signed_total==2*9223372036854775807 and len(result.eligible)==2


def test_aborted_recovery_attempt_work_target_is_not_filtered(client,sale,driver,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    from tests.test_payment_recovery import declaration,call
    from bookflow.company import payment_authority
    bank=account(client,'R0 recovery cash')
    pay=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-01',amount='10',deposit_to=bank,payment_method=method(client),operation_key='r0-recovery-cash'))
    work=bill(client,accepted(client,sale))
    draft=run(client,'payment selection create',dict(mode='existing_credit',payment=pay['id'],date='2026-06-02'))
    edits=[dict(invoice_id=work['id'],observed_invoice_version=1,action='remove')]
    begin=call(client,'begin',declaration(draft,edits));identifier=begin['original_receipt']['recovery_id']
    call(client,'upload',dict(recovery_id=identifier,chunk_index=0,entries=edits))
    call(client,'abort',dict(recovery_id=identifier,expected_recovery_version=2,disposition='discard_entire_attempt'))
    actual=population(driver,bank)
    assert {pay['id'],work['id']}<=set(actual.authority_transactions)
    original=payment_authority.require_resource
    def deny(s,capability,role):
        if capability=='customer-work':raise BookflowError('E_PERMISSION')
        return original(s,capability,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny)
    with driver.session() as s:
        with pytest.raises(BookflowError) as error:r.population(s,bank,'2026-12-31')
        assert error.value.code=='E_PERMISSION'


def test_applied_invoice_and_payment_prospective_settlement_owners(client,sale,driver):
    bank=account(client,'R0 settlement cash')
    invoice=run(client,'invoice post',dict(customer=sale['customer'],date='2026-01-10',lines=[dict(item=sale['item'],unit_price='10')]))
    pay=run(client,'payment receive',dict(customer=sale['customer'],date='2026-01-10',amount='10',deposit_to=bank,payment_method=method(client),operation_key='r0-settlement',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='5')])))
    ctx=Context.new(Interface.python,'R0 settlement',reason='Preview complete settlement')
    before=driver.dump()
    with driver.session() as s:
        cmd=registry.get('invoice update')
        body=dict(invoice=invoice['id'],expected_version=2,operation_key='r0-invoice-plan',memo='Replacement invoice',settlement_versions=[dict(payment=pay['id'],expected_version=1)])
        plan=cmd.plan(cmd.input_model.model_validate(body),ctx,s)
        prepared=r.prepare_prospective(s,ctx,plan)
        assert plan.data['settlement_extension'] and prepared.aggregate is plan
        assert prepared.changes.before==prepared.changes.after==()
        assert {invoice['id'],pay['id']}<=set(prepared.changes.authority_transactions)
        cmd=registry.get('payment update')
        inp=cmd.input_model.model_validate(dict(payment=pay['id'],expected_version=1,operation_key='r0-payment-plan',amount='12',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)]))
        plan=cmd.plan(inp,ctx,s);prepared=r.prepare_prospective(s,ctx,plan)
        assert sum(v.signed_debit for v in prepared.changes.before)==1000
        assert sum(v.signed_debit for v in prepared.changes.after)==1200
        assert {invoice['id'],pay['id']}<=set(prepared.changes.authority_transactions)
    assert driver.dump()==before


def test_retained_nonbank_transition_has_new_account_provenance(client,driver):
    a=account(client,'R0 leaving bank');asset=account(client,'R0 moved asset','other_current_asset');eq=account(client,'R0 leaving equity','equity')
    doc=journal(client,pair(a,eq,'10'))
    lines=[dict(account=asset if l['side']=='debit' else eq,side=l['side'],amount='10',line_id=l['line_id']) for l in doc['revision']['lines']]
    run(client,'journal update',dict(journal=doc['id'],expected_version=1,lines=lines))
    value=population(driver,a).current[0]
    assert not value.active and value.account_id==a
    assert any(json.loads(raw).get('account_id')==asset for raw in value.provenance)


def test_g2_cutoff_same_key_amount_date_and_bank_move(client,sale,driver):
    document=additional_document(client,sale,'100')
    a=document['deposit_to'];b=account(client,'R0 dated new bank')
    saved=driver.run('post',dict(operation_key='r0-dated-g2',document=document))
    original=population(driver,a,'2026-06-30').eligible[0]
    replacement_doc=replacement(saved,document);replacement_doc['additional'][0]['amount']='120'
    saved=driver.run('update',dict(operation_key='r0-dated-amount',deposit=saved.current.id,expected_version=1,document=replacement_doc),reason='Actual amount correction')
    assert population(driver,a,'2026-06-30').signed_total==12000
    replacement_doc['date']='2026-07-03'
    saved=driver.run('update',dict(operation_key='r0-dated-date',deposit=saved.current.id,expected_version=2,document=replacement_doc),reason='Actual date correction')
    assert population(driver,a,'2026-06-30').signed_total==0
    assert population(driver,a,'2026-07-31').signed_total==12000
    replacement_doc['deposit_to']=b
    driver.run('update',dict(operation_key='r0-dated-bank',deposit=saved.current.id,expected_version=3,document=replacement_doc),reason='Actual account correction')
    assert population(driver,a,'2026-07-31').signed_total==0
    result=population(driver,b,'2026-07-31');assert result.signed_total==12000 and result.eligible[0].ref==original.ref


def test_g2_same_account_opposite_roles_remain_distinct(client,sale,driver):
    document=additional_document(client,sale,'10',cash='10')
    b=account(client,'R0 opposite role bank')
    document['additional'][0]['from_account']=b;document['cash_back']['account']=b
    driver.run('post',dict(operation_key='r0-opposite-roles',document=document))
    out=population(driver,b)
    assert out.signed_total==0 and len(out.eligible)==2
    assert {v.ref.role for v in out.eligible}=={'cash_back','additional'}
    assert sorted(v.signed_debit for v in out.eligible)==[-1000,1000]
    assert len({v.movement_key for v in out.eligible})==2


@pytest.mark.parametrize('failure',['content','driver'])
def test_private_content_failure_is_typed_driver_failure_is_not_hidden(client,driver,monkeypatch,failure):
    from sqlalchemy.exc import OperationalError
    a=account(client,'R0 typed error bank');eq=account(client,'R0 typed error offset','equity');journal(client,pair(a,eq))
    def broken(g):
        if failure=='content':raise TypeError('Untrusted malformed content must not escape')
        raise OperationalError('owned query',{},RuntimeError('driver failure'))
    monkeypatch.setattr(r,'enumerate_graph',broken)
    with driver.session() as s:
        if failure=='content':assert r.population(s,a,'2026-12-31').model_dump()==dict(kind='corrupt_population',reason='invalid_owned_population')
        else:
            with pytest.raises(OperationalError):r.population(s,a,'2026-12-31')


@pytest.fixture(scope='session')
def reconciliation_legacy_worlds(tmp_path_factory):
    import io,os,subprocess,sys,tarfile
    from pathlib import Path
    parent=tmp_path_factory.mktemp('reconciliation-legacy');source=parent/'source';source.mkdir()
    pin='b890e1452f29017d45e584401d2e2213b155555d'
    archive=subprocess.check_output(['git','archive',pin,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    code=r'''
import bookflow,json,sys,shutil,hashlib
from bookflow.core import registry
from pathlib import Path
parent=Path(sys.argv[1]);seed=parent/'seed'
(parent/'import-pin.json').write_text(json.dumps(dict(bookflow_file=bookflow.__file__,interpreter=sys.executable)))
c=bookflow.connect(data_root=str(seed));c.init();c.demo.reset();del c
for kind in ('receipt','invoice','foreign'):
 root=parent/kind;shutil.copytree(seed,root);c=bookflow.connect(data_root=str(root));transcript=[]
 def run(n,d):
  context=dict(reason='Retained reconciliation fixture') if registry.get(n).is_write else {}
  try:
   result=c.run(n,d,company='Demo Plumbing Co',**context)
  except Exception as exc:
   transcript.append(dict(command=n,input=d,context=context,error=str(exc)))
   (parent/(kind+'-transcript.json')).write_text(json.dumps(transcript));raise
  transcript.append(dict(command=n,input=d,context=context,output=result))
  (parent/(kind+'-transcript.json')).write_text(json.dumps(transcript));return result
 customer=run('customer create',dict(name='R0 legacy customer'))['id']
 income=run('account create',dict(name='R0 positive income',type='income'))['id']
 target=run('account create',dict(name='R0 retained income',type='income'))['id']
 tax=next(x['id'] for x in run('sales-tax-code list',{})['items'] if not x['taxable'])
 def item(name,account,price):return run('item create',dict(name=name,type='service',sales_enabled=True,description=name,income_account_id=account,price=price,sales_tax_code_id=tax))['id']
 positive=item('R0 positive',income,'12.34');zero=item('R0 zero',target,'0')
 noun='sales-receipt' if kind=='receipt' else 'invoice';extra={};bank=None
 if kind=='receipt':
  bank=run('account create',dict(name='R0 sale bank',type='bank'))['id']
  extra=dict(deposit_to=bank,payment_method=run('payment-method create',dict(name='R0 legacy cash',kind='cash'))['id'])
 doc=run(noun+' post',dict(customer=customer,date='2026-01-10',lines=[dict(item=positive),dict(item=zero)],**extra))
 run('item update',dict(item=zero,income_account_id=income))
 transition=dict(account=target,type='credit_card' if kind=='invoice' else 'bank')
 if kind=='foreign':transition['currency']='EUR'
 run('account update',transition)
 lines=[dict(item=x['item_id'],line_id=x['line_id']) for x in doc['revision']['lines']];lines[1]['unit_price']='5'
 body={noun.replace('-','_'):doc['id'],'expected_version':1,'lines':lines}
 if kind=='receipt':body['deposit_to']=target
 doc=run(noun+' update',body)
 (parent/(kind+'.json')).write_text(json.dumps(dict(target=target,bank=bank,doc=doc)))
 (parent/(kind+'-transcript.json')).write_text(json.dumps(transcript))
 (parent/(kind+'-raw.json')).write_text(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}))
'''
    result=subprocess.run([sys.executable,'-c',code,str(parent)],cwd=source,
        env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(parent/'seed')),capture_output=True,text=True)
    (parent/'legacy-run.log').write_text(result.stdout+result.stderr)
    (parent/'source-pin.json').write_text(json.dumps(dict(commit=pin,interpreter=sys.executable,returncode=result.returncode)))
    assert result.returncode==0,result.stderr
    return parent


@pytest.fixture
def legacy_world(request,reconciliation_legacy_worlds,tmp_path,monkeypatch):
    import bookflow,shutil,hashlib
    kind={'test_receipt_control_group_separate_from_retained_bank_net':'receipt',
          'test_retained_invoice_card_credit_remains_reconcilable':'invoice',
          'test_foreign_account_is_typed_unsupported_home_foreign_original_supported':'foreign'}[request.node.name]
    parent=reconciliation_legacy_worlds;root=tmp_path/'legacy';shutil.copytree(parent/kind,root)
    expected=json.loads((parent/(kind+'-raw.json')).read_text())
    assert {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}==expected
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root));monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    yield bookflow.connect(data_root=str(root)),json.loads((parent/(kind+'.json')).read_text())
    original=parent/kind
    assert {str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest() for p in original.rglob('*') if p.is_file()}==expected


@pytest.fixture
def legacy_driver(legacy_world,monkeypatch):
    return driver.__wrapped__(legacy_world[0],monkeypatch)


@pytest.mark.parametrize('family',['payment_operation','deposit_operation'])
def test_actual_operation_owner_inventory_and_retained_participant_denial(client,sale,driver,monkeypatch,family):
    """Operation participants are real, but not exclusive of immutable edge history.

    The explicit owner-call assertion is a branch witness for M5/M10, not a
    claim that deleting historical applications/memberships is a valid route.
    """
    from bookflow.company import payment_authority
    from tests.test_work_billing_lifecycle import accepted,bill
    source=accepted(client,sale);invoice=bill(client,source)
    pay=run(client,'payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='30',
        payment_method=method(client),operation_key='r0-operation-receive',applications=dict(mode='inline',
        items=[dict(invoice=invoice['id'],expected_version=1,amount='24.68')])))
    application=pay['effect']['applications'][0]
    run(client,'payment unapply',dict(payment=pay['id'],expected_version=1,operation_key='r0-operation-unapply',
        applications=[dict(application_id=application['application_id'],invoice_expected_version=2)]))
    document=additional_document(client,sale,'1')
    document['sources']=[dict(source_type='payment',source=pay['id'],expected_version=2)]
    deposited=driver.run('post',dict(operation_key='r0-operation-deposit',document=document))
    removal=replacement(deposited,document);removal['sources']=[]
    changed=driver.run('update',dict(operation_key='r0-operation-remove',deposit=deposited.current.id,
        expected_version=1,document=removal),reason='Remove source but retain all history')
    assert changed.current.active_source_ids==()
    expected_graph={deposited.current.id,pay['id'],invoice['id']}
    with driver.session() as s:
        raw=s.company.raw
        # Ordinary removal and unapply preserve exact immutable edges. Neither
        # operation is an exclusive route to a participant in the current model.
        assert raw.execute('SELECT paying_transaction_id,paid_transaction_id FROM applications WHERE id=?',
            (application['application_id'],)).fetchall()==[(pay['id'],invoice['id'])]
        memberships=raw.execute('SELECT kind,source_transaction_id FROM deposit_memberships WHERE transaction_id=? ORDER BY rowid',
            (deposited.current.id,)).fetchall()
        assert memberships==[('claim',pay['id']),('release',pay['id'])]
        assert raw.execute('SELECT count(*) FROM deposit_current_memberships WHERE source_transaction_id=?',(pay['id'],)).fetchone()==(0,)
        if family=='payment_operation':
            rows=raw.execute("SELECT id,request_snapshot FROM payment_operations WHERE operation_key IN ('r0-operation-receive','r0-operation-unapply')").fetchall()
            expected_operations={identity:{pay['id'],invoice['id']} for identity,_ in rows}
            assert len(expected_operations)==2
            assert all(set(json.loads(payload)['resolved_transaction_ids'])=={pay['id'],invoice['id']} for _,payload in rows)
        else:
            rows=raw.execute('SELECT id FROM deposit_operations WHERE transaction_id=?',(deposited.current.id,)).fetchall()
            expected_operations={identity:{deposited.current.id,pay['id']} for (identity,) in rows}
            assert len(expected_operations)==2
            for identity,targets in expected_operations.items():
                assert set(x[0] for x in raw.execute('SELECT transaction_id FROM deposit_operation_targets WHERE operation_id=?',(identity,)))==targets
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
    before=driver.dump();observed={}
    owner=payment_authority.record_transactions
    def observe(db,kind,identity,*args,**kwargs):
        result=owner(db,kind,identity,*args,**kwargs)
        if kind==family and identity in expected_operations:observed[identity]=set(result)
        return result
    monkeypatch.setattr(payment_authority,'record_transactions',observe)
    actual=population(driver,document['deposit_to'])
    assert actual.signed_total==100 and set(actual.authority_transactions)==expected_graph
    assert observed==expected_operations, 'Every retained operation must reach its actual owning decoder'
    assert driver.dump()==before
    require=payment_authority.require_resource
    def deny_work(s,capability,role):
        if capability=='customer-work':raise BookflowError('E_PERMISSION',details={'hidden':'not publishable'})
        return require(s,capability,role)
    monkeypatch.setattr(payment_authority,'require_resource',deny_work)
    observed.clear()
    with driver.session() as s:
        with pytest.raises(BookflowError) as error:r.population(s,document['deposit_to'],'2026-12-31')
    assert error.value.code=='E_PERMISSION' and not error.value.details
    assert observed==expected_operations
    assert driver.dump()==before
