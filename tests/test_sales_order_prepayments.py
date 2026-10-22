"""R101 x R124: a sales order holds customer prepayments as estimates and work orders do.

A deposit received on an order is held there and shown on it; the order cannot be voided while it
holds money; money held on an accepted estimate follows it to the sales order made from it; and
`report customer-prepayments` still ties to the account throughout.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.test_customer_prepayments import books, estimate, held, receive  # noqa: F401


def _order(books, date='2026-03-03'):
    return books['run']('sales-order create', dict(customer=books['ortega'], date=date,
                        lines=[dict(item=books['labor'], quantity='4')]), reason='Ortega ordered')


def _ties(books, as_of='2026-12-31'):
    report = books['run']('report customer-prepayments', dict(as_of=as_of))
    assert report['total_held']['minor_units'] == report['account_balance']['minor_units']
    return report['total_held']['minor_units']


def test_a_deposit_received_on_an_order_is_held_there_and_blocks_its_void(books):
    run = books['run']
    order = _order(books)
    got = receive(books, '120.00', '2026-03-04', document=f'sales-order {order["number"]}')
    assert got['held_after']['minor_units'] == 12000
    shown = run('sales-order show', dict(sales_order=order['id']))
    assert shown['order']['prepayment_held']['minor_units'] == 12000
    listed = run('prepayment show', dict(document=f'sales-order {order["number"]}'))
    assert '12000' in str(listed) or '120.00' in str(listed)
    assert _ties(books) == 12000 and held(books) == 12000

    with pytest.raises(BookflowError) as refused:
        run('sales-order void', dict(sales_order=order['id'], expected_version=shown['version']), reason='Withdrawn')
    assert refused.value.code == 'E_WORK_DEPENDENCY' and 'prepayment' in refused.value.details['problem']
    assert refused.value.details['held']['minor_units'] == 12000
    # Closing is allowed, and says the money is still held.
    closed = run('sales-order update', dict(sales_order=order['id'], expected_version=shown['version'], status='closed'),
                 reason='Nothing more will ship')
    assert closed['status'] == 'closed' and any('prepayments are still held' in w for w in closed['warnings'])

    # Paid back, the order can go.
    run('prepayment refund', dict(document=f'sales-order {order["number"]}', amount='120.00', date='2026-03-06', bank_account=books['bank'],
                                  payment_method=books['methods']['Check']), reason='Order cancelled')
    assert _ties(books) == 0
    voided = run('sales-order void', dict(sales_order=order['id'], expected_version=closed['version']), reason='Withdrawn')
    assert voided['status'] == 'voided' and voided['order'].get('prepayment_held') is None


def test_money_on_an_estimate_follows_it_to_its_sales_order(books):
    run = books['run']
    est = estimate(books)
    receive(books, '150.00', '2026-03-02', document=f'estimate {est["number"]}')
    current = run('estimate show', dict(estimate=est['id']))
    order = run('estimate sales-order', dict(estimate=est['id'], expected_version=current['version'], date='2026-03-03',
                                             conversion_key='ortega-order'), reason='Make the order')
    assert order['order']['prepayment_held']['minor_units'] == 15000
    with pytest.raises(BookflowError) as refused:
        run('sales-order void', dict(sales_order=order['id'], expected_version=order['version']), reason='Withdrawn')
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    assert _ties(books) == 15000 and _ties(books, '2026-03-01') == 0
