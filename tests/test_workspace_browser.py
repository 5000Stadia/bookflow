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
