"""R102: committed stock (sales-orders design §3, increment 3).

One fixture holds a document in every state against hand-worked figures: open orders, a partly
invoiced order, a complete but uninvoiced work order, an order dated after the report date, and a
cancelled work order, a voided order and a closed order that commit nothing. Committed equals the
open stocked quantity across them; switching the preference moves available and the reorder flag
and nothing else; a company with nothing committed reports exactly as before; the report pages by
customer and by item; selling or ordering past what is available warns and proceeds.
"""
import time

import pytest

from tests.test_bill_item_lines import books, _inventory_part  # noqa: F401


def _stock(books, item, as_of='2017-12-31'):
    rows = books['run']('report stock-status', dict(as_of=as_of, limit=200))
    return next(row for row in rows['rows'] if row['item_id'] == item), rows


def _order(books, item, quantity, date, **extra):
    line = dict(item=item, quantity=quantity, **extra)
    return books['run']('sales-order create', dict(customer=books['customer'], date=date, lines=[line]),
                        reason='Order')


@pytest.fixture
def stocked(books):
    run = books['run']
    widget = _inventory_part(books, 'Widget')
    run('item update', dict(item=widget, reorder_point_min='10'), reason='Reorder at ten')
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='20', unit_cost='1.80')]),
        reason='Stock up')
    before, _ = _stock(books, widget)
    other = books['client'].customer.create(company=books['company'], name='Baker Bros')['id']
    a = _order(books, widget, '10', '2017-03-01', cancelled_quantity='2')            # open 8
    b = run('sales-order create', dict(customer=other, date='2017-03-05',
                                       lines=[dict(item=widget, quantity='5')]), reason='Order')   # open 5
    c = _order(books, widget, '3', '2017-04-10')                                     # open 3, dated later
    work = run('work-order create', dict(customer=books['customer'], date='2017-03-02', title='Fit four',
                                        lines=[dict(item=widget, quantity='4')]), reason='Job')
    run('work-order complete', dict(work_order=work['id'], expected_version=1,
                                    actual_start='2017-03-03T08:00:00Z'), reason='Done')         # complete, uninvoiced: 4
    gone = run('work-order create', dict(customer=books['customer'], date='2017-03-02', title='Called off',
                                        lines=[dict(item=widget, quantity='7')]), reason='Job')
    run('work-order update', dict(work_order=gone['id'], expected_version=1, status='cancelled'), reason='Called off')
    void = _order(books, widget, '6', '2017-03-03')
    run('sales-order void', dict(sales_order=void['id'], expected_version=1), reason='Mistake')
    closed = _order(books, widget, '9', '2017-03-03')
    run('sales-order update', dict(sales_order=closed['id'], expected_version=1, status='closed'), reason='No more')
    # Partly invoiced: an estimate for 6 bills 2, then becomes the order; 4 remain open on it.
    estimate = run('estimate create', dict(customer=books['customer'], date='2017-03-06', title='Six',
                                           lines=[dict(item=widget, quantity='6')]), reason='Quote')
    estimate = run('estimate update', dict(estimate=estimate['id'], expected_version=1, status='accepted',
                                           decision_note='Yes'), reason='Accepted')
    line = estimate['revision']['lines'][0]['line_id']
    run('estimate invoice', dict(estimate=estimate['id'], expected_version=estimate['version'], date='2017-03-07',
                                 conversion_key='two-now', selections=[dict(line_id=line, quantity='2')]), reason='Two now')
    estimate = run('estimate show', dict(estimate=estimate['id']))
    partly = run('estimate sales-order', dict(estimate=estimate['id'], expected_version=estimate['version'],
                                              date='2017-03-06', conversion_key='the-rest'), reason='The rest')
    run('purchase-order post', dict(vendor=books['vendor'], date='2017-03-10', expected_date='2017-04-15',
                                    lines=[dict(item=widget, quantity='5', rate='1.80')]), reason='Order more')
    return dict(widget=widget, before=before, a=a, b=b, c=c, work=work, partly=partly, other=other)


def test_committed_is_the_open_stocked_quantity_across_documents(books, stocked):
    widget = stocked['widget']
    # On hand 20 - 2 invoiced = 18. Committed: A 8 + B 5 + C 3 + work order 4 + the partly invoiced order 4 = 24.
    row, _ = _stock(books, widget)
    assert (row['quantity_on_hand'], row['quantity_committed'], row['quantity_available']) == ('18', '24', '-6')
    assert row['quantity_on_order'] == '5' and row['next_delivery_date'] == '2017-04-15'
    assert row['quantity_for_assemblies'] == '0' and row['below_reorder_point'] is True
    # As of the end of March the order dated 10 April is not yet committed (and the bill-free PO counts as placed).
    march, _ = _stock(books, widget, '2017-03-31')
    assert (march['quantity_committed'], march['quantity_available']) == ('21', '-3')
    shown = books['run']('item show', dict(item=widget))
    assert (shown['quantity_committed'], shown['quantity_available'], shown['quantity_on_order']) == ('24', '-6', '5')
    order = books['run']('sales-order show', dict(sales_order=stocked['a']['id']))['revision']['lines'][0]
    assert order['stock'] == dict(on_hand='18', committed_elsewhere='16', on_order='5', available='2')
    partly = stocked['partly']['revision']['lines'][0]['order']
    assert (partly['invoiced'], partly['open']) == ('2', '4')


