"""R181 increment 1: the seeded checker with shipments, and the twin property (design/pick-pack-ship.md §9).

**Seeded paperwork.** A fixture company takes receipts, sales, customer returns, sales orders, shipments (by order
default and by line, past what is on hand too), shipment corrections and voids, invoices of shipments and of orders,
invoice voids, late purchases dated before shipments, freight allocated by landed cost around ship dates, counts and
costing-method switches, in random order with random dates. After every step: the asset tie, the quantity tie
(placements on every date), the journal balancing and replay owing nothing (advanced inventory §3, as the FIFO
checker reads them); and the Shipped Not Invoiced tie -- `report shipped-not-invoiced` equals the account on every
date, and each shipment line's posted recognitions equal the derived balance worked out here independently from the
rows. No valid paperwork is refused by a guard trigger, an I/O error or an internal error.

**The twin.** Two companies take the same random paperwork, except that one ships an order on day S and bills it on
day B while its twin bills it on day B by an order-made invoice that issues the stock. They end with the same profit
and loss on every date and the same receivables; from B on the same stock value and quantities; between S and B the
only difference is the stock, less by the shipped units' cost, sitting in Shipped Not Invoiced. Stock paperwork in the
generated sequences is dated before S or after B (a movement between them would rightly cost the twin's later issue
differently); landed costs, backdated purchases, short shipments filled after B, counts and FIFO switches all take
part.

Five seeds of 25 steps in an ordinary run; BF_SHIP_SEEDS and BF_SHIP_STEPS widen it (the acceptance ran 60 x 40).
"""
import os
import random
import sqlite3
from pathlib import Path

import pytest

import bookflow
from bookflow import BookflowError
from bookflow.core.exact import endpoint_share
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_fifo_seeded import _closed_counts, _properties, CLOSING
from tests.test_inventory_sites import _db, _part

SEEDS = tuple(range(181, 181 + int(os.environ.get('BF_SHIP_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_SHIP_STEPS', '25'))
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04', '2017-03-05', '2017-03-06', '2017-03-07')
NEVER = ('E_INTERNAL', 'E_IO', 'E_GUARD_REFUSED')


@pytest.fixture
def shop(books):
    run = books['run']
    clearing = run('landed-cost setup', {}, reason='Freight on stock')['account_id']
    widget = _part(books, 'Widget', cost='10.00')
    gadget = _part(books, 'Gadget', cost='4.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    for item in (widget, gadget):
        run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(
            item=item, quantity='5', unit_cost='6.00')]), reason='Opening stock')
    run('company update', dict(closing_date=CLOSING), reason='February closed')
    return dict(books=books, run=run, clearing=clearing, widget=widget, gadget=gadget, acme=acme)


