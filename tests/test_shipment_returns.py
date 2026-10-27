"""R181 increment 2: returns and corrections (design/pick-pack-ship.md §6, §10).

Units a shipment took and no invoice billed come back on a shipment return: into stock at its date, to the bin they
left, worth their share of the shipment line's cost, out of Shipped Not Invoiced; to ship again on the order, or
cancelled on it. Units an invoice billed come back on a credit memo naming the invoice line, whose place, numbers and
cost come from the shipment line, and the order stays invoiced. Every row of the correction table, hand-worked.
"""
import sqlite3

import pytest

from tests.test_bill_item_lines import books, _inventory_part, _net  # noqa: F401
from tests.test_bins_cycle_counts import _bins, _part as _tracked
from tests.test_shipments import _balances, _committed, _error, _order_line, _sni, _tie, shop  # noqa: F401
from tests.test_inventory_sites import _db


def _ship(shop, quantity='6', date='2017-03-20', **extra):
    return shop['run']('shipment post', dict(sales_order=shop['order']['number'], date=date, lines=[dict(
        order_line=shop['line'], quantity=quantity, **extra)]), reason=f'Shipped {quantity}')


def test_one_of_six_comes_back_to_ship_again_and_the_account_falls_by_its_share(shop):
    run, books = shop['run'], shop['books']
    _ship(shop)
    preview = run('shipment return', dict(shipment='1001', date='2017-03-22', lines=[dict(
        shipment_line='1', quantity='1')]), reason='One came back', dry_run=True)
    assert preview['status'] == 'will_return' and preview['value']['amount'] == '4.00'
    assert preview['order_to_ship'] == {shop['line']: '5'}
    back = run('shipment return', dict(shipment='1001', date='2017-03-22', lines=[dict(
        shipment_line='1', quantity='1')]), reason='One came back')
    assert (back['number'], back['status'], back['version'], back['reopen']) == ('1001', 'returned', 1, True)
    assert back['value']['amount'] == '4.00' and back['lines'][0]['quantity'] == '1'
    assert back['next_commands'] == [f'bookflow shipment post --sales-order {shop["order"]["number"]} --json']
    # 4.00 leaves Shipped Not Invoiced for Inventory Asset on the 22nd, nothing before.
    assert _balances(books, '2017-03-21') == dict(asset=1600, sni=2400, cogs=0, income=0)
    assert _balances(books, '2017-03-22') == dict(asset=2000, sni=2000, cogs=0, income=0)
    figures, _ = _order_line(shop)
    assert (figures['shipped'], figures['shipped_not_invoiced'], figures['to_ship']) == ('5', '5', '5')
    assert _committed(shop) == ('5', '5')
    shown = run('shipment show', dict(shipment='1001'))
    line = shown['lines'][0]
    assert (line['quantity'], line['returned'], line['not_invoiced'], line['waiting']['amount']) == ('6', '1', '5', '20.00')
    assert [(r['number'], r['status'], r['quantity']) for r in shown['returns']] == [('1001', 'returned', '1')]
    report = run('report shipped-not-invoiced', dict(as_of='2017-03-25'))
    assert [(r['quantity'], r['cost']['amount']) for r in report['rows']] == [('5', '20.00')]

    # More than is out is refused, and so is a date before the ship date.
    over = _error(lambda: run('shipment return', dict(shipment='1001', date='2017-03-23', lines=[dict(
        shipment_line='1', quantity='6')]), reason='Too many'))
    assert over.code == 'E_VALIDATION' and over.details['reason'] == 'return_exceeds_out'
    early = _error(lambda: run('shipment return', dict(shipment='1001', date='2017-03-19'), reason='Too early'))
    assert early.code == 'E_VALIDATION' and early.details['ship_date'] == '2017-03-20'

    # The invoice bills the five still out; the order reads invoiced 5, to ship 5.
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-27'), reason='Bill the five')
    assert [line['quantity'] for line in invoice['revision']['lines']] == ['5']
    assert _balances(books, '2017-03-27') == dict(asset=2000, sni=0, cogs=2000, income=-4500)
    nothing = _error(lambda: run('shipment return', dict(shipment='1001', date='2017-03-28'), reason='Nothing left'))
    assert nothing.code == 'E_VALIDATION' and nothing.details['reason'] == 'nothing_to_return'

    # The shipment cannot be voided or restated while its return stands; a text change still writes a revision.
    refused = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=1, date='2017-03-21'),
                                 reason='Moved'))
    assert refused.code == 'E_WORK_DEPENDENCY' and 'shipment return 1001' in refused.message
    tracked = run('shipment update', dict(shipment='1001', expected_version=1, tracking_numbers=['1Z9']),
                  reason='Tracking')
    assert tracked['version'] == 2 and tracked['tracking_numbers'] == ['1Z9'] and tracked['lines'][0]['returned'] == '1'

    # Voiding the return sends the unit back out, shipped and not invoiced, at the 22nd.
    voided = run('shipment-return void', dict(shipment_return='1001', expected_version=1), reason='Never came back')
    assert (voided['status'], voided['version']) == ('voided', 2)
    assert _balances(books, '2017-03-22') == dict(asset=1600, sni=2400, cogs=0, income=0)
    assert _balances(books, '2017-03-27') == dict(asset=1600, sni=400, cogs=2000, income=-4500)
    figures, _ = _order_line(shop)
    assert (figures['shipped'], figures['shipped_not_invoiced'], figures['to_ship']) == ('6', '1', '4')
    assert [r['status'] for r in run('shipment-return query', dict(shipment='1001'))['rows']] == ['voided']
    _tie(books)


