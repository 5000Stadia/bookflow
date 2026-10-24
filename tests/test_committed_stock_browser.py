"""R102: committed stock in the browser -- stock status, the order's stock line and Open sales orders.

Stock status shows On sales orders, Available, On order and Next delivery and opens with its figures;
an order's stocked line says what is on hand, committed elsewhere, on order and available to it; the
Open sales orders report opens on its numbers by item, and every page fits a phone.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    return b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


def test_stock_status_the_order_line_and_open_sales_orders(register_browser):
    env, b = register_browser, register_browser.browser
    company = env.site.company_id
    base = f'{env.site.base_url}/c/{company}'
    order = _command(b, env.site, 'sales-order.show', {'sales_order': 'DEMO-SO-1'})
    for width in (1440, 390):
        b.viewport(width, 900)
        b.navigate(base + '/report/stock-status')
        b.wait_for('!!document.querySelector("#stock-rows tbody tr")')
        text = _text(b)
        if width == 1440:
            for heading in ('On sales orders', 'Available', 'On order', 'Next delivery'):
                assert heading in text, heading
            valve = b.evaluate('[...document.querySelectorAll("#stock-rows tbody tr")].find(r => r.innerText.includes("Brass Shutoff Valve"))'
                               '.querySelector("[data-value=quantity_committed]").innerText')
            assert valve.strip() == '10'
        assert _fits(b), width

        b.navigate(f'{base}/sales-order/{order["id"]}')
        b.wait_for('!!document.querySelector("[data-stock]")')
        stock = b.evaluate('document.querySelector("[data-stock]").innerText')
        assert 'On hand' in stock and 'Committed elsewhere 2' in stock and 'Available' in stock, stock
        assert _fits(b), width

        b.navigate(base + '/report/open-sales-orders')
        b.wait_for('document.body.innerText.includes("DEMO-SO-1")')
        text = _text(b)
        assert 'Open amount' in text and 'Backordered' in text or width == 390
        assert _fits(b), width
