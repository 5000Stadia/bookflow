"""R181 increment 5, review fixes: the fulfillment worksheet acts on what it showed.

Through HTTP: a second submit of the same page bills and ships nothing more (the order version is the invoice's
`expected_version`, the page's batch key Ship's idempotency key, and a stale page is refused per order); Ship ships at
the Site filter in effect; an order whose draft is wholly boxed sits on the Packed tab and Ship posts that draft;
packing slips print every live draft; the worksheet pages by the report's cursor; a report that fails after a batch
still shows what the batch made; the date is the company's own day. In the books: a paged cursor goes stale on a change
that posts nothing; stock carried at no value cannot ship now; the amounts are the invoice's own cents; and a seeded
run with ample stock finds can ship now equal to what `shipment post` ships at every step.
"""
import html
import os
import random
import re
import socket
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from bookflow import BookflowError
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_fulfillment_worksheet import can_ship
from tests.test_pick_lists import _item

PASSWORD = 'correct-horse-battery'
HEADERS = {'X-Bookflow-Workbench': '1', 'X-Bookflow-Client-Name': 'bookflow-workbench'}
HX = {**HEADERS, 'HX-Request': 'true'}


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def bench(books):
    """The books company served, signed in: a warehouse and an annex, widgets and gadgets, one customer."""
    run = books['run']  # before the host takes the data root
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='Our warehouse')
    run('inventory-site create', dict(name='Annex'), reason='Second shed')
    widget, gadget = _item(books, 'Widget'), _item(books, 'Gadget', cost='4.00')
    books['client'].run('user set-password', {'username': os_login(), 'password': PASSWORD})
    handle = start_serving(os.environ['BOOKFLOW_DATA_ROOT'], client_version(), bind=f'127.0.0.1:{_free_port()}',
                           secure_cookies=False)
    try:
        browser = TestClient(handle.app)
        assert browser.post('/login', json={'username': os_login(), 'password': PASSWORD}).status_code == 200

        def call(name, raw, reason=None):
            # The host holds the data root: commands go through its HTTP surface, as the workbench's own do.
            from bookflow.core import registry
            extra = {'X-Bookflow-Reason': reason or 'Worksheet fixes'} if registry.get(name).is_write else {}
            answered = browser.post(f"/companies/{books['company']}/commands/{name.replace(' ', '.')}", json=raw,
                                    headers={**HEADERS, **extra})
            body = answered.json()
            if answered.status_code != 200:
                error = body.get('error', body)
                raise BookflowError(error.get('code', 'E_UNKNOWN'), message=error.get('message'))
            return body
        yield dict(books=books, run=call, widget=widget, gadget=gadget, browser=browser,
                   base=f"/c/{books['company']}/inventory/fulfillment")
    finally:
        handle.stop()


def _stock(b, item, quantity, site=None, date='2017-03-01', cost='4.00'):
    b['run']('bill post', dict(vendor=b['books']['vendor'], date=date, items=[
        dict(item=item, quantity=str(quantity), unit_cost=cost, **({'site': site} if site else {}))]), reason='Stock in')


def _order(b, *lines, date='2017-03-02'):
    return b['run']('sales-order create', dict(customer=b['books']['customer'], date=date, lines=[
        dict(item=item, quantity=str(quantity), unit_price='30.00', **extra) for item, quantity, *rest in lines
        for extra in [rest[0] if rest else {}]]), reason='Ordered')


def _page(b, **query):
    answered = b['browser'].get(b['base'], params=dict(dict(tab='all'), **query), headers=HEADERS)
    assert answered.status_code == 200, answered.text[:800]
    return answered.text


def _form(page):
    """The hidden fields the batch form carries, as the browser would send them."""
    form = page[page.index('data-worksheet-batch'):]
    return {html.unescape(name): html.unescape(value)
            for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form)}


def _submit(b, page, action, *orders, **extra):
    data = dict(_form(page), action=action, order=list(orders), date='2017-03-10', **{'ctx:reason': 'Batch'})
    data.update(extra)
    answered = b['browser'].post(b['base'], data=data, headers=HX)
    assert answered.status_code == 200, answered.text[:800]
    return answered.text