def test_reopen_false_cancels_the_returned_units_on_the_order_and_a_void_restores_them(shop):
    run, books = shop['run'], shop['books']
    _ship(shop)
    back = run('shipment return', dict(shipment='1001', date='2017-03-22', reopen=False, lines=[dict(
        shipment_line='1', quantity='2')]), reason='Two came back; customer no longer wants them')
    assert back['reopen'] is False and back['value']['amount'] == '8.00' and back['next_commands'] == []
    figures, _ = _order_line(shop)
    assert (figures['shipped'], figures['cancelled'], figures['to_ship']) == ('4', '2', '4')
    assert _committed(shop) == ('4', '6')
    assert _balances(books, '2017-03-22') == dict(asset=2400, sni=1600, cogs=0, income=0)
    run('shipment-return void', dict(shipment_return='1001'), reason='Entered in error')
    figures, _ = _order_line(shop)
    assert (figures['shipped'], figures['cancelled'], figures['to_ship']) == ('6', '0', '4')
    assert _balances(books, '2017-03-31') == dict(asset=1600, sni=2400, cogs=0, income=0)
    _tie(books)


def test_a_void_is_refused_once_the_reopened_units_shipped_again(shop):
    run = shop['run']
    _ship(shop, quantity='10')
    run('shipment return', dict(shipment='1001', date='2017-03-22', lines=[dict(shipment_line='1', quantity='3')]),
        reason='Three back')
    run('shipment post', dict(sales_order=shop['order']['number'], date='2017-03-23'), reason='Sent again')
    refused = _error(lambda: run('shipment-return void', dict(shipment_return='1001'), reason='No'))
    assert refused.code == 'E_WORK_DEPENDENCY' and refused.details['reason'] == 'return_reopened_units_taken'
    voided = _error(lambda: run('shipment void', dict(shipment='1001'), reason='No'))
    assert voided.code == 'E_WORK_DEPENDENCY' and voided.details['reason'] == 'shipment_returned'
    _tie(shop['books'])


