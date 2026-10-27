"""R181 increment 3 review fixes: what keeps a pick list and the books telling the same story.

"All" on a lot-tracked line takes the suggested lot; an order whose units a live list holds is not invoiced, shipped
past the list, cut below the list, closed or voided; a list whose staged units outlive its shipment can still be
cancelled, and picking is not turned off over them; split shipments are corrected line by line; two lists never take
the same units whatever the preference.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db
from tests.test_pick_lists import _error, _on, floor  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser


def _bins(floor, item):
    """``{(bin, lot): quantity}`` non-zero placements of an item."""
    with sqlite3.connect(_db(floor['books'])) as conn:
        rows = conn.execute(
            "SELECT b.name, l.number, sum(p.quantity_microunits) FROM inventory_placements p "
            "LEFT JOIN inventory_bins b ON b.id = p.bin_id LEFT JOIN lots l ON l.id = p.lot_id WHERE p.item_id = ? "
            "GROUP BY 1, 2 HAVING sum(p.quantity_microunits) != 0", (item,)).fetchall()
    return {(b, lot): q // 1_000_000 for b, lot, q in rows}


def _stage(run):
    run('inventory-site update', dict(site='Warehouse', staging_bin='STAGE'), reason='Staging')


def _line(run, number, item):
    return next(line for line in run('pick-list show', dict(pick_list=number))['lines'] if line['item_name'] == item)


@pytest.mark.parametrize('staged', [True, False])
def test_all_on_a_lot_line_takes_the_suggested_lot(floor, staged):
    run = floor['run']
    _on(run)
    if staged:
        _stage(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    line = _line(run, number, 'Sealant')
    assert line['lot'] == 'L-SOON'
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(line=str(line['position']), mode='set', quantity=line['quantity'])]), reason='All')
    shipped = run('shipment post', dict(pick_list=number, date='2017-03-06'), reason='Truck')
    sealant = [row for row in shipped['lines'] if row['item_name'] == 'Sealant']
    assert [row['lot'] for row in sealant] == ['L-SOON']
    assert not any('unassigned' in warning for warning in shipped['warnings'])
    assert _bins(floor, floor['sealant']) == {('A2', 'L-SOON'): 1, ('A2', 'L-LATE'): 5}


def test_an_order_a_list_holds_is_not_invoiced_or_shipped_past_the_list(floor):
    run = floor['run']
    _on(run)
    _stage(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(item='Widget', quantity='3')]),
        reason='Picked')
    order = run('sales-order show', dict(sales_order=floor['first']))
    error = _error(lambda: run('sales-order invoice', dict(
        sales_order=floor['first'], expected_version=order['version'], date='2017-03-06', conversion_key='k1'),
        reason='Bill'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'picked_on_list'
    assert number in error.message and f'shipment post --pick-list {number}' in error.message
    assert error.details['pick_lists'] == [number]
    error = _error(lambda: run('shipment post', dict(sales_order=floor['first'], date='2017-03-06'), reason='Truck'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'picked_on_list'
    # Shipping from the list, then invoicing the shipment, is the way through.
    shipped = run('shipment post', dict(pick_list=number, date='2017-03-06'), reason='Truck')
    run('shipment invoice', dict(shipment=shipped['number'], date='2017-03-07'), reason='Bill')
    assert _bins(floor, floor['widget']) == {('A1', None): 7, ('A10', None): 4}


def test_an_order_a_list_holds_is_not_cut_below_it_closed_or_voided(floor):
    run = floor['run']
    _on(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    order = run('sales-order show', dict(sales_order=floor['first']))
    widget = next(line for line in order['revision']['lines']
                  if line['facts']['profile']['item']['label'] == 'Widget')
    sealant = next(line for line in order['revision']['lines'] if line['line_id'] != widget['line_id'])
    error = _error(lambda: run('sales-order update', dict(
        sales_order=floor['first'], expected_version=order['version'], lines=[
            dict(line_id=widget['line_id'], item=widget['facts']['item_id'], cancelled_quantity='2'),
            dict(line_id=sealant['line_id'], item=sealant['facts']['item_id'])]), reason='Cut'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'picked_on_list'
    assert number in error.message
    error = _error(lambda: run('sales-order update', dict(
        sales_order=floor['first'], expected_version=order['version'], status='closed'), reason='Close'))
    assert error.code == 'E_WORK_DEPENDENCY' and number in error.message
    error = _error(lambda: run('sales-order void', dict(sales_order=floor['first'], expected_version=order['version']),
                               reason='Void'))
    assert error.code == 'E_WORK_DEPENDENCY' and number in error.message
    # Adding to the order is fine; once the list is cancelled the order closes.
    run('pick-list cancel', dict(pick_list=number), reason='Not today')
    assert run('sales-order update', dict(sales_order=floor['first'], expected_version=order['version'],
                                          status='closed'), reason='Close')['status'] == 'closed'


def test_staged_units_left_after_shipping_go_back_on_cancel_and_hold_the_switch(floor):
    run = floor['run']
    _on(run)
    _stage(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(item='Widget', quantity='4'), dict(item='Sealant', quantity='2')]), reason='One too many')
    run('shipment post', dict(pick_list=number, date='2017-03-06'), reason='Truck')
    assert run('pick-list show', dict(pick_list=number))['status'] == 'done'
    assert _bins(floor, floor['widget']) == {('A1', None): 6, ('A10', None): 4, ('STAGE', None): 1}
    error = _error(lambda: run('company update', dict(pick_and_pack_enabled=False), reason='Off'))
    assert error.code == 'E_WORK_DEPENDENCY' and number in error.message
    cancelled = run('pick-list cancel', dict(pick_list=number, date='2017-03-07'), reason='Put back')
    assert cancelled['status'] == 'cancelled'
    assert _bins(floor, floor['widget']) == {('A1', None): 7, ('A10', None): 4}
    assert _bins(floor, floor['sealant']) == {('A2', 'L-SOON'): 1, ('A2', 'L-LATE'): 5}
    run('company update', dict(pick_and_pack_enabled=False), reason='Off')


def test_a_split_shipment_is_corrected_line_by_line_after_billing(floor):
    run = floor['run']
    _on(run)
    big = run('sales-order create', dict(customer=floor['acme'], date='2017-03-01', lines=[
        dict(item=floor['widget'], quantity='12', unit_price='30.00')]), reason='Big')['number']
    number = run('pick-list create', dict(orders=[big], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(code='WIDGET', bin='A1', quantity='9'), dict(code='WIDGET', bin='A10', quantity='2')]), reason='Picked')
    shipment = run('shipment post', dict(pick_list=number, date='2017-03-06'), reason='Truck')['number']
    lines = [dict(item='Widget', quantity='9', bin='A1'), dict(item='Widget', quantity='2', bin='A10')]
    assert run('shipment update', dict(shipment=shipment, expected_version=1, lines=lines),
               reason='Restate')['version'] == 2
    run('shipment invoice', dict(shipment=shipment, date='2017-03-08'), reason='Bill')
    shown = run('shipment show', dict(shipment=shipment))
    updated = run('shipment update', dict(shipment=shipment, expected_version=shown['version'], lines=list(
        reversed(lines))), reason='Restate billed')
    assert sorted((row['bin'], row['quantity']) for row in updated['lines']) == [('A1', '9'), ('A10', '2')]
    assert {row['line_identity'] for row in updated['lines']} == {row['line_identity'] for row in shown['lines']}
    error = _error(lambda: run('shipment update', dict(shipment=shipment, expected_version=updated['version'], lines=[
        dict(item='Widget', quantity='11', bin='A1')]), reason='Merge'))
    assert error.code == 'E_WORK_DEPENDENCY'
    error = _error(lambda: run('shipment update', dict(shipment=shipment, expected_version=updated['version'], lines=[
        dict(item='Widget', quantity='9'), dict(item='Widget', quantity='2')]), reason='Same twice'))
    assert error.code == 'E_VALIDATION'


def test_two_lists_never_take_the_same_units_with_the_preference_off(floor):
    run = floor['run']
    _on(run)
    run('company update', dict(available_deducts_sales_orders=False), reason='Preference')
    big = run('sales-order create', dict(customer=floor['acme'], date='2017-03-01', lines=[
        dict(item=floor['widget'], quantity='12', unit_price='30.00')]), reason='Big')['number']
    run('pick-list create', dict(orders=[big], date='2017-03-05'), reason='Wave 1')
    second = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave 2')
    widget = [line for line in second['lines'] if line['item_name'] == 'Widget']
    assert sum(float(line['quantity']) for line in widget if not line['short']) == 2
    assert [line['quantity'] for line in widget if line['short']] == ['1']


def test_the_create_form_does_not_render_while_picking_is_off(hosted):
    browser = _browser(hosted)
    base = f'/c/{hosted.company_id}'
    for path in ('/pick-list/create', '/pick-list/new/create'):
        page = browser.get(base + path)
        assert 'E_FEATURE_DISABLED' in page.text and 'Picking is off' in page.text, (path, page.text[:500])
        assert 'name="f:orders' not in page.text and 'name="c:orders' not in page.text, path