def test_switching_the_preference_moves_available_and_nothing_else(books, stocked):
    widget = stocked['widget']
    on, report_on = _stock(books, widget)
    books['run']('company update', dict(available_deducts_sales_orders=False), reason='Show on hand')
    off, report_off = _stock(books, widget)
    assert off['quantity_available'] == off['quantity_on_hand'] == '18' and off['below_reorder_point'] is False
    for key in set(on) - {'quantity_available', 'below_reorder_point'}:
        assert on[key] == off[key], key
    assert report_on['totals']['asset_value'] == report_off['totals']['asset_value']
    assert report_on['totals']['below_reorder_point'] - report_off['totals']['below_reorder_point'] == 1
    assert books['run']('item show', dict(item=widget))['quantity_available'] == '18'


def test_nothing_committed_reports_exactly_as_before(books, stocked):
    before = stocked['before']
    assert before['quantity_committed'] == '0' and before['quantity_available'] == before['quantity_on_hand'] == '20'
    assert before['next_delivery_date'] is None and before['quantity_on_order'] == '0'


def test_open_sales_orders_by_customer_and_by_item_page_and_tie(books, stocked):
    run = books['run']
    whole = run('report open-sales-orders', dict(date_to='2017-12-31', limit=200))
    assert whole['totals']['lines'] == 4                       # A, B, C and the partly invoiced order
    assert [row['number'] for row in whole['rows']][-1] == stocked['b']['number']   # Baker Bros sorts after Adams
    paged, cursor = [], None
    while True:
        page = run('report open-sales-orders', dict(date_to='2017-12-31', limit=1, **({'cursor': cursor} if cursor else {})))
        paged += page['rows']
        cursor = page['next_cursor']
        if not cursor:
            break
    assert [row['line_id'] for row in paged] == [row['line_id'] for row in whole['rows']]
    by_item = run('report open-sales-orders', dict(date_to='2017-12-31', by='item', limit=2))
    row = by_item['rows'][0]
    assert (row['quantity_on_hand'], row['quantity_available'], row['quantity_on_order']) == ('18', '-6', '5')
    a = next(row for row in whole['rows'] if row['sales_order_id'] == stocked['a']['id'])
    assert (a['ordered'], a['invoiced'], a['cancelled'], a['backordered'], a['open_amount']['amount']) == ('10', '0', '2', '8', '36.00')
    assert run('report open-sales-orders', dict(date_to='2017-12-31', customer=stocked['other']))['totals']['lines'] == 1
    assert run('report open-sales-orders', dict(date_to='2017-03-31'))['totals']['lines'] == 3
    assert run('report open-sales-orders', dict(date_to='2017-12-31', short_only=True))['totals']['lines'] == 4
    books['run']('company update', dict(available_deducts_sales_orders=False), reason='Show on hand')
    assert run('report open-sales-orders', dict(date_to='2017-12-31', short_only=True))['totals']['lines'] == 0


def test_selling_or_ordering_past_available_warns_and_proceeds(books, stocked):
    widget = stocked['widget']
    sold = books['run']('invoice post', dict(customer=books['customer'], date='2017-05-01',
                                             lines=[dict(item=widget, quantity='3')]), reason='Walk-in sale')
    warning = next(w for w in sold['warnings'] if 'committed' in w)
    assert 'Widget' in warning and stocked['a']['number'] in warning and 'work order' in warning
    order = books['run'](
        'sales-order create', dict(customer=books['customer'], date='2017-05-02', lines=[dict(item=widget, quantity='30')]),
        reason='Big order', dry_run=True)
    # On hand 15 after the sale and 24 committed elsewhere: -9 available to this order, so 39 short.
    assert any(w.startswith('39 Widget short') for w in order['warnings']), order['warnings']


def test_stock_status_time_on_a_thousand_open_lines(books):
    """Measured, not asserted tight: recorded in the R102 report. Five orders of 200 stocked lines."""
    run = books['run']
    widget = _inventory_part(books, 'Bulk widget')
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='100', unit_cost='1.00')]),
        reason='Stock')
    for n in range(5):
        run('sales-order create', dict(customer=books['customer'], date='2017-03-01',
                                       lines=[dict(item=widget, quantity='1')] * 200), reason=f'Bulk {n}')
    started = time.perf_counter()
    row, _ = _stock(books, widget)
    seconds = time.perf_counter() - started
    print(f'\nstock-status with 1,000 open lines: {seconds:.3f}s')
    assert row['quantity_committed'] == '1000' and row['quantity_available'] == '-900'
    assert seconds < 30


def test_pending_builds_count_by_their_date_and_deduct_under_their_own_preference(books):
    """R102 x R103: For Assemblies reads pending builds dated by the report date, as open orders are read;
    available deducts them while available_deducts_pending_builds (on by default) is on."""
    from tests.test_assembly_builds import _buy, _kit, _part
    run = books['run']
    tank, valve = _part(books, 'Heater Tank', '100.00'), _part(books, 'Relief Valve', '10.00')
    kit = _kit(books, [(tank, '1'), (valve, '2')])
    _buy(books, tank, '5', '100.00', '2017-03-01')
    _buy(books, valve, '10', '10.00', '2017-03-01')
    run('assembly build', dict(item=kit, quantity='6', date='2017-03-10', pending=True), reason='Waiting on parts')
    before, _ = _stock(books, tank, '2017-03-09')
    assert (before['quantity_for_assemblies'], before['quantity_available']) == ('0', '5')
    after, _ = _stock(books, tank, '2017-12-31')
    assert (after['quantity_for_assemblies'], after['quantity_available']) == ('6', '-1')
    assert _stock(books, valve)[0]['quantity_for_assemblies'] == '12'
    assert books['run']('item show', dict(item=tank))['quantity_available'] == '-1'
    run('company update', dict(available_deducts_pending_builds=False), reason='Show on hand')
    off, _ = _stock(books, tank)
    assert (off['quantity_for_assemblies'], off['quantity_available']) == ('6', '5')
    assert books['run']('item show', dict(item=tank))['quantity_available'] == '5'
