"""Real dispatch transaction, private financial consumer; no public activation."""
import json
import pytest
from bookflow.core import registry
from bookflow.company import deposit_drafts as drafts, deposit_lifecycle as lifecycle, deposit_persistence as persistence
from bookflow.company import deposit_draft_models as m
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_deposit_drafts import cash


@pytest.fixture
def run_private(client,monkeypatch):
    def run(function):
        command=registry.get('company show');result=[]
        def apply(plan,ctx,s):
            result.append(function(s,ctx.model_copy(update={'reason':'Owned financial witness'})))
            return registry.Applied(plan.preview,[],'Owned financial witness',audited=True)
        with monkeypatch.context() as patch:
            patch.setattr(command,'writes',frozenset({'company'}))
            patch.setattr(command,'kind','write');patch.setattr(command,'truth','company');patch.setattr(command,'apply',apply)
            client.run('company show',{},company=COMPANY)
        return result[0]
    return run


def financial(run,body,verb='post'):
    def action(s,ctx):
        inp=lifecycle.INPUTS[verb].model_validate(body)
        p=lifecycle.prepare(s,ctx,inp,verb)
        if isinstance(p,lifecycle.Prepared):
            inp=inp.model_copy(update={'dependency_guard':p.dependency_guard})
            p=lifecycle.prepare(s,ctx,inp,verb)
            return persistence.execute(s,ctx,p)
        return p
    return run(action)


def test_real_draft_post_consumes_and_preserves_revision(client,cash,run_private):
    bank=client.account.create(name='Financial draft bank',type='bank',company=COMPANY)['id']
    draft=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create'))
    draft=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    saved=run_private(lambda s,ctx:s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(draft.revision_id,)).fetchone()[0])
    body=dict(operation_key='consume-one',document=dict(mode='draft',draft=draft.id,expected_version=draft.version))
    posted=financial(run_private,body)
    assert posted.current.revision_bank_total==6000
    def check(s,ctx):
        got=drafts.show(s,m.DraftShow(draft=draft.id),ctx=ctx)
        assert got.state=='consumed' and got.version==draft.version+1 and got.revision_id==draft.revision_id
        assert s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(draft.revision_id,)).fetchone()[0]==saved
        assert s.company.raw.execute('SELECT draft_id,revision_id,operation_id FROM deposit_draft_consumptions').fetchall()==[(draft.id,draft.revision_id,posted.operation_id)]
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        return tuple(s.company.raw.iterdump())
    def retry(s,ctx):
        before=check(s,ctx)
        replay=lifecycle.prepare(s,ctx,lifecycle.INPUTS['post'].model_validate(body),'post')
        assert replay.idempotent_replay and replay.effect==posted.effect
        assert check(s,ctx)==before
    run_private(retry)


def make_posted(client,cash,run_private):
    bank=client.account.create(name='G3 consumer bank',type='bank',company=COMPANY)['id']
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create'))
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    result=financial(run_private,dict(operation_key='first-post',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
    return result,d


@pytest.mark.parametrize('changed',[False,True])
def test_edit_draft_noeffect_or_update_consumes_once(client,cash,run_private,changed):
    posted,original=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    if changed:
        edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=edit.id,expected_version=edit.version,header=m.HeaderPatch(memo='Edited through saved draft')),'update'))
    out=financial(run_private,dict(deposit=posted.current.id,expected_version=1,operation_key='edit-post',document=dict(mode='draft',draft=edit.id,expected_version=edit.version)),'update')
    assert out.changed is changed and out.current.version==1+int(changed)
    assert out.effect.consumed_draft.draft_id==edit.id and out.current_draft.state=='consumed'
    assert len(out.effect.batch_ids)==(2 if changed else 0)
    assert out.current.revision_bank_total==6000
    def check(s,ctx):
        assert drafts.show(s,m.DraftShow(draft=original.id),ctx=ctx).state=='consumed'
        assert drafts.show(s,m.DraftShow(draft=edit.id),ctx=ctx).state=='consumed'
        assert s.company.raw.execute('SELECT count(*) FROM deposit_draft_consumptions').fetchone()[0]==2
    run_private(check)