def _results(page):
    return {html.unescape(order): (cls == 'ok', re.sub('<[^>]+>', '', html.unescape(text)))
            for cls, order, text in re.findall(
                r'<li class="(ok|refused)" data-batch-result="([^"]+)"><strong>[^<]*</strong>: (.*?)</li>', page)}


def _line(run, number):
    return run('sales-order show', dict(sales_order=number))['revision']['lines'][0]['order']


def test_a_second_submit_of_the_same_page_bills_and_ships_nothing_more(bench):
    b, run = bench, bench['run']
    _stock(b, b['widget'], 100)
    _stock(b, b['gadget'], 3)
    billed = _order(b, (b['widget'], 10))
    run('shipment post', dict(sales_order=billed['number'], date='2017-03-03', lines=[
        dict(order_line=billed['revision']['lines'][0]['line_id'], quantity='4')]), reason='Shipped 4')
    short = _order(b, (b['gadget'], 6))
    page = _page(b)

    first = _results(_submit(b, page, 'invoice', billed['number']))
    assert first[billed['number']][0], first
    again = _results(_submit(b, page, 'invoice', billed['number']))
    assert not again[billed['number']][0] and 'version' in again[billed['number']][1].lower(), again
    assert _line(run, billed['number'])['invoiced'] == '4'  # the 6 unshipped units were never billed

    shipped = _results(_submit(b, page, 'ship', short['number']))
    assert shipped[short['number']][0] and ' 3 Gadget' in shipped[short['number']][1], shipped
    _stock(b, b['gadget'], 3, date='2017-03-05')
    replay = _results(_submit(b, page, 'ship', short['number']))
    assert replay[short['number']] == shipped[short['number']], replay  # the same shipment, not three more
    assert _line(run, short['number'])['shipped'] == '3'
    # The invoice moved the billed order on: this page's Ship is refused for it, not run on what the page never showed.
    stale = _results(_submit(b, page, 'ship', billed['number']))
    assert not stale[billed['number']][0] and 'changed since this page was read' in stale[billed['number']][1]
    assert _form(page)[f"version:{billed['number']}"] == str(billed['version'])
    assert page.count('hx-disabled-elt="[data-batch-actions] button"') == 2  # Ship and Create invoices, a courtesy


def test_batch_ship_ships_at_the_site_filter_what_the_row_showed(bench):
    """Widgets in both sheds, gadgets in the annex; the order's gadget line names the annex, so on its own the order
    ships widgets and gadgets from there. Filtered to the warehouse, the row shows widgets only, and Ship ships that."""
    b, run = bench, bench['run']
    _stock(b, b['widget'], 10, site='Warehouse')
    _stock(b, b['widget'], 10, site='Annex')
    _stock(b, b['gadget'], 10, site='Annex')
    order = _order(b, (b['widget'], 4), (b['gadget'], 2, {'site': 'Annex'}))
    widget_line = order['revision']['lines'][0]['line_id']
    own = run('report order-fulfillment', dict(limit=200))['rows'][0]
    assert own['site_name'] == 'Annex' and len(can_ship(own)) == 2
    row = run('report order-fulfillment', dict(site='Warehouse', limit=200))['rows'][0]
    assert can_ship(row) == {widget_line: Fraction(4)}
    done = _results(_submit(b, _page(b, site='Warehouse'), 'ship', order['number']))
    assert done[order['number']][0] and 'Gadget' not in done[order['number']][1], done
    shipment = run('shipment query', dict(sales_order=order['number']))['rows'][0]
    shown = run('shipment show', dict(shipment=shipment['number']))
    assert shown['site_name'] == 'Warehouse' and {line['order_line_id'] for line in shown['lines']} == {widget_line}


