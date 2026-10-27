"""R104 increment 7: the seeded checker under FIFO (design/advanced-inventory.md §3 properties, §9 increment 7).

A fixture company switches between average and FIFO, both ways, at random dates, while receipts, sales, customer
returns, vendor returns naming their receipt line and naming none, write-ups and write-downs, found quantities,
counts, transfers, freight allocations, builds and voids are entered in random order with random dates. After
every step: the asset tie, the quantity tie (placements on every date) and the journal balancing; and the three
properties -- each item's posted value and quantity are exactly what replay says under the company's timeline, with
nothing owed; placements equal replay's quantity; and no write dated after the closing date produced a row dated on
or before it. A run with one unit cost throughout checks, after every step, that FIFO equals average. No valid
paperwork is ever refused by a guard trigger, an I/O error or an internal error.

Five seeds of 25 steps in an ordinary run; BF_FIFO_SEEDS and BF_FIFO_STEPS widen it (the acceptance ran 60 x 40).
"""
import json
import os
import random
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.company.inventory_costing import replay, totals
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db, _part, _tie_every_date
from tests.test_landed_cost import _asset

SEEDS = tuple(range(71, 71 + int(os.environ.get('BF_FIFO_SEEDS', '5'))))
STEPS = int(os.environ.get('BF_FIFO_STEPS', '25'))
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04', '2017-03-05', '2017-03-06')
CLOSING = '2017-02-28'
NEVER = ('E_INTERNAL', 'E_IO', 'E_GUARD_REFUSED')


