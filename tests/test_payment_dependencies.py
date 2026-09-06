"""Authentic baselines attribute the interval, not a guessed commercial revision."""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_row8_journal import database_path
from tests.conftest import make_actor, as_user
from bookflow.core import audit


def test_guard_actual_settlement_event_and_safe_corrupt_baseline(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'DEPENDENCY-INVOICE')
    baseline = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)['settlement_guard']
    args = dict(customer=sale['customer'], date='2026-06-02', amount='50.00', payment_method=method(client),
        operation_key='guard-receive', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='50.00')]))
    client.run('payment receive', args, company=COMPANY)
    changed = client.run('payment settlement changes', dict(guard=baseline), company=COMPANY)
    assert not changed['unknown_history'] and changed['total_count'] == 2
    # The new payment header and the existing invoice header belong to one event.
    assert len({item['event_id'] for item in changed['items']}) == 1
    row = next(item for item in changed['items'] if item['record_id'] == invoice['id'])
    assert row['record_id'] == invoice['id'] and (row['version_before'], row['version_after']) == (1, 2)
    assert row['fields'] == [] and row['settlement_fields'] == ['settlement.applied', 'settlement.allocations']
    assert row['actor_id'] and row['latest_writer_id'] and row['age_seconds'] >= 0
    with pytest.raises(BookflowError) as caught:
        client.run('payment settlement changes', dict(guard=baseline[:-8] + 'AAAAAAAA'), company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE' and caught.value.details['reason'] == 'invalid_guard'
    with sqlite3.connect(database_path(client)) as db:
        # Disposable corruption witness; no application or commercial state changes.
        db.execute("UPDATE audit_entries SET after=? WHERE record_type='transaction' AND record_id=? AND version_after=1",
                   (b'\x00[]', invoice['id']))
    unknown = client.run('payment settlement changes', dict(guard=baseline), company=COMPANY)
    assert unknown['unknown_history'] and invoice['id'] in unknown['unknown_record_ids']
    assert {item['event_id'] for item in unknown['items']} == {row['event_id']}


def test_stale_guard_and_header_name_actual_settlement_fields(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'STALE-GUARD-INVOICE')
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='100.00',
        payment_method=method(client), operation_key='stale-guard-cash'), company=COMPANY)
    guard = client.run('payment show', dict(payment=paid['id']), company=COMPANY)['settlement_guard']
    client.run('payment apply', dict(payment=paid['id'], expected_version=1, date='2026-06-02', operation_key='stale-guard-apply',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='25.00')])), company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('payment update', dict(payment=paid['id'], expected_version=2, operation_key='stale-guard-edit',
            memo='Attempted correction', settlement_guard=guard), reason='Correct receipt', company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE'
    assert caught.value.details['reason'] == 'settlement_dependencies' and not caught.value.details['unknown_history']
    assert any('settlement.applied' in row['settlement_fields'] for row in caught.value.details['changes'])
    with pytest.raises(BookflowError) as caught:
        client.run('payment update', dict(payment=paid['id'], expected_version=1, operation_key='stale-header-edit', memo='Draft'),
                   reason='Correct receipt', company=COMPANY)
    assert caught.value.code == 'E_VERSION_CONFLICT'
    assert 'settlement.applied' in caught.value.details['changed_fields']
    assert 'Latest writer:' in str(caught.value)


def test_guard_rejects_owned_header_with_foreign_revision_baseline(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'FOREIGN-BASELINE-INVOICE')
    guard = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)['settlement_guard']
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='10.00',
        payment_method=method(client), operation_key='foreign-baseline-cash', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='10.00')])), company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        entry_id, raw = db.execute("SELECT id, after FROM audit_entries WHERE record_type='transaction' AND record_id=? AND version_after=1",
                                   (invoice['id'],)).fetchone()
        snapshot = audit.decode_snapshot(raw)
        snapshot['current_revision_id'] = db.execute('SELECT current_revision_id FROM transactions WHERE id=?', (payment['id'],)).fetchone()[0]
        db.execute('UPDATE audit_entries SET after=? WHERE id=?', (audit.encode_snapshot(snapshot), entry_id))
    result = client.run('payment settlement changes', dict(guard=guard), company=COMPANY)
    assert result['unknown_history'] and invoice['id'] in result['unknown_record_ids']
    with pytest.raises(BookflowError) as caught:
        client.run('invoice update', dict(invoice=invoice['id'], expected_version=1, operation_key='foreign-baseline-edit', memo='Stale draft'),
                   reason='Correct invoice', company=COMPANY)
    assert caught.value.code == 'E_VERSION_CONFLICT'
    assert 1 in caught.value.details['unknown_versions']


def test_interleaved_actors_keep_event_attribution_separate_from_latest_writer(client, sale, root):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'INTERLEAVED-INVOICE')
    guard = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)['settlement_guard']
    company_id = client.company.show(company=COMPANY)['id']
    actors = [make_actor(root, 'settlement-first', company_role=(company_id, 'standard')),
              make_actor(root, 'settlement-second', company_role=(company_id, 'standard'))]
    payment_method = method(client)
    payments = []
    for ordinal, name in enumerate(('settlement-first', 'settlement-second'), 1):
        user = as_user(root, name)
        payments.append(user.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='10.00',
            payment_method=payment_method, operation_key=f'interleaved-cash-{ordinal}', applications=dict(mode='inline', items=[
                dict(invoice=invoice['id'], expected_version=ordinal, amount='10.00')])), company=COMPANY))
    corrected = client.run('invoice update', dict(invoice=invoice['id'], expected_version=3, operation_key='interleaved-memo',
        memo='Commercial note only', settlement_versions=[dict(payment=row['id'], expected_version=1) for row in payments]),
        reason='Correct invoice note', company=COMPANY)
    changes = client.run('payment settlement changes', dict(guard=guard), company=COMPANY)
    own = [row for row in changes['items'] if row['record_id'] == invoice['id']]
    assert len(own) == 3
    assert [row['actor_id'] for row in own[:2]] == actors
    assert all(row['settlement_fields'] == ['settlement.applied', 'settlement.allocations'] for row in own[:2])
    assert own[2]['settlement_fields'] == []
    assert all(row['latest_writer_id'] == corrected['updated_by'] for row in own)
    with sqlite3.connect(database_path(client)) as db:
        db.execute("UPDATE audit_entries SET after=? WHERE event_id=? AND record_id=?", (b'\x00[]', own[0]['event_id'], invoice['id']))
    unknown = client.run('payment settlement changes', dict(guard=guard), company=COMPANY)
    assert unknown['unknown_history'] and invoice['id'] in unknown['unknown_record_ids']
    bad = next(row for row in unknown['items'] if row['event_id'] == own[0]['event_id'] and row['record_id'] == invoice['id'])
    assert bad['unknown_fields'] and bad['actor_id'] == actors[0]
