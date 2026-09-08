"""Real immutable ordinary deposit receipts, before whole-event integration."""
import copy
import json
import pytest
from pydantic import ValidationError
from bookflow.hub import audit_projection_legacy as views
from tests.test_deposit_draft_financial import run_private,make_posted,financial
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale,COMPANY

@pytest.fixture
def captured_operation(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        before=tuple(s.company.raw.iterdump())
        cursor=s.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?',(posted.operation_id,))
        value=dict(zip((v[0] for v in cursor.description),cursor.fetchone(),strict=True))
        assert tuple(s.company.raw.iterdump())==before
        return value
    return run_private(read)


@pytest.fixture
def captured(captured_operation):
    return json.loads(captured_operation['effect_snapshot'])

def test_actual_consumed_receipt_preserves_composition(captured):
    value=views.DepositAuditLifecycleOutput.model_validate(captured)
    output=value.model_dump(mode='json',by_alias=True)
    expected=copy.deepcopy(captured)
    expected.pop('facts_fingerprint');expected.pop('dependency_guard')
    expected['current_draft'].pop('manifest_hash')
    consumed=expected['effect']['consumed_draft'];consumed.pop('manifest_hash')
    consumed['snapshot']=json.loads(consumed['snapshot']);consumed['snapshot'].pop('high_water')
    # Existing typed sales provenance wraps the original map under values.
    # Assert that explicit shape while preserving every captured origin/value.
    for composition in (expected['effect']['financial']['intent'],consumed['snapshot']):
        for row in composition['sources']:
            source=row['source']
            source['profile']['origins']={'values':[dict(field=k,origin=v) for k,v in sorted(source['profile']['origins'].items())]}
            for component in source['components']:
                if component['sale_line'] is not None:
                    line=component['sale_line'];line['origins']={'values':[dict(field=k,origin=v) for k,v in sorted(line['origins'].items())]}
    assert output==expected
    assert output['effect']['consumed_draft']['snapshot']['summary']['source_total']==6000
    assert value.effect.consumed_draft.rows[0].draft_row_id==value.effect.consumed_draft.snapshot.sources[0].row_id


@pytest.mark.parametrize('defect',['unknown_nested','null_version','invalid_units'])
def test_actual_receipt_rejects_invalid_capture(captured,defect):
    value=copy.deepcopy(captured)
    if defect=='unknown_nested':
        manifest=json.loads(value['effect']['consumed_draft']['snapshot'])
        manifest['sources'][0]['invented']=True
        value['effect']['consumed_draft']['snapshot']=json.dumps(manifest)
    elif defect=='null_version':value['effect']['consumed_draft']['version']=None
    else:value['effect']['memberships'][0]['amount_minor_units']=-1
    with pytest.raises(ValidationError):views.DepositAuditLifecycleOutput.model_validate(value)


def test_actual_inline_post_and_void_keep_absent_draft(client,cash,run_private):
    bank=client.account.create(name='Inline audit bank',type='bank',company=COMPANY)['id']
    posted=financial(run_private,dict(operation_key='audit-inline',document=dict(mode='inline',deposit_to=bank,date='2026-06-03',sources=[cash])))
    voided=financial(run_private,dict(operation_key='audit-inline-void',deposit=posted.current.id,expected_version=posted.current.version),'void')
    def check(s,ctx):
        before=tuple(s.company.raw.iterdump())
        for result in (posted,voided):
            raw=json.loads(s.company.raw.execute('SELECT effect_snapshot FROM deposit_operations WHERE id=?',(result.operation_id,)).fetchone()[0])
            decoded=views.DepositAuditLifecycleOutput.model_validate(raw)
            output=decoded.model_dump(mode='json',by_alias=True)
            assert 'current_draft' not in output and 'consumed_draft' not in output['effect']
            assert output['current']==raw['current']
            assert output['effect']['action']==raw['effect']['action']
        assert decoded.effect.reversal is not None
        assert decoded.current.status=='voided' and decoded.current.effective_bank_total==0
        assert tuple(s.company.raw.iterdump())==before
    run_private(check)


def test_actual_operation_original_request_and_receipt_agree(captured_operation):
    value=views.DepositOperationView.model_validate(captured_operation)
    raw=json.loads(captured_operation['request_snapshot'])
    assert value.request_snapshot.input.document.draft==value.effect_snapshot.current_draft.id
    assert value.request_snapshot.resolved_draft.snapshot==value.effect_snapshot.effect.consumed_draft.snapshot
    assert value.request_snapshot.provided_fields==tuple(raw['provided_fields'])
    assert value.request_snapshot.input.document.model_dump(mode='json')==raw['input']['document']
    output=value.model_dump(mode='json',by_alias=True)
    assert 'request_hash' not in output
    assert 'resolved_draft' not in output['request_snapshot']
    assert 'resolved_identity_map' not in output['request_snapshot']


@pytest.mark.parametrize('defect',['command','operation','document','event','draft','presence','original_input','draft_target','bank','source_map'])
def test_actual_operation_rejects_crossed_receipt(captured_operation,defect):
    raw=copy.deepcopy(captured_operation)
    request=json.loads(raw['request_snapshot']);effect=json.loads(raw['effect_snapshot'])
    if defect=='command':raw['command']='deposit update'
    elif defect=='operation':raw['operation_key']='unrelated-operation'
    elif defect=='document':raw['transaction_id']=raw['id']
    elif defect=='event':raw['audit_event_id']=raw['id']
    elif defect=='draft':request['resolved_draft']['version']+=1
    elif defect=='presence':request['provided_fields']=[]
    elif defect=='original_input':request['input']['document']['expected_version']=0
    elif defect=='draft_target':request['input']['document']['draft']=raw['id']
    elif defect=='bank':request['resolved_identity_map']['bank']=raw['id']
    else:request['resolved_identity_map']['sources'][0]['row']=raw['id']
    raw.update(request_snapshot=request,effect_snapshot=effect)
    with pytest.raises(ValidationError):views.DepositOperationView.model_validate(raw)