@pytest.fixture
def shop(books):
    run = books['run']
    clearing = run('landed-cost setup', {}, reason='Freight on stock')['account_id']
    widget = _part(books, 'Widget', cost='10.00')
    gadget = _part(books, 'Gadget', cost='4.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    van = run('inventory-site create', dict(name='Van 1'), reason='The van')
    kit = books['client'].item.create(company=books['company'], name='Widget Kit', type='inventory_assembly',
                                      description='Kit', price='90.00', purchase_description='Kit', cost='0.00',
                                      cogs_account_id=books['cogs'], income_account_id=books['income'],
                                      members=[dict(component_item_id=widget, quantity='2')])['id']
    # Stock from before the close, then the close: nothing after may reach back into it.
    for item in (widget, gadget):
        run('bill post', dict(vendor=books['vendor'], date='2017-02-01', items=[dict(
            item=item, quantity='5', unit_cost='6.00')]), reason='Opening stock')
    run('company update', dict(closing_date=CLOSING), reason='February closed')
    return dict(books=books, run=run, clearing=clearing, widget=widget, gadget=gadget, acme=acme, kit=kit, van=van)


def _ledger(books):
    """Every item's movements as replay reads them (landed costs and named receipts followed to the receipt standing
    for them now), and the company's standing timeline, straight from the file."""
    with sqlite3.connect(_db(books)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute('SELECT * FROM inventory_movements')]
        carries = dict(conn.execute('SELECT from_movement_id, to_movement_id FROM landed_cost_carries').fetchall())
        timeline = tuple((r[0], r[1]) for r in conn.execute(
            "SELECT effective_from, method FROM costing_methods WHERE status = 'standing' ORDER BY effective_from"))
        line_of = dict(conn.execute('SELECT m.id, d.line_id FROM inventory_movements m JOIN document_lines d '
                                    'ON d.id = m.document_line_id').fetchall())
    retired = {row['reverses_movement_id'] for row in rows if row['kind'] == 'reversal'}
    standing = {}
    for row in rows:
        if row['kind'] == 'receipt' and row['returns_movement_id'] is None and row['id'] not in retired:
            standing[(row['item_id'], line_of.get(row['id']))] = row['id']
    by_item = {}
    for row in rows:
        if row['kind'] == 'landed':
            target = row['landed_on_movement_id']
            while target in carries:
                target = carries[target]
            row['landed_on_movement_id'] = target
        if row['kind'] == 'vendor_return' and row['returns_movement_id'] in retired:
            row['returns_movement_id'] = standing.get((row['item_id'], line_of.get(row['returns_movement_id'])),
                                                      row['returns_movement_id'])
        by_item.setdefault(row['item_id'], []).append(row)
    return by_item, timeline


def _properties(shop, before_close, same_cost=False):
    books, run = shop['books'], shop['run']
    _tie_every_date(books)                                     # placements equal the stock ledger on every date
    with sqlite3.connect(_db(books)) as conn:
        net = {account: int(value) for account, value in conn.execute(
            'SELECT account_id, sum(debit_minor_units - credit_minor_units) FROM posting_lines GROUP BY account_id')}
        dated = conn.execute('SELECT count(*) FROM inventory_movements WHERE effective_date <= ?', (CLOSING,)).fetchone()[0]
        placed = conn.execute('SELECT count(*) FROM inventory_placements WHERE effective_date <= ?', (CLOSING,)).fetchone()[0]
        lines = conn.execute('SELECT count(*) FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id '
                             'WHERE b.effective_date <= ?', (CLOSING,)).fetchone()[0]
    valuation = run('report inventory-valuation', dict(as_of='2017-12-31', limit=200))
    assert valuation['totals']['asset_value']['minor_units'] == net.get(_asset(books), 0)
    assert sum(net.values()) == 0
    # No write dated after the closing date produced a row dated on or before it.
    assert (dated, placed, lines) == before_close
    by_item, timeline = _ledger(books)
    for item_id, rows in by_item.items():
        state = replay(rows, timeline)
        # Posted value equals the replay's value, and nothing is owed.
        assert state.corrections == (), (item_id, state.corrections[:3])
        assert totals(rows) == (state.quantity_microunits, state.value_minor_units), item_id
        if state.quantity_microunits > 0:
            assert sum(layer.value_minor_units for layer in state.layers) == state.value_minor_units
        if same_cost:
            fifo = replay(rows, (('2017-01-01', 'fifo'),))
            average = replay(rows)
            assert fifo.targets == average.targets and fifo.value_minor_units == average.value_minor_units, item_id


def _closed_counts(books):
    with sqlite3.connect(_db(books)) as conn:
        return (conn.execute('SELECT count(*) FROM inventory_movements WHERE effective_date <= ?', (CLOSING,)).fetchone()[0],
                conn.execute('SELECT count(*) FROM inventory_placements WHERE effective_date <= ?', (CLOSING,)).fetchone()[0],
                conn.execute('SELECT count(*) FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id '
                             'WHERE b.effective_date <= ?', (CLOSING,)).fetchone()[0])


def _scenario(shop, seed, *, same_cost):
    books, run = shop['books'], shop['run']
    rnd = random.Random(seed)
    receipts, invoices, charges, allocations, builds, counts = [], [], [], [], [], []
    last_switch, method = None, 'average'
    done, refused = {}, {}
    before_close = _closed_counts(books)
    costs = ['6.00'] if same_cost else ['3.10', '4.00', '7.25', '11.00']
    ops = ['buy', 'buy', 'buy', 'sell', 'sell', 'return', 'vendor_named', 'vendor_named', 'vendor_plain', 'found', 'count',
           'transfer', 'switch', 'switch', 'void_invoice', 'void_receipt', 'build', 'void_build']
    if not same_cost:
        ops += ['write', 'freight', 'allocate', 'allocate']
    for step in range(STEPS):
        op = rnd.choice(ops)
        date = rnd.choice(DATES)
        item, name = rnd.choice([(shop['widget'], 'Widget'), (shop['gadget'], 'Gadget')])
        try:
            if op == 'buy':
                bill = run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=item, quantity=str(rnd.randint(1, 9)), unit_cost=rnd.choice(costs))]), reason='Stock in')
                receipts.append((bill['id'], item, bill['revision']['items'][0]['line_id']))
            elif op == 'sell':
                invoice = run('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
                    item=name, quantity=str(rnd.randint(1, 4)), unit_price='50.00')]), reason='Job')
                invoices.append(invoice['id'])
            elif op == 'return' and invoices:
                invoice = run('invoice show', dict(invoice=rnd.choice(invoices)))
                line = invoice['revision']['lines'][0]
                run('credit-memo post', dict(customer=shop['acme'], date=max(date, invoice['revision']['date']), lines=[dict(
                    quantity='1', source_invoice=invoice['id'], source_line=line['line_id'])]), reason='Came back')
            elif op == 'vendor_named' and receipts:
                bill_id, bill_item, line_id = rnd.choice(receipts)
                bill = run('bill show', dict(bill=bill_id))
                run('vendor-credit post', dict(vendor=books['vendor'], date=max(date, bill['revision']['date']),
                                               ap_account=books['payable'], items=[dict(
                    item=bill_item, quantity='1', unit_cost=rnd.choice(costs), receipt_line=line_id)]), reason='Back to its receipt')
            elif op == 'vendor_plain':
                run('vendor-credit post', dict(vendor=books['vendor'], date=date, ap_account=books['payable'], items=[dict(
                    item=item, quantity='1', unit_cost=rnd.choice(costs))]), reason='Back')
            elif op == 'write':
                run('inventory adjust', dict(item=name, date=date, adjustment_account=books['cogs'],
                                             value_change=rnd.choice(['1.00', '2.35']), negative_value=rnd.random() < .5),
                    reason='Revalue')
            elif op == 'found':
                run('inventory adjust', dict(item=name, date=date, adjustment_account=books['cogs'], quantity_change='1'),
                    reason='Found one')
            elif op == 'count':
                count = run('inventory-count create', dict(site='Main', date=date), reason='Count')
                run('inventory-count enter', dict(count=count['number'], entries=[dict(
                    item=name, quantity=str(rnd.randint(0, 12)))]), reason='Counted')
                fresh = run('inventory-count show', dict(count=count['number']))
                post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=books['cogs'])
                preview = run('inventory-count post', post, reason='Count', dry_run=True)
                run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
                counts.append(count['number'])
            elif op == 'transfer':
                run('inventory-transfer post', dict(date=date, from_site='Main', to_site='Van 1', lines=[dict(
                    item=name, quantity='1')]), reason='Load the van')
            elif op == 'switch':
                later = [d for d in DATES if last_switch is None or d > last_switch]
                if later:
                    chosen = rnd.choice(later)
                    method = 'fifo' if method == 'average' else 'average'
                    run('costing-method set', dict(method=method, effective_from=chosen), reason='Switch')
                    last_switch = chosen
            elif op == 'void_invoice' and invoices:
                invoice_id = invoices.pop(rnd.randrange(len(invoices)))
                invoice = run('invoice show', dict(invoice=invoice_id))
                run('invoice void', dict(invoice=invoice_id, expected_version=invoice['version']), reason='Void')
            elif op == 'void_receipt' and receipts:
                bill_id = receipts.pop(rnd.randrange(len(receipts)))[0]
                bill = run('bill show', dict(bill=bill_id))
                run('bill void', dict(bill=bill_id, expected_version=bill['version']), reason='Void stock')
            elif op == 'build':
                builds.append(run('assembly build', dict(item=shop['kit'], quantity='1', date=date), reason='Kit')['build_id'])
            elif op == 'void_build' and builds:
                run('assembly void', dict(build=builds.pop(rnd.randrange(len(builds)))), reason='Built in error')
            elif op == 'freight':
                charges.append(run('bill post', dict(vendor=books['vendor'], date=date, expenses=[dict(
                    account=shop['clearing'], amount=rnd.choice(['10.00', '33.33'])), ]), reason='Freight')['id'])
            elif op == 'allocate' and charges and receipts:
                targets = [dict(document=r[0]) for r in rnd.sample(receipts, rnd.randint(1, min(2, len(receipts))))]
                allocations.append(run('landed-cost post', dict(charges=[dict(document=rnd.choice(charges))],
                                                                targets=targets), reason='Spread')['allocation']['number'])
            else:
                continue
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if os.environ.get('BF_FIFO_DEBUG'):
                print(seed, step, op, error.code, error.message)
            assert error.code not in NEVER, (seed, step, op, error)
            # A switch dated after the close and after the last one is always accepted.
            assert op != 'switch', (seed, step, op, error)
        _properties(shop, before_close, same_cost)
    print('fifo seed', seed, 'same cost' if same_cost else '', done, refused)
    return done


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_paperwork_under_both_methods_keeps_every_property(shop, seed):
    _scenario(shop, seed, same_cost=False)