@pytest.mark.parametrize('retain',[True,False])
def test_coordinate_draft_real_source_action_consumes(client,sale,cash,run_private,retain):
    from bookflow.company import deposit_coordination as coordinate, deposit_coordinate_persistence as cp
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.publication import OSBinding
    posted,original=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    if not retain:
        # Additional cash keeps the replacement positive when the source is removed.
        income=client.account.create(name='Coordinate extra income',type='income',company=COMPANY)['id']
        customer=sale['customer']
        edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=edit.id,expected_version=edit.version,remove_sources=[cash['source']],
            set_additional=[m.AdditionalPatch(received_from=m.Party(kind='customer',id=customer),from_account=income,amount='5')]),'update'))
    if retain:
        from bookflow.core.errors import BookflowError
        with pytest.raises(BookflowError) as denied:
            client.run('sales-receipt update',dict(sales_receipt=cash['source'],expected_version=2,memo='Coordinated memo'),company=COMPANY,reason='Owned financial witness')
        assert denied.value.code=='E_DEPOSIT_DEPENDENCY'
    inp=CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,operation_key='draft-coordinate',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=cash['source'],expected_version=2,memo='Coordinated memo')) if retain else dict(kind='sales_receipt_void',sales_receipt=cash['source'],expected_version=2),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),draft_source_result='retain' if retain else 'remove')))
    def execute(s,ctx):
        binding=OSBinding.from_session(s)
        p=coordinate.prepare(s,ctx,inp,binding=binding)
        p=coordinate.prepare(s,ctx,inp.model_copy(update={'dependency_guard':p.dependency_guard}),binding=binding)
        result=cp.execute(s,ctx,p)
        before=tuple(s.company.raw.iterdump())
        replay=cp.recover(s,ctx,inp,binding)
        assert replay.effect==result.effect and replay.idempotent_replay
        assert tuple(s.company.raw.iterdump())==before
        assert result.current_draft.id==edit.id and result.current_draft.state=='consumed'
        assert result.effect.deposit.consumed_draft.draft_id==edit.id
        assert result.current.revision_bank_total==(6000 if retain else 500)
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        return result
    run_private(execute)


@pytest.mark.parametrize('fault',['orphan_receipt','header_without_receipt','wrong_hash','deferred_link'])
def test_consumption_persisted_equivalence_rolls_back_with_caller_state(client,cash,run_private,monkeypatch,fault):
    from bookflow.company import deposit_draft_consumption as consumption,schema as c
    from bookflow.core.errors import BookflowError
    bank=client.account.create(name='Atomic consumption bank',type='bank',company=COMPANY)['id']
    def action(s,ctx):
        d=drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create')
        d=drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update')
        # This real preceding nonposting write belongs to the caller transaction.
        # Failure of the subsequent financial savepoint must preserve it exactly.
        before=tuple(s.company.raw.iterdump())
        inp=lifecycle.INPUTS['post'].model_validate(dict(operation_key='consume-fault',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
        prepared=lifecycle.prepare(s,ctx,inp,'post')
        original=consumption.persist
        def broken(session,value):
            if fault=='orphan_receipt':
                session.company.conn.execute(c.deposit_draft_consumptions.insert().values(**value['row']))
                with pytest.raises(BookflowError) as reader:drafts.show(session,m.DraftShow(draft=d.id),ctx=ctx)
                assert reader.value.details=={'reason':'unexpected_consumption'}
            elif fault=='header_without_receipt':session.company.conn.execute(c.deposit_drafts.update().where(c.deposit_drafts.c.id==d.id).values(**value['after']))
            elif fault=='deferred_link':original(session,dict(value,after=dict(value['after'],consumed_operation_id='00000000000000000000000000')))
            else:original(session,dict(value,row=dict(value['row'],manifest_hash='0'*64)))
        with monkeypatch.context() as patch:
            patch.setattr(consumption,'persist',broken)
            with pytest.raises(BookflowError) as error:persistence.execute(s,ctx,prepared)
        assert error.value.code=='E_VALIDATION'
        if fault in ('deferred_link','header_without_receipt'):assert error.value.details=={'reason':'deposit_draft_foreign_key'}
        assert tuple(s.company.raw.iterdump())==before
        assert drafts.show(s,m.DraftShow(draft=d.id),ctx=ctx).state=='open'
        out=persistence.execute(s,ctx,prepared)
        assert out.current_draft.state=='consumed'
    run_private(action)


def test_copy_uses_new_financial_keys_and_checks_pinned_origin(client,cash,run_private):
    import sqlalchemy as sa
    from bookflow.company import schema as c,deposit_draft_provider as provider
    from bookflow.core.errors import BookflowError
    posted,original=make_posted(client,cash,run_private)
    financial(run_private,dict(deposit=posted.current.id,expected_version=1,operation_key='copy-void'),'void')
    copied=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(copy_from_voided=posted.current.id,expected_version=2),'create'))
    def check(s,ctx):
        h,r,manifest,_=drafts.load(s,copied.id,ctx=ctx,write=True)
        keys=[dict(k) for k in s.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==copied.id)).mappings()]
        assert h['edit_transaction_id'] is None and h['copy_transaction_id']==posted.current.id
        assert keys[0]['original_row_id']==posted.effect.financial.intent.sources[0].row_id
        provider.validate_row_origins(s,h,keys)
        for change in (dict(kind='additional'),dict(edit_transaction_id=cash['source']),dict(original_row_id=original.id)):
            with pytest.raises(BookflowError):provider.validate_row_origins(s,h,[dict(keys[0],**change)])
        return tuple(s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE draft_id=?',(original.id,)))
    before=run_private(check)
    out=financial(run_private,dict(operation_key='copy-new',document=dict(mode='draft',draft=copied.id,expected_version=copied.version)))
    assert out.current.id!=posted.current.id
    assert out.effect.financial.intent.sources[0].row_id!=posted.effect.financial.intent.sources[0].row_id
    assert out.effect.consumed_draft.rows[0].draft_row_id!=out.effect.consumed_draft.rows[0].financial_row_id
    assert run_private(lambda s,ctx:tuple(s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE draft_id=?',(original.id,))))==before


def test_full_cashback_bank_can_deactivate_then_copy_reports_current_issue(client,cash,sale,run_private):
    from bookflow.core.errors import BookflowError
    bank=client.account.create(name='Unused full cashback bank',type='bank',company=COMPANY)['id']
    destination=client.account.create(name='Actual cashback destination',type='bank',company=COMPANY)['id']
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch.model_validate(dict(date='2026-06-03',deposit_to=bank,cash_back=dict(account=destination,amount='60')))),'create'))
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    posted=financial(run_private,dict(operation_key='cashback-all',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
    assert posted.current.revision_bank_total==0 and posted.effect.financial.cash_back==6000
    assert run_private(lambda s,ctx:s.company.raw.execute('SELECT count(*) FROM posting_lines WHERE account_id=?',(bank,)).fetchone()[0])==0
    financial(run_private,dict(deposit=posted.current.id,expected_version=1,operation_key='cashback-void'),'void')
    with pytest.raises(BookflowError) as blocked:
        client.account.deactivate(account=destination,expected_version=1,company=COMPANY)
    assert blocked.value.code=='E_RECORD_IN_USE'
    client.account.deactivate(account=bank,expected_version=1,company=COMPANY)
    copied=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(copy_from_voided=posted.current.id,expected_version=2),'create'))
    assert copied.header.bank.active and 'deposit_to:unavailable' in copied.posting_issues
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:lifecycle.prepare(s,ctx,lifecycle.INPUTS['post'].model_validate(dict(operation_key='inactive-copy',document=dict(mode='draft',draft=copied.id,expected_version=1))),'post')
        assert error.value.code=='E_DEPOSIT_DRAFT_STATE'
        assert tuple(s.company.raw.iterdump())==before
        assert drafts.show(s,m.DraftShow(draft=d.id),ctx=ctx).state=='consumed'
    run_private(check)


