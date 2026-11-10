"""R188 increment 1: enroll a customer and preview the member price at phone width, in headless Chrome.

Both forms submit through htmx. Member widget sells at 19.99; the member level takes 12.5% off:
19.99 x 0.875 = 17.49125, to the cent 17.49.
"""
import json

import pytest
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command
from tests.test_service_sales_browser import _contained

pytestmark = [pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable'), pytest.mark.timeout(900)]


def _set(b, selector, value):
    b.evaluate(f'''(() => {{const e=document.querySelector({json.dumps(selector)});
        e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')


def test_enroll_a_customer_and_preview_the_member_price_at_390(browser_site, tmp_path):
    site = browser_site
    b = _Cdp(tmp_path / 'membership-chrome')
    try:
        b.navigate(site.base_url + '/login')
        b.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        b.wait_for("!!document.querySelector('.nav-group')")
        b.viewport(390, 900)
        run = lambda name, data: _command(b, site, name, data)  # noqa: E731
        income = run('account.create', dict(name='Member widget income', type='income'))['id']
        code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
        run('item.create', dict(name='Member widget', type='service', sales_enabled=True, income_account_id=income,
                                price='19.99', description='Member widget', sales_tax_code_id=code))
        customer = run('customer.create', dict(name='Browser member'))['id']
        run('company.update', dict(enable_price_levels=True, membership_pricing_enabled=True))
        run('price-level.create', dict(name='Browser member level', kind='fixed_percent', percent='-12.5'))
        plan = run('membership-plan.create', dict(name='Browser plan', price_level='Browser member level'))
        record = f'{site.base_url}/c/{site.company_id}/customer/{customer}'
        b.navigate(record)
        link = f'a[href="/c/{site.company_id}/customer/{customer}/membership"]'
        b.wait_for(f'!!document.querySelector({json.dumps(link)})')
        b.navigate(record + '/membership')
        b.wait_for('!!document.querySelector("#membership-enroll")')
        _set(b, '#membership-enroll [name=plan]', plan['id'])
        _set(b, '#membership-enroll [name=start_date]', '2026-11-01')
        _set(b, '#membership-enroll [name=end_date]', '2026-11-30')
        b.evaluate('document.querySelector("#membership-enroll button[type=submit]").click()')
        b.wait_for('(document.querySelector("#membership-workspace .notice")||{}).textContent === "Customer enrolled."')
        assert 'Browser plan' in b.evaluate('document.querySelector("#membership-history").parentElement.innerText')
        _set(b, '#membership-preview [name=item]', 'Member widget')
        _set(b, '#membership-preview [name=quantity]', '3')
        _set(b, '#membership-preview [name=date]', '2026-11-30')
        b.evaluate('document.querySelector("#membership-preview button[type=submit]").click()')
        b.wait_for('!!document.querySelector("#membership-preview-result")')
        said = b.evaluate('document.querySelector("#membership-preview-result").textContent')
        assert said.startswith('17.49 USD on 2026-11-30') and 'member price from plan Browser plan' in said, said
        _contained(b, 390)
        shown = run('customer.membership.show', dict(customer=customer, as_of='2026-11-30'))
        assert shown['effective']['plan']['id'] == plan['id'] and shown['effective']['end_date'] == '2026-11-30'
        assert shown['effective']['customer']['id'] == customer
    finally:
        b.close()