@pytest.mark.parametrize('seed', SEEDS[:2])
def test_seeded_paperwork_at_one_unit_cost_is_the_same_under_fifo_and_average(shop, seed):
    _scenario(shop, seed, same_cost=True)


def test_builds_under_fifo_take_component_layers_and_the_assembly_becomes_a_layer(shop):
    run, books = shop['run'], shop['books']
    run('costing-method set', dict(method='fifo', effective_from='2017-03-01'), reason='FIFO')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-02', items=[dict(
        item=shop['widget'], quantity='4', unit_cost='20.00')]), reason='Dearer widgets')
    # The kit takes 2 widgets oldest first: 2 of the opening layer (5 at 6.00) = 12.00, and becomes a layer.
    run('assembly build', dict(item=shop['kit'], quantity='1', date='2017-03-04'), reason='Kit')

    def kit():
        rows = run('report inventory-layers', dict(as_of='2017-03-31'))['rows']
        return [(row['quantity'], row['value']['minor_units']) for row in rows if row['item_name'] == 'Widget Kit']
    assert kit() == [('1', 1200)]
    # A sale backdated before the build takes 4 of the opening layer: the build now takes its last widget at 6.00
    # and one of the 20.00 layer, so the kit is worth 26.00, corrected at the build's date.
    run('invoice post', dict(customer=shop['acme'], date='2017-03-03', lines=[dict(
        item='Widget', quantity='4', unit_price='50.00')]), reason='Backdated job')
    assert kit() == [('1', 2600)]
    _properties(shop, _closed_counts(books))


