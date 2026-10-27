"""R181 increment 4: the seeded checker with packing (design/pick-pack-ship.md §5, §6, §9).

A fixture company with a weighed widget, an unweighed gadget and serial water heaters takes receipts, sales orders,
draft shipments (from the order and by line), packing (whole packings, a unit moved into a box, unpacked to box 0,
repacked, boxes weighed), drafts shipped and cancelled, shipments posted with boxes and with `--invoice`, shipment
corrections that cut a line, returns, voids, invoices of shipments (freight to bill on some), in random order with
random dates. After every step the ties of increment 1 hold -- asset, quantity on every date, journal balanced,
Shipped Not Invoiced equal to its report and to the derived balance -- and the box ties: on every current revision
of a shipment or draft, no box holds a line the revision does not carry, no line has more in boxes than it ships,
every serial number in a box is one its line ships and none is in two boxes; a box's weight is its contents' weight
plus tare, or its measured weight; live drafts of an order hold no more than it has to ship; a shipment's freight is
billed by at most one live invoice line. No valid paperwork is refused by a guard trigger, an I/O error or an
internal error.

Five seeds of 25 steps in an ordinary run; BF_PACK_SEEDS and BF_PACK_STEPS widen it.
"""
import json
import os
import random
import sqlite3
from fractions import Fraction

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_fifo_seeded import _closed_counts, _properties
from tests.test_inventory_sites import _db
from tests.test_pick_lists import _item
from tests.test_shipments_seeded import DATES, NEVER, _derived_balance_holds, _waiting_shipments, shop  # noqa: F401

