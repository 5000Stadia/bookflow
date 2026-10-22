"""R101: the sales order, a fifth customer-work kind (sales-orders design, increment 2).

An order with stocked, service and discount lines saves, revises, closes, reopens and voids, with
every revision readable; an estimate with stocked lines becomes either an order or a work order,
and never both; cancelled quantity and `close: true` work on order and work-order lines; nothing
here posts or moves stock, and the invoice made from a converted estimate still issues it.
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_asset, _inventory_part, _net  # noqa: F401


def _items(books):
    client, company = books['client'], books['company']
    widget = _inventory_part(books, 'Widget')
    untaxed = books['tax_codes']['Non']
    install = client.item.create(company=company, name='Install', type='service', price='60.00',
                                 description='Install on site', sales_tax_code_id=untaxed,
                                 income_account_id=books['income'])['id']
    discount = client.item.create(company=company, name='Ten Off', type='discount', discount_percent='10',
                                  description='Ten percent off', sales_tax_code_id=untaxed,
                                  income_account_id=books['income'])['id']
    return widget, install, discount


def _order(books, widget, install, discount, **extra):
    body = dict(customer=books['customer'], date='2017-03-01',
                lines=[dict(item=widget, quantity='10'), dict(item=install, quantity='2'),
                       dict(item=discount)])
    body.update(extra)
    return books['run']('sales-order create', body, reason='Adams ordered 10 widgets')


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def test_an_order_saves_revises_closes_reopens_and_voids_with_every_revision_readable(books):
    run = books['run']
    widget, install, discount = _items(books)
    before = _net(books)
    preview = run('sales-order create', dict(customer=books['customer'], date='2017-03-01',
                  lines=[dict(item=widget, quantity='10')]), reason='Look first', dry_run=True)
    assert preview['dry_run'] and preview['kind'] == 'sales_order'

    order = _order(books, widget, install, discount)
    assert order['kind'] == 'sales_order' and order['status'] == 'open' and order['version'] == 1
    assert order['title'] == 'Adams Plumbing'
    assert order['order']['progress'] == 'open' and order['order']['is_open']
    lines = order['revision']['lines']
    assert [line['facts']['profile']['item_type'] for line in lines] == ['inventory_part', 'service', 'discount']
    # 10 x 4.50 + 2 x 60.00 = 165.00; the discount takes 10% off the line above it (12.00): 153.00.
    assert order['net']['amount'] == '153.00'
    widget_line = lines[0]
    assert widget_line['order'] == dict(ordered='10', invoiced='0', cancelled='0', open='10',
                                        open_amount=widget_line['order']['open_amount'], state='open')
    assert widget_line['facts']['profile']['cogs_account']['id'] == books['cogs']
    assert 'order' not in lines[2]  # a discount is billed with the lines it reduces
    assert order['order']['open_amount']['amount'] == '153.00'

    # Revise: cancel 3 widgets, then close the install line outright.
    revised = run('sales-order update', dict(sales_order=order['number'], expected_version=1,
        lines=[dict(line_id=lines[0]['line_id'], item=widget, cancelled_quantity='3'),
               dict(line_id=lines[1]['line_id'], item=install, close=True),
               dict(line_id=lines[2]['line_id'], item=discount)]), reason='Adams wants 7, installs themselves')
    got = {line['facts']['item_id']: line for line in revised['revision']['lines']}
    assert got[widget]['order']['cancelled'] == '3' and got[widget]['order']['open'] == '7'
    assert got[install]['order']['state'] == 'closed' and got[install]['order']['open'] == '0'
    assert revised['version'] == 2 and revised['order']['progress'] == 'open'

    # Close needs a reason; closed has nothing open; reopening restores what was open.
    assert _error(lambda: run('sales-order update', dict(sales_order=order['id'], expected_version=2,
                                                         status='closed'))).code == 'E_REASON_REQUIRED'
    closed = run('sales-order update', dict(sales_order=order['id'], expected_version=2, status='closed'),
                 reason='Nothing more will ship')
    assert closed['status'] == 'closed' and closed['order']['progress'] == 'closed'
    assert not closed['order']['is_open'] and closed['order']['open_amount']['minor_units'] == 0
    assert {line['order']['open'] for line in closed['revision']['lines'] if 'order' in line} == {'0'}
    reopened = run('sales-order update', dict(sales_order=order['id'], expected_version=3, status='open'),
                   reason='They called back')
    widget_again = next(line for line in reopened['revision']['lines'] if line['facts']['item_id'] == widget)
    assert widget_again['order']['open'] == '7' and widget_again['cancelled_quantity'] == '3'

    # Cancelling more than is left is refused; the reason says what is possible.
    error = _error(lambda: run('sales-order update', dict(sales_order=order['id'], expected_version=4,
        lines=[dict(line_id=lines[0]['line_id'], item=widget, cancelled_quantity='11'),
               dict(line_id=lines[1]['line_id'], item=install),
               dict(line_id=lines[2]['line_id'], item=discount)]), reason='Too many'))
    assert error.code in ('E_VALIDATION', 'E_VALUE_RANGE')

    # Void: a reason, at most 140 characters, terminal; nothing posted, nothing moved.
    assert _error(lambda: run('sales-order void', dict(sales_order=order['id'], expected_version=4),
                              reason='x' * 141)).code == 'E_VALIDATION'
    voided = run('sales-order void', dict(sales_order=order['id'], expected_version=4), reason='Order withdrawn')
    assert voided['status'] == 'voided' and not voided['active'] and voided['order']['progress'] == 'voided'
    assert voided['revision']['decision_note'] == 'Order withdrawn'
    again = run('sales-order void', dict(sales_order=order['id'], expected_version=5), reason='Order withdrawn')
    assert again['changed'] is False
    assert _error(lambda: run('sales-order update', dict(sales_order=order['id'], expected_version=5,
                                                         memo='late'), reason='Late')).code == 'E_VALIDATION'

    history = run('sales-order history', dict(sales_order=order['id'], limit=50))
    assert [row['revision_number'] for row in history['items']] == [1, 2, 3, 4, 5]
    assert [row['status'] for row in history['items']] == ['open', 'open', 'closed', 'open', 'voided']
    first = run('sales-order show', dict(sales_order=order['id'], revision_number=1))
    assert first['revision']['revision_number'] == 1 and first['revision']['lines'][0]['quantity'] == '10'
    assert first['revision']['lines'][0]['cancelled_quantity'] == '0'
    assert run('sales-order query', dict(status='voided', active=None, limit=10))['count'] == 1
    assert _net(books) == before, 'a sales order posts nothing'
    widget_row = run('item show', {'item': widget})
    assert widget_row['quantity_on_hand'] == '0'


def test_copy_makes_an_independent_open_order_and_the_preference_turns_new_ones_off(books):
    run = books['run']
    widget, install, discount = _items(books)
    order = _order(books, widget, install, discount)
    lines = order['revision']['lines']
    run('sales-order update', dict(sales_order=order['id'], expected_version=1,
        lines=[dict(line_id=lines[0]['line_id'], item=widget, cancelled_quantity='4'),
               dict(line_id=lines[1]['line_id'], item=install), dict(line_id=lines[2]['line_id'], item=discount)]),
        reason='Fewer')
    copy = run('sales-order copy', dict(sales_order=order['id'], expected_version=2, date='2017-04-01'),
               reason='Same again')
    assert copy['status'] == 'open' and copy['number'] != order['number']
    assert copy['revision']['lines'][0]['cancelled_quantity'] == '0'
    assert {line['root_line_id'] for line in copy['revision']['lines']}.isdisjoint(
        {line['root_line_id'] for line in lines})

    run('company update', dict(sales_orders_enabled=False), reason='Off')
    error = _error(lambda: _order(books, widget, install, discount))
    assert error.code == 'E_FEATURE_DISABLED' and error.details['setting'] == 'sales_orders_enabled'
    assert run('sales-order show', dict(sales_order=order['id']))['status'] == 'open'


def _estimate(books, widget, install):
    run = books['run']
    estimate = run('estimate create', dict(customer=books['customer'], date='2017-03-01', title='Widgets',
                   lines=[dict(item=widget, quantity='10'), dict(item=install, quantity='1')]),
                   reason='Quote widgets')
    return run('estimate update', dict(estimate=estimate['id'], expected_version=1, status='accepted',
                                       decision_note='Signed'), reason='Accepted')


def test_an_estimate_with_stocked_lines_becomes_an_order_once_and_never_also_a_work_order(books):
    run = books['run']
    widget, install, _ = _items(books)
    accepted = _estimate(books, widget, install)
    body = dict(estimate=accepted['id'], expected_version=accepted['version'], date='2017-03-02',
                conversion_key='adams-order-1', ship_date='2017-03-09')
    order = run('estimate sales-order', body, reason='Make the order')
    assert order['kind'] == 'sales_order' and order['status'] == 'open'
    assert order['revision']['facts']['profile']['ship_date'] == '2017-03-09'
    quoted = {line['line_id']: line for line in run('estimate show', {'estimate': accepted['id']})['revision']['lines']}
    assert {line['root_line_id'] for line in order['revision']['lines']} == {line['root_line_id'] for line in quoted.values()}
    assert order['links'][0]['relation'] == 'estimate_sales_order'

    # Replay with the same key returns the same order; the same key with a different intent is refused.
    again = run('estimate sales-order', body, reason='Make the order')
    assert again['id'] == order['id'] and again['idempotent_replay']
    error = _error(lambda: run('estimate sales-order', dict(body, ship_date='2017-03-10'), reason='Other'))
    assert error.code == 'E_CONVERSION_KEY_REUSED'

    # One operational destination: neither a second order nor a work order.
    shown = run('estimate show', {'estimate': accepted['id']})
    for command, key in (('estimate sales-order', 'adams-order-2'), ('estimate work-order', 'adams-wo-1')):
        error = _error(lambda: run(command, dict(estimate=accepted['id'], expected_version=shown['version'],
                                                 date='2017-03-03', conversion_key=key), reason='Second'))
        assert error.code == 'E_WORK_DEPENDENCY' and 'never both' in error.details['problem']
    # Its billing now goes to the order.
    error = _error(lambda: run('estimate invoice', dict(estimate=accepted['id'], expected_version=shown['version'],
                                                        date='2017-03-04', conversion_key='inv-1'), reason='Bill'))
    assert error.code == 'E_WORK_DEPENDENCY' and 'sales order' in error.details['problem']


def test_an_estimate_with_stocked_lines_becomes_a_work_order_whose_invoice_issues_the_stock(books):
    run = books['run']
    widget, install, _ = _items(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='20', unit_cost='1.80')]),
        reason='Stock up')
    accepted = _estimate(books, widget, install)
    work = run('estimate work-order', dict(estimate=accepted['id'], expected_version=accepted['version'],
                                          date='2017-03-02', conversion_key='adams-wo'), reason='Do it')
    assert work['revision']['lines'][0]['facts']['profile']['item_type'] == 'inventory_part'
    assert work['revision']['lines'][0]['order']['open'] == '10'
    error = _error(lambda: run('estimate sales-order', dict(estimate=accepted['id'],
        expected_version=accepted['version'] + 1, date='2017-03-03', conversion_key='adams-so'), reason='Also'))
    assert error.code == 'E_WORK_DEPENDENCY'
    # Cancel 4 on the work order; completing fills ordered less cancelled.
    lines = work['revision']['lines']
    work = run('work-order update', dict(work_order=work['id'], expected_version=1,
        lines=[dict(line_id=lines[0]['line_id'], item=widget, cancelled_quantity='4'),
               dict(line_id=lines[1]['line_id'], item=install)]), reason='Only 6')
    done = run('work-order complete', dict(work_order=work['id'], expected_version=work['version'],
                                           actual_start='2017-03-03T08:00:00Z'), reason='Done')
    assert done['revision']['lines'][0]['completed_quantity'] == '6'
    # A share of a stocked line is refused; its quantity bills, and the invoice issues the stock.
    error = _error(lambda: run('work-order invoice', dict(work_order=work['id'], expected_version=done['version'],
        date='2017-03-05', conversion_key='wo-inv-pct', percent='50'), reason='Half'))
    assert error.code == 'E_VALIDATION'
    first = run('work-order invoice', dict(work_order=work['id'], expected_version=done['version'],
                date='2017-03-05', conversion_key='wo-inv-2', selections=[dict(line_id=lines[0]['line_id'], quantity='2')]),
                reason='Two went out first')
    assert run('item show', {'item': widget})['quantity_on_hand'] == '18'
    assert _net(books)[books['cogs']] == 360
    current = run('work-order show', {'work_order': work['id']})
    invoice = run('work-order invoice', dict(work_order=work['id'], expected_version=current['version'],
                  date='2017-03-06', conversion_key='wo-inv'), reason='Bill the rest')
    widget_line = next(line for line in invoice['revision']['lines'] if line['item_id'] == widget)
    assert widget_line['quantity'] == '4' and first['id'] != invoice['id']
    assert run('item show', {'item': widget})['quantity_on_hand'] == '14'
    assert _net(books)[books['cogs']] == 1080  # 6 x 1.80, issued by the two invoices
    shown = run('work-order show', {'work_order': work['id']})
    assert shown['revision']['lines'][0]['order'] == dict(ordered='10', invoiced='6', cancelled='4', open='0',
        open_amount=shown['revision']['lines'][0]['order']['open_amount'], state='closed')


def test_void_is_refused_while_an_invoice_consumes_the_order(books):
    """The invoicing increment bills orders directly; today an order's roots are consumed when the
    estimate it came from was invoiced first, and that is enough to prove the guard."""
    run = books['run']
    widget, install, _ = _items(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='20', unit_cost='1.80')]),
        reason='Stock up')
    accepted = _estimate(books, widget, install)
    install_line = next(line for line in accepted['revision']['lines'] if line['facts']['item_id'] == install)
    run('estimate invoice', dict(estimate=accepted['id'], expected_version=accepted['version'], date='2017-03-02',
                                 conversion_key='deposit-labor', line_ids=[install_line['line_id']]), reason='Bill the visit')
    shown = run('estimate show', {'estimate': accepted['id']})
    order = run('estimate sales-order', dict(estimate=accepted['id'], expected_version=shown['version'],
                                             date='2017-03-03', conversion_key='adams-so'), reason='Order the rest')
    got = {line['facts']['item_id']: line for line in order['revision']['lines']}
    assert got[install]['order']['invoiced'] == '1' and got[install]['order']['state'] == 'invoiced'
    assert got[widget]['order']['open'] == '10' and order['order']['progress'] == 'partly_invoiced'
    error = _error(lambda: run('sales-order void', dict(sales_order=order['id'], expected_version=1), reason='Withdraw'))
    assert error.code == 'E_WORK_DEPENDENCY' and got[install]['line_id'] in error.details['source_line_ids']
    # The invoiced line is fixed and stays; cancelling it is refused, the stocked line may be repriced.
    error = _error(lambda: run('sales-order update', dict(sales_order=order['id'], expected_version=1,
        lines=[dict(line_id=got[widget]['line_id'], item=widget)]), reason='Drop the visit'))
    assert error.code == 'E_WORK_DEPENDENCY'
    repriced = run('sales-order update', dict(sales_order=order['id'], expected_version=1,
        lines=[dict(line_id=got[widget]['line_id'], item=widget, unit_price='4.00'),
               dict(line_id=got[install]['line_id'], item=install)]), reason='Price match')
    assert repriced['version'] == 2