def test_the_demo_stays_on_average(root):
    found = 0
    for path in root.rglob('*.db'):
        with sqlite3.connect(path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='costing_methods'").fetchone():
                assert conn.execute('SELECT count(*) FROM costing_methods').fetchone()[0] == 0
                found += 1
    assert found


def test_a_build_on_a_day_whose_count_finds_its_components_only_at_the_count_is_refused_cleanly(shop):
    """R103: the build sits before that day's count, so the units the count finds come after it; the build would
    be short, and says so (it once raised KeyError 'transaction_id' on its own planned issue)."""
    run, books = shop['run'], shop['books']
    run('invoice post', dict(customer=shop['acme'], date='2017-03-02', lines=[dict(
        item='Widget', quantity='5', unit_price='50.00')]), reason='Sold out')
    count = run('inventory-count create', dict(site='Main', date='2017-03-05'), reason='Count')
    run('inventory-count enter', dict(count=count['number'], entries=[dict(item='Widget', quantity='10')]), reason='Counted')
    fresh = run('inventory-count show', dict(count=count['number']))
    post = dict(count=count['number'], expected_version=fresh['version'], adjustment_account=books['cogs'])
    preview = run('inventory-count post', post, reason='Count', dry_run=True)
    run('inventory-count post', dict(post, preview_fingerprint=preview['preview_fingerprint']), reason='Count')
    with pytest.raises(BookflowError) as refused:
        run('assembly build', dict(item=shop['kit'], quantity='1', date='2017-03-05'), reason='Kit')
    assert refused.value.code == 'E_VALIDATION' and refused.value.details['reason'] == 'build_left_short'


def test_fifo_corrections_say_fifo_in_the_journal(shop):
    run, books = shop['run'], shop['books']
    run('invoice post', dict(customer=shop['acme'], date='2017-03-03', lines=[dict(
        item='Widget', quantity='2', unit_price='50.00')]), reason='Job')
    done = run('costing-method set', dict(method='fifo', effective_from='2017-03-01'), reason='FIFO')
    assert done['entries'] == []                         # one opening layer at 6.00: nothing moves
    early = run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(
        item=shop['widget'], quantity='3', unit_cost='1.00')]), reason='Backdated, after the opening layer')
    run('invoice post', dict(customer=shop['acme'], date='2017-03-04', lines=[dict(
        item='Widget', quantity='4', unit_price='50.00')]), reason='Job')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-02', items=[dict(
        item=shop['widget'], quantity='1', unit_cost='2.00')]), reason='Another backdated bill')
    # Voiding the 1.00 bill: the 4 March sale's fourth widget now comes from the 2.00 layer, corrected at its date.
    bill = run('bill show', dict(bill=early['id']))
    run('bill void', dict(bill=early['id'], expected_version=bill['version']), reason='Entered in error')
    with sqlite3.connect(_db(books)) as conn:
        memos = {row[0] for row in conn.execute(
            "SELECT r.memo FROM transaction_revisions r JOIN transactions t ON t.current_revision_id = r.id "
            "WHERE r.memo LIKE '%cost correction%'")}
    assert memos and all(memo.startswith('FIFO cost correction') for memo in memos), memos
