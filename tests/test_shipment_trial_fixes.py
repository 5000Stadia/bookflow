"""R181 closing-trial fixes (notes/trials/r181-trial/results.md).

1. Availability is by site: `shipment post --sales-order` and the worksheet's can-ship-now read what the shipping site
   holds (less what documents commit there), and the shipment names the bins and lots its units leave, so stock at
   another site never lets a shipment drive this site's shelves below zero. Seeded over two sites with lots.
2. `sales-order invoice` needs no date, conversion key or expected version: "bill Acme" is one plain call.
3. `shipment post --pick-list` with `lines` ships part of what was picked; the rest stays on the list.
4. `barcode lookup` on a serial number or lot says where it is in fulfillment: pick list, draft or shipment, box.
5. JSON numbers are taken where decimal text is asked (quantity, line), exactly; a `reason` put inside `input` is told
   where it goes.
"""
import os
import random
import sqlite3
from fractions import Fraction

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_fulfillment_worksheet import can_ship, would_ship
from tests.test_inventory_sites import _db
from tests.test_pick_lists import _item, _on, floor  # noqa: F401

NEVER = ('E_INTERNAL', 'E_IO', 'E_GUARD_REFUSED')


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def _places(books):
    """``{(item, site, bin, lot): quantity}`` straight from the placements, numbers left out."""
    with sqlite3.connect(_db(books)) as conn:
        rows = conn.execute(
            'SELECT i.full_name, s.name, b.full_name, l.number, sum(p.quantity_microunits) FROM inventory_placements p '
            'JOIN items i ON i.id = p.item_id JOIN inventory_sites s ON s.id = p.site_id '
            'LEFT JOIN inventory_bins b ON b.id = p.bin_id LEFT JOIN lots l ON l.id = p.lot_id '
            'GROUP BY 1, 2, 3, 4').fetchall()
    return {(item, site, bin_, lot): Fraction(q, 1_000_000) for item, site, bin_, lot, q in rows if q}


@pytest.fixture
def depots(books):
    """A warehouse with bins A1 and A2 and a van; sealant (lot-tracked) 6 in A2 lot L-LATE and 2 in A1 lot L-SOON at
    the warehouse, 2 lot L-VAN in the van; Acme orders 9."""
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse', tracks_bins=True), reason='Our warehouse')
    run('inventory-site create', dict(name='Van 1'), reason='The van')
    for name in ('A1', 'A2'):
        run('bin create', dict(site='Warehouse', name=name), reason='Shelving')
    sealant = _item(books, 'Sealant', 'lot', cost='3.00')
    run('item update', dict(item='Sealant', site_settings=[dict(site='Warehouse', default_bin='A2')]), reason='Home')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=sealant, quantity='6', unit_cost='3.00', site='Warehouse', bin='A2', lot='L-LATE',
             lot_expiry='2017-12-31'),
        dict(item=sealant, quantity='2', unit_cost='3.00', site='Warehouse', bin='A1', lot='L-SOON',
             lot_expiry='2017-06-30'),
        dict(item=sealant, quantity='2', unit_cost='3.00', site='Van 1', lot='L-VAN')]), reason='Stock in')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    order = run('sales-order create', dict(customer=acme, date='2017-03-02', lines=[
        dict(item=sealant, quantity='9', unit_price='12.50')]), reason='Acme ordered')
    return dict(books=books, run=run, sealant=sealant, acme=acme, order=order['number'])


def _row(run, number, **filters):
    rows = run('report order-fulfillment', dict(limit=200, **filters))['rows']
    return next((row for row in rows if row['sales_order_number'] == number), None)


def test_a_shipment_takes_only_what_its_site_holds_by_bin_and_lot(depots):
    run, books, number = depots['run'], depots['books'], depots['order']
    row = _row(run, number)
    line = row['lines'][0]['order_line_id']
    # The warehouse holds 8; the van's 2 do not ship from the warehouse.
    assert row['site_name'] == 'Warehouse' and can_ship(row) == {line: 8} == would_ship(run, number)
    by_site = {site['site_name']: site for site in row['by_site']}
    assert not by_site['Warehouse']['complete']
    preview = run('shipment post', dict(sales_order=number, date='2017-03-05'), reason='Truck', dry_run=True)
    assert {(l['bin'], l['lot'], l['quantity']) for l in preview['lines']} == {('A2', 'L-LATE', '6'),
                                                                              ('A1', 'L-SOON', '2')}
    shipped = run('shipment post', dict(sales_order=number, date='2017-03-05'), reason='Truck')
    assert not shipped['warnings'], shipped['warnings']
    places = _places(books)
    assert all(q >= 0 for q in places.values()), places
    assert places[('Sealant', 'Van 1', None, 'L-VAN')] == 2
    # The warehouse is empty now: nothing can ship from it, and the last one ships from the van.
    row = _row(run, number)
    assert can_ship(row) == {} == would_ship(run, number)
    error = _error(lambda: run('shipment post', dict(sales_order=number, date='2017-03-06'), reason='Truck'))
    assert error.code == 'E_VALIDATION' and error.details['reason'] == 'nothing_to_ship'
    assert can_ship(_row(run, number, site='Van 1')) == {line: 1} == would_ship(run, number, 'Van 1')
    van = run('shipment post', dict(sales_order=number, site='Van 1', date='2017-03-06'), reason='Van run')
    assert [(l['lot'], l['quantity']) for l in van['lines']] == [('L-VAN', '1')] and not van['warnings']


