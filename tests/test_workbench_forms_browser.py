"""A person signed in with a cookie session submits a form on each workbench page that used to post natively.

Those forms 403'd with E_WORKBENCH_HEADER: the anti-CSRF header rides only on requests htmx issues.
Each now posts through htmx; here Chrome submits one form per page and the write lands: price rules,
mileage, finance charges, assembly build, pending builds, the budget grid, memorize report (which
navigates to the memorized list), loan payment and billing part of a purchase order.
Set BF_FORM_SHOTS=<dir> to keep a screenshot after each successful submit.
"""
import base64
import json
import os
from pathlib import Path

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

WHY = {'X-Bookflow-Reason': 'form witness seed'}
WATCH = '''void (window.__st = [], document.addEventListener('htmx:afterRequest',
  e => window.__st.push(e.detail.xhr.status)), window.__old = document.querySelector(%s))'''


def _open(b, url, section):
    b.navigate(url)
    b.wait_for(f'!!document.querySelector({json.dumps(section)})')
    b.evaluate(WATCH % json.dumps(section))


def _submit(b, section, button, fill='', *, navigates=False, name=None):
    """Fill the form, click its button, and wait for htmx to swap the section (or navigate away)."""
    if fill:
        b.evaluate(f'(() => {{ const v = (n, x) => {{ const e = document.querySelector(n); e.value = x;'
                   f' e.dispatchEvent(new Event("input", {{bubbles: true}})); }}; {fill} }})()')
    start = b.evaluate('location.pathname')
    b.evaluate(f'document.querySelector({json.dumps(button)}).click()')
    if navigates:
        b.wait_for(f'location.pathname !== {json.dumps(start)} && document.readyState === "complete"', timeout=30)
    else:
        b.wait_for(f'!window.__old.isConnected && !!document.querySelector({json.dumps(section)})', timeout=30)
        assert b.evaluate('window.__st') == [200], (button, b.evaluate('window.__st'),
                                                    b.evaluate('(document.querySelector("[role=alert]") || {}).innerText'))
    text = b.evaluate('document.body.innerText')
    assert 'E_WORKBENCH_HEADER' not in text and 'X-Bookflow-Workbench' not in text, text[:600]
    assert not b.evaluate('!!document.querySelector("[role=alert]")'), (button, text[:900])
    shots = os.environ.get('BF_FORM_SHOTS')
    if shots and name:
        out = Path(shots)
        out.mkdir(parents=True, exist_ok=True)
        for width in (1440, 390):
            b.viewport(width, 900)
            height = b.evaluate('Math.ceil(document.documentElement.scrollHeight)')
            b.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': min(int(height), 8000),
                                                          'deviceScaleFactor': 1, 'mobile': width <= 700})
            data = b.call('Page.captureScreenshot', {'format': 'png'}, timeout=30)['data']
            (out / f'{name}-{width}.png').write_bytes(base64.b64decode(data))
        b.viewport(1280, 900)
    return text


