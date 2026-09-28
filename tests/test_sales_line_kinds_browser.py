"""R147 in a real browser: the invoice grid takes a subtotal and a percentage discount, shows
their amounts live from the server's own preview, and posts the same invoice the command does."""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401
from tests.test_sales_document_browser import _add_line, _books, _preview, _saved, _contained
from tests.test_service_sales_browser import _choose, _click, _fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

AMOUNTS = ('[...document.querySelectorAll("[data-collection-path=lines] > [data-collection-items] > '
           '[data-collection-item] .line-amount")].map(e => e.textContent.trim())')


def test_subtotal_and_discount_lines_show_live_amounts_and_post(register_browser):
    env, b = register_browser, register_browser.browser
    run = _books(b, env.site)
    b.viewport(1280, 900)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-09-02')
    _choose(b, 'f:customer', 'Commercial Example Customer')
    for index, item in enumerate(('Mainline Clearing', 'Copper Coupling', 'Work Order Subtotal', 'Loyalty Discount')):
        _add_line(b)
        _choose(b, f'c:lines:{index}:item', item)
    # Live: the subtotal shows 145.00 + 5.50 and the 5% discount 7.53 (half-up of 7.525),
    # copied from the server's preview and never computed in the page.
    b.wait_for(f'(() => {{const a = {AMOUNTS}; return a.length === 4 && a[2].includes("150.50") && a[3].includes("7.53");}})()')
    live = b.evaluate(AMOUNTS)
    assert '145.00' in live[0] and '5.50' in live[1]
    _contained(b, 1280)
    _preview(b)
    _click(b, 'submit')
    saved = run('invoice.show', dict(invoice=_saved(b, 'invoice')))
    lines = saved['revision']['lines']
    assert [line.get('line_kind', 'item') for line in lines] == ['item', 'item', 'subtotal', 'discount']
    assert [(line.get('amount') or line['net'])['amount'] for line in lines] == ['145.00', '5.50', '150.50', '-7.53']
    assert [line['net']['amount'] for line in lines] == ['137.75', '5.22', '0.00', '0.00']