SEEDS = tuple(range(1910, 1910 + int(os.environ.get('BF_SITE_SEEDS', '3'))))
STEPS = int(os.environ.get('BF_SITE_STEPS', '18'))


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_two_sites_with_lots_never_ship_past_the_site(depots, seed):
    """Random receipts at either site (bins and lots), transfers between them, orders (some naming the van), and
    shipments from the order alone at either site: after every step each open order's can-ship-now at each site is
    what the dry run ships there, and no site, bin or lot is below zero."""
    run, books, sealant = depots['run'], depots['books'], depots['sealant']
    rnd = random.Random(seed)
    widget = _item(books, 'Widget', cost='4.00')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=widget, quantity='4', unit_cost='4.00', site='Warehouse', bin='A1')]), reason='Widgets')
    orders = [depots['order']]
    lots = ('L-SOON', 'L-LATE', 'L-VAN', 'L-NEW')
    for step in range(STEPS):
        op = rnd.choice(['buy', 'buy', 'move', 'order', 'ship', 'ship', 'ship'])
        date = f'2017-03-{rnd.randint(3, 9):02d}'
        try:
            if op == 'buy':
                site = rnd.choice(['Warehouse', 'Van 1'])
                item = rnd.choice([sealant, widget])
                line = dict(item=item, quantity=str(rnd.randint(1, 5)), unit_cost='3.00', site=site)
                if site == 'Warehouse':
                    line['bin'] = rnd.choice(['A1', 'A2'])
                if item == sealant:
                    line['lot'] = rnd.choice(lots)
                run('bill post', dict(vendor=books['vendor'], date=date, items=[line]), reason='Stock in')
            elif op == 'move':
                held = [(key, q) for key, q in _places(books).items() if key[1] == 'Warehouse' and q >= 1]
                if held:
                    (item, _, bin_, lot), q = rnd.choice(held)
                    line = dict(item=item, quantity=str(min(int(q), rnd.randint(1, 3))), from_bin=bin_)
                    if lot:
                        line['lot'] = lot
                    run('inventory-transfer post', dict(date=date, from_site='Warehouse', to_site='Van 1',
                                                        lines=[line]), reason='Load the van')
            elif op == 'order':
                lines = [dict(item=sealant, quantity=str(rnd.randint(1, 6)), unit_price='12.50')]
                if rnd.random() < .5:
                    lines.append(dict(item=widget, quantity=str(rnd.randint(1, 3)), unit_price='30.00'))
                if rnd.random() < .3:
                    lines[0]['site'] = 'Van 1'
                orders.append(run('sales-order create', dict(customer=depots['acme'], date='2017-03-02',
                                                             lines=lines), reason='Ordered')['number'])
            else:
                site = rnd.choice(['Warehouse', 'Van 1'])
                shipped = run('shipment post', dict(sales_order=rnd.choice(orders), site=site, date='2017-03-09'),
                              reason='Truck')
                assert not [w for w in shipped['warnings'] if 'now shows -' in w], (seed, step, shipped['warnings'])
        except BookflowError as error:
            assert error.code not in NEVER, (seed, step, op, error)
        assert all(q >= 0 for key, q in _places(books).items()), (seed, step, op, _places(books))
        for site in ('Warehouse', 'Van 1'):
            rows = {row['sales_order_number']: row for row in
                    run('report order-fulfillment', dict(limit=200, site=site))['rows']}
            for number, row in rows.items():
                assert can_ship(row) == would_ship(run, number, site), (seed, step, site, number)


# ---------------------------------------------------------------- 2. sales-order invoice


def test_bill_the_customer_is_one_plain_sales_order_invoice(depots):
    run, number = depots['run'], depots['order']
    run('shipment post', dict(sales_order=number, date='2017-03-05', lines=[dict(item='Sealant', quantity='3')]),
        reason='First truck')
    run('shipment post', dict(sales_order=number, date='2017-03-06', lines=[dict(item='Sealant', quantity='2')]),
        reason='Second truck')
    preview = run('sales-order invoice', dict(sales_order=number), reason='Bill Acme', dry_run=True)
    assert [line['quantity'] for line in preview['revision']['lines']] == ['5']
    made = run('sales-order invoice', dict(sales_order=number), reason='Bill Acme')
    assert made['total']['amount'] == '62.50' and made['revision']['date']
    left = run('shipment query', dict(sales_order=number, not_invoiced=True))
    assert not left.get('rows', left.get('items'))
    from bookflow.core import registry
    text = registry.get('sales-order invoice').description
    assert 'Posting issues the stock' not in text and 'shipped and not yet invoiced' in text


