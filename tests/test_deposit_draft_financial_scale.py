"""One retained authentic403 world, sequential real financial action receipts."""
import json
import os
import shutil
from pathlib import Path
import pytest
import bookflow
from bookflow.company import deposit_drafts as d,deposit_draft_models as m,deposit_lifecycle as life,deposit_persistence as persist
from bookflow.company import deposit_coordination as coord,deposit_coordinate_persistence as cp
from bookflow.company.deposit_coordinate_models import CoordinateInput,CoordinateOutput
from bookflow.core.publication import OSBinding
from tests.test_deposit_draft_financial import run_private
from tests.test_service_sales_lifecycle import COMPANY

ORIGINAL=Path('/tmp/bookflow-deposit-g3-durable-current/.cache/g3-durable/tmp/final-source/g3-403-world0/root')

@pytest.fixture(scope='module')
def world():
    target=Path(os.environ['BOOKFLOW_G3_FINANCIAL_WORLD'])
    if not target.exists():shutil.copytree(ORIGINAL,target)
    receipt=json.loads((ORIGINAL.parent/'403-receipt.json').read_text())
    value=dict(root=target,receipt=receipt)
    ready=target.parent/'ready.json'
    if ready.exists():
        value['ready']=m.DraftOutput.model_validate_json(ready.read_text())
        draft=value['ready']
        value['post_input']=life.INPUTS['post'].model_validate(dict(operation_key='G3-real403-post',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))
    from bookflow.company.deposit_lifecycle_models import LifecycleOutput
    for key,model in (('posted',LifecycleOutput),('edit',m.DraftOutput),('coordinated',CoordinateOutput),('ordinary_edit',m.DraftOutput),('updated',LifecycleOutput),('voided',LifecycleOutput),('copied',m.DraftOutput)):
        path=target.parent/(key+'.json')
        if path.exists():value[key]=model.model_validate_json(path.read_text())
    if 'edit' in value:
        payment=next(v for v in receipt['source_ids'] if v['source_type']=='payment')
        value['coordinate_input']=CoordinateInput.model_validate(dict(deposit=value['posted'].current.id,expected_version=1,operation_key='G3-real403-coordinate',
            source_action=dict(kind='payment_update',input=dict(payment=payment['source'],expected_version=2,amount='2')),
            replacement=dict(mode='document',document=dict(mode='draft',draft=value['edit'].id,expected_version=value['edit'].version),draft_source_result='retain')))
    return value

@pytest.fixture
def client(world,monkeypatch):
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(world['root']))
    return bookflow.connect(data_root=str(world['root']))


def save(world,key,value):
    world[key]=value
    if hasattr(value,'model_dump_json'):(world['root'].parent/(key+'.json')).write_text(value.model_dump_json())


def test_01_ready_full_draft(world,client,run_private):
    bank=client.account.create(name='G3 actual403 bank',type='bank',company=COMPANY)['id']
    prior=world['receipt']['all_matching']['draft']
    draft=run_private(lambda s,ctx:d.run(s,ctx,m.DraftUpdate(draft=prior['id'],expected_version=prior['version'],header=m.HeaderPatch(deposit_to=bank)),'update'))
    assert draft.summary.source_count==403 and draft.summary.bank_total==40300 and not draft.posting_issues
    save(world,'ready',draft)
    world['post_input']=life.INPUTS['post'].model_validate(dict(operation_key='G3-real403-post',document=dict(mode='draft',draft=draft.id,expected_version=draft.version)))


def test_02_preview_full_post(world,run_private):
    def action(s,ctx):
        before=tuple(s.company.raw.iterdump())
        result=life.prepare(s,ctx,world['post_input'],'post')
        assert tuple(s.company.raw.iterdump())==before
        data=json.loads(result.data_json)
        assert len(data['financial']['intent']['sources'])==403
        assert data['financial']['bank_total']==40300
        return result
    world['prepared_post']=run_private(action)


def test_03_commit_full_post(world,run_private):
    out=run_private(lambda s,ctx:persist.execute(s,ctx,world['prepared_post']))
    assert len(out.effect.memberships)==403 and out.current.revision_bank_total==40300
    assert len(out.effect.consumed_draft.rows)==403
    assert {v.source.transaction_id for v in out.effect.financial.intent.sources}=={v['source'] for v in world['receipt']['source_ids']}
    save(world,'posted',out)


