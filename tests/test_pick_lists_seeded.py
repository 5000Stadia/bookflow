"""R181 increment 3: the seeded checker with pick lists (design/pick-pack-ship.md §4, §9).

A fixture company with bins (and, on even seeds, a staging bin) takes receipts into bins, sales orders, pick lists
(named orders and every ready order), picks (add, set, set lower, "All" on any line, a lot-tracked one included),
closes and cancels, shipments posted from the lists and from the orders, shipment returns and voids, invoices of
shipments and of orders, order cuts and closes, and counts, in random order with random dates. After every step the ties of increment 1 hold -- asset, quantity on every date, journal balanced, Shipped Not
Invoiced equal to its report and to the derived balance -- and the pick ties: every pick placement nets to zero per
item, lot and serial number, under each entry and each cancelling revision; a list never plans units another live
list holds for the same order line (what is planned and held stays within what the line has to ship); no serial
number is picked on two live lists; a pick on a lot-tracked line the list suggested a lot for names a lot; no live
list holds units of an order that is not open. No valid paperwork is refused by a guard trigger, an I/O error or an internal
error.

Five seeds of 25 steps in an ordinary run; BF_PICK_SEEDS and BF_PICK_STEPS widen it.
"""
import os
import random
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_fifo_seeded import _closed_counts, _properties
from tests.test_inventory_sites import _db
from tests.test_pick_lists import _item
from tests.test_shipments_seeded import DATES, NEVER, _derived_balance_holds, _waiting_shipments, shop  # noqa: F401

