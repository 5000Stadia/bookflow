"""Row 17 browser journeys on disposable books in actual Chrome."""
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _choose, _value, _click, _contained, _error, _preview, _saved
from tests.test_customer_work_browser import visit

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', [1280, 390])
def test_linked_billing_correction_void_and_stale(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data, **headers: _command(b, env.site, name, data, **headers)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Billing income', type='income'))['id']
    run('account.create', dict(name='Billing receivables', type='accounts_receivable'))
    method = run('payment-method.create', dict(name='Billing cash', kind='cash'))['id']
    customer = run('customer.create', dict(name='Billing customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Billing labor', type='service', sales_enabled=True,
        income_account_id=income, price='10', description='Service', sales_tax_code_id=code))['id']
    source = run('estimate.create', dict(date='2026-01-12', title='Kitchen work', customer=customer,
        scope='Original internal scope', lines=[dict(item=item, quantity='2', net_amount='10.01', description='Tap repair'),
            dict(item=item, quantity='1', unit_price='20', description='Drain repair')]))
    source = run('estimate.update', dict(estimate=source['id'], expected_version=1,
        status='accepted', decision_note='Customer agreed'))
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    visit(b, base + '/estimate/' + source['id'] + '/invoice')
    _fill(b, 'f:date', '2026-01-13'); _choose(b, 'f:ar_account', 'Billing receivables')
    _fill(b, 'billing-selection', 'selected')
    first_line = source['revision']['lines'][0]['line_id']
    b.evaluate(f'document.getElementsByName("billing-line:{first_line}")[0].click()')
    key = _value(b, 'f:conversion_key'); assert len(key) >= 32
    _click(b, 'submit'); assert not _value(b, 'f:expected_facts_fingerprint')
    _preview(b); assert _value(b, 'f:conversion_key') == key
    import base64
    (tmp_path / f'billing-preview-{width}.png').write_bytes(base64.b64decode(
        b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))
    _contained(b, width)
    _click(b, 'submit'); invoice = _saved(b, 'invoice')
    assert 'Linked work sources' in b.evaluate('document.body.innerText')
    assert run('invoice.show', dict(invoice=invoice))['total_minor_units'] == 1001
    source_link = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent === "Open captured source revision").href')
    b.call('Page.navigate', {'url': source_link})
    b.wait_for('!!document.querySelector(".work-document")')
    assert 'revision_number=2' in b.evaluate('location.search')
    assert 'Original internal scope' in b.evaluate('document.body.innerText')
    # A work order takes ownership without making the invoice's line available again.
    current = run('estimate.show', dict(estimate=source['id']))
    order = run('estimate.work-order', dict(estimate=source['id'], expected_version=current['version'],
        date='2026-01-14', conversion_key='browser-order'))
    b.navigate(base + '/estimate/' + source['id'])
    b.wait_for('!!document.querySelector("[aria-label=\\"Work billing\\"]")')
    assert b.evaluate(f'!!document.querySelector("a[href=\\"/c/{env.site.company_id}/work-order/{order["id"]}/invoice\\"]")')
    assert 'continue billing from that work order' in b.evaluate('document.body.innerText')
    assert 'Total 10.01 USD · Amount due 10.01 USD' in b.evaluate("document.querySelector('[aria-label=\"Work billing\"]').innerText")
    visit(b, base + '/work-order/' + order['id'] + '/sales-receipt')
    _fill(b, 'f:date', '2026-01-14'); _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Billing cash'); _fill(b, 'f:payment_reference', 'Paid at kitchen')
    _fill(b, 'f:amount_received', '20.00')
    _fill(b, 'billing-selection', 'selected')
    second_line = order['revision']['lines'][1]['line_id']
    b.evaluate(f'document.getElementsByName("billing-line:{second_line}")[0].click()')
    _preview(b)
    # Current payment facts change after preview; keep all entered receipt inputs.
    run('payment-method.update', dict(payment_method=method, name='Billing cash revised'))
    _click(b, 'submit'); _error(b, 'E_PREVIEW_STALE')
    assert _value(b, 'f:payment_reference') == 'Paid at kitchen'
    assert _value(b, 'f:amount_received') == '20.00'
    assert not _value(b, 'f:expected_facts_fingerprint')
    assert b.evaluate(f'document.getElementsByName("billing-line:{second_line}")[0].checked')
    _preview(b); _contained(b, width); _click(b, 'submit'); receipt = _saved(b, 'sales-receipt')
    b.navigate(base + '/work-order/' + order['id'])
    b.wait_for('!!document.querySelector(".work-document")')
    receipt_row = b.evaluate(f"""document.querySelector('a[href="/c/{env.site.company_id}/sales-receipt/{receipt}"]').closest('li').innerText""")
    assert 'Total 20.00 USD · Amount due 0.00 USD' in receipt_row
    visit(b, base + '/invoice/' + invoice + '/void')
    _fill(b, 'ctx:reason', 'Cancel first bill'); _click(b, 'submit'); _saved(b, 'invoice')
    assert 'Voided' in b.evaluate('document.querySelector(".sales-document").innerText')
    # Correct by replacing the linked line with an independent sale; only that root releases.
    visit(b, base + '/sales-receipt/' + receipt + '/update')
    b.evaluate('document.querySelector("[data-collection-path=lines] [data-collection-remove]").click()')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Billing labor'); _fill(b, 'c:lines:0:net_amount', '5.01')
    _fill(b, 'f:amount_received', '5.01')
    _preview(b); _click(b, 'submit'); assert _saved(b, 'sales-receipt') == receipt
    state = run('work-order.billing', dict(work_order=order['id']))
    assert state['remaining_net_minor_units'] == 3001
    b.navigate(base + '/sales-receipt/' + receipt + '/history')
    b.wait_for('!!document.querySelector("[aria-label=\\"Sales history\\"]")')
    assert 'Sources for revision 1' in b.evaluate('document.body.innerText')
    history_link = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent.startsWith("Open captured source revision")).href')
    b.call('Page.navigate', {'url': history_link})
    b.wait_for('!!document.querySelector(".work-document")')
    assert 'revision_number=1' in b.evaluate('location.search')
    b.navigate(base + '/sales-receipt/' + receipt + '/history')
    b.wait_for('!!document.querySelector(".sales-document")')
    snapshots_link = b.evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent === "Read internal snapshots for sales revision 1").href')
    b.navigate(snapshots_link)
    b.wait_for('!!document.querySelector(".sales-document")')
    assert 'Original internal scope' in b.evaluate("""document.querySelector('[aria-label="Linked work sources"]').textContent""")
    _contained(b, width)
    # Both read commands render bounded billing pages.
    for noun, identity in [('estimate', source['id']), ('work-order', order['id'])]:
        visit(b, base + '/' + noun + '/' + identity + '/billing?limit=1')
        assert 'Existing bills' in b.evaluate('document.body.innerText')
        assert 'Total 10.01 USD · Amount due 0.00 USD' in b.evaluate('document.body.innerText')
        assert b.evaluate('!!document.querySelector("a[rel=next]")')
        _contained(b, width)
    # The other two financial command forms also preview real destinations.
    visit(b, base + '/work-order/' + order['id'] + '/invoice')
    _fill(b, 'f:date', '2026-01-15'); _choose(b, 'f:ar_account', 'Billing receivables'); _preview(b)
    independent = run('estimate.create', dict(date='2026-01-12', title='Independent scope', customer=customer,
        lines=[dict(item=item, net_amount='7.01')]))
    run('estimate.update', dict(estimate=independent['id'], expected_version=1, status='accepted', decision_note='Agreed'))
    visit(b, base + '/estimate/' + independent['id'] + '/sales-receipt')
    _fill(b, 'f:date', '2026-01-15'); _choose(b, 'f:deposit_to', 'CDP bank')
    _choose(b, 'f:payment_method', 'Billing cash revised')
    _fill(b, 'f:amount_received', '7.01'); _preview(b); _click(b, 'submit'); _saved(b, 'sales-receipt')


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('noun', ['invoice', 'sales-receipt'])
def test_ordinary_amount_sale_modes_and_conflict(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Amount income', type='income'))['id']
    run('account.create', dict(name='Amount receivables', type='accounts_receivable'))
    run('payment-method.create', dict(name='Amount cash', kind='cash'))
    customer = run('customer.create', dict(name='Amount customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    run('item.create', dict(name='Amount labor', type='service', sales_enabled=True,
        income_account_id=income, price='10', description='Service', sales_tax_code_id=code))
    base = f'{env.site.base_url}/c/{env.site.company_id}/{noun}'
    visit(b, base + '/post')
    _fill(b, 'f:date', '2026-01-12'); _choose(b, 'f:customer', 'Amount customer')
    _choose(b, 'f:ar_account' if noun == 'invoice' else 'f:deposit_to', 'Amount receivables' if noun == 'invoice' else 'CDP bank')
    if noun == 'sales-receipt':
        _choose(b, 'f:payment_method', 'Amount cash')
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    _choose(b, 'c:lines:0:item', 'Amount labor'); _fill(b, 'c:lines:0:quantity', '2')
    _fill(b, 'c:lines:0:net_amount', '10.01'); _preview(b); _click(b, 'submit'); identity = _saved(b, noun)
    selector = noun.replace('-', '_')
    first = run(noun + '.show', {selector: identity})
    assert first['revision']['lines'][0]['unit_price'] is None
    visit(b, base + '/' + identity + '/update')
    assert _value(b, 'c:lines:0:net_amount') == '10.01'
    _fill(b, 'c:lines:0:quantity', '3'); _preview(b)
    run(noun + '.update', {selector: identity, 'expected_version': 1, 'memo': 'Other writer'})
    _click(b, 'submit'); _error(b, 'E_VERSION_CONFLICT')
    assert _value(b, 'c:lines:0:net_amount') == '10.01'
    assert _value(b, 'c:lines:0:quantity') == '3'
    _contained(b, width)
    visit(b, base + '/' + identity + '/update')
    _fill(b, 'c:lines:0:quantity', '3'); _preview(b); _click(b, 'submit'); _saved(b, noun)
    assert run(noun + '.show', {selector: identity})['total_minor_units'] == 1001
    visit(b, base + '/' + identity + '/update')
    mode = b.evaluate('document.querySelector("[name^=price-mode]").name')
    _fill(b, mode, 'manual'); _fill(b, 'c:lines:0:unit_price', '5')
    _preview(b); _click(b, 'submit'); _saved(b, noun)
    changed = run(noun + '.show', {selector: identity})
    assert changed['total_minor_units'] == 1500
    assert changed['revision']['lines'][0]['unit_price']['amount'] == '5.00'
    _contained(b, width)


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('noun', ['estimate', 'work-order'])
def test_generic_billing_card_selects_source_before_preview(register_browser, width, noun):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900)
    income = run('account.create', dict(name='Picker income', type='income'))['id']
    run('account.create', dict(name='Picker receivables', type='accounts_receivable'))
    run('payment-method.create', dict(name='Picker cash', kind='cash'))
    customer = run('customer.create', dict(name='Picker customer'))['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    item = run('item.create', dict(name='Picker labor', type='service', sales_enabled=True,
        income_account_id=income, price='10.01', description='Picker line', sales_tax_code_id=code))['id']
    source = run(noun + '.create', dict(date='2026-01-12', title='Card selected work',
        customer=customer, lines=[dict(item=item)]))
    if noun == 'estimate':
        source = run('estimate.update', dict(estimate=source['id'], expected_version=1,
            status='accepted', decision_note='Customer agreed'))
    base = f'{env.site.base_url}/c/{env.site.company_id}/{noun}'
    for verb in ('invoice', 'sales-receipt', 'billing'):
        url = base + ('/billing' if verb == 'billing' else '/self/' + verb)
        b.navigate(url)
        b.wait_for('!!document.querySelector("[data-billing-source-picker]")')
        assert not b.evaluate('!!document.querySelector(".error")')
        assert not b.evaluate('!!document.querySelector("[name^=billing-line]")')
        _fill(b, 'title', 'Card selected work')
        b.evaluate('document.querySelector("[data-billing-source-picker] button").click()')
        b.wait_for('location.search.includes("title=Card") && !!document.querySelector("[data-billing-source]")')
        link = b.evaluate('document.querySelector("[data-billing-source]").href')
        assert link == base + '/' + source['id'] + '/' + verb
        _contained(b, width)
        visit(b, link)
        assert 'Existing bills' in b.evaluate('document.body.innerText')
        if verb != 'billing':
            assert _value(b, 'f:expected_version') == str(source['version'])
            assert len(_value(b, 'f:conversion_key')) >= 32
            assert b.evaluate('document.querySelectorAll("[name^=billing-line]").length') == 1
            _fill(b, 'f:date', '2026-01-13')
            if verb == 'invoice':
                _choose(b, 'f:ar_account', 'Picker receivables')
            else:
                _choose(b, 'f:deposit_to', 'CDP bank')
                _choose(b, 'f:payment_method', 'Picker cash')
                _fill(b, 'f:amount_received', '10.01')
            _preview(b)
        _contained(b, width)
