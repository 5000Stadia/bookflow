"""A genuine 403-application graph is corrected atomically without unapply."""
import sqlite3

import pytest

from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import posted, method, snapshots
from tests.test_row8_journal import database_path


@pytest.mark.timeout(600)
@pytest.mark.parametrize('count', [7, 403])
def test_complete_guarded_invoice_correction_and_prospective_pages(client, sale, count):
    invoice = posted(client, sale['customer'], sale['item'], '10.00', f'LARGE-CORRECTION-{count}')
    payment_method = method(client)
    for ordinal in range(count):
        client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='0.01',
            payment_method=payment_method, operation_key=f'large-correction-{count}-{ordinal}', applications=dict(mode='inline', items=[
                dict(invoice=invoice['id'], expected_version=ordinal+1, amount='0.01')])), company=COMPANY)
    guard = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)['settlement_guard']
    assert len(guard) <= 2048
    args = dict(invoice=invoice['id'], expected_version=count+1, operation_key=f'large-correction-{count}-edit',
        settlement_guard=guard, lines=[dict(line_id=invoice['revision']['lines'][0]['line_id'], item=sale['item'], quantity='1', unit_price='10.00'),
            dict(item=sale['item'], quantity='1', unit_price='10.00')])
    before = snapshots(client)
    preview = client.run('invoice update', args, reason='Add omitted equal work', company=COMPANY, dry_run=True)
    expected_changed_payments = count // 2
    assert preview['settlement']['effect_counts']['allocations'] == 2*expected_changed_payments
    for descriptor in preview['settlement']['prospective_pages']:
        items = list(preview['settlement']['effect'][descriptor['kind']])
        cursor = descriptor['next_cursor']
        while cursor:
            page = client.run('payment preview items', dict(request=descriptor['request'], kind=descriptor['kind'],
                facts_fingerprint=preview['facts_fingerprint'], cursor=cursor), company=COMPANY)
            assert page['facts_fingerprint'] == preview['facts_fingerprint'] and not page['committed']
            items.extend(page['items'])
            cursor = page['next_cursor']
        assert len(items) == descriptor['total_count']
    assert snapshots(client) == before
    result = client.run('invoice update', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']),
        reason='Add omitted equal work', company=COMPANY)
    assert result['version'] == count+2 and result['settlement']['current']['due_minor_units'] == 2000-count
    with sqlite3.connect(database_path(client)) as db:
        apps = db.execute("SELECT id,paying_transaction_id FROM applications WHERE paid_transaction_id=? AND kind='apply' ORDER BY effective_date,id", (invoice['id'],)).fetchall()
        assert len(apps) == count
        for ordinal, (app_id, payment_id) in enumerate(apps):
            live = db.execute("SELECT target_ordinal,amount_minor_units FROM application_allocations a WHERE application_id=? AND kind='allocation' AND NOT EXISTS (SELECT 1 FROM application_allocations r WHERE r.reverses_allocation_id=a.id)", (app_id,)).fetchall()
            assert live == [(1 if ordinal % 2 == 0 else 2, 1)]
            assert db.execute('SELECT version FROM transactions WHERE id=?', (payment_id,)).fetchone()[0] == (1 if ordinal % 2 == 0 else 2)
        assert db.execute("SELECT count(*) FROM applications WHERE kind='unapply' AND paid_transaction_id=?", (invoice['id'],)).fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