def _derived_balance_holds(path):
    """Shipped Not Invoiced, worked out from the rows alone: each line's recognitions equal the derived balance on
    every date, and the lines together equal the account on every date."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute('SELECT 1 FROM shipments LIMIT 1').fetchone():
            return
        account = conn.execute("SELECT id FROM accounts WHERE system_role = 'shipped_not_invoiced'").fetchone()[0]
        current = {row['shipment_id']: row for row in conn.execute(
            'SELECT * FROM shipment_revisions r WHERE revision_number = (SELECT max(revision_number) '
            'FROM shipment_revisions x WHERE x.shipment_id = r.shipment_id)')}
        lines = [dict(row) for row in conn.execute('SELECT * FROM shipment_lines')]
        movements = [dict(row) for row in conn.execute('SELECT * FROM inventory_movements')]
        billings = [dict(row) for row in conn.execute(
            "SELECT b.*, r.date FROM shipment_billings b JOIN transactions t ON t.id = b.transaction_id "
            "JOIN transaction_revisions r ON r.id = t.current_revision_id WHERE t.status = 'posted' AND EXISTS ("
            "SELECT 1 FROM document_lines d WHERE d.revision_id = r.id AND d.line_id = b.line_id)")]
        recognised = [dict(row) for row in conn.execute('SELECT * FROM shipment_recognitions')]
        postings = [dict(row) for row in conn.execute(
            'SELECT b.effective_date, l.debit_minor_units - l.credit_minor_units AS amount FROM posting_lines l '
            'JOIN posting_batches b ON b.id = l.batch_id WHERE l.account_id = ?', (account,))]
    owner = {line['movement_id']: line['line_identity'] for line in lines if line['movement_id']}
    quantity = {}
    for line in lines:
        rev = current[line['shipment_id']]
        if line['revision_id'] == rev['id']:
            quantity[line['line_identity']] = line['quantity_microunits'] if rev['status'] == 'shipped' else 0
    dates = sorted({*DATES, '2017-02-28', '2017-03-31', *(m['effective_date'] for m in movements),
                    *(b['date'] for b in billings)})
    for date in dates:
        total = 0
        for identity in {line['line_identity'] for line in lines}:
            cost = -sum(m['value_minor_units'] for m in movements if m['effective_date'] <= date and (
                owner.get(m['id']) == identity or owner.get(m['reverses_movement_id']) == identity
                or owner.get(m['corrects_movement_id']) == identity))
            billed = sum(b['quantity_microunits'] for b in billings
                         if b['shipment_line_identity'] == identity and b['date'] <= date)
            whole = quantity.get(identity, 0)
            target = endpoint_share(cost, whole, 0, min(billed, whole)) if whole else 0
            posted = sum(r['amount_minor_units'] for r in recognised
                         if r['shipment_line_identity'] == identity and r['effective_date'] <= date)
            assert posted == target, (date, identity, posted, target)
            assert billed <= whole or not whole and not billed, (date, identity)
            total += cost - posted
        assert total == sum(p['amount'] for p in postings if p['effective_date'] <= date), date


def _waiting_shipments(run):
    return [row['number'] for row in run('shipment query', dict(not_invoiced=True, status='shipped'))['rows']]


def _scenario(shop, seed):
    books, run = shop['books'], shop['run']
    rnd = random.Random(seed)
    receipts, invoices, orders, charges, counts = [], [], [], [], []
    last_switch, method = None, 'average'
    done, refused = {}, {}
    before_close = _closed_counts(books)
    costs = ['3.10', '4.00', '7.25', '11.00']
    ops = ['buy', 'buy', 'sell', 'return', 'order', 'order_free', 'ship', 'ship', 'ship_lines', 'ship_lines', 'ship_short',
           'invoice_shipment', 'invoice_shipment', 'invoice_shipment', 'invoice_order', 'void_invoice', 'void_invoice',
           'void_shipment', 'update_shipment', 'update_shipment', 'late_buy', 'late_buy', 'freight', 'allocate',
           'allocate', 'count', 'switch', 'void_receipt']
    for quantity in ('4', '7'):
        orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=[
            dict(item=shop['widget'], quantity=quantity, unit_price='30.00'),
            dict(item=shop['gadget'], quantity='3', unit_price='20.00')]), reason='Acme ordered')['number'])
    for step in range(STEPS):
        op = rnd.choice(ops)
        date = rnd.choice(DATES)
        item, name = rnd.choice([(shop['widget'], 'Widget'), (shop['gadget'], 'Gadget')])
        try:
            if op in ('buy', 'late_buy'):
                bill = run('bill post', dict(vendor=books['vendor'], date=DATES[0] if op == 'late_buy' else date,
                                             items=[dict(item=item, quantity=str(rnd.randint(1, 9)),
                                                         unit_cost=rnd.choice(costs))]), reason='Stock in')
                receipts.append(bill['id'])
            elif op == 'sell':
                invoices.append(run('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
                    item=name, quantity=str(rnd.randint(1, 3)), unit_price='50.00')]), reason='Counter')['id'])
            elif op == 'return' and invoices:
                invoice = run('invoice show', dict(invoice=rnd.choice(invoices)))
                line = invoice['revision']['lines'][0]
                run('credit-memo post', dict(customer=shop['acme'], date=max(date, invoice['revision']['date']), lines=[dict(
                    quantity='1', source_invoice=invoice['id'], source_line=line['line_id'])]), reason='Came back')
            elif op in ('order', 'order_free'):
                # A zero-price order bills shipments on invoices of total zero, which post no line (review).
                lines = [dict(item=shop['widget'], quantity=str(rnd.randint(1, 6)),
                              unit_price='0.00' if op == 'order_free' else '30.00')]
                if rnd.random() < .5:
                    lines.append(dict(item=shop['gadget'], quantity=str(rnd.randint(1, 6)),
                                      unit_price='0.00' if op == 'order_free' else '20.00'))
                orders.append(run('sales-order create', dict(customer=shop['acme'], date=DATES[0], lines=lines),
                                  reason='Acme ordered')['number'])
            elif op == 'ship' and orders:
                run('shipment post', dict(sales_order=rnd.choice(orders), date=date), reason='Truck')
            elif op in ('ship_lines', 'ship_short') and orders:
                order = run('sales-order show', dict(sales_order=rnd.choice(orders)))
                line = rnd.choice(order['revision']['lines'])
                to_ship = int(float(line['order'].get('to_ship', line['order']['open'])))
                if to_ship <= 0:
                    continue
                run('shipment post', dict(sales_order=order['number'], date=date, lines=[dict(
                    order_line=line['line_id'], quantity=str(to_ship if op == 'ship_short' else rnd.randint(1, to_ship)))]),
                    reason='Shipped ahead of stock' if op == 'ship_short' else 'Part of it')
            elif op == 'invoice_shipment':
                waiting = _waiting_shipments(run)
                if not waiting:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(waiting)))
                invoices.append(run('shipment invoice', dict(shipment=shipment['number'], date=max(date, shipment['date'])),
                                    reason='Bill the shipment')['id'])
            elif op == 'invoice_order' and orders:
                order = run('sales-order show', dict(sales_order=rnd.choice(orders)))
                invoices.append(run('sales-order invoice', dict(sales_order=order['number'], expected_version=order['version'],
                                                                conversion_key=f'{seed}-{step}', date=date),
                                    reason='Bill the order')['id'])
            elif op == 'void_invoice' and invoices:
                invoice_id = invoices.pop(rnd.randrange(len(invoices)))
                invoice = run('invoice show', dict(invoice=invoice_id))
                run('invoice void', dict(invoice=invoice_id, expected_version=invoice['version']), reason='Void')
            elif op in ('void_shipment', 'update_shipment'):
                rows = run('shipment query', dict(status='shipped'))['rows']
                if not rows:
                    continue
                shipment = run('shipment show', dict(shipment=rnd.choice(rows)['number']))
                if op == 'void_shipment':
                    run('shipment void', dict(shipment=shipment['number'], expected_version=shipment['version']),
                        reason='Did not go')
                else:
                    line = rnd.choice(shipment['lines'])
                    run('shipment update', dict(shipment=shipment['number'], expected_version=shipment['version'],
                                                date=date, lines=[dict(order_line=line['order_line_id'],
                                                                       quantity=str(rnd.randint(1, 3)))]),
                        reason='Corrected')
            elif op == 'freight':
                charges.append(run('bill post', dict(vendor=books['vendor'], date=date, expenses=[dict(
                    account=shop['clearing'], amount=rnd.choice(['10.00', '33.33']))]), reason='Freight')['id'])
            elif op == 'allocate' and charges and receipts:
                targets = [dict(document=r) for r in rnd.sample(receipts, rnd.randint(1, min(2, len(receipts))))]
                run('landed-cost post', dict(charges=[dict(document=rnd.choice(charges))], targets=targets, date=date),
                    reason='Spread')
            elif op == 'count':
                count = run('inventory-count create', dict(site='Main', date=date), reason='Count')
                run('inventory-count enter', dict(count=count['number'], entries=[dict(
                    item=name, quantity=str(rnd.randint(0, 12)))]), reason='Counted')
                fresh = run('inventory-count show', dict(count=count['number']))
                post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=books['cogs'])
                preview = run('inventory-count post', post, reason='Count', dry_run=True)
                run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
                counts.append(count['number'])
            elif op == 'switch':
                later = [d for d in DATES if last_switch is None or d > last_switch]
                if not later:
                    continue
                chosen = rnd.choice(later)
                method = 'fifo' if method == 'average' else 'average'
                run('costing-method set', dict(method=method, effective_from=chosen), reason='Switch')
                last_switch = chosen
            elif op == 'void_receipt' and receipts:
                bill_id = receipts.pop(rnd.randrange(len(receipts)))
                bill = run('bill show', dict(bill=bill_id))
                run('bill void', dict(bill=bill_id, expected_version=bill['version']), reason='Void stock')
            else:
                continue
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if os.environ.get('BF_SHIP_DEBUG'):
                print(seed, step, op, error.code, error.message)
            assert error.code not in NEVER, (seed, step, op, error)
        _properties(shop, before_close)
        for as_of in ('2017-03-01', '2017-03-04', '2017-03-31'):
            report = run('report shipped-not-invoiced', dict(as_of=as_of))
            assert report['total'] == report['account_balance'], (seed, step, as_of)
        _derived_balance_holds(_db(books))
    print('shipment seed', seed, done, refused)
    return done


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_paperwork_with_shipments_keeps_every_tie(shop, seed):
    done = _scenario(shop, seed)
    assert done


# ---------------------------------------------------------------- the twin


def _company(client, name):
    client.organization.new(name=f'{name} organization')
    company = client.company.new(legal_name=name, home_currency='USD', timezone='UTC',
                                 organization=f'{name} organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']

    def run(command, raw, **context):
        return client.run(command, raw, company=company, **context)
    first = lambda kind: next(row['id'] for row in accounts if row['type'] == kind)
    cogs, income = first('cost_of_goods_sold'), first('income')
    vendor = client.vendor.create(company=company, name='Northside Supply')['id']
    customer = client.customer.create(company=company, name='Acme')['id']
    widget = client.item.create(company=company, name='Widget', type='inventory_part', description='Widget',
                                price='30.00', purchase_description='Widget', cost='5.00', cogs_account_id=cogs,
                                income_account_id=income)['id']
    clearing = run('landed-cost setup', {}, reason='Freight')['account_id']
    return dict(company=company, run=run, vendor=vendor, customer=customer, widget=widget, clearing=clearing,
                name=name)


def _path(root, name):
    for path in Path(root).rglob('company.db'):
        if path.parent.name == name:
            return path
    raise AssertionError(name)


def _daily(path):
    """``{(account type or role, date): net}`` for every posting, and the account kinds."""
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT CASE WHEN a.system_role IN ('inventory_asset', 'shipped_not_invoiced') THEN a.system_role "
            "ELSE a.type END, b.effective_date, sum(l.debit_minor_units - l.credit_minor_units) "
            "FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id JOIN accounts a ON a.id = l.account_id "
            "GROUP BY 1, 2").fetchall()
        quantities = conn.execute('SELECT effective_date, sum(quantity_microunits) FROM inventory_movements '
                                  'GROUP BY effective_date').fetchall()
    return {(kind, date): amount for kind, date, amount in rows if amount}, dict(quantities)


def _at(daily, kind, date):
    return sum(amount for (k, d), amount in daily.items() if k == kind and d <= date)


@pytest.mark.parametrize('method', ['average', 'fifo'])
@pytest.mark.parametrize('seed', SEEDS)
def test_the_invoice_later_equals_the_invoice_at_once(tmp_path, monkeypatch, seed, method):
    root = tmp_path / 'twins'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    late, once = _company(client, 'Ships First'), _company(client, 'Bills At Once')
    rnd = random.Random(seed * 7 + (method == 'fifo'))
    days = [f'2017-03-{day:02d}' for day in range(1, 21)]
    ship_day, bill_day = sorted(rnd.sample(days[4:16], 2))
    outside = [d for d in days if d < ship_day or d > bill_day]
    plan = []
    if method == 'fifo':
        plan.append(('switch', rnd.choice([d for d in outside if d < ship_day])))
    plan.append(('buy', days[0], rnd.randint(3, 8), rnd.choice(['4.00', '6.50'])))
    plan.append(('order', days[0], rnd.randint(2, 9)))
    for _ in range(rnd.randint(2, 6)):
        plan.append(rnd.choice([('buy', rnd.choice(outside), rnd.randint(1, 6), rnd.choice(['3.10', '7.25', '11.00'])),
                                ('sell', rnd.choice(outside), rnd.randint(1, 2)),
                                ('freight', rnd.choice(days), rnd.choice(['10.00', '33.33']))]))
    plan.append(('ship', ship_day))
    for _ in range(rnd.randint(1, 5)):
        plan.append(rnd.choice([
            ('buy', rnd.choice([d for d in outside if d < ship_day]), rnd.randint(1, 5), '9.00'),   # late paperwork
            ('buy', rnd.choice([d for d in outside if d > bill_day]), rnd.randint(1, 5), '2.00'),   # fills a short ship
            ('allocate', rnd.choice([d for d in days if ship_day <= d <= bill_day])),
            ('count', rnd.choice([d for d in outside if d > bill_day]), rnd.randint(0, 6)),
            ('sell', rnd.choice([d for d in outside if d > bill_day]), 1)]))
    plan.append(('bill', bill_day))
    for _ in range(rnd.randint(0, 3)):
        plan.append(rnd.choice([('buy', rnd.choice([d for d in outside if d < ship_day]), 2, '8.00'),
                                ('sell', rnd.choice([d for d in outside if d > bill_day]), 1),
                                ('allocate', rnd.choice(days))]))
    state = {late['name']: dict(receipts=[], charges=[], order=None, shipment=None),
             once['name']: dict(receipts=[], charges=[], order=None, shipment=None)}
    for step in plan:
        for twin in (late, once):
            mine, run = state[twin['name']], twin['run']
            op = step[0]
            try:
                if op == 'switch':
                    run('costing-method set', dict(method='fifo', effective_from=step[1]), reason='FIFO')
                elif op == 'buy':
                    mine['receipts'].append(run('bill post', dict(vendor=twin['vendor'], date=step[1], items=[dict(
                        item=twin['widget'], quantity=str(step[2]), unit_cost=step[3])]), reason='In')['id'])
                elif op == 'order':
                    mine['order'] = run('sales-order create', dict(customer=twin['customer'], date=step[1], lines=[dict(
                        item=twin['widget'], quantity=str(step[2]), unit_price='30.00')]), reason='Order')
                    mine['quantity'] = step[2]
                elif op == 'sell':
                    run('invoice post', dict(customer=twin['customer'], date=step[1], lines=[dict(
                        item=twin['widget'], quantity=str(step[2]), unit_price='25.00')]), reason='Counter')
                elif op == 'freight':
                    mine['charges'].append(run('bill post', dict(vendor=twin['vendor'], date=step[1], expenses=[dict(
                        account=twin['clearing'], amount=step[2])]), reason='Freight')['id'])
                elif op == 'allocate':
                    if not mine['charges'] or not mine['receipts']:
                        continue
                    run('landed-cost post', dict(charges=[dict(document=mine['charges'].pop())],
                                                 targets=[dict(document=mine['receipts'][0])], date=step[1]),
                        reason='Spread')
                elif op == 'count':
                    if step[1] in mine.setdefault('counted', set()):
                        continue                           # one count of a place on a date
                    mine['counted'].add(step[1])
                    count = run('inventory-count create', dict(site='Main', date=step[1]), reason='Count')
                    run('inventory-count enter', dict(count=count['number'], entries=[dict(
                        item='Widget', quantity=str(step[2]))]), reason='Counted')
                    fresh = run('inventory-count show', dict(count=count['number']))
                    accounts = twin_accounts(twin)
                    post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=accounts)
                    preview = run('inventory-count post', post, reason='Count', dry_run=True)
                    run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']),
                        reason='Count')
                elif op == 'ship' and twin is late:
                    mine['shipment'] = run('shipment post', dict(sales_order=mine['order']['number'], date=step[1],
                        lines=[dict(item='Widget', quantity=str(mine['quantity']))]), reason='Shipped')['number']
                elif op == 'bill':
                    if twin is late:
                        run('shipment invoice', dict(shipment=mine['shipment'], date=step[1]), reason='Billed later')
                    else:
                        order = run('sales-order show', dict(sales_order=mine['order']['number']))
                        run('sales-order invoice', dict(sales_order=order['number'], expected_version=order['version'],
                                                        conversion_key='at-once', date=step[1], all_open=True),
                            reason='Shipped and billed')
            except BookflowError as error:
                assert error.code not in NEVER, (seed, step, error)
                raise AssertionError(f'{twin["name"]} refused {step}: {error.code} {error.message}') from None
    a, a_quantities = _daily(_path(root, late['name']))
    b, b_quantities = _daily(_path(root, once['name']))
    for date in ['2017-02-28', *days, '2017-03-31']:
        for kind in ('income', 'cost_of_goods_sold', 'expense', 'accounts_receivable'):
            assert _at(a, kind, date) == _at(b, kind, date), (seed, method, kind, date, ship_day, bill_day)
        stock_a = _at(a, 'inventory_asset', date)
        waiting = _at(a, 'shipped_not_invoiced', date)
        assert stock_a + waiting == _at(b, 'inventory_asset', date), (seed, method, date)
        if date < ship_day or date >= bill_day:
            assert waiting == 0 and stock_a == _at(b, 'inventory_asset', date), (seed, method, date)
            assert sum(v for d, v in a_quantities.items() if d <= date) == \
                sum(v for d, v in b_quantities.items() if d <= date), (seed, method, date)


def twin_accounts(twin):
    accounts = twin['run']('account query', dict(limit=200))['items']
    return next(row['id'] for row in accounts if row['type'] == 'cost_of_goods_sold')
