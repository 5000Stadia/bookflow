"""Workspace keyboard navigation and unsaved invoice state in actual Chrome."""
import json
import os
from pathlib import Path

import pytest

from tests.payment_raw_evidence import database
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import (  # noqa: F401
    _command, _key, _tab_to, _type, register_browser,
)
from tests.test_service_sales_browser import _choose, _fill, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
NAV = 'document.querySelector("#workspace-navigation")'
TRIGGER = '#workspace-navigation > summary'


def _menu(browser, expanded):
    browser.wait_for(f'{NAV}.open === {json.dumps(expanded)} && '
                     f'{NAV}.querySelector("summary").getAttribute("aria-expanded") === '
                     f'{json.dumps(str(expanded).lower())}')


def _current(browser, suffix):
    current = browser.evaluate('''[...document.querySelectorAll(
        '#workspace-navigation a[aria-current="page"]')].map(e=>new URL(e.href).pathname)''')
    assert len(current) == 1 and current[0].endswith(suffix), current


def test_mobile_workspace_keyboard_and_single_current_route(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(390, 844)
    b.navigate(base + '/')
    _menu(b, False)
    _current(b, '/' + env.site.company_id + '/')
    _tab_to(b, TRIGGER)
    _key(b, 'Enter')
    _menu(b, True)
    _key(b, 'Tab')
    assert b.evaluate('document.activeElement.matches("#workspace-navigation [data-home-link]")')
    _key(b, 'Tab')
    assert b.evaluate('document.activeElement.matches("#workspace-navigation [data-section=customers]")')
    _key(b, 'Enter')
    b.wait_for('location.pathname.endsWith("/_group/customers")')
    _menu(b, False)
    _current(b, '/_group/customers')
    _tab_to(b, TRIGGER)
    # Native summary Space activation, including the character event.
    b.call('Input.dispatchKeyEvent', {'type':'keyDown', 'key':' ', 'code':'Space',
                                    'windowsVirtualKeyCode':32, 'text':' '})
    b.call('Input.dispatchKeyEvent', {'type':'keyUp', 'key':' ', 'code':'Space',
                                    'windowsVirtualKeyCode':32})
    _menu(b, True)
    _key(b, 'Tab')
    _key(b, 'Escape')
    _menu(b, False)
    assert b.evaluate(f'document.activeElement.matches({json.dumps(TRIGGER)})')
    # Crossing the sidebar breakpoint must not leave focus on a hidden control.
    # The mobile summary has focus after Escape; desktop restores the current section.
    b.viewport(1440, 1000)
    _menu(b, True)
    b.wait_for('document.activeElement.matches("#workspace-navigation [data-section=customers]")')
    assert b.evaluate('document.activeElement.getClientRects().length > 0')
    assert b.evaluate(f'getComputedStyle(document.querySelector({json.dumps(TRIGGER)})).display') == 'none'
    _current(b, '/_group/customers')
    # A noncurrent sidebar link also transfers back to the mobile trigger.
    _tab_to(b, '#workspace-navigation [data-section=banking]')
    b.viewport(390, 844)
    _menu(b, False)
    b.wait_for(f'document.activeElement.matches({json.dumps(TRIGGER)})')
    assert b.evaluate('document.activeElement.getClientRects().length > 0')
    _key(b, 'Enter')
    _menu(b, True)
    _key(b, 'Tab')
    assert b.evaluate('document.activeElement.matches("#workspace-navigation [data-home-link]")')
    _key(b, 'Escape')
    _menu(b, False)
    assert b.evaluate(f'document.activeElement.matches({json.dumps(TRIGGER)})')
    b.navigate(base + '/invoice/post')
    _current(b, '/_group/customers')
    assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')


def test_dirty_invoice_survives_orientation_with_open_picker(register_browser):
    env, b = register_browser, register_browser.browser
    customer = _command(b, env.site, 'customer.create', {'name':'Orientation customer'})
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    dbs = list(root.glob('organizations/*/*/company.db'))
    assert dbs
    before = {str(p):database(p) for p in dbs}
    b.viewport(390, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _choose(b, 'f:customer', 'Orientation customer')
    assert _value(b, 'f:customer') == customer['id']
    _fill(b, 'f:date', '2026-09-18')
    _fill(b, 'f:memo', 'Unsaved invoice across orientation')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _fill(b, 'c:lines:0:quantity', '2.5')
    _fill(b, 'c:lines:0:description', 'Retain unsaved line description')
    search = '[name="label:f:customer"]'
    b.evaluate(f'document.querySelector({json.dumps(search)}).focus()')
    # Reopen the real asynchronous reference lookup while retaining its selected identity.
    _key(b, 'ArrowDown')
    picker = 'document.querySelector("[name=\\"f:customer\\"]").closest("[data-reference]")'
    b.wait_for(f'!{picker}.querySelector("[data-ref-options]").hidden && '
               f'{picker}.querySelectorAll("[role=option]").length > 0')
    b.evaluate('void (window.originalInvoice = document.querySelector("[data-sales-form]"))')
    values = b.evaluate('[...new FormData(originalInvoice).entries()]')
    choices = b.evaluate(f'{picker}.querySelector("[data-ref-options]").innerText')
    assert 'Orientation customer' in choices
    for width, height in [(844, 390), (320, 844), (900, 1200), (1440, 1000), (390, 844)]:
        b.call('Emulation.setDeviceMetricsOverride', {
            'width':width, 'height':height, 'deviceScaleFactor':1, 'mobile':width<1100,
            'screenOrientation':{'type':'landscapePrimary' if width>height else 'portraitPrimary',
                                 'angle':90 if width>height else 0},
        })
        _menu(b, width >= 1100)
        b.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))', await_promise=True)
        assert b.evaluate('originalInvoice === document.querySelector("[data-sales-form]")')
        assert b.evaluate('[...new FormData(originalInvoice).entries()]') == values
        assert b.evaluate(f'document.activeElement.matches({json.dumps(search)})')
        assert b.evaluate(f'!{picker}.querySelector("[data-ref-options]").hidden')
        assert b.evaluate(f'{picker}.querySelector("[data-ref-options]").innerText') == choices
        assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    # The preserved picker is still operable after rotation; no invoice is submitted.
    _key(b, 'ArrowDown')
    _key(b, 'Enter')
    b.wait_for(f'{picker}.querySelector("[data-ref-options]").hidden')
    assert _value(b, 'f:customer') == customer['id']
    assert _value(b, 'f:memo') == 'Unsaved invoice across orientation'
    assert {str(p):database(p) for p in dbs} == before


