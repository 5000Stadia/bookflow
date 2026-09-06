"""Bounded SQL payment projection retains captured search and current credit."""
import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY
from tests.test_payment_receipts import method


def test_payment_method_capture_unicode_search_and_watermark_pages(client):
    customer = client.customer.create(name='Straße Remittance Customer', company=COMPANY)['id']
    cash = method(client)
    check = client.run('payment-method create', dict(name='Query check', kind='check'), company=COMPANY)['id']
    payments = []
    for ordinal, payment_method in enumerate((cash, cash, check), 1):
        payments.append(client.run('payment receive', dict(customer=customer, date='2026-06-02', amount=f'{ordinal}.00',
            payment_method=payment_method, operation_key=f'query-cash-{ordinal}'), company=COMPANY))
    request = dict(customer=customer, payment_method=cash, sort='received', direction='asc', limit=1)
    first = client.run('payment query', request, company=COMPANY)
    assert first['total_count'] == 2 and first['items'][0]['id'] == payments[0]['id']
    second = client.run('payment query', dict(request, cursor=first['next_cursor']), company=COMPANY)
    assert second['items'][0]['id'] == payments[1]['id'] and second['next_cursor'] is None
    client.customer.update(customer=customer, expected_version=1, name='Current name', company=COMPANY)
    with pytest.raises(BookflowError) as caught:
        client.run('payment query', dict(request, cursor=first['next_cursor']), company=COMPANY)
    assert caught.value.code == 'E_QUERY_STALE'
    captured = client.run('payment query', dict(q='STRASSE'), company=COMPANY)
    assert captured['total_count'] == 3
    client.run('payment void', dict(payment=payments[2]['id'], expected_version=1, operation_key='query-void'), reason='Cancel receipt', company=COMPANY)
    unavailable = client.run('payment query', dict(customer=customer, has_available_credit=False), company=COMPANY)
    assert unavailable['total_count'] == 1 and unavailable['items'][0]['status'] == 'voided'
    assert unavailable['items'][0]['received_minor_units'] == 300 and unavailable['items'][0]['unapplied_minor_units'] == 0


@pytest.mark.parametrize('args', [dict(include_descendants=True), dict(date_from='2026-06-03', date_to='2026-06-02')])
def test_query_cross_field_validation(client, args):
    with pytest.raises(BookflowError) as caught:
        client.run('payment query', args, company=COMPANY)
    assert caught.value.code == 'E_VALIDATION'
