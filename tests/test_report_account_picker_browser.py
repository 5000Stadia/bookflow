"""Report account selection uses names and retains inactive historical accounts."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command  # noqa: F401
from tests.test_service_sales_browser import _choose
from tests.test_basic_report_browser import submit

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.timeout(120)
def test_report_account_picker_active_inactive_and_clear(register_browser):
    env = register_browser
    b = env.browser
    run = lambda name, values: _command(b, env.site, name, values)
    run('journal.post', dict(date='2026-06-05', lines=[
        dict(account=env.bank['id'], side='debit', amount='18.25'),
        dict(account=env.expense['id'], side='credit', amount='18.25')]))
    inactive = run('account.create', dict(name='Retired report account', type='expense'))
    run('account.deactivate', dict(account=inactive['id'], expected_version=1))
    inactive_report = run('report.general-ledger', dict(account=inactive['id'], date_from='2026-06-01', date_to='2026-06-30'))
    root = f'{env.site.base_url}/c/{env.site.company_id}'
    b.viewport(390, 900)
    b.navigate(root + '/report/general-ledger')
    b.wait_for('!!document.querySelector(`[name="label:f:account"]`)')
    _choose(b, 'f:account', env.bank['name'])
    submit(b, dict(date_from='2026-06-01', date_to='2026-06-30'))
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    assert b.evaluate('[...document.querySelectorAll("#report-lines [data-account]")].map(e=>e.dataset.account)') == [env.bank['id']]*3
    _choose(b, 'f:account', inactive['name'])
    assert 'inactive' in b.evaluate('document.querySelector(`[name="f:account"]`).closest("[data-reference]").textContent')
    submit(b, {})
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    assert b.evaluate('[...document.querySelectorAll("#report-lines [data-account]")].map(e=>e.dataset.account)') == [row['account_id'] for row in inactive_report['rows']]
    assert b.evaluate('document.querySelector(`[name="label:f:account"]`).value') .endswith(inactive['name'] + ' (inactive)')
    assert b.evaluate('document.documentElement.scrollWidth===document.documentElement.clientWidth')
    b.evaluate('document.querySelector(`[name="f:account"]`).closest("[data-reference]").querySelector("[data-ref-clear]").click()')
    submit(b, {})
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    assert b.evaluate('document.querySelector(`[name="f:account"]`).value') == ''
    ids = b.evaluate('[...document.querySelectorAll("#report-lines [data-account]")].map(e=>e.dataset.account)')
    assert env.bank['id'] in ids and env.expense['id'] in ids
    # The opt-in must not make inactive choices available in transaction pickers.
    path = root + '/_references/invoice/ar_account?target=account&q=' + inactive['name']
    answer = b.evaluate('fetch('+json.dumps(path)+').then(r=>r.text())')
    assert inactive['id'] not in answer
