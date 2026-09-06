"""Progress billing journeys in real Chrome using disposable company roots."""
import base64
import json
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _choose, _value, _click, _preview, _saved, _contained, _error
from tests.test_customer_work_browser import visit

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def capture(b, tmp_path, label, width):
    _contained(b, width)
    selector = ('[data-allocated-line]' if label == 'retained-proof-extra-charge' else '.billing-selection' if label == 'exact-rebill' else '.billing-table' if label in ('billing-history', 'zero-charge-physical-scope') else '.ledger-table')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).scrollIntoView({{block:"start"}})')
    (tmp_path / f'{label}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))


def check_line(b, identity):
    b.evaluate(f'document.getElementsByName({json.dumps("billing-line:" + identity)})[0].click()')


@pytest.mark.parametrize('width', [1280, 390])
def test_progress_correction_and_exact_rebill(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Progress income', type='income'))['id']
    run('account.create', dict(name='Progress receivables', type='accounts_receivable'))
    customer = run('customer.create', dict(name='Progress customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Progress labor', type='service', sales_enabled=True,
        income_account_id=income, price='1', description='Progress work', sales_tax_code_id=code))['id']
    source = run('estimate.create', dict(date='2026-01-12', title='Fractional progress', customer=customer,
        lines=[dict(item=item, quantity='0.000001', net_amount='1', description='Tiny quoted scope'),
               dict(item=item, quantity='4', unit_price='10', description='Regular scope')]))
    source = run('estimate.update', dict(estimate=source['id'], expected_version=1, status='accepted', decision_note='Agreed'))
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    url = base + '/estimate/' + source['id']
    visit(b, url + '/invoice?billing_qty=1&billing_percent=1')
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'partial')
    tiny, regular = [l['line_id'] for l in source['revision']['lines']]
    for identity, mode, value in [(tiny, 'net_amount', '0.40'), (regular, 'quantity', '1')]:
        check_line(b, identity); _fill(b, 'billing-mode:' + identity, mode); _fill(b, 'billing-value:' + identity, value)
    _preview(b)
    assert '1/2500000' in b.evaluate('document.body.innerText')
    capture(b, tmp_path, 'partial-preview', width)
    _click(b, 'submit'); bill_id = _saved(b, 'invoice')
    bill = run('invoice.show', dict(invoice=bill_id))
    assert bill['total_minor_units'] == 1040
    proof = bill['revision']['billing_sources'][0]['id']
    assert 'Add an unlinked line' in b.evaluate('document.body.innerText')
    link = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent === "Add an unlinked line").href')
    visit(b, link)
    assert b.evaluate('document.querySelectorAll("[data-allocated-line]").length') == 2
    assert not b.evaluate('document.querySelector("[data-allocated-line]").closest("fieldset").querySelector("[name$=quantity]")')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:2:item', 'Progress labor'); _fill(b, 'c:lines:2:net_amount', '2')
    _preview(b); capture(b, tmp_path, 'retained-proof-extra-charge', width)
    _click(b, 'submit'); assert _saved(b, 'invoice') == bill_id
    corrected = run('invoice.show', dict(invoice=bill_id))
    assert corrected['total_minor_units'] == 1240
    assert corrected['revision']['lines'][0]['quantity'] == '1/2500000'
    state = run('estimate.billing', dict(estimate=source['id']))
    assert state['remaining_net_minor_units'] == 3060
    # A source-version change must reject while preserving the user's selection.
    visit(b, url + '/invoice')
    _fill(b, 'f:date', '2026-01-14'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'percent'); _fill(b, 'billing-percent', '25'); _preview(b)
    current = run('estimate.show', dict(estimate=source['id']))
    run('estimate.update', dict(estimate=source['id'], expected_version=current['version'], memo='Current source'))
    _click(b, 'submit'); _error(b, 'E_VERSION_CONFLICT')
    assert _value(b, 'billing-percent') == '25'
    # Fresh original-scope percentage can still bill a partly billed line.
    visit(b, url + '/invoice')
    _fill(b, 'f:date', '2026-01-14'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'percent'); _fill(b, 'billing-percent', '25'); _preview(b)
    _click(b, 'submit'); second = _saved(b, 'invoice')
    assert run('invoice.show', dict(invoice=second))['total_minor_units'] == 1025
    visit(b, base + '/invoice/' + bill_id + '/void')
    _fill(b, 'ctx:reason', 'Release exact scope'); _click(b, 'submit'); _saved(b, 'invoice')
    b.navigate(base + '/invoice/' + bill_id + '?revision_number=1')
    b.wait_for('!!document.querySelector(".sales-document")')
    rebill = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent === "Rebill exact scope from this revision").href')
    b.call('Page.navigate', {'url': rebill})
    b.wait_for('!!document.querySelector("[name=\\"billing-rebill-bill\\"]")')
    assert _value(b, 'billing-rebill:' + tiny) == proof
    _fill(b, 'f:date', '2026-01-15'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'partial'); check_line(b, tiny)
    _fill(b, 'billing-mode:' + tiny, 'rebill_allocation_id'); _preview(b)
    capture(b, tmp_path, 'exact-rebill', width)
    _click(b, 'submit'); rebilled = _saved(b, 'invoice')
    out = run('invoice.show', dict(invoice=rebilled))
    assert out['total_minor_units'] == 40
    assert out['revision']['lines'][0]['quantity'] == '1/2500000'
    # Column choice stays in the URL and never expands the phone body.
    visit(b, url + '/billing?billing_qty=0&billing_percent=0')
    assert not b.evaluate('document.querySelector(".billing-table").innerText.includes("Billed %")')
    capture(b, tmp_path, 'billing-history', width)


