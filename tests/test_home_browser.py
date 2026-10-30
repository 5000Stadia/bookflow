"""R191: the Home button and *Make this page my Home* in real headless Chrome at a phone's 390 pixels.

A person on a report opens the account menu, makes the report their Home, goes elsewhere, and the house at the left
of the header brings them back. Nothing scrolls sideways.
"""
import json

import pytest

from tests.test_item_pictures_browser import _fits, _shot
from tests.test_row3_host import PASSWORD
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_make_this_page_my_home_then_the_home_button_returns_there(browser_site, tmp_path):
    b = _Cdp(tmp_path / 'home-chrome')
    try:
        b.viewport(390, 844)
        b.navigate(browser_site.base_url + '/login')
        b.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        b.wait_for("location.pathname.startsWith('/c/') && !!document.querySelector('[data-home-button]')")
        report = f'{browser_site.base_url}/c/{browser_site.company_id}/report/profit-and-loss'
        b.navigate(report)
        _fits(b)
        assert b.evaluate("document.querySelector('[data-home-button]').getBoundingClientRect().left < 80")
        b.evaluate("document.querySelector('details.account-menu > summary').click()")
        b.wait_for("document.querySelector('details.account-menu').open")
        b.evaluate("document.querySelector('[data-make-home]').click()")
        b.wait_for("document.querySelector('#make-home-status').textContent.includes('This page is now your Home.')")
        _shot(b, 390, 'make-home')
        b.navigate(f'{browser_site.base_url}/c/{browser_site.company_id}/customer')
        _fits(b)
        b.evaluate("document.querySelector('[data-home-button]').click()")
        b.wait_for(f"location.href === {json.dumps(report)} && document.readyState === 'complete'")
        _fits(b)
    finally:
        b.close()