def test_04_full_post_raw_and_recovery(world,run_private):
    posted=world['posted']
    def action(s,ctx):
        before=tuple(s.company.raw.iterdump())
        recovered=life.prepare(s,ctx,world['post_input'],'post')
        assert recovered.idempotent_replay and recovered.effect==posted.effect and recovered.current_draft.state=='consumed'
        assert tuple(s.company.raw.iterdump())==before
        actual=s.company.raw.execute('SELECT source_transaction_id,amount_minor_units FROM deposit_memberships WHERE transaction_id=? AND kind=?',(posted.current.id,'claim')).fetchall()
        assert len(actual)==403 and dict(actual)=={v['source']:100 for v in world['receipt']['source_ids']}
        total=s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?',(posted.current.id,posted.effect.financial.intent.bank.id)).fetchone()[0]
        assert total==40300
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
    run_private(action)


def test_05_edit_full_draft(world,run_private):
    draft=run_private(lambda s,ctx:d.run(s,ctx,m.DraftCreate(from_deposit=world['posted'].current.id,expected_version=1),'create'))
    assert draft.summary.source_count==403
    save(world,'edit',draft)
    payment=next(v for v in world['receipt']['source_ids'] if v['source_type']=='payment')
    world['coordinate_input']=CoordinateInput.model_validate(dict(deposit=world['posted'].current.id,expected_version=1,operation_key='G3-real403-coordinate',
        source_action=dict(kind='payment_update',input=dict(payment=payment['source'],expected_version=2,amount='2')),
        replacement=dict(mode='document',document=dict(mode='draft',draft=draft.id,expected_version=draft.version),draft_source_result='retain')))


def test_06_preview_full_coordinate(world,run_private):
    world['coordinate_preview']=run_private(lambda s,ctx:coord.prepare(s,ctx,world['coordinate_input'],binding=OSBinding.from_session(s)))


def test_07_guarded_full_coordinate(world,run_private):
    world['coordinate_input']=world['coordinate_input'].model_copy(update={'dependency_guard':world['coordinate_preview'].dependency_guard})
    world['coordinate_prepared']=run_private(lambda s,ctx:coord.prepare(s,ctx,world['coordinate_input'],binding=OSBinding.from_session(s)))


def test_08_commit_full_coordinate(world,run_private):
    out=run_private(lambda s,ctx:cp.execute(s,ctx,world['coordinate_prepared']))
    assert out.current.revision_bank_total==40400 and len(out.current.active_source_ids)==403
    assert out.current_draft.id==world['edit'].id
    assert len(out.effect.deposit.memberships)==806
    save(world,'coordinated',out)


def test_09_full_coordinate_recovery_and_raw_oracle(world,run_private):
    out=world['coordinated']
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        recovered=cp.recover(s,ctx,world['coordinate_input'],binding=OSBinding.from_session(s))
        assert recovered.idempotent_replay and recovered.effect==out.effect and recovered.current==out.current
        assert recovered.current_draft.id==world['edit'].id and recovered.current_draft.state=='consumed'
        assert tuple(s.company.raw.iterdump())==before
        rows=s.company.raw.execute('SELECT source_transaction_id,amount_minor_units FROM deposit_memberships WHERE transaction_id=? AND kind=?',(out.current.id,'claim')).fetchall()
        assert len(rows)==806
        current=s.company.raw.execute('SELECT m.source_transaction_id,m.amount_minor_units FROM deposit_current_memberships p JOIN deposit_memberships m ON m.id=p.membership_id WHERE p.transaction_id=?',(out.current.id,)).fetchall()
        payment=next(v['source'] for v in world['receipt']['source_ids'] if v['source_type']=='payment')
        assert dict(current)=={v['source']:200 if v['source']==payment else 100 for v in world['receipt']['source_ids']}
        gl=s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?',(out.current.id,out.effect.deposit.financial.intent.bank.id)).fetchone()[0]
        assert gl==40400 and sum(dict(current).values())==40400
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
    run_private(check)


