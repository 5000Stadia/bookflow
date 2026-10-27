"""R104 increment 5: bins and cycle counts.

The warehouse tracks bins A1, A2, A3 (and A3:Top inside A3). Stock received into a bin, sold from it, moved
between bins of one site, counted bin by bin, and found by a scanned bin label; the bins of a site always add up
to the site, the database refuses a bin of another site even when the application is bypassed, and cycle classes
put the items making 80% of issued cost in A and keep a weekly rotation inside every class's interval.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db, _tie_every_date


def _part(books, name, cost='2.00', price='5.00', tracking=None):
    extra = {'tracking': tracking} if tracking else {}
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part', description=name, price=price,
        purchase_description=name, cost=cost, cogs_account_id=books['cogs'], income_account_id=books['income'],
        **extra)['id']


def _bins(books, as_of='2017-12-31'):
    """``{(item, site, bin or None, number or None): quantity}`` straight from the placements."""
    with sqlite3.connect(_db(books)) as conn:
        rows = conn.execute(
            'SELECT i.full_name, s.name, b.full_name, coalesce(n.number, l.number), sum(p.quantity_microunits) '
            'FROM inventory_placements p JOIN items i ON i.id = p.item_id JOIN inventory_sites s ON s.id = p.site_id '
            'LEFT JOIN inventory_bins b ON b.id = p.bin_id LEFT JOIN serial_numbers n ON n.id = p.serial_number_id '
            'LEFT JOIN lots l ON l.id = p.lot_id WHERE p.effective_date <= ? GROUP BY 1, 2, 3, 4', (as_of,)).fetchall()
    return {(item, site, bin_, number): q / 1_000_000 for item, site, bin_, number, q in rows if q}


def _bin_ties(books):
    """On every date: the quantity tie per item; at every place the bins (with no bin) sum to the site, and the
    numbers plus unassigned sum to each bin; a serial number stays within -1..1; every placement's bin is a bin
    of its own site; and a number belongs to its item."""
    _tie_every_date(books)
    with sqlite3.connect(_db(books)) as conn:
        dates = [r[0] for r in conn.execute('SELECT DISTINCT effective_date FROM inventory_placements')]
        for date in dates:
            site = dict(((i, s), q) for i, s, q in conn.execute(
                'SELECT item_id, site_id, sum(quantity_microunits) FROM inventory_placements WHERE effective_date <= ? '
                'GROUP BY item_id, site_id', (date,)))
            by_bin = {}
            for i, s, b, q in conn.execute(
                    'SELECT item_id, site_id, bin_id, sum(quantity_microunits) FROM inventory_placements '
                    'WHERE effective_date <= ? GROUP BY item_id, site_id, bin_id', (date,)):
                by_bin[(i, s, b)] = q
            summed = {}
            for (i, s, _), q in by_bin.items():
                summed[(i, s)] = summed.get((i, s), 0) + q
            assert summed == site, date
            parts = {}
            for i, s, b, q in conn.execute(
                    'SELECT item_id, site_id, bin_id, sum(quantity_microunits) FROM inventory_placements '
                    'WHERE effective_date <= ? GROUP BY item_id, site_id, bin_id, lot_id, serial_number_id', (date,)):
                parts[(i, s, b)] = parts.get((i, s, b), 0) + q
            assert parts == by_bin, date
            for serial, q in conn.execute(
                    'SELECT serial_number_id, sum(quantity_microunits) FROM inventory_placements WHERE effective_date <= ? '
                    'AND serial_number_id IS NOT NULL GROUP BY serial_number_id', (date,)):
                assert -1_000_000 <= q <= 1_000_000, (date, serial, q)
        assert not conn.execute(
            'SELECT 1 FROM inventory_placements p LEFT JOIN inventory_bins b ON b.id = p.bin_id '
            'WHERE p.bin_id IS NOT NULL AND (b.id IS NULL OR b.site_id != p.site_id)').fetchone()
        assert not conn.execute(
            'SELECT 1 FROM inventory_placements p LEFT JOIN serial_numbers n ON n.id = p.serial_number_id '
            'LEFT JOIN lots l ON l.id = p.lot_id WHERE (p.serial_number_id IS NOT NULL AND n.item_id != p.item_id) '
            'OR (p.lot_id IS NOT NULL AND l.item_id != p.item_id)').fetchone()


@pytest.fixture
def shop(books):
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse', tracks_bins=True), reason='Our warehouse')
    run('inventory-site create', dict(name='Van 1'), reason='The van')
    for name in ('A1', 'A2', 'A3'):
        run('bin create', dict(site='Warehouse', name=name, barcode=f'BIN-{name}'), reason='Shelving')
    run('bin create', dict(site='Warehouse', name='Top', parent='A3'), reason='Top shelf of A3')
    widget = _part(books, 'Widget')
    elbow = _part(books, 'Elbow', cost='1.00', price='3.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    return dict(books=books, run=run, widget=widget, elbow=elbow, acme=acme)


def _receive(shop, item, quantity, date='2017-03-01', **line):
    return shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
        item=item, quantity=str(quantity), unit_cost='2.00', **line)]), reason='In')


def _sell(shop, item_name, quantity, date='2017-03-05', **line):
    return shop['run']('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
        item=item_name, quantity=str(quantity), **line)]), reason='Sold')


# ---------------------------------------------------------------- the list

def test_bins_go_three_levels_deep_and_are_unique_per_site(shop):
    run = shop['run']
    top = run('bin show', dict(bin='A3:Top'))
    assert (top['full_name'], top['depth'], top['site_name']) == ('A3:Top', 2, 'Warehouse')
    run('bin create', dict(name='Left', parent='A3:Top'), reason='Third level')
    with pytest.raises(BookflowError) as deep:
        run('bin create', dict(name='Inner', parent='A3:Top:Left'), reason='Too deep')
    assert deep.value.code == 'E_VALIDATION' and '3 levels' in str(deep.value.details)
    with pytest.raises(BookflowError) as twice:
        run('bin create', dict(site='Warehouse', name='a1'), reason='Again')
    assert twice.value.code == 'E_NAME_TAKEN'
    with pytest.raises(BookflowError) as van:
        run('bin create', dict(site='Van 1', name='A1'), reason='Van shelf')
    assert van.value.code == 'E_VALIDATION' and 'does not track bins' in van.value.message
    run('inventory-site update', dict(site='Van 1', tracks_bins=True), reason='Van shelves')
    assert run('bin create', dict(site='Van 1', name='A1'), reason='Same name, other site')['full_name'] == 'A1'
    with pytest.raises(BookflowError) as ambiguous:
        run('bin show', dict(bin='A1'))
    assert 'name the site too' in str(ambiguous.value.details)
    assert run('bin show', dict(bin='A1', site='Van 1'))['site_name'] == 'Van 1'
    renamed = run('bin update', dict(bin='A3', site='Warehouse', name='B3'), reason='Relabelled')
    assert renamed['renamed'] == ['B3:Top', 'B3:Top:Left']
    assert run('bin show', dict(bin='B3:Top:Left'))['depth'] == 3


def test_stock_goes_into_bins_and_the_bins_add_up_to_the_site(shop):
    run = shop['run']
    _receive(shop, shop['widget'], 10, bin='A1')
    _receive(shop, shop['widget'], 4, bin='A3:Top')
    _receive(shop, shop['widget'], 2)                       # no bin, no default: no bin
    run('item update', dict(item='Widget', site_settings=[dict(site='Warehouse', default_bin='A2')]), reason='Home bin')
    _receive(shop, shop['widget'], 5)                       # the default bin
    held = _bins(shop['books'])
    assert held == {('Widget', 'Warehouse', 'A1', None): 10, ('Widget', 'Warehouse', 'A3:Top', None): 4,
                    ('Widget', 'Warehouse', None, None): 2, ('Widget', 'Warehouse', 'A2', None): 5}
    _sell(shop, 'Widget', 3, bin='A1')
    sale = _sell(shop, 'Widget', 1)                          # default bin A2
    assert _bins(shop['books'])[('Widget', 'Warehouse', 'A1', None)] == 7
    assert _bins(shop['books'])[('Widget', 'Warehouse', 'A2', None)] == 4
    report = run('report items-by-bin', dict(site='Warehouse', as_of='2017-12-31'))
    rows = {(r['bin'], r['item_name']): r['quantity_on_hand'] for r in report['rows']}
    assert rows == {('A1', 'Widget'): '7', ('A2', 'Widget'): '4', ('A3:Top', 'Widget'): '4', ('(no bin)', 'Widget'): '2'}
    assert report['site_totals'] == [dict(site='Warehouse', item_id=shop['widget'], item_name='Widget', quantity_on_hand='17')]
    a3 = run('report items-by-bin', dict(bin='A3', as_of='2017-12-31'))
    assert [(r['bin'], r['quantity_on_hand']) for r in a3['rows']] == [('A3:Top', '4')]
    # A return goes back to the bin its sale took from; a correction that names no bin keeps it.
    invoice = run('invoice show', dict(invoice=sale['id']))
    run('credit-memo post', dict(customer=shop['acme'], date='2017-03-06', lines=[dict(
        source_invoice=sale['id'], source_line=invoice['revision']['lines'][0]['line_id'], quantity='1')]),
        reason='Return')
    assert _bins(shop['books'])[('Widget', 'Warehouse', 'A2', None)] == 5
    _bin_ties(shop['books'])
    with pytest.raises(BookflowError) as untracked:
        _sell(shop, 'Widget', 1, site='Van 1', bin='A1')
    assert 'does not track bins' in untracked.value.message


def test_a_correction_keeps_its_bin_and_a_change_of_bin_alone_moves_the_stock(shop):
    run = shop['run']
    bill = _receive(shop, shop['widget'], 6, bin='A1')
    shown = run('bill show', dict(bill=bill['id']))
    line = shown['revision']['items'][0]
    run('bill update', dict(bill=bill['id'], expected_version=shown['version'], items=[dict(
        line_id=line['line_id'], item=shop['widget'], quantity='8', unit_cost='2.00')]), reason='Two more')
    assert _bins(shop['books']) == {('Widget', 'Warehouse', 'A1', None): 8}
    shown = run('bill show', dict(bill=bill['id']))
    line = shown['revision']['items'][0]
    run('bill update', dict(bill=bill['id'], expected_version=shown['version'], items=[dict(
        line_id=line['line_id'], item=shop['widget'], quantity='8', unit_cost='2.00', bin='A2')]), reason='Wrong bin')
    assert _bins(shop['books']) == {('Widget', 'Warehouse', 'A2', None): 8}
    _bin_ties(shop['books'])


def test_moving_between_bins_of_one_site_moves_quantity_only(shop):
    run = shop['run']
    _receive(shop, shop['widget'], 10, bin='A1')
    before = _net(shop['books'])
    moved = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Widget', quantity='4', from_bin='A1', to_bin='A3')]), reason='Restock A3')
    assert moved['lines'][0]['from_bin'] == 'A1' and moved['lines'][0]['to_bin'] == 'A3'
    assert _bins(shop['books']) == {('Widget', 'Warehouse', 'A1', None): 6, ('Widget', 'Warehouse', 'A3', None): 4}
    assert _net(shop['books']) == before
    with pytest.raises(BookflowError) as same:
        run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Warehouse', lines=[
            dict(item='Widget', quantity='1', from_bin='A1', to_bin='A1')]), reason='Nowhere')
    assert same.value.code == 'E_VALIDATION'
    with pytest.raises(BookflowError):
        run('inventory-transfer post', dict(from_site='Van 1', to_site='Van 1', lines=[
            dict(item='Widget', quantity='1')]), reason='No bins on the van')
    # Out to the van from a bin, then the transfer voided: both places restored.
    out = run('inventory-transfer post', dict(date='2017-03-03', from_site='Warehouse', to_site='Van 1', lines=[
        dict(item='Widget', quantity='2', from_bin='A3')]), reason='Van run')
    assert _bins(shop['books'])[('Widget', 'Van 1', None, None)] == 2
    run('inventory-transfer void', dict(transfer=out['number'], expected_version=out['version']), reason='Did not go')
    assert _bins(shop['books']) == {('Widget', 'Warehouse', 'A1', None): 6, ('Widget', 'Warehouse', 'A3', None): 4}
    warned = run('inventory-transfer post', dict(date='2017-03-04', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Widget', quantity='5', from_bin='A3', to_bin='A2')]), reason='Too many')
    assert any('Bin A3 at Warehouse now shows -1 Widget' in w and 'A1 holds 6' in w for w in warned['warnings'])
    _bin_ties(shop['books'])


def test_serial_numbers_and_lots_live_in_bins(shop):
    run, books = shop['run'], shop['books']
    heater = _part(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    sealant = _part(books, 'Sealant', cost='4.00', price='9.00', tracking='lot')
    _receive(shop, heater, 2, bin='A1', serial_numbers=['WH-1', 'WH-2'])
    _receive(shop, sealant, 5, bin='A2', lot='L-1')
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Water Heater', quantity='1', from_bin='A1', to_bin='A3', serial_numbers=['WH-2'])]), reason='Shelf')
    held = _bins(books)
    assert held[('Water Heater', 'Warehouse', 'A1', 'WH-1')] == 1 and held[('Water Heater', 'Warehouse', 'A3', 'WH-2')] == 1
    # A sale naming the wrong bin takes the unit from where the books hold it, and says so.
    sold = _sell(shop, 'Water Heater', 1, bin='A1', serial_numbers=['WH-2'])
    assert any('left from Warehouse bin A3' in w for w in sold.get('warnings', []))
    assert ('Water Heater', 'Warehouse', 'A3', 'WH-2') not in _bins(books)
    _sell(shop, 'Sealant', 2, bin='A2', lot='L-1')
    assert _bins(books)[('Sealant', 'Warehouse', 'A2', 'L-1')] == 3
    # Unassigned units in a bin are numbered there.
    _receive(shop, heater, 1, bin='A2')
    run('inventory assign', dict(item='Water Heater', serial_numbers=['WH-9'], site='Warehouse', bin='A2',
                                 date='2017-03-07'), reason='Label read')
    assert _bins(books)[('Water Heater', 'Warehouse', 'A2', 'WH-9')] == 1
    _bin_ties(books)


def test_the_database_refuses_a_bin_of_another_site_even_when_the_application_is_bypassed(shop):
    run = shop['run']
    run('inventory-site update', dict(site='Van 1', tracks_bins=True), reason='Van shelves')
    run('bin create', dict(site='Van 1', name='Rack'), reason='Van rack')
    _receive(shop, shop['widget'], 3, bin='A1')
    with sqlite3.connect(_db(shop['books'])) as conn:
        bin_van = conn.execute("SELECT id FROM inventory_bins WHERE full_name = 'Rack'").fetchone()[0]
        bin_a1 = conn.execute("SELECT id FROM inventory_bins WHERE full_name = 'A1'").fetchone()[0]
        warehouse = conn.execute("SELECT site_id FROM inventory_bins WHERE id = ?", (bin_a1,)).fetchone()[0]
        row = conn.execute("SELECT * FROM inventory_placements LIMIT 1").fetchone()
        columns = [r[1] for r in conn.execute('PRAGMA table_info(inventory_placements)')]
        values = dict(zip(columns, row))
        values.update(id='01HZZZZZZZZZZZZZZZZZZZZZZZ', bin_id=bin_van, site_id=warehouse, movement_id=None, source='count',
                      source_line_id='01HZZZZZZZZZZZZZZZZZZZZZZY', reverses_placement_id=None)
        with pytest.raises(sqlite3.IntegrityError, match='a placement names a bin of another site'):
            conn.execute(f'INSERT INTO inventory_placements ({", ".join(values)}) VALUES ({", ".join("?" * len(values))})',
                         list(values.values()))
        with pytest.raises(sqlite3.IntegrityError, match='keeps its site'):
            conn.execute('UPDATE inventory_bins SET site_id = (SELECT site_id FROM inventory_bins WHERE id = ?) WHERE id = ?',
                         (bin_van, bin_a1))
        with pytest.raises(sqlite3.IntegrityError, match='never deleted'):
            conn.execute('DELETE FROM inventory_bins WHERE id = ?', (bin_van,))
    with pytest.raises(BookflowError) as busy:
        run('bin deactivate', dict(bin='A1'), reason='Empty?')
    assert busy.value.code == 'E_RECORD_IN_USE' and 'Widget' in busy.value.message
    with pytest.raises(BookflowError) as off:
        run('inventory-site update', dict(site='Warehouse', tracks_bins=False), reason='No more bins')
    assert off.value.code == 'E_RECORD_IN_USE'


def test_a_scanned_bin_label_is_a_bin(shop):
    run = shop['run']
    _receive(shop, shop['widget'], 3, bin='A3')
    found = run('barcode lookup', dict(code='BIN-A3'))
    assert found['kind'] == 'bin' and found['match']['line_fields'] == {'site': 'Warehouse', 'bin': 'A3'}
    assert found['match']['on_hand'][0]['item'] == 'Widget' and found['match']['on_hand'][0]['quantity'] == '3'
    assert run('bin show', dict(bin='BIN-A3'))['full_name'] == 'A3'
    with pytest.raises(BookflowError) as taken:
        run('bin create', dict(name='A4', barcode='BIN-A3'), reason='Duplicate label')
    assert taken.value.code == 'E_VALIDATION'


# ---------------------------------------------------------------- counts by bin

def _post_count(run, made, account):
    preview = run('inventory-count post', dict(count=made['number'], expected_version=made['version'],
                                               adjustment_account=account), reason='Count', dry_run=True)
    return run('inventory-count post', dict(count=made['number'], expected_version=made['version'],
                                            adjustment_account=account,
                                            preview_fingerprint=preview['preview_fingerprint']), reason='Count')


def test_count_bin_a3_counts_exactly_what_a3_holds(shop):
    run, books = shop['run'], shop['books']
    shrinkage = books['client'].account.create(company=books['company'], name='Inventory Shrinkage', type='expense')['id']
    run('item update', dict(item='Elbow', barcode='0012345678905'), reason='Label')
    _receive(shop, shop['widget'], 10, bin='A1')
    _receive(shop, shop['widget'], 14, bin='A3')
    _receive(shop, shop['elbow'], 2, bin='A3:Top')
    made = run('inventory-count create', dict(site='Warehouse', date='2017-03-10', bins=['A3']), reason='Count bin A3')
    assert sorted((line['item_name'], line['bin'], line['expected']) for line in made['lines']) == [
        ('Elbow', 'A3:Top', '2'), ('Widget', 'A3', '14')]
    entered = run('inventory-count enter', dict(count=made['number'], entries=[
        dict(item='Widget', quantity='12'), dict(code='0012345678905', quantity='3', bin='A3:Top')]),
        reason='Counted bin A3')
    assert {(line['item_name'], line['bin'], line['difference']) for line in entered['entered']} == {
        ('Widget', 'A3', '-2'), ('Elbow', 'A3:Top', '1')}
    _post_count(run, made, shrinkage)
    held = _bins(books)
    assert held[('Widget', 'Warehouse', 'A3', None)] == 12 and held[('Widget', 'Warehouse', 'A1', None)] == 10
    assert held[('Elbow', 'Warehouse', 'A3:Top', None)] == 3
    # A misplaced unit: counted in A2, missing from A1 -- moved, never lost and found.
    made = run('inventory-count create', dict(site='Warehouse', date='2017-03-11', bins=['A1', 'A2']), reason='Count A')
    run('inventory-count enter', dict(count=made['number'], entries=[
        dict(item='Widget', quantity='9', bin='A1'), dict(item='Widget', quantity='1', bin='A2')]), reason='Counted')
    before = _net(books)
    _post_count(run, made, shrinkage)
    assert _net(books) == before
    assert _bins(books)[('Widget', 'Warehouse', 'A2', None)] == 1
    _bin_ties(books)


# ---------------------------------------------------------------- cycle counts

def _stocked(shop, names, cost='1.00', quantity=100, date='2017-01-02'):
    ids = {}
    for name in names:
        ids[name] = _part(shop['books'], name, cost=cost, price='3.00')
        shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
            item=ids[name], quantity=str(quantity), unit_cost=cost)]), reason='Stock')
    return ids


def test_classify_proposes_a_for_the_items_making_80_percent_of_issued_cost(shop):
    run = shop['run']
    sold = {'P1': 50, 'P2': 30, 'P3': 10, 'P4': 5, 'P5': 3, 'P6': 2}
    _stocked(shop, [*sold, 'P7'])
    for name, quantity in sold.items():
        _sell(shop, name, quantity, date='2017-02-01')
    # A count shortage and an adjustment are not issued cost.
    shrinkage = shop['books']['client'].account.create(company=shop['books']['company'], name='Shrinkage', type='expense')['id']
    run('inventory adjust', dict(item='P7', quantity_change='-40', date='2017-02-02', adjustment_account=shrinkage),
        reason='Broken')
    preview = run('cycle-count classify', dict(date_from='2017-01-01', date_to='2017-12-31'), dry_run=True)
    proposed = {row['item_name']: row['proposed_class'] for row in preview['rows'] if row['item_name'].startswith('P')}
    assert proposed == {'P1': 'A', 'P2': 'A', 'P3': 'B', 'P4': 'B', 'P5': 'C', 'P6': 'C', 'P7': 'C'}
    assert preview['total_issued_cost']['amount'] == '100.00' and preview['applied'] is False
    assert [r['cumulative_share'] for r in preview['rows'][:2]] == ['50.00', '80.00']
    assert run('cycle-class list', {})['cycle_classes'][0]['id'] == ''     # nothing stored by a dry run
    applied = run('cycle-count classify', dict(date_from='2017-01-01', date_to='2017-12-31'), reason='ABC')
    assert applied['applied'] is True
    classes = {row['name']: row for row in run('cycle-class list', {})['cycle_classes']}
    assert {name: row['interval_days'] for name, row in classes.items()} == {'A': 30, 'B': 90, 'C': 365}
    assert classes['A']['item_count'] == 2
    assert run('item show', dict(item='P3'))['cycle_class_id'] == classes['B']['id']
    again = run('cycle-count classify', dict(date_from='2017-01-01', date_to='2017-12-31'), dry_run=True)
    assert again['changes'] == 0
    run('item update', dict(item='P7', cycle_class='A'), reason='Theft-prone')
    assert run('item show', dict(item='P7'))['cycle_class_id'] == classes['A']['id']


def _count(run, site, date, account, **selection):
    made = run('inventory-count create', dict(site=site, date=date, **selection), reason='Cycle count')
    run('inventory-count enter', dict(count=made['number'], entries=[
        dict(item=line['item_name'], quantity=line['expected'], **({'bin': line['bin']} if line['bin'] else {}))
        for line in made['lines']]), reason='Counted')
    _post_count(run, made, account)
    return made


def test_due_dates_on_hand_worked_dates(shop):
    run, books = shop['run'], shop['books']
    account = books['client'].account.create(company=books['company'], name='Shrinkage', type='expense')['id']
    _stocked(shop, ['X', 'Y', 'Z', 'W'])
    run('item update', dict(item='X', cycle_class='A'), reason='Fast')
    run('item update', dict(item='Y', cycle_class='B'), reason='Medium')
    run('item update', dict(item='Z', cycle_class='C'), reason='Slow')
    _count(run, 'Warehouse', '2017-03-01', account, items=['X', 'Y'])
    due = run('cycle-count due', dict(site='Warehouse', as_of='2017-03-31'))
    # Z was never counted, so it has been due since it was first stocked (88 days, a quarter of C's 365); X fell due
    # today (0 of A's 30).
    assert [(r['item_name'], r['last_counted'], r['due_date'], r['days_overdue']) for r in due['rows']] == [
        ('Z', None, '2017-01-02', 88), ('X', '2017-03-01', '2017-03-31', 0)]
    assert due['not_due'] == 1        # Y falls due on 2017-05-30; W is unclassified
    assert [r['item_name'] for r in run('cycle-count due', dict(site='Warehouse', as_of='2017-03-30'))['rows']] == ['Z']
    assert 'Y' in [r['item_name'] for r in run('cycle-count due', dict(site='Warehouse', as_of='2017-05-30'))['rows']]
    # An item counted today is not due tomorrow; the van, where nothing ever was, has nothing due.
    _count(run, 'Warehouse', '2017-04-03', account, cycle=True, limit=1)
    assert [r['item_name'] for r in run('cycle-count due', dict(site='Warehouse', as_of='2017-04-04'))['rows']] == ['X']
    assert run('cycle-count due', dict(site='Van 1', as_of='2017-04-04'))['rows'] == []


def test_weekly_cycle_counts_reach_every_item_within_its_interval(shop):
    """Nine items, three per class, counted in weekly rounds of three for 20 weeks: every item is counted in the
    first rounds, and none waits longer than its interval plus the round it falls due in."""
    from datetime import date, timedelta
    run, books = shop['run'], shop['books']
    account = books['client'].account.create(company=books['company'], name='Shrinkage', type='expense')['id']
    names = [f'{cls}{n}' for cls in 'ABC' for n in range(1, 4)]
    _stocked(shop, names, date='2016-12-01')
    for name in names:
        run('item update', dict(item=name, cycle_class=name[0]), reason='Class')
    interval = {'A': 30, 'B': 90, 'C': 365}
    counted = {name: [] for name in names}
    day = date(2017, 1, 2)
    for week in range(20):
        try:
            made = _count(run, 'Warehouse', day.isoformat(), account, cycle=True, limit=3)
        except BookflowError as nothing:
            assert 'no item is due' in str(nothing.details)
            day += timedelta(days=7)
            continue
        assert made['selection']['kind'] == 'cycle'
        for line in made['lines']:
            counted[line['item_name']].append(day)
        day += timedelta(days=7)
    assert all(counted[name] and counted[name][0] <= date(2017, 1, 16) for name in names)
    end = day - timedelta(days=7)
    for name, days in counted.items():
        stops = days + [end]
        gaps = [(b - a).days for a, b in zip(days, stops[1:])]
        assert max(gaps) <= interval[name[0]] + 6, (name, days)


# ---------------------------------------------------------------- the migration

def test_co0097_on_a_populated_company_changes_no_existing_row_and_matches_the_metadata(shop):
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from bookflow.company import schema as c
    from tests.test_negative_stock_migration import _at
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0097_bins_cycle_counts')
    assert m.DDL == str(CreateTable(c.metadata.tables['cycle_classes']).compile(dialect=dialect()))
    from bookflow.company import bin_cycle_schema
    assert m.GUARDS == bin_cycle_schema.GUARDS
    _receive(shop, shop['widget'], 5, bin='A1')
    _sell(shop, 'Widget', 2, bin='A1')
    path = _db(shop['books'])
    copy = path.parent / 'rewound97.db'
    with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        for trigger in m.TRIGGERS:
            conn.execute(f'DROP TRIGGER {trigger}')
        conn.execute(f'DROP INDEX {m.INDEX_NAME}')
        conn.execute('ALTER TABLE items DROP COLUMN cycle_class_id')
        conn.execute('DROP TABLE cycle_classes')
        conn.execute("UPDATE alembic_version SET version_num='co0095'")
        conn.commit()
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name != 'alembic_version'")]
        before = {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}
    _at(copy, 'co0097')
    with sqlite3.connect(copy) as conn:
        for name in names:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            if name == 'items':
                columns = [r[1] for r in conn.execute('PRAGMA table_info(items)')]
                at = columns.index('cycle_class_id')
                assert {row[at] for row in rows} == {None}
                rows = [row[:at] + row[at + 1:] for row in rows]
            assert sorted(rows, key=repr) == before[name], name
        assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name IN ('cycle_classes', "
                            "'inventory_placements_bin_site', 'inventory_bins_parent')").fetchone() == (3,)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


# ---------------------------------------------------------------- seeded paperwork with bins

import os as _os
SEEDS = tuple(range(51, 51 + int(_os.environ.get('BF_BIN_SEEDS', '4'))))
STEPS = int(_os.environ.get('BF_BIN_STEPS', '30'))
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04')
REFUSED = ('E_INTERNAL', 'E_IO', 'E_GUARD_REFUSED')


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_paperwork_with_bins_keeps_every_tie(shop, seed):
    """The increment-4 checker with bins: late paperwork in a fixed random order -- receipts and sales into and
    out of bins, bin-to-bin transfers, transfers between sites, counts by bin, assignments in bins, returns and
    voids, with serial and lot numbers -- and after every step: bins sum to their site, the number tie at every
    bin, the quantity tie and the asset tie. No valid paperwork is refused by a guard or internally."""
    import random
    from tests.test_inventory_count_review import _asset
    run, books = shop['run'], shop['books']
    rnd = random.Random(seed)
    heater = _part(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    sealant = _part(books, 'Sealant', cost='4.00', price='9.00', tracking='lot')
    shrinkage = books['client'].account.create(company=books['company'], name='Inventory Shrinkage', type='expense')['id']
    sites, bins_, lots = ['Warehouse', 'Van 1'], ['A1', 'A2', 'A3', 'A3:Top', None], ['L-1', 'L-2']
    serials = [f'WH-{n}' for n in range(1, 9)]
    sales, counts, transfers = [], [], []

    def where(site):
        place = {'site': site}
        chosen = rnd.choice(bins_) if site == 'Warehouse' else None
        if chosen:
            place['bin'] = chosen
        return place

    for step in range(STEPS):
        op = rnd.choice(['receive', 'receive', 'sell', 'sell', 'bin_move', 'transfer', 'assign', 'return', 'count',
                         'count_bins', 'void', 'lot_in', 'lot_out', 'widget_in', 'widget_out', 'retransfer'])
        date = rnd.choice(DATES)
        site = rnd.choice(sites)
        try:
            if op == 'receive':
                named = rnd.sample(serials, rnd.randint(0, 2))
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=heater, quantity=str(len(named) + rnd.randint(0, 1) or 1), unit_cost='100.00',
                    serial_numbers=named, **where(site))]), reason='In')
            elif op == 'sell':
                named = rnd.sample(serials, rnd.randint(0, 1))
                payload = dict(customer=shop['acme'], date=date, lines=[dict(
                    item='Water Heater', quantity='1', serial_numbers=named, **where(site))])
                if rnd.random() < .3:
                    payload['count_position'] = 'after'
                sales.append(run('invoice post', payload, reason='Job')['id'])
            elif op == 'bin_move':
                a, b = rnd.sample(bins_, 2)
                item = rnd.choice(['Water Heater', 'Sealant', 'Widget'])
                line = dict(item=item, quantity='1', **({'from_bin': a} if a else {}), **({'to_bin': b} if b else {}))
                if item == 'Water Heater':
                    line['serial_numbers'] = rnd.sample(serials, rnd.randint(0, 1))
                elif item == 'Sealant' and rnd.random() < .6:
                    line['lot'] = rnd.choice(lots)
                transfers.append(run('inventory-transfer post', dict(date=date, from_site='Warehouse', to_site='Warehouse',
                                                                     lines=[line]), reason='Shelve'))
            elif op == 'transfer':
                a, b = rnd.sample(sites, 2)
                line = dict(item='Water Heater', quantity='1', serial_numbers=rnd.sample(serials, rnd.randint(0, 1)))
                if a == 'Warehouse' and rnd.random() < .7:
                    line['from_bin'] = rnd.choice(bins_[:-1])
                if b == 'Warehouse' and rnd.random() < .7:
                    line['to_bin'] = rnd.choice(bins_[:-1])
                transfers.append(run('inventory-transfer post', dict(date=date, from_site=a, to_site=b, lines=[line]),
                                     reason='Move'))
            elif op == 'retransfer' and transfers:
                old = transfers.pop(rnd.randrange(len(transfers)))
                current = run('inventory-transfer show', dict(transfer=old['number']))
                if current['status'] == 'posted':
                    if rnd.random() < .5:
                        run('inventory-transfer void', dict(transfer=old['number'], expected_version=current['version']),
                            reason='Undo')
                    else:
                        run('inventory-transfer update', dict(transfer=old['number'], expected_version=current['version'],
                                                              date=rnd.choice(DATES)), reason='Redate')
            elif op == 'assign':
                run('inventory assign', dict(item='Water Heater', serial_numbers=[rnd.choice(serials)], date=date,
                                             **where(site)), reason='Labels')
            elif op == 'return' and sales:
                sale = run('invoice show', dict(invoice=rnd.choice(sales)))
                if sale['status'] == 'posted':
                    run('credit-memo post', dict(customer=shop['acme'], date=max(date, sale['revision']['date']), lines=[dict(
                        source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'], quantity='1')]),
                        reason='Return')
            elif op in ('count', 'count_bins'):
                selection = (dict(bins=rnd.sample(bins_[:-1], rnd.randint(1, 2))) if op == 'count_bins' and site == 'Warehouse'
                             else dict(items=['Water Heater', 'Sealant', 'Widget']))
                made = run('inventory-count create', dict(site=site, date=date, **selection), reason='Count')
                entries = []
                for line in made['lines']:
                    serial = line['place'].startswith('serial ')
                    entries.append(dict(item=line['item_name'], quantity=str(rnd.randint(0, 1) if serial else rnd.randint(0, 6)),
                                        **({'bin': line['bin']} if line['bin'] else {}),
                                        **({'serial_number': line['place'][7:]} if serial else {}),
                                        **({'lot': line['place'][4:]} if line['place'].startswith('lot ') else {})))
                if site == 'Warehouse' and rnd.random() < .5:
                    entries.append(dict(item='Widget', quantity=str(rnd.randint(0, 3)), bin=rnd.choice(bins_[:-1])))
                if entries:
                    run('inventory-count enter', dict(count=made['number'], entries=entries), reason='Counted')
                _post_count(run, made, shrinkage)
                counts.append(made['number'])
            elif op == 'void' and counts:
                number = counts.pop(rnd.randrange(len(counts)))
                run('inventory-count void', dict(count=number, expected_version=run(
                    'inventory-count show', dict(count=number))['version']), reason='Recount')
            elif op == 'lot_in':
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=sealant, quantity=str(rnd.randint(1, 5)), unit_cost='4.00', lot=rnd.choice(lots), **where(site))]),
                    reason='Sealant in')
            elif op == 'lot_out':
                line = dict(item='Sealant', quantity=str(rnd.randint(1, 3)), **where(site))
                if rnd.random() < .7:
                    line['lot'] = rnd.choice(lots)
                sales.append(run('invoice post', dict(customer=shop['acme'], date=date, lines=[line]),
                                 reason='Sealant')['id'])
            elif op == 'widget_in':
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=shop['widget'], quantity=str(rnd.randint(1, 6)), unit_cost='2.00', **where(site))]), reason='In')
            elif op == 'widget_out':
                sales.append(run('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
                    item='Widget', quantity=str(rnd.randint(1, 3)), **where(site))]), reason='Out')['id'])
        except BookflowError as error:
            # Refusals a person can act on are fine (a number already in stock or gone, over-returning, a place
            # counted twice); nothing internal, and no guard refusing valid paperwork.
            assert error.code not in REFUSED, (seed, step, op, error)
        _bin_ties(books)
        valuation = run('report inventory-valuation', dict(as_of='2017-12-31'))
        assert valuation['totals']['asset_value']['minor_units'] == _net(books).get(_asset(books), 0), (seed, step, op)



# ---------------------------------------------------------------- review fixes (R104 increment 5 review)

def _count_bins(run, account, date, bins_, entries):
    made = run('inventory-count create', dict(site='Warehouse', date=date, bins=bins_), reason='Count')
    run('inventory-count enter', dict(count=made['number'], entries=entries), reason='Counted')
    _post_count(run, made, account)
    return made


def test_a_bin_only_correction_after_a_count_of_that_bin_is_refused(shop):
    """Bill 5 into A1, count A1 = 5, then move the bill line to A2: the count keeps A1 at 5, so the line's units
    would be in A2 too and the stock doubles. Refused, naming the count and the transfer to record instead."""
    run, books = shop['run'], shop['books']
    account = books['client'].account.create(company=books['company'], name='Shrinkage', type='expense')['id']
    bill = _receive(shop, shop['widget'], 5, bin='A1')
    made = _count_bins(run, account, '2017-03-02', ['A1'], [dict(item='Widget', quantity='5', bin='A1')])
    before = _net(books)
    shown = run('bill show', dict(bill=bill['id']))
    line = shown['revision']['items'][0]
    with pytest.raises(BookflowError) as moved:
        run('bill update', dict(bill=bill['id'], expected_version=shown['version'], items=[dict(
            line_id=line['line_id'], item=shop['widget'], quantity='5', unit_cost='2.00', bin='A2')]), reason='Wrong bin')
    assert moved.value.code == 'E_VALIDATION'
    assert f"count {made['number']} found these in A1; record a transfer from A1 to A2 dated after the count instead" \
        in str(moved.value.details)
    assert _net(books) == before and _bins(books) == {('Widget', 'Warehouse', 'A1', None): 5}
    # A change of quantity or price is not a move, and goes through (the count is derived again).
    run('bill update', dict(bill=bill['id'], expected_version=shown['version'], items=[dict(
        line_id=line['line_id'], item=shop['widget'], quantity='6', unit_cost='2.50')]), reason='Invoice said 6')
    assert _bins(books) == {('Widget', 'Warehouse', 'A1', None): 5}
    _bin_ties(books)
    # The serial variant: the count derived again by a move names itself.
    heater = _part(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    bill2 = _receive(shop, heater, 1, bin='A1', serial_numbers=['WH-1'])
    _count_bins(run, account, '2017-03-03', ['A1'], [dict(item='Water Heater', quantity='1', serial_number='WH-1', bin='A1')])
    shown = run('bill show', dict(bill=bill2['id']))
    line = shown['revision']['items'][0]
    with pytest.raises(BookflowError) as serial:
        run('bill update', dict(bill=bill2['id'], expected_version=shown['version'], items=[dict(
            line_id=line['line_id'], item=heater, quantity='1', unit_cost='2.00', serial_numbers=['WH-1'], bin='A2')]),
            reason='Wrong bin')
    assert serial.value.code == 'E_VALIDATION' and 'Count C-' in serial.value.message
    _bin_ties(books)


def test_stock_at_no_bin_can_be_named_moved_and_sold_once_a_default_bin_exists(shop):
    run, books = shop['run'], shop['books']
    _receive(shop, shop['widget'], 5)                                   # at no bin
    run('item update', dict(item='Widget', site_settings=[dict(site='Warehouse', default_bin='A3')]), reason='Home bin')
    # to_bin only: the units leave from where they are (no bin), not from the empty default bin.
    moved = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Widget', quantity='2', to_bin='A3')]), reason='Shelve')
    assert moved['lines'][0]['from_bin'] is None and not moved['warnings']
    named = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Widget', quantity='1', from_bin='(No Bin)', to_bin='A2')]), reason='Shelve')
    assert named['lines'][0]['from_bin'] is None
    assert _bins(books) == {('Widget', 'Warehouse', None, None): 2, ('Widget', 'Warehouse', 'A3', None): 2,
                            ('Widget', 'Warehouse', 'A2', None): 1}
    # A sale naming no bin: the default bin holds 2, so 2 come from A3; then 3 more must come from no bin and A2,
    # never driving a bin below zero while units sit elsewhere.
    sale = _sell(shop, 'Widget', 2, date='2017-03-03')
    assert _bins(books)[('Widget', 'Warehouse', None, None)] == 2 and ('Widget', 'Warehouse', 'A3', None) not in _bins(books)
    assert not [w for w in sale.get('warnings', []) if 'now shows -' in w]
    _sell(shop, 'Widget', 3, date='2017-03-04')
    assert {k: v for k, v in _bins(books).items() if k[1] == 'Warehouse'} == {}
    # Nothing left at the site: the next one goes below zero at the default bin, with a warning.
    short = _sell(shop, 'Widget', 1, date='2017-03-05')
    assert _bins(books) == {('Widget', 'Warehouse', 'A3', None): -1}
    assert any('Bin A3 at Warehouse now shows -1 Widget' in w for w in short.get('warnings', []))
    _bin_ties(books)


def test_an_item_counted_in_one_of_its_bins_is_not_counted(shop):
    run, books = shop['run'], shop['books']
    account = books['client'].account.create(company=books['company'], name='Shrinkage', type='expense')['id']
    _receive(shop, shop['widget'], 10, date='2017-01-03', bin='A1')
    _receive(shop, shop['widget'], 5, date='2017-01-03', bin='A2')
    run('item update', dict(item='Widget', cycle_class='A'), reason='Class')
    _count_bins(run, account, '2017-03-04', ['A1'], [dict(item='Widget', quantity='10', bin='A1')])
    row = [r for r in run('cycle-count due', dict(site='Warehouse', as_of='2017-03-10'))['rows'] if r['item_name'] == 'Widget']
    assert row and row[0]['last_counted'] is None
    _count_bins(run, account, '2017-03-05', ['A1', 'A2'], [dict(item='Widget', quantity='10', bin='A1'),
                                                           dict(item='Widget', quantity='5', bin='A2')])
    assert not [r for r in run('cycle-count due', dict(site='Warehouse', as_of='2017-03-10'))['rows'] if r['item_name'] == 'Widget']
    empty = run('cycle-count classify', dict(date_from='2016-01-01', date_to='2016-12-31'), dry_run=True)
    assert empty['changes'] == 0 and 'No goods were issued' in empty['message']


def test_a_serial_number_split_over_two_bins_says_so(shop):
    """Late paperwork within one site: WH-1 received into A1, moved to A3 on the 3rd, and a sale backdated to the
    2nd takes it from A1, where it was then. The transfer now moves a unit A1 no longer holds: A1 shows -1 and A3
    shows 1. The ties hold, and the sale says the paperwork places the unit in two bins."""
    run, books = shop['run'], shop['books']
    heater = _part(books, 'Water Heater', cost='100.00', price='300.00', tracking='serial')
    _receive(shop, heater, 1, date='2017-03-01', bin='A1', serial_numbers=['WH-1'])
    run('inventory-transfer post', dict(date='2017-03-03', from_site='Warehouse', to_site='Warehouse', lines=[
        dict(item='Water Heater', quantity='1', from_bin='A1', to_bin='A3', serial_numbers=['WH-1'])]), reason='Shelve')
    sold = _sell(shop, 'Water Heater', 1, date='2017-03-02', serial_numbers=['WH-1'])
    assert any('in two bins' in w for w in sold.get('warnings', [])), sold.get('warnings')
    _bin_ties(books)


def test_thirty_items_in_weekly_rounds_keep_every_class_within_its_interval_plus_a_round(shop):
    """Ten items per class, rounds of five a week for 60 weeks: after a warm-up of twelve weeks, no class's worst
    gap between counts passes its interval plus one round (the due list ranks by lateness over the interval)."""
    from datetime import date, timedelta
    run, books = shop['run'], shop['books']
    account = books['client'].account.create(company=books['company'], name='Shrinkage', type='expense')['id']
    names = [f'{cls}{n}' for cls in 'ABC' for n in range(10)]
    _stocked(shop, names, quantity=5, date='2016-12-01')
    for name in names:
        run('item update', dict(item=name, cycle_class=name[0]), reason='Class')
    interval = {'A': 30, 'B': 90, 'C': 365}
    counted = {name: [] for name in names}
    day = date(2017, 1, 2)
    for week in range(60):
        try:
            made = _count(run, 'Warehouse', day.isoformat(), account, cycle=True, limit=5)
        except BookflowError as nothing:
            assert 'no item is due' in str(nothing.details)
        else:
            for line in made['lines']:
                counted[line['item_name']].append(day)
        day += timedelta(days=7)
    warm = date(2017, 1, 2) + timedelta(weeks=12)
    worst = {}
    for name, days in counted.items():
        assert days, name
        stops = days + [day]
        for a, b in zip(stops, stops[1:]):
            if a >= warm:
                worst[name[0]] = max(worst.get(name[0], 0), (b - a).days)
    assert all(worst[cls] <= interval[cls] + 7 for cls in worst), worst
