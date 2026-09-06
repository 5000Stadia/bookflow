"""Useful receipt corrections retain job ownership and original target cents."""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path


def test_family_receipt_increase_guard_source_restatement_and_unapply(client, sale):
    job = client.customer.create(name='Correction owned job', parent_id=sale['customer'], company=COMPANY)['id']
    invoice = posted(client, job, sale['item'], '100.00', 'CORRECTION-JOB')
    received = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='100.00',
        payment_method=method(client), operation_key='correction-new-cash', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='80.00')])), company=COMPANY)
    guard = client.run('payment show', dict(payment=received['id']), company=COMPANY)['settlement_guard']
    args = dict(payment=received['id'], expected_version=1, operation_key='correction-increase',
                amount='110.00', memo='Correct original remittance amount', settlement_guard=guard)
    before = snapshots(client)
    preview = client.run('payment update', args, reason='Correct remittance', company=COMPANY, dry_run=True)
    assert snapshots(client) == before
    corrected = client.run('payment update', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']),
                           reason='Correct remittance', company=COMPANY)
    assert corrected['version'] == 2 and corrected['current']['available_minor_units'] == 3000
    assert {row['party_id']: row['received_minor_units'] for row in corrected['current']['components']} == {sale['customer']: 3000, job: 8000}
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute("SELECT count(*) FROM applications WHERE paying_transaction_id=?", (received['id'],)).fetchone()[0] == 1
        assert db.execute("SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND name_id=? AND account_id=(SELECT id FROM accounts WHERE type='accounts_receivable' LIMIT 1)",
                          (received['id'], job)).fetchone()[0] == -8000
        rows = db.execute('SELECT kind,amount_minor_units,target_revision_id FROM application_allocations WHERE source_transaction_id=? ORDER BY rowid', (received['id'],)).fetchall()
        assert rows == [('allocation', 8000, invoice['current_revision_id']), ('reversal', 8000, invoice['current_revision_id']), ('allocation', 8000, invoice['current_revision_id'])]
    invoice_now = client.run('invoice show', dict(invoice=invoice['id']), company=COMPANY)
    assert invoice_now['version'] == 3 and invoice_now['revision']['revision_number'] == 1
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('payment update', dict(payment=received['id'], expected_version=2, operation_key='cannot-borrow-job',
            amount='70.00', invoice_versions=[dict(invoice=invoice['id'], expected_version=3)]), reason='Correct amount', company=COMPANY)
    assert caught.value.code == 'E_APPLIED_EXCEEDS_TOTAL' and snapshots(client) == before
    client.run('payment unapply', dict(payment=received['id'], expected_version=2, operation_key='unapply-restated', applications=[
        dict(application_id=received['effect']['applications'][0]['application_id'], invoice_expected_version=3)]),
        reason='Undo allocation', company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute("SELECT sum(CASE kind WHEN 'allocation' THEN amount_minor_units ELSE -amount_minor_units END) FROM application_allocations WHERE source_transaction_id=?", (received['id'],)).fetchone()[0] == 0


def test_zero_payer_capacity_retires_component_without_zero_legs_and_reuses_key(client, sale):
    job = client.customer.create(name='Zero payer capacity job', parent_id=sale['customer'], company=COMPANY)['id']
    invoice = posted(client, job, sale['item'], '80.00', 'ZERO-PAYER-JOB')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='100.00',
        payment_method=method(client), operation_key='zero-payer-cash', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='80.00')])), company=COMPANY)
    key = next(row['component_key_id'] for row in payment['current']['components'] if row['party_id'] == sale['customer'])
    for version, amount, expected in [(1, '80.00', 0), (2, '100.00', 2000)]:
        result = client.run('payment update', dict(payment=payment['id'], expected_version=version, amount=amount,
            operation_key=f'zero-payer-correction-{version}', invoice_versions=[dict(invoice=invoice['id'], expected_version=version+1)]),
            reason='Correct cash amount', company=COMPANY)
        payer = next(row for row in result['current']['components'] if row['party_id'] == sale['customer'])
        assert payer['component_key_id'] == key and payer['received_minor_units'] == expected
        assert (payer['component_id'] is None) == (expected == 0)
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM payment_component_keys WHERE transaction_id=?', (payment['id'],)).fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM posting_lines WHERE transaction_id=? AND debit_minor_units=0 AND credit_minor_units=0', (payment['id'],)).fetchone()[0] == 0