def test_provider_maps_only_captured_assertions_and_keeps_full_typed_values(client,cash,run_private):
    from bookflow.company import deposit_draft_provider as provider
    from bookflow.company.deposit_models import DraftDocument
    from bookflow.core.publication import OSBinding
    asserted=client.run('custom-field create',dict(name='Asserted kind',kind='bool',scopes=['deposit']),company=COMPANY)['id']
    omitted=client.run('custom-field create',dict(name='No asserted kind',kind='number',scopes=['deposit']),company=COMPANY)['id']
    bank=client.account.create(name='Typed capture bank',type='bank',company=COMPANY)['id']
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch.model_validate(dict(date='2026-06-03',deposit_to=bank,
        custom_fields={asserted:False,omitted:'1.000000001'},expected_custom_field_kinds={asserted:'bool'}))),'create'))
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        document=provider.load(s,ctx,DraftDocument(mode='draft',draft=d.id,expected_version=d.version),OSBinding.from_session(s))
        assert document.expected_custom_field_kinds.root=={asserted:'bool'}
        assert document.custom_fields.root=={asserted:False,omitted:'1.000000001'}
        raw=json.loads(document.pin.snapshot)['header']['custom_fields']
        assert raw[asserted]['expected_kind']=='bool' and 'expected_kind' not in raw[omitted]
        assert tuple(s.company.raw.iterdump())==before
    run_private(check)
    out=financial(run_private,dict(operation_key='typed-consumption',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
    def posted(s,ctx):
        raw=json.loads(s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(out.current.revision_id,)).fetchone()[0])
        assert raw[asserted]['value'] is False and raw[asserted]['canonical_text']=='false'
        assert raw[omitted]['value']=='1.000000001' and raw[omitted]['canonical_text']=='1.000000001'
        assert set(raw)=={asserted,omitted}
        saved=json.loads(s.company.raw.execute('SELECT request_snapshot FROM deposit_operations WHERE id=?',(out.operation_id,)).fetchone()[0])
        captures=json.loads(saved['resolved_draft']['snapshot'])['header']['custom_fields']
        assert captures[asserted]['expected_kind']=='bool' and 'expected_kind' not in captures[omitted]
    run_private(posted)


def test_bulk_graph_exact_rows_and_adapter_match_ordinary_owners(client,cash,sale,run_private):
    from bookflow.company import deposit_sources as source
    from tests.test_deposit_sources import uf
    from bookflow.core.errors import BookflowError
    payment=client.run('payment receive',dict(operation_key='bulk-owner-payment',customer=sale['customer'],deposit_to=uf(client),payment_method='Payment witness cash',date='2026-06-02',amount='60'),company=COMPANY)
    ids=[cash['source'],payment['id']]
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        many=source.graph_many(s,ids)
        assert set(many)==set(ids)
        for identity in ids:
            one=source.graph(s,identity)
            assert many[identity]['header']==one['header']
            assert set(many[identity])==set(one)
            for name in source.TABLES:
                normalize=lambda rows:sorted(json.dumps(r,sort_keys=True) for r in rows)
                assert normalize(many[identity][name])==normalize(one[name])
        assert source.load_many(s,ids)=={identity:source.load(s,identity) for identity in ids}
        with pytest.raises(BookflowError) as missing:source.graph_many(s,[*ids,'00000000000000000000000000'])
        assert missing.value.code=='E_RECORD_NOT_FOUND'
        assert tuple(s.company.raw.iterdump())==before
    run_private(check)

from tests.test_deposit_dependency_binding import bound_people


def test_permanent_draft_recovery_other_actor_and_revoked_principal(root,client,cash,run_private,bound_people,monkeypatch):
    from tests.test_deposit_dependency_binding import observe,_credential
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.core import clock
    from bookflow.core.context import Context,Interface
    from bookflow.core.errors import BookflowError
    posted,d=make_posted(client,cash,run_private)
    financial(run_private,dict(operation_key='later-void',deposit=posted.current.id,expected_version=1),'void')
    inp=lifecycle.INPUTS['post'].model_validate(dict(operation_key='first-post',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
    ctx=Context.new(Interface.python,'Owned financial witness',reason='Owned financial witness')
    def recover(s):
        before=tuple(s.company.raw.iterdump())
        out=lifecycle.prepare(s,ctx,inp,'post')
        assert out.idempotent_replay and out.effect==posted.effect and out.current.status=='voided'
        assert out.current_draft.state=='consumed' and out.current_draft.revision_id==d.revision_id
        assert tuple(s.company.raw.iterdump())==before
    observe(bound_people['two'],monkeypatch,recover,bound_people['company'])
    credential=_credential(client,bound_people['agent'],bound_people['first'])
    boundctx=ctx.model_copy(update={'on_behalf_of':bound_people['first']})
    observe(bound_people['bot'],monkeypatch,lambda s:lifecycle.prepare(s,boundctx,inp,'post',binding=credential),bound_people['company'])
    with writer(root) as db:db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==bound_people['first']).values(revoked_at=clock.now_iso()))
    def denied(s):
        before=tuple(s.company.raw.iterdump())
        errors=[]
        for attempted in (inp,inp.model_copy(update={'document':inp.document.model_copy(update={'expected_version':d.version+1})})):
            with pytest.raises(BookflowError) as error:lifecycle.prepare(s,boundctx,attempted,'post',binding=credential)
            assert error.value.code in ('E_PERMISSION','E_COMPANY_NOT_FOUND','E_UNAUTHENTICATED')
            assert d.id not in str(error.value.details) and cash['source'] not in str(error.value.details)
            errors.append((error.value.code,error.value.details))
        assert errors[0]==errors[1]
        assert tuple(s.company.raw.iterdump())==before
    observe(bound_people['bot'],monkeypatch,denied,bound_people['company'])


def test_financial_captures_all_kinds_without_refresh_or_late_defaults(client,cash,run_private):
    values={'text':'Saved text','number':'1.000000001','date':'2026-06-03','bool':False,'choice':'Alpha'}
    fields={}
    for kind,value in values.items():
        fields[kind]=client.run('custom-field create',dict(name='Captured '+kind,kind=kind,scopes=['deposit'],default=value,
            **({'choices':[dict(value='Alpha'),dict(value='Beta')]} if kind=='choice' else {})),company=COMPANY)['id']
    bank=client.account.create(name='All captured kinds bank',type='bank',company=COMPANY)['id']
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create'))
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    client.run('custom-field update',dict(custom_field=fields['text'],expected_version=1,name='Current different name',default='Current different default',position=9),company=COMPANY)
    late=client.run('custom-field create',dict(name='Late default',kind='text',scopes=['deposit'],default='Must not appear'),company=COMPANY)['id']
    out=financial(run_private,dict(operation_key='all-kinds-capture',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
    def check(s,ctx):
        raw=json.loads(s.company.raw.execute('SELECT custom_fields_snapshot FROM transaction_revisions WHERE id=?',(out.current.revision_id,)).fetchone()[0])
        assert set(raw)==set(fields.values())
        assert len({v['value_id'] for v in raw.values()})==5
        for kind,key in fields.items():
            assert raw[key]['name']=='Captured '+kind and raw[key]['kind']==kind
            assert raw[key]['value']==values[kind] and type(raw[key]['value']) is type(values[kind])
            assert raw[key]['definition_version']==1 and raw[key]['position']==0
            assert raw[key]['canonical_text']==('false' if kind=='bool' else values[kind])
            slot=s.company.raw.execute('SELECT def_id,record_type,record_id,active,canonical_text FROM custom_field_values WHERE id=?',(raw[key]['value_id'],)).fetchone()
            assert slot==(key,'deposit',out.current.id,1,raw[key]['canonical_text'])
        assert s.company.raw.execute('SELECT count(*) FROM custom_field_values WHERE record_id=? AND def_id=?',(out.current.id,late)).fetchone()[0]==0
        assert raw[fields['choice']]['choice_label']=='Alpha' and raw[fields['choice']]['choice_id']==d.header.custom_fields[fields['choice']].choice_id
        assert drafts.show(s,m.DraftShow(draft=d.id),ctx=ctx).header.custom_fields==d.header.custom_fields
    run_private(check)
    client.run('custom-field deactivate',dict(custom_field=fields['text'],expected_version=2),company=COMPANY)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=out.current.id,expected_version=1),'create'))
    assert not edit.posting_issues
    unchanged=financial(run_private,dict(deposit=out.current.id,expected_version=1,operation_key='inactive-capture-noop',document=dict(mode='draft',draft=edit.id,expected_version=edit.version)),'update')
    assert not unchanged.changed and unchanged.current.version==1 and unchanged.effect.batch_ids==()
    run_private(check)


