"""R101 increment 5: shortfall coverage (sales-orders design §5 and §8.5).

A 4-short order makes a 4-unit purchase order from the preferred vendor and a retry orders nothing;
two orders never count the same incoming units; amending the purchase order down, receiving it and
voiding it bring the shortfall back by exactly what each removed; mixed vendors are named, spares
may be ordered, and a hand-made purchase order with no link is incoming every document may use.
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


@pytest.fixture
def shop(books):
    run, client, company = books['run'], books['client'], books['company']
    other = client.vendor.create(company=company, name='Eastern Fittings')['id']

    def part(name, vendor, cost):
        return client.item.create(company=company, name=name, type='inventory_part', description=name, price='4.50',
                                  purchase_description=name, cost='1.80', cogs_account_id=books['cogs'],
                                  income_account_id=books['income'],
                                  vendor_profiles=[dict(vendor_id=vendor, preferred_rank=1, purchase_cost=cost)])['id']
    widget = part('Widget', books['vendor'], '1.60')
    gasket = part('Gasket', other, '0.40')
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='6', unit_cost='1.60')]),
        reason='Six in stock')
    return dict(widget=widget, gasket=gasket, other=other)


def _order(books, lines, customer=None):
    return books['run']('sales-order create', dict(customer=customer or books['customer'], date='2017-03-01', lines=lines),
                        reason='Order')


def _buy(books, order, **extra):
    return books['run']('sales-order purchase-order', dict(sales_order=order['number'], date='2017-03-02', **extra),
                        reason='Order the shortfall')


def _short(books, order):
    """What the command would order now, by item name, or {} when nothing is short."""
    try:
        preview = books['run']('sales-order purchase-order', dict(sales_order=order['id'], date='2017-03-02'),
                               reason='Look', dry_run=True)
    except BookflowError as error:
        if 'nothing on this document is short' in str(error.details):
            return {}
        raise
    return {line['description']: line['quantity'] for line in preview['purchase_order']['revision']['lines']}


def test_a_four_short_order_buys_four_from_the_preferred_vendor_and_a_retry_orders_nothing(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='10')])
    made = _buy(books, order)
    po = made['purchase_order']
    assert po['vendor_id'] == books['vendor'] and [line['quantity'] for line in po['revision']['lines']] == ['4']
    assert po['revision']['lines'][0]['rate']['amount'] == '1.60'                 # the vendor's purchase cost
    assert made['links'][0]['linked'] == '4' and made['links'][0]['to_arrive'] == '4'
    assert run('purchase-order show', dict(purchase_order=po['id']))['covers'][0]['work_number'] == order['number']
    line = run('sales-order show', dict(sales_order=order['id']))['revision']['lines'][0]
    assert line['coverage'][0]['purchase_order_number'] == po['revision']['number'] and line['coverage'][0]['to_arrive'] == '4'
    error = _error(lambda: _buy(books, order))
    assert error.code == 'E_VALIDATION' and 'nothing on this document is short' in error.details['problem']
    assert len(run('purchase-order query', {})['items']) == 1


def test_two_orders_never_count_the_same_incoming_units(books, shop):
    other = books['client'].customer.create(company=books['company'], name='Baker Bros')['id']
    first = _order(books, [dict(item=shop['widget'], quantity='4')])
    second = _order(books, [dict(item=shop['widget'], quantity='4')], customer=other)
    # 6 on hand, 8 open: the first order's 4 come first for the second and vice versa, so each is short 2.
    assert _short(books, first) == {'Widget': '2'} and _short(books, second) == {'Widget': '2'}
    _buy(books, first)
    # The 2 ordered for the first are its own; with them, 6 on hand serve the rest: nothing is short twice.
    assert _short(books, first) == {} and _short(books, second) == {}
    third = _order(books, [dict(item=shop['widget'], quantity='3')], customer=other)
    assert _short(books, third) == {'Widget': '3'}
    # A hand-made purchase order with no link is incoming every document may use.
    books['run']('purchase-order post', dict(vendor=books['vendor'], date='2017-03-03',
        lines=[dict(item=shop['widget'], quantity='2', rate='1.60')]), reason='By hand')
    assert _short(books, third) == {'Widget': '1'}


def test_amending_down_receiving_and_voiding_bring_the_shortfall_back_by_what_each_removed(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='10')])
    po = _buy(books, order)['purchase_order']
    line = po['revision']['lines'][0]
    amended = run('purchase-order update', dict(purchase_order=po['id'], expected_version=po['version'],
        lines=[dict(line_id=line['line_id'], item=shop['widget'], quantity='3', rate='1.60')]), reason='Only three')
    assert _short(books, order) == {'Widget': '1'}                   # amended down by 1: 1 short again
    run('purchase-order void', dict(purchase_order=po['id'], expected_version=amended['version']), reason='Vendor cancelled')
    assert _short(books, order) == {'Widget': '4'}                   # voided: the 3 still to arrive are short again
    # Receiving: what arrives is on hand, open to every document as on-hand stock is; the link covers the rest.
    po = _buy(books, order)['purchase_order']
    line = po['revision']['lines'][0]
    run('item-receipt post', dict(vendor=books['vendor'], date='2017-03-04', purchase_order=po['id'],
        purchase_order_version=po['version'], items=[dict(item=shop['widget'], quantity='3', unit_cost='1.60',
                                                          order_line_id=line['line_id'])]), reason='Three arrived')
    covered = run('sales-order show', dict(sales_order=order['id']))['revision']['lines'][0]['coverage']
    assert [row['to_arrive'] for row in covered] == ['0', '1'] and _short(books, order) == {}
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '9'
    # Another order now takes that stock first-come: the first order's shortfall comes back by exactly the 3 received.
    other = books['client'].customer.create(company=books['company'], name='Baker Bros')['id']
    _order(books, [dict(item=shop['widget'], quantity='3')], customer=other)
    assert _short(books, order) == {'Widget': '3'}


def test_mixed_vendors_are_named_and_spares_may_be_ordered(books, shop):
    order = _order(books, [dict(item=shop['widget'], quantity='8'), dict(item=shop['gasket'], quantity='5')])
    error = _error(lambda: _buy(books, order))
    vendors = {row['vendor']: [line['item'] for line in row['lines']] for row in error.details['vendors']}
    assert vendors == {'Northside Supply': ['Widget'], 'Eastern Fittings': ['Gasket']}
    first = _buy(books, order, vendor=books['vendor'])
    assert [line['quantity'] for line in first['purchase_order']['revision']['lines']] == ['2']
    assert first['left_for_other_vendors'][0]['item'] == 'Gasket'
    spares = _buy(books, order, vendor=shop['other'], lines=[dict(item=shop['gasket'], quantity='12')])
    assert [line['quantity'] for line in spares['purchase_order']['revision']['lines']] == ['12']
    assert _short(books, order) == {}
