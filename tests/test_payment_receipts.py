"""Public receipts/applications checked against independent exact-party GL nets."""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import database_path, assert_oracle


def posted(client, customer, item, amount, number):
    return client.run('invoice post', dict(customer=customer, date='2026-06-01', number=number,
        lines=[dict(item=item, quantity='1', unit_price=amount)]), company=COMPANY)


def method(client):
    return client.run('payment-method create', dict(name='Payment witness cash', kind='cash'), company=COMPANY)['id']


def snapshots(client):
    with sqlite3.connect(database_path(client)) as raw:
        return {table: raw.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (
            'principals', 'transactions', 'transaction_revisions', 'posting_batches', 'posting_lines',
            'posting_line_sources', 'applications', 'application_allocations', 'audit_events', 'audit_entries', 'payment_operations')}


def test_receive_exact_party_balances_and_permanent_replay(client, sale):
    parent = sale['customer']
    a = client.customer.create(name='Payment Job A', parent_id=parent, company=COMPANY)['id']
    b = client.customer.create(name='Payment Job B', parent_id=parent, company=COMPANY)['id']
    invoices = [posted(client, party, sale['item'], amount, number) for party, amount, number in (
        (parent, '1.00', 'PAY-P'), (a, '6.00', 'PAY-A'), (b, '4.00', 'PAY-B'))]
    bank = client.account.create(name='Payment witness bank', type='bank', company=COMPANY)['id']
    args = dict(customer=parent, date='2026-06-01', amount='10.00', deposit_to=bank,
        payment_method=method(client), operation_key='family-cash-once', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount=amount) for invoice, amount in zip(invoices, ('1.00', '6.00', '2.00'))]))
    preview = client.run('payment receive', args, company=COMPANY, dry_run=True)
    paid = client.run('payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert paid['current']['received_minor_units'] == 1000
    assert paid['current']['applied_minor_units'] == 900 and paid['current']['available_minor_units'] == 100
    assert {row['party_id']: row['received_minor_units'] for row in paid['current']['components']} == {parent: 200, a: 600, b: 200}
    assert {party: client.customer.show(customer=party, company=COMPANY)['current_balance']['minor_units'] for party in (parent, a, b)} == {parent: -100, a: 0, b: 200}
    discovery = client.run('payment invoices', dict(mode='new_receipt', customer=parent,
        date='2026-06-01'), company=COMPANY)
    # Net AR includes unapplied payer cash; it is not the sum of invoice dues.
    assert discovery['payer_balance']['minor_units'] == -100
    assert discovery['family_balance']['minor_units'] == 100
    assert sum(row['due_minor_units'] for row in discovery['items']) == 200
    ar = invoices[0]['revision']['profile']['control_account']['id']
    assert_oracle(client, paid['id'], {('2026-06-01', bank): 1000, ('2026-06-01', ar): -1000})
    for invoice in invoices:
        current = client.run('invoice show', dict(invoice=invoice['id']), company=COMPANY)
        assert current['version'] == 2 and current['current_revision_id'] == invoice['current_revision_id']
        assert current['revision']['revision_number'] == 1
    before = snapshots(client)
    replay = client.run('payment receive', args, company=COMPANY)
    assert replay['idempotent_replay'] and replay['effect'] == paid['effect']
    assert snapshots(client) == before
    with pytest.raises(BookflowError) as caught:
        client.run('payment receive', dict(args, amount='11.00'), company=COMPANY)
    assert caught.value.code == 'E_PAYMENT_OPERATION_KEY_REUSED'
    assert client.run('payment show', dict(payment=paid['id']), company=COMPANY)['current']['available_minor_units'] == 100


def test_existing_credit_exact_party_apply_no_posting_or_revision(client, sale):
    child = client.customer.create(name='Payment exact-party job', parent_id=sale['customer'], company=COMPANY)['id']
    own_invoice = posted(client, sale['customer'], sale['item'], '100.00', 'PAY-OWN')
    child_invoice = posted(client, child, sale['item'], '100.00', 'PAY-CHILD')
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-01', amount='150.00',
        payment_method=method(client), operation_key='unapplied-parent-cash'), company=COMPANY)
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('payment apply', dict(payment=paid['id'], expected_version=1, date='2026-06-01', operation_key='wrong-party',
            applications=dict(mode='inline', items=[dict(invoice=child_invoice['id'], expected_version=1, amount='50.00')])), company=COMPANY)
    assert caught.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert snapshots(client) == before
    result = client.run('payment apply', dict(payment=paid['id'], expected_version=1, date='2026-06-01', operation_key='own-party',
        applications=dict(mode='inline', items=[dict(invoice=own_invoice['id'], expected_version=1, amount='50.00')])), company=COMPANY)
    after = snapshots(client)
    for table in ('transaction_revisions', 'posting_batches', 'posting_lines', 'posting_line_sources'):
        assert after[table] == before[table]
    assert result['version'] == 2 and result['current']['available_minor_units'] == 10000
    assert client.customer.show(customer=sale['customer'], company=COMPANY)['current_balance']['minor_units'] == -5000


def test_saved_selection_consumed_atomically_and_retry_recovers(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'PAY-SELECTED')
    draft = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'], date='2026-06-01', amount='150.00'), company=COMPANY)
    draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=1,
        set_items=[dict(invoice=invoice['id'], expected_version=1, amount='100.00')]), company=COMPANY)
    args = dict(customer=sale['customer'], date='2026-06-01', amount='150.00', payment_method=method(client), operation_key='selected-cash',
                applications=dict(mode='selection', selection=draft['id'], expected_version=2))
    paid = client.run('payment receive', args, company=COMPANY)
    assert client.run('payment selection show', dict(selection=draft['id']), company=COMPANY)['state'] == 'consumed'
    assert client.run('payment receive', args, company=COMPANY)['effect'] == paid['effect']
    with pytest.raises(BookflowError) as caught:
        client.run('payment receive', dict(args, operation_key='new-attempt-consumed'), company=COMPANY)
    assert caught.value.code == 'E_SELECTION_CONSUMED'
