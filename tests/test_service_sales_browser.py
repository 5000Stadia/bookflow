"""Integrated generated sales forms in actual Chrome; disposable fixture books only."""
import json
import re

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
FP = '[name="f:expected_facts_fingerprint"]'


def _name(b, name):
    match = re.fullmatch(r'(label:)?c:lines:(\d+):(.+)', name)
    if match:
        label, index, field = match.groups()
        suffix = ':' + field
        prefix = 'label:c:' if label else 'c:'
        return b.evaluate(f'''document.querySelectorAll('[data-collection-path=lines] > [data-collection-items] > [data-collection-item]')[{index}]
            .querySelector('[name^="{prefix}"][name$="{suffix}"]').name''')
    return name


def _value(b, name):
    name = _name(b, name)
    return b.evaluate(f'document.getElementsByName({json.dumps(name)})[0].value')


def _fill(b, name, value):
    name = _name(b, name)
    b.evaluate(f'''(() => {{const e=document.getElementsByName({json.dumps(name)})[0];
        e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')


def _choose(b, name, label):
    name = _name(b, name)
    _fill(b, 'label:' + name, label)
    picker = f'document.getElementsByName({json.dumps(name)})[0].closest("[data-reference]")'
    b.wait_for(f'{picker}.querySelectorAll("[role=option]").length > 0')
    b.evaluate(f'''(() => {{const options=[...{picker}.querySelectorAll('[role=option]')];
        const option=options.find(e=>e.textContent.includes({json.dumps(label)}));
        if(!option) throw new Error('Missing choice '+{json.dumps(label)}); option.click();}})()''')
    assert _value(b, name)


def _click(b, action):
    b.evaluate(f'document.querySelector("button[value={action}]").click()')


def _preview(b):
    b.evaluate('void (window.salesPreviewForm = document.querySelector("[data-sales-form]"))')
    _click(b, 'preview')
    b.wait_for(f'!window.salesPreviewForm.isConnected && (!!document.querySelector(".error") || (document.querySelector({json.dumps(FP)})?.value.length === 64))')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), b.evaluate('document.body.innerText')
    fingerprint = b.evaluate(f'document.querySelector({json.dumps(FP)}).value')
    assert re.fullmatch('[0-9a-f]{64}', fingerprint)
    assert b.evaluate('document.querySelector(".sales-document").innerText.includes("Preview — nothing written")')
    return fingerprint


def _contained(b, width):
    assert b.evaluate('document.documentElement.scrollWidth') <= width + 1, b.evaluate('''({width:innerWidth,
        scroll:document.documentElement.scrollWidth, offenders:[...document.querySelectorAll('body *')]
        .filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(e=>({tag:e.tagName,name:e.name,cls:e.className}))})''')


def _saved(b, noun):
    b.wait_for(f'!document.querySelector("[data-generated-form]") && !!document.querySelector(".sales-document") && location.pathname.includes("/{noun}/")')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def _effects(run, sale, control, income, amount, count):
    for account in (control, income):
        page = run('register.query', dict(account=account, date_from='2026-01-01', date_to='2026-12-31'))
        rows = [r for r in page['rows'] if r['kind'] == 'posting' and r['transaction_id'] == sale]
        assert len(rows) == count
        assert sum(r['increase']['minor_units'] - r['decrease']['minor_units'] for r in rows) == amount
        assert all(r['transaction_type'] in ('invoice', 'sales_receipt') for r in rows)


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('noun', ['invoice', 'sales-receipt'])
def test_generated_sale_preview_correct_history_and_void(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)
    b.viewport(width, 900 if width == 1280 else 844)
    income = run('account.create', dict(name='Browser sales income', type='income'))['id']
    if noun == 'invoice':
        run('account.create', dict(name='Browser receivables', type='accounts_receivable'))
    customer = run('customer.create', dict(name='Browser sales customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Browser service', type='service', sales_enabled=True,
        income_account_id=income, price='12.34', description='Captured service description', sales_tax_code_id=code))['id']
    if noun == 'sales-receipt':
        run('payment-method.create', dict(name='Browser cash', kind='cash'))
    base = f'{env.site.base_url}/c/{env.site.company_id}/{noun}'
    b.navigate(base + '/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-01-12')
    _fill(b, 'f:memo', 'Original browser sale')
    _choose(b, 'f:customer', 'Browser sales customer')
    assert _value(b, 'f:customer') == customer
    if noun == 'invoice':
        _choose(b, 'f:ar_account', 'Browser receivables')
    if noun == 'sales-receipt':
        _choose(b, 'f:deposit_to', 'CDP bank')
        _choose(b, 'f:payment_method', 'Browser cash')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Browser service')
    assert _value(b, 'c:lines:0:item') == item
    _fill(b, 'c:lines:0:quantity', '2.5')
    # Leave price defaulted on this row so correction can witness saved origins.
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:1:item', 'Browser service')
    _fill(b, 'c:lines:1:quantity', '2')
    _fill(b, 'c:lines:1:unit_price', '1.11')
    _contained(b, width)
    _preview(b)
    assert '33.07' in b.evaluate('document.querySelector(".sales-document").innerText')
    assert 'Captured service description' in b.evaluate('document.querySelector(".sales-document").innerText')
    _contained(b, width)
    assert run('account.show', dict(account=income))['balance']['minor_units'] == 0
    _fill(b, 'f:memo', 'Original browser sale checked')
    assert _value(b, 'f:expected_facts_fingerprint') == ''
    _click(b, 'submit')
    assert b.evaluate('!!document.querySelector("[data-sales-form]")')
    assert 'Preview again' in b.evaluate('document.querySelector("[data-sales-preview-status]").textContent')
    assert run('account.show', dict(account=income))['balance']['minor_units'] == 0
    _preview(b)
    _click(b, 'submit')
    sale_id = _saved(b, noun)
    selector = noun.replace('-', '_')
    first = run(noun + '.show', {selector: sale_id})
    assert first['version'] == 1 and first['total_minor_units'] == 3307
    control = first['revision']['profile']['control_account']['id']
    _effects(run, sale_id, control, income, 3307, 2)
    _contained(b, width)
    # Navigate using the actual detail action, then preserve captured definitions.
    edit = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.pathname.endsWith("/update"))?.href')
    assert edit, b.evaluate('document.body.innerText')
    run('item.update', dict(item=item, price='99.00', description='New master description'))
    b.navigate(edit)
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'c:lines:0:quantity', '3')
    _fill(b, 'f:memo', 'Corrected browser sale')
    _preview(b)
    assert '39.24' in b.evaluate('document.querySelector(".sales-document").innerText')
    _click(b, 'submit')
    assert _saved(b, noun) == sale_id
    corrected = run(noun + '.show', {selector: sale_id})
    assert corrected['version'] == 2 and corrected['total_minor_units'] == 3924
    for old, new in zip(first['revision']['lines'], corrected['revision']['lines']):
        assert old['line_id'] == new['line_id']
        assert old['item_snapshot'] == new['item_snapshot']
        assert old['unit_price'] == new['unit_price']
    _effects(run, sale_id, control, income, 3924, 6)
    # Historical links originate in the account register, including both revisions.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/account/{control}/register?date_from=2026-01-01&date_to=2026-12-31')
    history_link = json.dumps('a[href*="/' + sale_id + '?revision_number="]')
    b.wait_for(f'Array.from(document.querySelectorAll("#register-history tr[data-kind=posting]")).filter(r=>r.querySelector({history_link})).length === 6')
    links = b.evaluate('Array.from(document.querySelectorAll("#register-history a")).map(a=>a.href)')
    for revision, total, memo in [(1, '33.07', 'Original browser sale checked'), (2, '39.24', 'Corrected browser sale')]:
        target = base + '/' + sale_id + '?revision_number=' + str(revision)
        assert target in links
        b.navigate(target)
        b.wait_for('!!document.querySelector(".sales-document")')
        text = b.evaluate('document.querySelector(".sales-document").innerText')
        assert total in text and memo in text and 'Captured service description' in text
        assert 'New master description' not in text
        _contained(b, width)
    b.navigate(base + '/' + sale_id)
    void_url = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.pathname.endsWith("/void"))?.href')
    assert void_url
    b.navigate(void_url)
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _fill(b, 'ctx:reason', 'Browser duplicate sale')
    _click(b, 'submit')
    _saved(b, noun)
    final = run(noun + '.show', {selector: sale_id})
    assert final['status'] == 'voided' and final['version'] == 3
    _effects(run, sale_id, control, income, 0, 8)
    assert 'Voided' in b.evaluate('document.querySelector(".sales-document").innerText')
    _contained(b, width)


def _error(b, code):
    b.wait_for(f'document.querySelector(".error")?.textContent.includes({json.dumps(code)})')


def _post_error(env, name, payload):
    return env.browser.evaluate(f'''fetch('/companies/{env.site.company_id}/commands/{name}', {{
        method:'POST', credentials:'same-origin', headers:{{'Content-Type':'application/json',
        'X-Bookflow-Workbench':'1', 'X-Bookflow-Client-Name':'bookflow-workbench'}},
        body:JSON.stringify({json.dumps(payload)})}}).then(async r=>({{status:r.status, body:await r.json()}}))''', await_promise=True)


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('noun', ['invoice', 'sales-receipt'])
def test_generated_sale_stale_facts_and_version_retain_draft(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)
    b.viewport(width, 900 if width == 1280 else 844)
    income = run('account.create', dict(name='Conflict income', type='income'))['id']
    customer = run('customer.create', dict(name='Conflict customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Conflict service', type='service', sales_enabled=True,
        income_account_id=income, price='10.00', description='Saved conflict labor', sales_tax_code_id=code))['id']
    extra = {}
    if noun == 'sales-receipt':
        method = run('payment-method.create', dict(name='Conflict cash', kind='cash'))['id']
        extra = dict(deposit_to=env.bank['id'], payment_method=method)
    base = f'{env.site.base_url}/c/{env.site.company_id}/{noun}'
    b.navigate(base + '/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-01-12')
    _fill(b, 'f:memo', 'My unsaved sale')
    _choose(b, 'f:customer', 'Conflict customer')
    if noun == 'sales-receipt':
        _choose(b, 'f:deposit_to', 'CDP bank')
        _choose(b, 'f:payment_method', 'Conflict cash')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Conflict service')
    _fill(b, 'c:lines:0:quantity', '2.5')
    fingerprint = _preview(b)
    payload = dict(date='2026-01-12', customer=customer, memo='My unsaved sale',
        lines=[dict(item=item, quantity='2.5')], **extra)
    api_preview = run(noun + '.post?dry_run=1', payload)
    run('item.update', dict(item=item, price='11.00'))
    # An actual HTTP conflict, with no injected status or altered application response.
    rejected = _post_error(env, noun + '.post', dict(payload,
        expected_facts_fingerprint=api_preview['facts_fingerprint']))
    assert rejected['status'] == 409 and rejected['body']['code'] == 'E_PREVIEW_STALE', rejected
    b.evaluate("""document.addEventListener('htmx:afterRequest', event => {
        window.salesLastResponse = {status:event.detail.xhr.status,
            text:event.detail.xhr.responseText.slice(0, 4000)};
    });""")
    _click(b, 'submit')
    try:
        _error(b, 'E_PREVIEW_STALE')
    except AssertionError as error:
        raise AssertionError(b.evaluate("({response:window.salesLastResponse, status:document.querySelector('[data-sales-preview-status]')?.textContent, fingerprint:document.getElementsByName('f:expected_facts_fingerprint')[0]?.value})")) from error
    assert _value(b, 'c:lines:0:quantity') == '2.5'
    assert _value(b, 'c:lines:0:item') == item
    assert _value(b, 'f:memo') == 'My unsaved sale'
    assert _value(b, 'f:expected_facts_fingerprint') == ''
    assert run('account.show', dict(account=income))['balance']['minor_units'] == 0
    _contained(b, width)
    assert _preview(b) != fingerprint
    assert '27.50' in b.evaluate('document.querySelector(".sales-document").innerText')
    _click(b, 'submit')
    sale_id = _saved(b, noun)
    selector = noun.replace('-', '_')
    first = run(noun + '.show', {selector: sale_id})
    assert first['version'] == 1 and first['total_minor_units'] == 2750
    b.navigate(base + '/' + sale_id + '/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'c:lines:0:quantity', '4')
    _fill(b, 'f:memo', 'Keep my attempted correction')
    _preview(b)
    other = run(noun + '.update', {selector: sale_id, 'expected_version': 1, 'memo': 'Other writer saved'},
        **{'X-Bookflow-Reason': 'Concurrent browser witness'})
    assert other['version'] == 2
    _click(b, 'submit')
    _error(b, 'E_VERSION_CONFLICT')
    assert _value(b, 'f:expected_version') == '1'
    assert _value(b, 'c:lines:0:quantity') == '4'
    assert _value(b, 'f:memo') == 'Keep my attempted correction'
    assert run(noun + '.show', {selector: sale_id})['revision'] == other['revision']
    _contained(b, width)
    # Follow the conflict's review link, then start a fresh versioned correction.
    review = b.evaluate('document.querySelector(".workflow-note[role=alert] a").href')
    b.navigate(review)
    b.wait_for('!!document.querySelector(".sales-document")')
    assert 'Other writer saved' in b.evaluate('document.querySelector(".sales-document").innerText')
    edit = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.pathname.endsWith("/update")).href')
    b.navigate(edit)
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    assert _value(b, 'f:expected_version') == '2'
    _fill(b, 'c:lines:0:quantity', '4')
    _fill(b, 'f:memo', 'Keep my attempted correction')
    _preview(b)
    _click(b, 'submit')
    assert _saved(b, noun) == sale_id
    corrected = run(noun + '.show', {selector: sale_id})
    assert corrected['version'] == 3 and corrected['total_minor_units'] == 4400
    control = corrected['revision']['profile']['control_account']['id']
    _effects(run, sale_id, control, income, 4400, 5)
    _contained(b, width)
