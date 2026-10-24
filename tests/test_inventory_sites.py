"""R104 increment 1: where stock is (placements), sites, transfers, and figures by site.

A plumber with a warehouse and a van. Twenty widgets arrive at the warehouse, five go on the van and
three are sold from it, leaving 15 and 2, with the general ledger and the item's value untouched by
the transfer. Throughout, placements equal the movement ledger's quantity on every date (the quantity
tie), the database refuses a movement whose placements do not sum to it even when the application is
bypassed, and corrections and voids of sales, receipts, returns and builds move placements exactly.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401


def _db(books):
    import os
    for path in Path(os.environ['BOOKFLOW_DATA_ROOT']).rglob('*.db'):
        with sqlite3.connect(path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='inventory_placements'").fetchone():
                return path
    raise AssertionError('no company database found')


def _part(books, name='Widget', cost='2.00'):
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part', description=name, price='5.00',
        purchase_description=name, cost=cost, cogs_account_id=books['cogs'], income_account_id=books['income'])['id']


def _by_site(books, as_of='2017-12-31', **extra):
    rows = books['run']('report quantity-by-site', dict(as_of=as_of, limit=200, **extra))['rows']
    return {(row['item_name'], row['site_name']): row for row in rows}


def _held(books, as_of='2017-12-31'):
    return {key: row['quantity_on_hand'] for key, row in _by_site(books, as_of).items()
            if row['quantity_on_hand'] != '0'}


def _tie_every_date(books):
    """Placements and movements agree per item on every date either ledger has a row on."""
    with sqlite3.connect(_db(books)) as conn:
        dates = [row[0] for row in conn.execute(
            'SELECT effective_date FROM inventory_movements UNION SELECT effective_date FROM inventory_placements')]
        for date in dates:
            placed = dict(conn.execute('SELECT item_id, sum(quantity_microunits) FROM inventory_placements '
                                       'WHERE effective_date <= ? GROUP BY item_id', (date,)).fetchall())
            moved = dict(conn.execute('SELECT item_id, sum(quantity_microunits) FROM inventory_movements '
                                      'WHERE effective_date <= ? GROUP BY item_id', (date,)).fetchall())
            placed = {k: v for k, v in placed.items() if v}
            moved = {k: v for k, v in moved.items() if v}
            assert placed == moved, date


@pytest.fixture
def shop(books):
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='Our warehouse')
    van = run('inventory-site create', dict(name='Van 1', description='Service van'), reason='The van')
    widget = _part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01',
                          items=[dict(item=widget, quantity='20', unit_cost='2.00', site='Warehouse')]), reason='In')
    return dict(books=books, run=run, widget=widget, van=van)


def test_receive_move_and_sell_leaves_15_and_2_and_the_transfer_posts_nothing(shop):
    books, run = shop['books'], shop['run']
    ledger_before = _net(books)
    value_before = run('report inventory-valuation', dict(as_of='2017-12-31'))['totals']['asset_value']
    preview = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                                  lines=[dict(item='Widget', quantity='5')]),
                  reason='Load the van', dry_run=True)
    assert preview['status'] == 'will_post' and preview['posts_to_ledger'] is False
    moved = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                                lines=[dict(item='Widget', quantity='5')]), reason='Load the van')
    assert moved['status'] == 'posted' and moved['number'] == 'T-1'
    assert {(r['site_name'], r['quantity_on_hand']) for r in moved['on_hand_after']} == {('Warehouse', '15'), ('Van 1', '5')}
    assert _net(books) == ledger_before
    assert run('report inventory-valuation', dict(as_of='2017-12-31'))['totals']['asset_value'] == value_before

    run('invoice post', dict(customer=books['customer'], date='2017-03-03', site='Van 1',
                             lines=[dict(item=shop['widget'], quantity='3')]), reason='Sold from the van')
    assert _held(books) == {('Widget', 'Warehouse'): '15', ('Widget', 'Van 1'): '2'}
    # One cost company-wide: 17 left at 2.00 is 34.00, shared by quantity, summing to the account.
    valuation = run('report inventory-valuation', dict(as_of='2017-12-31', by='site'))
    by_site = {row['site_name']: row['asset_value']['minor_units'] for row in valuation['rows']}
    assert by_site == {'Warehouse': 3000, 'Van 1': 400}
    assert valuation['totals']['asset_value']['minor_units'] == 3400
    company = run('report stock-status', dict(as_of='2017-12-31'))['rows']
    assert [(r['quantity_on_hand'], r['asset_value']['minor_units']) for r in company] == [('17', 3400)]
    assert 'site_id' not in company[0]          # the company reading reads exactly as before
    van = run('report stock-status', dict(as_of='2017-12-31', site='Van 1'))['rows'][0]
    assert van['quantity_on_hand'] == '2' and van['site_name'] == 'Van 1'
    _tie_every_date(books)


def test_a_van_sale_past_its_stock_warns_and_names_the_warehouse(shop):
    run = shop['run']
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                        lines=[dict(item='Widget', quantity='2')]), reason='Load')
    sold = run('invoice post', dict(customer=shop['books']['customer'], date='2017-03-03',
                                    lines=[dict(item=shop['widget'], quantity='3', site='Van 1')]), reason='Sold')
    assert 'Van 1 now shows -1 Widget; Warehouse holds 18.' in sold['warnings']
    _tie_every_date(shop['books'])


def test_voiding_the_transfer_restores_both_sites_and_updating_it_moves_them(shop):
    run = shop['run']
    moved = run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                                lines=[dict(item='Widget', quantity='5')]), reason='Load')
    fixed = run('inventory-transfer update', dict(transfer=moved['number'], expected_version=1,
                                                  lines=[dict(item='Widget', quantity='4')]), reason='Only four')
    assert fixed['version'] == 2
    assert _held(shop['books']) == {('Widget', 'Warehouse'): '16', ('Widget', 'Van 1'): '4'}
    with pytest.raises(BookflowError) as refused:
        run('inventory-transfer void', dict(transfer=moved['number']), reason='')
    assert refused.value.code in ('E_REASON_REQUIRED', 'E_VALIDATION')
    voided = run('inventory-transfer void', dict(transfer=moved['number'], expected_version=2), reason='Never loaded')
    assert voided['status'] == 'voided'
    assert _held(shop['books']) == {('Widget', 'Warehouse'): '20'}
    shown = run('inventory-transfer show', dict(transfer=moved['number']))
    assert [h['operation'] for h in shown['history']] == ['post', 'update', 'void']
    with pytest.raises(BookflowError):
        run('inventory-transfer update', dict(transfer=moved['number'], expected_version=3, memo='x'), reason='No')
    _tie_every_date(shop['books'])


def test_a_closed_period_transfer_is_refused(shop):
    run = shop['run']
    moved = run('inventory-transfer post', dict(date='2017-03-05', from_site='Warehouse', to_site='Van 1',
                                                lines=[dict(item='Widget', quantity='1')]), reason='Load')
    run('company update', {'closing_date': '2017-03-31'})
    for name, args in (('inventory-transfer post', dict(date='2017-03-10', from_site='Warehouse', to_site='Van 1',
                                                         lines=[dict(item='Widget', quantity='1')])),
                       ('inventory-transfer void', dict(transfer=moved['number'])),
                       ('inventory-transfer update', dict(transfer=moved['number'], expected_version=1,
                                                          date='2017-04-02'))):
        with pytest.raises(BookflowError) as refused:
            run(name, args, reason='Too late')
        assert refused.value.code == 'E_PERIOD_CLOSED'


def test_the_database_refuses_unbalanced_placements_even_when_the_application_is_bypassed(shop, monkeypatch):
    from bookflow.company import placements
    books = shop['books']
    with monkeypatch.context() as patched, pytest.raises(Exception) as refused:
        patched.setattr(placements, 'placements_for_rows', lambda s, rows, sites=None, planned=None: [])
        books['run']('invoice post', dict(customer=books['customer'], date='2017-03-03',
                                          lines=[dict(item=shop['widget'], quantity='1')]), reason='Sold')
    chain, cause = [], refused.value
    while cause is not None:
        chain.append(str(cause))
        cause = cause.__cause__ or cause.__context__
    assert any('placements do not sum' in text for text in chain), chain
    assert _held(books) == {('Widget', 'Warehouse'): '20'}          # nothing was written
    with sqlite3.connect(_db(books)) as conn:
        movement = conn.execute("SELECT * FROM inventory_movements WHERE kind='receipt'").fetchone()
        columns = [c[1] for c in conn.execute('PRAGMA table_info(inventory_movements)')]
        row = dict(zip(columns, movement))
        row.update(id='01ZZZZZZZZZZZZZZZZZZZZZZZZ', sequence=999999, posting_line_id=None, value_minor_units=0)
        with pytest.raises(sqlite3.IntegrityError, match='placements do not sum'):
            conn.execute(f"INSERT INTO inventory_movements ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
        placement = conn.execute('SELECT * FROM inventory_placements').fetchone()
        names = [c[1] for c in conn.execute('PRAGMA table_info(inventory_placements)')]
        late = dict(zip(names, placement), id='01ZZZZZZZZZZZZZZZZZZZZZZZY')
        with pytest.raises(sqlite3.IntegrityError, match='already written'):
            conn.execute(f"INSERT INTO inventory_placements ({', '.join(late)}) VALUES ({', '.join('?' * len(late))})",
                         list(late.values()))
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('UPDATE inventory_placements SET quantity_microunits = 1')
        conn.rollback()


def test_corrections_and_voids_move_placements_exactly(shop):
    books, run, widget = shop['books'], shop['run'], shop['widget']
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                        lines=[dict(item='Widget', quantity='10')]), reason='Load')
    # A sale from the van, corrected and voided: the correction stays where the sale took from.
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03',
                                    lines=[dict(item=widget, quantity='3', site='Van 1')]), reason='Sold')
    assert _held(books) == {('Widget', 'Warehouse'): '10', ('Widget', 'Van 1'): '7'}
    sale = run('invoice update', dict(invoice=sale['id'], expected_version=sale['version'],
                                      lines=[dict(line_id=sale['revision']['lines'][0]['line_id'], item=widget,
                                                  quantity='4')]), reason='Four, not three')
    assert _held(books) == {('Widget', 'Warehouse'): '10', ('Widget', 'Van 1'): '6'}
    # A returning credit memo gives back to the van, where its sale took from; voided, it leaves again.
    credit = run('credit-memo post', dict(customer=books['customer'], date='2017-03-04', lines=[dict(
        source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'], quantity='1')]),
        reason='One back')
    assert _held(books)[('Widget', 'Van 1')] == '7'
    run('credit-memo void', dict(credit_memo=credit['id'], expected_version=credit['version']), reason='Kept it')
    assert _held(books)[('Widget', 'Van 1')] == '6'
    run('invoice void', dict(invoice=sale['id'], expected_version=sale['version']), reason='Cancelled')
    assert _held(books) == {('Widget', 'Warehouse'): '10', ('Widget', 'Van 1'): '10'}
    # A receipt at the van, corrected and voided.
    bill = run('bill post', dict(vendor=books['vendor'], date='2017-03-05',
                                 items=[dict(item=widget, quantity='5', unit_cost='2.00', site='Van 1')]), reason='Van buy')
    assert _held(books)[('Widget', 'Van 1')] == '15'
    bill = run('bill update', dict(bill=bill['id'], expected_version=bill['version'], items=[dict(
        line_id=bill['revision']['items'][0]['line_id'], item=widget, quantity='6', unit_cost='2.00')]),
        reason='Six came')
    assert _held(books)[('Widget', 'Van 1')] == '16'
    run('bill void', dict(bill=bill['id'], expected_version=bill['version']), reason='Wrong vendor')
    assert _held(books)[('Widget', 'Van 1')] == '10'
    # A build at the van takes its components there and puts the kit there; voided, both come back.
    kit = books['client'].item.create(
        company=books['company'], name='Widget Pair', type='inventory_assembly', description='Two widgets',
        price='12.00', purchase_description='Two widgets', cost='0.00', cogs_account_id=books['cogs'],
        income_account_id=books['income'], members=[dict(component_item_id=widget, quantity='2')])['id']
    build = run('assembly build', dict(item=kit, quantity='3', date='2017-03-06', site='Van 1'), reason='Pairs')
    assert build['site_name'] == 'Van 1'
    held = _held(books)
    assert held[('Widget', 'Van 1')] == '4' and held[('Widget Pair', 'Van 1')] == '3'
    run('assembly void', dict(build=build['build_id'], expected_version=build['version']), reason='Undo')
    held = _held(books)
    assert held[('Widget', 'Van 1')] == '10' and ('Widget Pair', 'Van 1') not in held
    # An adjustment names its site too.
    run('inventory adjust', dict(item=widget, date='2017-03-07', adjustment_account=books['cogs'],
                                 quantity_change='-1', site='Van 1'), reason='Lost one')
    assert _held(books) == {('Widget', 'Warehouse'): '10', ('Widget', 'Van 1'): '9'}
    _tie_every_date(books)


def test_committed_on_order_and_available_by_site_against_hand_worked_figures(shop):
    books, run, widget = shop['books'], shop['run'], shop['widget']
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                        lines=[dict(item='Widget', quantity='5')]), reason='Load')
    run('sales-order create', dict(customer=books['customer'], date='2017-03-05',
                                   lines=[dict(item=widget, quantity='3', site='Van 1')]), reason='Van job')
    run('sales-order create', dict(customer=books['customer'], date='2017-03-05',
                                   lines=[dict(item=widget, quantity='4')]), reason='Unassigned order')
    run('work-order create', dict(customer=books['customer'], date='2017-03-05', title='Shop job',
                                  lines=[dict(item=widget, quantity='2', site='Warehouse')]), reason='Job')
    run('purchase-order post', dict(vendor=books['vendor'], date='2017-03-06',
                                    lines=[dict(item=widget, quantity='10', rate='2.00', site='Van 1'),
                                           dict(item=widget, quantity='6', rate='2.00')]), reason='Order')
    rows = _by_site(books)
    # Warehouse: 15 on hand, 2 committed (the work order), 13 available, 6 on order (the line with no site).
    w = rows[('Widget', 'Warehouse')]
    assert (w['quantity_on_hand'], w['quantity_committed'], w['quantity_available'], w['quantity_on_order']) == \
        ('15', '2', '13', '6')
    # Van 1: 5 on hand, 3 committed, 2 available, 10 on order.
    v = rows[('Widget', 'Van 1')]
    assert (v['quantity_on_hand'], v['quantity_committed'], v['quantity_available'], v['quantity_on_order']) == \
        ('5', '3', '2', '10')
    # The unassigned order commits on its own row; the sites plus unassigned are the company figures.
    u = rows[('Widget', 'Unassigned')]
    assert (u['quantity_on_hand'], u['quantity_committed']) == ('0', '4')
    company = run('report stock-status', dict(as_of='2017-12-31'))['rows'][0]
    assert (company['quantity_committed'], company['quantity_available'], company['quantity_on_order']) == ('9', '11', '16')
    van = run('report stock-status', dict(as_of='2017-12-31', site='Van 1'))['rows'][0]
    assert (van['quantity_committed'], van['quantity_available'], van['quantity_on_order']) == ('3', '2', '10')
    # Per-site reorder points are the site's own.
    run('item update', dict(item=widget, site_settings=[dict(site='Van 1', reorder_point_min='2',
                                                             reorder_point_max='8')]), reason='Van stock level')
    van = run('report stock-status', dict(as_of='2017-12-31', site='Van 1'))['rows'][0]
    assert van['reorder_point_min'] == '2' and van['below_reorder_point'] is True


def test_a_site_holding_stock_cannot_be_deactivated_and_an_unknown_site_lists_the_sites(shop):
    run = shop['run']
    with pytest.raises(BookflowError) as held:
        run('inventory-site deactivate', dict(site='Warehouse'), reason='Close it')
    assert held.value.code == 'E_VALIDATION'           # the default site
    run('inventory-transfer post', dict(date='2017-03-02', from_site='Warehouse', to_site='Van 1',
                                        lines=[dict(item='Widget', quantity='1')]), reason='Load')
    with pytest.raises(BookflowError) as busy:
        run('inventory-site deactivate', dict(site='Van 1'), reason='Sold the van')
    assert busy.value.code == 'E_RECORD_IN_USE' and 'Widget' in busy.value.details['items']
    with pytest.raises(BookflowError) as unknown:
        run('inventory-transfer post', dict(from_site='Truck 9', to_site='Van 1',
                                            lines=[dict(item='Widget', quantity='1')]), reason='Load')
    assert unknown.value.details['sites'] == ['Van 1', 'Warehouse']
    listed = run('inventory-site list', {})['sites']
    assert [(s['name'], s['is_default']) for s in listed] == [('Van 1', False), ('Warehouse', True)]


def test_migration_places_every_existing_movement_at_main(books):
    """co0089 on a populated company: one placement per quantity-moving movement, at Main."""
    from tests.test_negative_stock_migration import _at
    widget = _part(books)
    run = books['run']
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01',
                          items=[dict(item=widget, quantity='8', unit_cost='2.00')]), reason='In')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-02',
                                    lines=[dict(item=widget, quantity='3')]), reason='Out')
    run('invoice void', dict(invoice=sale['id'], expected_version=sale['version']), reason='Undo')
    path = _db(books)
    copy = path.parent / 'rewound.db'
    with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        # Back to co0087: the new objects gone, the three columns stripped from the schema text.
        conn.execute('PRAGMA foreign_keys=OFF')
        for (name,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger' AND sql LIKE '%placement%' "
                                    "OR name LIKE 'inventory_transfer%' AND type='trigger' "
                                    "OR name='inventory_sites_no_delete'").fetchall():
            conn.execute(f'DROP TRIGGER IF EXISTS {name}')
        for table in ('inventory_placements', 'inventory_transfer_lines', 'inventory_transfer_revisions',
                      'inventory_transfers', 'item_site_settings', 'serial_numbers', 'lots', 'inventory_bins',
                      'inventory_sites'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute('PRAGMA writable_schema=ON')
        conn.execute("UPDATE sqlite_schema SET sql = replace(sql, ', site_id VARCHAR(26) REFERENCES inventory_sites (id)', '') "
                     "WHERE name IN ('work_lines', 'purchase_order_lines', 'assembly_builds')")
        conn.execute('PRAGMA writable_schema=OFF')
        conn.execute("UPDATE alembic_version SET version_num='co0087'")
        conn.commit()
    _at(copy, 'co0089')
    with sqlite3.connect(copy) as conn:
        sites = conn.execute('SELECT name, is_default FROM inventory_sites').fetchall()
        assert sites == [('Main', 1)]
        moving = conn.execute('SELECT id, quantity_microunits FROM inventory_movements '
                              'WHERE quantity_microunits != 0 ORDER BY id').fetchall()
        placed = conn.execute('SELECT movement_id, quantity_microunits FROM inventory_placements ORDER BY movement_id').fetchall()
        assert placed == moving and len(placed) == 3       # receipt, issue and its reversal
        assert conn.execute('SELECT count(*) FROM inventory_placements WHERE reverses_placement_id IS NOT NULL').fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name='inventory_movements_placed'").fetchone() == (1,)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
