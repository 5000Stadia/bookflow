"""Full original source plans versus keyless slices and persistent source identity."""
import copy
import sqlite3
import pytest
from bookflow import BookflowError
from bookflow.core import registry, clock
from bookflow.core.ids import new_id
from bookflow.company import deposit_composition as composition
from bookflow.company.deposit_coordination import canonical_source_data, coalesce_headers
from bookflow.company.deposit_coordinate_models import PaymentUpdateAction, SalesReceiptUpdateAction
from bookflow.company.payment_models import PaymentUpdateIntent, EffectProvenance
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_row8_journal import database_path


def compare_observer(monkeypatch, noun, callback):
    command=registry.get(noun+' update');original=command.plan
    def read(inp,ctx,s):
        at=clock.now_iso()
        with monkeypatch.context() as patch:
            patch.setattr(clock,'now_iso',lambda:at)
            ordinary=original(inp,ctx,s)
            if noun=='payment':
                intent=PaymentUpdateIntent.model_validate(inp.model_dump(exclude_unset=True,exclude={'operation_key','expected_facts_fingerprint'}))
                action=PaymentUpdateAction(kind='payment_update',input=intent)
            else:
                action=SalesReceiptUpdateAction(kind='sales_receipt_update',input=inp)
            provenance=EffectProvenance(at=at,event_id=new_id(),operation_id=new_id())
            prepared=composition.prepare_source_effect(s,ctx,action,provenance=provenance,expected_fingerprint=ordinary.preview.facts_fingerprint)
            assert canonical_source_data(ordinary,payment=noun=='payment')==canonical_source_data(prepared.plan,payment=noun=='payment')
            with pytest.raises(BookflowError) as caught:
                composition.prepare_source_effect(s,ctx,action,provenance=provenance,expected_fingerprint='0'*64)
            assert caught.value.code=='E_PREVIEW_STALE'
            broken=copy.deepcopy(prepared.plan);broken.data['new_unclassified_owner']={'cash':999}
            with pytest.raises(BookflowError):canonical_source_data(broken,payment=noun=='payment')
            callback(ordinary,prepared,s)
        return ordinary
    monkeypatch.setattr(command,'plan',read)


@pytest.mark.parametrize('destination', ['uf','bank'])
def test_n2_full_keyless_payment_plan_with_exact_bank_and_settlement_effects(client,sale,monkeypatch,destination):
    invoice=posted(client,sale['customer'],sale['item'],'100','B-N2')
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=method(client),
        operation_key='B-N2-receive',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    args=dict(payment=payment['id'],expected_version=1,amount='120',operation_key='B-N2-update',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)])
    bank=client.account.create(name='B source bank',type='bank',company=COMPANY)['id']
    if destination=='bank':args['deposit_to']=bank
    path=database_path(client)
    with sqlite3.connect(path) as db:
        before=tuple(db.iterdump())
        uf=db.execute("SELECT id FROM accounts WHERE system_role='undeposited_funds'").fetchone()[0]
        ar=db.execute('SELECT ar_account_id FROM payment_profiles WHERE transaction_id=?',(payment['id'],)).fetchone()[0]
        oldkeys=db.execute('SELECT id FROM payment_component_keys WHERE transaction_id=?',(payment['id'],)).fetchall()
    def check(ordinary,prepared,s):
        assert prepared.cash.cash_minor_units==12000 if destination=='uf' else prepared.cash is None
        pending=prepared.plan.data['pending']
        delta={}
        for row in pending['posting_lines']:delta[row['account_id']]=delta.get(row['account_id'],0)+row['debit_minor_units']-row['credit_minor_units']
        assert delta==({uf:2000,ar:-2000} if destination=='uf' else {uf:-10000,bank:12000,ar:-2000})
        assert len(pending['application_allocations'])==2 and not pending['applications']
        assert {r['component_key_id'] for r in pending['payment_components']}=={r[0] for r in oldkeys}
        assert not pending['payment_component_keys']
        assert prepared.plan.preview.current.applied_minor_units==10000
        assert prepared.plan.preview.current.available_minor_units==2000
        header=prepared.plan.data['header'];old=prepared.plan.data['before']
        member=dict(old,version=old['version']+1)
        merged=coalesce_headers([(old,header),(old,member)],prepared.provenance,s.actor.id,'python')
        assert len(merged)==1 and merged[0][1]['version']==2 and merged[0][1]['current_revision_id']==header['current_revision_id']
    with monkeypatch.context() as patch:
        compare_observer(patch,'payment',check)
        client.run('payment update',args,company=COMPANY,reason='Correct actual cash',dry_run=True)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before


from tests.test_tax_policy_sales import tax_sale, request as tax_request
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_deposit_sources import uf


