"""Real company page reads library-created note revisions through projected activity."""
import json
from types import SimpleNamespace

import pytest

from bookflow.adapters.http.app import create_app
from bookflow.core.context import client_version
from bookflow.core.host import Host
from tests.test_audit_projection_activity import world, customer, note, edited
from tests.test_row3_host import live as live_server
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.test_service_sales_browser import _contained


@pytest.fixture(scope='module')
def company_site(world, edited):
    client = world['client']
    cid = client.company.show(company='Demo Plumbing Co')['company_id']
    token = client.token.issue(label='disposable-history-browser')['secret']
    host = Host(world['root'], version=client_version())
    host.start()
    server = live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=create_app(host, secure_cookies=False))))
    try:
        yield next(server), cid, token
    finally:
        server.close()
        host.stop()


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.parametrize('width', (1280, 390))
@pytest.mark.timeout(180)
def test_library_note_revisions_visible_in_company_page(company_site, customer, tmp_path, width):
    site, cid, token = company_site
    browser = _Cdp(tmp_path / 'chrome')
    try:
        browser.viewport(width, 850)
        browser.call('Network.enable')
        browser.call('Network.setExtraHTTPHeaders', {'headers': {'Authorization': 'Bearer ' + token}})
        url = site + '/c/' + cid + '/customer/' + customer['id']
        browser.call('Page.navigate', {'url': url}, timeout=60)
        browser.wait_for("document.readyState === 'complete' && location.href === " + json.dumps(url), timeout=60)
        browser.wait_for("document.querySelector('[data-section=notes] [data-items]')?.textContent.includes('Corrected exactly')", timeout=60)
        browser.wait_for("document.querySelector('[data-section=activity] [data-items]')?.textContent.includes('Original <script>')", timeout=60)
        assert browser.evaluate("document.querySelector('[data-section=notes] [data-items]').textContent.includes('Original <script>')") is False
        assert browser.evaluate("document.querySelectorAll('[data-annotations] [data-items] script').length") == 0
        _contained(browser, width)
        timing=browser.evaluate("(() => {const n=performance.getEntriesByType('navigation')[0]; return {response_ms:n.responseEnd-n.startTime,load_ms:n.loadEventEnd-n.startTime};})()")
        print(json.dumps({'width':width,'navigation':timing},sort_keys=True))
    finally:
        browser.close()
