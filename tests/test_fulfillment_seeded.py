"""R181 increment 5: the seeded shipments checker, extended to the fulfillment worksheet (design/pick-pack-ship.md §10).

The shipments fixture company, picking and packing on, takes random receipts (some dated later than others), sales
orders, shipments from the order and by line, pick lists (made, picked, shipped from, closed, cancelled), drafts (made,
packed, shipped, cancelled), order invoices and shipment invoices, in random order with random dates. After every
step, for every open order the worksheet lists, its can-ship-now equals what the dry run of `shipment post
--sales-order` would ship (nothing when that is refused), and every open order with stock still to ship is listed.
No valid paperwork is refused by a guard trigger, an I/O error or an internal error.

Five seeds of 25 steps in an ordinary run; BF_SHEET_SEEDS and BF_SHEET_STEPS widen it.
"""
import os
import random
from fractions import Fraction

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_fulfillment_worksheet import can_ship, would_ship
from tests.test_shipments_seeded import DATES, NEVER, _waiting_shipments, shop  # noqa: F401

SEEDS = tuple(range(1850, 1850 + int(os.environ.get('BF_SHEET_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_SHEET_STEPS', '25'))


def _to_ship(line):
    figures = line.get('order') or {}
    return Fraction(figures.get('to_ship', figures.get('open', '0')) or '0')


def _worksheet_ties(run, orders, stocked):
    rows = {row['sales_order_number']: row for row in run('report order-fulfillment', dict(limit=200))['rows']}
    checked = 0
    for number in orders:
        shown = run('sales-order show', dict(sales_order=number))
        to_ship = sum(_to_ship(line) for line in shown['revision']['lines'] if line['facts']['item_id'] in stocked)
        if shown['status'] == 'open' and shown['active'] and to_ship > 0:
            assert number in rows, (number, to_ship)
        if number in rows:
            assert can_ship(rows[number]) == would_ship(run, number), (number, rows[number])
            checked += 1
    return checked


def _scenario(shop, seed):
    run = shop['run']
    rnd = random.Random(seed)
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick and pack')
    orders, done, refused, checked = [], {}, {}, 0

    def order():
        lines = [dict(item=shop['widget'], quantity=str(rnd.randint(1, 5)), unit_price='30.00')]
        if rnd.random() < .6:
            lines.append(dict(item=shop['gadget'], quantity=str(rnd.randint(1, 4)), unit_price='20.00'))
        orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=lines),
                          reason='Acme ordered')['number'])

    for _ in range(3):
        order()

    def lists(status):
        return [row['number'] for row in run('pick-list query', dict(status=status, limit=200))['rows']]

    ops = ['order', 'buy', 'buy', 'ship', 'ship_lines', 'pick', 'pick', 'enter', 'enter', 'enter', 'ship_pick',
           'ship_pick', 'close', 'cancel_pick', 'draft', 'pack', 'ship_draft', 'cancel_draft', 'invoice_order',
           'invoice_shipment']
    for step in range(STEPS):
        op = rnd.choice(ops)
        date = rnd.choice(DATES)
        try:
            if op == 'order':
                order()
            elif op == 'buy':
                run('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
                    item=rnd.choice([shop['widget'], shop['gadget']]), quantity=str(rnd.randint(3, 12)),
                    unit_cost='4.00')]), reason='Stock in')
            elif op == 'ship':
                run('shipment post', dict(sales_order=rnd.choice(orders), date=date), reason='Truck')
            elif op == 'ship_lines':
                number = rnd.choice(orders)
                lines = [dict(order_line=line['line_id'], quantity='1')
                         for line in run('sales-order show', dict(sales_order=number))['revision']['lines']
                         if _to_ship(line) >= 1][:1]
                if not lines:
                    continue
                run('shipment post', dict(sales_order=number, date=date, lines=lines), reason='Truck, past stock')
            elif op == 'pick':
                run('pick-list create', dict(orders=rnd.sample(orders, min(len(orders), rnd.randint(1, 2))),
                                             date=date), reason='Wave')
            elif op == 'enter':
                found = lists('open')
                if not found:
                    continue
                shown = run('pick-list show', dict(pick_list=rnd.choice(found)))
                line = rnd.choice(shown['lines'])
                run('pick-list enter', dict(pick_list=shown['number'], date=max(date, shown['date']), entries=[dict(
                    line=str(line['position']), quantity=str(rnd.randint(1, 3)))]), reason='Picked')
            elif op == 'ship_pick':
                found = lists('open') + lists('done')
                if not found:
                    continue
                shown = run('pick-list show', dict(pick_list=rnd.choice(found)))
                run('shipment post', dict(pick_list=shown['number'], sales_order=rnd.choice(shown['orders'])['sales_order'],
                                          date=max(date, shown['date'])), reason='Shipped what was picked')
            elif op in ('close', 'cancel_pick'):
                found = lists('open') + (lists('done') if op == 'cancel_pick' else [])
                if not found:
                    continue
                run('pick-list ' + ('close' if op == 'close' else 'cancel'), dict(pick_list=rnd.choice(found)),
                    reason='Done')
            elif op == 'draft':
                run('shipment create', dict(sales_order=rnd.choice(orders), date=date), reason='Draft')
            elif op in ('pack', 'ship_draft', 'cancel_draft'):
                drafts = [row['number'] for row in run('shipment query', dict(status='draft', limit=200))['rows']]
                if not drafts:
                    continue
                number = rnd.choice(drafts)
                if op == 'pack':
                    run('shipment pack', dict(shipment=number, line='1', box=1), reason='Boxed')
                elif op == 'ship_draft':
                    run('shipment post', dict(shipment=number, date=date), reason='Truck')
                else:
                    run('shipment cancel', dict(shipment=number), reason='Not today')
            elif op == 'invoice_order':
                shown = run('sales-order show', dict(sales_order=rnd.choice(orders)))
                run('sales-order invoice', dict(sales_order=shown['number'], expected_version=shown['version'],
                                                conversion_key=f'{seed}-{step}', date=DATES[-1]), reason='Bill')
            elif op == 'invoice_shipment':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                run('shipment invoice', dict(shipment=rnd.choice(waiting), date=DATES[-1]), reason='Bill')
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if os.environ.get('BF_SHEET_DEBUG'):
                print(seed, step, op, error.code, error.message)
            assert error.code not in NEVER, (seed, step, op, error)
        checked += _worksheet_ties(run, orders, {shop['widget'], shop['gadget']})
    print('worksheet seed', seed, 'checked', checked, done, refused)
    return done, checked


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_worksheet_can_ship_now_is_what_shipment_post_ships(shop, seed):
    done, checked = _scenario(shop, seed)
    assert checked > 0 and sum(done.values()) > 0
