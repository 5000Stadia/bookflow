"""R181 increment 3: picking in real headless Chrome, at 1440 and at 390 pixels.

The demo company has picking off: the order page offers Ship but no pick list, and the Inventory menu lists no pick
lists. An admin turns it on in company settings; the order page then offers Create pick list. A person makes the list
for an order needing three tubes of sealant, opens the pick screen, scans the bin label of A2 and the sealant's barcode
three times, and three are picked; Ship what was picked posts the shipment. Every form submits through htmx and at a
phone's width nothing scrolls sideways. With BF_PICK_SHOTS set to a folder, the screens are saved there.
"""
import base64
import os
from pathlib import Path

import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

A2 = '2000000001012'
SEALANT = '4006381334020'


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    folder = os.environ.get('BF_PICK_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _scan(b, code):
    b.evaluate(f'''(() => {{ const input = document.querySelector('[data-scan-mode=pick] [data-scan-input]');
        input.value = {code!r};
        input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()''')


@pytest.mark.parametrize('width', (1440, 390))
def test_pick_an_order_by_scanning_and_ship_what_was_picked(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    customer = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})['revision']['facts']['profile']['customer']['id']
    order = write('sales-order.create', {'customer': customer, 'date': DEMO_AS_OF, 'number': 'PICK-1', 'lines': [
        {'item': 'Pipe Sealant', 'quantity': '3'}]})

    # Off: the order page offers Ship, not a pick list.
    b.navigate(f'{base}/sales-order/{order["id"]}')
    b.wait_for('!!document.querySelector("[data-ship-order]")', timeout=30)
    assert not b.evaluate('!!document.querySelector("[data-pick-order]")')
    _shot(b, width, 'order-picking-off')
    if width == 390:
        _fits(b)
    b.navigate(f'{base}/_group/items')
    b.wait_for('document.readyState === "complete"', timeout=30)
    assert 'Pick lists' not in _text(b)

    # An admin turns picking on in company settings.
    write('company.update', {'pick_and_pack_enabled': True})
    b.navigate(f'{base}/company/self/update')
    b.wait_for('document.readyState === "complete"', timeout=30)
    assert 'Use pick lists' in _text(b), _text(b)[-1500:]
    b.navigate(f'{base}/sales-order/{order["id"]}')
    b.wait_for('!!document.querySelector("[data-pick-order]")', timeout=30)
    _shot(b, width, 'order-picking-on')
    if width == 390:
        _fits(b)

    b.evaluate('document.querySelector("[data-pick-order]").click()')
    b.wait_for('!!document.querySelector("[data-pick-start] [name=orders]")', timeout=30)
    assert b.evaluate('document.querySelector("[data-pick-start] [name=orders]").value') == 'PICK-1'
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-pick-start]');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r};
        f.querySelector('[name=picker]').value = 'Sam';
        f.querySelector('[data-pick-create]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-pick-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-pick-result]")'), _text(b)[-1500:]
    number = b.evaluate('document.querySelector("#pick-list").dataset.pick')

    b.wait_for('!!window.bookflowScan && !!document.querySelector("[data-scan-mode=pick] [data-scan-input]")',
               timeout=30)
    _scan(b, A2)
    b.wait_for('document.querySelector("[data-scan-bin]").value === "A2"', timeout=30)
    for expected in ('1', '2', '3'):
        _scan(b, SEALANT)
        b.wait_for(f'!!document.querySelector("[data-pick-result]") && document.querySelector("[data-pick-result]")'
                   f'.innerText.includes("picked {expected} of 3") || !!document.querySelector("[role=alert]")',
                   timeout=30)
        assert not b.evaluate('!!document.querySelector("[role=alert]")'), _text(b)[-1500:]
    shown = run('pick-list.show', {'pick_list': number})
    assert [(line['bin'], line['picked']) for line in shown['lines']] == [('A2', '3')]
    _shot(b, width, 'pick-screen')
    if width == 390:
        _fits(b)
    # An item not on the list says so and adds nothing.
    _scan(b, '4006381334013')
    b.wait_for('!!document.querySelector("[role=alert]")', timeout=30)
    alert = b.evaluate('document.querySelector("[role=alert]").innerText')
    assert 'not on pick list' in alert, alert
    b.wait_for('document.querySelector("[data-scan-status]").innerText.includes("Nothing was added")', timeout=30)
    b.evaluate('document.querySelector("[role=alert]").remove()')
    assert run('pick-list.show', {'pick_list': number})['lines'][0]['picked'] == '3'

    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-pick-ship]').closest('form');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r}; f.querySelector('[data-pick-ship]').click(); }})()''')
    b.wait_for('(document.querySelector("[data-pick-result]")?.innerText || "").includes("Shipment") || '
               '!!document.querySelector("[role=alert]")', timeout=30)
    assert 'Shipment' in b.evaluate('document.querySelector("[data-pick-result]")?.innerText || ""'), _text(b)[-1500:]
    rows = run('shipment.query', {'sales_order': 'PICK-1'})['rows']
    assert len(rows) == 1 and rows[0]['quantity'] == '3'
    assert run('pick-list.show', {'pick_list': number})['status'] == 'done'

    b.navigate(f'{base}/inventory/pick-lists')
    b.wait_for('!!document.querySelector("[data-pick-list]")', timeout=30)
    _shot(b, width, 'pick-lists')
    if width == 390:
        _fits(b)
    b.navigate(f'{base}/_group/items')
    b.wait_for('document.readyState === "complete"', timeout=30)
    assert 'Pick lists' in _text(b)
    b.navigate(f'{base}/inventory/pick-lists/{number}/print')
    b.wait_for('document.readyState === "complete"', timeout=30)
    assert 'Bin A2' in _text(b) and 'PICK-1' in _text(b)
    _shot(b, width, 'pick-list-print')
    if width == 390:
        _fits(b)
