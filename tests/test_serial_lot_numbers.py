"""R104 increment 4: serial and lot numbers.

A plumber's water heaters carry serial numbers and the sealant carries lots. WH-1 is received at the
warehouse, moved to Van 1, sold to Acme and returned, and its history reads in that order; selling it
twice is refused naming the first sale, and the database refuses a second WH-1 in stock even when the
application check is bypassed. Throughout, a number's placements plus the unassigned bucket sum to what
each place holds, and placements sum to movements on every date.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db, _held, _tie_every_date


def _item(books, name, tracking, cost='100.00', price='300.00'):
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part', description=name, price=price,
        purchase_description=name, cost=cost, cogs_account_id=books['cogs'], income_account_id=books['income'],
        tracking=tracking)['id']


def _numbers(books, as_of='2017-12-31'):
    """``{(item, site, number or None): quantity}`` straight from the placements."""
    with sqlite3.connect(_db(books)) as conn:
        rows = conn.execute(
            'SELECT i.full_name, s.name, coalesce(n.number, l.number), sum(p.quantity_microunits) '
            'FROM inventory_placements p JOIN items i ON i.id = p.item_id JOIN inventory_sites s ON s.id = p.site_id '
            'LEFT JOIN serial_numbers n ON n.id = p.serial_number_id LEFT JOIN lots l ON l.id = p.lot_id '
            'WHERE p.effective_date <= ? GROUP BY 1, 2, 3', (as_of,)).fetchall()
    return {(item, site, number): q // 1_000_000 for item, site, number, q in rows if q}


def _number_ties(books):
    """The quantity tie one level down: on every date, at every place, the numbers plus unassigned sum to the
    place's quantity, a serial number is never below -1 or above 1, and a number belongs to its item."""
    _tie_every_date(books)
    with sqlite3.connect(_db(books)) as conn:
        dates = [r[0] for r in conn.execute('SELECT DISTINCT effective_date FROM inventory_placements')]
        for date in dates:
            place = dict(((i, s), q) for i, s, q in conn.execute(
                'SELECT item_id, site_id, sum(quantity_microunits) FROM inventory_placements WHERE effective_date <= ? '
                'GROUP BY item_id, site_id', (date,)))
            parts = {}
            for i, s, q in conn.execute(
                    'SELECT item_id, site_id, sum(quantity_microunits) FROM inventory_placements WHERE effective_date <= ? '
                    'GROUP BY item_id, site_id, lot_id, serial_number_id', (date,)):
                parts[(i, s)] = parts.get((i, s), 0) + q
            assert parts == place, date
            for serial, q in conn.execute(
                    'SELECT serial_number_id, sum(quantity_microunits) FROM inventory_placements WHERE effective_date <= ? '
                    'AND serial_number_id IS NOT NULL GROUP BY serial_number_id', (date,)):
                assert -1_000_000 <= q <= 1_000_000, (date, serial, q)
        assert not conn.execute(
            'SELECT 1 FROM inventory_placements p LEFT JOIN serial_numbers n ON n.id = p.serial_number_id '
            'LEFT JOIN lots l ON l.id = p.lot_id WHERE (p.serial_number_id IS NOT NULL AND n.item_id != p.item_id) '
            'OR (p.lot_id IS NOT NULL AND l.item_id != p.item_id)').fetchone()


@pytest.fixture
def shop(books):
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='Our warehouse')
    run('inventory-site create', dict(name='Van 1'), reason='The van')
    heater = _item(books, 'Water Heater', 'serial')
    sealant = _item(books, 'Sealant', 'lot', cost='4.00', price='9.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    return dict(books=books, run=run, heater=heater, sealant=sealant, acme=acme)


def _receive(shop, date='2017-03-01', serials=('WH-1', 'WH-2'), quantity=None, **extra):
    return shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
        item=shop['heater'], quantity=str(quantity or len(serials)), unit_cost='100.00', serial_numbers=list(serials),
        **extra)]), reason='Heaters in')


def _sell(shop, date, serials=None, quantity='1', site=None, **extra):
    line = dict(item='Water Heater', quantity=quantity)
    if serials is not None:
        line['serial_numbers'] = list(serials)
    payload = dict(customer=shop['acme'], date=date, lines=[line], **extra)
    if site:
        payload['site'] = site
    return shop['run']('invoice post', payload, reason='Sold')


