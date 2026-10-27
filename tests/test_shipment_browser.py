"""R181 increment 1: shipments in real headless Chrome, at 1440 and at 390 pixels.

A person opens Ship from the order page, sets three valves of DEMO-SO-1 to ship, previews (the cost going to Shipped
Not Invoiced, nothing saved) and ships; the shipment page shows the cost waiting, and Create invoice bills it, after
which nothing waits. Every form submits through htmx, as the server requires of a cookie session, and at a phone's
width nothing scrolls sideways.
"""
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


@pytest.mark.parametrize('width', (1440, 390))
def test_ship_part_of_an_order_and_bill_it_from_the_shipment_page(register_browser, width):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(width, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    order = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})
    line = order['revision']['lines'][0]['line_id']

    b.navigate(f'{base}/sales-order/{order["id"]}')
    b.wait_for('!!document.querySelector("[data-ship-order]")', timeout=30)
    b.evaluate('document.querySelector("[data-ship-order]").click()')
    b.wait_for('!!document.querySelector("[data-ship-form] [name^=\\"qty:\\"]")', timeout=30)
    b.evaluate(f'''(() => {{ document.querySelector('[name="qty:{line}"]').value = '3';
        document.querySelector('[data-ship-form] [name=date]').value = {DEMO_AS_OF!r};
        document.querySelector('[data-ship-form] [name=tracking]').value = '1Z999';
        document.querySelector('[data-ship-form] [name="ctx:reason"]').value = 'Truck took three'; }})()''')
    b.evaluate('document.querySelector("button[value=preview]").click()')
    b.wait_for('!!document.querySelector("[data-ship-preview]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-ship-preview]")'), _text(b)[-1500:]
    assert run('shipment.query', {})['rows'] == []                     # the preview saved nothing
    if width == 390:
        _fits(b)
    b.evaluate('document.querySelector("button[value=ship]").click()')
    b.wait_for('!!document.querySelector("[data-shipment-result]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-shipment-result]")'), _text(b)[-1500:]
    shipment = run('shipment.show', {'shipment': '1001'})
    assert [line['quantity'] for line in shipment['lines']] == ['3'] and shipment['tracking_numbers'] == ['1Z999']
    waiting = b.evaluate('document.querySelector("[data-shipment-waiting]").innerText')
    assert waiting == shipment['cost']['amount'] and shipment['cost']['minor_units'] > 0, waiting
    if width == 390:
        _fits(b)
    # R181 increment 2: one of the three comes back before it is billed, through the page's Return form.
    b.evaluate('''(() => { const f = document.querySelector('[data-shipment-return]');
        f.querySelector('[name=quantity]').value = '1'; f.querySelector('[name=date]').value = %r;
        f.querySelector('[name="ctx:reason"]').value = 'One came back';
        f.querySelector('button[value=return]').click(); })()''' % DEMO_AS_OF)
    b.wait_for('!!document.querySelector("[data-shipment-returned]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-shipment-returned]")'), _text(b)[-1500:]
    returns = run('shipment-return.query', {'shipment': '1001'})['rows']
    assert [(r['status'], r['quantity']) for r in returns] == [('returned', '1')]
    assert b.evaluate('!!document.querySelector("[data-shipment-returns]")')
    if width == 390:
        _fits(b)
    b.evaluate('''(() => { document.querySelector('[data-shipment-invoice] [name=date]').value = %r;
        document.querySelector('[data-shipment-invoice] [name="ctx:reason"]').value = 'Bill the three';
        document.querySelector('[data-shipment-invoice] button[value=invoice]').click(); })()''' % DEMO_AS_OF)
    b.wait_for('!!document.querySelector("[data-shipment-invoiced]") || !!document.querySelector("[role=alert]")', timeout=30)
    assert b.evaluate('!!document.querySelector("[data-shipment-invoiced]")'), _text(b)[-1500:]
    assert b.evaluate('document.querySelector("[data-shipment-waiting]").innerText') == '0.00'
    assert run('report.shipped-not-invoiced', {'as_of': DEMO_AS_OF})['total']['minor_units'] == 0
    assert [line['quantity'] for line in run('shipment.show', {'shipment': '1001'})['lines']] == ['3']
    assert run('shipment.show', {'shipment': '1001'})['lines'][0]['invoiced'] == '2'
