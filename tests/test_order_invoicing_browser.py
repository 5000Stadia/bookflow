"""R101 increment 4: Create invoice from a sales order in the browser, filled from the command's preview.

The order's invoice page offers the order's own choices (what can ship, every open quantity, chosen lines,
a quantity per line) and no percentage or paid sales receipt; previewing shows what would be billed, the tax
at today's rates against the quote, and writes nothing; saving posts the invoice and the order reads it.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def test_create_invoice_from_an_order(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1440, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    order = run('sales-order.show', {'sales_order': 'DEMO-SO-1'})
    page = f'{env.site.base_url}/c/{env.site.company_id}/sales-order/{order["id"]}/invoice'
    b.navigate(page)
    b.wait_for('!!document.querySelector("[name=billing-selection]")')
    options = b.evaluate('[...document.querySelectorAll("[name=billing-selection] option")].map(o => o.value)')
    assert options == ['remaining', 'all-open', 'selected', 'partial']
    assert not b.evaluate('!!document.querySelector("[name=billing-percent]")')
    assert 'Create paid sales receipt' not in _text(b)

    def act(action):
        b.evaluate('''(() => {for (const [name, value] of [["f:date", "2026-10-22"]]) {
            const e = document.querySelector(`[name="${name}"]`); if (e && !e.value) {e.value = value; e.dispatchEvent(new Event("input", {bubbles: true}));}}
            const r = document.querySelector("input[name$=reason]"); r.value = "Shipped"; r.dispatchEvent(new Event("input", {bubbles: true}));})()''')
        b.evaluate('void (window.invoiceForm = document.querySelector("[name=billing-selection]").form)')
        b.evaluate(f'document.querySelector("button[value={action}]").click()')
        b.wait_for('!window.invoiceForm || !window.invoiceForm.isConnected', timeout=30)
        b.wait_for('document.readyState === "complete"')

    # Each line shows the order's quantities and its item's stock: on hand, or available with the switch.
    figures = b.evaluate('document.querySelector("[data-order-figures]").innerText')
    assert 'Ordered 10' in figures and 'Backordered 8' in figures and 'On hand' in figures, figures
    assert not b.evaluate('document.querySelector("[data-stock-available]").checkVisibility()')
    b.evaluate('document.querySelector("[data-stock-switch]").click()')
    assert b.evaluate('document.querySelector("[data-stock-available]").checkVisibility()')
    assert not b.evaluate('document.querySelector("[data-stock-on-hand]").checkVisibility()')
    _shot(b, 'create-invoice-available')
    act('preview')
    b.wait_for('!!document.querySelector("[data-order-invoice]")', timeout=30)
    panel = b.evaluate('document.querySelector("[data-order-invoice]").innerText')
    assert 'This invoice would bill' in panel and '· 8' in panel and 'quoted on the order' in panel, panel
    assert run('sales-order.show', {'sales_order': order['id']})['version'] == order['version']
    b.viewport(390, 900)
    assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    b.viewport(1440, 900)
    b.evaluate('''(() => {const r = document.querySelector("input[name$=reason]"); if (r && !r.value) {r.value = "Shipped";
        r.dispatchEvent(new Event("input", {bubbles: true}));} document.querySelector("button[value=submit]").click();})()''')
    import time
    for _ in range(60):
        shown = run('sales-order.show', {'sales_order': order['id']})
        if not shown['order']['is_open']:
            break
        time.sleep(0.5)
    # Everything still open shipped; the two valves cancelled earlier make it read closed, not invoiced in full.
    assert shown['order']['progress'] == 'closed' and not shown['order']['is_open'], shown['order']
    # The invoice it made shows the order's quantities on its page, and its print carries them too.
    billing = run('sales-order.billing', {'sales_order': order['id']})
    invoice = billing['destinations'][-1]
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/{invoice["id"]}')
    b.wait_for('!!document.querySelector("[data-line-order]")', timeout=30)
    line = b.evaluate('document.querySelector("[data-line-order]").innerText')
    assert 'ordered 10' in line and 'previously invoiced 0' in line and 'backordered 0' in line, line
    _shot(b, 'invoice-from-order')
    printed = b.evaluate(f'''fetch("/c/{env.site.company_id}/invoice/{invoice["id"]}/print").then(async r =>
        [r.status, r.headers.get("content-type"), btoa(String.fromCharCode(...new Uint8Array(await r.arrayBuffer())))])''',
        await_promise=True, timeout=30)
    assert printed[0] == 200 and 'pdf' in printed[1], printed[:2]
    if SHOTS:
        import base64
        (SHOTS / 'invoice-from-order-print.pdf').write_bytes(base64.b64decode(printed[2]))


SHOTS = None
if __import__('os').environ.get('BF_SHOTS'):
    from pathlib import Path
    SHOTS = Path(__import__('os').environ['BF_SHOTS'])
    SHOTS.mkdir(parents=True, exist_ok=True)


def _shot(b, name):
    """Full-length screenshots at 1440 and 390 when BF_SHOTS names a folder; nothing otherwise."""
    if not SHOTS:
        return
    import base64
    for width in (1440, 390):
        b.viewport(width, 900)
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (name, width)
        data = b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})['data']
        (SHOTS / f'{name}-{width}.png').write_bytes(base64.b64decode(data))
    b.viewport(1440, 900)
