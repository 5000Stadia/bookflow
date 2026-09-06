"""A boolean definition default must survive ordinary generated browser controls."""

import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.timeout(90)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_generated_boolean_default_is_a_boolean_on_preview_and_save(register_browser, width):
    env, browser = register_browser, register_browser.browser
    browser.viewport(width, 900)
    browser.navigate(env.site.base_url + '/c/' + env.site.company_id + '/custom-field/create')
    browser.wait_for('!!document.querySelector("[data-generated-form]")')
    _fill(browser, 'f:name', 'Boolean default browser witness')
    _fill(browser, 'f:kind', 'bool')
    browser.evaluate('document.querySelector("[data-collection-path=scopes] [data-collection-add]").click()')
    scope_name = browser.evaluate('document.querySelector("[data-collection-path=scopes] [data-collection-items] select").name')
    _fill(browser, scope_name, 'customer')
    _fill(browser, 'f:default', 'false')
    _contained(browser, width)
    browser.evaluate('void (window.beforeForm = document.querySelector("[data-generated-form]"))')
    _click(browser, 'preview')
    browser.wait_for('!window.beforeForm.isConnected')
    assert not browser.evaluate('document.querySelector(".error")?.textContent'), browser.evaluate('document.body.innerText')
    preview = browser.evaluate('document.querySelector(".warn pre").textContent')
    assert json.loads(preview)['default'] is False
    _click(browser, 'submit')
    browser.wait_for('!location.pathname.endsWith("/create")')
    saved = _command(browser, env.site, 'custom-field.show', {'custom_field': 'Boolean default browser witness'})
    assert saved['default'] is False
    browser.navigate(env.site.base_url + '/c/' + env.site.company_id + '/custom-field/' + saved['id'] + '/update')
    browser.wait_for('!!document.querySelector("[data-generated-form]")')
    _fill(browser, 'f:default', 'true')
    browser.evaluate('void (window.beforeForm = document.querySelector("[data-generated-form]"))')
    _click(browser, 'preview')
    browser.wait_for('!window.beforeForm.isConnected')
    assert not browser.evaluate('document.querySelector(".error")?.textContent')
    assert json.loads(browser.evaluate('document.querySelector(".warn pre").textContent'))['default'] is True
    _click(browser, 'submit')
    browser.wait_for('!location.pathname.endsWith("/update")')
    updated = _command(browser, env.site, 'custom-field.show', {'custom_field': saved['id']})
    assert updated['default'] is True and updated['version'] == saved['version'] + 1
