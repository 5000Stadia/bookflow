"""Recorded cancellation preserves exact historical allocation and posting evidence."""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path


def test_unapply_then_void_exact_dates_noop_audit_and_permanent_replay(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'CANCEL-INVOICE')
    receive = dict(customer=sale['customer'], amount='150.00', date='2026-06-02',
        payment_method=method(client), operation_key='cancel-receive', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='100.00')]))
    payment = client.run('payment receive', receive, company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('payment void', dict(payment=payment['id'], expected_version=1, operation_key='void-too-soon'),
                   reason='Cancel mistaken receipt', company=COMPANY)
    assert caught.value.code == 'E_HAS_APPLICATIONS'
    args = dict(payment=payment['id'], expected_version=1, operation_key='cancel-application', applications=[
        dict(application_id=payment['effect']['applications'][0]['application_id'], invoice_expected_version=2)])
    before = snapshots(client)
    preview = client.run('payment unapply', args, reason='Remove incorrect allocation', company=COMPANY, dry_run=True)
    assert snapshots(client) == before
    undone = client.run('payment unapply', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']),
        reason='Remove incorrect allocation', company=COMPANY)
    assert undone['version'] == 2 and undone['current']['available_minor_units'] == 15000
    after = snapshots(client)
    for table in ('transaction_revisions', 'posting_batches', 'posting_lines', 'posting_line_sources'):
        assert before[table] == after[table]
    assert client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)['due_minor_units'] == 10000
    void_args = dict(payment=payment['id'], expected_version=2, operation_key='cancel-cash')
    voided = client.run('payment void', void_args, reason='Cancel mistaken receipt', company=COMPANY)
    assert voided['current']['status'] == 'voided' and voided['version'] == 3
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=?',
                          (payment['id'],)).fetchone()[0] == 0
        assert db.execute('SELECT DISTINCT effective_date FROM posting_batches WHERE transaction_id=?',
                          (payment['id'],)).fetchall() == [('2026-06-02',)]
        old_count = db.execute('SELECT count(*) FROM audit_events').fetchone()[0]
    stable = snapshots(client)
    noop = client.run('payment void', dict(void_args, expected_version=3, operation_key='confirm-void'),
                     reason='Confirm already voided', company=COMPANY)
    assert noop['changed'] is False and noop['effect']['financial_changed'] is False
    newer = snapshots(client)
    for table in ('transactions', 'transaction_revisions', 'posting_batches', 'posting_lines', 'posting_line_sources', 'applications', 'application_allocations'):
        assert newer[table] == stable[table]
    with sqlite3.connect(database_path(client)) as db:
        assert db.execute('SELECT count(*) FROM audit_events').fetchone()[0] == old_count + 1
    stable = snapshots(client)
    replay = client.run('payment receive', receive, company=COMPANY)
    assert replay['idempotent_replay'] and replay['current']['status'] == 'voided'
    assert snapshots(client) == stable
    replay = client.run('payment unapply', args, reason='Remove incorrect allocation', company=COMPANY)
    assert replay['idempotent_replay'] and snapshots(client) == stable


def test_unapply_uses_original_closed_date_and_retry_remains_readonly(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '1.00', 'CLOSED-UNAPPLY')
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='closed-cancel-cash', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='1.00')])), company=COMPANY)
    args = dict(payment=payment['id'], expected_version=1, operation_key='closed-cancel-unapply', applications=[
        dict(application_id=payment['effect']['applications'][0]['application_id'], invoice_expected_version=2)])
    client.run('company update', dict(closing_date='2026-06-02'), company=COMPANY)
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('payment unapply', args, reason='Remove allocation', company=COMPANY)
    assert caught.value.code == 'E_PERIOD_CLOSED' and snapshots(client) == before