SEEDS = tuple(range(1840, 1840 + int(os.environ.get('BF_PACK_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_PACK_STEPS', '25'))
UNIT = 1_000_000


def _current(conn, table, key):
    return {row['id']: dict(row) for row in conn.execute(
        f'SELECT * FROM {table} r WHERE revision_number = (SELECT max(revision_number) FROM {table} x '
        f'WHERE x.{key} = r.{key})')}


def _box_ties(path, run):
    """Every current revision's boxes against its lines, read from the rows (the serial numbers each shipped line
    took, from `shipment show`)."""
    shipped = {}
    for row in run('shipment query', dict(status='shipped', limit=500))['rows']:
        for line in run('shipment show', dict(shipment=row['number']))['lines']:
            shipped[line['line_identity']] = set(line.get('serial_numbers') or ())
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        weights = {row['id']: row['unit_weight_microunits'] for row in conn.execute('SELECT id, unit_weight_microunits '
                                                                                   'FROM items')}
        revisions = []
        for rev in _current(conn, 'shipment_revisions', 'shipment_id').values():
            if rev['status'] != 'shipped':
                assert not conn.execute('SELECT 1 FROM shipment_packages WHERE revision_id = ?', (rev['id'],)).fetchone()
                continue
            lines = {line['line_identity']: (int(line['quantity_microunits']), shipped.get(line['line_identity'], set()),
                                             line['item_id'])
                     for line in conn.execute('SELECT * FROM shipment_lines WHERE revision_id = ?', (rev['id'],))}
            revisions.append((rev['id'], lines))
        for rev in _current(conn, 'shipment_draft_revisions', 'draft_id').values():
            if rev['status'] != 'draft':
                continue
            lines = {line['line_identity']: (int(line['quantity_microunits']),
                                             set(json.loads(line['serial_numbers'] or '[]')), line['item_id'])
                     for line in conn.execute('SELECT * FROM shipment_draft_lines WHERE draft_id = ?', (rev['draft_id'],))}
            revisions.append((rev['id'], lines))
        checked = 0
        for revision_id, lines in revisions:
            boxed, numbers = {}, []
            for box in conn.execute('SELECT * FROM shipment_packages WHERE revision_id = ?', (revision_id,)):
                weight = int(box['tare_microunits'] or 0)
                for content in conn.execute('SELECT * FROM shipment_package_contents WHERE package_id = ?', (box['id'],)):
                    assert content['revision_id'] == revision_id and content['line_identity'] in lines
                    quantity, serials, item = lines[content['line_identity']]
                    boxed[content['line_identity']] = boxed.get(content['line_identity'], 0) + content['quantity_microunits']
                    held = json.loads(content['serial_numbers'] or '[]')
                    if held:
                        assert len(held) * UNIT == content['quantity_microunits'] and set(held) <= serials, held
                    numbers += held
                    if weights.get(item):
                        weight += Fraction(content['quantity_microunits'] * weights[item], UNIT)
                checked += 1
            assert len(numbers) == len(set(numbers))
            for identity, quantity in boxed.items():
                assert quantity <= lines[identity][0], (identity, quantity, lines[identity])
        return checked


def _draft_holds(run):
    """Live drafts of an order hold, together, no more than each order line has to ship."""
    held, to_ship = {}, {}
    for row in run('shipment query', dict(status='draft', limit=500))['rows']:
        draft = run('shipment show', dict(shipment=row['number']))
        for line in draft['lines']:
            key = (draft['sales_order_number'], line['order_line_id'])
            held[key] = held.get(key, 0) + Fraction(line['quantity'])
            to_ship[key] = Fraction(draft['order_to_ship'][line['order_line_id']])
    for key, quantity in held.items():
        assert quantity <= to_ship[key], (key, quantity, to_ship[key])


def _freight_once(path):
    with sqlite3.connect(path) as conn:
        assert not conn.execute(
            "SELECT b.shipment_id FROM shipment_freight_billings b JOIN transactions t ON t.id = b.transaction_id "
            "WHERE t.status = 'posted' AND EXISTS (SELECT 1 FROM document_lines d WHERE d.revision_id = "
            "t.current_revision_id AND d.line_id = b.line_id) GROUP BY b.shipment_id HAVING count(*) > 1").fetchall()


def _weights(run, number):
    shown = run('shipment show', dict(shipment=number))
    for box in (shown.get('packing') or {}).get('packages') or ():
        computed = Fraction(box['tare'] or 0) + sum(Fraction(c['weight']) for c in box['contents'] if c['weight'])
        assert Fraction(box['computed_weight']) == computed
        assert Fraction(box['weight']) == (Fraction(box['measured_weight']) if box['measured_weight'] else computed)


def _scenario(shop, seed):
    books, run = shop['books'], shop['run']
    rnd = random.Random(seed)
    before_close = _closed_counts(books)
    run('company update', dict(pick_and_pack_enabled=True), reason='We pack')
    run('item update', dict(item=shop['widget'], unit_weight='2.5'), reason='Weighed')
    heater = _item(books, 'Water Heater', 'serial', barcode='HEATER', cost='100.00')
    run('item update', dict(item=heater, unit_weight='40'), reason='Weighed')
    serials = [f'WH-{n}' for n in range(1001, 1013)]
    run('bill post', dict(vendor=books['vendor'], date=DATES[0], items=[
        dict(item=heater, quantity='12', unit_cost='100.00', serial_numbers=serials)]), reason='Heaters')
    shipping = books['client'].item.create(company=books['company'], name='Shipping', type='other_charge',
                                           description='Shipping', price='0', sales_enabled=True,
                                           purchase_enabled=False, income_account_id=books['income'])['id']
    run('company update', dict(shipping_item_id=shipping), reason='Freight item')
    orders, done, refused = [], {}, {}

    def order():
        lines = [dict(item=shop['widget'], quantity=str(rnd.randint(1, 4)), unit_price='30.00')]
        if rnd.random() < .6:
            lines.append(dict(item=shop['gadget'], quantity=str(rnd.randint(1, 3)), unit_price='20.00'))
        if rnd.random() < .6:
            lines.append(dict(item=heater, quantity=str(rnd.randint(1, 2)), unit_price='300.00'))
        orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=lines),
                          reason='Acme ordered')['number'])

    for _ in range(3):
        order()

    def boxes_for(shown):
        """A random whole packing of a shipment's lines."""
        count = rnd.randint(1, 3)
        packages = [dict(box=n, contents=[]) for n in range(1, count + 1)]
        for line in shown['lines']:
            if rnd.random() < .2:
                continue   # left unboxed
            target = rnd.choice(packages)
            if line.get('serial_numbers'):
                taken = rnd.sample(line['serial_numbers'], rnd.randint(1, len(line['serial_numbers'])))
                target['contents'].append(dict(line=str(line['position']), serial_numbers=taken))
            else:
                quantity = Fraction(line['quantity'])
                part = max(1, int(quantity * rnd.choice([Fraction(1, 2), 1])))
                target['contents'].append(dict(line=str(line['position']), quantity=str(min(part, quantity))))
        if rnd.random() < .4:
            packages[0]['weight'] = rnd.choice(['3.25', '41', '90.5'])
        if rnd.random() < .4:
            packages[-1]['tare'] = rnd.choice(['0', '1.5'])
        return packages

    def ship_lines(number):
        shown = run('sales-order show', dict(sales_order=number))
        lines = []
        for line in shown['revision']['lines']:
            figures = line.get('order') or {}
            to_ship = Fraction(figures.get('to_ship', figures.get('open', '0')) or '0')
            if to_ship <= 0:
                continue
            quantity = min(to_ship, rnd.randint(1, 3))
            entry = dict(order_line=line['line_id'], quantity=str(quantity))
            if line['facts']['item_id'] == heater:
                on_hand = [n for n in serials if n not in shipped_serials()]
                if len(on_hand) < quantity:
                    continue
                entry['serial_numbers'] = rnd.sample(on_hand, int(quantity))
            lines.append(entry)
        return lines

    def shipped_serials():
        with sqlite3.connect(_db(books)) as conn:
            return {row[0] for row in conn.execute(
                "SELECT s.number FROM inventory_placements p JOIN serial_numbers s ON s.id = p.serial_number_id "
                "GROUP BY s.id HAVING sum(p.quantity_microunits) <= 0")} | {
                n for row in conn.execute(
                    "SELECT l.serial_numbers FROM shipment_draft_lines l JOIN shipment_draft_revisions r "
                    "ON r.draft_id = l.draft_id WHERE r.revision_number = (SELECT max(revision_number) FROM "
                    "shipment_draft_revisions x WHERE x.draft_id = r.draft_id) AND r.status = 'draft'")
                for n in json.loads(row[0] or '[]')}

    def live(status):
        return [row['number'] for row in run('shipment query', dict(status=status, limit=500))['rows']]

    ops = ['order', 'buy', 'draft', 'draft', 'draft_lines', 'pack', 'pack', 'move', 'move', 'move', 'unpack',
           'repack', 'weigh', 'ship_draft', 'ship_draft', 'cancel_draft', 'ship_boxes', 'ship_invoice', 'cut',
           'return', 'void_shipment', 'invoice_shipment']
    for step in range(STEPS):
        op = rnd.choice(ops)
        date = rnd.choice(DATES)
        try:
            if op == 'order':
                order()
            elif op == 'buy':
                run('bill post', dict(vendor=books['vendor'], date=DATES[0], items=[dict(
                    item=rnd.choice([shop['widget'], shop['gadget']]), quantity=str(rnd.randint(1, 9)),
                    unit_cost=rnd.choice(['3.10', '4.00', '7.25']))]), reason='Stock in')
            elif op in ('draft', 'draft_lines'):
                number = rnd.choice(orders)
                body = dict(sales_order=number, date=date)
                if op == 'draft_lines':
                    lines = ship_lines(number)
                    if not lines:
                        continue
                    body['lines'] = lines
                if rnd.random() < .4:
                    body['freight_amount'] = rnd.choice(['5.00', '12.50'])
                run('shipment create', body, reason='Draft')
            elif op in ('pack', 'repack', 'move', 'unpack', 'weigh'):
                choices = live('draft') + (live('shipped') if rnd.random() < .5 else [])
                if not choices:
                    continue
                shown = run('shipment show', dict(shipment=rnd.choice(choices)))
                if not shown['lines']:
                    continue
                body = dict(shipment=shown['number'], expected_version=shown['version'])
                if op in ('pack', 'repack'):
                    body['packages'] = boxes_for(shown) if op == 'pack' or rnd.random() < .7 else []
                elif op == 'weigh':
                    body.update(box=rnd.randint(1, 3), weight=rnd.choice(['0', '5.5', '120']),
                                tracking_number=f'1Z{step}')
                else:
                    line = rnd.choice(shown['lines'])
                    if line.get('serial_numbers'):
                        body['unit'] = rnd.choice(line['serial_numbers'])
                    else:
                        body['line'] = str(line['position'])
                        body['quantity'] = str(rnd.randint(1, 2))
                    body['box'] = 0 if op == 'unpack' else rnd.randint(1, 3)
                run('shipment pack', body, reason='Packed')
                _weights(run, shown['number'])
            elif op == 'ship_draft':
                drafts = live('draft')
                if not drafts:
                    continue
                shown = run('shipment show', dict(shipment=rnd.choice(drafts)))
                run('shipment post', dict(shipment=shown['number'], date=date, invoice=rnd.random() < .3),
                    reason='Truck')
            elif op == 'cancel_draft':
                drafts = live('draft')
                if not drafts:
                    continue
                run('shipment cancel', dict(shipment=rnd.choice(drafts)), reason='Not today')
            elif op in ('ship_boxes', 'ship_invoice'):
                number = rnd.choice(orders)
                lines = ship_lines(number)
                if not lines:
                    continue
                body = dict(sales_order=number, date=date, lines=lines)
                if op == 'ship_invoice':
                    body['invoice'] = True
                    if rnd.random() < .5:
                        body['freight_amount'] = '9.00'
                else:
                    body['packages'] = [dict(contents=[dict(line='1')])]
                run('shipment post', body, reason='Truck', idempotency_key=f'{seed}-{step}')
                if op == 'ship_invoice':
                    # A retry under the same key writes nothing again.
                    before = run('shipment query', dict(limit=500))['total']
                    run('shipment post', body, reason='Truck', idempotency_key=f'{seed}-{step}')
                    assert run('shipment query', dict(limit=500))['total'] == before
            elif op == 'cut':
                shipped = live('shipped')
                if not shipped:
                    continue
                shown = run('shipment show', dict(shipment=rnd.choice(shipped)))
                lines = []
                for line in shown['lines']:
                    entry = dict(order_line=line['order_line_id'])
                    quantity = Fraction(line['quantity'])
                    if line.get('serial_numbers'):
                        kept = line['serial_numbers'][:max(1, len(line['serial_numbers']) - 1)]
                        entry.update(quantity=str(len(kept)), serial_numbers=kept)
                    else:
                        entry['quantity'] = str(max(1, quantity - 1))
                    lines.append(entry)
                run('shipment update', dict(shipment=shown['number'], expected_version=shown['version'],
                                            lines=lines), reason='Fewer went')
            elif op == 'return':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(waiting)))
                run('shipment return', dict(shipment=shipment['number'], date=max(date, shipment['date'])),
                    reason='Came back')
            elif op == 'void_shipment':
                shipped = live('shipped')
                if not shipped:
                    continue
                shown = run('shipment show', dict(shipment=rnd.choice(shipped)))
                run('shipment void', dict(shipment=shown['number'], expected_version=shown['version']),
                    reason='Did not go')
            elif op == 'invoice_shipment':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(waiting)))
                run('shipment invoice', dict(shipment=shipment['number'], date=max(date, shipment['date'])),
                    reason='Bill')
            else:
                continue
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if os.environ.get('BF_PACK_DEBUG'):
                print(seed, step, op, error.code, error.message)
            assert error.code not in NEVER, (seed, step, op, error)
        _properties(shop, before_close)
        for as_of in ('2017-03-01', '2017-03-04', '2017-03-31'):
            report = run('report shipped-not-invoiced', dict(as_of=as_of))
            assert report['total'] == report['account_balance'], (seed, step, as_of)
        _derived_balance_holds(_db(books))
        _box_ties(_db(books), run)
        _draft_holds(run)
        _freight_once(_db(books))
    print('pack seed', seed, done, refused)
    return done


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_paperwork_with_packing_keeps_every_tie(shop, seed):
    done = _scenario(shop, seed)
    assert sum(done.get(op, 0) for op in ('pack', 'move', 'repack', 'unpack', 'weigh')) > 0