def test_10_ordinary_edit_full_manifest(world,run_private):
    edit=run_private(lambda s,ctx:d.run(s,ctx,m.DraftCreate(from_deposit=world['coordinated'].current.id,expected_version=2),'create'))
    assert edit.summary.source_count==403
    save(world,'ordinary_edit',edit)


def test_11_change_full_draft_header(world,run_private):
    prior=world['ordinary_edit']
    edit=run_private(lambda s,ctx:d.run(s,ctx,m.DraftUpdate(draft=prior.id,expected_version=prior.version,header=m.HeaderPatch(memo='Full403 ordinary replacement')),'update'))
    assert edit.summary.source_count==403
    save(world,'ordinary_edit',edit)


def test_12_preview_full_ordinary_update(world,run_private):
    edit=world['ordinary_edit']
    world['update_input']=life.INPUTS['update'].model_validate(dict(deposit=world['coordinated'].current.id,expected_version=2,operation_key='G3-real403-update',document=dict(mode='draft',draft=edit.id,expected_version=edit.version)))
    world['update_preview']=run_private(lambda s,ctx:life.prepare(s,ctx,world['update_input'],'update'))


def test_13_commit_full_ordinary_update(world,run_private):
    inp=world['update_input'].model_copy(update={'dependency_guard':world['update_preview'].dependency_guard})
    # Preserve the reviewed complete preview; execute revalidates this same guard.
    from dataclasses import replace
    prepared=replace(world['update_preview'],input_json=inp.model_dump_json(by_alias=True,exclude_unset=True))
    out=run_private(lambda s,ctx:persist.execute(s,ctx,prepared))
    assert out.current.version==3 and out.current.revision_bank_total==40400
    assert len(out.effect.memberships)==806 and len(out.effect.consumed_draft.rows)==403
    save(world,'updated',out)


def test_14_preview_full_void(world,run_private):
    world['void_input']=life.INPUTS['void'].model_validate(dict(deposit=world['updated'].current.id,expected_version=3,operation_key='G3-real403-void'))
    world['void_preview']=run_private(lambda s,ctx:life.prepare(s,ctx,world['void_input'],'void'))


def test_15_commit_full_void(world,run_private):
    from dataclasses import replace
    inp=world['void_input'].model_copy(update={'dependency_guard':world['void_preview'].dependency_guard})
    prepared=replace(world['void_preview'],input_json=inp.model_dump_json(by_alias=True,exclude_unset=True))
    out=run_private(lambda s,ctx:persist.execute(s,ctx,prepared))
    assert out.current.status=='voided' and out.current.effective_bank_total==0 and len(out.effect.memberships)==403
    save(world,'voided',out)


def test_16_copy_authentic_voided403_preserves_all_original_bytes(world,run_private):
    def copy(s,ctx):
        before={name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in
            ('transactions','transaction_revisions','posting_batches','posting_lines','deposit_profiles','deposit_memberships','deposit_current_memberships','deposit_operations','deposit_operation_items','deposit_draft_consumptions')}
        old={name:tuple(s.company.raw.execute('SELECT * FROM '+name)) for name in ('deposit_drafts','deposit_draft_revisions','deposit_draft_row_keys','deposit_draft_sources','deposit_draft_additional')}
        copied=d.run(s,ctx,m.DraftCreate(copy_from_voided=world['voided'].current.id,expected_version=4),'create')
        assert copied.summary.source_count==403 and copied.summary.bank_total==40400
        assert copied.copy_transaction_id==world['voided'].current.id and copied.edit_transaction_id is None
        assert copied.id not in (world['ready'].id,world['edit'].id,world['ordinary_edit'].id)
        assert copied.stale_source_ids==() and copied.header.number is None
        for name,rows in before.items():assert tuple(s.company.raw.execute('SELECT * FROM '+name))==rows
        for name,rows in old.items():
            actual=set(s.company.raw.execute('SELECT * FROM '+name))
            assert set(rows)<=actual
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships WHERE transaction_id=?',(world['voided'].current.id,)).fetchone()[0]==0
        for original in (world['ready'],world['edit'],world['ordinary_edit']):
            row=s.company.raw.execute('SELECT state,current_revision_id FROM deposit_drafts WHERE id=?',(original.id,)).fetchone()
            assert row==('consumed',original.revision_id)
        return copied
    save(world,'copied',run_private(copy))


