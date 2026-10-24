"""R101 increment 5: both shortfall displays in the browser.

An order line shows the purchase order ordered for it and what is still to arrive; the purchase
order shows the order it covers, linked; both fit a phone.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_the_order_line_and_the_purchase_order_show_their_coverage(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, payload: _command(b, env.site, name, payload)
    order = run('sales-order.create', {'customer': 'Riverside Apartments', 'date': '2026-10-22',
                                       'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '40'}]})
    made = run('sales-order.purchase-order', {'sales_order': order['id'], 'date': '2026-10-22', 'expected_date': '2026-10-29'})
    po = made['purchase_order']
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    for width in (1440, 390):
        b.viewport(width, 900)
        b.navigate(f'{base}/sales-order/{order["id"]}')
        b.wait_for('!!document.querySelector("[data-coverage]")')
        text = b.evaluate('document.querySelector("[data-coverage]").innerText')
        assert po['revision']['number'] in text and 'to arrive' in text and 'expected' in text, text
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        b.navigate(f'{base}/purchase-order/{po["id"]}')
        b.wait_for('!!document.querySelector("[data-covers]")')
        covers = b.evaluate('document.querySelector("[data-covers]").innerText')
        assert order['number'] in covers and 'still to arrive' in covers, covers
        assert b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