def test_n7_keyless_work_tax_redistribution_keeps_full_billing_and_cash_confirmation(client,tax_sale,monkeypatch):
    source=accepted(client,tax_sale,**tax_request(tax_sale))
    receipt=bill(client,source,verb='sales-receipt',deposit_to=uf(client),payment_method='Check',amount_received='0.22')
    retained=receipt['revision']['lines'][1]
    data=dict(sales_receipt=receipt['id'],expected_version=1,lines=[dict(line_id=retained['line_id'],item=tax_sale['item']),dict(item=tax_sale['item'],net_amount='0.10',tax_code=tax_sale['taxable'])])
    path=database_path(client)
    with sqlite3.connect(path) as db:before=tuple(db.iterdump())
    def check(ordinary,prepared,s):
        assert prepared.cash.cash_minor_units==22
        assert prepared.plan.preview.revision.lines[0].tax_minor_units==2
        allocations=prepared.plan.data['billing_allocations']
        assert len(allocations)==1 and allocations[0]['tax_minor_units']==2
        assert prepared.plan.preview.revision.billing_sources[0].tax_minor_units==2
        assert any(component.sale_line.allocation_proof is not None for component in prepared.cash.components)
        assert {r['tax_item_id'] for r in prepared.plan.data['pending']['sales_tax_components']}==set(tax_sale['rules'])
        assert prepared.bank_changes.before==prepared.bank_changes.after==()
    with monkeypatch.context() as patch:
        compare_observer(patch,'sales-receipt',check)
        client.run('sales-receipt update',data,company=COMPANY,dry_run=True)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before
    # Exercise the actual owning confirmation predicate, retaining full original
    # source plans through the keyless boundary on each successful correction.
    revised=client.run('sales-receipt update',data,company=COMPANY)
    independent=revised['revision']['lines'][1]
    smaller=dict(sales_receipt=receipt['id'],expected_version=2,lines=[dict(line_id=independent['line_id'],item=tax_sale['item'])])
    with sqlite3.connect(path) as db:before=tuple(db.iterdump())
    with pytest.raises(BookflowError):client.run('sales-receipt update',smaller,company=COMPANY,dry_run=True)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before
    def reduced(ordinary,prepared,s):
        assert prepared.cash.cash_minor_units==11
        assert prepared.plan.data['billing_allocations']==[]
        assert prepared.plan.preview.revision.billing_sources==[]
    with monkeypatch.context() as patch:
        compare_observer(patch,'sales-receipt',reduced)
        client.run('sales-receipt update',dict(smaller,amount_received='0.11'),company=COMPANY,dry_run=True)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before


from tests.test_deposit_lifecycle import driver, additional_document, replacement


