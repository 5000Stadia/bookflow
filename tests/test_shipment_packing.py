"""R181 increment 4: packing and shipping facts (design/pick-pack-ship.md §5, §6, §10).

Packing -- draft shipments and boxes -- is an optional layer under the company switch (K3): off, its commands refuse
and `shipment post` works as before. Freight to bill, tracking numbers, invoice on ship and the packing slip are
shipping facts, there with the switch off. The done-when: "WH-1001 went in box 2" moves exactly that heater; a box's
weight is its contents' weight, or the measured one; packing a shipped shipment reposts nothing; the packing slip
prints without prices, with backorders and serial numbers; `shipment post --invoice` writes both documents in one
transaction and a retry writes neither again.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_inventory_sites import _db
from tests.test_pick_lists import _item, floor  # noqa: F401


def _error(call):
    with pytest.raises(BookflowError) as caught:
        call()
    return caught.value


@pytest.fixture
def dock(books):
    """Serial water heaters WH-1001 and WH-1002 (40 lb each), a widget (2.5 lb), a sealant with no weight; an order
    for both heaters, 3 widgets and 2 sealants, and a Shipping item."""
    run = books['run']
    widget = _item(books, 'Widget', barcode='WIDGET')
    heater = _item(books, 'Water Heater', 'serial', barcode='HEATER', cost='100.00')
    sealant = _item(books, 'Sealant')
    run('item update', dict(item=widget, unit_weight='2.5'), reason='Weighed')
    run('item update', dict(item=heater, unit_weight='40'), reason='Weighed')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[
        dict(item=widget, quantity='10', unit_cost='4.00'), dict(item=sealant, quantity='5', unit_cost='2.00'),
        dict(item=heater, quantity='2', unit_cost='100.00', serial_numbers=['WH-1001', 'WH-1002'])]),
        reason='Stock in')
    shipping = books['client'].item.create(company=books['company'], name='Shipping', type='other_charge',
                                           description='Shipping', price='0', sales_enabled=True,
                                           purchase_enabled=False, income_account_id=books['income'])['id']
    order = run('sales-order create', dict(customer=books['customer'], date='2017-03-02', lines=[
        dict(item=heater, quantity='2', unit_price='300.00'), dict(item=widget, quantity='5', unit_price='30.00'),
        dict(item=sealant, quantity='2', unit_price='9.00')]), reason='Adams ordered')
    lines = {row['facts']['item_id']: row['line_id'] for row in order['revision']['lines']}
    return dict(books=books, run=run, widget=widget, heater=heater, sealant=sealant, shipping=shipping,
                order=order['number'], lines=lines)


def _on(run):
    run('company update', dict(pick_and_pack_enabled=True), reason='We pack now')


def _ship(dock, **extra):
    d = dock
    body = dict(sales_order=d['order'], date='2017-03-20', lines=[
        dict(order_line=d['lines'][d['heater']], quantity='2', serial_numbers=['WH-1001', 'WH-1002']),
        dict(order_line=d['lines'][d['widget']], quantity='3'), dict(order_line=d['lines'][d['sealant']], quantity='2')])
    body.update(extra)
    return d['run']('shipment post', body, reason='Truck')


def _entry(path, transaction_id):
    with sqlite3.connect(path) as conn:
        return (conn.execute('SELECT version FROM transactions WHERE id = ?', (transaction_id,)).fetchone(),
                conn.execute('SELECT count(*) FROM posting_batches WHERE transaction_id = ?', (transaction_id,)).fetchone(),
                conn.execute('SELECT count(*) FROM inventory_movements').fetchone(),
                conn.execute('SELECT count(*) FROM inventory_placements').fetchone())


def test_off_by_default_packing_refuses_and_shipping_facts_still_work(dock):
    run = dock['run']
    assert run('company show', {})['info']['pick_and_pack_enabled'] is False
    assert run('company show', {})['info']['weight_unit'] == 'lb'
    for name, body in (('shipment create', dict(sales_order=dock['order'])),
                       ('shipment pack', dict(shipment='1001', unit='WH-1001', box=2)),
                       ('shipment cancel', dict(shipment='1001'))):
        error = _error(lambda: run(name, body, reason='Pack'))
        assert error.code == 'E_FEATURE_DISABLED' and 'company update' in error.message, name
    error = _error(lambda: _ship(dock, packages=[dict(contents=[dict(line='1')])]))
    assert error.code == 'E_FEATURE_DISABLED'
    # Tracking, freight to bill and invoice on ship are shipping facts: there with the switch off.
    shipped = _ship(dock, tracking_numbers=['1Z999'], freight_amount='12.50', freight_item=dock['shipping'],
                    invoice=True)
    assert shipped['status'] == 'shipped' and shipped['tracking_numbers'] == ['1Z999']
    assert shipped['packing'] is None and shipped['freight']['amount']['amount'] == '12.50'
    invoice = run('invoice show', dict(invoice=shipped['invoice']['id']))
    assert invoice['revision']['date'] == '2017-03-20'
    assert shipped['invoice']['total']['amount'] == '720.50'   # 600 + 90 + 18 for the goods, 12.50 freight
    assert shipped['freight']['billed_by'] == invoice['number']
    assert run('shipment show', dict(shipment=shipped['number']))['invoices']
    # Turning packing on and off again is allowed with nothing open.
    _on(run)
    run('company update', dict(pick_and_pack_enabled=False), reason='Not yet')


def test_wh_1001_went_in_box_2_moves_exactly_that_heater(dock):
    run = dock['run']
    _on(run)
    draft = run('shipment create', dict(sales_order=dock['order'], lines=[
        dict(order_line=dock['lines'][dock['heater']], quantity='2', serial_numbers=['WH-1001', 'WH-1002']),
        dict(order_line=dock['lines'][dock['widget']], quantity='3')]), reason='Draft')
    assert draft['status'] == 'draft' and draft['packing']['packed'] is False
    assert run('shipment query', {})['rows'][0]['status'] == 'draft'
    packed = run('shipment pack', dict(shipment=draft['number'], expected_version=1, unit='WH-1001', box=2),
                 reason='Heater in box 2')
    (box,) = packed['packing']['packages']
    assert box['box'] == 2 and [(c['serial_numbers'], c['quantity']) for c in box['contents']] == [(['WH-1001'], '1')]
    unboxed = {row['item_name']: row for row in packed['packing']['unboxed']}
    assert unboxed['Water Heater']['serial_numbers'] == ['WH-1002'] and unboxed['Widget']['quantity'] == '3'
    # A scan of the widget's barcode puts one widget in box 1; then the other heater; then all is in.
    run('shipment pack', dict(shipment=draft['number'], unit='WIDGET', quantity='3', box=1), reason='Widgets')
    packed = run('shipment pack', dict(shipment=draft['number'], unit='WH-1002', box=1, weight='95'),
                 reason='Heater two')
    assert packed['packing']['packed'] is True and packed['version'] == 4
    # Moving WH-1001 from box 2 into box 1 moves that heater alone; box 0 unboxes it.
    moved = run('shipment pack', dict(shipment=draft['number'], unit='WH-1001', box=1), reason='Same box')
    assert [p['box'] for p in moved['packing']['packages']] == [1]
    assert sorted(moved['packing']['packages'][0]['contents'][0]['serial_numbers']) == ['WH-1001', 'WH-1002']
    out = run('shipment pack', dict(shipment=draft['number'], unit='WH-1001', box=0), reason='Out again')
    assert out['packing']['unboxed'][0]['serial_numbers'] == ['WH-1001']
    # The draft ships with its boxes; the shipment keeps the draft's number and line identities.
    shipped = run('shipment post', dict(shipment=draft['number'], expected_version=out['version']), reason='Ship')
    assert shipped['number'] == draft['number'] and shipped['status'] == 'shipped'
    assert [l['line_identity'] for l in shipped['lines']] == [l['line_identity'] for l in draft['lines']]
    assert shipped['packing']['packages'][0]['contents'][0]['serial_numbers'] == ['WH-1002']
    assert run('shipment show', dict(shipment=draft['number']))['status'] == 'shipped'
    error = _error(lambda: run('shipment post', dict(shipment=draft['number']), reason='Again'))
    assert error.code == 'E_VALIDATION' and 'already' in error.message
    # On the shipped shipment, WH-1001 into box 2 moves that heater only, and reposts nothing.
    before = _entry(_db(dock['books']), shipped['transaction_id'])
    repacked = run('shipment pack', dict(shipment=shipped['number'], expected_version=shipped['version'],
                                         unit='WH-1001', box=2), reason='Heater in box 2')
    assert repacked['version'] == shipped['version'] + 1
    boxes = {p['box']: p for p in repacked['packing']['packages']}
    assert [c['serial_numbers'] for c in boxes[2]['contents']] == [['WH-1001']]
    assert _entry(_db(dock['books']), shipped['transaction_id']) == before
    assert repacked['cost'] == shipped['cost']


def test_box_weights_come_from_their_contents_or_the_measured_weight(dock):
    run = dock['run']
    _on(run)
    shipped = _ship(dock, packages=[
        dict(package_type='Pallet', tare='12', contents=[dict(line='1', serial_numbers=['WH-1001', 'WH-1002'])]),
        dict(contents=[dict(line='2', quantity='3'), dict(line='3')]),
        dict(box=4, weight='7.75', contents=[])])
    boxes = {p['box']: p for p in shipped['packing']['packages']}
    assert boxes[1]['computed_weight'] == '92' and boxes[1]['weight'] == '92'      # 2 x 40 + tare 12
    assert boxes[2]['computed_weight'] == '7.5' and boxes[2]['unweighed'] == '2'   # 3 x 2.5; sealant has none
    assert [c['weight'] for c in boxes[2]['contents']] == ['7.5', None]
    assert boxes[4]['weight'] == '7.75' and boxes[4]['measured_weight'] == '7.75'
    packing = shipped['packing']
    assert packing['packed'] is True and packing['total_weight'] == '107.25' and packing['unweighed'] == '2'
    assert packing['weight_unit'] == 'lb'
    # A measured weight replaces the computed one in the total; the unit is the company's label.
    run('company update', dict(weight_unit='kg'), reason='Metric')
    weighed = run('shipment pack', dict(shipment=shipped['number'], box=2, weight='8'), reason='Scale')
    assert weighed['packing']['total_weight'] == '107.75' and weighed['packing']['weight_unit'] == 'kg'
    assert weighed['packing']['unweighed'] == '0'
    # Too many units in boxes, or a serial number the line does not ship, is refused.
    error = _error(lambda: run('shipment pack', dict(shipment=shipped['number'], packages=[
        dict(contents=[dict(line='2', quantity='4')])]), reason='Too many'))
    assert error.code == 'E_VALIDATION'
    error = _error(lambda: run('shipment pack', dict(shipment=shipped['number'], packages=[
        dict(contents=[dict(line='1', serial_numbers=['WH-9999'])])]), reason='Wrong one'))
    assert error.code == 'E_VALIDATION'


def test_corrections_carry_the_packing_and_freight_once_billed_is_fixed(dock):
    run = dock['run']
    _on(run)
    shipped = _ship(dock, freight_amount='9.00', freight_item=dock['shipping'],
                    packages=[dict(contents=[dict(line='2', quantity='3')]), dict(contents=[dict(line='3')])])
    # A text change and a repost both keep the boxes; a quantity cut clamps what the box holds.
    text = run('shipment update', dict(shipment=shipped['number'], expected_version=shipped['version'],
                                       tracking_numbers=['1Z1']), reason='Tracking')
    assert text['packing']['packages'] == shipped['packing']['packages']
    cut = run('shipment update', dict(shipment=shipped['number'], expected_version=text['version'], lines=[
        dict(order_line=dock['lines'][dock['heater']], quantity='2', serial_numbers=['WH-1001', 'WH-1002']),
        dict(order_line=dock['lines'][dock['widget']], quantity='1')]), reason='Two stayed')
    boxes = {p['box']: p for p in cut['packing']['packages']}
    assert boxes[1]['contents'][0]['quantity'] == '1' and boxes[2]['contents'] == []
    invoice = run('shipment invoice', dict(shipment=shipped['number'], date='2017-03-27'), reason='Bill')
    assert [line['item_id'] for line in invoice['revision']['lines']].count(dock['shipping']) == 1
    error = _error(lambda: run('shipment update', dict(shipment=shipped['number'], expected_version=cut['version'], freight_amount='20.00', freight_item=dock['shipping']), reason='More freight'))
    assert error.code == 'E_WORK_DEPENDENCY' and 'freight' in error.message
    # Voiding the invoice frees the freight again; a second invoice bills it once more, not twice.
    run('invoice void', dict(invoice=invoice['id']), reason='Wrong')
    assert run('shipment show', dict(shipment=shipped['number']))['freight']['billed_by'] is None
    again = run('shipment invoice', dict(shipment=shipped['number'], date='2017-03-28'), reason='Bill again')
    assert [line['item_id'] for line in again['revision']['lines']].count(dock['shipping']) == 1


def test_drafts_hold_their_units_and_cancel_and_the_switch_waits_for_them(dock):
    run = dock['run']
    _on(run)
    draft = run('shipment create', dict(sales_order=dock['order'], freight_amount='5', freight_item=dock['shipping']),
                reason='Everything')
    assert {l['item_name']: l['quantity'] for l in draft['lines']} == {'Water Heater': '2', 'Widget': '5', 'Sealant': '2'}
    error = _error(lambda: run('shipment post', dict(sales_order=dock['order']), reason='Around it'))
    assert error.code == 'E_VALIDATION'          # the draft holds every unit there is to ship
    error = _error(lambda: run('company update', dict(pick_and_pack_enabled=False), reason='Off'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'draft_shipment_open'
    cancelled = run('shipment cancel', dict(shipment=draft['number'], expected_version=1), reason='Not today')
    assert cancelled['status'] == 'cancelled'
    assert run('shipment query', dict(status='cancelled'))['rows'][0]['number'] == draft['number']
    run('company update', dict(pick_and_pack_enabled=False), reason='Off')
    assert _ship(dock)['number'] != draft['number']


def _pages(pdf):
    return pdf.count(b'/Type /Page') - pdf.count(b'/Type /Pages')


def test_the_packing_slip_prints_without_prices_with_backorders_serials_and_boxes(dock):
    from bookflow.documents import model, render
    run = dock['run']
    read = lambda name, raw, company: run(name, raw)   # noqa: E731
    # Switch off: a slip of an unboxed shipment, every line listed, backorders and serial numbers on it.
    shipped = run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', tracking_numbers=['1Z999'],
                                        lines=[dict(order_line=dock['lines'][dock['heater']], quantity='1',
                                                    serial_numbers=['WH-1002']),
                                               dict(order_line=dock['lines'][dock['widget']], quantity='3')]),
                  reason='Part of it')
    slip = model.build(read, dock['books']['company'], 'packing-slip', {'document': shipped['number']})
    text = ' '.join(' '.join(row) for row in slip.rows)
    assert slip.title == 'Packing Slip' and not slip.totals
    assert 'WH-1002' in text and '300.00' not in text and '30.00' not in text
    assert [row[:6] for row in slip.rows] == [('Water Heater', 'Water Heater', '2', '1', '1', 'WH-1002'),
                                              ('Widget', 'Widget', '5', '3', '2', '')]
    assert ('Tracking', '1Z999') in slip.facts and ('Sales order', dock['order']) in slip.facts
    # Packed: a heading per box with its weight, the units not boxed last; one page per box with per_box.
    _on(run)
    packed = run('shipment pack', dict(shipment=shipped['number'], packages=[
        dict(tracking_number='1Z1', contents=[dict(line='1')]), dict(contents=[dict(line='2', quantity='2')])]),
        reason='Two boxes')
    assert packed['packing']['packed'] is False
    slip = model.build(read, dock['books']['company'], 'packing-slip', {'document': shipped['number']})
    assert [row[0] for row in slip.rows] == ['Box 1 · 40 lb · Tracking 1Z1', 'Water Heater', 'Box 2 · 5 lb', 'Widget',
                                             'Not in a box', 'Widget']
    assert [row[3] for row in slip.rows if row[0] == 'Widget'] == ['2', '1']
    printed = render(read, dock['books']['company'], 'packing-slip', {'document': shipped['number'], 'per_box': True})
    assert printed.filename == f'packing-slip-{shipped["number"]}.pdf' and _pages(printed.content) == 3
    # A long shipment runs over pages, headings repeated, nothing cut.
    many = [dict(box=n, contents=[dict(line='2', quantity='0.1')]) for n in range(1, 31)]
    run('shipment pack', dict(shipment=shipped['number'], packages=many), reason='Small boxes')
    printed = render(read, dock['books']['company'], 'packing-slip', {'document': shipped['number']})
    assert _pages(printed.content) >= 2


def _counts(path):
    with sqlite3.connect(path) as conn:
        return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('shipments', 'shipment_billings', 'transactions', 'inventory_movements'))


def test_invoice_on_ship_writes_both_in_one_transaction_and_a_retry_writes_neither_again(dock, monkeypatch):
    run, path = dock['run'], _db(dock['books'])
    preview = run('shipment post', dict(
        sales_order=dock['order'], date='2017-03-20', invoice=True), reason='Truck', dry_run=True)
    assert preview['status'] == 'will_ship' and any('invoice' in w for w in preview['warnings'])
    before = _counts(path)
    # The invoice refusing takes the shipment back with it: nothing is written.
    from bookflow.commands import shipment_cmds

    class Refusing:
        def plan(self, *args):
            raise BookflowError('E_PERIOD_CLOSED')

        def apply(self, *args):
            raise AssertionError('never reached')

    monkeypatch.setattr(shipment_cmds, '_inner', lambda: Refusing())
    error = _error(lambda: run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', invoice=True),
                               reason='Truck', idempotency_key='ship-1'))
    assert error.code == 'E_PERIOD_CLOSED' and _counts(path) == before
    monkeypatch.undo()
    first = run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', invoice=True), reason='Truck',
                idempotency_key='ship-2')
    after = _counts(path)
    assert first['invoice']['id'] and after[0] == before[0] + 1 and after[1] > before[1]
    again = run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', invoice=True), reason='Truck',
                idempotency_key='ship-2')
    assert again['number'] == first['number'] and again['invoice'] == first['invoice'] and _counts(path) == after
    order = run('sales-order show', dict(sales_order=dock['order']))
    assert all(line['order']['invoiced'] == line['order']['ordered'] for line in order['revision']['lines'])
    # Invoicing the shipment again finds nothing left to bill.
    error = _error(lambda: run('shipment invoice', dict(shipment=first['number']), reason='Twice'))
    assert error.code in ('E_VALIDATION', 'E_WORK_DEPENDENCY')


def test_a_draft_from_a_pick_list_ships_what_was_picked_and_goes_stale_when_picks_change(floor):
    run = floor['run']
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick and pack')
    number = run('pick-list create', dict(orders=[floor['second']], date='2017-03-05'), reason='Pick')['number']
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[
        dict(code='WH-1001'), dict(code='WIDGET', bin='A1')]), reason='Scan')
    draft = run('shipment create', dict(pick_list=number), reason='Pack it')
    assert {(l['item_name'], l['quantity']) for l in draft['lines']} == {('Water Heater', '1'), ('Widget', '1')}
    packed = run('shipment pack', dict(shipment=draft['number'], unit='WH-1001', box=1), reason='Heater boxed')
    # A pick after the draft makes it stale: posting it is refused; a fresh draft ships the new picks.
    run('pick-list enter', dict(pick_list=number, date='2017-03-05', entries=[dict(code='WH-1002')]), reason='Scan')
    error = _error(lambda: run('shipment post', dict(shipment=draft['number']), reason='Ship'))
    assert error.code == 'E_VALIDATION' and error.details['reason'] == 'draft_stale'
    run('shipment cancel', dict(shipment=draft['number'], expected_version=packed['version']), reason='Stale')
    fresh = run('shipment create', dict(pick_list=number), reason='Again')
    shipped = run('shipment post', dict(shipment=fresh['number'], date='2017-03-05'), reason='Truck')
    heater = next(l for l in shipped['lines'] if l['item_name'] == 'Water Heater')
    assert sorted(heater['serial_numbers']) == ['WH-1001', 'WH-1002']
    shown = run('pick-list show', dict(pick_list=number))
    assert [o['shipments'] for o in shown['orders']] == [[shipped['number']]]


def test_co0105_ddl_is_the_metadata():
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateTable
    from bookflow.company import schema as c, shipment_packing_schema
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0105_shipment_packing')
    assert m.DDL == tuple(str(CreateTable(c.metadata.tables[t]).compile(dialect=dialect()))
                          for t in reversed(m.TABLES))
    assert m.GUARDS == shipment_packing_schema.GUARDS


def _order_invoice(dock, key, **extra):
    run = dock['run']
    version = run('sales-order show', dict(sales_order=dock['order']))['version']
    return run('sales-order invoice', dict(sales_order=dock['order'], conversion_key=key, date='2017-03-27',
                                           expected_version=version, **extra), reason='Bill the order')


def test_freight_rides_on_whichever_invoice_bills_the_units_and_is_fixed_after(dock):
    """Review medium 1: `sales-order invoice` billing a shipment's units carries its freight once, as `shipment
    invoice` does; after, the freight is fixed and `shipment invoice` says there is nothing left."""
    run = dock['run']
    first = run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', freight_amount='9.00',
                                      freight_item=dock['shipping'], lines=[
        dict(order_line=dock['lines'][dock['widget']], quantity='3')]), reason='Truck')
    invoice = _order_invoice(dock, 'k1')
    freight = [l for l in invoice['revision']['lines'] if l['item_id'] == dock['shipping']]
    assert len(freight) == 1 and freight[0]['net_minor_units'] == 900
    shown = run('shipment show', dict(shipment=first['number']))
    assert shown['freight']['billed_by'] == invoice['number'] and shown['freight']['to_bill'] is False
    error = _error(lambda: run('shipment update', dict(shipment=first['number'], expected_version=shown['version'],
                                                       freight_amount='20.00', freight_item=dock['shipping']),
                               reason='More'))
    assert error.code == 'E_WORK_DEPENDENCY' and 'freight' in error.message
    error = _error(lambda: run('shipment invoice', dict(shipment=first['number'], date='2017-03-28'), reason='Again'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'nothing_to_invoice'
    assert invoice['number'] in error.message
    # Freight added after every unit was billed has no invoice left to ride on: refused, not left "to bill".
    second = run('shipment post', dict(sales_order=dock['order'], date='2017-03-21', lines=[
        dict(order_line=dock['lines'][dock['widget']], quantity='2')]), reason='Truck')
    _order_invoice(dock, 'k2', shipments=[second['number']])
    error = _error(lambda: run('shipment update', dict(shipment=second['number'], expected_version=second['version'],
                                                       freight_amount='4.00', freight_item=dock['shipping']),
                               reason='Forgot'))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'units_invoiced'


def test_a_live_draft_keeps_its_units_from_the_order_invoice_and_from_a_correction(dock):
    """Review medium 3: units a draft holds are refused to an order invoice that issues stock and to a shipment
    correction shipping more, naming the draft. Low 5: the draft's memo is the shipment journal's memo."""
    run = dock['run']
    _on(run)
    shipped = run('shipment post', dict(sales_order=dock['order'], date='2017-03-20', lines=[
        dict(order_line=dock['lines'][dock['widget']], quantity='3')]), reason='Truck')
    draft = run('shipment create', dict(sales_order=dock['order'], memo='Dock 4', lines=[
        dict(order_line=dock['lines'][dock['heater']], quantity='1', serial_numbers=['WH-1001']),
        dict(order_line=dock['lines'][dock['widget']], quantity='2')]), reason='Pack it')
    error = _error(lambda: _order_invoice(dock, 'k1', selections=[
        dict(line_id=dock['lines'][dock['heater']], quantity='1')]))
    assert error.code == 'E_WORK_DEPENDENCY' and error.details['reason'] == 'held_by_draft'
    assert draft['number'] in error.message and 'post' in error.message
    error = _error(lambda: run('shipment update', dict(shipment=shipped['number'], expected_version=shipped['version'],
                                                       lines=[dict(order_line=dock['lines'][dock['widget']],
                                                                   quantity='5')]), reason='All five'))
    assert error.code == 'E_WORK_DEPENDENCY' and draft['number'] in error.message
    posted = run('shipment post', dict(shipment=draft['number'], date='2017-03-22'), reason='Ship')
    with sqlite3.connect(_db(dock['books'])) as conn:
        memo = conn.execute('SELECT r.memo FROM transactions t JOIN transaction_revisions r '
                            'ON r.id = t.current_revision_id WHERE t.id = ?', (posted['transaction_id'],)).fetchone()
    assert memo == ('Dock 4',)


def test_pick_list_suggestions_leave_out_what_a_draft_holds(floor):
    """Review medium 2: a draft holding the whole order leaves nothing for a list to plan, and the refusal says so."""
    run = floor['run']
    run('company update', dict(pick_and_pack_enabled=True), reason='We pick and pack')
    draft = run('shipment create', dict(sales_order=floor['second']), reason='Pack it')
    error = _error(lambda: run('pick-list create', dict(orders=[floor['second']], date='2017-03-05'), reason='Pick'))
    assert error.code == 'E_VALIDATION' and error.details['reason'] == 'nothing_to_pick'
    assert f'draft shipment {draft["number"]}' in error.message
