"""Saved selection is an ordinary generated control, including phone preview."""
import json
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.timeout(120)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_saved_selection_control_previews_real_receipt(register_browser, width):
    env, browser = register_browser, register_browser.browser
    customer = _command(browser, env.site, 'customer.create', {'name': 'Selection browser payer'})['id']
    method = _command(browser, env.site, 'payment-method.create', {'name': 'Selection browser cash', 'kind': 'cash'})['id']
    selection = _command(browser, env.site, 'payment.selection.create', dict(mode='new_receipt', customer=customer,
        date='2026-06-01', amount='12.00'))
    browser.viewport(width, 900)
    browser.navigate(env.site.base_url + '/c/' + env.site.company_id + '/payment/receive')
    browser.wait_for('!!document.querySelector("[data-generated-form]") && document.readyState === "complete"')
    for name, value in {'customer': customer, 'date': '2026-06-01', 'amount': '12.00', 'payment_method': method,
        'operation_key': 'browser-selection', 'applications.mode': 'selection',
        'applications.selection': selection['id'], 'applications.expected_version': str(selection['version'])}.items():
        _fill(browser, 'f:' + name, value)
    assert browser.evaluate('document.getElementsByName("f:applications.selection")[0].disabled') is False
    assert browser.evaluate('document.getElementsByName("collection:applications.items")[0].disabled') is True
    _contained(browser, width)
    browser.evaluate('void(window.beforeForm = document.querySelector("[data-generated-form]"))')
    _click(browser, 'preview')
    browser.wait_for('!window.beforeForm.isConnected')
    assert not browser.evaluate('document.querySelector(".error")?.textContent'), browser.evaluate('document.body.innerText')
    preview = json.loads(browser.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['current']['received_minor_units'] == 1200
    assert _command(browser, env.site, 'payment.query', {'customer': customer})['items'] == []
    assert _command(browser, env.site, 'payment.selection.show', {'selection': selection['id']})['state'] == 'open'
