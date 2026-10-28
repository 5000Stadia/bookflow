"""R181 increment 5: the fulfillment worksheet and the shipments report (design/pick-pack-ship.md §2, §8, §10).

Can ship now is what `shipment post --sales-order <n>` would ship: every figure here is checked against hand-worked
numbers and against that command's own dry run, at the order's site and at each site asked for. A receipt makes a
short order newly shippable; the statuses follow picking and packing; sorts and filters narrow the same rows; the
shipments report lists boxes, weights and tracking numbers. Worksheet time is measured on the demo and on 1,000 open
order lines.
"""
import time
from fractions import Fraction

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_pick_lists import _item


def would_ship(run, number, site=None):
    """``{order line id: base quantity}`` the dry run of `shipment post` from the order alone ships; {} if refused."""
    body = dict(sales_order=number, **({'site': site} if site else {}))
    try:
        preview = run('shipment post', body, reason='Would it ship', dry_run=True)
    except BookflowError as error:
        assert error.code in ('E_VALIDATION', 'E_WORK_DEPENDENCY'), error
        return {}
    out = {}
    for line in preview['lines']:
        out[line['order_line_id']] = out.get(line['order_line_id'], 0) + Fraction(line['quantity'])
    return out


def can_ship(row):
    return {line['order_line_id']: Fraction(line['can_ship_now']) for line in row['lines']
            if Fraction(line['can_ship_now'])}


def sheet(run, **filters):
    return run('report order-fulfillment', dict(limit=200, **filters))


@pytest.fixture
def two_sites(books):
    """A warehouse and an annex. Widgets 10 on hand at the warehouse, gadgets 1 at the annex. Acme orders 3 widgets (any site) and 2 gadgets from the
    annex; Adams, a day later, 9 widgets."""
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='Our warehouse')
    run('inventory-site create', dict(name='Annex'), reason='Second shed')
    widget, gadget = _item(books, 'Widget'), _item(books, 'Gadget', cost='4.00')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=widget, quantity='10', unit_cost='4.00'), dict(item=gadget, quantity='1', unit_cost='2.00', site='Annex')]),
        reason='Stock in')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    first = run('sales-order create', dict(customer=acme, date='2017-03-02', ship_date='2017-03-04', lines=[
        dict(item=widget, quantity='3', unit_price='30.00'),
        dict(item=gadget, quantity='2', unit_price='9.00', site='Annex')]), reason='Acme ordered')
    second = run('sales-order create', dict(customer=books['customer'], date='2017-03-03', lines=[
        dict(item=widget, quantity='9', unit_price='30.00')]), reason='Adams ordered')
    return dict(books=books, run=run, widget=widget, gadget=gadget, acme=acme, first=first, second=second)


def _rows(report):
    return {row['sales_order_number']: row for row in report['rows']}


def test_can_ship_now_by_site_matches_hand_worked_figures_and_the_dry_run(two_sites):
    run, first, second = two_sites['run'], two_sites['first'], two_sites['second']
    report = sheet(run, as_of='2017-03-06')
    rows = _rows(report)
    a, b = rows[first['number']], rows[second['number']]
    # Availability is by site: the widgets are all at the warehouse, the gadget at the annex.
    # Widgets at the warehouse: 10 on hand, Adams commits 9, so 1 is available to Acme; Acme commits 3, so 7 to Adams.
    # Acme's only line naming a site names the annex, so `shipment post` ships from there: no widget, 1 gadget.
    assert a['site_name'] == 'Annex' and b['site_name'] == 'Warehouse'
    widget_line, gadget_line = (line['order_line_id'] for line in a['lines'])
    assert can_ship(a) == {gadget_line: 1}
    assert can_ship(b) == {b['lines'][0]['order_line_id']: 7}
    assert a['shippable_amount']['amount'] == '9.00' and b['shippable_amount']['amount'] == '210.00'
    assert a['to_ship_amount']['amount'] == '108.00' and not a['can_ship_complete']
    assert a['fulfillment_status'] == 'to_pick' and a['invoice_progress'] == 'open'
    assert a['promised_ship_date'] == '2017-03-04' and a['days_late'] == 2 and b['days_late'] is None
    by_site = {site['site_name']: site for site in a['by_site']}
    assert by_site['Annex']['lines'] == 1 and by_site['Warehouse']['lines'] == 1
    assert by_site['Warehouse']['shippable_amount']['amount'] == '30.00'
    for row in (a, b):
        assert can_ship(row) == would_ship(run, row['sales_order_number'])
        for site in row['by_site']:
            at = _rows(sheet(run, site=site['site_name']))
            assert can_ship(at[row['sales_order_number']]) == would_ship(run, row['sales_order_number'],
                                                                           site['site_name'])
    # At the warehouse Acme's gadget line, which ships from the annex, has nothing.
    warehouse = _rows(sheet(run, site='Warehouse'))[first['number']]
    assert can_ship(warehouse) == {widget_line: 1} and warehouse['site_name'] == 'Warehouse'
    assert report['totals']['orders'] == 2 and report['totals']['shippable_amount']['amount'] == '219.00'