def test_packed_orders_ship_their_draft_and_every_draft_prints(bench):
    b, run = bench, bench['run']
    run('company update', dict(pick_and_pack_enabled=True), reason='Packing on')
    _stock(b, b['widget'], 20)
    order = _order(b, (b['widget'], 5))
    line = order['revision']['lines'][0]['line_id']
    first = run('shipment create', dict(sales_order=order['number'], date='2017-03-03', lines=[
        dict(order_line=line, quantity='2')]), reason='Draft one')
    second = run('shipment create', dict(sales_order=order['number'], date='2017-03-03', lines=[
        dict(order_line=line, quantity='3')]), reason='Draft two')
    slips = b['browser'].get(f"{b['base']}/packing-slips", params={'order': order['number']}, headers=HEADERS)
    assert slips.status_code == 200 and slips.content.startswith(b'%PDF')
    assert len(re.findall(rb'/Type\s*/Page[^s]', slips.content)) == 2  # a slip per draft
    run('shipment pack', dict(shipment=first['number'], line='1', quantity='2', box=1), reason='Boxed')
    page = _page(b, tab='packed')
    assert 'data-tab="packed"' in page and f'data-order="{order["number"]}"' in page
    assert _form(page)[f"packed:{order['number']}"] == first['number']
    done = _results(_submit(b, page, 'ship', order['number']))
    assert done[order['number']][0] and first['number'] in done[order['number']][1], done
    assert run('shipment show', dict(shipment=first['number']))['status'] == 'shipped'
    assert run('shipment show', dict(shipment=second['number']))['status'] == 'draft'


def test_the_worksheet_pages_through_every_order(bench, monkeypatch):
    from bookflow.adapters.workbench import fulfillment_pages
    b = bench
    _stock(b, b['widget'], 50)
    numbers = [_order(b, (b['widget'], 2), date=f'2017-03-0{day}')['number'] for day in (2, 3, 4)]
    monkeypatch.setattr(fulfillment_pages, 'PAGE_SIZE', 2)
    page = _page(b)
    assert re.findall(r'data-order="([^"]+)"', page) == numbers[:2] and '3 orders (2 on this page)' in page
    more = html.unescape(re.search(r'href="([^"]+)" data-worksheet-more', page).group(1))
    rest = b['browser'].get(more, headers=HEADERS).text
    assert re.findall(r'data-order="([^"]+)"', rest) == numbers[2:] and 'data-worksheet-first' in rest
    assert 'data-worksheet-more' not in rest
    _order(b, (b['widget'], 1), date='2017-03-01')  # posts nothing, yet the old cursor is stale
    again = b['browser'].get(more, headers=HEADERS).text
    assert 'data-worksheet-notice' in again and len(re.findall(r'data-order="', again)) == 2


def test_a_report_that_fails_after_a_batch_still_shows_what_it_made(bench, monkeypatch):
    from bookflow.company import fulfillment
    b = bench
    _stock(b, b['widget'], 10)
    order = _order(b, (b['widget'], 2))
    page = _page(b)

    def broken(*args, **kwargs):
        raise BookflowError('E_VALIDATION', message='The worksheet could not be read just now.')
    monkeypatch.setattr(fulfillment, 'worksheet', broken)
    after = _submit(b, page, 'ship', order['number'])
    assert _results(after)[order['number']][0] and 'could not be read just now' in after


def test_the_date_is_the_company_day(bench):
    b = bench
    hour = datetime.now(timezone.utc).hour
    zone, shift = ('Etc/GMT-14', 14) if hour >= 12 else ('Etc/GMT+12', -12)
    b['run']('company update', dict(timezone=zone), reason='Far from UTC')
    _stock(b, b['widget'], 10)
    _order(b, (b['widget'], 2))
    day = (datetime.now(timezone.utc) + timedelta(hours=shift)).date().isoformat()
    assert day != datetime.now(timezone.utc).date().isoformat()
    assert f'name="date" value="{day}"' in _page(b)


# ---------------------------------------------------------------- the report itself


@pytest.fixture
def shop(books):
    run = books['run']
    widget = _item(books, 'Widget')
    return dict(books=books, run=run, widget=widget)


def _sheet(run, **filters):
    return {r['sales_order_number']: r for r in run('report order-fulfillment', dict(limit=200, **filters))['rows']}