def test_n2_actual_claim_source_overlay_and_complete_independent_money(client,sale,driver):
    from bookflow.company import payment_corrections, deposits, deposit_sources
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.company.deposit_coordination import prepare_source_overlay
    from bookflow.core.context import Context, Interface
    from bookflow.core.publication import OSBinding
    from tests.test_deposit_g1 import nets
    invoice=posted(client,sale['customer'],sale['item'],'100','B-CLAIM-N2')
    payment_method=method(client)
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=payment_method,operation_key='B-claimed-receive',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='100')])),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=payment_method,lines=[dict(item=sale['item'],quantity='1',unit_price='60')]),company=COMPANY)
    document=additional_document(client,sale,'20',cash='5')
    fee=client.account.create(name='B N2 merchant fee',type='expense',company=COMPANY)['id']
    document['additional'].append(dict(received_from=dict(kind='customer',id=sale['customer']),from_account=fee,amount='-3'))
    document['sources']=[dict(source_type='payment',source=payment['id'],expected_version=1),dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-claimed-deposit',document=document))
    assert deposited.effect.financial.bank_total==17200
    body=replacement(deposited,document)
    body['sources']=[dict(source_result=True,source=payment['id']),dict(source_type='sales_receipt',source=receipt['id'],expected_version=2)]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-N2-coordinate',
        replacement=dict(mode='document',document=body),source_action=dict(kind='payment_update',input=dict(payment=payment['id'],expected_version=2,amount='120',invoice_versions=[dict(invoice=invoice['id'],expected_version=2)])))
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'B overlay',reason='Correct actual received cash and bank once')
        binding=OSBinding.from_session(s)
        provenance=EffectProvenance(at=clock.now_iso(),event_id=new_id(),operation_id=new_id())
        original=payment_corrections.prepare_effect(s,ctx,inp.source_action.input,provenance)
        source=composition.prepare_source_effect(s,ctx,inp.source_action,provenance=provenance,expected_fingerprint=original.preview.facts_fingerprint)
        overlay=prepare_source_overlay(s,ctx,inp,source,binding)
        prior=deposited.effect.financial
        other=prior.intent.sources[1].model_copy(update={'source':deposit_sources.load(s,receipt['id'])})
        proposed=deposits.prepare(prior.intent.model_copy(update={'sources':(overlay.retained_row,other)}))
        assert (proposed.posting_total,proposed.subtotal,proposed.bank_total,proposed.cash_back)==(20000,19700,19200,500)
        assert overlay.retained_row.row_id==prior.intent.sources[0].row_id
        assert overlay.retained_row.occurrences==prior.intent.sources[0].occurrences
        assert overlay.retained_row.source.expected_header_version==3
        delta=nets(proposed);old=nets(prior)
        for account,units in old.items():delta[account]=delta.get(account,0)-units
        for row in source.plan.data['pending']['posting_lines']:
            delta[row['account_id']]=delta.get(row['account_id'],0)+row['debit_minor_units']-row['credit_minor_units']
        assert {key:value for key,value in delta.items() if value}=={document['deposit_to']:2000,invoice['revision']['profile']['control_account']['id']:-2000}
        assert source.plan.preview.current.available_minor_units==2000
        from bookflow.company.deposit_coordination import resolve_coordinate
        from bookflow.company.deposit_models import Effect
        import json
        combined=resolve_coordinate(s,ctx,inp,binding)
        resolved=Effect.model_validate_json(json.dumps(json.loads(combined.deposit_data_json)['financial']))
        assert (resolved.posting_total,resolved.subtotal,resolved.bank_total,resolved.cash_back)==(20000,19700,19200,500)
        assert resolved.cells==proposed.cells and resolved.legs==proposed.legs
        assert resolved.intent.sources[0].row_id==prior.intent.sources[0].row_id
        assert resolved.intent.sources[0].occurrences==prior.intent.sources[0].occurrences
        versions={old['id']:(old['version'],new['version']) for old,new in json.loads(combined.headers_json)}
        assert versions=={payment['id']:(2,3),receipt['id']:(2,3),invoice['id']:(2,3),deposited.current.id:(1,2)}
        assert [(v.account_id,v.signed_debit) for v in combined.deposit_bank_after if v.active]==[(document['deposit_to'],19200)]
        from bookflow.company import deposit_dependency_history as history
        request=history.request(dict(command='deposit coordinate',input=inp.model_dump(mode='json',exclude_unset=True),context={'reason':ctx.reason}))
        recipe,readset=history.capture(s,request,binding)
        assert not readset.unknown
        assert set(readset.transactions)=={payment['id'],receipt['id'],invoice['id'],deposited.current.id}
        guard=history.issue(s,recipe,readset,binding)
        assert history.compare(s,guard,request,binding).matches
        from bookflow.company import deposit_coordination as coordinator
        first=coordinator.prepare(s,ctx,inp,binding=binding)
        anchored=inp.model_copy(update={'dependency_guard':first.dependency_guard,'expected_facts_fingerprint':first.facts_fingerprint})
        second=coordinator.prepare(s,ctx,anchored,binding=binding,expected_source_fingerprint=first.resolution.source.source_fingerprint)
        assert first.facts_fingerprint==second.facts_fingerprint
        coordinator.validate(s,ctx,first)
        from dataclasses import replace
        broken=replace(first,resolution=copy.deepcopy(first.resolution))
        wrong=json.loads(broken.resolution.deposit_data_json)
        wrong['financial']['bank_total']+=1
        broken=replace(broken,resolution=replace(broken.resolution,deposit_data_json=json.dumps(wrong)))
        with pytest.raises(BookflowError):coordinator.validate(s,ctx,broken)
        broken=replace(first,resolution=copy.deepcopy(first.resolution))
        bad_headers=json.loads(broken.resolution.headers_json);bad_headers[0][1]['version']+=1
        broken=replace(broken,resolution=replace(broken.resolution,headers_json=json.dumps(bad_headers)))
        with pytest.raises(BookflowError):coordinator.validate(s,ctx,broken)
        with pytest.raises(BookflowError) as mismatch:
            coordinator.prepare(s,ctx,anchored,binding=binding,expected_source_fingerprint='0'*64)
        assert mismatch.value.code=='E_PREVIEW_STALE'
        removed=copy.deepcopy(body);removed['sources']=[body['sources'][1]]
        raw=inp.model_dump(mode='json',exclude_unset=True)
        bank_action=copy.deepcopy(raw);bank_action['source_action']['input']['deposit_to']=document['deposit_to']
        bank_action['replacement']['document']=removed
        bank_input=CoordinateInput.model_validate_json(json.dumps(bank_action))
        redirected=coordinator.prepare(s,ctx,bank_input,binding=binding)
        assert redirected.resolution.source.cash is None
        assert [(v.account_id,v.signed_debit) for v in redirected.resolution.source.bank_changes.after if v.active]==[(document['deposit_to'],12000)]
        again=coordinator.prepare(s,ctx,bank_input,binding=binding)
        assert redirected.facts_fingerprint==again.facts_fingerprint
        # The void source disappears while the other funding remains real.
        void_action=copy.deepcopy(raw)
        void_action['source_action']=dict(kind='payment_void',payment=payment['id'],expected_version=2,unapply='all_active')
        void_action['replacement']['document']=removed
        canceled=coordinator.prepare(s,ctx,CoordinateInput.model_validate_json(json.dumps(void_action)),binding=binding)
        assert canceled.resolution.source.cash is None
        assert len(canceled.resolution.source.plan.data['cancellation_set'].application_ids)==1
        assert {v.invoice_id:v.due_minor_units for v in canceled.resolution.source.plan.preview.effect.document_changes}=={invoice['id']:10000}
        assert json.loads(canceled.resolution.deposit_data_json)['financial']['bank_total']==7200
        unchanged=copy.deepcopy(void_action);unchanged['source_action']['unapply']='retain_none'
        with pytest.raises(BookflowError) as applications:
            coordinator.prepare(s,ctx,CoordinateInput.model_validate_json(json.dumps(unchanged)),binding=binding)
        assert applications.value.code=='E_HAS_APPLICATIONS'
        invalid_retain=copy.deepcopy(void_action);invalid_retain['replacement']['document']=body
        with pytest.raises(BookflowError) as invalid:
            coordinator.prepare(s,ctx,CoordinateInput.model_validate_json(json.dumps(invalid_retain)),binding=binding)
        assert invalid.value.code=='E_DEPOSIT_SOURCE_INELIGIBLE'
        # Wrong-source cash and a foreign claim owner cannot be supplied as proof.
        from dataclasses import replace
        bad=replace(source,cash=source.cash.model_copy(update={'cash_minor_units':12001}))
        with pytest.raises(BookflowError):prepare_source_overlay(s,ctx,inp,bad,binding)
    assert driver.dump()==before


