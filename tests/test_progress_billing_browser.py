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
    selector = ('.billing-progress-table' if label.startswith('progress-projection') else '[aria-label="Receipt correction payment"]' if label.startswith('receipt-correction') else '[data-allocated-line]' if label == 'retained-proof-extra-charge' else '.billing-selection' if label == 'exact-rebill' else '.billing-table' if label in ('billing-history', 'zero-charge-physical-scope') else '.ledger-table')
    b.evaluate(f'document.querySelector({json.dumps(selector)}).scrollIntoView({{block:"start"}})')
    (tmp_path / f'{label}-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))


def progress_stage(b, identity, stage):
    selector = f'[data-progress-line="{identity}"] [data-progress-stage="{stage}"]'
    return b.evaluate(f'document.querySelector({json.dumps(selector)}).innerText')


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
    assert 'Quantity 0' in progress_stage(b, tiny, 'previous')
    assert 'Quantity 1/2500000' in progress_stage(b, tiny, 'current')
    assert 'Net 0.40' in progress_stage(b, tiny, 'cumulative')
    assert 'Quantity 3/5000000' in progress_stage(b, tiny, 'remaining')
    assert 'Net 0.60' in progress_stage(b, tiny, 'remaining')
    assert 'Quantity 3' in progress_stage(b, regular, 'remaining')
    capture(b, tmp_path, 'progress-projection-mixed', width)
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
    visit(b, url + '/invoice?billing_qty=0&billing_percent=0')
    _fill(b, 'f:date', '2026-01-14'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'percent'); _fill(b, 'billing-percent', '25'); _preview(b)
    assert not b.evaluate('document.querySelector(".billing-progress-table [data-progress-quantity]")')
    assert not b.evaluate('document.querySelector(".billing-progress-table [data-progress-percent]")')
    assert 'billing_qty=0' in b.evaluate('location.search')
    current = run('estimate.show', dict(estimate=source['id']))
    run('estimate.update', dict(estimate=source['id'], expected_version=current['version'], memo='Current source'))
    _click(b, 'submit'); _error(b, 'E_VERSION_CONFLICT')
    assert _value(b, 'billing-percent') == '25'
    # Fresh original-scope percentage can still bill a partly billed line.
    visit(b, url + '/invoice')
    _fill(b, 'f:date', '2026-01-14'); _choose(b, 'f:ar_account', 'Progress receivables')
    _fill(b, 'billing-selection', 'percent'); _fill(b, 'billing-percent', '25'); _preview(b)
    assert 'Net 0.40' in progress_stage(b, tiny, 'previous')
    assert 'Net 0.25' in progress_stage(b, tiny, 'current')
    assert 'Quantity 13/20000000' in progress_stage(b, tiny, 'cumulative')
    assert '65% of original scope' in progress_stage(b, tiny, 'cumulative')
    assert 'Net 0.35' in progress_stage(b, tiny, 'remaining')
    capture(b, tmp_path, 'progress-projection-cumulative', width)
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
    assert 'Quantity 0' in progress_stage(b, regular, 'current')
    assert 'Net 0.00' in progress_stage(b, regular, 'current')
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
@pytest.mark.parametrize('policy,tax_cents,gross_cents', [('line_component_half_even',0,5), (None,1,6)], ids=['explicit-legacy','current-default'])
def test_progress_paid_receipt_and_zero_charge_scope(register_browser, width, tmp_path, policy, tax_cents, gross_cents):
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
        **({'sales_tax_calculation':policy} if policy else {}),
        sales_tax_item=tax, customer_tax_code=taxable,
        lines=[dict(item=item, quantity='3', net_amount='0.10', tax_code=taxable, description='Charged scope'),
               dict(item=item, quantity='1', net_amount='0', description='Free inspection')]))
    base = f'{env.site.base_url}/c/{env.site.company_id}/work-order/{source["id"]}'
    visit(b, base + '/sales-receipt')
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Progress cash'); _fill(b, 'f:amount_received', f'0.{gross_cents:02d}')
    _fill(b, 'billing-selection', 'partial')
    for line in source['revision']['lines']:
        check_line(b, line['line_id']); _fill(b, 'billing-mode:' + line['line_id'], 'percent')
        _fill(b, 'billing-value:' + line['line_id'], '50')
    _preview(b)
    charged = source['revision']['lines'][0]['line_id']
    assert f'Actual tax 0.{tax_cents:02d}' in progress_stage(b, charged, 'current')
    assert f'Gross 0.{gross_cents:02d}' in progress_stage(b, charged, 'cumulative')
    assert f'Forecast tax 0.{tax_cents:02d}' in progress_stage(b, charged, 'remaining')
    assert f'Forecast gross 0.{gross_cents:02d}' in progress_stage(b, charged, 'remaining')
    capture(b, tmp_path, 'progress-projection-tax', width)
    capture(b, tmp_path, 'paid-progress', width)
    _click(b, 'submit'); identity = _saved(b, 'sales-receipt')
    assert run('sales-receipt.show', dict(sales_receipt=identity))['total_minor_units'] == gross_cents
    state = run('work-order.billing', dict(work_order=source['id']))
    assert state['lines'][0]['tax_minor_units'] == 1
    assert state['lines'][0]['billed_tax_minor_units'] == state['lines'][0]['remaining_tax_minor_units'] == tax_cents
    visit(b, base + '/billing')
    assert 'Uncharged physical scope remains' in b.evaluate('document.body.innerText')
    assert 'Amount due 0.00 USD' in b.evaluate('document.body.innerText')
    capture(b, tmp_path, 'zero-charge-physical-scope', width)