@pytest.mark.parametrize('field',['name','definition_version','value_id','omission'])
def test_independent_captured_custom_validator_rejects_substituted_facts(client,cash,run_private,field):
    import copy
    from dataclasses import replace
    from bookflow.company import deposit_draft_custom_fields as custom
    from bookflow.core.errors import BookflowError
    key=client.run('custom-field create',dict(name='Independent captured name',kind='text',scopes=['deposit'],default='Original'),company=COMPANY)['id']
    bank=client.account.create(name='Independent custom bank',type='bank',company=COMPANY)['id']
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create'))
    d=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=d.id,expected_version=d.version,set_sources=[m.SourcePatch(**cash)]),'update'))
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        inp=lifecycle.INPUTS['post'].model_validate(dict(operation_key='captured-negative',document=dict(mode='draft',draft=d.id,expected_version=d.version)))
        p=lifecycle.prepare(s,ctx,inp,'post');data=json.loads(p.data_json)
        _,r,manifest,_=drafts.load(s,d.id,ctx=ctx)
        bad=copy.deepcopy(p.custom_plan.snapshot)
        if field=='omission':bad.pop(key)
        else:bad[key][field]={'name':'Substituted current name','definition_version':2,'value_id':cash['source']}[field]
        forged=replace(p.custom_plan,snapshot=bad)
        with pytest.raises(BookflowError) as error:custom.validate(s,forged,data['identity'],{},manifest,r['manifest_hash'],bad)
        assert error.value.code=='E_VALIDATION'
        assert tuple(s.company.raw.iterdump())==before
        custom.validate(s,p.custom_plan,data['identity'],{},manifest,r['manifest_hash'],p.custom_plan.snapshot)
    run_private(check)