def test_source_result_requires_literal_boolean_not_integer():
    from bookflow.company.deposit_coordinate_models import SourceResult
    from pydantic import ValidationError
    assert SourceResult(source_result=True,source=new_id()).source_result is True
    for value in (1,0,'true',None):
        with pytest.raises(ValidationError):SourceResult(source_result=value,source=new_id())


def test_exact_coalescing_noop_conflict_max_and_text_identity_disposition():
    from bookflow.company.deposit_coordination import canonical_source_data
    from bookflow.core.registry import Plan
    from bookflow.company.payment_outputs import PaymentSourceOutput
    from types import SimpleNamespace
    before=dict(id='stable-source',version=7,current_revision_id='old',updated_at='oldtime',updated_by='oldactor',updated_via='python',memo='oldmemo')
    provenance=EffectProvenance(at='2026-06-03T00:00:00+00:00',event_id=new_id(),operation_id=new_id())
    assert coalesce_headers([(before,before)],provenance,'actor','python')==()
    financial=dict(before,version=8,current_revision_id='new')
    membership=dict(before,version=8)
    result=coalesce_headers([(before,membership),(before,financial)],provenance,'actor','python')
    assert result[0][1]==dict(before,version=8,current_revision_id='new',updated_at=provenance.at,updated_by='actor',updated_via='python')
    with pytest.raises(BookflowError):coalesce_headers([(before,financial),(before,dict(financial,current_revision_id='other'))],provenance,'actor','python')
    maximum=dict(before,version=9223372036854775807)
    with pytest.raises(BookflowError) as caught:coalesce_headers([(maximum,dict(maximum,version=9223372036854775808))],provenance,'actor','python')
    assert caught.value.code=='E_VALUE_RANGE'
    # Memo text that equals a generated ID is still text. Only classified row
    # identities and foreign references may change in the equality projection.
    generated=new_id()
    plan=Plan(SimpleNamespace(),dict(input=PaymentUpdateIntent(payment='stable-source',expected_version=7,memo=generated),
        operation='update',pending={'transaction_revisions':[dict(id=generated,transaction_id='stable-source',memo=generated)]}))
    projection=canonical_source_data(plan,payment=True)
    assert projection['pending']['transaction_revisions'][0]==dict(id='transaction_revisions/0',transaction_id='stable-source',memo=generated)
    assert projection['input']['memo']==generated and plan.data['pending']['transaction_revisions'][0]['id']==generated


def test_n7_claimed_work_source_complete_history_and_cash_confirmation(client,tax_sale,driver):
    import json
    from bookflow.company import deposit_coordination as coordinator
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    work=accepted(client,tax_sale,**tax_request(tax_sale))
    receipt=bill(client,work,verb='sales-receipt',deposit_to=uf(client),payment_method='Check',amount_received='0.22')
    document=additional_document(client,tax_sale,'1')
    document['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-guard-work-post',document=document))
    body=replacement(deposited,document);body['sources']=[dict(source_result=True,source=receipt['id'])]
    source=dict(sales_receipt=receipt['id'],expected_version=2,amount_received='0.33',
        lines=[dict(line_id=receipt['revision']['lines'][0]['line_id'],item=tax_sale['item']),
               dict(item=tax_sale['item'],net_amount='0.20',tax_code=tax_sale['taxable'])])
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-guard-work-coordinate',
        source_action=dict(kind='sales_receipt_update',input=source),replacement=dict(mode='document',document=body))
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'B N7 guarded work',reason='Retain allocated work and capture actual receipt correction')
        binding=OSBinding.from_session(s)
        planned=coordinator.prepare(s,ctx,inp,binding=binding)
        assert planned.resolution.source.cash.cash_minor_units==33
        assert json.loads(planned.resolution.deposit_data_json)['financial']['bank_total']==133
        assert len(planned.resolution.source.plan.data['billing_allocations'])==1
        facts=json.loads(planned.readset_json)
        assert not facts['unknown']
        assert any(row['kind']=='work_document' and row['id']==work['id'] for row in facts['records'])
        assert any(row['kind']=='source_item' and row['id']==tax_sale['item'] for row in facts['records'])
        anchored=inp.model_copy(update={'dependency_guard':planned.dependency_guard,'expected_facts_fingerprint':planned.facts_fingerprint})
        again=coordinator.prepare(s,ctx,anchored,binding=binding)
        assert again.facts_fingerprint==planned.facts_fingerprint
    assert driver.dump()==before


