"""R181 increment 5: the fulfillment worksheet in real headless Chrome, at 1440 and at 390 pixels.

A person opens Fulfillment from the Items menu: it opens on the orders that can ship now, each row's can-ship-now
amount the report's own. With picking off it offers Ship, Create invoices and Print packing slips and no pick action.
Two orders are selected and shipped with one click (a shipment each), their packing slips print from a real click as
one PDF with a page per order, and Create invoices bills each (an invoice each). Turned on, picking adds its tabs,
Create pick list makes one list for a selected order and Print pick lists prints it. Writes submit through htmx; at a
phone's width nothing scrolls sideways. With BF_WORKSHEET_SHOTS set to a folder, the screens are saved there.
"""
import base64
import os
import re
import time
from pathlib import Path

import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    folder = os.environ.get('BF_WORKSHEET_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _select(b, *numbers):
    b.evaluate('document.querySelectorAll("[data-select-order]").forEach(box => box.checked = false)')
    for number in numbers:
        b.evaluate(f'document.querySelector(\'[data-select-order][value="{number}"]\').checked = true')


def _opened(b, part, before):
    """The URL of the tab a real click opened (a form with target _blank)."""
    for _ in range(100):
        found = [t['url'] for t in b.call('Target.getTargets', {})['targetInfos']
                 if part in t['url'] and t['targetId'] not in before]
        if found:
            return found[0]
        time.sleep(.1)
    raise AssertionError(f'no tab opened for {part}')


def _click(b, selector):
    """A real mouse click (a user gesture, so the browser opens the tab a form with target _blank asks for)."""
    point = b.evaluate(f'''(() => {{ const el = document.querySelector({selector!r});
        el.scrollIntoView({{block: 'center'}}); const r = el.getBoundingClientRect();
        return {{x: r.left + r.width / 2, y: r.top + r.height / 2}}; }})()''')
    for event in ('mousePressed', 'mouseReleased'):
        b.call('Input.dispatchMouseEvent', {'type': event, 'x': point['x'], 'y': point['y'], 'button': 'left',
                                            'clickCount': 1})


def _targets(b):
    return {t['targetId'] for t in b.call('Target.getTargets', {})['targetInfos']}


def _fetch(b, url):
    return base64.b64decode(b.evaluate(f'''(async () => {{ const r = await fetch({url!r});
        const bytes = new Uint8Array(await r.arrayBuffer()); let s = '';
        for (const x of bytes) s += String.fromCharCode(x); return btoa(s); }})()''', await_promise=True))


def _rows(b):
    return b.evaluate('[...document.querySelectorAll("[data-order]")].map(r => [r.dataset.order, '
                      'r.querySelector("[data-shippable]").innerText])')


@pytest.mark.parametrize('width', (1440, 390))
def test_ship_print_and_invoice_selected_orders_from_the_worksheet(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    customer = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})['revision']['facts']['profile']['customer']['id']
    accounts = run('account.query', {'limit': 200})['items']
    item = write('item.create', {'name': 'Worksheet Part', 'type': 'inventory_part', 'description': 'Part',
                                 'price': '20.00', 'cost': '3.00', 'purchase_description': 'Part',
                                 'cogs_account_id': next(a['id'] for a in accounts if a['type'] == 'cost_of_goods_sold'),
                                 'income_account_id': next(a['id'] for a in accounts if a['type'] == 'income')})['id']
    vendor = run('vendor.query', {'limit': 1})['items'][0]['id']
    write('bill.post', {'vendor': vendor, 'date': DEMO_AS_OF, 'items': [{'item': item, 'quantity': '10',
                                                                         'unit_cost': '3.00'}]})
    for number in ('WS-1', 'WS-2', 'WS-3'):
        write('sales-order.create', {'customer': customer, 'date': DEMO_AS_OF, 'number': number,
                                     'lines': [{'item': item, 'quantity': '1'}]})

    b.navigate(f'{base}/_group/items')
    b.wait_for('document.readyState === "complete"', timeout=30)
    assert 'Fulfillment' in _text(b) and 'Pick lists' not in _text(b)
    b.evaluate('[...document.querySelectorAll("a")].find(a => a.getAttribute("href")?.endsWith("/inventory/fulfillment"))'
               '.click()')
    b.wait_for('!!document.querySelector("[data-worksheet-batch]")', timeout=30)
    report = run('report.order-fulfillment', {'can_ship': True, 'limit': 200})
    assert _rows(b) == [[row['sales_order_number'], row['shippable_amount']['amount']] for row in report['rows']]
    assert {'WS-1', 'WS-2', 'WS-3'} <= {row[0] for row in _rows(b)}
    assert not b.evaluate('!!document.querySelector("[data-batch-pick]") || !!document.querySelector("[data-batch-picks]")')
    assert b.evaluate('[...document.querySelectorAll("[data-tab]")].map(a => a.dataset.tab).join()') == \
        'ready,to_ship,waiting,all'
    _shot(b, width, 'worksheet')
    if width == 390:
        _fits(b)

    # Ship two orders with one click: a shipment each.
    _select(b, 'WS-1', 'WS-2')
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-worksheet-batch]');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r}; f.querySelector('[data-batch-ship]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-batch-results]") || !!document.querySelector("[role=alert]")', timeout=60)
    assert b.evaluate('document.querySelectorAll("[data-batch-results] li.ok").length') == 2, _text(b)[-1500:]
    for number in ('WS-1', 'WS-2'):
        assert len(run('shipment.query', {'sales_order': number})['rows']) == 1
    _shot(b, width, 'shipped')
    if width == 390:
        _fits(b)

    # Their packing slips print from a real click: one PDF, a page per order.
    b.navigate(f'{base}/inventory/fulfillment?tab=waiting')
    b.wait_for('!!document.querySelector("[data-select-order]")', timeout=30)
    _select(b, 'WS-1', 'WS-2')
    before = _targets(b)
    _click(b, '[data-batch-slips]')
    url = _opened(b, 'packing-slips', before)
    assert re.search(r'order=WS-1.*order=WS-2', url), url
    pdf = _fetch(b, url)
    assert pdf.startswith(b'%PDF') and len(re.findall(rb'/Type\s*/Page[^s]', pdf)) == 2

    # Create invoices: an invoice each.
    _select(b, 'WS-1', 'WS-2')
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-worksheet-batch]');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r}; f.querySelector('[data-batch-invoice]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-batch-results]") || !!document.querySelector("[role=alert]")', timeout=60)
    assert b.evaluate('document.querySelectorAll("[data-batch-results] li.ok").length') == 2, _text(b)[-1500:]
    for number in ('WS-1', 'WS-2'):
        assert run('sales-order.show', {'sales_order': number})['order']['progress'] == 'invoiced'
    if width == 390:
        _fits(b)

    # Picking on: its tabs and actions appear; one list for a selected order, printed from a real click.
    write('company.update', {'pick_and_pack_enabled': True})
    b.navigate(f'{base}/inventory/fulfillment')
    b.wait_for('!!document.querySelector("[data-batch-pick]")', timeout=30)
    assert b.evaluate('[...document.querySelectorAll("[data-tab]")].map(a => a.dataset.tab).join()') == \
        'ready,to_pick,picking,to_pack,waiting,all'
    _select(b, 'WS-3')
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-worksheet-batch]');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r}; f.querySelector('[data-batch-pick]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-batch-results]") || !!document.querySelector("[role=alert]")', timeout=60)
    assert b.evaluate('document.querySelectorAll("[data-batch-results] li.ok").length') == 1, _text(b)[-1500:]
    listed = run('pick-list.query', {'sales_order': 'WS-3'})['rows']
    assert len(listed) == 1
    b.navigate(f'{base}/inventory/fulfillment?tab=picking')
    b.wait_for('!!document.querySelector("[data-select-order]")', timeout=30)
    assert [row[0] for row in _rows(b)] == ['WS-3']
    _shot(b, width, 'picking')
    if width == 390:
        _fits(b)
    _select(b, 'WS-3')
    before = _targets(b)
    _click(b, '[data-batch-picks]')
    url = _opened(b, 'fulfillment/pick-lists', before)
    page = b.evaluate(f'fetch({url!r}).then(r => r.text())', await_promise=True)
    assert f'data-printed-pick-list="{listed[0]["number"]}"' in page and 'WS-3' in page
