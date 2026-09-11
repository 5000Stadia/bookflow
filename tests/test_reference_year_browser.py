"""Real Chrome, isolated CLI-seeded reference company, desktop and phone register."""
import base64
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_reference_year import reference_template, reference_client  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome is unavailable')


@pytest.fixture
def reference_site(reference_client):
    client, root = reference_client
    login = os_login()
    client.run('user set-password', {'username':login, 'password':PASSWORD})
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1',0)); listener.listen(64)
    address = f'127.0.0.1:{listener.getsockname()[1]}'
    handle = start_serving(root,client_version(),bind=address,secure_cookies=False,publish_descriptor=False)
    server = uvicorn.Server(uvicorn.Config(handle.app,log_level='warning',access_log=False))
    thread = threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started and time.monotonic()<deadline:
            time.sleep(.02)
        assert server.started
        yield SimpleNamespace(base_url='http://'+address,login=login)
    finally:
        server.should_exit=True
        thread.join(timeout=10)
        listener.close()
        handle.stop()


@pytest.mark.parametrize('width,height', [(1280,900),(390,844)])
def test_reference_picker_checking_register(reference_site, tmp_path, width, height):
    site = reference_site
    browser = _Cdp(tmp_path/'chrome-profile')
    try:
        browser.viewport(width,height)
        browser.navigate(site.base_url+'/login')
        browser.evaluate(f"""(() => {{
            document.querySelector('[name=username]').value={json.dumps(site.login)};
            document.querySelector('[name=password]').value={json.dumps(PASSWORD)};
            document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        browser.wait_for("[...document.querySelectorAll('main a')].some(a=>a.textContent==='Reference Plumbing Co')")
        browser.evaluate("[...document.querySelectorAll('main a')].find(a=>a.textContent==='Reference Plumbing Co').click()")
        browser.wait_for("document.querySelector('h1')?.textContent==='Reference Plumbing Co'")
        browser.evaluate("[...document.querySelectorAll('main a')].find(a=>a.getAttribute('href').endsWith('/account')).click()")
        browser.wait_for("[...document.querySelectorAll('tr')].some(r=>r.textContent.includes('Checking'))")
        # The account list stopped carrying a register link of its own when it became
        # the master browser, so this walks where a person now walks: open the account,
        # then take its own primary action by the name printed on it.
        browser.evaluate("[...document.querySelectorAll('#master-results tbody tr')].find(r=>r.textContent.includes('Checking')).querySelector('a').click()")
        browser.wait_for("[...document.querySelectorAll('.actions a')].some(a=>a.textContent==='Open register')")
        browser.evaluate("[...document.querySelectorAll('.actions a')].find(a=>a.textContent==='Open register').click()")
        # $72,550 journal balance + $100 service + $8 tax + $20 exempt.
        browser.wait_for("document.querySelector('#register-current')?.textContent.includes('72678.00')")
        browser.navigate(browser.evaluate('location.href').split('?')[0]+'?date_from=2026-01-01&date_to=2026-12-31')
        browser.wait_for("document.querySelector('#register-period-totals')?.textContent.includes('72678.00')")
        assert 'USD' in browser.evaluate("document.querySelector('#register-current').textContent")
        assert 'Reference Plumbing Co' in browser.evaluate("document.querySelector('.current-company').textContent")
        assert 'REF-' in browser.evaluate("document.querySelector('#register-history').textContent")
        assert browser.evaluate('document.documentElement.scrollWidth') <= width+1
        for part in ('entry','balance','history'):
            if part=='balance':
                browser.evaluate("document.querySelector('#register-current').scrollIntoView({block:'center'})")
            if part=='history':
                browser.evaluate("document.querySelector('#register-history').scrollIntoView({block:'start'})")
            picture=browser.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
            (tmp_path/f'reference-register-{part}-{width}.png').write_bytes(base64.b64decode(picture['data']))
        browser.navigate(site.base_url+'/hub/demo/reset')
        browser.wait_for("!!document.querySelector('[name=\"f:include_reference\"]')")
        assert 'entire existing demo organization and all its companies' in browser.evaluate("document.querySelector('main').textContent")
        browser.evaluate("document.querySelector('[name=\"f:include_reference\"]').value='true'; document.querySelector('button[value=preview]').click()")
        browser.wait_for("document.querySelector('main pre')?.textContent.includes('reference_company_id')")
        preview = json.loads(browser.evaluate("document.querySelector('main pre').textContent"))
        assert preview['dry_run'] and preview['reference_display_name']=='Reference Plumbing Co'
        assert preview['reference_company_id'] != preview['company_id']
        (tmp_path/'chrome-version.json').write_text(json.dumps(browser.call('Browser.getVersion'),indent=2))
    finally:
        browser.close()