def test_coordinate_noeffect_still_consumes_draft_without_financial_rows(client,cash,run_private):
    from bookflow.company import deposit_coordination as coord,deposit_coordinate_persistence as cp
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.publication import OSBinding
    posted,_=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    inp=CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,operation_key='coordinate-noeffect-draft',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=cash['source'],expected_version=2)),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),draft_source_result='retain')))
    def action(s,ctx):
        tables=('transactions','transaction_revisions','posting_batches','posting_lines','deposit_memberships','deposit_current_memberships','custom_field_values','sequences','bank_effect_versions')
        before={name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in tables}
        binding=OSBinding.from_session(s)
        preview=coord.prepare(s,ctx,inp,binding=binding)
        accepted=inp.model_copy(update={'dependency_guard':preview.dependency_guard})
        prepared=coord.prepare(s,ctx,accepted,binding=binding)
        out=cp.execute(s,ctx,prepared)
        assert not out.changed and not out.new_effect and out.current.version==1
        assert out.current_draft.state=='consumed' and out.effect.deposit.consumed_draft.draft_id==edit.id
        assert out.effect.deposit.batch_ids==() and out.effect.deposit.memberships==()
        assert {name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in tables}==before
        raw=tuple(s.company.raw.iterdump())
        replay=cp.recover(s,ctx,inp,binding=binding)
        assert replay.effect==out.effect and replay.idempotent_replay
        assert tuple(s.company.raw.iterdump())==raw
    run_private(action)


