"""Actual authenticated hub-history pages, pagination and detail in Chrome."""
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from bookflow.adapters.http.app import create_app
from tests.test_history_cursors import world
from tests.test_row3_host import live as live_server
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.test_service_sales_browser import _contained


@pytest.fixture
def history_site(world):
    host, *_ = world
    app = create_app(host, secure_cookies=False)
    yield from live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=app)))


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.parametrize('width', (1280, 390))
@pytest.mark.timeout(180)
def test_authenticated_history_pages(world, history_site, tmp_path, width):
    _, _, first, second = world
    browser = _Cdp(tmp_path / 'chrome')
    try:
        browser.viewport(width, 850)
        browser.call('Network.enable')
        browser.call('Network.setExtraHTTPHeaders', {'headers': {'Authorization': 'Bearer secret-R'}})

        def visit(url):
            browser.call('Page.navigate', {'url': url}, timeout=60)
            browser.wait_for("document.readyState === 'complete' && location.href === " + json.dumps(url), timeout=60)

        visit(history_site + '/hub/audit?limit=1')
        assert browser.evaluate('document.querySelector("h1").textContent') == 'Hub audit'
        rows = browser.evaluate('[...document.querySelectorAll("table tr td:first-child a")].map(a=>a.pathname)')
        assert rows == ['/hub/audit/' + second]
        _contained(browser, width)
        older = browser.evaluate('document.querySelector("a[rel=next]").href')
        bookmark = parse_qs(urlparse(older).query)['before'][0]
        assert bookmark and not bookmark.isdecimal()
        visit(older)
        assert browser.evaluate('[...document.querySelectorAll("table tr td:first-child a")].map(a=>a.pathname)') == ['/hub/audit/' + first]
        detail = browser.evaluate('document.querySelector("table tr td:first-child a").href')
        visit(detail)
        text = browser.evaluate('document.body.innerText')
        assert first in text and 'projection_version' in text
        assert 'high_water' not in text and 'session_id' not in text
        _contained(browser, width)
    finally:
        browser.close()