def _company_state():
    paths = list(Path(os.environ['BOOKFLOW_DATA_ROOT']).glob('organizations/*/*/company.db'))
    assert paths
    return {str(path):database(path) for path in paths}


def _visible_refusal_by_actions(browser, error_selector, button_selector):
    geometry = browser.evaluate(f'''(() => {{
        const error=document.querySelector({json.dumps(error_selector)});
        const button=document.querySelector({json.dumps(button_selector)});
        const e=error.getBoundingClientRect(), b=button.getBoundingClientRect();
        return {{errorTop:e.top,errorBottom:e.bottom,buttonTop:b.top,buttonBottom:b.bottom,
                 scrollY,viewport:innerHeight,errorVisible:!!e.width && !!e.height,
                 buttonVisible:!!b.width && !!b.height}};
    }})()''')
    assert geometry['errorVisible'] and geometry['buttonVisible'], geometry
    assert 0 <= geometry['errorTop'] < geometry['errorBottom'] <= geometry['viewport'], geometry
    assert geometry['errorBottom'] <= geometry['buttonTop'] < geometry['buttonBottom'] <= geometry['viewport'], geometry
    assert geometry['buttonTop'] - geometry['errorBottom'] < 100, geometry
    assert geometry['scrollY'] > 100, geometry  # Feedback stays at the submitted action, not page top.


