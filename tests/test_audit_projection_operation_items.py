"""Closed operation-item adapters against real permanent payment receipts.

Complete event publication additionally requires the pending operation-header adapter.
"""
from pathlib import Path
import copy,json,sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.core.errors import BookflowError
from bookflow.hub.audit_projection_legacy import decode_company_snapshot
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def receipt_items(world):
    from tests.test_service_sales_lifecycle import sale
    from tests.test_payment_receipts import method,posted
    client=world['client'];company='Demo Plumbing Co';sales=sale.__wrapped__(client)
    invoice=posted(client,sales['customer'],sales['item'],'1','Operation item invoice')
    payment=client.run('payment receive',dict(customer=sales['customer'],date='2026-06-02',
        amount='1.50',payment_method=method(client),operation_key='projection-items',
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=company)
    path=Path(client.company.show(company=company)['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT after FROM audit_entries WHERE record_type='payment_operation_item' AND event_id=?",(payment['effect']['audit_event_id'],)).fetchall()
    captures=[decode_snapshot(row[0]) for row in rows]
    assert {row['kind'] for row in captures}=={'source_components','request_applications','effect_applications','allocations','document_changes'}
    return path,captures


@pytest.mark.parametrize('kind',['source_components','request_applications','effect_applications','allocations','document_changes'])
def test_actual_operation_item_exact_values(receipt_items,kind):
    path,rows=receipt_items;before=storage(path)
    found=[r for r in rows if r['kind']==kind];assert found
    for raw in found:
        decoded=decode_company_snapshot(producer='payment receive',record_type='payment_operation_item',action='create',snapshot=raw)
        actual=decoded.model_dump(mode='json');actual.pop('tag')
        expected=copy.deepcopy(raw)
        if kind=='document_changes':
            for key in ('audit_watermark','settlement_guard'):expected['item_snapshot'].pop(key,None)
        assert actual==expected
        values=actual['item_snapshot']
        if kind=='source_components':
            assert (values['received_minor_units'],values['applied_minor_units'],values['available_minor_units'])==(150,100,50)
        elif kind=='document_changes':assert (values['gross_minor_units'],values['applied_minor_units'],values['due_minor_units'])==(100,100,0)
        else:assert values['amount']=={'amount':'1.00','minor_units':100,'currency':'USD'}
        bad=copy.deepcopy(raw);bad['item_snapshot']['invented']=True
        with pytest.raises(BookflowError) as caught:
            decode_company_snapshot(producer='payment receive',record_type='payment_operation_item',action='create',snapshot=bad)
        assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
        # Captured owner references are required by the actual producer. Their
        # nullable projection types permit authorized masking, not corrupt input.
        required={'source_components':('party_id','party_name','ar_account_id'),
                  'effect_applications':('party_id',)}.get(kind,())
        for field in required:
            bad=copy.deepcopy(raw);bad['item_snapshot'][field]=None
            with pytest.raises(BookflowError) as caught:
                decode_company_snapshot(producer='payment receive',record_type='payment_operation_item',action='create',snapshot=bad)
            assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    assert storage(path)==before