@pytest.mark.parametrize('kind,first_price,changed', [('fixed_percent',1357,dict(percent='20')),('per_item',1500,None)])
def test_captured_price_version_survives_later_rules_with_complete_guard(client,sale,driver,kind,first_price,changed):
    import json
    from bookflow.company import deposit_coordination as coordinator,deposit_dependency_history as history
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    client.company.update(enable_price_levels=True,company=COMPANY)
    rule=dict(percent='10') if kind=='fixed_percent' else dict(items=[dict(item_id=sale['item'],price='15.00',adjustment_basis='standard_price')])
    level=client.run('price-level create',dict(name='B captured source pricing',kind=kind,**rule),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),
        payment_method=method(client),price_level=level['id'],lines=[dict(item=sale['item'])]),company=COMPANY)
    assert receipt['revision']['lines'][0]['unit_price_minor_units']==first_price
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-captured-price-post',document=doc))
    if changed is None:changed=dict(items=[dict(item_id=sale['item'],price='25.00',adjustment_basis='standard_price')])
    client.run('price-level update',dict(price_level=level['id'],expected_version=1,**changed),company=COMPANY)
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-captured-price-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,
            lines=[dict(line_id=receipt['revision']['lines'][0]['line_id'],item=sale['item']),dict(item=sale['item'])])),
        replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'B captured source price',reason='Add a line under the captured selected price rule')
    before=driver.dump()
    with driver.session() as s:
        binding=OSBinding.from_session(s)
        first=coordinator.prepare(s,ctx,inp,binding=binding)
        assert [row.unit_price_minor_units for row in first.resolution.source.plan.preview.revision.lines]==[first_price,first_price]
        facts=json.loads(first.readset_json)
        assert not facts['unknown']
        captures=[row for row in facts['records'] if row['kind']=='source_price_version']
        assert len(captures)==1 and captures[0]['id']==level['id']+'@1'
        assert captures[0]['version']==1
        anchored=inp.model_copy(update={'dependency_guard':first.dependency_guard,'expected_facts_fingerprint':first.facts_fingerprint})
        second=coordinator.prepare(s,ctx,anchored,binding=binding)
        assert first.facts_fingerprint==second.facts_fingerprint
    assert driver.dump()==before


def test_same_tax_identity_new_agency_and_default_class_restates_source_and_deposit(client,tax_sale,driver):
    import json
    from bookflow.company import deposit_coordination as coordinator,sales
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    first=client.run('class create',dict(name='B original cash class'),company=COMPANY)['id']
    second=client.run('class create',dict(name='B corrected cash class'),company=COMPANY)['id']
    client.company.update(use_classes=True,company=COMPANY)
    client.run('item update',dict(item=tax_sale['item'],expected_version=1,default_class_id=first),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=tax_sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        sales_tax_item=tax_sale['group'],lines=[dict(item=tax_sale['item'],net_amount='1.00',tax_code=tax_sale['taxable'])]),company=COMPANY)
    document=additional_document(client,tax_sale,'1');document['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    posted_deposit=driver.run('post',dict(operation_key='B-tax-class-seed',document=document))
    agency=client.vendor.create(name='B corrected tax agency',is_tax_agency=True,company=COMPANY)['id']
    client.run('item update',dict(item=tax_sale['rules'][0],expected_version=1,tax_agency_vendor_id=agency),company=COMPANY)
    client.run('item update',dict(item=tax_sale['item'],expected_version=2,default_class_id=second),company=COMPANY)
    body=replacement(posted_deposit,document);body['sources']=[dict(source_result=True,source=receipt['id'])]
    # Also exercise the deposit's actual public class alias in the complete
    # original-request codec; it is not normalized into another input spelling.
    body['additional'][0]['class']=second
    inp=CoordinateInput(deposit=posted_deposit.current.id,expected_version=1,operation_key='B-tax-class-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,refresh_defaults=True)),
        replacement=dict(mode='document',document=body))
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'B metadata restatement',reason='Capture the corrected agency and cash class')
        binding=OSBinding.from_session(s)
        prepared=coordinator.prepare(s,ctx,inp,binding=binding)
        source=prepared.resolution.source
        assert source.cash.cash_minor_units==110 and prepared.resolution.changed
        assert source.plan.preview.changed
        assert {component.cash.class_id for component in source.cash.components}=={second}
        taxes={component.tax.tax_item.id:component.tax.agency.id for component in source.cash.components if component.tax is not None}
        assert taxes[tax_sale['rules'][0]]==agency and set(taxes)==set(tax_sale['rules'])
        financial=json.loads(prepared.resolution.deposit_data_json)['financial']
        assert financial['bank_total']==210
        assert prepared.resolution.overlay.retained_row.occurrences==posted_deposit.effect.financial.intent.sources[0].occurrences
        ordinary=sales.prepare(s,ctx,inp.source_action.input,'sales_receipt','update',provenance=source.provenance)
        assert canonical_source_data(ordinary,payment=False)==canonical_source_data(source.plan,payment=False)
        coordinator.validate(s,ctx,prepared)
        assert not json.loads(prepared.readset_json)['unknown']
    assert driver.dump()==before