def test_receive_and_sell_a_serial_number_and_selling_it_twice_is_refused(shop):
    _receive(shop)
    assert _numbers(shop['books']) == {('Water Heater', 'Warehouse', 'WH-1'): 1, ('Water Heater', 'Warehouse', 'WH-2'): 1}
    sale = _sell(shop, '2017-03-05', ['WH-1'])
    assert ('Water Heater', 'Warehouse', 'WH-1') not in _numbers(shop['books'])
    with pytest.raises(BookflowError) as refused:
        _sell(shop, '2017-03-06', ['WH-1'])
    assert refused.value.code == 'E_VALIDATION' and f'invoice {sale["number"]}' in refused.value.message
    _number_ties(shop['books'])


def test_wh1_received_moved_sold_and_returned_shows_that_history_in_order(shop):
    run = shop['run']
    bill = _receive(shop)
    transfer = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Water Heater', quantity='1', serial_numbers=['WH-1'])]), reason='Load the van')
    assert transfer['lines'][0]['serial_numbers'] == ['WH-1']
    sale = _sell(shop, '2017-03-05', ['WH-1'], site='Van 1')
    assert _numbers(shop['books']).get(('Water Heater', 'Van 1', 'WH-1')) is None
    shown = run('invoice show', dict(invoice=sale['id']))
    credit = run('credit-memo post', dict(customer=shop['acme'], date='2017-03-09', lines=[dict(
        source_invoice=sale['id'], source_line=shown['revision']['lines'][0]['line_id'], quantity='1')]), reason='Return')
    # The return names no number: its sale took exactly WH-1, so WH-1 comes back, where it was sold from.
    assert _numbers(shop['books'])[('Water Heater', 'Van 1', 'WH-1')] == 1
    history = run('report serial-lot-history', dict(number='WH-1'))
    assert [(row['document'], row['site_name'], row['quantity_in'], row['quantity_out'], row['balance'])
            for row in history['rows']] == [
        (f'bill {bill["number"]}', 'Warehouse', '1', '0', '1'),
        (f'inventory transfer {transfer["number"]}', 'Warehouse', '0', '1', '0'),
        (f'inventory transfer {transfer["number"]}', 'Van 1', '1', '0', '1'),
        (f'invoice {sale["number"]}', 'Van 1', '0', '1', '0'),
        (f'credit memo {credit["number"]}', 'Van 1', '1', '0', '1')]
    assert history['rows'][3]['name'] == 'Acme'
    shown = run('serial-number show', dict(number='WH-1'))
    assert shown['on_hand'] == '1' and [p['site_name'] for p in shown['places']] == ['Van 1']
    _number_ties(shop['books'])


def test_the_database_refuses_a_second_wh1_in_stock_even_when_the_application_is_bypassed(shop, monkeypatch):
    from bookflow.company import serial_lots
    _receive(shop)
    with pytest.raises(BookflowError):
        _receive(shop, date='2017-03-02', serials=('WH-1',))
    with monkeypatch.context() as patched, pytest.raises(Exception) as refused:
        patched.setattr(serial_lots, 'check', lambda state, rows, field='lines': state.warnings)
        _receive(shop, date='2017-03-02', serials=('WH-1',))
    chain, cause = [], refused.value
    while cause is not None:
        chain.append(str(cause))
        cause = cause.__cause__ or cause.__context__
    assert any('in stock twice' in text for text in chain), chain
    assert _numbers(shop['books'])[('Water Heater', 'Warehouse', 'WH-1')] == 1
    with sqlite3.connect(_db(shop['books'])) as conn:
        placement = conn.execute('SELECT * FROM inventory_placements WHERE serial_number_id IS NOT NULL').fetchone()
        names = [c[1] for c in conn.execute('PRAGMA table_info(inventory_placements)')]
        row = dict(zip(names, placement))
        # A fractional serial unit, and a number of another item, are refused by the rows themselves.
        for change, match in ((dict(quantity_microunits=-500000), 'one whole unit'),
                              (dict(item_id=shop['sealant'], quantity_microunits=-1000000), 'another item')):
            bad = dict(row, id='01ZZZZZZZZZZZZZZZZZZZZZZZZ', source='count', movement_id=None, source_line_id='X',
                       sequence=999999, **change)
            with pytest.raises(sqlite3.IntegrityError, match=match):
                conn.execute(f"INSERT INTO inventory_placements ({', '.join(bad)}) VALUES ({', '.join('?' * len(bad))})",
                             list(bad.values()))
        # A count placing WH-1 in stock again, bypassing every check, is refused too.
        twice = dict(row, id='01ZZZZZZZZZZZZZZZZZZZZZZZY', source='count', movement_id=None, source_line_id='X',
                     sequence=999999, effective_date='2017-04-01')
        with pytest.raises(sqlite3.IntegrityError, match='in stock twice'):
            conn.execute(f"INSERT INTO inventory_placements ({', '.join(twice)}) VALUES ({', '.join('?' * len(twice))})",
                         list(twice.values()))
        conn.rollback()


