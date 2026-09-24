"""The desktop menu collapses to icons or moves to a top bar, and remembers it; phones keep the tile menu."""
import json

from tests.test_row5_browser_acceptance import PASSWORD, _Cdp, browser_site  # noqa: F401

NAV = "document.querySelector('#company-navigation')"


def _signed_in(browser, site):
    browser.navigate(site.base_url + "/login")
    browser.evaluate(f"""(() => {{
      document.querySelector('[name="username"]').value = {json.dumps(site.login)};
      document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
      document.querySelector('form[hx-post="/login"]').requestSubmit();
    }})()""")
    browser.wait_for("!!document.querySelector('.nav-group')")


def _box(browser, selector):
    return browser.evaluate(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect();"
                            " return {left: r.left, top: r.top, width: r.width, height: r.height}; })()")


def test_the_desktop_menu_collapses_moves_and_remembers(browser_site, tmp_path):
    browser = _Cdp(tmp_path / "menu")
    try:
        browser.viewport(1280, 900)
        _signed_in(browser, browser_site)
        home = f"{browser_site.base_url}/c/{browser_site.company_id}/"
        side = _box(browser, "#company-navigation")
        assert side["width"] > 180 and _box(browser, "main")["left"] >= side["width"]

        browser.evaluate("document.querySelector('[data-nav-collapse]').click()")
        browser.wait_for(f"{NAV}.getBoundingClientRect().width < 80")
        assert browser.evaluate("document.querySelector('[data-nav-collapse]').getAttribute('aria-pressed')") == "true"
        # Icons only: each section still names itself, to assistive technology and on hover.
        assert browser.evaluate("document.querySelector('[data-section=customers] .nav-label').textContent") == "Customers"
        assert browser.evaluate("document.querySelector('[data-section=customers]').title") == "Customers"
        assert _box(browser, "main")["left"] < 80

        browser.navigate(home)
        browser.wait_for(f"{NAV}.getBoundingClientRect().width < 80")

        browser.evaluate("document.querySelector('[data-nav-collapse]').click()")
        browser.evaluate("document.querySelector('[data-nav-top]').click()")
        browser.wait_for(f"{NAV}.getBoundingClientRect().width > 1000")
        bar = _box(browser, "#company-navigation")
        assert bar["height"] < 120 and _box(browser, "main")["top"] >= bar["top"] + bar["height"]
        assert _box(browser, "main")["left"] < 40
        browser.navigate(home)
        browser.wait_for(f"{NAV}.getBoundingClientRect().width > 1000")
        assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

        browser.evaluate("document.querySelector('[data-nav-top]').click()")
        browser.wait_for(f"{NAV}.getBoundingClientRect().width > 180 && {NAV}.getBoundingClientRect().width < 300")
    finally:
        browser.close()


def test_a_phone_keeps_the_tile_menu_without_layout_controls(browser_site, tmp_path):
    browser = _Cdp(tmp_path / "menu-phone")
    try:
        browser.viewport(390, 844)
        _signed_in(browser, browser_site)
        # A desktop preference saved on this device must not change the phone menu.
        browser.evaluate("localStorage.setItem('bookflow.menu', JSON.stringify({collapsed: true, top: true}))")
        browser.navigate(f"{browser_site.base_url}/c/{browser_site.company_id}/")
        browser.evaluate("document.getElementById('workspace-navigation').open = true")
        assert browser.evaluate("getComputedStyle(document.querySelector('.nav-controls')).display") == "none"
        tile = _box(browser, "[data-section=customers]")
        assert tile["height"] >= 44 and tile["width"] >= 90
        assert browser.evaluate("getComputedStyle(document.querySelector('[data-section=customers] .nav-label')).position") != "absolute"
        assert browser.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    finally:
        browser.close()
