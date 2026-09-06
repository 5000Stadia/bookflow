"""Adopt changed exact recovery recommendations without losing a browser draft.

BOOKFLOW_TEST_RECOVERY_ROOT may identify CLOSED disposable books containing the
403 real Recovery customer installments. They are copied, never served or edited.
Without that cache the same public-command history is built once for both widths.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from bookflow import Client
from bookflow.commands.host_cmds import start_serving
from bookflow.core.config import os_login
from bookflow.core.context import client_version
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp
from tests.test_row8_register_browser import _command, _key
from tests.test_service_sales_browser import _fill, _choose, _click, _preview, _saved, _contained
from tests.test_customer_work_browser import visit

pytestmark = [pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable'), pytest.mark.timeout(600)]


def copy_closed(source, target):
    assert not (source / 'host.json').exists()
    paths = list(source.rglob('*.db'))
    assert paths and all(not Path(str(p)+'-wal').exists() or not Path(str(p)+'-wal').stat().st_size for p in paths)
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    shutil.copytree(source, target)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items())


@pytest.fixture(scope='module')
def fragmented_root(tmp_path_factory):
    root = tmp_path_factory.mktemp('recovery-history') / 'books'
    cached = os.environ.get('BOOKFLOW_TEST_RECOVERY_ROOT')
    if cached:
        source = Path(cached).resolve()
        assert source.is_relative_to('/tmp') and source != root
        copy_closed(source, root)
    client = Client(data_root=str(root))
    if not cached:
        client.init(); client.demo.reset()
    company = client.company.list()['items'][0]['company_id']
    call = lambda name, data: client.run(name, data, company=company, reason='Recovery browser history')
    if not cached:
        customer = call('customer create', dict(name='Recovery customer'))['id']
        income = call('account create', dict(name='Recovery income', type='income'))['id']
        ar = call('account create', dict(name='Recovery AR', type='accounts_receivable'))['id']
        code = next(r['id'] for r in call('sales-tax-code list', {})['items'] if not r['taxable'])
        item = call('item create', dict(name='Recovery labor', description='Recovery service labor', type='service', sales_enabled=True,
            income_account_id=income, price='0.01', sales_tax_code_id=code))['id']
        source = call('estimate create', dict(date='2026-01-12', customer=customer, title='Recovery scope',
            lines=[dict(item=item, quantity='404', net_amount='4.04')]))
        source = call('estimate update', dict(estimate=source['id'], expected_version=1, status='accepted', decision_note='Agreed'))
        line = source['revision']['lines'][0]['line_id']
        version = source['version']
        for index in range(403):
            result = call('estimate invoice', dict(estimate=source['id'], expected_version=version,
                date='2026-01-13', ar_account=ar, conversion_key=f'recovery-browser-{index}',
                selections=[dict(line_id=line, quantity='1')]))
            version = result['source_current']['version']
    customer = call('customer show', dict(customer='Recovery customer'))['id']
    bills = []; cursor = None
    while True:
        page = call('invoice query', dict(customer=customer, status=None, limit=200, **({'cursor':cursor} if cursor else {})))
        bills.extend(page['items'])
        if not page['has_more']: break
        cursor = page['next_cursor']
    bills.sort(key=lambda row: row['created_at'])
    assert len(bills) == 403 and all(r['status'] == 'posted' for r in bills)
    for index in range(0,403,2):
        call('invoice void', dict(invoice=bills[index]['id'], expected_version=1))
    call('company update', dict(progress_billing_enabled=False, close_estimates_after_billing=True))
    client.run('user set-password', dict(username=os_login(), password=PASSWORD))
    return root


@pytest.fixture
def recovery_browser(fragmented_root, tmp_path, monkeypatch):
    root = tmp_path / 'books'; copy_closed(fragmented_root, root)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root)); monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    company = Client(data_root=str(root)).company.list()['items'][0]['company_id']
    listener = socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(64)
    port = listener.getsockname()[1]
    handle = start_serving(root, client_version(), bind=f'127.0.0.1:{port}', publish_descriptor=False)
    server = uvicorn.Server(uvicorn.Config(handle.app, log_level='warning', access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True); thread.start()
    b = None
    try:
        deadline = time.monotonic()+10
        while not server.started and time.monotonic()<deadline: time.sleep(.02)
        assert server.started
        b = _Cdp(tmp_path/'chrome')
        site = SimpleNamespace(base_url=f'http://127.0.0.1:{port}', company_id=company)
        b.navigate(site.base_url+'/login')
        b.evaluate(f'''document.querySelector('[name=username]').value={json.dumps(os_login())};
            document.querySelector('[name=password]').value={json.dumps(PASSWORD)};
            document.querySelector('form[hx-post="/login"]').requestSubmit()''')
        b.wait_for('!!document.querySelector(".group-grid")')
        yield SimpleNamespace(browser=b, site=site)
    finally:
        if b: b.close()
        server.should_exit=True; thread.join(timeout=10); listener.close(); handle.stop()


@pytest.mark.parametrize('width', [1280,390])
def test_stale_recovery_explicit_adoption_retains_draft(recovery_browser, width, tmp_path):
    env = recovery_browser; b = env.browser; b.viewport(width,900)
    call = lambda name, data: _command(b, env.site, name, data, **{'X-Bookflow-Reason':'Recovery browser witness'})
    customer = call('customer.show', dict(customer='Recovery customer'))['id']
    source = call('estimate.query', dict(customer=customer))['items'][0]
    source = call('estimate.show', dict(estimate=source['id']))
    line = source['revision']['lines'][0]['line_id']
    state = call('estimate.billing', dict(estimate=source['id']))
    assert state['lines'][0]['recommended_net_amount']['minor_units'] == 200
    bills = []; cursor = None
    while True:
        page = call('invoice.query', dict(customer=customer, status='posted', limit=200, **({'cursor':cursor} if cursor else {})))
        bills.extend(page['items'])
        if not page['has_more']: break
        cursor = page['next_cursor']
    first_occupied = min(bills, key=lambda row: row['created_at'])
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    visit(b, base+'/estimate/'+source['id']+'/invoice')
    _fill(b,'f:date','2026-01-13'); _choose(b,'f:ar_account','Recovery AR')
    _fill(b,'f:memo','Retain my recovery draft'); _fill(b,'f:number',f'ADOPT-{width}')
    _fill(b,'billing-selection','recovery')
    b.evaluate('document.getElementsByName('+json.dumps('billing-line:'+line)+')[0].click()')
    _preview(b)
    def draft():
        return b.evaluate('Object.fromEntries(new FormData(document.querySelector("[data-sales-form]")))')
    # Even accepting the unchanged recommendation must invalidate a valid preview.
    b.evaluate('document.querySelector("[data-billing-recovery-adopt]").click()')
    assert draft()['f:expected_facts_fingerprint'] == ''
    _preview(b)
    initial = draft()
    call('invoice.void', dict(invoice=first_occupied['id'], expected_version=1))
    state = call('estimate.billing', dict(estimate=source['id']))
    assert state['source_version'] == source['version']
    assert state['lines'][0]['requires_bounded_recovery'] and state['lines'][0]['recommended_net_amount']['minor_units'] == 202
    _click(b,'submit'); b.wait_for('document.body.innerText.includes("E_PREVIEW_STALE") && !document.querySelector(".htmx-request")')
    assert draft()['billing-recovery:'+line] == '2.00'  # Never silently replace a rejected request.
    _click(b,'preview'); b.wait_for('document.body.innerText.includes("E_FEATURE_DISABLED") && !document.querySelector(".htmx-request")')
    assert draft()['billing-recovery:'+line] == '2.00'
    button = 'document.querySelector("[data-billing-recovery-adopt]")'
    assert b.evaluate(button+'.textContent').strip() == 'Use current recommendation: 2.02 USD net'
    b.evaluate(button+'.focus()')
    _key(b, 'Enter')
    assert draft()['billing-recovery:'+line] == '2.02'
    assert draft()['f:expected_facts_fingerprint'] == ''
    for field in ('f:date','f:ar_account','f:memo','f:number','f:conversion_key','f:expected_version','billing-selection','billing-line:'+line):
        assert draft()[field] == initial[field], field
    assert call('estimate.billing', dict(estimate=source['id'])) == state
    _preview(b)
    assert 'This conversion retains active estimate availability.' in b.evaluate('document.body.innerText')
    _contained(b,width)
    b.evaluate('document.querySelector(".billing-selection").scrollIntoView({block:"start"})')
    (tmp_path/f'recovery-adopted-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',dict(format='png'))['data']))
    _click(b,'submit'); _saved(b,'invoice')
    result = call('invoice.show', dict(invoice=f'ADOPT-{width}'))
    assert result['subtotal_minor_units'] == 202 and result['memo'] == initial['f:memo']
    assert len(result['revision']['billing_sources'][0]['allocation_proof']['spans']) == 200
    assert call('estimate.show', dict(estimate=source['id']))['active']
    assert not call('company.show', {})['info']['progress_billing_enabled']