def test_coordinate_deferred_consumption_fk_fence_keeps_caller_sentinel(client,cash,run_private,monkeypatch):
    from bookflow.company import deposit_coordination as coord,deposit_coordinate_persistence as cp,deposit_draft_consumption as consumption
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.publication import OSBinding
    from bookflow.core.errors import BookflowError
    posted,_=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    inp=CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,operation_key='coordinate-deferred-link',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=cash['source'],expected_version=2,memo='Actual source change')),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),draft_source_result='retain')))
    def action(s,ctx):
        marker=drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(memo='Caller sentinel')),'create')
        binding=OSBinding.from_session(s)
        preview=coord.prepare(s,ctx,inp,binding=binding)
        accepted=inp.model_copy(update={'dependency_guard':preview.dependency_guard})
        prepared=coord.prepare(s,ctx,accepted,binding=binding)
        before=tuple(s.company.raw.iterdump())
        original=consumption.persist;fence=cp._foreign_keys;observed=[]
        def bad(session,value):
            original(session,dict(value,after=dict(value['after'],consumed_operation_id='00000000000000000000000000')))
        def checked(session):
            operation=session.company.raw.execute('SELECT id FROM deposit_operations WHERE operation_key=?',('coordinate-deferred-link',)).fetchone()
            if operation:
                assert session.company.raw.execute('SELECT count(*) FROM deposit_draft_consumptions WHERE operation_id=?',operation).fetchone()[0]==1
                observed.append(bool(session.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()))
            fence(session)
        with monkeypatch.context() as patch:
            patch.setattr(consumption,'persist',bad);patch.setattr(cp,'_foreign_keys',checked)
            with pytest.raises(BookflowError) as error:cp.execute(s,ctx,prepared)
        assert error.value.code=='E_INTERNAL' and observed==[True]
        assert tuple(s.company.raw.iterdump())==before
        assert drafts.show(s,m.DraftShow(draft=marker.id),ctx=ctx).header.memo=='Caller sentinel'
        with monkeypatch.context() as patch:
            patch.setattr(cp,'_foreign_keys',checked)
            out=cp.execute(s,ctx,prepared)
        assert observed==[True,False] and out.current_draft.state=='consumed'
    run_private(action)


def test_history_owner_index_equals_full_predicate_at_each_real_endpoint(client,cash,run_private):
    from bookflow.company import deposit_dependency_history as history
    posted,_=make_posted(client,cash,run_private)
    voided=financial(run_private,dict(deposit=posted.current.id,expected_version=1,operation_key='index-void'),'void')
    def check(s,ctx):
        cases=[(None,False,2),(posted.effect.audit_event_id,True,1),(voided.effect.audit_event_id,True,2)]
        for endpoint,historical,claim_rows in cases:
            indexed=history.History(s,endpoint,historical=historical)
            scanned=history.History(s,endpoint,historical=historical)
            for kind,field in (('transaction_revision','transaction_id'),('posting_batch','transaction_id'),
                               ('posting_line','transaction_id'),('deposit_membership','transaction_id')):
                for owners in ((posted.current.id,),(cash['source'],),('00000000000000000000000000',),(cash['source'],posted.current.id)):
                    expected=scanned.find(kind,lambda row:row[field] in owners)
                    actual=indexed.find_owners(kind,field,owners)
                    assert actual==expected
            membership=indexed.find_owners('deposit_membership','source_transaction_id',(cash['source'],))
            assert len(membership)==claim_rows
            assert {v['kind'] for v in membership}==({'claim'} if claim_rows==1 else {'claim','release'})
            assert indexed.unknown==scanned.unknown==set()
    run_private(check)


