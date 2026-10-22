"""R124: a person takes a deposit from a work order's page and sees it held there and on the customer.

The work order's page offers Receive deposit; the form opens with the document filled in; after the
receipt the document's and the customer's pages both show the money held, and once the work order's
invoice uses it the draw is listed with an Undo link.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _text(b):
    return b.evaluate('document.body.innerText')


def _links(b):
    return b.evaluate('[...document.querySelectorAll("a")].map(a => a.getAttribute("href") || "")')


def test_deposit_from_the_work_order_page(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    run = lambda name, payload: _command(b, env.site, name, payload)
    company = env.site.company_id
    income = run('account.create', {'name': 'Remodel income', 'type': 'income'})['id']
    code = next(row['id'] for row in run('sales-tax-code.list', {})['items'] if not row['taxable'])
    item = run('item.create', {'name': 'Remodel labor', 'type': 'service', 'description': 'Labor', 'price': '100.00',
                               'income_account_id': income, 'sales_tax_code_id': code})['id']
    customer = run('customer.create', {'name': 'Deposit Witness'})['id']
    wo = run('work-order.create', {'date': '2026-03-02', 'title': 'Kitchen', 'customer': customer,
                                   'lines': [{'item': item, 'quantity': '4'}]})
    page = f'{env.site.base_url}/c/{company}/work-order/{wo["id"]}'
    b.navigate(page)
    b.wait_for('document.readyState === "complete"')
    assert 'Customer prepayments' in _text(b) and 'No deposits received.' in _text(b)
    receive = f'/c/{company}/prepayment/receive?document={wo["id"]}'
    assert receive in _links(b)

    b.navigate(env.site.base_url + receive)
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.getElementsByName("f:document")[0].value') == wo['id']
    for name, value in (('amount', '150.00'), ('date', '2026-03-03'), ('payment_method', 'Check')):
        b.evaluate(f'''(() => {{const e=document.getElementsByName("f:{name}")[0]; e.value="{value}";
            e.dispatchEvent(new Event("input",{{bubbles:true}}));}})()''')
    b.evaluate('void (window.depositForm = document.querySelector("[data-generated-form]"))')
    b.evaluate('document.querySelector("button[value=submit]").click()')
    b.wait_for('!window.depositForm || !window.depositForm.isConnected', timeout=20)
    b.wait_for('document.readyState === "complete"')
    held = run('prepayment.show', {'document': wo['id']})
    assert held['held']['minor_units'] == 15000

    b.navigate(page)
    b.wait_for('document.readyState === "complete"')
    text = _text(b)
    assert 'Held 150.00 USD' in text and held['receipts'][0]['receipt_number'] in text
    b.navigate(f'{env.site.base_url}/c/{company}/customer/{customer}')
    b.wait_for('document.readyState === "complete"')
    assert 'Held 150.00 USD' in _text(b)

    current = run('work-order.show', {'work_order': wo['id']})
    invoice = run('work-order.invoice', {'work_order': wo['id'], 'expected_version': current['version'],
                                         'conversion_key': 'browser-1', 'date': '2026-03-10'})
    assert invoice['prepayment_applied']['minor_units'] == 15000
    b.navigate(page)
    b.wait_for('document.readyState === "complete"')
    text = _text(b)
    assert 'Held 0.00 USD' in text and f'invoice {invoice["number"]}' in text
    assert any(link.startswith(f'/c/{company}/prepayment/unapply?draw=') for link in _links(b))
