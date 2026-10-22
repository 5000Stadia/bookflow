"""R101 increment 4: invoicing from sales orders (sales-orders design §4 and §8.4).

10 ordered with 6 on hand bills 6 by default and leaves 4 backordered; the explicit choices bill
what they name; two orders competing for the last units each see the other's commitment and ship
none by default; a retried key returns the same invoice and a different intent is refused; a tax
rate changed between two partial invoices taxes each at its own day's rate with the difference from
the quote shown; prepayments held on the order are applied.
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _net  # noqa: F401


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


@pytest.fixture
def shop(books):
    run, client, company = books['run'], books['client'], books['company']
    widget = _inventory_part(books, 'Widget')
    untaxed = books['tax_codes']['Non']
    install = client.item.create(company=company, name='Install', type='service', price='60.00',
                                 description='Install on site', sales_tax_code_id=untaxed,
                                 income_account_id=books['income'])['id']
    run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(item=widget, quantity='6', unit_cost='1.80')]),
        reason='Six in stock')
    return dict(widget=widget, install=install)


def _order(books, lines, date='2017-03-01', customer=None):
    return books['run']('sales-order create', dict(customer=customer or books['customer'], date=date, lines=lines),
                        reason='Order')


def _invoice(books, order, key, date='2017-03-02', **extra):
    current = books['run']('sales-order show', dict(sales_order=order['id']))
    return books['run']('sales-order invoice', dict(sales_order=order['number'], expected_version=current['version'],
                        conversion_key=key, date=date, **extra), reason='Shipped')


def test_ten_ordered_with_six_on_hand_bills_six_and_backorders_four(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='10'), dict(item=shop['install'], quantity='1')])
    preview = run('sales-order invoice', dict(sales_order=order['number'], expected_version=1, conversion_key='ship-1',
                                             date='2017-03-02'), reason='Shipped', dry_run=True)
    got = {line['item_id']: line['quantity'] for line in preview['revision']['lines']}
    assert got == {shop['widget']: '6', shop['install']: '1'}
    invoice = _invoice(books, order, 'ship-1')
    assert {line['item_id']: line['quantity'] for line in invoice['revision']['lines']} == got
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '0'
    shown = run('sales-order show', dict(sales_order=order['id']))
    widget = next(line for line in shown['revision']['lines'] if line['facts']['item_id'] == shop['widget'])
    assert (widget['order']['invoiced'], widget['order']['open'], widget['order']['state']) == ('6', '4', 'partly_invoiced')
    assert shown['order']['progress'] == 'partly_invoiced'
    assert run('report stock-status', dict(as_of='2017-12-31'))['rows'][0]['quantity_committed'] == '4'
    # Nothing more can ship: refused with each line's open quantity and the choices.
    error = _error(lambda: _invoice(books, order, 'ship-2'))
    assert error.code == 'E_VALIDATION' and error.details['lines'][0]['open'] == '4' and error.details['choices']
    # all_open bills the backorder whatever the stock (selling past on hand warns, as an invoice does).
    rest = _invoice(books, order, 'ship-3', all_open=True)
    assert [line['quantity'] for line in rest['revision']['lines']] == ['4']
    assert run('sales-order show', dict(sales_order=order['id']))['order']['progress'] == 'invoiced'
    # A closed order is not invoiced; a retry of the first key still returns its invoice.
    again = run('sales-order invoice', dict(sales_order=order['number'], expected_version=1, conversion_key='ship-1',
                                            date='2017-03-02'), reason='Shipped')
    assert again['id'] == invoice['id'] and again['idempotent_replay']


def test_explicit_choices_and_the_refused_modes(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='10'), dict(item=shop['install'], quantity='2')])
    lines = order['revision']['lines']
    by_item = _invoice(books, order, 'by-item', selections=[dict(item=shop['widget'], quantity='2')])
    assert [line['quantity'] for line in by_item['revision']['lines']] == ['2']
    whole = _invoice(books, order, 'whole', line_ids=[lines[1]['line_id']])
    assert [line['quantity'] for line in whole['revision']['lines']] == ['2']
    for bad in (dict(percent='50'), dict(selections=[dict(line_id=lines[0]['line_id'], net_amount='5.00')])):
        error = _error(lambda: _invoice(books, order, 'bad', **bad))
        assert error.code == 'E_VALIDATION'
    # A different intent under a used key is refused.
    error = _error(lambda: _invoice(books, order, 'by-item', selections=[dict(item=shop['widget'], quantity='3')]))
    assert error.code == 'E_CONVERSION_KEY_REUSED'


def test_two_orders_competing_for_the_last_units_ship_none_by_default(books, shop):
    other = books['client'].customer.create(company=books['company'], name='Baker Bros')['id']
    first = _order(books, [dict(item=shop['widget'], quantity='6')])          # six on hand, six on each order
    second = _order(books, [dict(item=shop['widget'], quantity='6')], customer=other)
    for order in (first, second):
        error = _error(lambda: _invoice(books, order, 'compete-' + order['number']))
        assert error.code == 'E_VALIDATION' and error.details['lines'][0]['available'] == '0'
    # The person chooses: the first gets all six.
    chosen = _invoice(books, first, 'first-gets-them', line_ids=[first['revision']['lines'][0]['line_id']])
    assert chosen['revision']['lines'][0]['quantity'] == '6'


def test_each_partial_invoice_is_taxed_at_its_own_days_rate_with_the_difference_shown(books, shop):
    run = books['run']
    taxed = books['tax_codes']['Tax']
    agency = books['client'].vendor.create(company=books['company'], name='State Revenue', is_tax_agency=True)['id']
    liability = next(row['id'] for row in run('account query', dict(limit=200))['items']
                     if row['type'] == 'other_current_liability' and row.get('system_role') == 'sales_tax_payable') \
        if any(row.get('system_role') == 'sales_tax_payable' for row in run('account query', dict(limit=200))['items']) else None
    tax = run('item create', dict(name='State tax', type='sales_tax_item', tax_percent='5', tax_agency_vendor_id=agency,
                                  **({'liability_account_id': liability} if liability else {})), reason='Tax')
    run('company update', dict(sales_tax_enabled=True, default_sales_tax_item_id=tax['id']), reason='Tax on')
    run('item update', dict(item=shop['widget'], sales_tax_code_id=taxed), reason='Taxable')
    order = _order(books, [dict(item=shop['widget'], quantity='4')])          # 4 x 4.50 = 18.00, quoted tax 0.90
    assert order['tax']['amount'] == '0.90'
    first = _invoice(books, order, 'tax-1', selections=[dict(item=shop['widget'], quantity='2')])
    assert first['tax']['amount'] == '0.45' and first['order_tax']['difference']['amount'] == '0.00'
    run('item update', dict(item=tax['id'], tax_percent='10'), reason='Rate rises')
    second = _invoice(books, order, 'tax-2', date='2017-03-09', selections=[dict(item=shop['widget'], quantity='2')])
    assert second['tax']['amount'] == '0.90'
    assert second['order_tax']['quoted_tax']['amount'] == '0.45' and second['order_tax']['difference']['amount'] == '0.45'
    assert run('invoice show', dict(invoice=first['id']))['tax']['amount'] == '0.45'   # the first keeps its day's rate


def test_money_held_on_the_order_is_applied_to_its_invoice(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['install'], quantity='2')])            # 120.00
    run('prepayment receive', dict(document=f'sales-order {order["number"]}', amount='50.00', date='2017-03-01',
                                   payment_method=books['methods']['Check']))
    invoice = _invoice(books, order, 'with-deposit')
    assert invoice['prepayment_applied']['amount'] == '50.00' and invoice['balance_due']['amount'] == '70.00'
    report = run('report customer-prepayments', dict(as_of='2017-12-31'))
    assert report['total_held']['minor_units'] == 0 == report['account_balance']['minor_units']


def _open(books, order, item):
    shown = books['run']('sales-order show', dict(sales_order=order['id']))
    line = next(line for line in shown['revision']['lines'] if line['facts']['item_id'] == item)
    return line['order']['open'], line['order']['cancelled'], line['order']['invoiced']


def _billed(books, shop):
    """An order for 6 widgets and 1 install, invoiced in full (6 on hand)."""
    order = _order(books, [dict(item=shop['widget'], quantity='6'), dict(item=shop['install'], quantity='1')])
    return order, _invoice(books, order, 'full-' + order['number'])


@pytest.mark.parametrize('choice', ['reopen', 'cancel'])
def test_voiding_an_order_made_invoice_reopens_or_cancels_as_chosen_and_says_which(books, shop, choice):
    run = books['run']
    order, invoice = _billed(books, shop)
    body = dict(invoice=invoice['id'], **({} if choice == 'reopen' else {'release_to_order': 'cancel'}))
    preview = run('invoice void', body, reason='Wrong customer', dry_run=True)
    assert any(('open again' if choice == 'reopen' else 'cancelled on sales order') in w for w in preview['warnings'])
    voided = run('invoice void', body, reason='Wrong customer')
    assert any(order['number'] in w for w in voided['warnings'])
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '6'
    if choice == 'reopen':
        assert _open(books, order, shop['widget']) == ('6', '0', '0')
        assert run('item show', dict(item=shop['widget']))['quantity_committed'] == '6'
    else:
        assert _open(books, order, shop['widget']) == ('0', '6', '0')
        assert run('sales-order show', dict(sales_order=order['id']))['order']['progress'] == 'closed'


def _grant_delete(books):
    run, client = books['run'], books['client']
    state = client.run('permission show', {})
    if state.get('mode') != 'policy_v1' or state.get('activation_required'):
        client.run('permission activate', dict(expected_generation=state['generation'],
                                               expected_catalog_sha256=state['catalog_sha256']))
    member = next(row for row in client.run('membership list', {'company': books['company']})['items']
                  if row['scope_type'] == 'company' and row['scope_id'] == books['company'])
    client.run('membership grant', dict(user=member['user_id'], company=books['company'], expected_version=member['version'],
                                        grants=['transaction.invoice.delete']))


@pytest.mark.parametrize('choice', ['reopen', 'cancel'])
def test_deleting_an_order_made_invoice_reopens_or_cancels_as_chosen(books, shop, choice):
    run = books['run']
    _grant_delete(books)
    order, invoice = _billed(books, shop)
    body = dict(invoice=invoice['id'], expected_version=invoice['version'],
                **({} if choice == 'reopen' else {'release_to_order': 'cancel'}))
    deleted = run('invoice delete', body, reason='Entered twice')
    assert any(order['number'] in w for w in deleted['warnings'])
    assert _open(books, order, shop['install']) == (('1', '0', '0') if choice == 'reopen' else ('0', '1', '0'))


@pytest.mark.parametrize('choice', ['reopen', 'cancel'])
def test_an_update_removing_an_order_made_line_releases_it_and_other_updates_do_not(books, shop, choice):
    run = books['run']
    order, invoice = _billed(books, shop)
    lines = invoice['revision']['lines']
    widget = next(line for line in lines if line['item_id'] == shop['widget'])
    memo = run('invoice update', dict(invoice=invoice['id'], expected_version=invoice['version'], memo='Delivered'),
               reason='Note it')
    assert not any('open again' in w or 'cancelled on' in w for w in memo['warnings'])
    assert _open(books, order, shop['install']) == ('0', '0', '1')
    body = dict(invoice=invoice['id'], expected_version=memo['version'],
                lines=[dict(line_id=widget['line_id'], item=shop['widget'])],
                **({} if choice == 'reopen' else {'release_to_order': 'cancel'}))
    updated = run('invoice update', body, reason='Install not done')
    assert any(order['number'] in w for w in updated['warnings'])
    assert _open(books, order, shop['install']) == (('1', '0', '0') if choice == 'reopen' else ('0', '1', '0'))
    assert _open(books, order, shop['widget']) == ('0', '0', '6')
    # Changing an order-made line's quantity in place stays refused.
    error = _error(lambda: run('invoice update', dict(invoice=invoice['id'], expected_version=updated['version'],
        lines=[dict(line_id=widget['line_id'], item=shop['widget'], quantity='5')]), reason='Fewer'))
    assert error.code in ('E_WORK_DEPENDENCY', 'E_VALIDATION')


def test_credit_memos_leave_the_order_alone(books, shop):
    run = books['run']
    order, invoice = _billed(books, shop)
    widget = next(line for line in invoice['revision']['lines'] if line['item_id'] == shop['widget'])
    run('credit-memo post', dict(customer=books['customer'], date='2017-03-05',
                                 lines=[dict(item=shop['install'], quantity='1', unit_price='10.00')]), reason='Goodwill')
    run('credit-memo post', dict(customer=books['customer'], date='2017-03-06',
                                 lines=[dict(source_invoice=invoice['id'], source_line=widget['line_id'], quantity='2')]),
        reason='Two came back')
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '2'   # restocked
    assert _open(books, order, shop['widget']) == ('0', '0', '6')                     # still invoiced


def test_simultaneous_partial_invoices_and_retried_keys_bill_issue_and_release_once(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='6')])
    one = dict(sales_order=order['id'], expected_version=1, conversion_key='half-a', date='2017-03-02',
               selections=[dict(item=shop['widget'], quantity='3')])
    two = dict(one, conversion_key='half-b')
    first = run('sales-order invoice', one, reason='Shipped')
    error = _error(lambda: run('sales-order invoice', two, reason='Shipped'))
    assert error.code == 'E_VERSION_CONFLICT'                     # the second saw the order before the first wrote
    assert run('sales-order invoice', one, reason='Shipped')['id'] == first['id']   # a retry returns the same
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '3'
    second = run('sales-order invoice', dict(two, expected_version=2), reason='Shipped')
    assert run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '0'
    assert _open(books, order, shop['widget']) == ('0', '0', '6')
    run('invoice void', dict(invoice=second['id']), reason='Wrong')
    run('invoice void', dict(invoice=second['id']), reason='Wrong')
    assert _open(books, order, shop['widget']) == ('3', '0', '3') and \
        run('item show', dict(item=shop['widget']))['quantity_on_hand'] == '3'


def test_an_ordinary_sale_or_payment_is_steered_to_the_open_order(books, shop):
    run = books['run']
    order = _order(books, [dict(item=shop['widget'], quantity='4')])
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03', lines=[dict(item=shop['widget'], quantity='2')]),
               reason='Counter sale', dry_run=True)
    note = next(w for w in sale['warnings'] if 'sales-order invoice' in w)
    assert f'sales-order invoice {order["number"]} --expected-version 1' in note
    paid = run('payment receive', dict(customer=books['customer'], date='2017-03-03', amount='50.00',
                                       payment_method=books['methods']['Check'], operation_key='deposit-maybe'),
               reason='Money in', dry_run=True)
    assert any(f'prepayment receive --document "sales-order {order["number"]}"' in w for w in paid['warnings']), paid['warnings']
