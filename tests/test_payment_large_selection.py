"""One complete remittance across 201 jobs with interleaved draft/page writes."""
import sqlite3
import pytest

from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method, posted, snapshots
from tests.test_row8_journal import database_path


@pytest.mark.timeout(300)
def test_201_jobs_one_cash_receipt_readonly_prospective_and_durable_pages(client, sale):
    jobs, invoices = [], []
    for ordinal in range(201):
        job = client.customer.create(name=f'Payment large job {ordinal:03}', parent_id=sale['customer'], company=COMPANY)['id']
        jobs.append(job)
        invoices.append(posted(client, job, sale['item'], '0.03', f'PAY-LARGE-{ordinal:03}'))
    draft = client.run('payment selection create', dict(mode='new_receipt', customer=sale['customer'], date='2026-06-01', amount='6.03'), company=COMPANY)
    request = dict(mode='new_receipt', customer=sale['customer'], date='2026-06-01', amount='6.03', strategy='exact_then_oldest', limit=50)
    seen = []
    while True:
        page = client.run('payment suggest', request, company=COMPANY)
        assert page['total_count'] == 201
        seen.extend(row['invoice_id'] for row in page['items'])
        draft = client.run('payment selection update', dict(selection=draft['id'], expected_version=draft['version'],
            set_items=[dict(invoice=row['invoice_id'], expected_version=row['expected_version'], amount='0.03', amount_origin='calculated') for row in page['items']]), company=COMPANY)
        if page['next_cursor'] is None:
            break
        request['cursor'] = page['next_cursor']
    assert len(seen) == len(set(seen)) == 201
    args = dict(customer=sale['customer'], date='2026-06-01', amount='6.03', payment_method=method(client), operation_key='large-complete-remittance',
        applications=dict(mode='selection', selection=draft['id'], expected_version=draft['version']))
    before = snapshots(client)
    preview = client.run('payment receive', args, company=COMPANY, dry_run=True)
    assert preview['effect']['operation_id'] is None and len(preview['effect']['applications']) == 50
    assert preview['effect_counts']['applications'] == preview['effect_counts']['source_components'] == 201
    assert preview['current']['component_count'] == 201 and len(preview['current']['components']) == 50
    for descriptor in preview['prospective_pages']:
        items = list(preview['effect'][descriptor['kind']])
        while descriptor['next_cursor']:
            page = client.run('payment preview items', dict(request=descriptor['request'], kind=descriptor['kind'],
                facts_fingerprint=descriptor['facts_fingerprint'], cursor=descriptor['next_cursor']), company=COMPANY)
            assert page['projection'] == 'prospective'
            items.extend(page['items'])
            descriptor['next_cursor'] = page['next_cursor']
        assert len(items) == descriptor['total_count'] == 201
        assert all(row.get('application_id') is None and row.get('allocation_id') is None for row in items)
    assert snapshots(client) == before
    paid = client.run('payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert paid['current']['applied_minor_units'] == 603 and paid['current']['available_minor_units'] == 0
    seen = []
    request = dict(operation_key=args['operation_key'], kind='effect_applications', limit=50)
    while True:
        page = client.run('payment operation items', request, company=COMPANY)
        seen.extend(row['invoice_id'] for row in page['items'])
        if page['next_cursor'] is None:
            break
        request['cursor'] = page['next_cursor']
    assert set(seen) == {row['id'] for row in invoices} and len(seen) == 201
    with sqlite3.connect(database_path(client)) as raw:
        legs = raw.execute('SELECT name_id,debit_minor_units,credit_minor_units FROM posting_lines WHERE transaction_id=?', (paid['id'],)).fetchall()
        assert len(legs) == 202
        assert [(party, debit) for party, debit, credit in legs if debit] == [(sale['customer'], 603)]
        assert {party: credit for party, debit, credit in legs if credit} == {job: 3 for job in jobs}
        assert raw.execute('SELECT count(*) FROM applications WHERE paying_transaction_id=?', (paid['id'],)).fetchone() == (201,)
        assert raw.execute('PRAGMA foreign_key_check').fetchall() == []