def test_each_formerly_native_form_submits_with_a_cookie_session(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    company = env.site.company_id
    base = f'{env.site.base_url}/c/{company}'

    def run(name, payload):
        return _command(b, env.site, name, payload, **({} if name.endswith('.list') else WHY))

    accounts = {row['type']: row['id'] for row in run('account.list', {})['items']}
    income = run('account.create', {'name': 'FW Sales', 'type': 'income'})['id']
    cogs = accounts.get('cost_of_goods_sold') or run('account.create', {'name': 'FW Cost', 'type': 'cost_of_goods_sold'})['id']
    equity = accounts.get('equity') or run('account.create', {'name': 'FW Equity', 'type': 'equity'})['id']
    exempt = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', {'name': 'FW Fitting', 'type': 'service', 'sales_enabled': True, 'description': 'Fitting',
                        'price': '20.00', 'income_account_id': income, 'sales_tax_code_id': exempt})
    run('customer.create', {'name': 'FW Acme'})

    # Price rules: Check a price.
    _open(b, f'{base}/price-rules', '#price-rules')
    _submit(b, '#price-rules', 'button[value=check]',
            'v("[data-price-check] [name=item]", "FW Fitting"); v("[data-price-check] [name=customer]", "FW Acme");'
            ' v("[data-price-check] [name=date]", "2026-11-02");', name='price-rules')
    assert b.evaluate('!!document.querySelector("[data-price-check-result]")')

    # Mileage: set a rate.
    _open(b, f'{base}/mileage', '#mileage')
    text = _submit(b, '#mileage', 'button[value=rate]',
                   'v("[name=effective_date]", "2026-10-01"); v("[name=rate]", "0.70");')
    assert 'Mileage rate 0.70 from 2026-10-01.' in text, text[:600]

    # Finance charges: recalculate the preview.
    _open(b, f'{base}/finance-charge/assess', '#finance-charges')
    _submit(b, '#finance-charges', 'button[value=preview]', 'v("[name=date]", "2026-10-20");')

    # Assembly build: build one kit.
    tank = run('item.create', {'name': 'FW Tank', 'type': 'inventory_part', 'description': 'Tank', 'price': '200',
                               'purchase_description': 'Tank', 'cost': '100', 'cogs_account_id': cogs,
                               'income_account_id': income})['id']
    kit = run('item.create', {'name': 'FW Kit', 'type': 'inventory_assembly', 'description': 'Kit', 'price': '500',
                              'purchase_description': 'Kit', 'cost': '0', 'cogs_account_id': cogs,
                              'income_account_id': income, 'members': [{'component_item_id': tank, 'quantity': '1'}]})['id']
    run('inventory.adjust', {'item': tank, 'date': '2026-02-01', 'quantity_change': '3', 'value_change': '300.00',
                             'adjustment_account': equity})
    _open(b, f'{base}/assembly/build?item={kit}&date=2026-02-10&quantity=1', '#assembly-build')
    _submit(b, '#assembly-build', 'button[value=build]', 'v("#assembly-build [name=\\"ctx:reason\\"]", "Built one");')
    assert b.evaluate('!!document.querySelector("[data-assembly-result]")')

    # Pending builds: delete a pending one from the list.
    run('assembly.build', {'item': kit, 'date': '2026-02-11', 'quantity': '5', 'pending': True})
    _open(b, f'{base}/assembly/builds?status=pending', '#assembly-builds')
    _submit(b, '#assembly-builds', '[data-assembly-build] button[value=delete]',
            'v("[data-assembly-build] [name=\\"ctx:reason\\"]", "Not needed");')
    assert b.evaluate('!!document.querySelector("[data-assembly-result]")')

    # Budget grid: save one month.
    budget = run('budget.create', {'name': 'FW Plan', 'fiscal_year': 2026})['budget']
    _open(b, f'{base}/budget/{budget["id"]}', '#budget-grid')
    text = _submit(b, '#budget-grid', 'button[value=save]', f'v("[name=\\"m:{income}:0\\"]", "1200.00");'
                   ' const r = document.querySelector("#budget-grid [name=\\"ctx:reason\\"]"); if (r) r.value = "first pass";',
                   name='budget')
    assert 'Saved 1 month amount.' in text, text[:600]

    # Memorize report: saving goes on to the memorized report list.
    _open(b, f'{base}/report/profit-and-loss/memorize?f:date_from=2026-01-01&f:date_to=2026-12-31', '#memorize-report')
    _submit(b, '#memorize-report', '[data-memorize-save]', 'v("#memorize-report [name=name]", "FW year P&L");', navigates=True)
    assert b.evaluate('location.pathname') == f'/c/{company}/memorized-report'
    assert 'FW year P&L' in b.evaluate('document.body.innerText')

    # Loan: write the next payment's check.
    loan_account = run('account.create', {'name': 'FW Loan', 'type': 'long_term_liability'})['id']
    interest = run('account.create', {'name': 'FW Interest', 'type': 'expense'})['id']
    run('journal.post', {'date': '2026-01-02', 'lines': [
        {'account': env.bank['id'], 'side': 'debit', 'amount': '25000.00'},
        {'account': loan_account, 'side': 'credit', 'amount': '25000.00'}]})
    loan = run('loan.create', {'name': 'FW Truck loan', 'liability_account': loan_account, 'interest_account': interest,
                               'principal': '25000.00', 'annual_rate': '6.5', 'term_payments': 60,
                               'first_payment_date': '2026-02-01', 'payment_account': env.bank['id']})['loan']['loan']
    _open(b, f'{base}/loan/{loan["id"]}', '#loan')
    _submit(b, '#loan', '[data-loan-pay] button[type=submit]', 'v("[name=\\"ctx:reason\\"]", "first payment");')
    assert b.evaluate('!!document.querySelector("[data-notice]")')

    # Purchase order: bill part of it.
    vendor = run('vendor.create', {'name': 'FW Supply'})['id']
    po = run('purchase-order.post', {'date': '2026-03-01', 'vendor': vendor,
                                     'lines': [{'item': tank, 'quantity': '10', 'rate': '10.00'}]})
    line = po['revision']['lines'][0]['line_id']
    _open(b, f'{base}/purchase-order/{po["id"]}/bill-part', '#po-bill-part')
    _submit(b, '#po-bill-part', 'button[value=bill]',
            f'v("[name=\\"qty:{line}\\"]", "4"); v("[name=date]", "2026-03-05"); v("[name=\\"ctx:reason\\"]", "Billed 4");')
    assert b.evaluate('!!document.querySelector("[data-notice]")')
