"""Actual Chrome company policy and final-billing availability at desktop/phone widths."""
import base64
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command, _key
from tests.test_service_sales_browser import _fill, _choose, _click, _preview, _saved, _contained
from tests.test_customer_work_browser import visit

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', [1280, 390])
def test_policy_forms_final_bill_and_retained_estimate(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    command = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = command('account.create', dict(name='Preference income', type='income'))['id']
    command('account.create', dict(name='Preference receivables', type='accounts_receivable'))
    customer = command('customer.create', dict(name='Preference browser customer'))['id']
    code = next(r['id'] for r in command('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = command('item.create', dict(name='Preference labor', type='service', sales_enabled=True,
        income_account_id=income, price='1', description='Preference work', sales_tax_code_id=code))['id']
    source = command('estimate.create', dict(date='2026-01-12', customer=customer, title='Preference visit',
        lines=[dict(item=item, quantity='1')]))
    source = command('estimate.update', dict(estimate=source['id'], expected_version=1, status='accepted', decision_note='Agreed'))
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base + '/')
    b.wait_for('!!document.querySelector(".group-grid")')
    assert b.evaluate('!!document.querySelector(\'a[href$="/estimate/create"]\')')
    visit(b, base + '/company/self/update')
    assert 'Customer work preferences' in b.evaluate('document.body.innerText')
    for field, value in [('estimates_enabled','false'), ('progress_billing_enabled','false'), ('close_estimates_after_billing','true')]:
        _fill(b, 'f:' + field, value)
    _contained(b, width)
    (tmp_path/f'company-preferences-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))
    _click(b, 'submit')
    b.wait_for('document.body.innerText.includes("Saved successfully")')
    assert not command('company.show', {})['info']['estimates_enabled']
    b.navigate(base + '/')
    b.wait_for('!!document.querySelector(".group-grid")')
    links = b.evaluate('Array.from(document.querySelectorAll("a"), a => a.pathname)')
    assert not any(path.endswith(('/estimate/create', '/estimate/copy', '/proposal/estimate')) for path in links)
    assert base.removeprefix(env.site.base_url) + '/estimate' in links
    assert base.removeprefix(env.site.base_url) + '/work-order/create' in links
    _contained(b, width)
    b.evaluate('Array.from(document.querySelectorAll(".noun-row")).find(e => e.querySelector("h3").textContent === "estimate").scrollIntoView({block:"center"})')
    (tmp_path/f'home-disabled-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))
    b.evaluate('document.querySelector(\'a[href$="/estimate"]\').focus()')
    _key(b, 'Enter')
    b.wait_for('location.pathname.endsWith("/estimate")')
    b.navigate(base + '/estimate')
    b.wait_for('document.readyState === "complete"')
    assert not b.evaluate('Array.from(document.querySelectorAll("a")).some(a=>a.pathname.endsWith("/estimate/create"))')
    b.navigate(base + '/estimate/create')
    b.wait_for('document.body.innerText.includes("E_FEATURE_DISABLED")')
    assert 'Company work preferences' in b.evaluate('document.body.innerText')
    visit(b, base + '/estimate/' + source['id'] + '/invoice')
    assert not b.evaluate('document.getElementsByName("billing-percent").length')
    assert b.evaluate('Array.from(document.getElementsByName("billing-selection")[0].options).map(o=>o.value)') == ['remaining','selected','recovery']
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:ar_account', 'Preference receivables')
    _preview(b)
    assert 'This bill makes the estimate inactive.' in b.evaluate('document.body.innerText')
    _contained(b, width)
    (tmp_path/f'closure-preview-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))
    _click(b, 'submit'); _saved(b, 'invoice')
    assert 'This conversion made the estimate inactive.' in b.evaluate('document.body.innerText')
    assert 'Current source availability: inactive' in b.evaluate('document.body.innerText')
    (tmp_path/f'closure-saved-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {'format':'png'})['data']))
    current = command('estimate.show', dict(estimate=source['id']))
    assert current['status'] == 'accepted' and not current['active']
    b.navigate(base + '/estimate/' + source['id'])
    b.wait_for('!!document.querySelector(".sales-document")')
    assert 'History' in b.evaluate('document.body.innerText')
    assert not b.evaluate('Array.from(document.querySelectorAll("a")).some(a=>a.pathname.endsWith("/copy"))')