SEEDS = tuple(range(1810, 1810 + int(os.environ.get('BF_PICK_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_PICK_STEPS', '25'))


def _held(run):
    """``{(order number, order line id): held}`` by live lists, read from their public outputs."""
    out = {}
    for row in run('pick-list query', dict(limit=500))['rows']:
        if row['status'] == 'cancelled':
            continue
        shown = run('pick-list show', dict(pick_list=row['number']))
        shipped = {order['sales_order'] for order in shown['orders'] if order['shipments']}
        planned, picked = {}, {}
        for line in shown['lines']:
            if line['sales_order'] in shipped:
                continue
            key = (line['sales_order'], line['order_line_id'])
            if not line['short']:
                planned[key] = planned.get(key, 0) + float(line['quantity'])
            picked[key] = picked.get(key, 0) + float(line['picked'])
        for key in set(planned) | set(picked):
            held = max(planned.get(key, 0), picked.get(key, 0)) if shown['status'] == 'open' else picked.get(key, 0)
            out[key] = out.get(key, 0) + held
    return out


def _to_ship(run, number):
    order = run('sales-order show', dict(sales_order=number))
    return {line['line_id']: float(line['order'].get('to_ship', line['order']['open']))
            for line in order['revision']['lines'] if line.get('order')}


def _pick_ties(path):
    with sqlite3.connect(path) as conn:
        # "All" (set) and add alike take the lot the list suggested when the picker names none.
        assert not conn.execute(
            "SELECT 1 FROM pick_entries e JOIN pick_list_lines l ON l.id = e.line_id WHERE l.lot_id IS NOT NULL "
            "AND e.lot_id IS NULL AND e.quantity_microunits > 0").fetchall()
        assert not conn.execute(
            "SELECT 1 FROM inventory_placements WHERE source = 'pick' GROUP BY source_line_id, item_id, lot_id, "
            "serial_number_id HAVING sum(quantity_microunits) != 0").fetchall()
        assert not conn.execute(
            "SELECT 1 FROM inventory_placements WHERE source = 'pick' GROUP BY item_id, lot_id, serial_number_id "
            "HAVING sum(quantity_microunits) != 0").fetchall()


def _scenario(shop, seed):
    books, run = shop['books'], shop['run']
    rnd = random.Random(seed)
    before_close = _closed_counts(books)
    run('inventory-site update', dict(site='Main', tracks_bins=True), reason='Shelves')
    for name in ('A1', 'A2', 'A10', 'STAGE'):
        run('bin create', dict(site='Main', name=name), reason='Shelving')
    run('item update', dict(item=shop['widget'], site_settings=[dict(site='Main', default_bin='A1')]), reason='Home')
    if seed % 2 == 0:
        run('inventory-site update', dict(site='Main', staging_bin='STAGE'), reason='Staging')
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick')
    sealant = _item(books, 'Sealant', 'lot')
    run('bill post', dict(vendor=books['vendor'], date=DATES[0], items=[
        dict(item=sealant, quantity='6', unit_cost='2.00', bin='A2', lot='L-LATE', lot_expiry='2017-12-31'),
        dict(item=sealant, quantity='4', unit_cost='2.00', bin='A2', lot='L-SOON', lot_expiry='2017-06-30')]),
        reason='Sealant in')
    orders, invoices, done, refused = [], [], {}, {}
    for bin_, quantity in (('A1', '6'), ('A2', '4'), ('A10', '5')):
        for stocked in (shop['widget'], shop['gadget']):
            run('bill post', dict(vendor=books['vendor'], date=DATES[0], items=[dict(
                item=stocked, quantity=quantity, unit_cost='4.00', bin=bin_)]), reason='Shelved stock')
    for quantity in ('3', '5', '2'):
        orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=[
            dict(item=shop['widget'], quantity=quantity, unit_price='30.00'),
            dict(item=shop['gadget'], quantity='2', unit_price='20.00'),
            dict(item=sealant, quantity='1', unit_price='9.00')]), reason='Acme ordered')['number'])
    ops = ['enter_all', 'enter_all', 'order_cut', 'order_close', 'order_invoice', 'buy', 'order', 'order', 'pick', 'pick', 'pick', 'pick_ready', 'enter', 'enter', 'enter', 'enter', 'enter',
           'enter_set', 'close', 'cancel', 'ship_pick', 'ship_pick', 'ship_pick', 'ship_pick', 'ship', 'return',
           'void_shipment', 'invoice_shipment', 'count']
    for step in range(STEPS):
        op = rnd.choice(ops)
        date = rnd.choice(DATES)
        item, name = rnd.choice([(shop['widget'], 'Widget'), (shop['gadget'], 'Gadget')])
        try:
            if op == 'buy':
                run('bill post', dict(vendor=books['vendor'], date=DATES[0], items=[dict(
                    item=item, quantity=str(rnd.randint(1, 9)), unit_cost=rnd.choice(['3.10', '4.00', '7.25']),
                    bin=rnd.choice(['A1', 'A2', 'A10']))]), reason='Stock in')
            elif op == 'order':
                lines = [dict(item=shop['widget'], quantity=str(rnd.randint(1, 6)), unit_price='30.00')]
                if rnd.random() < .5:
                    lines.append(dict(item=shop['gadget'], quantity=str(rnd.randint(1, 6)), unit_price='20.00'))
                if rnd.random() < .5:
                    lines.append(dict(item=sealant, quantity=str(rnd.randint(1, 3)), unit_price='9.00'))
                orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=lines),
                                  reason='Acme ordered')['number'])
            elif op in ('pick', 'pick_ready'):
                before = _held(run)
                body = dict(date=date)
                if op == 'pick' and orders:
                    body['orders'] = rnd.sample(orders, rnd.randint(1, min(3, len(orders))))
                else:
                    body['ready'] = True
                    body['complete_only'] = rnd.random() < .3
                made = run('pick-list create', body, reason='Wave')
                planned = {}
                for line in made['lines']:
                    if not line['short']:
                        key = (line['sales_order'], line['order_line_id'])
                        planned[key] = planned.get(key, 0) + float(line['quantity'])
                # A second list never re-picks what another holds: planned plus held stays within to ship.
                for (number, line_id), quantity in planned.items():
                    assert quantity + before.get((number, line_id), 0) <= _to_ship(run, number)[line_id] + 1e-9, (
                        seed, step, number, line_id)
            elif op in ('enter', 'enter_set'):
                rows = run('pick-list query', dict(status='open'))['rows']
                if not rows:
                    continue
                shown = run('pick-list show', dict(pick_list=rnd.choice(rows)['number']))
                line = rnd.choice(shown['lines'])
                entry = dict(line=str(line['position']), quantity=str(rnd.randint(0 if op == 'enter_set' else 1, 4)),
                             mode='set' if op == 'enter_set' else 'add')
                if rnd.random() < .3:
                    entry['bin'] = rnd.choice(['A1', 'A2', 'A10'])
                run('pick-list enter', dict(pick_list=shown['number'], date=date, entries=[entry]), reason='Picked')
            elif op == 'enter_all':
                rows = run('pick-list query', dict(status='open'))['rows']
                if not rows:
                    continue
                shown = run('pick-list show', dict(pick_list=rnd.choice(rows)['number']))
                lots = [line for line in shown['lines'] if line['lot']]
                line = rnd.choice(lots if lots and rnd.random() < .7 else shown['lines'])
                run('pick-list enter', dict(pick_list=shown['number'], date=date, entries=[dict(
                    line=str(line['position']), mode='set', quantity=line['quantity'])]), reason='All')
            elif op in ('order_cut', 'order_close', 'order_invoice') and orders:
                number = rnd.choice(orders)
                order = run('sales-order show', dict(sales_order=number))
                if op == 'order_close':
                    run('sales-order update', dict(sales_order=number, expected_version=order['version'],
                                                   status='closed'), reason='Closed')
                elif op == 'order_invoice':
                    invoices.append(run('sales-order invoice', dict(
                        sales_order=number, expected_version=order['version'], date=date,
                        conversion_key=f'{seed}-{step}'), reason='Bill')['id'])
                else:
                    lines = order['revision']['lines']
                    cut = rnd.choice(lines)
                    body = [dict(line_id=line['line_id'], item=line['facts']['item_id']) for line in lines]
                    body[lines.index(cut)]['cancelled_quantity'] = str(rnd.randint(1, 3))
                    run('sales-order update', dict(sales_order=number, expected_version=order['version'], lines=body),
                        reason='Cut')
            elif op in ('close', 'cancel'):
                rows = [r for r in run('pick-list query', {})['rows'] if r['status'] in (
                    ('open',) if op == 'close' else ('open', 'done'))]
                if not rows:
                    continue
                body = dict(pick_list=rnd.choice(rows)['number'])
                if op == 'cancel':
                    body['date'] = date
                run(f'pick-list {op}', body, reason='Done' if op == 'close' else 'Not today')
            elif op == 'ship_pick':
                rows = [r for r in run('pick-list query', {})['rows'] if r['status'] != 'cancelled'
                        and r['picked_lines']]
                if not rows:
                    continue
                shown = run('pick-list show', dict(pick_list=rnd.choice(rows)['number']))
                waiting = [o['sales_order'] for o in shown['orders'] if not o['shipments']]
                if not waiting:
                    continue
                run('shipment post', dict(pick_list=shown['number'], sales_order=rnd.choice(waiting), date=date),
                    reason='Truck')
            elif op == 'ship' and orders:
                run('shipment post', dict(sales_order=rnd.choice(orders), date=date), reason='Truck')
            elif op == 'return':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(waiting)))
                run('shipment return', dict(shipment=shipment['number'], date=max(date, shipment['date'])),
                    reason='Came back')
            elif op == 'void_shipment':
                rows = run('shipment query', dict(status='shipped'))['rows']
                if not rows:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(rows)['number']))
                run('shipment void', dict(shipment=shipment['number'], expected_version=shipment['version']),
                    reason='Did not go')
            elif op == 'invoice_shipment':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(waiting)))
                invoices.append(run('shipment invoice', dict(shipment=shipment['number'],
                                                             date=max(date, shipment['date'])), reason='Bill')['id'])
            elif op == 'count':
                count = run('inventory-count create', dict(site='Main', date=date), reason='Count')
                run('inventory-count enter', dict(count=count['number'], entries=[dict(
                    item=name, quantity=str(rnd.randint(0, 12)), bin=rnd.choice(['A1', 'A2']))]), reason='Counted')
                fresh = run('inventory-count show', dict(count=count['number']))
                post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=books['cogs'])
                preview = run('inventory-count post', post, reason='Count', dry_run=True)
                run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
            else:
                continue
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if os.environ.get('BF_PICK_DEBUG'):
                print(seed, step, op, error.code, error.message)
            assert error.code not in NEVER, (seed, step, op, error)
        _properties(shop, before_close)
        for as_of in ('2017-03-01', '2017-03-04', '2017-03-31'):
            report = run('report shipped-not-invoiced', dict(as_of=as_of))
            assert report['total'] == report['account_balance'], (seed, step, as_of)
        _derived_balance_holds(_db(books))
        _pick_ties(_db(books))
        # A live list holds only units of open orders (cuts, closes, voids and invoices are refused around it).
        for (number, _), quantity in _held(run).items():
            if quantity:
                assert run('sales-order show', dict(sales_order=number))['status'] == 'open', (seed, step, number)
        serials = [n for row in run('pick-list query', dict(limit=500))['rows'] if row['status'] != 'cancelled'
                   for line in run('pick-list show', dict(pick_list=row['number']))['lines']
                   for n in line['picked_serial_numbers']]
        assert len(serials) == len(set(serials))
    print('pick seed', seed, done, refused)
    return done


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_paperwork_with_pick_lists_keeps_every_tie(shop, seed):
    done = _scenario(shop, seed)
    assert done.get('pick') or done.get('pick_ready')
