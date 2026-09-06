"""Dated projections retain current knowledge and future capacity reservations."""
import pytest

from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method
from tests.test_payment_receipts import snapshots


@pytest.mark.parametrize('dates', [
    ('2026-06-10',),
    ('2026-05-20',),
    ('2026-06-10', '2026-05-20', '2026-06-15'),
    ('2026-06-10', '2026-05-20'),
])
@pytest.mark.parametrize('voided', [False, True])
def test_dated_existence_follows_final_corrected_obligation(client, sale, dates, voided):
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'CORRECTED-DATES')
    version = 1
    for date in dates:
        result = client.run('invoice update', dict(invoice=invoice['id'], expected_version=version,
            date=date, operation_key=f'correct-date-{version}'), company=COMPANY, reason='Correct effective date')
        version = result['version']
    if voided:
        client.run('invoice void', dict(invoice=invoice['id'], expected_version=version),
            company=COMPANY, reason='Cancel final corrected obligation')
        version += 1
    before = snapshots(client)
    for cutoff in ('2026-05-19', '2026-05-20', '2026-06-01', '2026-06-05', '2026-06-10', '2026-06-15'):
        state = client.run('invoice settlement', dict(invoice=invoice['id'], as_of=cutoff), company=COMPANY)
        effective = cutoff >= dates[-1]
        expected_status = 'not_effective' if not effective else 'voided' if voided else 'unpaid'
        assert state['status'] == expected_status
        assert state['gross_minor_units'] == state['due_minor_units'] == (100 if effective and not voided else 0)
        assert state['applied_minor_units'] == 0
        assert state['all_committed_current']['status'] == ('voided' if voided else 'unpaid')
        assert state['all_committed_current']['version'] == version
    assert snapshots(client) == before


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