def test_a_heater_sold_with_no_number_leaves_unassigned_and_assign_numbers_what_that_sale_took(shop):
    run = shop['run']
    _receive(shop, serials=('WH-1',), quantity=2)            # one unit arrives without a number
    sale = _sell(shop, '2017-03-05')                          # the agent did not know which
    assert ('Water Heater', 'Warehouse', None) not in _numbers(shop['books'])
    preview = run('inventory assign', dict(item='Water Heater', document=f'invoice {sale["number"]}',
                                           serial_numbers=['WH-7']), reason='From the job sheet', dry_run=True)
    assert preview['posted'] is False and preview['date'] == '2017-03-05'
    done = run('inventory assign', dict(item='Water Heater', document=f'invoice {sale["number"]}',
                                        serial_numbers=['WH-7']), reason='From the job sheet')
    assert done['status'] == 'assigned'
    history = run('report serial-lot-history', dict(number='WH-7'))
    assert [row['balance'] for row in history['rows']] == ['-1']      # WH-7 left on that sale; no receipt named it
    _number_ties(shop['books'])
    # The sale is voided: its numbering goes with it.
    shown = run('invoice show', dict(invoice=sale['id']))
    run('invoice void', dict(invoice=sale['id'], expected_version=shown['version']), reason='Wrong customer')
    assert run('serial-number show', dict(number='WH-7'))['on_hand'] == '0'
    _number_ties(shop['books'])


def test_turning_tracking_on_leaves_stock_unassigned(shop):
    run, books = shop['run'], shop['books']
    plain = books['client'].item.create(
        company=books['company'], name='Boiler', type='inventory_part', description='Boiler', price='900.00',
        purchase_description='Boiler', cost='400.00', cogs_account_id=books['cogs'], income_account_id=books['income'])['id']
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(item=plain, quantity='3', unit_cost='400.00')]),
        reason='In')
    with pytest.raises(BookflowError):
        run('invoice post', dict(customer=shop['acme'], date='2017-03-02', lines=[dict(item='Boiler', quantity='1',
                                                                                     serial_numbers=['B-1'])]), reason='x')
    run('item update', dict(item='Boiler', tracking='serial'), reason='Warranties')
    stock = run('report serial-lot-stock', dict(item='Boiler', as_of='2017-12-31'))
    assert [(r['number'], r['site_name'], r['quantity']) for r in stock['rows']] == [(None, 'Warehouse', '3')]
    run('inventory assign', dict(item='Boiler', serial_numbers=['B-1', 'B-2'], date='2017-03-03'), reason='Read the labels')
    stock = run('report serial-lot-stock', dict(item='Boiler', as_of='2017-12-31'))
    assert sorted((r['number'] or '', r['quantity']) for r in stock['rows']) == [('', '1'), ('B-1', '1'), ('B-2', '1')]
    with pytest.raises(BookflowError):
        run('item update', dict(item='Boiler', tracking='none'), reason='Off')
    _number_ties(books)


def test_a_serial_number_sold_before_its_receipt_shows_minus_one_until_the_receipt(shop):
    run = shop['run']
    sale = _sell(shop, '2017-03-01', ['WH-9'])
    assert any('not on file' in w for w in sale['warnings'])
    assert run('serial-number show', dict(number='WH-9', as_of='2017-03-01'))['on_hand'] == '-1'
    _receive(shop, date='2017-03-04', serials=('WH-9',))
    assert run('serial-number show', dict(number='WH-9'))['on_hand'] == '0'
    with pytest.raises(BookflowError) as refused:
        _sell(shop, '2017-03-06', ['WH-9'])
    assert f'invoice {sale["number"]}' in refused.value.message
    _number_ties(shop['books'])


def test_whole_units_a_foreign_return_and_a_correction_moving_serials_back(shop):
    run = shop['run']
    _receive(shop, serials=('WH-1', 'WH-2', 'WH-3'))
    with pytest.raises(BookflowError):
        _sell(shop, '2017-03-05', quantity='0.5')
    with pytest.raises(BookflowError):
        _sell(shop, '2017-03-05', ['WH-1', 'WH-2'], quantity='1')
    sale = _sell(shop, '2017-03-05', ['WH-1'])
    shown = run('invoice show', dict(invoice=sale['id']))
    line = shown['revision']['lines'][0]['line_id']
    with pytest.raises(BookflowError) as refused:
        run('credit-memo post', dict(customer=shop['acme'], date='2017-03-09', lines=[dict(
            source_invoice=sale['id'], source_line=line, quantity='1', serial_numbers=['WH-2'])]), reason='Return')
    assert 'WH-2' in refused.value.message
    # A correction that keeps the line but names WH-2 sends WH-1 back to the shelf.
    corrected = run('invoice update', dict(invoice=sale['id'], expected_version=shown['version'], lines=[dict(
        line_id=line, item='Water Heater', quantity='1', serial_numbers=['WH-2'])]), reason='Wrong unit')
    held = _numbers(shop['books'])
    assert held.get(('Water Heater', 'Warehouse', 'WH-1')) == 1 and ('Water Heater', 'Warehouse', 'WH-2') not in held
    # A correction that restates nothing about numbers keeps WH-2.
    shown = run('invoice show', dict(invoice=sale['id']))
    run('invoice update', dict(invoice=sale['id'], expected_version=shown['version'], memo='Kept'), reason='Memo')
    assert ('Water Heater', 'Warehouse', 'WH-2') not in _numbers(shop['books'])
    _number_ties(shop['books'])


