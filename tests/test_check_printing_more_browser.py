"""Print later on Pay Sales Tax and on a customer refund, submitted by a person in a real browser.

Each window carries the anchor's "Print later" box. Ticked and saved through htmx, the remittance
and the refund are saved with no check number and wait in the bank account's print queue, where
the Print Checks page lists them beside each other. Set BF_CHECK_SHOTS=<dir> for 1440 and 390 wide
screenshots of both forms and the queue.
"""
import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_credit_windows_browser import _add_row, _pick, _preview, _save, _set
from tests.test_check_printing_browser import _shot

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _tick(b, name):
    # A checkbox on a document window, a yes/no choice on a generic form.
    b.evaluate(f'''(() => {{const e = [...document.getElementsByName({name!r})].find(x => x.type !== 'hidden');
        if (!e) throw new Error('no Print later control: ' + [...document.querySelectorAll('[name]')].map(x => x.name).join(','));
        if (e.type === 'checkbox') e.checked = true; else e.value = 'true';
        e.dispatchEvent(new Event('change', {{bubbles: true}}));}})()''')


def _submitted_through_htmx(b):
    return b.evaluate('!!document.querySelector("[data-generated-form]")?.getAttribute("hx-post")')


@pytest.mark.timeout(1200)
def test_print_later_on_pay_sales_tax_and_on_a_refund(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    company = env.site.company_id
    base = f'{env.site.base_url}/c/{company}'

    def run(name, payload):
        return _command(b, env.site, name, payload, **{'X-Bookflow-Reason': 'print later witness'})

    def read(name, payload):
        return _command(b, env.site, name, payload)

    bank = env.bank['id']
    liability = next(row['id'] for row in read('account.list', {})['items'] if row.get('system_role') == 'sales_tax_payable')
    run('company.update', {'sales_tax_enabled': True})
    agency = run('vendor.create', {'name': 'PL Revenue Dept', 'is_tax_agency': True,
                                   'address': {'line1': '1 Capitol Way', 'city': 'Springfield', 'state': 'IL', 'postal_code': '62701'}})['id']
    tax = run('item.create', {'name': 'PL six percent', 'type': 'sales_tax_item', 'tax_percent': '6',
                              'tax_agency_vendor_id': agency, 'liability_account_id': liability})['id']
    income = run('account.create', {'name': 'PL sales', 'type': 'income'})['id']
    taxable = next(row['id'] for row in read('sales-tax-code.list', {})['items'] if row['taxable'])
    item = run('item.create', {'name': 'PL visit', 'type': 'service', 'sales_enabled': True, 'description': 'Visit',
                               'income_account_id': income, 'price': '100.00', 'sales_tax_code_id': taxable})['id']
    customer = run('customer.create', {'name': 'PL homeowner'})['id']
    run('invoice.post', {'customer': customer, 'date': '2026-10-01', 'sales_tax_item': tax,
                         'lines': [{'item': item, 'quantity': '3'}]})
    memo = run('credit-memo.post', {'customer': customer, 'date': '2026-10-02', 'sales_tax_item': tax,
                                    'lines': [{'item': item, 'quantity': '1'}]})

    # Pay Sales Tax, Print later ticked.
    b.navigate(f'{base}/sales-tax/pay')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert 'Print later' in b.evaluate('document.body.innerText')
    assert _submitted_through_htmx(b)
    check = next(row['id'] for row in read('payment-method.list', {})['items'] if row['kind'] == 'check')
    _set(b, 'f:agency', agency)
    _set(b, 'f:date', '2026-10-20')
    _set(b, 'f:through_date', '2026-10-01')
    _set(b, 'f:amount', '18.00')
    _set(b, 'f:funding_account', bank)
    _set(b, 'f:method', check)
    _tick(b, 'f:to_be_printed')
    _shot(b, 'pay-sales-tax-print-later')
    _preview(b)
    b.evaluate('document.querySelector("button[value=submit]").click()')
    b.wait_for('document.body.innerText.includes("Saved successfully")')
    paid = read('sales-tax.payment.query', {'agency': agency})['items'][0]['id']

    # A refund of the credit memo, Print later ticked.
    b.navigate(f'{base}/customer-refund/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert 'Print later' in b.evaluate('document.body.innerText')
    assert _submitted_through_htmx(b)
    _set(b, 'f:date', '2026-10-21')
    _add_row(b, 'sources')
    b.wait_for('!!document.querySelector(\'[name^="c:sources:"][name$=":credit_memo"]\')')
    source = b.evaluate('(document.querySelector(\'[name^="c:sources:"][name$=":credit_memo"]\') || {}).name')
    assert source, b.evaluate('[...document.querySelectorAll("[data-generated-form] [name]")].map(x => x.name).join(" ")')
    if b.evaluate(f'!!document.getElementsByName("label:{source}")[0]'):
        _pick(b, source, memo['number'])
    else:
        _set(b, source, memo['id'])
    _pick(b, 'f:funding_account', 'CDP bank')
    _pick(b, 'f:method', 'Check')
    _tick(b, 'f:to_be_printed')
    _shot(b, 'customer-refund-print-later')
    _preview(b)
    refunded = _save(b, 'customer-refund', '.refund-sources')

    shown_tax = read('sales-tax.payment.show', {'payment': paid})
    shown_refund = read('customer-refund.show', {'refund': refunded})
    assert (shown_tax['check_number'], shown_tax['print_status']) == (None, 'queued')
    assert (shown_refund['check_number'], shown_refund['print_status']) == (None, 'queued')

    b.navigate(f'{base}/print-checks?account={bank}')
    b.wait_for('!!document.querySelector("[data-print-queue]")')
    assert b.evaluate(f'!!document.querySelector(\'[data-queued="{shown_tax["id"]}"]\')')
    assert b.evaluate(f'!!document.querySelector(\'[data-queued="{shown_refund["id"]}"]\')')
    text = b.evaluate('document.body.innerText')
    assert 'Pay sales tax' in text and 'Refund' in text
    _shot(b, 'print-checks-tax-and-refund')
