"""R181 increment 1: shipments and the books (design/pick-pack-ship.md §2, §3, §6, §10).

An order ships on one day and is billed on a later one: the units leave stock at the ship date and their cost waits in
Shipped Not Invoiced, an owned asset, until the invoice moves it to cost of goods sold. Hand-worked postings for each
event; the done-when walk; committed as to ship; the order figures; corrections, voids and refusals.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_asset, _inventory_part, _net  # noqa: F401
from tests.test_inventory_sites import _db


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def _sni(books):
    rows = books['client'].account.query(company=books['company'], limit=200)['items']
    return next((row['id'] for row in rows if row['full_name'] == 'Shipped Not Invoiced'), None)


def _balances(books, date):
    net = _net(books, '2017-01-01', date)
    return dict(asset=net.get(_inventory_asset(books), 0), sni=net.get(_sni(books), 0), cogs=net.get(books['cogs'], 0),
                income=net.get(books['income'], 0))


@pytest.fixture
def shop(books):
    run = books['run']
    widget = _inventory_part(books, 'Widget')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(
        item=widget, quantity='10', unit_cost='4.00')]), reason='Ten widgets at 4.00')
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-01', lines=[dict(
        item=widget, quantity='10', unit_price='9.00')]), reason='Adams ordered 10 widgets')
    return dict(books=books, run=run, widget=widget, order=order, line=order['revision']['lines'][0]['line_id'])


def _committed(shop, as_of='2017-12-31'):
    rows = shop['run']('report stock-status', dict(as_of=as_of, limit=50))['rows']
    row = next(row for row in rows if row['item_id'] == shop['widget'])
    return row['quantity_committed'], row['quantity_on_hand']


def _order_line(shop):
    order = shop['run']('sales-order show', dict(sales_order=shop['order']['number']))
    return order['revision']['lines'][0]['order'], order


def test_ten_ordered_six_shipped_on_the_twentieth_and_billed_on_the_twenty_seventh(shop):
    run, books = shop['run'], shop['books']
    preview = run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-20', lines=[dict(
        order_line=shop['line'], quantity='6')]), reason='Truck took six', dry_run=True)
    assert preview['status'] == 'will_ship' and preview['cost']['amount'] == '24.00'
    assert preview['order_to_ship'] == {shop['line']: '4'}
    shipped = run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-20', tracking_numbers=['1Z999'],
                                        lines=[dict(order_line=shop['line'], quantity='6')]), reason='Truck took six')
    assert shipped['status'] == 'shipped' and shipped['number'] == '1001' and shipped['version'] == 1
    assert shipped['cost']['amount'] == '24.00' and shipped['shipped_not_invoiced']['amount'] == '24.00'
    assert shipped['next_commands'] == ['bookflow shipment invoice 1001 --json']
    assert shipped['tracking_numbers'] == ['1Z999']
    # 6 x 4.00 = 24.00 leaves Inventory Asset for Shipped Not Invoiced on the 20th; nothing reaches income or COGS.
    assert _balances(books, '2017-03-19') == dict(asset=4000, sni=0, cogs=0, income=0)
    assert _balances(books, '2017-03-20') == dict(asset=1600, sni=2400, cogs=0, income=0)
    figures, order = _order_line(shop)
    assert (figures['shipped'], figures['shipped_not_invoiced'], figures['to_ship'], figures['open']) == ('6', '6', '4', '10')
    # Committed is what is still to ship: 4, from the 20th; on hand 4.
    assert _committed(shop) == ('4', '4')
    report = run('report shipped-not-invoiced', dict(as_of='2017-03-25'))
    assert report['total'] == report['account_balance'] == {'amount': '24.00', 'currency': 'USD', 'minor_units': 2400}
    assert [(row['shipment_number'], row['quantity'], row['cost']['amount']) for row in report['rows']] == [('1001', '6', '24.00')]

    # An invoice dated before the ship date cannot bill it.
    early = _error(lambda: run('shipment invoice', dict(shipment='1001', date='2017-03-19'), reason='Too early'))
    assert early.code == 'E_VALIDATION' and early.details['ship_date'] == '2017-03-20'
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-27'), reason='Bill the six')
    assert [(line['quantity'], line['net']['amount']) for line in invoice['revision']['lines']] == [('6', '54.00')]
    assert _balances(books, '2017-03-26') == dict(asset=1600, sni=2400, cogs=0, income=0)
    assert _balances(books, '2017-03-27') == dict(asset=1600, sni=0, cogs=2400, income=-5400)
    _, on_hand = _committed(shop)
    assert on_hand == '4'                     # billing a shipment moves no stock
    figures, _ = _order_line(shop)
    assert (figures['invoiced'], figures['shipped_not_invoiced'], figures['to_ship']) == ('6', '0', '4')

    # A purchase backdated before the 20th recosts the shipment at the 20th and the invoice at the 27th:
    # 20 on hand at 5.00 when six shipped, so they cost 30.00.
    run('bill post', dict(vendor=books['vendor'], date='2017-03-10', items=[dict(
        item=shop['widget'], quantity='10', unit_cost='6.00')]), reason='Late paperwork')
    assert _balances(books, '2017-03-20') == dict(asset=7000, sni=3000, cogs=0, income=0)
    assert _balances(books, '2017-03-27') == dict(asset=7000, sni=0, cogs=3000, income=-5400)

    # Voiding the shipment is refused while the invoice bills it, naming the invoice.
    refused = _error(lambda: run('shipment void', dict(shipment='1001'), reason='Never mind'))
    assert refused.code == 'E_WORK_DEPENDENCY' and invoice['number'] in refused.message
    # Voiding the invoice moves the cost back; the order reads shipped not invoiced again.
    current = run('invoice show', dict(invoice=invoice['id']))
    run('invoice void', dict(invoice=invoice['id'], expected_version=current['version']), reason='Billed in error')
    assert _balances(books, '2017-03-27') == dict(asset=7000, sni=3000, cogs=0, income=0)
    figures, _ = _order_line(shop)
    assert (figures['invoiced'], figures['shipped_not_invoiced'], figures['to_ship']) == ('0', '6', '4')
    # And now the shipment voids: the six are back at the 20th and to ship again.
    voided = run('shipment void', dict(shipment='1001'), reason='Never shipped')
    assert voided['status'] == 'voided' and voided['version'] == 2
    assert _balances(books, '2017-12-31') == dict(asset=10000, sni=0, cogs=0, income=0)
    assert _committed(shop) == ('10', '20')
    _tie(books)


def _tie(books):
    """Shipped Not Invoiced ties, and nothing is owed by the derived balance."""
    run = books['run']
    report = run('report shipped-not-invoiced', dict(as_of='2017-12-31'))
    assert report['total'] == report['account_balance']
    with sqlite3.connect(_db(books)) as conn:
        net = dict(conn.execute('SELECT account_id, sum(debit_minor_units - credit_minor_units) FROM posting_lines '
                                'GROUP BY account_id').fetchall())
    assert sum(net.values()) == 0


def test_the_default_ships_what_is_available_and_the_default_invoice_bills_shipped_units_first(shop):
    run, books = shop['run'], shop['books']
    run('invoice post', dict(customer=books['customer'], date='2017-03-02', lines=[dict(
        item='Widget', quantity='7', unit_price='9.00')]), reason='Counter sale')
    shipped = run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-05'), reason='Ship it')
    # Three on hand: three ship and seven stay backordered.
    assert [line['quantity'] for line in shipped['lines']] == ['3']
    assert shipped['order_to_ship'] == {shop['line']: '7'}
    order = run('sales-order show', dict(sales_order=shop['order']['number']))
    invoice = run('sales-order invoice', dict(sales_order=shop['order']['number'], expected_version=order['version'],
                                              conversion_key='bill-shipped', date='2017-03-06'), reason='Bill what went')
    assert [line['quantity'] for line in invoice['revision']['lines']] == ['3']
    assert _committed(shop) == ('7', '0')
    # Nothing left shipped and not invoiced: the default falls back to what can ship (nothing on hand).
    order = run('sales-order show', dict(sales_order=shop['order']['number']))
    nothing = _error(lambda: run('sales-order invoice', dict(
        sales_order=shop['order']['number'], expected_version=order['version'], conversion_key='again',
        date='2017-03-07'), reason='Again'))
    assert nothing.code == 'E_VALIDATION'
    _tie(books)


def test_shipping_past_on_hand_is_provisional_and_the_receipt_trues_it_up(books):
    run = books['run']
    gadget = _inventory_part(books, 'Gadget')
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-01', lines=[dict(
        item=gadget, quantity='5', unit_price='9.00')]), reason='Five gadgets')
    line = order['revision']['lines'][0]['line_id']
    # Nothing on hand: ships at the purchase cost (1.80) provisionally, with a warning.
    shipped = run('shipment post', dict(sales_order=order['number'], date='2017-03-03', lines=[dict(
        order_line=line, quantity='5')]), reason='Shipped ahead of the paperwork')
    assert shipped['cost']['amount'] == '9.00' and shipped['warnings']
    run('bill post', dict(vendor=books['vendor'], date='2017-03-04', items=[dict(
        item=gadget, quantity='5', unit_cost='2.00')]), reason='The receipt')
    # The receipt fills the shortfall: a true-up of 1.00 at the 4th, all in Shipped Not Invoiced (nothing billed).
    assert _balances(books, '2017-03-04')['sni'] == 1000
    run('shipment invoice', dict(shipment=shipped['number'], date='2017-03-05'), reason='Bill')
    assert _balances(books, '2017-03-05') == dict(asset=0, sni=0, cogs=1000, income=-4500)
    _tie(books)


def test_a_line_holding_shipped_units_bills_exactly_them_and_closing_with_them_is_refused(shop):
    run = shop['run']
    run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-05', lines=[dict(
        item='Widget', quantity='2')]), reason='Two went')
    order = run('sales-order show', dict(sales_order=shop['order']['number']))
    mixed = _error(lambda: run('sales-order invoice', dict(
        sales_order=shop['order']['number'], expected_version=order['version'], conversion_key='mixed',
        date='2017-03-06', selections=[dict(line_id=shop['line'], quantity='5')]), reason='Five'))
    assert mixed.code == 'E_VALIDATION' and mixed.details['reason'] == 'shipped_units_first'
    closing = _error(lambda: run('sales-order update', dict(sales_order=order['id'], expected_version=order['version'],
                                                           status='closed'), reason='Done'))
    assert closing.code == 'E_WORK_DEPENDENCY' and '1001' in closing.message
    # Exactly the shipped two bills from the shipment.
    run('sales-order invoice', dict(sales_order=shop['order']['number'], expected_version=order['version'],
                                    conversion_key='two', date='2017-03-06',
                                    selections=[dict(line_id=shop['line'], quantity='2')]), reason='Two')
    assert _committed(shop) == ('8', '8')


def test_updates_repost_and_respect_what_was_billed(shop):
    run, books = shop['run'], shop['books']
    shipped = run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-05', lines=[dict(
        item='Widget', quantity='4')]), reason='Four')
    # Carrier and tracking only: a new revision, the same stock.
    tracked = run('shipment update', dict(shipment='1001', expected_version=1, carrier='UPS Ground',
                                          tracking_numbers=['1Z1']), reason='Tracking')
    assert tracked['version'] == 2 and tracked['carrier'] == 'UPS Ground' and tracked['cost'] == shipped['cost']
    moved = run('shipment update', dict(shipment='1001', expected_version=2, date='2017-03-08',
                                        lines=[dict(item='Widget', quantity='5')]), reason='Five, on the 8th')
    assert moved['date'] == '2017-03-08' and moved['cost']['amount'] == '20.00'
    assert _balances(books, '2017-03-07')['sni'] == 0 and _balances(books, '2017-03-08')['sni'] == 2000
    run('shipment invoice', dict(shipment='1001', date='2017-03-09'), reason='Bill five')
    below = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=3, lines=[dict(
        item='Widget', quantity='3')]), reason='Three'))
    assert below.code == 'E_WORK_DEPENDENCY'
    later = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=3, date='2017-03-10'),
                               reason='Later'))
    assert later.code == 'E_VALIDATION'
    # The journal editor may not touch the shipment's entry or post to the account.
    entry = run('shipment show', dict(shipment='1001'))['transaction_id']
    assert _error(lambda: run('journal void', dict(journal=entry), reason='No')).code == 'E_VALIDATION'
    assert _error(lambda: run('journal post', dict(date='2017-03-10', lines=[
        dict(account=_sni(books), side='debit', amount='1.00'),
        dict(account=books['cogs'], side='credit', amount='1.00')]), reason='No')).code == 'E_VALIDATION'
    _tie(books)


def test_a_recost_reaching_an_invoice_in_a_closed_period_is_refused(shop):
    run, books = shop['run'], shop['books']
    run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-20', lines=[dict(
        item='Widget', quantity='6')]), reason='Six')
    run('shipment invoice', dict(shipment='1001', date='2017-03-27'), reason='Bill')
    run('company update', dict(closing_date='2017-03-31'), reason='March closed')
    # A purchase backdated before the ship date would recost the shipment and the invoice, both closed.
    refused = _error(lambda: run('bill post', dict(vendor=books['vendor'], date='2017-03-15', items=[dict(
        item=shop['widget'], quantity='1', unit_cost='6.00')]), reason='Late'))
    assert refused.code == 'E_PERIOD_CLOSED'
    # Voiding the invoice in the closed period is refused too; the books stay as closed.
    invoice = run('shipment show', dict(shipment='1001'))['invoices'][0]
    current = run('invoice show', dict(invoice=invoice['invoice_id']))
    assert _error(lambda: run('invoice void', dict(invoice=invoice['invoice_id'], expected_version=current['version']),
                              reason='No')).code == 'E_PERIOD_CLOSED'
    assert _balances(books, '2017-03-31') == dict(asset=1600, sni=0, cogs=2400, income=-5400)


def test_a_credit_memo_returning_billed_shipped_goods_is_refused_cleanly_until_returns_arrive(shop):
    run = shop['run']
    run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-05', lines=[dict(
        item='Widget', quantity='2')]), reason='Two')
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-06'), reason='Bill')
    line = invoice['revision']['lines'][0]['line_id']
    refused = _error(lambda: run('credit-memo post', dict(customer=shop['books']['customer'], date='2017-03-07', lines=[
        dict(quantity='1', source_invoice=invoice['id'], source_line=line)]), reason='One came back'))
    assert refused.code == 'E_VALIDATION' and refused.details['shipments'] == ['1001']
    _tie(shop['books'])


def test_a_zero_price_invoice_billing_a_shipment_still_moves_its_cost(books):
    """Review finding: an invoice of total zero posts no line, and its billing must still reach the derived balance."""
    run = books['run']
    widget = _inventory_part(books, 'Free Widget')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(
        item=widget, quantity='10', unit_cost='5.00')]), reason='In')
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-01', lines=[dict(
        item=widget, quantity='2', unit_price='0.00')]), reason='Two free samples')
    shipped = run('shipment post', dict(sales_order=order['number'], date='2017-03-05'), reason='Samples went')
    invoice = run('shipment invoice', dict(shipment=shipped['number'], date='2017-03-06'), reason='Bill at nothing')
    assert _balances(books, '2017-03-06')['sni'] == 0 and _balances(books, '2017-03-06')['cogs'] == 1000
    current = run('invoice show', dict(invoice=invoice['id']))
    run('invoice void', dict(invoice=invoice['id'], expected_version=current['version']), reason='Not billed')
    assert _balances(books, '2017-03-31')['sni'] == 1000 and _balances(books, '2017-03-31')['cogs'] == 0
    _tie(books)


def test_a_billed_shipment_takes_other_serial_numbers_and_an_invoice_keeps_its_date_after_the_ship_date(books):
    """Review findings: correcting a billed shipment's numbers is valid paperwork; an invoice update may not date the
    invoice before the shipment it bills."""
    from tests.test_bins_cycle_counts import _part as _tracked
    run = books['run']
    heater = _tracked(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(
        item=heater, quantity='4', unit_cost='100.00', serial_numbers=['WH-1', 'WH-2', 'WH-3', 'WH-4'])]), reason='In')
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-01', lines=[dict(
        item=heater, quantity='2', unit_price='300.00')]), reason='Two heaters')
    run('shipment post', dict(sales_order=order['number'], date='2017-03-05', lines=[dict(
        item=heater, quantity='2', serial_numbers=['WH-1', 'WH-2'])]), reason='Two went')
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-06'), reason='Bill')
    swapped = run('shipment update', dict(shipment='1001', expected_version=1, lines=[dict(
        item=heater, quantity='2', serial_numbers=['WH-3', 'WH-4'])]), reason='It was the other two')
    assert swapped['version'] == 2 and swapped['lines'][0]['serial_numbers'] == ['WH-3', 'WH-4']
    assert swapped['lines'][0]['invoiced'] == '2'
    current = run('invoice show', dict(invoice=invoice['id']))
    early = _error(lambda: run('invoice update', dict(invoice=invoice['id'], expected_version=current['version'],
                                                      date='2017-03-04'), reason='Earlier'))
    assert early.code == 'E_VALIDATION' and early.details['ship_date'] == '2017-03-05'
    _tie(books)