def _receive_sealant(shop):
    run = shop['run']
    run('bill post', dict(vendor=shop['books']['vendor'], date='2017-03-01', items=[
        dict(item=shop['sealant'], quantity='10', unit_cost='4.00', lot='L-200', lot_expiry='2018-06-30'),
        dict(item=shop['sealant'], quantity='5', unit_cost='4.00', lot='L-100', lot_expiry='2017-09-30')]), reason='In')


def test_a_sealant_sale_naming_no_lot_suggests_the_earliest_expiring_lot_and_the_trace_lists_every_customer(shop):
    run, books = shop['run'], shop['books']
    _receive_sealant(shop)
    preview = run('invoice post', dict(customer=shop['acme'], date='2017-03-05', lines=[dict(item='Sealant', quantity='2')]),
                  reason='Sold', dry_run=True)
    assert any('L-100' in w and 'earliest-expiring' in w for w in preview['warnings'])
    transfer = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Sealant', quantity='3', lot='L-100')]), reason='Load')
    other = books['client'].customer.create(company=books['company'], name='Brook Homes')['id']
    for customer in (shop['acme'], other):
        run('invoice post', dict(customer=customer, date='2017-03-06', site='Van 1',
                                 lines=[dict(item='Sealant', quantity='1', lot='L-100')]), reason='Sold')
    trace = run('report lot-trace', dict(number='L-100'))
    documents = [row['document'] for row in trace['rows']]
    assert documents[0].startswith('bill ') and f'inventory transfer {transfer["number"]}' in documents
    assert trace['customers'] == ['Acme', 'Brook Homes'] and trace['vendors'] == ['Northside Supply']
    stock = run('report serial-lot-stock', dict(item='Sealant', as_of='2017-12-31'))
    assert [(r['number'], r['site_name'], r['quantity'], r['expiry_date']) for r in stock['rows']] == [
        ('L-100', 'Van 1', '1', '2017-09-30'), ('L-100', 'Warehouse', '2', '2017-09-30'),
        ('L-200', 'Warehouse', '10', '2018-06-30')]
    expired = run('invoice post', dict(customer=other, date='2017-10-02', lines=[dict(item='Sealant', quantity='1', lot='L-100')]),
                  reason='Sold')
    assert any('expired' in w for w in expired['warnings'])
    assert [l['number'] for l in run('lot list', dict(expiring_by='2017-12-31'))['numbers']] == ['L-100']
    _number_ties(books)


def test_counts_count_by_serial_number_and_lot(shop):
    run, books = shop['run'], shop['books']
    _receive(shop, serials=('WH-1', 'WH-2'), quantity=3)
    _receive_sealant(shop)
    shrinkage = books['client'].account.create(company=books['company'], name='Inventory Shrinkage', type='expense')['id']
    count = run('inventory-count create', dict(site='Warehouse', date='2017-03-10', items=['Water Heater', 'Sealant']),
                reason='Count')
    places = sorted((line['item_name'], line['place'], line['expected']) for line in count['lines'])
    assert places == [('Sealant', 'lot L-100', '5'), ('Sealant', 'lot L-200', '10'),
                      ('Water Heater', 'serial WH-1', '1'), ('Water Heater', 'serial WH-2', '1'),
                      ('Water Heater', 'unassigned', '1')]
    run('inventory-count enter', dict(count=count['number'], entries=[
        dict(item='Water Heater', serial_number='WH-1', quantity='1'), dict(item='Water Heater', serial_number='WH-2', quantity='0'),
        dict(item='Water Heater', serial_number='WH-5', quantity='1'),    # the unassigned unit's label, read now
        dict(item='Water Heater', quantity='0'),
        dict(item='Sealant', lot='L-100', quantity='4'), dict(item='Sealant', lot='L-200', quantity='10')]), reason='Counted')
    with pytest.raises(BookflowError):
        run('inventory-count enter', dict(count=count['number'], entries=[
            dict(item='Water Heater', serial_number='WH-1', quantity='2')]), reason='Typo')
    preview = run('inventory-count post', dict(count=count['number'], expected_version=count['version'],
                                               adjustment_account=shrinkage), reason='Count', dry_run=True)
    run('inventory-count post', dict(count=count['number'], expected_version=count['version'], adjustment_account=shrinkage,
                                     preview_fingerprint=preview['preview_fingerprint']), reason='Count')
    held = _numbers(books)
    assert held[('Water Heater', 'Warehouse', 'WH-1')] == 1 and held[('Water Heater', 'Warehouse', 'WH-5')] == 1
    assert ('Water Heater', 'Warehouse', 'WH-2') not in held and ('Water Heater', 'Warehouse', None) not in held
    assert held[('Sealant', 'Warehouse', 'L-100')] == 4
    _number_ties(books)


