"""R177 in the browser: the Receive Payments window writes a bad debt off with a receipt of 0.00.

Entering 0.00 shows the discount account and the reason; the balance typed as the invoice's discount
goes to the expense account named; no cash is recorded. What the books say afterwards is read back
through the commands, never from the page.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_customer_payment_browser import click, field, wait

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_receive_payments_writes_a_balance_off_to_bad_debt(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name.replace(' ', '.'), data, **headers)
    b.viewport(1280, 900)
    income = run('account create', dict(name='Write-off browser income', type='income'))['id']
    run('account create', dict(name='Browser Bad Debt', type='expense'))
    payer = run('customer create', dict(name='Write-off Browser Customer'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name='Write-off browser labor', type='service', sales_enabled=True,
                                   income_account_id=income, price='100', description='Work',
                                   sales_tax_code_id=exempt))['id']
    invoice = run('invoice post', dict(customer=payer, date='2026-06-01', number='WO-BROWSER-1',
                                       lines=[dict(item=item, quantity='1', net_amount='300.00')]))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?customer={payer}')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    assert b.evaluate("document.querySelector('#payment-reason-label').hidden")
    field(b, 'date', '2026-09-30'); field(b, 'amount', '0.00')
    # 0.00 is a write-off: the discount account and the reason appear.
    assert not b.evaluate("document.querySelector('#payment-reason-label').hidden")
    assert not b.evaluate("document.querySelector('#payment-discount-account-label').hidden")
    field(b, 'discount-account', 'Browser Bad Debt'); field(b, 'reason', 'Customer closed; owner approved')
    click(b, 'load')
    row = f"document.querySelector('[data-invoice=\"{invoice['id']}\"]')"
    b.evaluate(f"""(() => {{const e = {row}.querySelector('.payment-discount');
        e.value = '300.00'; e.dispatchEvent(new Event('input', {{bubbles: true}}));}})()""")
    wait(b)
    click(b, 'preview')
    assert not b.evaluate("document.querySelector('#payment-save').disabled")
    click(b, 'save')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    receipt = run('payment query', dict(customer=payer))['items'][0]
    assert receipt['received_minor_units'] == 0
    shown = run('payment show', dict(payment=receipt['id']))
    assert shown['current']['discount_minor_units'] == 30000 and shown['current']['available_minor_units'] == 0
    names = {row['current_account_name']: row['debit']['minor_units'] - row['credit']['minor_units']
             for row in run('report trial-balance', dict(date_to='2026-12-31', limit=200))['rows']}
    assert names['Browser Bad Debt'] == 30000
