"""Billing part of a purchase order (R153): Enter Bills against an order for some of a line.

Ten ordered; a bill for four, then one for six; a third is refused; voiding a bill gives its
four back and they bill again; a partial receipt is then billed from the receipt; a line is
closed and reopened by hand. After every step the order's own line tracking, its status and
the open purchase orders report agree.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net  # noqa: F401


def _order(books, item, *lines):
    return books['run']('purchase-order post', dict(
        date='2017-01-01', vendor=books['vendor'],
        lines=[dict(item=item, quantity='10', rate='10.00'), *lines]), reason='Order ten')


def _line(books, po, position=1):
    shown = books['run']('purchase-order show', dict(purchase_order=po['id']))
    return shown, shown['line_tracking'][position - 1]


def _open_report(books):
    report = books['run']('report open-purchase-orders', dict(date_to='2017-12-31'))
    return {row['number']: row['open_balance']['minor_units'] for row in report['rows']}


def _bill(books, po, *selections, date='2017-01-10', **extra):
    return books['run']('bill post', dict(purchase_order=po['id'], date=date,
                                          order_lines=list(selections), **extra), reason='Vendor billed part')


def test_ten_ordered_four_billed_then_six_then_over_billing_refused(books):
    run = books['run']; item = _inventory_part(books); asset = _inventory_asset(books)
    po = _order(books, item)
    assert _open_report(books) == {po['number']: 10000}

    four = _bill(books, po, dict(line='1', quantity='4'))
    assert four['total']['minor_units'] == 4000 and four['purchase_order_id'] == po['id']
    shown, line = _line(books, po)
    assert shown['status'] == 'partly_received'
    assert (line['received_quantity'], line['billed_quantity'], line['open_quantity']) == ('4', '4', '6')
    assert shown['part_bill_ids'] == [four['id']]
    assert shown['receiving'][0]['remaining_quantity_microunits'] == 6000000
    assert _open_report(books) == {po['number']: 6000}
    assert _net(books) == {asset: 4000, books['payable']: -4000}

    six = _bill(books, po, dict(line=line['line_id']))          # by id; quantity defaults to what is open
    assert six['total']['minor_units'] == 6000
    shown, line = _line(books, po)
    assert shown['status'] == 'closed' and line['open_quantity'] == '0'
    assert _open_report(books) == {}

    with pytest.raises(BookflowError) as refused:
        _bill(books, po, dict(line='1', quantity='1'))
    assert refused.value.code == 'E_VALIDATION'
    assert 'remain open' in str(refused.value.details) or 'nothing remains' in str(refused.value.details)
    assert _net(books) == {asset: 10000, books['payable']: -10000}


def test_over_billing_names_what_is_open_and_changes_nothing(books):
    item = _inventory_part(books)
    po = _order(books, item)
    _bill(books, po, dict(line='1', quantity='7'))
    with pytest.raises(BookflowError) as refused:
        _bill(books, po, dict(line='1', quantity='4'))
    assert refused.value.code == 'E_VALIDATION'
    assert 'only 3 of 10 remain open' in str(refused.value.details)
    assert _line(books, po)[1]['open_quantity'] == '3'


def test_void_returns_the_quantity_and_it_bills_again(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item)
    four = _bill(books, po, dict(line='1', quantity='4'))
    six = _bill(books, po, dict(line='1', quantity='6'))
    assert _line(books, po)[0]['status'] == 'closed'

    with pytest.raises(BookflowError) as fixed:
        run('bill update', dict(bill=four['id'], expected_version=four['version'], memo='edit'), reason='edit')
    assert fixed.value.code == 'E_WORK_DEPENDENCY'

    run('bill void', dict(bill=four['id']), reason='Billed in error')
    shown, line = _line(books, po)
    assert shown['status'] == 'partly_received' and line['open_quantity'] == '4'
    assert shown['part_bill_ids'] == [six['id']]
    assert _open_report(books) == {po['number']: 4000}

    again = _bill(books, po, dict(line='1', quantity='4', unit_cost='10.50'))
    assert again['total']['minor_units'] == 4200            # what the vendor charged, not the order rate
    shown, line = _line(books, po)
    assert shown['status'] == 'closed' and line['billed_quantity'] == '10'
    assert _open_report(books) == {}


def test_partial_receipt_then_a_bill_for_it_then_the_rest_billed_from_the_order(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item)
    line_id = po['revision']['lines'][0]['line_id']
    receipt = run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'], purchase_order=po['id'],
        purchase_order_version=po['version'], items=[dict(item=item, order_line_id=line_id, quantity='3', unit_cost='10.00')]),
        reason='Receive three')
    shown, line = _line(books, po)
    assert (line['received_quantity'], line['billed_quantity'], line['open_quantity']) == ('3', '0', '7')
    assert _open_report(books) == {po['number']: 7000}

    run('bill post', dict(date='2017-01-08', receipts=[dict(receipt_line=receipt['items'][0]['id'],
        expected_receipt_version=receipt['version'], quantity='3')]), reason='Bill for the receipt')
    shown, line = _line(books, po)
    assert (line['received_quantity'], line['billed_quantity'], line['open_quantity']) == ('3', '3', '7')
    assert _open_report(books) == {po['number']: 7000}

    with pytest.raises(BookflowError):
        _bill(books, po, dict(line='1', quantity='8'))       # three already arrived; seven are open
    _bill(books, po, dict(line='1', quantity='7'))
    shown, line = _line(books, po)
    assert shown['status'] == 'closed' and line['billed_quantity'] == '10' and line['received_quantity'] == '10'
    assert _open_report(books) == {}


def test_closing_a_line_by_hand_and_reopening_it(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item, dict(account=books['supplies'], amount='50.00', description='Fittings'))
    _bill(books, po, dict(line='1', quantity='6'), dict(line='2', amount='20.00'))
    shown, line = _line(books, po, 2)
    assert line['measured'] == 'amount' and line['open_amount_minor_units'] == 3000
    assert _open_report(books) == {po['number']: 4000 + 3000}

    closed = run('purchase-order close-lines', dict(purchase_order=po['id'], lines=['1']), reason='Rest backordered for good')
    assert closed['line_tracking'][0]['closed'] is True and closed['status'] == 'partly_received'
    assert _open_report(books) == {po['number']: 3000}
    assert run('purchase-order show', dict(purchase_order=po['id']))['receiving'][0]['remaining_quantity_microunits'] == 0
    with pytest.raises(BookflowError) as refused:
        _bill(books, po, dict(line='1', quantity='1'))
    assert 'closed' in str(refused.value.details)

    closed = run('purchase-order close-lines', dict(purchase_order=po['id'], lines=['2']), reason='No more fittings')
    assert closed['status'] == 'closed'
    assert _open_report(books) == {}

    reopened = run('purchase-order close-lines', dict(purchase_order=po['id'], lines=['1'], reopen=True), reason='Supplier found them')
    assert reopened['status'] == 'partly_received' and reopened['line_tracking'][0]['open_quantity'] == '4'
    assert _open_report(books) == {po['number']: 4000}


def test_a_whole_order_bill_after_a_partial_one_bills_only_what_is_open(books):
    run = books['run']; item = _inventory_part(books); asset = _inventory_asset(books)
    po = _order(books, item)
    _bill(books, po, dict(line='1', quantity='4'))
    rest = run('bill post', dict(purchase_order=po['id'], date='2017-01-12'), reason='Whole order bill')
    assert rest['total']['minor_units'] == 6000
    shown, line = _line(books, po)
    assert shown['status'] == 'closed' and line['billed_quantity'] == '10'
    assert _net(books) == {asset: 10000, books['payable']: -10000}
    with pytest.raises(BookflowError) as nothing:
        run('bill post', dict(purchase_order=po['id'], date='2017-01-13'), reason='Again')
    assert nothing.value.code == 'E_WORK_DEPENDENCY' and 'nothing is still open' in str(nothing.value.details)


def test_a_whole_order_bill_after_closing_every_line_is_refused(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item)
    _bill(books, po, dict(line='1', quantity='4'))
    run('purchase-order close-lines', dict(purchase_order=po['id'], lines=['1']), reason='No more coming')
    with pytest.raises(BookflowError) as refused:
        run('bill post', dict(purchase_order=po['id'], date='2017-01-12'), reason='Whole order bill')
    assert refused.value.code == 'E_WORK_DEPENDENCY'
    with pytest.raises(BookflowError) as written:
        run('bill post', dict(purchase_order=po['id'], date='2017-01-12',
                              items=[dict(item=item, quantity='6', unit_cost='10.00')]), reason='Own lines')
    assert written.value.code == 'E_WORK_DEPENDENCY'


def test_updating_or_voiding_an_order_respects_what_bills_took(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item)
    line_id = po['revision']['lines'][0]['line_id']
    four = _bill(books, po, dict(line='1', quantity='4'))

    def update(quantity):
        version = run('purchase-order show', dict(purchase_order=po['id']))['version']
        return run('purchase-order update', dict(purchase_order=po['id'], expected_version=version,
                   lines=[dict(line_id=line_id, item=item, quantity=quantity, rate='10.00')]), reason='Change')
    with pytest.raises(BookflowError) as cut:
        update('2')
    assert cut.value.code == 'E_VALIDATION' and 'already received or billed' in str(cut.value.details)
    assert update('4')['status'] == 'closed'                 # cut to exactly what was billed: done
    raised = update('20')
    assert raised['status'] == 'partly_received' and _line(books, po)[1]['open_quantity'] == '16'
    with pytest.raises(BookflowError) as dropped:
        version = run('purchase-order show', dict(purchase_order=po['id']))['version']
        run('purchase-order update', dict(purchase_order=po['id'], expected_version=version,
            lines=[dict(item=item, quantity='5', rate='10.00')]), reason='Replace the line')
    assert dropped.value.code == 'E_VALIDATION'

    with pytest.raises(BookflowError) as held:
        run('purchase-order void', dict(purchase_order=po['id']), reason='Withdraw')
    assert held.value.code == 'E_WORK_DEPENDENCY' and four['id'] in str(held.value.details)
    run('bill void', dict(bill=four['id']), reason='Billed in error')
    assert run('purchase-order void', dict(purchase_order=po['id']), reason='Withdraw')['status'] == 'voided'


def test_receiving_counts_bills_and_closed_lines_and_a_receipt_update_near_capacity(books):
    run = books['run']; item = _inventory_part(books)
    po = _order(books, item)
    line_id = po['revision']['lines'][0]['line_id']
    _bill(books, po, dict(line='1', quantity='4'))

    def receive(quantity):
        version = run('purchase-order show', dict(purchase_order=po['id']))['version']
        return run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'], purchase_order=po['id'],
            purchase_order_version=version, items=[dict(item=item, order_line_id=line_id, quantity=quantity, unit_cost='10.00')]),
            reason='Receive')
    with pytest.raises(BookflowError) as over:
        receive('7')                                          # four already arrived with the bill
    assert over.value.code == 'E_WORK_DEPENDENCY'
    receipt = receive('3')
    # Correcting the receipt at the line's capacity gives its old quantity back before claiming.
    fixed = run('item-receipt update', dict(receipt=receipt['id'], expected_version=receipt['version'],
        purchase_order_version=run('purchase-order show', dict(purchase_order=po['id']))['version'],
        items=[dict(line_id=receipt['items'][0]['line_id'], item=item, order_line_id=line_id, quantity='6', unit_cost='10.00')]),
        reason='Six arrived, not three')
    assert fixed['items'][0]['quantity_microunits'] == 6000000
    assert _line(books, po)[1]['open_quantity'] == '0'

    other = _order(books, item)
    run('purchase-order close-lines', dict(purchase_order=other['id'], lines=['1']), reason='Never coming')
    with pytest.raises(BookflowError) as closed:
        run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'], purchase_order=other['id'],
            purchase_order_version=run('purchase-order show', dict(purchase_order=other['id']))['version'],
            items=[dict(item=item, order_line_id=other['revision']['lines'][0]['line_id'], quantity='1', unit_cost='10.00')]),
            reason='Receive')
    assert closed.value.code == 'E_WORK_DEPENDENCY'