def test_a_paged_cursor_goes_stale_on_a_change_that_posts_nothing(shop):
    run, b = shop['run'], shop['books']
    run('bill post', dict(vendor=b['vendor'], date='2017-03-01', items=[
        dict(item=shop['widget'], quantity='100', unit_cost='4')]), reason='in')
    for day in ('2017-03-02', '2017-03-03', '2017-03-04'):
        run('sales-order create', dict(customer=b['customer'], date=day, lines=[
            dict(item=shop['widget'], quantity='3', unit_price='30')]), reason='o')
    first = run('report order-fulfillment', dict(limit=1))
    run('sales-order create', dict(customer=b['customer'], date='2017-03-01', lines=[
        dict(item=shop['widget'], quantity='3', unit_price='30')]), reason='earlier order')
    with pytest.raises(BookflowError) as stale:
        run('report order-fulfillment', dict(limit=1, cursor=first['next_cursor']))
    assert stale.value.code == 'E_QUERY_STALE'


def test_stock_carried_at_no_value_cannot_ship_now(shop):
    run, b = shop['run'], shop['books']
    run('bill post', dict(vendor=b['vendor'], date='2017-03-01', items=[
        dict(item=shop['widget'], quantity='10', unit_cost='0')]), reason='in at nothing')
    number = run('sales-order create', dict(customer=b['customer'], date='2017-03-02', lines=[
        dict(item=shop['widget'], quantity='3', unit_price='30')]), reason='o')['number']
    row = _sheet(run)[number]
    assert can_ship(row) == {} and row['shippable_amount']['amount'] == '0.00'
    assert 'shipment_without_value' in row['note'] and number not in _sheet(run, can_ship=True)
    with pytest.raises(BookflowError) as refused:
        run('shipment post', dict(sales_order=number), reason='ship')
    assert refused.value.code == 'E_VALIDATION'


def test_amounts_are_the_cents_the_next_invoice_bills(shop):
    run, b = shop['run'], shop['books']
    run('bill post', dict(vendor=b['vendor'], date='2017-03-01', items=[
        dict(item=shop['widget'], quantity='100', unit_cost='4')]), reason='in')
    order = run('sales-order create', dict(customer=b['customer'], date='2017-03-02', lines=[
        dict(item=shop['widget'], quantity='3', unit_price='3.3333')]), reason='o')
    number, line = order['number'], order['revision']['lines'][0]['line_id']
    run('shipment post', dict(sales_order=number, date='2017-03-03', lines=[dict(order_line=line, quantity='1')]),
        reason='s')
    row = _sheet(run)[number]
    assert (row['shipped_not_invoiced_amount']['amount'], row['to_ship_amount']['amount']) == ('3.33', '6.67')
    run('sales-order invoice', dict(sales_order=number, expected_version=order['version'], conversion_key='a',
                                    date='2017-03-04'), reason='i')
    run('shipment post', dict(sales_order=number, date='2017-03-05', lines=[dict(order_line=line, quantity='1')]),
        reason='s')
    row = _sheet(run)[number]
    version = run('sales-order show', dict(sales_order=number))['version']
    invoice = run('sales-order invoice', dict(sales_order=number, expected_version=version, conversion_key='b',
                                              date='2017-03-06'), reason='i')
    assert row['shipped_not_invoiced_amount']['amount'] == invoice['total']['amount'] == '3.34'
    assert row['to_ship_amount']['amount'] == row['shippable_amount']['amount'] == '3.33'


# ---------------------------------------------------------------- seeded, ample stock

DATES = ['2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04']