def test_a_receipt_makes_a_short_order_newly_shippable(two_sites):
    run, first = two_sites['run'], two_sites['first']
    assert not sheet(run, newly_shippable='2017-03-04')['rows']
    run('bill post', dict(vendor=two_sites['books']['vendor'], date='2017-03-05', items=[
        dict(item=two_sites['gadget'], quantity='1', unit_cost='2.00', site='Annex')]), reason='Gadget came')
    newly = sheet(run, newly_shippable='2017-03-04')['rows']
    assert [row['sales_order_number'] for row in newly] == [first['number']]
    assert Fraction(newly[0]['lines'][1]['can_ship_now']) == 2
    # Since the day the gadget came nothing is new.
    assert not sheet(run, newly_shippable='2017-03-05')['rows']


def test_sorts_filters_and_shipping_states(two_sites):
    run, first, second = two_sites['run'], two_sites['first'], two_sites['second']
    numbers = lambda report: [row['sales_order_number'] for row in report['rows']]
    assert numbers(sheet(run)) == [first['number'], second['number']]
    assert numbers(sheet(run, sort='shippable_amount')) == [second['number'], first['number']]
    assert numbers(sheet(run, sort='customer')) == [first['number'], second['number']]   # Acme, Adams
    assert numbers(sheet(run, customer='Adams Plumbing')) == [second['number']]
    assert numbers(sheet(run, complete_only=True)) == []
    run('shipment post', dict(sales_order=second['number'], date='2017-03-05'), reason='Truck')
    rows = _rows(sheet(run))
    assert rows[second['number']]['fulfillment_status'] == 'partly_shipped'
    assert rows[second['number']]['shipped_not_invoiced_amount']['amount'] == '210.00'
    assert numbers(sheet(run, status='partly_shipped')) == [second['number']]
    assert numbers(sheet(run, shipped_not_invoiced=True)) == [second['number']]
    # Adams took 7 of the 10 widgets: Acme can ship nothing more of them now.
    assert numbers(sheet(run, can_ship=True)) == [first['number']]
    run('sales-order update', dict(sales_order=second['number'], expected_version=1, lines=[
        dict(line_id=rows[second['number']]['lines'][0]['order_line_id'], item=two_sites['widget'],
             cancelled_quantity='2')]),
        reason='Two cancelled')
    shipped = _rows(sheet(run))[second['number']]
    assert shipped['fulfillment_status'] == 'shipped' and shipped['can_ship_complete'] is False
    with pytest.raises(BookflowError) as caught:
        sheet(run, status='lost')
    assert caught.value.code == 'E_VALIDATION'