def test_a_return_after_a_recost_takes_its_share_of_the_new_cost_and_moves_with_later_ones(shop):
    run, books = shop['run'], shop['books']
    _ship(shop)                                                      # 6 at 4.00 = 24.00 on the 20th
    run('bill post', dict(vendor=books['vendor'], date='2017-03-10', items=[dict(
        item=shop['widget'], quantity='10', unit_cost='6.00')]), reason='Late paperwork')   # 6 at 5.00 = 30.00
    back = run('shipment return', dict(shipment='1001', date='2017-03-22', lines=[dict(
        shipment_line='1', quantity='2')]), reason='Two back')
    assert back['value']['amount'] == '10.00'
    assert _balances(books, '2017-03-22')['sni'] == 2000
    run('shipment invoice', dict(shipment='1001', date='2017-03-27'), reason='Bill four')
    assert _balances(books, '2017-03-27') == dict(asset=8000, sni=0, cogs=2000, income=-3600)
    # A purchase backdated again recosts the issue, the return and the invoice's share together: 30 at 4.33...
    run('bill post', dict(vendor=books['vendor'], date='2017-03-05', items=[dict(
        item=shop['widget'], quantity='10', unit_cost='3.00')]), reason='Even later paperwork')
    shown = run('shipment-return show', dict(shipment_return='1001'))
    net = _net(books, '2017-01-01', '2017-03-31')
    assert net.get(_sni(books), 0) == 0
    cost = int(round(float(run('shipment show', dict(shipment='1001'))['lines'][0]['cost']['amount']) * 100))
    value = int(round(float(shown['value']['amount']) * 100))
    assert net.get(books['cogs'], 0) == cost - value
    _tie(books)


def test_a_returned_serial_number_is_back_in_stock_at_its_bin_and_numbers_not_shipped_are_refused(books):
    run = books['run']
    run('inventory-site update', dict(site='Main', tracks_bins=True), reason='Bins')
    run('bin create', dict(site='Main', name='A1'), reason='Shelf')
    heater = _tracked(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(
        item=heater, quantity='3', unit_cost='100.00', bin='A1', serial_numbers=['WH-1', 'WH-2', 'WH-3'])]),
        reason='In')
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-01', lines=[dict(
        item=heater, quantity='2', unit_price='300.00')]), reason='Two heaters')
    run('shipment post', dict(sales_order=order['number'], date='2017-03-05', lines=[dict(
        item=heater, quantity='2', bin='A1', serial_numbers=['WH-1', 'WH-2'])]), reason='Two went')
    assert ('Water Heater', 'Main', 'A1', 'WH-1') not in _bins(books)
    stranger = _error(lambda: run('shipment return', dict(shipment='1001', date='2017-03-06', lines=[dict(
        item=heater, quantity='1', serial_numbers=['WH-3'])]), reason='Not ours'))
    assert stranger.code == 'E_VALIDATION'
    back = run('shipment return', dict(shipment='1001', date='2017-03-06', lines=[dict(
        item=heater, quantity='1', serial_numbers=['WH-2'])]), reason='WH-2 came back')
    assert back['lines'][0]['serial_numbers'] == ['WH-2'] and back['lines'][0]['bin'] == 'A1'
    assert back['value']['amount'] == '100.00'
    assert _bins(books)[('Water Heater', 'Main', 'A1', 'WH-2')] == 1
    _tie(books)


