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
def captured(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        before=tuple(s.company.raw.iterdump())
        row=s.company.raw.execute('SELECT effect_snapshot FROM deposit_operations WHERE id=?',(posted.operation_id,)).fetchone()
        value=json.loads(row[0])
        assert tuple(s.company.raw.iterdump())==before
        return value
    return run_private(read)


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
