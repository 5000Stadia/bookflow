"""Direct and composite evidence reaches the same conditional resource boundary."""
import io

import pytest

from bookflow import BookflowError
from bookflow.company import payment_authority
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method
from tests.test_work_billing_lifecycle import accepted, bill
from bookflow.hub.permission_runtime import catalog_bundle
from bookflow.storage.engine import open_database
from tests.test_permission_snapshots import install_fixture_policy


@pytest.fixture(autouse=True)
def current_history_policy(root):
    """Initialize current history policy on the ordinary disposable root copy."""
    with open_database(root / "hub.db", writable=True) as db:
        install_fixture_policy(db.raw, catalog_bundle())


def test_attachment_and_annotation_audit_require_historical_work_graph(client, sale, monkeypatch):
    invoice = bill(client, accepted(client, sale))
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='evidence-payment', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='1.00')])), company=COMPANY)
    note = client.note.add(record_type='transaction', record_id=payment['id'], body='Protected remittance detail', company=COMPANY)['note']
    attachment = client.attachment.add(record_type='transaction', record_id=payment['id'], original_filename='payment.txt',
        input_stream=io.BytesIO(b'protected receipt bytes'), company=COMPANY)
    # Share bytes with an ordinary customer too; that association must not grant
    # access around the protected payment endpoint.
    client.attachment.link(attachment=attachment['attachment']['id'], record_type='customer', record_id=sale['customer'], company=COMPANY)
    note_event = client.audit.list(record_type='note', record_id=note['id'], company=COMPANY)['items'][0]['id']
    attachment_events = [row['id'] for row in client.audit.list(record_type='attachment_link',
        record_id=attachment['link']['id'], company=COMPANY)['items']]
    client.run('payment unapply', dict(payment=payment['id'], expected_version=1, operation_key='evidence-unapply', applications=[
        dict(application_id=payment['effect']['applications'][0]['application_id'], invoice_expected_version=2)]),
        reason='Remove recorded allocation', company=COMPANY)
    original = payment_authority.require_resource
    seen = []
    def deny_work(session, capability, role):
        seen.append((capability, role))
        if capability == 'customer-work':
            raise BookflowError('E_PERMISSION', details={'capability': capability})
        return original(session, capability, role)
    monkeypatch.setattr(payment_authority, 'require_resource', deny_work)
    sink = io.BytesIO()
    with pytest.raises(BookflowError) as caught:
        client.attachment.get(attachment=attachment['attachment']['id'], output_stream=sink, company=COMPANY)
    assert caught.value.code == 'E_PERMISSION' and sink.getvalue() == b''
    for event in [note_event, *attachment_events]:
        with pytest.raises(BookflowError) as caught:
            client.audit.show(event=event, company=COMPANY)
        assert caught.value.code == 'E_EVENT_NOT_FOUND'
    filtered = client.audit.list(record_type='note', record_id=note['id'], company=COMPANY)
    assert filtered['items'] == []
    assert ('customer-work', 'member') in seen


@pytest.mark.parametrize('snapshot', ['[]', '{}', '{"resolved_transaction_ids":[1]}'])
def test_batched_audit_disclosure_keeps_unknown_operation_ownership_closed(client, sale, monkeypatch, snapshot):
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1',
        payment_method=method(client),operation_key='unresolved-evidence'),company=COMPANY)
    original=payment_authority._evidence_rows
    operation=paid['effect']['operation_id']
    def malformed(db,table,field,value,cache):
        rows=original(db,table,field,value,cache)
        if table.name=='payment_operations' and value is not None:
            return [dict(row,request_snapshot=snapshot) if row['id']==operation else row for row in rows]
        return rows
    event=client.audit.list(record_type='payment_operation',record_id=operation,company=COMPANY)['items'][0]['id']
    monkeypatch.setattr(payment_authority,'_evidence_rows',malformed)
    with pytest.raises(BookflowError) as caught:
        client.audit.show(event=event,company=COMPANY)
    assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    with pytest.raises(BookflowError) as caught:
        client.audit.list(record_type='payment_operation',record_id=operation,company=COMPANY)
    assert caught.value.code=='E_VALIDATION' and caught.value.details=={'reason':'audit_format'}
    # Internal envelope-denial control; real governed pairs have separate witnesses.
    original_resource=payment_authority.require_resource
    def restricted(session, capability, role):
        if capability=='customer-work':raise BookflowError('E_PERMISSION')
        return original_resource(session, capability, role)
    monkeypatch.setattr(payment_authority,'require_resource',restricted)
    with pytest.raises(BookflowError) as caught:
        client.audit.show(event=event,company=COMPANY)
    assert caught.value.code=='E_EVENT_NOT_FOUND'
    assert client.audit.list(record_type='payment_operation',record_id=operation,company=COMPANY)['items']==[]