def test_an_invoiced_unit_comes_back_on_a_credit_memo_from_the_shipment_line_and_the_order_stays_invoiced(shop):
    run, books = shop['run'], shop['books']
    _ship(shop, quantity='2', date='2017-03-05')
    _ship(shop, quantity='3', date='2017-03-06')
    invoice = run('sales-order invoice', dict(sales_order=shop['order']['number'],
                                              expected_version=_order_line(shop)[1]['version'],
                                              conversion_key='both', date='2017-03-07'), reason='Bill both')
    line = invoice['revision']['lines'][0]
    assert line['quantity'] == '5'
    assert _balances(books, '2017-03-07') == dict(asset=2000, sni=0, cogs=2000, income=-4500)
    # One credited line returns units of one shipment line: 3 of the 5 would span both shipments.
    spans = _error(lambda: run('credit-memo post', dict(customer=books['customer'], date='2017-03-08', lines=[
        dict(quantity='3', source_invoice=invoice['id'], source_line=line['line_id'])]), reason='Three back'))
    assert spans.code == 'E_VALIDATION' and spans.details['reason'] == 'return_spans_shipments'
    credit = run('credit-memo post', dict(customer=books['customer'], date='2017-03-08', lines=[
        dict(quantity='1', source_invoice=invoice['id'], source_line=line['line_id'])]), reason='One came back')
    # Dr Inventory Asset, Cr Cost of Goods Sold, 4.00; Shipped Not Invoiced untouched; the order stays invoiced.
    assert _balances(books, '2017-03-08') == dict(asset=2400, sni=0, cogs=1600, income=-3600)
    figures, _ = _order_line(shop)
    assert (figures['invoiced'], figures['shipped'], figures['to_ship']) == ('5', '5', '5')
    assert _committed(shop)[1] == '6'
    # The shipment that unit left on cannot be voided or restated while the credit memo stands.
    restate = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=1, date='2017-03-04'),
                                 reason='Moved'))
    assert restate.code == 'E_WORK_DEPENDENCY' and 'credit memo' in restate.message
    run('credit-memo void', dict(credit_memo=credit['id'], expected_version=credit['version']), reason='Error')
    assert _balances(books, '2017-03-31') == dict(asset=2000, sni=0, cogs=2000, income=-4500)
    _tie(books)


def test_the_correction_table_row_by_row(shop):
    """design §6: packing facts, quantity down and up, place, date and void, not invoiced and invoiced."""
    run, books = shop['run'], shop['books']
    _ship(shop, quantity='4', date='2017-03-05')                     # 4 x 4.00 = 16.00
    entry = run('shipment show', dict(shipment='1001'))['transaction_id']
    with sqlite3.connect(_db(books)) as conn:
        before = conn.execute('SELECT count(*) FROM posting_lines').fetchone()[0]
    # Packing facts: a new revision, nothing reposts.
    facts = run('shipment update', dict(shipment='1001', expected_version=1, carrier='UPS', memo='Dock 3',
                                        tracking_numbers=['1Z1', '1Z2']), reason='Carrier')
    assert (facts['version'], facts['carrier'], facts['memo'], facts['tracking_numbers']) == (2, 'UPS', 'Dock 3', ['1Z1', '1Z2'])
    with sqlite3.connect(_db(books)) as conn:
        assert conn.execute('SELECT count(*) FROM posting_lines').fetchone()[0] == before
    assert run('shipment show', dict(shipment='1001'))['transaction_id'] == entry
    # Quantity down, not invoiced: the units go back at the ship date.
    down = run('shipment update', dict(shipment='1001', expected_version=2, lines=[dict(item='Widget', quantity='3')]),
               reason='Three')
    assert down['cost']['amount'] == '12.00' and _balances(books, '2017-03-05') == dict(asset=2800, sni=1200, cogs=0, income=0)
    # Quantity up: allowed while the order has that much to ship; more is refused.
    up = run('shipment update', dict(shipment='1001', expected_version=3, lines=[dict(item='Widget', quantity='8')]),
             reason='Eight')
    assert up['cost']['amount'] == '32.00'
    too_many = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=4, lines=[dict(
        item='Widget', quantity='11')]), reason='Eleven'))
    assert too_many.code == 'E_VALIDATION'
    # Date, not invoiced: reposts the shipment.
    moved = run('shipment update', dict(shipment='1001', expected_version=4, date='2017-03-06'), reason='The 6th')
    assert moved['date'] == '2017-03-06' and _balances(books, '2017-03-05')['sni'] == 0
    assert _balances(books, '2017-03-06')['sni'] == 3200
    # Invoiced: quantity down below the billed refused naming the invoice; packing facts and an earlier date fine.
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-07'), reason='Bill eight')
    assert _balances(books, '2017-03-07') == dict(asset=800, sni=0, cogs=3200, income=-7200)
    below = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=5, lines=[dict(
        item='Widget', quantity='4')]), reason='Four'))
    assert below.code == 'E_WORK_DEPENDENCY' and invoice['number'] in below.message
    later = _error(lambda: run('shipment update', dict(shipment='1001', expected_version=5, date='2017-03-08'),
                               reason='Later'))
    assert later.code == 'E_VALIDATION'
    earlier = run('shipment update', dict(shipment='1001', expected_version=5, date='2017-03-04'), reason='Earlier')
    assert earlier['date'] == '2017-03-04' and _balances(books, '2017-03-04')['sni'] == 3200
    assert _balances(books, '2017-03-07') == dict(asset=800, sni=0, cogs=3200, income=-7200)
    tracked = run('shipment update', dict(shipment='1001', expected_version=6, tracking_numbers=['1Z3']),
                  reason='Tracking')
    assert tracked['version'] == 7
    # Void, invoiced: refused naming the invoice; voiding the invoice first lets it void.
    refused = _error(lambda: run('shipment void', dict(shipment='1001'), reason='No'))
    assert refused.code == 'E_WORK_DEPENDENCY' and invoice['number'] in refused.message
    current = run('invoice show', dict(invoice=invoice['id']))
    run('invoice void', dict(invoice=invoice['id'], expected_version=current['version']), reason='Error')
    run('shipment void', dict(shipment='1001'), reason='Never went')
    assert _balances(books, '2017-12-31') == dict(asset=4000, sni=0, cogs=0, income=0)
    figures, _ = _order_line(shop)
    assert figures.get('to_ship', figures['open']) == '10'
    _tie(books)