# Five seeds of 30 steps in an ordinary run; BF_SERIAL_SEEDS and BF_SERIAL_STEPS widen it (the review ran 60 x 40).
import os as _os
SEEDS = tuple(range(21, 21 + int(_os.environ.get('BF_SERIAL_SEEDS', '5'))))
STEPS = int(_os.environ.get('BF_SERIAL_STEPS', '30'))
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04')


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_numbered_paperwork_keeps_every_number_tie(shop, seed):
    """The increment-2 tie checker, ported: late paperwork in a fixed random order -- now with serial and lot
    lines, transfers of numbers, assignments, returns and counts by number -- and after every step the
    movement tie, the number tie at every place on every date, and the asset tie hold."""
    import random
    from tests.test_inventory_count_review import _asset
    run, books = shop['run'], shop['books']
    rnd = random.Random(seed)
    shrinkage = books['client'].account.create(company=books['company'], name='Inventory Shrinkage', type='expense')['id']
    sites, lots = ['Warehouse', 'Van 1'], ['L-1', 'L-2', 'L-3']
    serials = [f'WH-{n}' for n in range(1, 9)]
    sales, counts = [], []
    for step in range(STEPS):
        op = rnd.choice(['receive', 'receive', 'sell', 'sell', 'transfer', 'assign', 'return', 'count', 'void',
                         'lot_in', 'lot_out'])
        date = rnd.choice(DATES)
        site = rnd.choice(sites)
        try:
            if op == 'receive':
                named = rnd.sample(serials, rnd.randint(0, 2))
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=shop['heater'], quantity=str(len(named) + rnd.randint(0, 1) or 1), unit_cost='100.00',
                    serial_numbers=named, site=site)]), reason='In')
            elif op == 'sell':
                named = rnd.sample(serials, rnd.randint(0, 1))
                payload = dict(customer=shop['acme'], date=date, site=site,
                               lines=[dict(item='Water Heater', quantity='1', serial_numbers=named)])
                if rnd.random() < .3:
                    payload['count_position'] = 'after'
                sales.append(run('invoice post', payload, reason='Job')['id'])
            elif op == 'transfer':
                a, b = rnd.sample(sites, 2)
                run('inventory-transfer post', dict(date=date, from_site=a, to_site=b, lines=[
                    dict(item='Water Heater', quantity='1', serial_numbers=rnd.sample(serials, rnd.randint(0, 1)))]),
                    reason='Move')
            elif op == 'assign':
                run('inventory assign', dict(item='Water Heater', serial_numbers=[rnd.choice(serials)], site=site,
                                             date=date), reason='Labels')
            elif op == 'return' and sales:
                sale = run('invoice show', dict(invoice=rnd.choice(sales)))
                if sale['status'] == 'posted':
                    run('credit-memo post', dict(customer=shop['acme'], date=max(date, sale['revision']['date']), lines=[dict(
                        source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'], quantity='1')]),
                        reason='Return')
            elif op == 'count':
                made = run('inventory-count create', dict(site=site, date=date, items=['Water Heater', 'Sealant']),
                           reason='Count')
                entries = [dict(item=line['item_name'], quantity=str(rnd.randint(0, 1) if 'serial' in line['place']
                                                                       else rnd.randint(0, 6)),
                                **({'serial_number': line['place'][7:]} if line['place'].startswith('serial ') else {}),
                                **({'lot': line['place'][4:]} if line['place'].startswith('lot ') else {}))
                           for line in made['lines']]
                run('inventory-count enter', dict(count=made['number'], entries=entries), reason='Counted')
                preview = run('inventory-count post', dict(count=made['number'], expected_version=made['version'],
                                                           adjustment_account=shrinkage), reason='Count', dry_run=True)
                run('inventory-count post', dict(count=made['number'], expected_version=made['version'],
                                                 adjustment_account=shrinkage,
                                                 preview_fingerprint=preview['preview_fingerprint']), reason='Count')
                counts.append(made['number'])
            elif op == 'void' and counts:
                number = counts.pop(rnd.randrange(len(counts)))
                run('inventory-count void', dict(count=number, expected_version=run(
                    'inventory-count show', dict(count=number))['version']), reason='Recount')
            elif op == 'lot_in':
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=shop['sealant'], quantity=str(rnd.randint(1, 5)), unit_cost='4.00', lot=rnd.choice(lots), site=site)]),
                    reason='Sealant in')
            elif op == 'lot_out':
                line = dict(item='Sealant', quantity=str(rnd.randint(1, 3)))
                if rnd.random() < .7:
                    line['lot'] = rnd.choice(lots)
                sales.append(run('invoice post', dict(customer=shop['acme'], date=date, site=site, lines=[line]),
                                 reason='Sealant')['id'])
        except BookflowError as error:
            # Refusals a person can act on are fine (a number already in stock or gone, over-returning, a place
            # counted twice); nothing internal may surface.
            assert error.code not in ('E_INTERNAL', 'E_IO'), (seed, step, op, error)
        _number_ties(books)
        valuation = run('report inventory-valuation', dict(as_of='2017-12-31'))
        assert valuation['totals']['asset_value']['minor_units'] == _net(books).get(_asset(books), 0), (seed, step, op)


