"""Actual browser list labels, escaping, fixed command count and navigation."""
import base64
import json
import socket
import threading
import time
from urllib.parse import urlencode
import pytest
import uvicorn
from bookflow.core.context import client_version
from bookflow.core.config import os_login
from bookflow.commands.host_cmds import start_serving
from tests.test_row5_browser_acceptance import _Cdp, PASSWORD
from tests.test_payment_list_labels import labels, run


@pytest.fixture
def label_site(root, client, labels, monkeypatch):
    from bookflow.adapters.workbench import payments
    calls = []
    original = payments.mount
    def mount(app, **kwargs):
        execute = kwargs['run']
        def counted(request, name, raw, company):
            calls.append(name)
            return execute(request, name, raw, company)
        original(app, **dict(kwargs, run=counted))
    monkeypatch.setattr(payments, 'mount', mount)
    profile = run(client, 'payment show', payment=labels[2][0]['id'])['revision']['profile']
    client.run('user set-password', {'username':os_login(), 'password':PASSWORD})
    company = client.company.list()['items'][0]['company_id']
    listener = socket.socket()
    listener.bind(('127.0.0.1',0)); listener.listen(64)
    port = listener.getsockname()[1]
    handle = start_serving(root, client_version(), bind=f'127.0.0.1:{port}', secure_cookies=False, publish_descriptor=False)
    server = uvicorn.Server(uvicorn.Config(handle.app, log_level='warning', access_log=False))
    thread = threading.Thread(target=lambda:server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic()<deadline:
        time.sleep(.02)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}', company, calls, profile
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        handle.stop()


@pytest.mark.parametrize('width,height', [(1280,900),(390,844)])
def test_captured_list_browser(label_site, labels, client, tmp_path, width, height, monkeypatch):
    base, company, calls, profile = label_site
    payer, method, payments = labels
    expected_labels = [profile['payer']['label'], profile['payment_method']['label']]
    browser = _Cdp(tmp_path/'chrome')
    receipts=[]
    try:
        browser.viewport(width,height)
        browser.navigate(base+'/login')
        browser.evaluate(f'''(() => {{document.querySelector('[name="username"]').value={json.dumps(os_login())}; document.querySelector('[name="password"]').value={json.dumps(PASSWORD)}; document.querySelector('form[hx-post="/login"]').requestSubmit();}})()''')
        browser.wait_for(f'location.href === {json.dumps(base+"/c/"+company+"/")}')
        for q,count in [('LABEL-000',1),('LABEL-',25)]:
            calls.clear()
            browser.navigate(base+'/c/'+company+'/payment?'+urlencode({'q':q,'payment_method':method}))
            browser.wait_for(f'document.querySelectorAll(".payment-invoices tbody tr").length === {count}')
            assert calls == ['company show','payment query','payment-method list']
            observed=browser.evaluate('Array.from(document.querySelectorAll(".payment-invoices tbody tr")).map(r=>[r.querySelector(\'[data-label="Customer"]\').textContent,r.querySelector(\'[data-label="Method"]\').textContent])')
            assert observed == [expected_labels]*count
            assert browser.evaluate('document.querySelectorAll(".payment-invoices payer, .payment-invoices cash").length') == 0
            assert browser.evaluate('document.documentElement.scrollWidth') <= width+1
            receipts.append({'q':q,'count':count,'commands':list(calls),'labels':observed})
        (tmp_path/f'payment-labels-{width}.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot',{'format':'png'})['data']))
        browser.evaluate('document.querySelector(".payment-invoices tbody tr").scrollIntoView()')
        (tmp_path/f'payment-labels-row-{width}.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot',{'format':'png'})['data']))
        next_url=browser.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent==="Next page").href')
        browser.navigate(next_url)
        assert browser.evaluate('document.querySelectorAll(".payment-invoices tbody tr").length') == 1
        record=browser.evaluate('document.querySelector(".payment-invoices tbody a").href')
        browser.navigate(record)
        browser.wait_for('!!document.querySelector("#payment-amount")')
        from bookflow.company import payment_preparation
        decode = payment_preparation._payment_query_labels
        monkeypatch.setattr(payment_preparation, '_payment_query_labels', lambda snapshot, **ids: decode('{PRIVATE-PROFILE', **ids))
        failed = browser.evaluate(f'''(async () => {{ const r = await fetch({json.dumps(base+'/c/'+company+'/payment?q=LABEL-')}); return {{status:r.status, text:await r.text()}}; }})()''', await_promise=True)
        assert failed['status'] == 500
        assert 'E_PAYMENT_PROFILE_INVALID' in failed['text']
        assert 'PRIVATE-PROFILE' not in failed['text'] and 'data-label="Customer"' not in failed['text']
        receipts.append({'error_status':failed['status'], 'safe_error_page':True})
        (tmp_path/'browser-receipt.json').write_text(json.dumps(receipts,indent=2))
    finally:
        browser.close()
