"""Private paycheck entry, save/resume and its hand-worked P1 trace at 390 px."""
import json

import pytest

from bookflow.adapters.workbench.payroll_pages import draft_input
from bookflow.core.errors import BookflowError
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401


def test_payroll_form_exact_cents_and_no_value_in_errors():
    form = {'employee': 'employee', 'period_start': '2026-10-01', 'period_end': '2026-10-14',
            'operation_key': 'form-test', 'taxes_confirmed': 'yes', 'e0_code': 'Regular',
            'e0_quantity': '40', 'e0_rate': '25', 'i0_code': 'Manual tax', 'i0_kind': 'employee_tax',
            'i0_amount': '100.01', 'i0_liability_account': 'tax payable', 'i0_payee': 'Agency',
            'i0_source_note': 'Independently calculated'}
    result = draft_input(form)
    assert result['items'][0]['amount_minor_units'] == 10001
    assert result['earnings'][0] == {'code': 'Regular', 'quantity': '40', 'rate': '25'}
    form['i0_amount'] = '9876.543'
    with pytest.raises(BookflowError) as error:
        draft_input(form)
    assert '9876' not in str(error.value.to_dict())
    assert error.value.code == 'E_VALIDATION'


def _set(browser, selector, value):
    browser.evaluate(f'document.querySelector({json.dumps(selector)}).value = {json.dumps(value)}')


def _submit(browser, selector):
    browser.evaluate('window.payrollSameDocument = true; document.querySelector("#payroll-workspace").dataset.before = "yes"')
    browser.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    browser.wait_for('!!document.querySelector("#payroll-workspace:not([data-before])")', timeout=10)
    assert browser.evaluate('window.payrollSameDocument === true')
    assert not browser.evaluate('!!document.querySelector("[data-payroll-error]")'), browser.evaluate(
        'document.querySelector("[data-payroll-error]").innerText')
    assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_p1_private_save_calculate_and_reopen_at_390(register_browser):
    browser, site = register_browser.browser, register_browser.site
    account = lambda name, kind: _command(browser, site, 'account.create', {'name': name, 'type': kind})['id']
    wages = account('Payroll browser wages', 'expense')
    employer = account('Payroll browser employer cost', 'expense')
    net = account('Payroll browser net payable', 'other_current_liability')
    tax = account('Payroll browser tax payable', 'other_current_liability')
    employee = _command(browser, site, 'employee.create', {'name': 'Payroll browser employee'})['id']
    browser.viewport(390, 900)
    browser.navigate(f'{site.base_url}/c/{site.company_id}/payroll')
    browser.wait_for('!!document.querySelector("#payroll-workspace")')
    assert browser.evaluate('document.querySelector("#payroll-workspace").getAttribute("hx-history")') == 'false'
    for key, value in [('wage_account', wages), ('employer_tax_account', employer), ('net_pay_account', net)]:
        _set(browser, f'[name={key}]', value)
    browser.evaluate('document.querySelector("[name=enabled]").checked = true')
    _set(browser, '[name="ctx:reason"]', 'Configure manual payroll')
    _submit(browser, 'form:has([name=settings_version]) button')
    for key, value in [('employee', employee), ('period_start', '2026-10-01'), ('period_end', '2026-10-14'),
                       ('pay_date', '2026-10-15'), ('e0_code', 'Regular'), ('e0_quantity', '40'), ('e0_rate', '25'),
                       ('i0_code', 'Fictional employee tax'), ('i0_amount', '100'), ('i0_liability_account', tax),
                       ('i0_payee', 'Fictional agency'), ('i0_source_note', 'Fictional test: 1000 times 10 percent'),
                       ('i1_code', 'Fictional employer tax'), ('i1_amount', '50'), ('i1_liability_account', tax),
                       ('i1_payee', 'Fictional agency'), ('i1_source_note', 'Fictional test: 1000 times 5 percent')]:
        _set(browser, f'[data-payroll-form] [name={key}]', value)
    _set(browser, '[data-payroll-form] [name="ctx:reason"]', 'Save manual paycheck')
    browser.evaluate('document.querySelector("[name=taxes_confirmed]").checked = true')
    _submit(browser, '[data-payroll-form] [value=add_earning]')
    assert browser.evaluate('document.querySelector("[name=e0_quantity]").value') == '40'
    assert browser.evaluate('!!document.querySelector("[name=e1_code]")')
    _submit(browser, '[data-payroll-save]')
    draft_id = browser.evaluate('document.querySelector("[data-payroll-identity]").dataset.id')
    _submit(browser, '[data-payroll-calculate]')
    expected = {'gross_minor_units': '1000.00 USD', 'net_minor_units': '900.00 USD',
                'employer_cost_minor_units': '50.00 USD', 'employee_tax_minor_units': '100.00 USD',
                'debit_minor_units': '1050.00 USD', 'credit_minor_units': '1050.00 USD'}
    for key, amount in expected.items():
        assert browser.evaluate(f'document.querySelector("[data-payroll-total={key}]").innerText') == amount
    browser.navigate(f'{site.base_url}/c/{site.company_id}/payroll?draft={draft_id}')
    browser.wait_for('!!document.querySelector("[data-payroll-calculation]")')
    assert browser.evaluate('document.querySelector("[data-payroll-total=net_minor_units]").innerText') == '900.00 USD'
    assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    assert 'Fictional employee tax' not in browser.evaluate('JSON.stringify(localStorage)')


def test_payroll_navigation_has_no_legacy_role_fallback():
    from bookflow.adapters.workbench.pages import EFFECTIVE, _role_allows
    from bookflow.core import registry
    token = EFFECTIVE.set(None)
    try:
        assert not _role_allows(registry.get('payroll settings'), {'role': 'admin'}, hub_admin=True)
        assert not _role_allows(registry.get('payroll settings'), {'role': 'standard'})
    finally:
        EFFECTIVE.reset(token)
