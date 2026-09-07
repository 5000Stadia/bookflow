"""Closed adapters over real producer snapshots; no schema-selected test oracle."""
import json
import sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.core.errors import BookflowError
from bookflow.hub.audit_projection_legacy import decode_company_snapshot
from tests.test_row8_journal import database_path


INTERNAL={'path','name_key','full_name_key','number_key','code_key','initials_key',
          'label_key','value_key','definition_name_key','provider_profile_ref','tax_id'}


def test_actual_chart_and_profile_histories_preserve_all_authorized_fields(client):
    path=database_path(client)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        before='\n'.join(db.iterdump())
        rows=db.execute('SELECT e.command,a.record_type,a.action,a.after FROM audit_entries a JOIN audit_events e ON e.id=a.event_id WHERE e.command IN (?,?)',('chart apply','profile apply')).fetchall()
        assert rows
        seen=set()
        for command,kind,action,blob in rows:
            raw=decode_snapshot(blob)
            view=decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
            actual=view.model_dump(mode='json',by_alias=True)
            assert actual.pop('tag')==kind
            assert actual=={key:value for key,value in raw.items() if key not in INTERNAL}
            seen.add(kind)
        assert 'account' in seen and 'payment_method' in seen and 'term' in seen
        assert '\n'.join(db.iterdump())==before


def test_entitled_unknown_field_and_wrong_scalar_rejected(client):
    with sqlite3.connect(database_path(client).as_uri()+'?mode=ro',uri=True) as db:
        command,kind,action,blob=db.execute("SELECT e.command,a.record_type,a.action,a.after FROM audit_entries a JOIN audit_events e ON e.id=a.event_id WHERE e.command='chart apply' AND a.record_type='account' LIMIT 1").fetchone()
    raw=decode_snapshot(blob)
    for altered in (dict(raw,future_secret='not a declared field'),dict(raw,version=True)):
        with pytest.raises(BookflowError) as caught:
            decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=altered)
        assert caught.value.code=='E_VALIDATION'
        assert caught.value.details=={'reason':'audit_format'}


@pytest.mark.parametrize('amount,currency,minor',[('0.20','USD',20),('200','JPY',200),('-0.001','KWD',-1)])
def test_captured_money_independent_exact_examples(amount,currency,minor):
    from bookflow.hub.audit_projection_legacy import CapturedMoney
    raw=dict(amount=amount,currency=currency,minor_units=minor)
    assert CapturedMoney.model_validate_json(json.dumps(raw)).model_dump()==raw
    for bad in (dict(raw,minor_units=minor+1),dict(raw,minor_units=True),
                dict(raw,amount=0.2),dict(raw,currency='ZZZ'),dict(raw,minor_units=2**63)):
        with pytest.raises(ValueError):CapturedMoney.model_validate_json(json.dumps(bad))


def test_snapshot_codec_accepts_only_owned_prefixes_and_object_payloads():
    from bookflow.hub.audit_projection import _decode
    from bookflow.core.audit import encode_snapshot
    for value in ({'version':1},{'body':'long '*80}):
        encoded=encode_snapshot(value)
        assert _decode(encoded)==value
        for bad in (b'\x02'+encoded[1:],encoded[1:],b'\x00[]',b'\x01invalid',b'\x00{"x":NaN}'):
            with pytest.raises(BookflowError) as caught:_decode(bad)
            assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}


def test_closed_history_variants_and_empty_activity_shape():
    from bookflow.hub.audit_projection import HistorySelection,ProjectedHistory
    from bookflow.core.publication_audit import document
    valid=HistorySelection(mode='activity',company='C',record_type='customer',record_id='R',kinds=(),limit=200)
    assert valid.kinds==()
    expected=dict(events=[],next_anchor=None,endpoint=None,has_more=False,scanned_count=0,
        scanned_more=False,items=[],count=0,next_entry_anchor=None)
    assert document(ProjectedHistory((),None,None,False,0,False,(),activity=True))==expected
    for fields in (
        dict(mode='activity',company='C',record_type='customer',record_id='R',limit=201),
        dict(mode='activity',company='C',record_type='customer',record_id='R',anchor='E'),
        dict(mode='activity',company='C',record_type='customer',record_id='R',kinds=('note','note')),
        dict(mode='list',entry_anchor='X'),dict(mode='show',event='E',limit=1),
        dict(mode='tail',empty_start=True,anchor='E'),dict(mode='list',limit=True)):
        with pytest.raises(ValueError):HistorySelection(**fields)


def test_unknown_reference_owner_has_no_empty_requirement_fallback():
    from bookflow.hub.audit_projection_legacy import entry_requirement
    with pytest.raises(BookflowError) as caught:entry_requirement('unowned_future_record')
    assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    assert entry_requirement('customer_contact_point')==( ('customer','member'), )
    assert entry_requirement('payment_selection_recovery_active')==( ('ledger.read','member'), )