def test_co0095_on_a_populated_company_changes_no_existing_row_and_matches_the_metadata(shop):
    """co0095 upgrades a company with stock: every existing row of every table reads the same (items gain only
    tracking = none), the new objects exist, and the frozen DDL is exactly the current metadata."""
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    from bookflow.company import schema as c
    from tests.test_negative_stock_migration import _at
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0095_serial_lot_numbers')
    table = c.metadata.tables['inventory_assignments']
    assert m.DDL == str(CreateTable(table).compile(dialect=dialect()))
    assert m.INDEXES[:2] == tuple(str(CreateIndex(i).compile(dialect=dialect()))
                                  for i in sorted(table.indexes, key=lambda i: i.name))
    run = shop['run']
    plain = shop['books']['client'].item.create(
        company=shop['books']['company'], name='Boiler', type='inventory_part', description='Boiler', price='9.00',
        purchase_description='Boiler', cost='4.00', cogs_account_id=shop['books']['cogs'],
        income_account_id=shop['books']['income'])['id']
    run('bill post', dict(vendor=shop['books']['vendor'], date='2017-03-01', items=[dict(item=plain, quantity='5', unit_cost='4.00')]),
        reason='In')
    run('invoice post', dict(customer=shop['acme'], date='2017-03-02', lines=[dict(item='Boiler', quantity='2')]), reason='Out')
    path = _db(shop['books'])
    copy = path.parent / 'rewound95.db'
    with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        for trigger in m.TRIGGERS:
            conn.execute(f'DROP TRIGGER {trigger}')
        for index in m.INDEX_NAMES:
            conn.execute(f'DROP INDEX {index}')
        conn.execute('DROP TABLE inventory_assignments')
        conn.execute('UPDATE items SET tracking = \'none\'')
        conn.execute('ALTER TABLE items DROP COLUMN tracking')
        conn.execute("UPDATE alembic_version SET version_num='co0096'")
        conn.commit()
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name != 'alembic_version'")]
        before = {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}
    _at(copy, 'co0095')
    with sqlite3.connect(copy) as conn:
        for name in names:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            if name == 'items':
                columns = [r[1] for r in conn.execute('PRAGMA table_info(items)')]
                at = columns.index('tracking')
                assert {row[at] for row in rows} == {'none'}
                rows = [row[:at] + row[at + 1:] for row in rows]
            assert sorted(rows, key=repr) == before[name], name
        assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name IN ('inventory_assignments', "
                            "'inventory_movements_serial_range', 'ix_inventory_placements_serial')").fetchone() == (3,)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


# ---------------------------------------------------------------- review findings (R104 increment 4 review)

def _ok(fn):
    """Valid paperwork: never refused by a trigger (E_IO) or internally."""
    try:
        return fn()
    except BookflowError as error:
        assert error.code not in ('E_IO', 'E_INTERNAL'), error
        raise