def test_ordinary_vendor_history_uses_actual_name_and_attributes_rename(client,sale,driver):
    from bookflow.company import deposit_lifecycle,deposit_dependency_history as history
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    vendor=client.vendor.create(name='B actual vendor history',company=COMPANY)['id']
    doc=additional_document(client,sale,'1');doc['additional'][0]['received_from']=dict(kind='vendor',id=vendor)
    inp=deposit_lifecycle.INPUTS['post'].model_validate(dict(operation_key='B-vendor-post',document=doc))
    ctx=Context.new(Interface.python,'B vendor history')
    before=driver.dump()
    with driver.session() as s:
        binding=OSBinding.from_session(s)
        request=history.request(dict(command='deposit post',input=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)))
        recipe,facts=history.capture(s,request,binding)
        assert not facts.unknown
        selected=[row for row in facts.records if row.kind=='vendor' and row.id==vendor]
        assert len(selected)==1
        import json
        assert json.loads(selected[0].semantic_json)==dict(id=vendor,name='B actual vendor history',active=True)
        guard=history.issue(s,recipe,facts,binding)
        prepared=deposit_lifecycle.prepare(s,ctx,inp,'post',binding=binding)
        assert prepared.dependency_guard
    assert driver.dump()==before
    client.vendor.update(vendor=vendor,expected_version=1,name='B renamed vendor history',company=COMPANY)
    before=driver.dump()
    with driver.session() as s:
        compared=history.compare(s,guard,request,OSBinding.from_session(s))
        assert not compared.matches and not compared.unknown_history
        changes=[row for row in compared.changes if row.kind=='vendor']
        assert len(changes)==1 and changes[0].record_id==vendor and changes[0].fields==('name',)
    assert driver.dump()==before


def test_coordinate_noeffect_forward_date_and_closed_period_guard(client,sale,driver):
    import json
    from bookflow.company import deposit_coordination as coordinator
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],net_amount='1.00')]),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-date-seed',document=doc))
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-date-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2)),replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'B dates and equality',reason='Review complete source and deposit dates')
    before=driver.dump()
    with driver.session() as s:
        binding=OSBinding.from_session(s)
        unchanged=coordinator.prepare(s,ctx,inp,binding=binding)
        assert not unchanged.resolution.changed and not unchanged.resolution.source.plan.preview.changed
        assert json.loads(unchanged.resolution.headers_json)==[]
        coordinator.validate(s,ctx,unchanged)
        raw=inp.model_dump(mode='json',by_alias=True,exclude_unset=True)
        raw['source_action']['input']['date']='2026-06-04'
        with pytest.raises(BookflowError) as wrong:
            coordinator.prepare(s,ctx,CoordinateInput.model_validate_json(json.dumps(raw)),binding=binding)
        assert wrong.value.code=='E_DEPOSIT_DATE_BEFORE_SOURCE'
        raw['replacement']['document']['date']='2026-06-05'
        forward=CoordinateInput.model_validate_json(json.dumps(raw))
        prepared=coordinator.prepare(s,ctx,forward,binding=binding)
        assert prepared.resolution.source.cash.receipt_date=='2026-06-04'
        assert json.loads(prepared.resolution.deposit_data_json)['financial']['intent']['date']=='2026-06-05'
        anchored=forward.model_copy(update={'dependency_guard':prepared.dependency_guard})
        coordinator.validate(s,ctx,prepared)
    assert driver.dump()==before
    client.company.update(closing_date='2026-06-02',company=COMPANY)
    before=driver.dump()
    with driver.session() as s:
        binding=OSBinding.from_session(s)
        with pytest.raises(BookflowError) as stale:coordinator.prepare(s,ctx,anchored,binding=binding)
        assert stale.value.code=='E_PREVIEW_STALE' and stale.value.details['history']=='known_stale'
        with pytest.raises(BookflowError) as closed:coordinator.prepare(s,ctx,forward,binding=binding)
        assert closed.value.code=='E_PERIOD_CLOSED' and closed.value.details['date']=='2026-06-02'
    assert driver.dump()==before