def test_mobile_server_refusal_retains_edit_and_focuses_action_summary(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/account/{env.expense["id"]}/update')
    b.wait_for('!!document.querySelector("form[data-generated-form]")')
    # A real second writer makes this loaded edit stale; no refused response is injected.
    _command(b, env.site, 'account.update', {
        'account':env.expense['id'], 'expected_version':1, 'name':'Concurrent account edit',
    })
    before = _company_state()
    _fill(b, 'f:name', 'Retain my mobile account name')
    version = _value(b, 'f:expected_version')
    b.evaluate('void (window.submittedForm=document.querySelector("form[data-generated-form]"))')
    button = 'button[name=action][value=submit]'
    b.evaluate(f'document.querySelector({json.dumps(button)}).focus()')
    _key(b, 'Enter')
    b.wait_for('!submittedForm.isConnected && !!document.querySelector("[data-submit-error]")')
    b.wait_for('document.activeElement.matches("[data-submit-error]")')
    assert 'E_VERSION_CONFLICT' in b.evaluate('document.querySelector("[data-submit-error-top]").innerText')
    assert b.evaluate('document.querySelectorAll("[data-submit-error]").length') == 1
    assert b.evaluate('document.querySelector("[data-submit-error]").nextElementSibling.matches(".form-submit")')
    _visible_refusal_by_actions(b, '[data-submit-error]', button)
    assert _value(b, 'f:name') == 'Retain my mobile account name'
    assert _value(b, 'f:expected_version') == version
    assert _company_state() == before


def test_mobile_payment_refusal_beside_preview_retains_input_and_recovery(register_browser):
    env, b = register_browser, register_browser.browser
    customer = _command(b, env.site, 'customer.create', {'name':'Mobile refusal customer'})
    method = _command(b, env.site, 'payment-method.create', {'name':'Mobile refusal cheque', 'kind':'check'})
    payment = _command(b, env.site, 'payment.receive', {
        'customer':customer['id'], 'date':'2026-09-18', 'amount':'25.00',
        'payment_method':method['id'], 'deposit_to':env.bank['id'],
        'operation_key':'workspace-mobile-refusal', 'applications':{'mode':'inline','items':[]},
    })
    before = _company_state()
    b.viewport(390, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?customer={customer["id"]}')
    b.wait_for('document.querySelector("#payment-workspace")?.dataset.loaded === "true" && !document.querySelector("#payment-workspace").hasAttribute("aria-busy")')
    today = b.evaluate('JSON.parse(document.querySelector("#payment-config").textContent).today')
    assert today and b.evaluate('document.querySelector("#payment-date").value') == today
    assert b.evaluate('document.querySelector("[data-company-today]").dataset.companyToday') == today
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?payment={payment["id"]}&mode=update')
    b.wait_for('document.querySelector("#payment-workspace")?.dataset.loaded === "true" && !document.querySelector("#payment-workspace").hasAttribute("aria-busy")')
    for identity in ('payment-error', 'payment-review', 'payment-retry'):
        assert b.evaluate(f'document.querySelectorAll("#{identity}").length') == 1, identity
    assert b.evaluate('document.querySelector("#payment-error").nextElementSibling.contains(document.querySelector("#payment-preview"))')
    for identity, value in [('payment-amount','35.00'),('payment-memo','Retain my payment correction')]:
        b.evaluate(f'''(() => {{const e=document.getElementById({json.dumps(identity)});
            e.value={json.dumps(value)};e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')
    b.evaluate('document.querySelector("#payment-preview").focus()')
    _key(b, 'Enter')
    b.wait_for('!document.querySelector("#payment-error").hidden && !document.querySelector("#payment-workspace").hasAttribute("aria-busy")')
    assert 'E_REASON_REQUIRED' in b.evaluate('document.querySelector("#payment-error").innerText')
    _visible_refusal_by_actions(b, '#payment-error', '#payment-preview')
    assert b.evaluate('document.querySelector("#payment-amount").value') == '35.00'
    assert b.evaluate('document.querySelector("#payment-memo").value') == 'Retain my payment correction'
    assert b.evaluate('!document.querySelector("#payment-review").hidden && !document.querySelector("#payment-review").disabled')
    assert b.evaluate('document.querySelector("#payment-retry").hidden && document.querySelector("#payment-save").disabled')
    assert _company_state() == before
    # Fix the refusal in the retained form and obtain a real preview, without saving.
    b.evaluate('document.querySelector("#payment-reason").value="Correct received amount"')
    b.evaluate('document.querySelector("#payment-preview").focus()')
    _key(b, 'Enter')
    b.wait_for('!document.querySelector("#payment-workspace").hasAttribute("aria-busy") && !document.querySelector("#payment-preview-result").hidden')
    assert b.evaluate('document.querySelector("#payment-error").hidden && !document.querySelector("#payment-save").disabled')
    assert b.evaluate('document.querySelector("#payment-memo").value') == 'Retain my payment correction'
    assert _company_state() == before


def test_mobile_failed_htmx_request_retains_values_and_does_not_retry(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/account/{env.expense["id"]}/update')
    b.wait_for('!!document.querySelector("form[data-generated-form]")')
    before = _company_state()
    _fill(b, 'f:name', 'Retain offline mobile account name')
    b.evaluate('''window.attemptedRequests=0;
        document.addEventListener('htmx:beforeRequest',()=>window.attemptedRequests++);
        void (window.offlineForm=document.querySelector('form[data-generated-form]'));''')
    values = b.evaluate('[...new FormData(offlineForm).entries()]')
    button = 'button[name=action][value=submit]'
    b.call('Network.enable')
    b.call('Network.emulateNetworkConditions', {
        'offline':True, 'latency':0, 'downloadThroughput':-1, 'uploadThroughput':-1,
    })
    try:
        # Native activation reaches HTMX's real XHR, which Chrome refuses while offline.
        b.evaluate(f'document.querySelector({json.dumps(button)}).focus()')
        _key(b, 'Enter')
        b.wait_for('!!document.querySelector("[data-connection-error]")')
        b.wait_for('document.activeElement.matches("[data-connection-error]")')
        assert b.evaluate('document.querySelectorAll("[data-connection-error]").length') == 1
        message = b.evaluate('document.querySelector("[data-connection-error]").innerText')
        assert 'could not be confirmed' in message and 'A save may have completed' in message
        assert 'check the record before trying again' in message
        _visible_refusal_by_actions(b, '[data-connection-error]', button)
        assert b.evaluate('offlineForm.isConnected')
        assert b.evaluate('[...new FormData(offlineForm).entries()]') == values
        assert b.evaluate('attemptedRequests') == 1
    finally:
        b.call('Network.emulateNetworkConditions', {
            'offline':False, 'latency':0, 'downloadThroughput':-1, 'uploadThroughput':-1,
        })
    # Reconnection is not consent to submit again.
    b.evaluate('new Promise(resolve=>setTimeout(resolve,250))', await_promise=True)
    assert b.evaluate('attemptedRequests') == 1
    assert b.evaluate('[...new FormData(offlineForm).entries()]') == values
    assert _company_state() == before
