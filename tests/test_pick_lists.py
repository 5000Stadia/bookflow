"""R181 increment 3: pick lists (design/pick-pack-ship.md §4, §10).

Picking is an optional layer on shipping (K3): off by default, when every `pick-list` command refuses and `shipment
post` ships from the order alone. On, a list for a wave of orders runs in bin order with the earliest-expiring lot
suggested; picks append (two pickers both land); a short pick ships short; a second list never re-picks units on the
first; with a staging bin, picked units move to staging and a count finds them there, and without one a count names
them. Picks post no money.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


def _item(books, name, tracking='none', barcode=None, cost='10.00'):
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part', description=name, price='30.00',
        purchase_description=name, cost=cost, cogs_account_id=books['cogs'], income_account_id=books['income'],
        tracking=tracking, **({'barcode': barcode} if barcode else {}))['id']


@pytest.fixture
def floor(books):
    """A warehouse with bins A1, A2, A3 and A10; a widget in A1 (its default bin), a lot-tracked sealant in A2 in two
    lots, serial water heaters in A3; two open orders needing all three."""
    run = books['run']
    run('inventory-site update', dict(site='Main', name='Warehouse', tracks_bins=True), reason='Our warehouse')
    for name in ('A1', 'A2', 'A3', 'A10', 'STAGE'):
        run('bin create', dict(site='Warehouse', name=name, barcode=f'BIN-{name}'), reason='Shelving')
    widget = _item(books, 'Widget', barcode='WIDGET')
    sealant = _item(books, 'Sealant', 'lot', barcode='SEAL')
    heater = _item(books, 'Water Heater', 'serial', barcode='HEATER', cost='100.00')
    run('item update', dict(item='Widget', site_settings=[dict(site='Warehouse', default_bin='A1')]), reason='Home bin')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=widget, quantity='10', unit_cost='4.00', bin='A1'),
        dict(item=widget, quantity='4', unit_cost='4.00', bin='A10'),
        dict(item=sealant, quantity='5', unit_cost='2.00', bin='A2', lot='L-LATE', lot_expiry='2017-12-31'),
        dict(item=sealant, quantity='3', unit_cost='2.00', bin='A2', lot='L-SOON', lot_expiry='2017-06-30'),
        dict(item=heater, quantity='2', unit_cost='100.00', bin='A3', serial_numbers=['WH-1001', 'WH-1002'])]),
        reason='Stock in')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    first = run('sales-order create', dict(customer=acme, date='2017-03-02', lines=[
        dict(item=widget, quantity='3', unit_price='30.00'), dict(item=sealant, quantity='2', unit_price='9.00')]),
        reason='Acme ordered')
    second = run('sales-order create', dict(customer=books['customer'], date='2017-03-03', lines=[
        dict(item=heater, quantity='2', unit_price='300.00'), dict(item=widget, quantity='2', unit_price='30.00')]),
        reason='Adams ordered')
    return dict(books=books, run=run, widget=widget, sealant=sealant, heater=heater, acme=acme,
                first=first['number'], second=second['number'])


def _on(run):
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick now')


def test_off_by_default_every_pick_list_command_refuses_and_shipping_is_unchanged(floor):
    run = floor['run']
    info = run('company show', {})['info']
    assert info['pick_and_pack_enabled'] is False
    error = _error(lambda: run('pick-list create', dict(orders=[floor['first']]), reason='Pick'))
    assert error.code == 'E_FEATURE_DISABLED' and 'company update' in error.message
    assert error.details['setting'] == 'pick_and_pack_enabled'
    assert _error(lambda: run('pick-list query', {})).code == 'E_FEATURE_DISABLED'
    assert _error(lambda: run('shipment post', dict(pick_list='P-1'), reason='x')).code == 'E_FEATURE_DISABLED'
    shipped = run('shipment post', dict(sales_order=floor['first'], date='2017-03-05'), reason='Truck')
    assert shipped['status'] == 'shipped'


def test_a_wave_of_two_orders_is_one_list_in_bin_order_with_the_earliest_lot(floor):
    run = floor['run']
    _on(run)
    preview = run('pick-list create', dict(orders=[floor['first'], floor['second']], date='2017-03-05'),
                  reason='Morning wave', dry_run=True)
    assert preview['status'] == 'will_create'
    made = run('pick-list create', dict(orders=[floor['first'], floor['second']], date='2017-03-05', picker='Sam'),
               reason='Morning wave')
    assert made['number'] == 'P-1' and made['status'] == 'open' and made['picker'] == 'Sam'
    rows = [(line['bin'], line['item_name'], line['sales_order'], line['quantity'], line['lot']) for line in made['lines']]
    assert rows == [('A1', 'Widget', floor['first'], '3', None), ('A1', 'Widget', floor['second'], '2', None),
                    ('A2', 'Sealant', floor['first'], '2', 'L-SOON'),
                    ('A3', 'Water Heater', floor['second'], '2', None)]
    heater = made['lines'][3]
    assert heater['serial_hints'] == ['WH-1001', 'WH-1002']
    assert {order['sales_order'] for order in made['orders']} == {floor['first'], floor['second']}
    # Picks post no money.
    with sqlite3.connect(_db(floor['books'])) as conn:
        assert conn.execute("SELECT count(*) FROM transactions WHERE type = 'journal_entry'").fetchone()[0] == 0


def test_scanning_the_bin_then_the_item_three_times_picks_three_and_a_short_line_ships_short(floor):
    run = floor['run']
    _on(run)
    made = run('pick-list create', dict(orders=[floor['first'], floor['second']], date='2017-03-05'), reason='Wave')
    number = made['number']
    for _ in range(3):
        out = run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(code='WIDGET', bin='A1')]),
                  reason='Scan')
    line = next(l for l in out['lines'] if l['sales_order'] == floor['first'] and l['item_name'] == 'Widget')
    assert line['picked'] == '3' and line['to_pick'] == '0'
    # The next widget scan fills the second order's line; one only: picked short.
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(code='WIDGET', bin='A1')]),
        reason='Scan')
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(code='SEAL', quantity='2'), dict(code='WH-1001'), dict(code='WH-1002')]), reason='Scan')
    shown = run('pick-list show', dict(pick_list=number))
    sealant = next(l for l in shown['lines'] if l['item_name'] == 'Sealant')
    heater = next(l for l in shown['lines'] if l['item_name'] == 'Water Heater')
    assert sealant['picked'] == '2' and heater['picked_serial_numbers'] == ['WH-1001', 'WH-1002']
    closed = run('pick-list close', dict(pick_list=number), reason='Done picking')
    assert closed['status'] == 'done' and any('picked 1 of 2' in w for w in closed['warnings'])
    shipped = run('shipment post', dict(pick_list=number, sales_order=floor['second'], date='2017-03-05'),
                  reason='Truck')
    quantities = {(l['item_name'], l['quantity']) for l in shipped['lines']}
    assert quantities == {('Water Heater', '2'), ('Widget', '1')}
    heater_line = next(l for l in shipped['lines'] if l['item_name'] == 'Water Heater')
    assert sorted(heater_line['serial_numbers']) == ['WH-1001', 'WH-1002']
    order = run('sales-order show', dict(sales_order=floor['second']))
    widget = next(l for l in order['revision']['lines'] if l['facts']['profile']['item']['label'] == 'Widget')
    assert widget['order']['to_ship'] == '1'
    # Shipping the same order from the list again is refused, naming the shipment.
    again = _error(lambda: run('shipment post', dict(pick_list=number, sales_order=floor['second']), reason='Again'))
    assert again.code == 'E_WORK_DEPENDENCY' and shipped['number'] in again.message
    first = run('shipment post', dict(pick_list=number, date='2017-03-05'), reason='Truck')
    assert {(l['item_name'], l['quantity'], l.get('lot')) for l in first['lines']} == {
        ('Widget', '3', None), ('Sealant', '2', 'L-SOON')}
    assert run('pick-list show', dict(pick_list=number))['next_commands'] == []


def test_two_pickers_both_land_and_a_second_list_never_repicks(floor):
    run = floor['run']
    _on(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    client = floor['books']['client']
    for key in ('picker-a', 'picker-b'):
        client.run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(item='Widget')]),
                   company=floor['books']['company'], reason='Scan', idempotency_key=key)
    assert next(l for l in run('pick-list show', dict(pick_list=number))['lines']
                if l['item_name'] == 'Widget')['picked'] == '2'
    again = _error(lambda: run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Again'))
    assert again.code == 'E_VALIDATION' and number in again.message
    # Ready picks only what no list holds: the second order.
    ready = run('pick-list create', dict(ready=True, date='2017-03-05'), reason='Everything else')
    assert {line['sales_order'] for line in ready['lines']} == {floor['second']}
    # Competition for the last widgets: 14 on hand, 3 held by P-1, 2 for the second order -- all there.
    assert sum(float(l['quantity']) for l in ready['lines'] if l['item_name'] == 'Widget') == 2
    # Turning picking off is refused while a list is open, naming it.
    off = _error(lambda: run('company update', dict(pick_and_pack_enabled=False), reason='Off'))
    assert off.code == 'E_WORK_DEPENDENCY' and number in off.message
    run('pick-list cancel', dict(pick_list=number), reason='Not today')
    run('pick-list cancel', dict(pick_list=ready['number']), reason='Not today')
    run('company update', dict(pick_and_pack_enabled=False), reason='Off')


def test_natural_bin_order_and_competition_for_the_last_units(floor):
    run = floor['run']
    _on(run)
    run('item update', dict(item='Widget', site_settings=[dict(site='Warehouse', default_bin=None)]),
        reason='No home')
    acme = floor['acme']
    big = run('sales-order create', dict(customer=acme, date='2017-03-01', lines=[
        dict(item=floor['widget'], quantity='12', unit_price='30.00')]), reason='Big order')['number']
    made = run('pick-list create', dict(orders=[floor['first'], big], date='2017-03-05'), reason='Wave')
    widget = [(l['sales_order'], l['bin'], l['quantity'], l['short']) for l in made['lines'] if l['item_name'] == 'Widget']
    # 14 on hand, 2 committed to the second order (not on the list): the older big order takes the 12 left, A1
    # before A10, and the first order is short its 3 (no bin).
    assert sorted(widget, key=str) == sorted([(big, 'A1', '10', False), (big, 'A10', '2', False),
                                              (floor['first'], None, '3', True)], key=str)
    bins = [l['bin'] for l in made['lines']]
    assert bins == ['A1', 'A2', 'A10', None]
    assert any('short 3' in note for note in made['notes'])


def test_a_staging_bin_moves_picks_and_a_count_finds_them_there(floor):
    run = floor['run']
    _on(run)
    run('inventory-site update', dict(site='Warehouse', staging_bin='STAGE'), reason='Staging area')
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(item='Widget', quantity='3'), dict(item='Sealant', quantity='2', lot='L-SOON')]), reason='Picked')
    with sqlite3.connect(_db(floor['books'])) as conn:
        picks = conn.execute("SELECT item_id, lot_id, serial_number_id, sum(quantity_microunits) FROM "
                             "inventory_placements WHERE source = 'pick' GROUP BY 1, 2, 3").fetchall()
    assert picks and all(row[3] == 0 for row in picks)
    count = run('inventory-count create', dict(site='Warehouse', date='2017-03-06', bins=['A1', 'STAGE']),
                reason='Count A1')
    held = {(l['item_name'], l['bin']): l['expected'] for l in count['lines']}
    assert held[('Widget', 'A1')] == '7' and held[('Widget', 'STAGE')] == '3'
    run('inventory-count cancel', dict(count=count['number'], expected_version=1), reason='Just looking')
    # Setting the line lower puts one back; cancelling moves the rest back.
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(item='Widget', quantity='2', mode='set')]), reason='One back')
    shipped = run('shipment post', dict(pick_list=number, date='2017-03-06'), reason='Truck', dry_run=True)
    assert {(l['item_name'], l['quantity'], l['bin']) for l in shipped['lines']} == {
        ('Widget', '2', 'STAGE'), ('Sealant', '2', 'STAGE')}
    run('pick-list cancel', dict(pick_list=number, date='2017-03-06'), reason='Not today')
    count = run('inventory-count create', dict(site='Warehouse', date='2017-03-07', bins=['A1', 'STAGE']),
                reason='Count A1')
    held = {(l['item_name'], l['bin']): l['expected'] for l in count['lines']}
    assert held[('Widget', 'A1')] == '10' and held.get(('Widget', 'STAGE'), '0') == '0'


def test_without_staging_a_count_lists_picked_units(floor):
    run = floor['run']
    _on(run)
    number = run('pick-list create', dict(orders=[floor['first']], date='2017-03-05'), reason='Wave')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(item='Widget', quantity='3')]),
        reason='Picked')
    count = run('inventory-count create', dict(site='Warehouse', date='2017-03-06', bins=['A1']), reason='Count A1')
    assert f'3 Widget picked for sales order {floor["first"]}, list {number}, not shipped (bin A1).' \
        in count['warnings']
    assert next(l for l in count['lines'] if l['item_name'] == 'Widget')['expected'] == '10'


def test_serial_warnings_and_refusals(floor):
    run = floor['run']
    _on(run)
    number = run('pick-list create', dict(orders=[floor['second']], date='2017-03-05'), reason='Wave')['number']
    out = run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(item='Water Heater', serial_number='WH-1001', bin='A1')]), reason='Picked')
    assert any('WH-1001' in w and 'A3' in w for w in out['warnings'])
    unknown = _error(lambda: run('pick-list enter', dict(pick_list=number, entries=[dict(code='NOPE')]), reason='x'))
    assert unknown.code == 'E_VALIDATION'
    off_list = _error(lambda: run('pick-list enter', dict(pick_list=number, entries=[dict(code='SEAL')]), reason='x'))
    assert off_list.code == 'E_VALIDATION' and 'not on pick list' in off_list.message


def test_co0104_ddl_is_the_metadata():
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from bookflow.company import schema as c
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0104_pick_lists')
    assert m.DDL == tuple(str(CreateTable(c.metadata.tables[t]).compile(dialect=dialect()))
                          for t in reversed(m.TABLES))
    from bookflow.company import pick_list_schema
    assert m.GUARDS == pick_list_schema.GUARDS