from tests.test_deposit_dependency_binding import bound_people,_credential,observe as binding_observe,_storage


def test_coordinate_actual_fixed_principal_and_transfer_denials(root,client,sale,driver,monkeypatch,bound_people):
    from bookflow.company import deposit_coordination as coordinator
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    people=bound_people
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1',payment_method=method(client),operation_key='B-agent-source'),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='payment',source=payment['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-agent-deposit',document=doc))
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=payment['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-agent-coordinate',
        source_action=dict(kind='payment_update',input=dict(payment=payment['id'],expected_version=2,amount='2')),replacement=dict(mode='document',document=body))
    one=_credential(client,people['agent'],people['first']);two=_credential(client,people['agent'],people['second'])
    def run(s):
        ctx=Context.new(Interface.python,'B actual principal',reason='Correct actual received cash',on_behalf_of=people['first'])
        prepared=coordinator.prepare(s,ctx,inp,binding=one)
        return inp.model_copy(update={'dependency_guard':prepared.dependency_guard})
    before=_storage(root,database_path(client))
    anchored=binding_observe(people['bot'],monkeypatch,run,people['company'])
    def mismatch(s):
        ctx=Context.new(Interface.python,'B other principal',reason='Correct actual received cash',on_behalf_of=people['second'])
        with pytest.raises(BookflowError) as denied:coordinator.prepare(s,ctx,anchored,binding=two)
        assert denied.value.code=='E_VALIDATION' and denied.value.details==dict(field='dependency_guard',reason='invalid_guard')
    binding_observe(people['bot'],monkeypatch,mismatch,people['company'])
    assert _storage(root,database_path(client))==before
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==people['first'],h.memberships.c.scope_id==people['company']).values(role='readonly'))
    before=_storage(root,database_path(client))
    def permission(s):
        ctx=Context.new(Interface.python,'B revoked writer',reason='Correct actual received cash',on_behalf_of=people['first'])
        with pytest.raises(BookflowError) as denied:coordinator.prepare(s,ctx,anchored,binding=one)
        assert denied.value.code=='E_PERMISSION'
        assert all(identity not in str(denied.value.details) for identity in (payment['id'],deposited.current.id,people['first']))
    binding_observe(people['bot'],monkeypatch,permission,people['company'])
    assert _storage(root,database_path(client))==before


def test_n6_actual_zero_capacity_roundtrip_retains_component_and_occurrence(client,sale,driver,monkeypatch):
    from bookflow.company import deposits,deposit_sources,deposit_coordination as coordinator
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    job=client.customer.create(name='B capacity job',parent_id=sale['customer'],company=COMPANY)['id']
    invoice=posted(client,job,sale['item'],'1','B-ZERO-CAPACITY')
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='2',payment_method=method(client),operation_key='B-zero-receive',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=COMPANY)
    key=next(row['component_key_id'] for row in payment['current']['components'] if row['party_id']==sale['customer'])
    with driver.session() as s:
        initial=deposit_sources.load(s,payment['id']);occurrences=deposits.occurrences(initial)
    original_occurrences=occurrences
    for version,amount,capacity in ((1,'1',0),(2,'2',100)):
        body=dict(payment=payment['id'],expected_version=version,amount=amount,operation_key='B-zero-'+str(version),
            invoice_versions=[dict(invoice=invoice['id'],expected_version=version+1)])
        before=driver.dump();seen=[]
        def check(ordinary,prepared,s):
            payer=next(row for row in prepared.plan.preview.current.components if row.party_id==sale['customer'])
            assert payer.component_key_id==key and payer.received_minor_units==capacity
            assert (payer.component_id is None)==(capacity==0)
            assert key in {item.identity for item in prepared.cash.semantic_presence}
            if capacity==0:assert key not in {item.key.identity for item in prepared.cash.components}
            else:assert key in {item.key.identity for item in prepared.cash.components}
            seen.append(deposits.occurrences(prepared.cash,occurrences))
        with monkeypatch.context() as patch:
            compare_observer(patch,'payment',check)
            client.run('payment update',body,company=COMPANY,reason='Correct actual payer capacity',dry_run=True)
        assert driver.dump()==before and len(seen)==1
        occurrences=seen[0]
        client.run('payment update',body,company=COMPANY,reason='Correct actual payer capacity')
    assert occurrences==original_occurrences
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='payment',source=payment['id'],expected_version=3)]
    deposited=driver.run('post',dict(operation_key='B-zero-deposit',document=doc))
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=payment['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-zero-coordinate',
        source_action=dict(kind='payment_update',input=dict(payment=payment['id'],expected_version=4,amount='1',invoice_versions=[dict(invoice=invoice['id'],expected_version=4)])),
        replacement=dict(mode='document',document=body))
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'B zero retained claim',reason='Retain the semantic payer identity at zero capacity')
        prepared=coordinator.prepare(s,ctx,inp,binding=OSBinding.from_session(s))
        assert prepared.resolution.overlay.retained_row.occurrences==deposited.effect.financial.intent.sources[0].occurrences
        assert prepared.resolution.overlay.retained_row.row_id==deposited.effect.financial.intent.sources[0].row_id
        assert key in {item.identity for item in prepared.resolution.source.cash.semantic_presence}
        assert key not in {item.key.identity for item in prepared.resolution.source.cash.components}
        coordinator.validate(s,ctx,prepared)
    assert driver.dump()==before