def test_invoice_when_shipping_is_a_company_preference(shop):
    run = shop['run']
    assert run('company show', {})['info']['invoice_when_shipping'] == 'ask'
    run('company update', dict(invoice_when_shipping='never'), reason='We bill at month end')
    assert run('company show', {})['info']['invoice_when_shipping'] == 'never'
    shipped = _ship(shop, quantity='2')
    assert shipped['next_commands'] == []
    assert _error(lambda: run('company update', dict(invoice_when_shipping='sometimes'), reason='No')).code == 'E_VALIDATION'
    run('company update', dict(invoice_when_shipping='always'), reason='Bill on ship')
    assert run('shipment show', dict(shipment='1001'))['next_commands'] == ['bookflow shipment invoice 1001 --json']


def test_cash_basis_reads_a_billed_shipments_cost_as_the_invoice_lines_own(shop):
    """Increment 1 left this: the cost a billed shipment moves (`shipment_cost` entries) is deferred on the cash basis
    by the paid fraction of the invoice line that billed it, exactly as a stock-moving invoice's cost line is."""
    run, books = shop['run'], shop['books']
    _ship(shop, quantity='6', date='2017-03-20')
    invoice = run('shipment invoice', dict(shipment='1001', date='2017-03-27'), reason='Bill six')   # 54.00, cost 24.00

    def pnl(basis='cash', last='2017-04-30'):
        out = run('report profit-and-loss', dict(date_from='2017-03-01', date_to=last, basis=basis, limit=200))
        return out['totals']['cost_of_goods_sold']['minor_units'], out['totals']['income']['minor_units']

    assert pnl('accrual') == (2400, 5400)
    assert pnl() == (0, 0)                                      # nothing paid: nothing recognised
    run('payment receive', dict(customer=books['customer'], date='2017-04-02', amount='27.00', operation_key='half',
        deposit_to=books['bank'], payment_method=books['methods']['Cash'], applications={'mode': 'inline', 'items': [
            dict(invoice=invoice['id'], amount='27.00', expected_version=invoice['version'])]}), reason='Half paid')
    assert pnl() == (1200, 2700)
    assert pnl(last='2017-03-31') == (0, 0)
