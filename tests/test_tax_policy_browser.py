"""Policy edit, preview, history and responsive readback through actual Chrome."""
import base64
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _value, _preview, _click, _saved, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', [1280, 390])
def test_human_policy_correction(register_browser, width, tmp_path):
    env, b = register_browser, register_browser.browser
    run = lambda name, data: _command(b, env.site, name, data)
    b.viewport(width, 900 if width == 1280 else 844)
    income = run('account.create', dict(name='Tax browser income', type='income'))['id']
    ar = run('account.create', dict(name='Tax browser AR', type='accounts_receivable'))['id']
    liability = run('account.show', dict(account='Sales Tax Payable'))['id']
    initial_tax_balance = run('account.show', dict(account=liability))['balance']['minor_units']
    agency = run('vendor.create', dict(name='Tax browser agency', is_tax_agency=True))['id']
    customer = run('customer.create', dict(name='Tax browser customer'))['id']
    code = next(row['id'] for row in run('sales-tax-code.list', {})['items'] if row['taxable'])
    item = run('item.create', dict(name='Tax browser service', type='service', sales_enabled=True, description='Tax rounding example', price='1.00', sales_tax_code_id=code, income_account_id=income))['id']
    rules = [run('item.create', dict(name='Tax browser '+label, type='sales_tax_item', tax_percent='5', tax_agency_vendor_id=agency, liability_account_id=liability))['id'] for label in ('A', 'Z')]
    group = run('item.create', dict(name='Tax browser group', type='sales_tax_group', members=[dict(component_item_id=rule, quantity='1') for rule in rules]))['id']
    run('company.update', dict(sales_tax_enabled=True))
    original = run('invoice.post', dict(customer=customer, ar_account=ar, date='2026-06-01', sales_tax_item=group,
        lines=[dict(item=item, net_amount='0.10', tax_code=code) for _ in range(2)]))
    assert original['tax_minor_units'] == 2
    base = f'{env.site.base_url}/c/{env.site.company_id}/invoice/{original["id"]}'
    b.navigate(base+'/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    assert _value(b, 'f:sales_tax_calculation') == 'invoice_combined_half_up'
    assert 'Combined tax on taxable total' in b.evaluate('document.querySelector("[name=\\"f:sales_tax_calculation\\"]").selectedOptions[0].textContent')
    _fill(b, 'f:sales_tax_calculation', 'line_component_half_even')
    _fill(b, 'ctx:reason', 'Compare captured tax rounding policies')
    _preview(b)
    _contained(b, width)
    assert run('invoice.show', dict(invoice=original['id']))['tax_minor_units'] == 2
    _click(b, 'submit')
    assert _saved(b, 'invoice') == original['id']
    saved = run('invoice.show', dict(invoice=original['id']))
    assert saved['tax_minor_units'] == 0 and saved['version'] == 2
    assert saved['revision']['tax_calculation_details']['origin']['kind'] == 'explicit'
    b.evaluate('document.querySelector(".tax-details").open = true')
    assert 'Explicit document choice' in b.evaluate('document.querySelector(".tax-details").innerText')
    _contained(b, width)
    b.evaluate('document.querySelector(".tax-details").scrollIntoView()')
    screenshot = b.call('Page.captureScreenshot', {'format':'png'})['data']
    (tmp_path / f'tax-policy-{width}.png').write_bytes(base64.b64decode(screenshot))
    assert run('account.show', dict(account=ar))['balance']['minor_units'] == 20
    assert run('account.show', dict(account=liability))['balance']['minor_units'] == initial_tax_balance
    b.navigate(base+'?revision_number=1')
    b.wait_for('!!document.querySelector(".tax-details")')
    b.evaluate('document.querySelector(".tax-details").open = true')
    assert 'Captured company default' in b.evaluate('document.querySelector(".tax-details").innerText')
    assert '0.02' in b.evaluate('document.querySelector(".sales-document").innerText')
    _contained(b, width)


@pytest.mark.parametrize('width',[1280,390])
def test_print_includes_closed_tax_evidence(register_browser,width,tmp_path):
    import subprocess
    test_human_policy_correction(register_browser,width,tmp_path)
    b=register_browser.browser
    b.navigate(b.evaluate('location.href'))
    b.wait_for('!!document.querySelector(".tax-details")')
    assert not b.evaluate('document.querySelector(".tax-details").open')
    path=tmp_path/f'closed-tax-history-{width}.pdf'
    path.write_bytes(base64.b64decode(b.call('Page.printToPDF',dict(printBackground=True))['data']))
    printed=subprocess.check_output(['pdftotext',str(path),'-'],text=True)
    (tmp_path/f'closed-tax-history-{width}.txt').write_text(printed)
    assert all(text in printed for text in ('Captured company default','Stable tax order: 1','Stable tax order: 2','Tax browser agency','5% on 0.10','0.02'))
    assert not b.evaluate('document.querySelector(".tax-details").open')
