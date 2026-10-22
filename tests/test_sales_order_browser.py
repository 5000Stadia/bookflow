"""R101: a person finds, reads, enters and prints sales orders in the browser.

Customers lists Sales orders and offers a new one; the list opens on the order; the order page shows
Ordered, Invoiced, Cancelled and Backordered for each line with its progress and open amount, and
prints; the form asks for what an order takes (no billable flag, a cancelled quantity and Close per
line), and a preview from it writes nothing. Every page fits a phone without sideways scrolling.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _fits(b):
    return b.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')


def test_find_read_enter_and_print_a_sales_order(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, payload: _command(b, env.site, name, payload)
    company = env.site.company_id
    income = run('account.create', {'name': 'Order sales', 'type': 'income'})['id']
    cogs = run('account.create', {'name': 'Order cost', 'type': 'cost_of_goods_sold'})['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    valve = run('item.create', {'name': 'Gate valve', 'type': 'inventory_part', 'description': 'Gate valve 1in',
                                'price': '18.00', 'income_account_id': income, 'purchase_description': 'Gate valve',
                                'cost': '9.00', 'cogs_account_id': cogs, 'sales_tax_code_id': code})['id']
    visit = run('item.create', {'name': 'Fit valve', 'type': 'service', 'description': 'Fit on site',
                                'price': '75.00', 'income_account_id': income, 'sales_tax_code_id': code})['id']
    customer = run('customer.create', {'name': 'Quayside Dairy'})['id']
    order = run('sales-order.create', {'customer': customer, 'date': '2026-10-20', 'number': 'SO-77',
                                       'lines': [{'item': valve, 'quantity': '12', 'cancelled_quantity': '2'},
                                                 {'item': visit, 'quantity': '1'}]})
    base = f'{env.site.base_url}/c/{company}'

    for width in (1440, 390):
        b.viewport(width, 900)
        b.navigate(base + '/sales-order')
        b.wait_for('document.body.innerText.includes("SO-77")')
        assert 'Quayside Dairy' in _text(b) and _fits(b), width

        b.navigate(f'{base}/sales-order/{order["id"]}')
        b.wait_for('!!document.querySelector(".work-document .document-lines")')
        text = _text(b)
        for word in ('Ordered', 'Invoiced', 'Cancelled', 'Backordered', 'Open', 'Print / save PDF'):
            assert word in text, (width, word)
        cells = b.evaluate('[...document.querySelectorAll(".document-lines tbody tr:first-child [data-label=Backordered]")]'
                           '.map(e => e.innerText.replace("Backordered", "").trim())')
        assert cells == ['10'], cells
        assert 'Billable' not in b.evaluate('document.querySelector(".document-lines").innerText')
        assert _fits(b), width
    printed = b.evaluate(f'fetch("/c/{company}/sales-order/{order["id"]}/print").then(r => r.headers.get("content-type"))')
    assert printed.startswith('application/pdf')

    b.viewport(1440, 900)
    b.navigate(base)
    b.wait_for('document.readyState === "complete"')
    hrefs = b.evaluate('[...document.querySelectorAll("a")].map(a => a.getAttribute("href") || "")')
    assert f'/c/{company}/sales-order' in hrefs and f'/c/{company}/sales-order/create' in hrefs

    b.navigate(base + '/sales-order/create')
    b.wait_for('document.readyState === "complete"')
    names = b.evaluate('[...document.querySelectorAll("[name]")].map(e => e.getAttribute("name"))')
    assert any(name.endswith(':cancelled_quantity') for name in names), names
    assert any(name.endswith(':close') for name in names), names
    assert not any(name.endswith(':billable') or name.endswith(':completed_quantity') for name in names), names
    assert not any(name in ('f:scope', 'f:expires_on', 'f:decision_note') for name in names), names
    for width in (1440, 390):
        b.viewport(width, 900)
        assert _fits(b), width
    assert run('sales-order.query', {'limit': 10})['count'] == 1