def test_a_reversed_pair_is_not_two_steps_for_the_database(shop):
    """A reversal keeps its original's date and position under a later sequence; a placement sorting before
    the pair once read +-2 and was refused with E_IO. Bill date moved earlier; void and re-enter dated earlier;
    an `after` sale voided and the number sold again that day; a return voided and the sale corrected."""
    run = shop['run']
    bill = _receive(shop, date='2017-03-03', serials=('WH-1',))
    shown = run('bill show', dict(bill=bill['id']))
    _ok(lambda: run('bill update', dict(bill=bill['id'], expected_version=shown['version'], date='2017-03-02'), reason='Date'))
    shown = run('bill show', dict(bill=bill['id']))
    _ok(lambda: run('bill void', dict(bill=bill['id'], expected_version=shown['version']), reason='Wrong'))
    _ok(lambda: _receive(shop, date='2017-03-01', serials=('WH-1',)))
    sale = _ok(lambda: _sell(shop, '2017-03-05', ['WH-4'], count_position='after'))
    _ok(lambda: run('invoice void', dict(invoice=sale['id'], expected_version=run('invoice show', dict(
        invoice=sale['id']))['version']), reason='Void'))
    _ok(lambda: _sell(shop, '2017-03-05', ['WH-4'], site='Van 1'))
    sold = _ok(lambda: _sell(shop, '2017-03-06', ['WH-1']))
    shown = run('invoice show', dict(invoice=sold['id']))
    credit = _ok(lambda: run('credit-memo post', dict(customer=shop['acme'], date='2017-03-07', lines=[dict(
        source_invoice=sold['id'], source_line=shown['revision']['lines'][0]['line_id'], quantity='1')]), reason='Return'))
    _ok(lambda: run('credit-memo void', dict(credit_memo=credit['id'], expected_version=run('credit-memo show', dict(
        credit_memo=credit['id']))['version']), reason='Void'))
    shown = run('invoice show', dict(invoice=sold['id']))
    _ok(lambda: run('invoice update', dict(invoice=sold['id'], expected_version=shown['version'], lines=[dict(
        line_id=shown['revision']['lines'][0]['line_id'], item='Water Heater', quantity='1', serial_numbers=[])]),
        reason='Unknown unit'))
    _number_ties(shop['books'])