@pytest.mark.parametrize('receipt_key',['posted','coordinated'])
def test_17_complete_original_operation_source_pages_after_void(world,run_private,receipt_key):
    from bookflow.company import deposit_operation_pages as pages
    from bookflow.company.deposit_dependency_models import PageInput
    original=world[receipt_key]
    expected=original.effect.deposit.financial.intent.sources if receipt_key=='coordinated' else original.effect.financial.intent.sources
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        actual=[];cursor=None;seen=set();counts=[];digests=set()
        while True:
            page=pages.items(s,original.operation_key,'request_sources',PageInput(limit=200,cursor=cursor),OSBinding.from_session(s))
            assert page.total_count==403
            actual.extend(page.items);counts.append(len(page.items));digests.add(page.digest)
            cursor=page.next_cursor
            if cursor is None:break
            assert cursor not in seen
            seen.add(cursor)
        assert counts==[200,200,3] and len(digests)==1
        assert tuple(actual)==expected and len({v.source.transaction_id for v in actual})==403
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    run_private(check)


def test_18_original_recoveries_after_full_void_have_zero_raw_mutation(world,run_private):
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        for key in ('posted','coordinated'):
            if key=='posted':out=life.prepare(s,ctx,world['post_input'],'post')
            else:out=cp.recover(s,ctx,world['coordinate_input'],OSBinding.from_session(s))
            assert out.idempotent_replay and out.effect==world[key].effect
            assert out.current.status=='voided' and out.current.version==4
            assert out.current.effective_bank_total==0 and not out.current.active_source_ids
            assert out.current_draft.state=='consumed'
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==before
    run_private(check)


def test_19_copy_reports_actual_source_change_without_refresh_or_financial_effect(world,client,run_private):
    source=next(v['source'] for v in world['receipt']['source_ids'] if v['source_type']=='payment')
    def before(s,ctx):
        version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(source,)).fetchone()[0]
        snapshot=s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(world['copied'].revision_id,)).fetchone()[0]
        return version,snapshot
    version,snapshot=run_private(before)
    client.run('payment update',dict(payment=source,expected_version=version,amount='3',operation_key='G3-copy-source-changed'),company=COMPANY,reason='Owned copied source availability witness')
    (world['root'].parent/'copy-source-change.json').write_text(json.dumps(dict(source=source,before_version=version,snapshot=snapshot)))


def test_20_copy_unavailable_full_manifest_and_failed_post_preserve_raw_state(world,run_private):
    source=next(v['source'] for v in world['receipt']['source_ids'] if v['source_type']=='payment')
    def check(s,ctx):
        old=tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump())
        snapshot=s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(world['copied'].revision_id,)).fetchone()[0]
        from bookflow.company import payment_queries as q
        assert q.digest(json.loads(snapshot))==world['copied'].manifest_hash
        captured=json.loads(snapshot)['sources']
        assert len(captured)==403
        assert {v['source']['transaction_id']:v['source']['cash_minor_units'] for v in captured}=={v['source']:200 if v['source']==source else 100 for v in world['receipt']['source_ids']}
        actual=d.show(s,m.DraftShow(draft=world['copied'].id),ctx=ctx)
        assert actual.summary.source_count==403 and actual.summary.bank_total==40400
        assert actual.stale_source_ids==(source,)
        assert s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(actual.revision_id,)).fetchone()[0]==snapshot
        from bookflow.core.errors import BookflowError
        inp=life.INPUTS['post'].model_validate(dict(operation_key='G3-copy-unavailable-post',document=dict(mode='draft',draft=actual.id,expected_version=actual.version)))
        with pytest.raises(BookflowError) as error:life.prepare(s,ctx,inp,'post')
        assert error.value.code in ('E_PREVIEW_STALE','E_VERSION_CONFLICT')
        assert (tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))==old
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships WHERE transaction_id=?',(world['voided'].current.id,)).fetchone()[0]==0
    run_private(check)