def test_history_commercial_repeated_revision_keeps_owner_sequence_and_value_proof(client,cash,run_private):
    from bookflow.company import deposit_dependency_history as history
    posted,_=make_posted(client,cash,run_private)
    def check(s,ctx):
        reader=history.History(s)
        from bookflow.company import schema as c
        import sqlalchemy as sa
        header=dict(s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id==posted.current.id)).mappings().one())
        seq=s.company.raw.execute('SELECT seq FROM audit_events WHERE id=?',(posted.effect.audit_event_id,)).fetchone()[0]
        expected=history.History(s).commercial(header,seq)
        first=reader.commercial(header,seq)
        assert first==expected
        first['memo']='Caller mutation must not alter retained immutable facts'
        assert reader.commercial(header,seq)==expected
        assert reader.commercial(header,seq+1)==expected
        with pytest.raises(history.MissingHistory):reader.commercial(header,seq-1)
        wrong=dict(header,id=cash['source'])
        with pytest.raises(history.MissingHistory):reader.commercial(wrong,seq)
        assert reader.commercial(header,seq)==history.History(s).commercial(header,seq)
    run_private(check)


def test_guarded_draft_seals_exact_complete_capture_without_intervening_writes(client,cash,run_private,monkeypatch):
    from bookflow.company import deposit_dependency_history as history
    posted,_=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    inp=lifecycle.INPUTS['update'].model_validate(dict(deposit=posted.current.id,expected_version=1,operation_key='same-snapshot-proof',document=dict(mode='draft',draft=edit.id,expected_version=edit.version)))
    def check(s,ctx):
        preview=lifecycle.prepare(s,ctx,inp,'update')
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        original=history.recipe_for_readset;seen=[]
        def prove(session,request,readset,binding):
            with monkeypatch.context() as patch:
                patch.setattr(history,'recipe_for_readset',original)
                expected_recipe,expected=history.capture(session,request,binding)
            assert readset==expected
            assert len(readset.records)>1 and cash['source'] in readset.transactions and posted.current.id in readset.transactions
            actual=original(session,request,readset,binding)
            assert actual==expected_recipe
            seen.append(actual)
            return actual
        with monkeypatch.context() as patch:
            patch.setattr(history,'recipe_for_readset',prove)
            result=lifecycle.prepare(s,ctx,inp.model_copy(update={'dependency_guard':preview.dependency_guard}),'update')
        assert len(seen)==1 and result.facts_fingerprint==preview.facts_fingerprint
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    run_private(check)


