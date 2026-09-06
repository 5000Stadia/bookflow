"""Sales page batching keeps independent show values and exact per-invoice nets."""
import pytest
import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import payment_authority
from tests.test_service_sales_lifecycle import COMPANY, sale
from tests.test_payment_receipts import method, posted, snapshots
from tests.test_work_billing_lifecycle import accepted, bill


def test_sales_pages_match_show_without_per_row_sql(client, sale):
    invoices = [posted(client, sale['customer'], sale['item'], '1', f'BATCH-{i:02}') for i in range(12)]
    cash = method(client)
    bank = client.account.create(name='Batch receipt bank', type='bank', company=COMPANY)['id']
    client.run('payment receive', dict(customer=sale['customer'], date='2026-06-01', amount='1.25',
        payment_method=cash, operation_key='batch-page-receipt', applications=dict(mode='inline', items=[
            dict(invoice=invoices[0]['id'], expected_version=1, amount='1'),
            dict(invoice=invoices[1]['id'], expected_version=1, amount='0.25')])), company=COMPANY)
    client.run('invoice void', dict(invoice=invoices[2]['id'], expected_version=1), company=COMPANY, reason='Cancel duplicate')
    for i in range(3):
        client.run('sales-receipt post', dict(customer=sale['customer'], date='2026-06-01',
            payment_method=cash, deposit_to=bank, number=f'BATCH-RECEIPT-{i}', lines=[dict(item=sale['item'])]), company=COMPANY)
    before = snapshots(client)
    for noun, limits in [('invoice', (2, 12)), ('sales-receipt', (1, 3))]:
        counts = []
        for limit in limits:
            statements = []
            def capture(conn, cursor, statement, parameters, context, many):
                statements.append(statement)
            sa.event.listen(sa.engine.Engine, 'before_cursor_execute', capture)
            try:
                page = client.run(noun+' query', dict(customer=sale['customer'], limit=limit), company=COMPANY)
            finally:
                sa.event.remove(sa.engine.Engine, 'before_cursor_execute', capture)
            counts.append(len(statements))
            assert len(page['items']) == limit
            for row in page['items']:
                shown = client.run(noun+' show', {noun.replace('-', '_'):row['id']}, company=COMPANY)
                assert row == {key:shown[key] for key in row}
        assert counts[0] == counts[1], (noun, counts)
    page = client.run('invoice query', dict(customer=sale['customer']), company=COMPANY)
    states = {row['id']:row['settlement_current'] for row in page['items']}
    assert states[invoices[0]['id']]['status'] == 'paid'
    assert states[invoices[1]['id']]['due_minor_units'] == 75
    assert states[invoices[2]['id']]['status'] == 'voided'
    assert snapshots(client) == before


def test_invoice_page_keeps_large_separate_values_and_historical_work_gate(client, sale, monkeypatch):
    protected = bill(client, accepted(client, sale))
    for i in range(2):
        posted(client, sale['customer'], sale['item'], '90000000000000000', f'BATCH-LARGE-{i}')
    page = client.run('invoice query', dict(customer=sale['customer'], number='BATCH-LARGE-'), company=COMPANY)
    assert [row['settlement_current']['due_minor_units'] for row in page['items']] == [9000000000000000000]*2
    original = payment_authority.require_resource
    def deny_work(session, resource, role):
        if resource == 'customer-work':
            raise BookflowError('E_PERMISSION', details={'capability':resource})
        return original(session, resource, role)
    monkeypatch.setattr(payment_authority, 'require_resource', deny_work)
    with pytest.raises(BookflowError) as caught:
        client.run('invoice query', dict(number=protected['number']), company=COMPANY)
    assert caught.value.code == 'E_PERMISSION'