@pytest.mark.parametrize('width', [1280, 390])
def test_linked_receipt_correction_requires_exact_received_total(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Correction income', type='income'))['id']
    run('payment-method.create', dict(name='Correction cash', kind='cash'))
    customer = run('customer.create', dict(name='Correction customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Correction labor', type='service', sales_enabled=True,
        income_account_id=income, price='1', description='Correction work', sales_tax_code_id=code))['id']
    source = run('work-order.create', dict(date='2026-01-12', title='Correct paid progress', customer=customer,
        lines=[dict(item=item, quantity='0.000001', net_amount='1')]))
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    visit(b, base + '/work-order/' + source['id'] + '/sales-receipt')
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Correction cash'); _fill(b, 'f:amount_received', '0.40')
    _fill(b, 'billing-selection', 'partial')
    line_id = source['revision']['lines'][0]['line_id']
    check_line(b, line_id); _fill(b, 'billing-mode:' + line_id, 'net_amount')
    _fill(b, 'billing-value:' + line_id, '0.40')
    _preview(b); _click(b, 'submit'); identity = _saved(b, 'sales-receipt')
    receipt_url = base + '/sales-receipt/' + identity
    original = run('sales-receipt.show', dict(sales_receipt=identity))
    original_state = run('work-order.billing', dict(work_order=source['id']))

    visit(b, receipt_url + '/update')
    assert _value(b, 'f:amount_received') == ''  # Never infer an actual receipt of money.
    assert b.evaluate('document.querySelectorAll("[name=\\"f:amount_received\\"]").length') == 1
    assert b.evaluate('document.querySelectorAll("[data-allocated-line]").length') == 1
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:1:item', 'Correction labor'); _fill(b, 'c:lines:1:net_amount', '1')
    _fill(b, 'f:payment_reference', 'Extra charge paid')

    def rejected_preview(amount):
        _fill(b, 'f:amount_received', amount)
        b.evaluate('void (window.rejectedReceiptForm = document.querySelector("[data-sales-form]"))')
        _click(b, 'preview')
        b.wait_for('!window.rejectedReceiptForm.isConnected && !!document.querySelector(".error")')
        assert 'amount_received' in b.evaluate('document.querySelector(".error").innerText')
        assert _value(b, 'f:amount_received') == amount
        assert _value(b, 'c:lines:1:net_amount') == '1'
        assert _value(b, 'f:payment_reference') == 'Extra charge paid'
        assert not _value(b, 'f:expected_facts_fingerprint')
        assert run('sales-receipt.show', dict(sales_receipt=identity))['version'] == original['version']
        _contained(b, width)

    rejected_preview('')
    rejected_preview('1.00')  # Additional charge alone is not the new total.
    _fill(b, 'f:amount_received', '1.40'); _preview(b)
    assert _value(b, 'f:amount_received') == '1.40'
    assert _value(b, 'c:lines:1:net_amount') == '1'
    capture(b, tmp_path, 'receipt-correction-exact-total', width)
    _click(b, 'submit'); assert _saved(b, 'sales-receipt') == identity
    corrected = run('sales-receipt.show', dict(sales_receipt=identity))
    assert corrected['version'] == original['version'] + 1
    assert corrected['total_minor_units'] == 140
    assert corrected['revision']['lines'][0]['quantity'] == '1/2500000'
    assert corrected['revision']['lines'][0]['item_snapshot']['allocation_proof'] == original['revision']['lines'][0]['item_snapshot']['allocation_proof']
    state = run('work-order.billing', dict(work_order=source['id']))
    assert state['remaining_net_minor_units'] == original_state['remaining_net_minor_units'] == 60
    assert state['destinations'][0]['amount_due_minor_units'] == 0

    # Metadata corrections retain compatibility and do not infer a received total.
    visit(b, receipt_url + '/update')
    assert _value(b, 'f:amount_received') == ''
    _fill(b, 'f:payment_reference', 'Confirmed receipt')
    _preview(b); _click(b, 'submit'); assert _saved(b, 'sales-receipt') == identity
    assert run('sales-receipt.show', dict(sales_receipt=identity))['total_minor_units'] == 140

    # Removing the linked line does not erase the receipt's linked-work history.
    visit(b, receipt_url + '/update')
    b.evaluate('document.querySelector("[data-allocated-line]").closest("[data-collection-item]").querySelector("[data-collection-remove]").click()')
    _fill(b, 'f:amount_received', '1.00'); _preview(b); _click(b, 'submit')
    assert _saved(b, 'sales-receipt') == identity
    released = run('sales-receipt.show', dict(sales_receipt=identity))
    assert not released['revision']['billing_sources']
    visit(b, receipt_url + '/update')
    assert not b.evaluate('document.querySelector("[data-allocated-line]")')
    assert 'even after linked lines are removed' in b.evaluate('document.body.innerText')
    _fill(b, 'c:lines:0:net_amount', '2')
    _click(b, 'preview'); _error(b, 'E_VALIDATION')
    assert 'amount_received' in b.evaluate('document.querySelector(".error").innerText')
    assert run('sales-receipt.show', dict(sales_receipt=identity))['version'] == released['version']
    _fill(b, 'f:amount_received', '2.00'); _preview(b); _click(b, 'submit')
    assert _saved(b, 'sales-receipt') == identity
    assert run('sales-receipt.show', dict(sales_receipt=identity))['total_minor_units'] == 200
    assert run('work-order.billing', dict(work_order=source['id']))['remaining_net_minor_units'] == 100