def test_picking_and_packing_states_and_an_order_a_list_holds(two_sites):
    run, books, second = two_sites['run'], two_sites['books'], two_sites['second']
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick')
    assert sheet(run)['pick_and_pack'] is True
    made = run('pick-list create', dict(orders=[second['number']], site='Warehouse', date='2017-03-05'),
               reason='Pick')
    row = _rows(sheet(run))[second['number']]
    assert row['fulfillment_status'] == 'picking' and row['pick_lists'] == [made['number']]
    assert can_ship(row) == {} == would_ship(run, second['number']) and made['number'] in row['note']
    run('pick-list enter', dict(pick_list=made['number'], entries=[dict(item=two_sites['widget'], quantity='7')]),
        reason='Picked')
    row = _rows(sheet(run))[second['number']]
    assert row['fulfillment_status'] == 'picked' and row['lines'][0]['picked'] == '7'
    run('shipment post', dict(pick_list=made['number'], date='2017-03-05'), reason='Shipped what was picked')
    draft = run('shipment create', dict(sales_order=two_sites['first']['number'], site='Warehouse'), reason='Draft')
    row = _rows(sheet(run))[two_sites['first']['number']]
    assert row['drafts'] == [draft['number']] and row['lines'][0]['drafted'] == '1'
    assert row['fulfillment_status'] == 'to_pick'
    assert can_ship(row) == would_ship(run, two_sites['first']['number'])
    run('shipment pack', dict(shipment=draft['number'], line='1', box=1), reason='Boxed')
    row = _rows(sheet(run))[two_sites['first']['number']]
    assert row['fulfillment_status'] == 'packed' and row['lines'][0]['packed'] == '1'


def test_the_shipments_report_lists_boxes_weights_and_tracking(two_sites):
    run, first, second = two_sites['run'], two_sites['first'], two_sites['second']
    run('company update', dict(pick_and_pack_enabled=True), reason='We pack')
    run('item update', dict(item=two_sites['widget'], unit_weight='2.5'), reason='Weighed')
    run('shipment post', dict(sales_order=second['number'], date='2017-03-05', tracking_numbers=['1Z1'],
                              packages=[dict(box=1, tracking_number='1Z2', tare='1', contents=[dict(line='1')])]),
        reason='Truck')
    run('shipment post', dict(sales_order=first['number'], date='2017-03-06', site='Annex'), reason='Van')
    report = run('report shipments', dict(date_from='2017-03-01', date_to='2017-03-31'))
    assert [row['sales_order_number'] for row in report['rows']] == [second['number'], first['number']]
    truck = report['rows'][0]
    assert truck['tracking_numbers'] == ['1Z1', '1Z2'] and truck['boxes'] == 1 and truck['quantity'] == '7'
    assert truck['weight'] == '18.5' and truck['packages'][0]['weight'] == '18.5'
    assert report['totals'] == dict(shipments=2, boxes=1, weight=report['totals']['weight'])
    assert [row['site_name'] for row in run('report shipments', dict(date_to='2017-03-31', site='Annex'))['rows']] \
        == ['Annex']
    assert run('report shipments', dict(date_to='2017-03-05', customer='Acme'))['rows'] == []
    assert run('report shipments', dict(date_to='2017-03-04'))['rows'] == []


def _timed(run, **filters):
    started = time.perf_counter()
    report = sheet(run, **filters)
    return report, time.perf_counter() - started


def test_worksheet_time_on_the_demo(client):
    run = lambda name, raw: client.run(name, raw, company='Demo Plumbing Co')
    report, seconds = _timed(run)
    print(f'worksheet on the demo: {len(report["rows"])} orders in {seconds:.3f}s')
    for row in report['rows']:
        assert can_ship(row) == would_ship(lambda n, r, **k: client.run(n, r, company='Demo Plumbing Co', **k),
                                           row['sales_order_number'])


@pytest.mark.timeout(900)
def test_worksheet_time_on_a_thousand_open_order_lines(books):
    run = books['run']
    items = [_item(books, f'Part {n}') for n in range(20)]
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=item, quantity='300', unit_cost='4.00') for item in items]), reason='Stock in')
    for n in range(200):
        run('sales-order create', dict(customer=books['customer'], date='2017-03-02', lines=[
            dict(item=items[(n + k) % 20], quantity=str(1 + (n + k) % 4), unit_price='30.00') for k in range(5)]),
            reason='Ordered')
    report, seconds = _timed(run)
    assert report['totals']['orders'] == 200 and len(report['rows']) == 200
    print(f'worksheet on 1,000 open order lines: {seconds:.3f}s')
    assert seconds < 30   # measured 2.5 s; a bound for a regression an order of magnitude worse, not a target