@pytest.mark.parametrize('width', [1280, 390])
def test_progress_paid_receipt_and_zero_charge_scope(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Receipt progress income', type='income'))['id']
    run('payment-method.create', dict(name='Progress cash', kind='cash'))
    customer = run('customer.create', dict(name='Receipt progress customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Receipt progress labor', type='service', sales_enabled=True,
        income_account_id=income, price='1', description='Progress work', sales_tax_code_id=code))['id']
    run('company.update', dict(sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
    agency = run('vendor.create', dict(name='Progress tax agency', is_tax_agency=True))['id']
    liability = next(r['id'] for r in run('account.list', {})['items'] if r['system_role'] == 'sales_tax_payable')
    tax = run('item.create', dict(name='Progress ten percent', type='sales_tax_item', tax_percent='10',
        tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    taxable = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if r['taxable'])
    source = run('work-order.create', dict(date='2026-01-12', title='Paid progress', customer=customer,
        sales_tax_item=tax, customer_tax_code=taxable,
        lines=[dict(item=item, quantity='3', net_amount='0.10', tax_code=taxable, description='Charged scope'),
               dict(item=item, quantity='1', net_amount='0', description='Free inspection')]))
    base = f'{env.site.base_url}/c/{env.site.company_id}/work-order/{source["id"]}'
    visit(b, base + '/sales-receipt')
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Progress cash'); _fill(b, 'f:amount_received', '0.05')
    _fill(b, 'billing-selection', 'partial')
    for line in source['revision']['lines']:
        check_line(b, line['line_id']); _fill(b, 'billing-mode:' + line['line_id'], 'percent')
        _fill(b, 'billing-value:' + line['line_id'], '50')
    _preview(b); capture(b, tmp_path, 'paid-progress', width)
    _click(b, 'submit'); identity = _saved(b, 'sales-receipt')
    assert run('sales-receipt.show', dict(sales_receipt=identity))['total_minor_units'] == 5
    state = run('work-order.billing', dict(work_order=source['id']))
    assert state['lines'][0]['tax_minor_units'] == 1
    assert state['lines'][0]['billed_tax_minor_units'] == state['lines'][0]['remaining_tax_minor_units'] == 0
    visit(b, base + '/billing')
    assert 'Uncharged physical scope remains' in b.evaluate('document.body.innerText')
    assert 'Amount due 0.00 USD' in b.evaluate('document.body.innerText')
    capture(b, tmp_path, 'zero-charge-physical-scope', width)
