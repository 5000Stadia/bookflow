"""R101: an invoice made from a sales order shows the order's quantities (sales-orders design §4).

`invoice show`, its preview and the printed invoice give each order-made line the order's ordered,
previously invoiced and backordered quantities; an ordinary line carries none. An earlier invoice keeps
reading as it stood when it was made: a later invoice is not "previously invoiced" on it.
"""
from tests.test_bill_item_lines import books, _inventory_part  # noqa: F401
from tests.test_order_invoicing import shop, _order, _invoice  # noqa: F401


def _orders(result):
    return [line.get('order') for line in result['revision']['lines']]


def _print(books, invoice):
    from bookflow.documents.model import build
    client = books['client']
    return build(lambda name, data, company: client.run(name, data, company=company),
                 books['company'], 'invoice', {'document': invoice['id']})


def test_order_made_invoice_lines_show_ordered_previously_invoiced_and_backordered(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='10'), dict(item=shop['install'], quantity='1')])
    first = _invoice(books, order, 'ship-1')
    widget, install = _orders(run('invoice show', dict(invoice=first['id'])))
    assert (widget['sales_order_number'], widget['ordered'], widget['previously_invoiced'], widget['backordered']) == (
        order['number'], '10', '0', '4')
    assert (install['ordered'], install['previously_invoiced'], install['backordered']) == ('1', '0', '0')
    assert _orders(first)[0]['backordered'] == '4'
    # The rest comes in and is billed: its preview and result count the first invoice as previous.
    run('bill post', dict(vendor=books['vendor'], date='2017-03-03', items=[dict(item=shop['widget'], quantity='4',
        unit_cost='1.80')]), reason='The rest came in')
    current = run('sales-order show', dict(sales_order=order['id']))
    body = dict(sales_order=order['number'], expected_version=current['version'], conversion_key='ship-2',
                date='2017-03-04')
    preview = run('sales-order invoice', body, reason='Rest shipped', dry_run=True)
    assert [(o['ordered'], o['previously_invoiced'], o['backordered']) for o in _orders(preview)] == [('10', '6', '0')]
    second = run('sales-order invoice', body, reason='Rest shipped')
    assert [(o['ordered'], o['previously_invoiced'], o['backordered']) for o in _orders(
        run('invoice show', dict(invoice=second['id'])))] == [('10', '6', '0')]
    # The first invoice still reads as it was made.
    assert _orders(run('invoice show', dict(invoice=first['id'])))[0]['previously_invoiced'] == '0'
    # The printed invoice carries the order's columns.
    printed = _print(books, first)
    labels = [column.label for column in printed.columns]
    assert labels[:6] == ['Item', 'Description', 'Ordered', 'Prev. invoiced', 'Backordered', 'Invoiced'], labels
    assert printed.rows[0][2:6] == ('10', '0', '4', '6'), printed.rows
    # Voiding the first reopens its 6, so the second no longer counts them as previously invoiced.
    run('invoice void', dict(invoice=first['id'], expected_version=1), reason='Wrong shipment')
    assert _orders(run('invoice show', dict(invoice=second['id'])))[0]['previously_invoiced'] == '0'


def test_an_ordinary_invoice_carries_no_order_quantities_and_prints_as_before(books, shop):
    run = books['run']
    invoice = run('invoice post', dict(customer=books['customer'], date='2017-03-02',
                                       lines=[dict(item=shop['install'], quantity='1')]), reason='Visit')
    assert _orders(run('invoice show', dict(invoice=invoice['id']))) == [None]
    assert [column.label for column in _print(books, invoice).columns][:3] == ['Item', 'Description', 'Qty']


def test_billing_other_work_for_a_customer_with_an_open_order_is_steered_to_the_order(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='4')])
    estimate = run('estimate create', dict(customer=books['customer'], date='2017-03-02', title='Widgets',
                                           lines=[dict(item=shop['widget'], quantity='2')]), reason='Quote')
    estimate = run('estimate update', dict(estimate=estimate['id'], expected_version=1, status='accepted',
                                           decision_note='Yes'), reason='Accepted')
    body = dict(estimate=estimate['id'], expected_version=estimate['version'], date='2017-03-03', conversion_key='e-1')
    command = f'sales-order invoice {order["number"]} --expected-version 1'
    preview = run('estimate invoice', body, reason='Billed', dry_run=True)
    assert any(command in w for w in preview['warnings']), preview['warnings']
    posted = run('estimate invoice', body, reason='Billed')
    assert any(command in w for w in posted['warnings']), posted['warnings']
    # The help says so too.
    for name in ('estimate invoice', 'work-order invoice', 'time-activity invoice', 'invoice post'):
        from bookflow.core import registry
        assert 'sales-order invoice' in registry.get(name).description, name