@pytest.mark.parametrize('seed', [1, 2, 3])
def test_seeded_can_ship_now_is_what_shipment_post_ships(books, seed):
    """Random orders, receipts (some dated a year on), pick lists, drafts, boxes, invoices and cancellations over two
    sites, a lot item and a case unit, with ample stock: every real `shipment post` from an order ships exactly its
    row's can-ship-now."""
    run, rnd = books['run'], random.Random(seed)
    run('inventory-site update', dict(site='Main', name='Warehouse'), reason='x')
    run('inventory-site create', dict(name='Annex'), reason='x')
    run('company update', dict(units_of_measure_mode='multiple_related_units'), reason='u')
    units = run('unit-of-measure create', dict(name='Cnt', units=[
        dict(name='Each', abbreviation='ea', is_base=True, base_factor='1'),
        dict(name='Case', abbreviation='cs', is_base=False, base_factor='6')]), reason='u')
    items = [_item(books, 'W'), _item(books, 'G', cost='4.00'), _item(books, 'L', 'lot'),
             books['client'].item.create(
                 company=books['company'], name='Bolts', type='inventory_part', description='b', price='1.00',
                 purchase_description='b', cost='1', cogs_account_id=books['cogs'], income_account_id=books['income'],
                 unit_of_measure_set_id=units['id'])['id']]
    if rnd.random() < .5:
        run('company update', dict(pick_and_pack_enabled=True), reason='p')
    if rnd.random() < .4:
        run('company update', dict(available_deducts_sales_orders=False), reason='p')
    sites, orders, mismatches = ['Warehouse', 'Annex', None], [], []

    def buy(item, date=None):
        extra = dict(lot='L%d' % rnd.randint(1, 3)) if item == items[2] else {}
        run('bill post', dict(vendor=books['vendor'], date=date or rnd.choice(DATES), items=[dict(
            item=item, quantity=str(rnd.randint(20, 60)), unit_cost='3.00', site=rnd.choice(sites[:2]), **extra)]),
            reason='in')

    def order():
        lines = []
        for item in rnd.sample(items, rnd.randint(1, 3)):
            line = dict(item=item, quantity=str(rnd.randint(1, 8)), unit_price='7.77',
                        **({'unit': 'cs'} if item == items[3] and rnd.random() < .6 else {}))
            site = rnd.choice(sites)
            if site:
                line['site'] = site
            lines.append(line)
        orders.append(run('sales-order create', dict(customer=books['customer'], date=rnd.choice(DATES),
                                                     lines=lines), reason='o')['number'])

    def open_lists():
        try:
            return [r['number'] for r in run('pick-list query', dict(status='open', limit=200))['rows']]
        except BookflowError:
            return []

    for item in items:
        buy(item)
    for _ in range(4):
        order()
    for step in range(40):
        op = rnd.choice(['order', 'buy', 'pick', 'enter', 'draft', 'pack', 'ship', 'ship', 'ship', 'invoice',
                         'cancel'])
        try:
            if op == 'order':
                order()
            elif op == 'buy':
                buy(rnd.choice(items), rnd.choice(DATES + ['2027-01-01']))
            elif op == 'pick':
                run('pick-list create', dict(orders=rnd.sample(orders, min(2, len(orders))), date=rnd.choice(DATES)),
                    reason='w')
            elif op == 'enter':
                found = open_lists()
                if found:
                    shown = run('pick-list show', dict(pick_list=rnd.choice(found)))
                    picked = rnd.choice(shown['lines'])
                    run('pick-list enter', dict(pick_list=shown['number'], date='2017-03-04', entries=[
                        dict(line=str(picked['position']), quantity='1')]), reason='p')
            elif op == 'draft':
                run('shipment create', dict(sales_order=rnd.choice(orders), date=rnd.choice(DATES)), reason='d')
            elif op == 'pack':
                drafts = [r['number'] for r in run('shipment query', dict(status='draft', limit=200))['rows']]
                if drafts:
                    run('shipment pack', dict(shipment=rnd.choice(drafts), line='1', box=1), reason='b')
            elif op == 'cancel':
                found = open_lists()
                if found:
                    run('pick-list cancel', dict(pick_list=rnd.choice(found)), reason='c')
            elif op == 'invoice':
                number = rnd.choice(orders)
                shown = run('sales-order show', dict(sales_order=number))
                run('sales-order invoice', dict(sales_order=number, expected_version=shown['version'],
                                                conversion_key=f'{seed}-{step}', date='2017-03-04'), reason='i')
            elif op == 'ship':
                rows, number = _sheet(run), rnd.choice(orders)
                claimed = can_ship(rows[number]) if number in rows else {}
                try:
                    out = run('shipment post', dict(sales_order=number), reason='ship')
                    got = {}
                    for line in out['lines']:
                        got[line['order_line_id']] = got.get(line['order_line_id'], 0) + Fraction(line['quantity'])
                except BookflowError as error:
                    if error.code in ('E_INTERNAL', 'E_IO'):
                        raise
                    got = {}
                if claimed != got:
                    mismatches.append((step, number, claimed, got))
        except BookflowError as error:
            if error.code in ('E_INTERNAL', 'E_IO'):
                raise
    assert not mismatches, mismatches
