"""R181 increment 4: packing in real headless Chrome, at 1440 and at 390 pixels.

The demo company has pick and pack off: a shipment page offers Print packing slip but no Pack, and the pack screen
answers with the switch's message. Turned on, the ship page offers Make a draft to pack; the draft opens on the pack
screen, where a person scans the water heater into box 1 and the sealant into box 2 (a real htmx submit each), enters
box 2's measured weight, and ships. The shipment page then lists the boxes, and the packing slip prints. At a phone's
width nothing scrolls sideways. With BF_PACK_SHOTS set to a folder, the screens are saved there (the slip as PNG
pages, through pdftoppm).
"""
import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

HEATER = '4006381334013'
SEALANT = '4006381334020'


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0, 5).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    folder = os.environ.get('BF_PACK_SHOTS')
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    b.evaluate('(() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); })()')
    height = b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)')
    (Path(folder) / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format': 'png', 'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}})['data']))


def _put(b, code, box, quantity=''):
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-pack-scan]');
        f.querySelector('[name=code]').value = {code!r}; f.querySelector('[name=box]').value = {str(box)!r};
        f.querySelector('[name=quantity]').value = {quantity!r};
        f.querySelector('[data-pack-put]').click(); }})()''')
    b.wait_for(f'(document.querySelector("[data-pack-result]")?.innerText || "").includes({code!r}) || '
               '!!document.querySelector("[data-pack-error]")', timeout=30)
    assert not b.evaluate('!!document.querySelector("[data-pack-error]")'), _text(b)[-1500:]


@pytest.mark.parametrize('width', (1440, 390))
def test_pack_a_draft_by_scanning_and_ship_it(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    write = lambda name, payload: _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'Browser test'})
    customer = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})['revision']['facts']['profile']['customer']['id']
    write('item.update', {'item': 'Water Heater', 'unit_weight': '120'})
    order = write('sales-order.create', {'customer': customer, 'date': DEMO_AS_OF, 'number': f'PACK-{width}', 'lines': [
        {'item': 'Water Heater', 'quantity': '1', 'site': 'Warehouse'},
        {'item': 'Pipe Sealant', 'quantity': '2', 'site': 'Warehouse'}]})

    # Off: the ship page offers no draft, and the pack screen answers with the switch's message.
    b.navigate(f'{base}/shipments/new?sales_order={order["number"]}')
    b.wait_for('!!document.querySelector("[data-ship-form]")', timeout=30)
    assert not b.evaluate('!!document.querySelector("[data-ship-draft]")')
    income = next(a['id'] for a in run('account.query', {'limit': 200})['items'] if a['type'] == 'income')
    freight = write('item.create', {'name': f'Freight {width}', 'type': 'other_charge', 'description': 'Freight',
                                    'price': '0', 'sales_enabled': True, 'purchase_enabled': False,
                                    'income_account_id': income})
    write('company.update', {'pick_and_pack_enabled': True, 'shipping_item_id': freight['id']})

    b.navigate(f'{base}/shipments/new?sales_order={order["number"]}')
    b.wait_for('!!document.querySelector("[data-ship-draft]")', timeout=30)
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-ship-form]');
        f.querySelector('[name=date]').value = {DEMO_AS_OF!r}; f.querySelector('[name=freight]').value = '18.00';
        f.querySelector('[data-ship-draft]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-pack-scan]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-pack-scan]")'), _text(b)[-1500:]
    number = b.evaluate('document.querySelector("#pack-screen").dataset.pack')
    assert run('shipment.show', {'shipment': number})['status'] == 'draft'

    _put(b, HEATER, 1)
    _put(b, SEALANT, 2, '2')
    b.evaluate('''(() => { const f = document.querySelector('[data-box="2"] form');
        f.querySelector('[name=weight]').value = '0.6'; f.querySelector('[name=tracking_number]').value = '1Z999';
        f.querySelector('[data-box-save]').click(); })()''')
    b.wait_for('(document.querySelector("[data-pack-result]")?.innerText || "").includes("Box 2 saved")', timeout=30)
    assert b.evaluate('document.querySelector("[data-pack-state]").innerText') == 'Packed'
    shown = run('shipment.show', {'shipment': number})
    boxes = {p['box']: p for p in shown['packing']['packages']}
    assert boxes[1]['weight'] == '120' and boxes[2]['measured_weight'] == '0.6'
    assert boxes[2]['tracking_number'] == '1Z999' and shown['packing']['packed'] is True
    _shot(b, width, 'pack-screen')
    if width == 390:
        _fits(b)
    # An item not on the shipment says so and moves nothing.
    b.evaluate(f'''(() => {{ const f = document.querySelector('[data-pack-scan]');
        f.querySelector('[name=code]').value = 'NOT-A-THING'; f.querySelector('[data-pack-put]').click(); }})()''')
    b.wait_for('!!document.querySelector("[data-pack-error]")', timeout=30)

    b.evaluate('document.querySelector("[data-pack-ship]").click()')
    b.wait_for('(document.querySelector("[data-pack-result]")?.innerText || "").includes("shipped")', timeout=30)
    shipped = run('shipment.show', {'shipment': number})
    assert shipped['status'] == 'shipped' and len(shipped['packing']['packages']) == 2
    assert shipped['freight']['amount']['amount'] == '18.00'

    b.navigate(f'{base}/shipments/{number}')
    b.wait_for('!!document.querySelector("[data-shipment-box-list]")', timeout=30)
    assert 'Box 2' in _text(b) and b.evaluate('!!document.querySelector("[data-shipment-pack]")')
    _shot(b, width, 'shipment-boxes')
    if width == 390:
        _fits(b)

    href = b.evaluate('document.querySelector("[data-packing-slip]").href')
    pdf = base64.b64decode(b.evaluate(f'''(async () => {{ const r = await fetch({href!r});
        const buf = new Uint8Array(await r.arrayBuffer()); let s = '';
        for (const x of buf) s += String.fromCharCode(x); return btoa(s); }})()''', await_promise=True))
    assert pdf.startswith(b'%PDF')
    folder = os.environ.get('BF_PACK_SHOTS')
    if folder and shutil.which('pdftoppm') and width == 1440:
        (Path(folder) / 'packing-slip.pdf').write_bytes(pdf)
        for scale, name in ((1440, 'packing-slip-1440'), (390, 'packing-slip-390')):
            subprocess.run(['pdftoppm', '-png', '-singlefile', '-scale-to-x', str(scale), '-scale-to-y', '-1',
                            str(Path(folder) / 'packing-slip.pdf'), str(Path(folder) / name)], check=True)