def test_unit_selection_and_relevant_changes_keep_complete_original_source_plan(client,sale,driver):
    import json
    from bookflow.company import deposit_coordination as coordinator,sales
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    client.company.update(units_of_measure_mode='multiple_related_units',company=COMPANY)
    units=client.run('unit-of-measure create',dict(name='B actual units',units=[dict(name='Each',abbreviation='ea',is_base=True,base_factor='1'),
        dict(name='Half',abbreviation='hf',base_factor='0.5')]),company=COMPANY)
    client.run('item update',dict(item=sale['item'],expected_version=1,unit_of_measure_set_id=units['id']),company=COMPANY)
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],unit='ea')]),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-unit-seed',document=doc))
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-unit-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,
            lines=[dict(item=sale['item'],line_id=receipt['revision']['lines'][0]['line_id'],unit='hf',quantity='2')])),replacement=dict(mode='document',document=body))
    before=driver.dump()
    with driver.session() as s:
        ctx=Context.new(Interface.python,'B units',reason='Use two half units with captured exact pricing')
        prepared=coordinator.prepare(s,ctx,inp,binding=OSBinding.from_session(s))
        line=prepared.resolution.source.plan.preview.revision.lines[0]
        assert (line.unit_factor_nanounits,line.unit_price_minor_units,line.net_minor_units)==(500000000,617,1234)
        ordinary=sales.prepare(s,ctx,inp.source_action.input,'sales_receipt','update',provenance=prepared.resolution.source.provenance)
        assert canonical_source_data(ordinary,payment=False)==canonical_source_data(prepared.resolution.source.plan,payment=False)
        facts=json.loads(prepared.readset_json)
        assert not facts['unknown'] and any(row['kind']=='source_unit' and row['id']==units['id'] for row in facts['records'])
        coordinator.validate(s,ctx,prepared)
    assert driver.dump()==before


def test_source_refresh_issuer_rename_requires_complete_guard_change(client,sale,driver,monkeypatch):
    """Required contract witness: do not waive an unaudited source-copy read."""
    import json
    from bookflow.company import deposit_coordination as coordinator,deposit_dependency_history as history
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],net_amount='1')]),company=COMPANY)
    doc=additional_document(client,sale,'1');doc['sources']=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)]
    deposited=driver.run('post',dict(operation_key='B-source-issuer-seed',document=doc))
    company=client.company.list()['items'][0]['company_id']
    body=replacement(deposited,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    inp=CoordinateInput(deposit=deposited.current.id,expected_version=1,operation_key='B-source-issuer-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=receipt['id'],expected_version=2,refresh_defaults=True)),
        replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'B source issuer witness',reason='Refresh complete source defaults')
    def prepare(s):return coordinator.prepare(s,ctx,inp,binding=OSBinding.from_session(s))
    first=binding_observe(client,monkeypatch,prepare,company)
    old_name=first.resolution.source.plan.preview.revision.issuer_snapshot['display_name']
    path=database_path(client)
    with sqlite3.connect(path) as db:
        company_audit=tuple(db.execute("SELECT id FROM audit_entries WHERE record_type='company_info' ORDER BY id"))
    client.run('company rename',dict(name='B refreshed source issuer',move=False),company=company)
    with sqlite3.connect(path) as db:
        assert tuple(db.execute("SELECT id FROM audit_entries WHERE record_type='company_info' ORDER BY id"))==company_audit
        before=tuple(db.iterdump())
    anchored=inp.model_copy(update={'dependency_guard':first.dependency_guard})
    def inspect(s):
        binding=OSBinding.from_session(s)
        original=history.request(dict(command='deposit coordinate',input=anchored.model_dump(mode='json',by_alias=True,exclude_unset=True),context={'reason':ctx.reason}))
        compared=history.compare(s,first.dependency_guard,original,binding)
        fresh=coordinator.prepare(s,ctx,anchored,binding=binding)
        assert fresh.resolution.source.plan.preview.revision.issuer_snapshot['display_name']=='B refreshed source issuer'
        assert old_name!='B refreshed source issuer'
        assert json.loads(fresh.resolution.deposit_data_json)['issuer']['display_name']==old_name
        assert fresh.facts_fingerprint!=first.facts_fingerprint
        return compared
    comparison=binding_observe(client,monkeypatch,inspect,company)
    with sqlite3.connect(path) as db:assert tuple(db.iterdump())==before
    assert not comparison.matches, 'A source issuer changed without company history; complete coordinate guard incorrectly still matches'
