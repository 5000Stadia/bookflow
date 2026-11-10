"""Invoice recipient entry and reopened attribution through the phone workbench."""
import json

import pytest
from bookflow.core import registry
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_service_sales_browser import _fill, _choose, _click, _preview, _saved, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def test_invoice_recipient_prompt_cancel_and_reopened_report_at_390px(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 900)

    def run(name, data):
        headers = {'X-Bookflow-Reason': 'Invoice attribution browser journey'} if registry.get(name.replace('.', ' ')).is_write else {}
        return _command(b, env.site, name, data, **headers)

    info = run('company.show', {})
    run('company.update', {'commission_attribution_enabled': True, 'expected_version': info['info_version']})
    income = run('account.create', {'name': 'Attribution income', 'type': 'income'})['id']
    ar = run('account.create', {'name': 'Attribution receivables', 'type': 'accounts_receivable'})['id']
    customer = run('customer.create', {'name': 'Attribution customer'})['id']
    code = next(r['id'] for r in run('sales-tax-code.list', {})['items'] if not r['taxable'])
    recipients = []
    for name, initials in [('Ada Commission', 'AC'), ('Bo Commission', 'BC')]:
        person = run('other-name.create', {'name': name})['id']
        recipients.append(run('sales-rep.create', {'name': name, 'initials': initials, 'name_type': 'other_name', 'name_id': person})['id'])
    run('item.create', {'name': 'Attribution service', 'type': 'service', 'sales_enabled': True,
        'income_account_id': income, 'price': '25.00', 'sales_tax_code_id': code, 'commission_enabled': True})
    disabled = run('item.create', {'name': 'Unattributed service', 'type': 'service', 'sales_enabled': True,
        'income_account_id': income, 'price': '10.00', 'sales_tax_code_id': code})['id']
    legacy = run('invoice.post', {'customer': customer, 'ar_account': ar, 'date': '2026-01-11',
        'lines': [{'item': disabled, 'quantity': '1'}]})['id']
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/{legacy}/update')
    b.wait_for('!!document.querySelector("[data-commission-entry]")')
    _choose(b, 'c:lines:0:item', 'Attribution service')
    b.wait_for('!!document.querySelector(".commission-picker[open]")')
    b.evaluate('document.querySelector("[data-commission-cancel]").click()')
    assert b.evaluate('document.querySelectorAll(".line-body > [data-collection-item]").length') == 1
    assert b.evaluate("""document.querySelector('[data-ref-value][name$=":item"]').value""") == disabled
    assert b.evaluate('document.querySelector("[data-commission-value]").value') == ''
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-commission-entry]")')
    _fill(b, 'f:date', '2026-01-12')
    _choose(b, 'f:customer', 'Attribution customer')
    _choose(b, 'f:ar_account', 'Attribution receivables')
    add = 'document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()'
    rows = 'document.querySelectorAll(".line-body > [data-collection-item]")'
    b.evaluate(add)
    _choose(b, 'c:lines:0:item', 'Unattributed service')
    b.wait_for('document.querySelector("[data-commission-edit]").textContent === "No commission"')
    assert not b.evaluate('!!document.querySelector(".commission-picker[open]")')
    b.evaluate('document.querySelector(".line-body [data-collection-remove]").click()')
    b.evaluate(add)
    _choose(b, 'c:lines:0:item', 'Attribution service')
    b.wait_for('!!document.querySelector(".commission-picker[open]")')
    _contained(b, 390)
    b.evaluate('document.querySelector("[data-commission-cancel]").click()')
    assert b.evaluate(rows + '.length') == 0
    for index, name in enumerate(['Ada Commission', 'Bo Commission']):
        b.evaluate(add)
        _choose(b, f'c:lines:{index}:item', 'Attribution service')
        b.wait_for('!!document.querySelector(".commission-picker[open]")')
        b.evaluate(f'''[...document.querySelectorAll('[data-commission-choices] label')]
            .find(e=>e.textContent.includes({json.dumps(name)})).querySelector('input').click()''')
        b.evaluate('document.querySelector("[data-commission-select]").click()')
        _fill(b, f'c:lines:{index}:quantity', '1')
    before = b.evaluate('[...document.querySelectorAll("[data-commission-value]")].map(e=>e.value)')
    b.evaluate('document.querySelector("[data-commission-edit]").click()')
    b.wait_for('!!document.querySelector(".commission-picker[open]")')
    b.evaluate('document.querySelector("[data-commission-cancel]").click()')
    assert b.evaluate('[...document.querySelectorAll("[data-commission-value]")].map(e=>e.value)') == before
    _fill(b, 'ctx:reason', 'Record invoice commission attribution')
    _preview(b)
    _click(b, 'submit')
    invoice = _saved(b, 'invoice')
    saved = run('invoice.show', {'invoice': invoice})
    assert [r['item_snapshot']['commission']['recipients'][0]['recipient_label']
        for r in saved['revision']['lines']] == ['Ada Commission', 'Bo Commission']
    run('sales-rep.deactivate', {'sales_rep': recipients[0], 'expected_version': 1})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/{invoice}/update')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:memo', 'Keep historical recipient on correction')
    _fill(b, 'c:lines:1:quantity', '2')
    _fill(b, 'ctx:reason', 'Correct invoice quantity')
    _preview(b)
    _click(b, 'submit')
    assert _saved(b, 'invoice') == invoice
    corrected = run('invoice.show', {'invoice': invoice})
    assert corrected['revision']['lines'][0]['item_snapshot']['commission']['recipients'][0]['recipient_label'] == 'Ada Commission'
    report = f'{env.site.base_url}/c/{env.site.company_id}/report/commission-attribution'
    for _ in range(2):
        b.navigate(report)
        b.wait_for('!!document.getElementsByName("f:date_from")[0]')
        _fill(b, 'f:date_from', '2026-01-01')
        _fill(b, 'f:date_to', '2026-12-31')
        _click(b, 'submit')
        b.wait_for('!!document.querySelector("[data-commission-report]")')
        text = b.evaluate('document.querySelector("[data-commission-report]").innerText')
        assert 'Ada Commission' in text and 'Bo Commission' in text and '$25.00' in text
        _contained(b, 390)