@pytest.mark.parametrize('fault',['omitted_row','extra_row','ordinal','source_owner','source_amount','hash','revision','row_identity','occurrence'])
def test_independent_draft_financial_manifest_rejects_complete_graph_substitution(client,cash,run_private,fault):
    from bookflow.company import deposit_draft_provider as provider
    from bookflow.company.deposit_models import Effect
    from bookflow.core.errors import BookflowError
    bank=client.account.create(name='Independent manifest bank',type='bank',company=COMPANY)['id']
    def check(s,ctx):
        draft=drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create')
        draft=drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_sources=[m.SourcePatch(**cash)]),'update')
        inp=lifecycle.INPUTS['post'].model_validate(dict(operation_key='independent-manifest',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
        prepared=lifecycle.prepare(s,ctx,inp,'post');data=json.loads(prepared.data_json)
        effect=Effect.model_validate_json(json.dumps(data['financial']))
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        provider.validate_financial(s,ctx,data,effect,prepared.binding,custom_plan=prepared.custom_plan)
        rows=list(effect.intent.sources);row=rows[0]
        if fault=='omitted_row':rows=[]
        elif fault=='extra_row':rows.append(row)
        elif fault=='ordinal':rows[0]=row.model_copy(update={'ordinal':row.ordinal+1})
        elif fault=='source_owner':rows[0]=row.model_copy(update={'source':row.source.model_copy(update={'business_batch_id':'00000000000000000000000000'})})
        elif fault=='source_amount':rows[0]=row.model_copy(update={'source':row.source.model_copy(update={'cash_minor_units':row.source.cash_minor_units+1})})
        elif fault=='occurrence':rows[0]=row.model_copy(update={'occurrences':(row.occurrences[0].model_copy(update={'ordinal':row.occurrences[0].ordinal+1}),*row.occurrences[1:])})
        elif fault=='row_identity':rows[0]=row.model_copy(update={'row_id':'00000000000000000000000000'})
        elif fault=='hash':data['draft']['manifest_hash']='0'*64
        elif fault=='revision':data['draft']['revision_id']='00000000000000000000000000'
        # Keep every emitted posting leg unchanged and balanced. A sum-only
        # validator would accept all these substituted source/identity facts.
        corrupted=effect.model_copy(update={'intent':effect.intent.model_copy(update={'sources':tuple(rows)})})
        assert sum(v.signed_debit for v in corrupted.legs)==0
        with pytest.raises(BookflowError) as error:provider.validate_financial(s,ctx,data,corrupted,prepared.binding,custom_plan=prepared.custom_plan)
        assert error.value.code=='E_VALIDATION'
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    run_private(check)


def test_financial_draft_preserves_captured_reference_labels_with_current_eligibility(client,run_private):
    bank=client.account.create(name='Captured bank label',type='bank',company=COMPANY)['id']
    income=client.account.create(name='Captured income label',type='income',company=COMPANY)['id']
    party=client.customer.create(name='Captured party label',company=COMPANY)['id']
    method=client.run('payment-method create',dict(name='Captured method label',kind='cash'),company=COMPANY)['id']
    cls=client.run('class create',dict(name='Captured class label'),company=COMPANY)['id']
    draft=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create'))
    draft=run_private(lambda s,ctx:drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_additional=[m.AdditionalPatch.model_validate(dict(received_from=dict(kind='customer',id=party),from_account=income,amount='12.35',payment_method=method,**{'class':cls}))]),'update'))
    for identity,name in ((bank,'Current bank label'),(income,'Current income label')):
        client.account.update(account=identity,expected_version=1,name=name,company=COMPANY)
    client.customer.update(customer=party,expected_version=1,name='Current party label',company=COMPANY)
    client.run('class update',dict(**{'class':cls},expected_version=1,name='Current class label'),company=COMPANY)
    client.run('payment-method update',dict(payment_method=method,expected_version=1,name='Current method label'),company=COMPANY)
    out=financial(run_private,dict(operation_key='captured-reference-labels',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
    actual=out.effect.financial.intent
    assert actual.bank.name==actual.bank.full_name=='Captured bank label'
    assert actual.additional[0].account.name==actual.additional[0].account.full_name=='Captured income label'
    assert actual.additional[0].dimensions.party_name=='Captured party label'
    assert actual.additional[0].dimensions.class_name=='Captured class label'
    assert actual.additional[0].payment_method.label=='Captured method label' and actual.additional[0].payment_method.version==1
    assert out.current.revision_bank_total==1235


def test_consumed_operation_reader_does_not_require_posting_but_recovery_does(root,client,cash,run_private,bound_people,monkeypatch):
    from tests.test_deposit_dependency_binding import observe
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as h
    from bookflow.company import deposit_operation_pages as pages, deposit_operations as ops
    from bookflow.core.publication import OSBinding
    from bookflow.core.context import Context,Interface
    from bookflow.core.errors import BookflowError
    posted,draft=make_posted(client,cash,run_private)
    with writer(root) as db:
        db.conn.execute(h.memberships.update().where(h.memberships.c.user_id==bound_people['second']).values(role='readonly'))
    def check(s):
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        binding=OSBinding.from_session(s)
        out=pages.authorized_output(s,ops.find(s,'first-post'),binding)
        assert out.effect==posted.effect and out.current_draft.state=='consumed'
        ctx=Context.new(Interface.python,'Readonly recovery',reason='Owned financial witness')
        inp=lifecycle.INPUTS['post'].model_validate(dict(operation_key='first-post',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
        with pytest.raises(BookflowError) as error:lifecycle.prepare(s,ctx,inp,'post',binding=binding)
        assert error.value.code=='E_PERMISSION'
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    observe(bound_people['two'],monkeypatch,check,bound_people['company'])


def test_inline_caps_remain_bounded_and_resolved_documents_are_not_caller_inputs(client,cash,run_private):
    from bookflow.company import deposit_draft_provider as provider
    from bookflow.company.deposit_models import InlineDocument,DraftDocument
    from bookflow.core.publication import OSBinding
    from bookflow.core.errors import BookflowError
    from pydantic import ValidationError
    bank=client.account.create(name='Bounded inline bank',type='bank',company=COMPANY)['id']
    sources=[dict(source_type='payment',source=str(index).zfill(26),expected_version=1) for index in range(1,202)]
    with pytest.raises(ValidationError):InlineDocument.model_validate(dict(mode='inline',date='2026-06-03',deposit_to=bank,sources=sources))
    def check(s,ctx):
        draft=drafts.run(s,ctx,m.DraftCreate(header=m.HeaderPatch(date='2026-06-03',deposit_to=bank)),'create')
        draft=drafts.run(s,ctx,m.DraftUpdate(draft=draft.id,expected_version=draft.version,set_sources=[m.SourcePatch(**cash)]),'update')
        bound=OSBinding.from_session(s)
        resolved=provider.load(s,ctx,DraftDocument(mode='draft',draft=draft.id,expected_version=draft.version),bound)
        with pytest.raises(BookflowError) as error:provider.load(s,ctx,resolved,bound)
        assert error.value.code=='E_VALIDATION'
    run_private(check)
