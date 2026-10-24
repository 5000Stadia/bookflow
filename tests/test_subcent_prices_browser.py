"""A sub-cent rate typed in the real invoice window posts exactly (R150), in headless Chrome.

The form submits through htmx (hx-post), so this is the path a person takes. Fuel defaults to
its 3.459 price: 12.5 x 3.459 = 43.2375 -> 43.24. A rate typed as arithmetic, 0.05/4, keeps its
five places (0.0125) instead of rounding to the cent, and 1,000 at it is 12.50.
"""
import json

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_service_sales_browser import _fill, _choose, _value, _click, _preview, _saved, _contained, _name

pytestmark = [pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable'), pytest.mark.timeout(900)]


@pytest.mark.parametrize('width', [1280, 390])
def test_a_sub_cent_rate_typed_in_the_invoice_window_posts_exactly(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    run = lambda name, data: _command(b, env.site, name, data)  # noqa: E731
    income = run('account.create', dict(name='Fuel income', type='income'))['id']
    run('account.create', dict(name='Fuel receivables', type='accounts_receivable'))
    run('customer.create', dict(name='Haulage customer'))
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    for name, price in (('Diesel', '3.459'), ('Screw', '0.01')):
        run('item.create', dict(name=name, type='service', sales_enabled=True, income_account_id=income,
                                price=price, description=name, sales_tax_code_id=code))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-01-12')
    _choose(b, 'f:customer', 'Haulage customer')
    _choose(b, 'f:ar_account', 'Fuel receivables')
    add = 'document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()'
    b.evaluate(add)
    _choose(b, 'c:lines:0:item', 'Diesel')
    _fill(b, 'c:lines:0:quantity', '12.5')
    b.evaluate(add)
    _choose(b, 'c:lines:1:item', 'Screw')
    _fill(b, 'c:lines:1:quantity', '1000')
    _fill(b, 'c:lines:1:unit_price', '0.05/4')
    rate = json.dumps(_name(b, 'c:lines:1:unit_price'))
    assert b.evaluate(f'document.getElementsByName({rate})[0].dataset.mathExtraPlaces') == '3'
    _preview(b)
    assert _value(b, 'c:lines:1:unit_price') == '0.0125'
    shown = b.evaluate('document.querySelector(".sales-document").innerText')
    assert '43.24' in shown and '12.50' in shown and '55.74' in shown
    _click(b, 'submit')
    saved = run('invoice.show', dict(invoice=_saved(b, 'invoice')))
    assert [(line['unit_price']['amount'], line['net']['amount']) for line in saved['revision']['lines']] == [
        ('3.459', '43.24'), ('0.0125', '12.50')]
    assert saved['total']['amount'] == '55.74'
    _contained(b, width)