def test_a_count_derived_again_retires_its_loose_placements_before_it_posts_found_units(shop):
    """Two heaters, one moved to the van; a van count finds WH-4 and counts the unassigned unit at 0 (the
    places cancel: no movement). Voiding the transfer derives the count again, which now posts WH-4 found --
    after retiring the loose WH-4 it replaces, never beside it."""
    run, books = shop['run'], shop['books']
    shrinkage = books['client'].account.create(company=books['company'], name='Inventory Shrinkage', type='expense')['id']
    _receive(shop, serials=(), quantity=2)
    moved = run('inventory-transfer post', dict(date='2017-03-03', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Water Heater', quantity='1')]), reason='Load')
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-04', items=['Water Heater']), reason='Count')
    run('inventory-count enter', dict(count=count['number'], entries=[
        dict(item='Water Heater', serial_number='WH-4', quantity='1'), dict(item='Water Heater', quantity='0')]), reason='Counted')
    preview = run('inventory-count post', dict(count=count['number'], expected_version=count['version'],
                                               adjustment_account=shrinkage), reason='Count', dry_run=True)
    run('inventory-count post', dict(count=count['number'], expected_version=count['version'], adjustment_account=shrinkage,
                                     preview_fingerprint=preview['preview_fingerprint']), reason='Count')
    shown = run('inventory-transfer show', dict(transfer=moved['number']))
    _ok(lambda: run('inventory-transfer void', dict(transfer=moved['number'], expected_version=shown['version']), reason='Never moved'))
    assert _numbers(books)[('Water Heater', 'Van 1', 'WH-4')] == 1
    _number_ties(books)


def test_impossible_dates_are_refused(shop):
    for bad in ('2017-02-30', '2017-13-45', '9999-99-99'):
        with pytest.raises(BookflowError) as refused:
            shop['run']('bill post', dict(vendor=shop['books']['vendor'], date='2017-03-01', items=[dict(
                item=shop['sealant'], quantity='1', unit_cost='4.00', lot='L-9', lot_expiry=bad)]), reason='In')
        assert refused.value.code == 'E_VALIDATION'
        with pytest.raises(BookflowError):
            shop['run']('serial-number show', dict(number='WH-1', as_of=bad))


def test_late_paperwork_that_splits_a_serial_over_two_sites_says_so_and_a_transfer_repairs_it(shop):
    run = shop['run']
    _receive(shop, serials=('WH-1',))
    run('inventory-transfer post', dict(date='2017-03-03', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Water Heater', quantity='1', serial_numbers=['WH-1'])]), reason='Load')
    late = _sell(shop, '2017-03-02', ['WH-1'])        # backdated before the transfer that moved it
    assert any('two sites' in w and 'WH-1' in w for w in late['warnings']), late['warnings']
    _number_ties(shop['books'])


def test_a_transfer_of_an_expired_lot_warns(shop):
    run = shop['run']
    _receive_sealant(shop)
    moved = run('inventory-transfer post', dict(date='2017-10-02', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Sealant', quantity='1', lot='L-100')]), reason='Load')
    assert any('expired on 2017-09-30' in w for w in moved['warnings']), moved['warnings']


def test_the_database_refuses_a_loose_reversal_that_is_not_its_own_counts(shop):
    """With the application bypassed, a count-source row reversing a sale's placement would put a sold WH-1 back
    beside the one received again -- two in stock and the quantity tie broken. The database refuses it: a
    reversal with no movement or transfer revision retires only a loose placement of its own count."""
    _receive(shop, serials=('WH-1',))
    _sell(shop, '2017-03-02', ['WH-1'])
    _receive(shop, date='2017-03-03', serials=('WH-1',))
    with sqlite3.connect(_db(shop['books'])) as conn:
        conn.row_factory = sqlite3.Row
        sale = dict(conn.execute(
            "SELECT p.* FROM inventory_placements p JOIN inventory_movements m ON m.id = p.movement_id "
            "WHERE p.serial_number_id IS NOT NULL AND p.quantity_microunits < 0 AND m.kind = 'issue'").fetchone())
        row = dict(sale, id='01ZZZZZZZZZZZZZZZZZZZZZZZA', source='count', movement_id=None, source_line_id='X',
                   sequence=990001, quantity_microunits=-sale['quantity_microunits'], reverses_placement_id=sale['id'])
        with pytest.raises(sqlite3.IntegrityError, match='its own count|in stock twice'):
            conn.execute(f"INSERT INTO inventory_placements ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
        conn.rollback()
    assert _numbers(shop['books'])[('Water Heater', 'Warehouse', 'WH-1')] == 1
    _number_ties(shop['books'])


def test_an_empty_number_on_a_line_that_moves_no_stock_is_no_number(shop):
    """A form's empty Serial numbers or Lot column, or an agent's `serial_numbers: []`, on a service line is no
    number at all; only a real one is refused."""
    books, run = shop['books'], shop['run']
    labor = books['client'].item.create(company=books['company'], name='Labor', type='service', description='Labor',
                                        price='80.00', income_account_id=books['income'],
                                        sales_tax_code_id=next(iter(books['tax_codes'].values())))['id']
    sale = run('invoice post', dict(customer=shop['acme'], date='2017-03-05', lines=[
        dict(item=labor, quantity='1', serial_numbers=[], lot=''), dict(item=labor, quantity='2', serial_numbers=[' '])]),
        reason='Job')
    shown = run('invoice show', dict(invoice=sale['id']))
    run('invoice update', dict(invoice=sale['id'], expected_version=shown['version'], lines=[
        dict(line_id=line['line_id'], item=labor, quantity=line['quantity'], serial_numbers=[], lot='')
        for line in shown['revision']['lines']], memo='Corrected'), reason='Correct')
    with pytest.raises(BookflowError) as refused:
        run('invoice post', dict(customer=shop['acme'], date='2017-03-05', lines=[
            dict(item=labor, quantity='1', serial_numbers=['WH-1'])]), reason='Job')
    assert 'only stocked lines' in str(refused.value.details)
    run('bill post', dict(vendor=books['vendor'], date='2017-03-05', items=[dict(
        item=books['labor'], quantity='1', serial_numbers=[], lot='')]), reason='Sub')


def test_a_voided_return_gives_its_serial_number_back_to_be_returned_again(shop):
    """Credit memo post, void, post: the void releases WH-1, so the second return may name it again."""
    run = shop['run']
    _receive(shop)
    sale = _sell(shop, '2017-03-05', ['WH-1'])
    line = run('invoice show', dict(invoice=sale['id']))['revision']['lines'][0]['line_id']
    payload = dict(customer=shop['acme'], date='2017-03-09', lines=[dict(
        source_invoice=sale['id'], source_line=line, quantity='1', serial_numbers=['WH-1'])])
    credit = run('credit-memo post', payload, reason='Return')
    run('credit-memo void', dict(credit_memo=credit['id'], expected_version=credit['version']), reason='Wrong day')
    assert ('Water Heater', 'Warehouse', 'WH-1') not in _numbers(shop['books'])
    again = run('credit-memo post', dict(payload, date='2017-03-10'), reason='Return again')
    assert again['status'] == 'posted'
    assert _numbers(shop['books'])[('Water Heater', 'Warehouse', 'WH-1')] == 1
    _number_ties(shop['books'])
