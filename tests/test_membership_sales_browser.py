"""R188 increment 2 in headless Chrome at 1440 and 390 px: the Members view of the customer list, a bulk
enrollment reversed as one unit, a job that stops inheriting, and a sales receipt for a job priced as a member.

Every form submits through htmx. Member widget sells at 19.99; the member level takes 12.5% off:
19.99 x 0.875 = 17.49125, to the cent 17.49; three of them 52.47. At the ordinary price three are 59.97.
"""
import json

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _choose, _contained, _fill, _preview, _saved, _click

pytestmark = [pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable'), pytest.mark.timeout(900)]


def _set(b, selector, value):
    b.evaluate(f'''(() => {{const e=document.querySelector({json.dumps(selector)});
        e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')


def _text(b, selector):
    return b.evaluate(f'(document.querySelector({json.dumps(selector)})||{{}}).innerText || ""')


@pytest.mark.parametrize('width', [1440, 390])
def test_members_view_bulk_reversal_and_a_job_sale_at_member_price(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    run = lambda name, data: _command(b, site, name, data)  # noqa: E731
    b.viewport(width, 900 if width == 1440 else 844)
    income = run('account.create', dict(name='Member widget income', type='income'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', dict(name='Member widget', type='service', sales_enabled=True, income_account_id=income,
                            price='19.99', description='Member widget', sales_tax_code_id=code))
    run('payment-method.create', dict(name='Member cash', kind='cash'))
    parent = run('customer.create', dict(name='Browser member parent'))['id']
    job = run('customer.create', dict(name='Browser member job', parent_id=parent))['id']
    for name in ('Bulk browser one', 'Bulk browser two'):
        run('customer.create', dict(name=name))
    run('company.update', dict(enable_price_levels=True, membership_pricing_enabled=True))
    run('price-level.create', dict(name='Browser member level', kind='fixed_percent', percent='-12.5'))
    plan = run('membership-plan.create', dict(name='Browser plan', price_level='Browser member level'))['id']
    run('customer.membership.set', dict(customer=parent, plan=plan, start_date='2020-01-01'))
    base = f'{site.base_url}/c/{site.company_id}'

    # The customer list offers the Members view only while the layer is on.
    b.navigate(base + '/customer')
    link = f'a[href="/c/{site.company_id}/memberships"]'
    b.wait_for(f'!!document.querySelector({json.dumps(link)})')
    b.evaluate(f'document.querySelector({json.dumps(link)}).click()')
    b.wait_for('!!document.querySelector("#members-list")')
    _set(b, '#members-filter [name=plan]', plan)
    _set(b, '#members-filter [name=as_of]', '2026-11-15')
    b.evaluate('document.querySelector("#members-filter button[type=submit]").click()')
    b.wait_for('location.search.includes("plan=")')
    b.wait_for('!!document.querySelector("#members-list")')
    listed = _text(b, '#members-list')
    assert 'Browser member parent' in listed and 'Own enrollment' in listed, listed
    assert 'Browser member job' in listed and 'Inherited from Browser member parent' in listed, listed
    _contained(b, width)

    # Two customers enrolled as one batch, then that batch reversed whole.
    _set(b, '#members-enroll [name=customers]', 'Bulk browser one\nBulk browser two')
    _set(b, '#members-enroll [name=plan]', plan)
    _set(b, '#members-enroll [name=start_date]', '2026-11-01')
    b.evaluate('document.querySelector("#members-enroll button[type=submit]").click()')
    b.wait_for('!!document.querySelector("#members-enrolled")')
    assert 'Enrolled 2 customers' in _text(b, '#members-enrolled')
    assert len(run('customer.membership.list', dict(plan=plan, as_of='2026-11-15'))['items']) == 4
    _contained(b, width)
    b.evaluate('window.confirm = () => true')
    b.evaluate('document.querySelector("#members-reverse button[type=submit]").click()')
    b.wait_for('!!document.querySelector("#members-reversed")')
    assert '2 enrollments removed' in _text(b, '#members-reversed')
    left = run('customer.membership.list', dict(plan=plan, as_of='2026-11-15'))['items']
    assert {r['customer']['id'] for r in left} == {parent, job}

    # The job's sales receipt takes the parent's member price: 3 x 17.49 = 52.47.
    b.navigate(base + '/sales-receipt/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-11-15')
    _choose(b, 'f:customer', 'Browser member job')
    _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Member cash')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Member widget')
    _fill(b, 'c:lines:0:quantity', '3')
    _preview(b)
    shown = b.evaluate('document.querySelector(".sales-document").innerText')
    assert '17.49' in shown and '52.47' in shown, shown
    _contained(b, width)
    _click(b, 'submit')
    sale = _saved(b, 'sales-receipt')
    saved = run('sales-receipt.show', dict(sales_receipt=sale))
    assert saved['total_minor_units'] == 5247
    assert saved['revision']['profile']['membership']['owner']['id'] == parent

    # The job stops inheriting on its membership panel; its next sale is at the ordinary price.
    b.navigate(f'{base}/customer/{job}/membership')
    b.wait_for('!!document.querySelector("#membership-inherit-form")')
    assert 'Inherited from' in _text(b, '#membership-effective')
    b.evaluate('document.querySelector("#membership-inherit-form button[type=submit]").click()')
    b.wait_for('(document.querySelector("#membership-workspace .notice")||{}).textContent === '
               '"This job no longer inherits a membership."')
    assert 'does not inherit' in _text(b, '#membership-inherits')
    _contained(b, width)
    priced = run('price-rule.preview', dict(item='Member widget', customer=job, quantity='3', date='2026-11-15'))
    assert priced['unit_price'] == '19.99'
