"""Early-payment discounts in the Receive Payments and Pay Bills windows, driven in real Chrome.

Each window shows the document's discount date and the discount its terms suggest for the
payment date, takes it only when the person presses Take (or types it), and saves a payment
that settles the document by the money plus the discount. What the books say afterwards is
read back through the commands, never from the page.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_customer_payment_browser import click, field, wait
from tests.test_pay_bills_browser import READY, _row, _save, _set

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_receive_payments_takes_the_suggested_discount(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name.replace(' ', '.'), data)
    b.viewport(1280, 900)
    terms = next(row['id'] for row in run('term query', dict(limit=50))['items'] if row['name'] == '2% 10 Net 30')
    income = run('account create', dict(name='Discount browser income', type='income'))['id']
    payer = run('customer create', dict(name='Discount Browser Customer', terms_id=terms))['id']
    method = run('payment-method create', dict(name='Discount cheque', kind='check'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name='Discount browser labor', type='service', sales_enabled=True,
                                   income_account_id=income, price='100', description='Work',
                                   sales_tax_code_id=exempt))['id']
    invoice = run('invoice post', dict(customer=payer, date='2026-06-01', number='DISC-BROWSER-1',
                                       lines=[dict(item=item, quantity='1', net_amount='400.00')]))
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/receive-payments?customer={payer}')
    b.wait_for("document.querySelector('#payment-workspace')?.dataset.loaded==='true'")
    wait(b)
    # 2% of 400.00 is 8.00 through 2026-06-11; the customer paid 392.00 on 2026-06-08.
    field(b, 'date', '2026-06-08'); field(b, 'amount', '392.00'); field(b, 'method', method)
    field(b, 'destination', env.bank['id'])
    click(b, 'load')
    row = f"document.querySelector('[data-invoice=\"{invoice['id']}\"]')"
    assert '8.00' in b.evaluate(f"{row}.querySelector('td[data-label=\"Discount\"]').innerText")
    b.evaluate(f"{row}.querySelector('input[type=checkbox]').click()")
    wait(b)
    b.evaluate(f"""(() => {{const e = {row}.querySelector('input[aria-label^="Payment for"]');
        e.value = '392.00'; e.dispatchEvent(new Event('change', {{bubbles: true}}));}})()""")
    wait(b)
    b.evaluate(f"{row}.querySelector('.payment-take-discount').click()")
    wait(b)
    assert b.evaluate(f"{row}.querySelector('.payment-discount').value") == '8.00'
    click(b, 'preview')
    assert 'discount 8.00 USD' in b.evaluate("document.querySelector('#payment-preview-result').innerText")
    click(b, 'save')
    settled = run('invoice settlement', dict(invoice=invoice['id']))
    assert settled['due_minor_units'] == 0
    assert [(row['amount_minor_units'], row['discount_minor_units']) for row in settled['applications']] == [(40000, 800)]


def test_pay_bills_takes_the_suggested_discount(register_browser):
    env, b = register_browser, register_browser.browser
    run = lambda name, payload: _command(b, env.site, name, payload)
    b.viewport(1280, 900)
    terms = next(row['id'] for row in run('term.query', {'limit': 50})['items'] if row['name'] == '2% 10 Net 30')
    accounts = run('account.list', {})['items']
    bank = next(a for a in accounts if a['type'] == 'bank')['id']
    check = next(m for m in run('payment-method.list', {})['items'] if m['kind'] == 'check')['id']
    expense = run('account.create', {'name': 'Discount browser parts', 'type': 'expense'})['id']
    vendor = run('vendor.create', {'name': 'Discount browser supply', 'terms_id': terms})['id']
    bill = run('bill.post', {'vendor': vendor, 'date': '2026-06-25',
                             'expenses': [{'account': expense, 'amount': '250.00'}]})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/pay-bills?vendor={vendor}')
    b.wait_for(READY)
    _set(b, 'pay-bills-date', '2026-06-30')
    _set(b, 'pay-bills-funding', bank)
    _set(b, 'pay-bills-method', check)
    b.wait_for(f'!!{_row(bill["id"])}')
    # Discount date 2026-07-05; 2% of 250.00 is 5.00 and the payment date is inside the window.
    assert b.evaluate(f'{_row(bill["id"])}.querySelector(`td[data-label="Disc. date"]`).innerText').strip()
    assert '5.00' in b.evaluate(f'{_row(bill["id"])}.querySelector(".pay-bills-discount-hint").textContent')
    b.evaluate(f'{_row(bill["id"])}.querySelector(".pay-bills-take-discount").click()')
    assert b.evaluate(f'{_row(bill["id"])}.querySelector(".pay-bills-discount").value') == '5.00'
    assert b.evaluate(f'{_row(bill["id"])}.querySelector(".pay-bills-amount").value') == '245.00'
    assert b.evaluate('document.querySelector("#pay-bills-discount-total").dataset.discountMinor') == '500'
    _save(b)
    assert 'discount' in b.evaluate('document.querySelector("#pay-bills-result").innerText')
    shown = run('bill.show', {'bill': bill['id']})['settlement_current']
    assert shown['open_minor_units'] == 0
    assert {row['source_type']: row['applied_minor_units'] for row in shown['sources']} == {
        'bill_payment': 24500, 'early_discount': 500}
