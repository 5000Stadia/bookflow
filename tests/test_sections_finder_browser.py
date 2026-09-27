"""R61 in actual Chrome: the command finder opens from the keyboard and the header, and a
section's "+ New" menu opens as a sheet on a phone."""
import json

import pytest

from tests.test_menu_layout_browser import _signed_in
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _key, _type

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

KEYS = {'ArrowDown': 40, 'ArrowUp': 38, 'Escape': 27}


def _press(b, key, *, ctrl=False):
    code = 'KeyK' if key == 'k' else key
    values = {'key': key, 'code': code, 'modifiers': 2 if ctrl else 0, 'windowsVirtualKeyCode': KEYS.get(key, 75)}
    b.call('Input.dispatchKeyEvent', {'type': 'keyDown', **values})
    b.call('Input.dispatchKeyEvent', {'type': 'keyUp', **values})


def test_the_finder_opens_from_the_keyboard_filters_and_goes(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'finder')
    try:
        b.viewport(1440, 900)
        _signed_in(b, browser_site)
        base = f'{browser_site.base_url}/c/{browser_site.company_id}'
        b.navigate(base + '/_group/customers')
        b.wait_for('!!document.querySelector("[data-new-menu]")')
        # The header shows the finder as a search field with its shortcut.
        assert b.evaluate('document.querySelector("header [data-finder-open]").getClientRects().length > 0')
        b.evaluate('document.querySelector(".section-search input").focus()')
        _press(b, 'k', ctrl=True)
        b.wait_for('document.querySelector("#finder").open && document.activeElement.id === "finder-input"')
        b.wait_for('document.querySelectorAll("#finder-results [role=option]").length > 5')
        _type(b, 'link vendor')
        b.wait_for('[...document.querySelectorAll("#finder-results [role=option]")].length === 2')
        labels = b.evaluate('[...document.querySelectorAll("#finder-results [role=option] a")].map(a => a.textContent)')
        assert labels == ['Link a customer to a vendor', 'Unlink a customer from a vendor'], labels
        _press(b, 'ArrowDown')
        assert b.evaluate('document.querySelector("#finder-input").getAttribute("aria-activedescendant")') == 'finder-option-1'
        _press(b, 'ArrowUp')
        # Escape closes and hands focus back to where it was.
        _press(b, 'Escape')
        b.wait_for('!document.querySelector("#finder").open')
        assert b.evaluate('document.activeElement.matches(".section-search input")')
        # Enter opens the highlighted page.
        _press(b, 'k', ctrl=True)
        b.wait_for('document.querySelector("#finder").open')
        _type(b, 'customer types')
        b.wait_for('document.querySelector("#finder-results [aria-selected=true] a")?.textContent === "Customer types"')
        _key(b, 'Enter')
        b.wait_for('location.pathname.endsWith("/customer-type")')
    finally:
        b.close()


def test_on_a_phone_the_finder_sits_beside_menu_and_new_opens_a_sheet(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'finder-phone')
    try:
        b.viewport(390, 844)
        _signed_in(b, browser_site)
        base = f'{browser_site.base_url}/c/{browser_site.company_id}'
        b.navigate(base + '/_group/customers')
        b.wait_for('!!document.querySelector("[data-new-menu]")')
        assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        finder = b.evaluate('(() => { const r = document.querySelector("header [data-finder-open]").getBoundingClientRect(); return {w: r.width, top: r.top, bottom: r.bottom}; })()')
        assert 0 < finder['w'] <= 48 and finder['bottom'] <= 64, finder
        # The first customers are on the first screen.
        assert b.evaluate('document.querySelector(".section-rows a").getBoundingClientRect().bottom') < 844
        b.evaluate('document.querySelector(".new-menu summary").click()')
        sheet = b.evaluate('(() => { const r = document.querySelector(".new-menu-sheet").getBoundingClientRect(); return {left: r.left, right: r.right, bottom: r.bottom}; })()')
        assert sheet['left'] == 0 and sheet['right'] == 390 and abs(sheet['bottom'] - 844) < 2, sheet
        # A tap on the backdrop outside the sheet closes it.
        b.evaluate('document.querySelector(".new-menu").dispatchEvent(new MouseEvent("click", {bubbles: true}))')
        b.wait_for('!document.querySelector(".new-menu").open')
        b.evaluate('document.querySelector("header [data-finder-open]").click()')
        b.wait_for('document.querySelector("#finder").open')
        box = b.evaluate('(() => { const r = document.querySelector("#finder").getBoundingClientRect(); return {w: r.width, left: r.left}; })()')
        assert box == {'w': 390, 'left': 0}, box
        _type(b, 'invoice')
        b.wait_for('document.querySelectorAll("#finder-results [role=option]").length > 0')
        first = b.evaluate('document.querySelector("#finder-results [role=option] a").textContent')
        assert 'nvoice' in first, first
        assert b.evaluate('document.querySelector("#finder .finder-footer a").getAttribute("href")') == f'/c/{browser_site.company_id}/_all'
    finally:
        b.close()
