"""The workbench shell in actual Chrome: the phone menu in the header, focus after a whole-page
submit, and the Banking section's way into the real account registers."""
import json

import pytest

from tests.test_menu_layout_browser import _signed_in
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _key, _tab_to

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _box(browser, selector):
    return browser.evaluate(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                            " return {top: r.top, bottom: r.bottom, left: r.left, right: r.right}; })()")


def test_the_phone_menu_opens_from_the_header_wherever_the_page_is_scrolled(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'shell-menu')
    try:
        b.viewport(390, 844)
        _signed_in(b, browser_site)
        b.navigate(f'{browser_site.base_url}/c/{browser_site.company_id}/')
        b.wait_for('!!document.querySelector("#menu-toggle")')
        # The button is in the header bar, and nothing sits between the header and the page.
        assert b.evaluate('!!document.querySelector("header.app-header #menu-toggle")')
        header = _box(b, 'header.app-header')
        assert header['bottom'] <= 64
        assert _box(b, 'main')['top'] - header['bottom'] < 2
        assert b.evaluate('document.querySelector("#company-navigation").getClientRects().length') == 0
        # In-app links on the Overview carry no off-site arrow.
        assert b.evaluate('getComputedStyle(document.querySelector("a.flow-tile"), "::after").content') in ('none', 'normal')
        # Scrolled down, the menu still opens under the sticky header, over the page.
        b.evaluate('window.scrollTo(0, 900)')
        b.evaluate('document.querySelector("#menu-toggle").click()')
        b.wait_for('document.querySelector("#menu-toggle").getAttribute("aria-expanded") === "true"')
        tile = _box(b, '#company-navigation [data-section=banking]')
        assert header['bottom'] - 1 <= tile['top'] < 844, tile
        assert b.evaluate('document.elementFromPoint(195, %d).closest("#company-navigation") !== null' % (tile['top'] + 20))
        # Keyboard: Escape closes and returns to the button; Tab from the open button enters the menu.
        _key(b, 'Escape')
        b.wait_for('document.querySelector("#menu-toggle").getAttribute("aria-expanded") === "false"')
        assert b.evaluate('document.activeElement.id') == 'menu-toggle'
        _key(b, 'Enter')
        b.wait_for('document.querySelector("#workspace-navigation").hasAttribute("data-open")')
        _key(b, 'Tab')
        assert b.evaluate('document.activeElement.matches("#company-navigation [data-home-link]")')
        # Tabbing past the last section closes the menu instead of wandering under it.
        _tab_to(b, '#company-navigation a[href$="/_all"]')
        _key(b, 'Tab')
        b.wait_for('!document.querySelector("#workspace-navigation").hasAttribute("data-open")')
        assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
    finally:
        b.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_running_a_report_moves_focus_to_its_result_not_the_skip_link(browser_site, tmp_path, width):
    b = _Cdp(tmp_path / f'shell-focus-{width}')
    try:
        b.viewport(width, 844)
        _signed_in(b, browser_site)
        b.navigate(f'{browser_site.base_url}/c/{browser_site.company_id}/report/profit-and-loss')
        run = 'document.querySelector("button[name=action][value=submit]")'
        b.wait_for(f'!!{run}')
        b.evaluate(f'(() => {{ const d = {run}.closest("details"); if (d) d.open = true; {run}.scrollIntoView({{block:"center"}}); {run}.focus(); }})()')
        _key(b, 'Enter')
        b.wait_for('!!document.querySelector("#financial-statement")', timeout=30)
        b.wait_for('document.activeElement !== document.body', timeout=10)
        assert b.evaluate('document.activeElement.closest("#financial-statement") !== null'), \
            b.evaluate('document.activeElement.outerHTML.slice(0, 200)')
        assert not b.evaluate('document.activeElement.matches(".skip-link")')
        # The result is on screen, clear of the sticky header.
        top = b.evaluate('document.activeElement.getBoundingClientRect().top')
        header = _box(b, 'header.app-header')['bottom']
        assert header - 1 <= top < 844 * 0.5, (top, header)
        # The next Tab continues from the result, not from the top of the page.
        _key(b, 'Tab')
        assert not b.evaluate('document.activeElement.matches(".skip-link")')
    finally:
        b.close()


def test_the_banking_register_entry_opens_the_account_registers(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'shell-registers')
    try:
        b.viewport(390, 844)
        _signed_in(b, browser_site)
        base = f'{browser_site.base_url}/c/{browser_site.company_id}'
        b.navigate(base + '/_group/banking')
        b.wait_for('!!document.querySelector("a[href$=\'/_registers\']")')
        # The Banking page no longer offers the generated register helper in place of a register.
        assert not b.evaluate('[...document.querySelectorAll("main a")].some(a => a.getAttribute("href").endsWith("/register/calculate"))')
        b.evaluate('document.querySelector("a[href$=\'/_registers\']").click()')
        b.wait_for('location.pathname.endsWith("/_registers") && !!document.querySelector(".register-tools")')
        groups = b.evaluate('[...document.querySelectorAll(".register-choices h2")].map(h => h.textContent)')
        assert groups[:2] == ['Bank accounts', 'Credit cards'], groups
        names = b.evaluate('[...document.querySelectorAll(".register-choices .register-choice-name")].map(e => e.textContent)')
        assert '1000 · Checking' in names and not any('Income' in n for n in names), names
        # Balances read as money.
        assert b.evaluate('document.querySelector(".register-choices .num").textContent').startswith('$')
        assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        # The generated register commands stay reachable from this page.
        tools = b.evaluate('[...document.querySelectorAll(".register-tools a")].map(a => new URL(a.href).pathname)')
        assert any(t.endswith('/register/calculate') for t in tools), tools
        for tool in tools:
            status = b.evaluate(f'fetch({json.dumps(tool)}, {{credentials: "same-origin"}}).then(r => r.status)', await_promise=True)
            assert status == 200, (tool, status)
        b.evaluate('[...document.querySelectorAll(".register-choices a")].find(a => a.textContent.includes("Checking")).click()')
        b.wait_for('/\\/account\\/[^/]+\\/register$/.test(location.pathname)')
        b.wait_for('!!document.querySelector("h1")')
        _current = b.evaluate('document.querySelector("#company-navigation a[aria-current]")?.dataset.section')
        assert _current == 'banking'
    finally:
        b.close()


def test_the_overview_reads_figures_then_attention_then_activity_then_tasks(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'shell-overview')
    try:
        _signed_in(b, browser_site)
        home = f'{browser_site.base_url}/c/{browser_site.company_id}/'
        tops = '''[...document.querySelectorAll("[data-figure]")].map(a => Math.round(a.getBoundingClientRect().top))'''
        order = '''[".overview-figures", ".overview-attention", ".overview-activity", ".flow-board"].map(
            s => document.querySelector(s).getBoundingClientRect().top)'''
        b.viewport(390, 844)
        b.navigate(home)
        b.wait_for('!!document.querySelector("[data-figure]")')
        figures = b.evaluate(tops)
        assert len(figures) == 5 and figures[0] == figures[1] and figures[2] > figures[1], figures
        assert b.evaluate(order) == sorted(b.evaluate(order))
        assert b.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        b.viewport(1440, 900)
        b.navigate(home)
        b.wait_for('!!document.querySelector("[data-figure]")')
        assert len(set(b.evaluate(tops))) == 1, b.evaluate(tops)
        # The figures and both lists sit on the first screen of a desktop.
        assert b.evaluate('document.querySelector(".overview-activity").getBoundingClientRect().top') < 900
    finally:
        b.close()
