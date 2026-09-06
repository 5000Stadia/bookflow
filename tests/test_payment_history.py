"""Dated projections retain current knowledge and future capacity reservations."""
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method


def test_effective_cutoff_future_capacity_and_unapply_history(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'DATED-INVOICE')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='100.00',
        payment_method=method(client), operation_key='dated-cash'), company=COMPANY)
    applied = client.run('payment apply', dict(payment=payment['id'], expected_version=1, date='2026-06-10', operation_key='dated-apply',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='40.00')])), company=COMPANY)
    projected = client.run('payment settlement', dict(payment=payment['id'], as_of='2026-06-02'), company=COMPANY)
    assert (projected['received_minor_units'], projected['applied_minor_units'], projected['unapplied_minor_units']) == (10000, 0, 10000)
    assert projected['all_committed_current']['available_minor_units'] == 6000
    due = client.run('invoice settlement', dict(invoice=invoice['id'], as_of='2026-06-02'), company=COMPANY)
    assert due['due_minor_units'] == 10000 and due['all_committed_current']['due_minor_units'] == 6000
    app_id = applied['effect']['applications'][0]['application_id']
    active = client.run('application show', dict(application=app_id), company=COMPANY)
    assert active['active'] and active['current_allocation_count'] == 1
    client.run('payment unapply', dict(payment=payment['id'], expected_version=2, operation_key='dated-unapply',
        applications=[dict(application_id=app_id, invoice_expected_version=2)]), reason='Remove future allocation', company=COMPANY)
    after = client.run('invoice settlement', dict(invoice=invoice['id'], as_of='2026-06-10'), company=COMPANY)
    assert after['due_minor_units'] == 10000
    history = client.run('application history', dict(application=app_id), company=COMPANY)
    assert history['total_count'] == 4
    assert {row['application']['kind'] for row in history['items'] if row['application']} == {'apply', 'unapply'}
    assert {row['allocation']['kind'] for row in history['items'] if row['allocation']} == {'allocation', 'reversal'}
    payment_history = client.run('payment history', dict(payment=payment['id']), company=COMPANY)
    assert {row['kind'] for row in payment_history['items']} == {'receipt_revision', 'operation', 'application', 'allocation'}