# ---------------------------------------------------------------- 3. part of a pick list


def test_part_of_a_pick_list_ships_and_the_rest_stays_on_the_list(floor):
    run, books, first = floor['run'], floor['books'], floor['first']
    _on(run)
    run('inventory-site update', dict(site='Warehouse', staging_bin='STAGE'), reason='Staging area')
    number = run('pick-list create', dict(orders=[first], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(item='Widget', quantity=3), dict(item='Sealant', quantity='2', lot='L-SOON')]), reason='Picked')
    too_many = _error(lambda: run('shipment post', dict(pick_list=number, date='2017-03-06', lines=[
        dict(item='Widget', quantity='4')]), reason='Truck'))
    assert too_many.code == 'E_VALIDATION' and '3 picked' in too_many.message
    part = run('shipment post', dict(pick_list=number, date='2017-03-06', lines=[dict(item='Widget', quantity='1')]),
               reason='Truck took one')
    assert [(l['item_name'], l['quantity'], l['bin']) for l in part['lines']] == [('Widget', '1', 'STAGE')]
    shown = run('pick-list show', dict(pick_list=number))
    assert shown['status'] == 'open' and any('--pick-list' in n for n in shown['next_commands'])
    row = _row(run, first)
    widget_line = next(l for l in row['lines'] if l['item_name'] == 'Widget')
    assert Fraction(widget_line['on_picks']) == 2
    # Shipping past the list is refused, and the refusal offers shipping part of the list before cancelling it.
    refused = _error(lambda: run('shipment post', dict(sales_order=first, date='2017-03-06'), reason='Truck'))
    assert refused.details['reason'] == 'picked_on_list'
    assert refused.message.index('`lines`') < refused.message.index('pick-list cancel')
    rest = run('shipment post', dict(pick_list=number, date='2017-03-07'), reason='The rest')
    assert {(l['item_name'], l['quantity']) for l in rest['lines']} == {('Widget', '2'), ('Sealant', '2')}
    assert run('pick-list show', dict(pick_list=number))['status'] == 'done'
    places = _places(books)
    assert not places.get(('Widget', 'Warehouse', 'STAGE', None)) and places[('Widget', 'Warehouse', 'A1', None)] == 7


# ---------------------------------------------------------------- 4. barcode lookup's fulfillment


def test_barcode_lookup_says_which_list_shipment_and_box_hold_a_unit(floor):
    run, second = floor['run'], floor['second']
    _on(run)
    assert run('barcode lookup', dict(code='WH-1001'))['match']['fulfillment'] == []
    number = run('pick-list create', dict(orders=[second], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(code='WH-1001'), dict(code='WH-1002')]), reason='Picked')
    found = run('barcode lookup', dict(code='WH-1001'))
    assert [(p['kind'], p['number'], p['sales_order']) for p in found['match']['fulfillment']] == [
        ('pick_list', number, second)]
    shipped = run('shipment post', dict(pick_list=number, date='2017-03-06', lines=[
        dict(item='Water Heater', serial_numbers=['WH-1001', 'WH-1002'])]), reason='Truck')
    run('shipment pack', dict(shipment=shipped['number'], unit='WH-1001', box=2), reason='Box 2')
    found = run('barcode lookup', dict(code='WH-1001'))
    assert [(p['kind'], p['number'], p['boxes']) for p in found['match']['fulfillment']] == [
        ('shipment', shipped['number'], [2])]
    assert f'shipment {shipped["number"]}' in found['message'] and 'box 2' in found['message']
    assert run('barcode lookup', dict(code='WIDGET'))['match'].get('fulfillment') is None


# ---------------------------------------------------------------- 5. small slips


def test_json_numbers_are_taken_exactly_where_decimal_text_is_asked(floor):
    run, first = floor['run'], floor['first']
    _on(run)
    number = run('pick-list create', dict(orders=[first], date='2017-03-05'), reason='Wave')['number']
    out = run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(line=1, quantity=2), dict(line=2, quantity=0.5)]), reason='Picked')
    picked = sorted(Fraction(line['picked']) for line in out['lines'])
    assert picked == [Fraction(1, 2), 2]
    for bad in (True, 1e-7, float('nan')):
        assert _error(lambda: run('pick-list enter', dict(pick_list=number, entries=[
            dict(line=1, quantity=bad)]), reason='x')).code == 'E_VALIDATION'


def test_a_reason_inside_input_is_told_where_it_goes(floor):
    error = _error(lambda: floor['run']('shipment post', dict(sales_order=floor['first'], reason='Truck'),
                                        reason='Truck'))
    assert error.code == 'E_CONTEXT_IN_INPUT' and error.details['fields'] == ['reason']
    assert 'out of input' in error.message and '"reason"' in error.message


def test_pick_everything_ready_names_every_order_it_took(floor):
    run = floor['run']
    _on(run)
    made = run('pick-list create', dict(ready=True, date='2017-03-05'), reason='Pick all')
    assert {order['sales_order'] for order in made['orders']} == {floor['first'], floor['second']}
