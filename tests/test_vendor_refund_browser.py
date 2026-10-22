"""A vendor's refund check recorded from the vendor credit's own page, in a real browser (R149).

The journey: a 40.00 credit is open; the saved credit offers "Record refund check"; the form it
opens names the credit and nothing of the credit's own number or memo; a 15.00 check is deposited;
the credit then has 25.00 free and lists the refund deposit it answers, linked to that deposit.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401
from tests.test_credit_windows_browser import _books, _read, _set, _text
from tests.test_service_sales_browser import _click as _action, _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _click(b, selector):
    b.evaluate(f'''(() => {{const e = document.querySelector({json.dumps(selector)});
        if (!e) throw new Error('nothing matches ' + {json.dumps(selector)}); e.click();}})()''')


@pytest.mark.parametrize('width', [1280, 390])
def test_a_refund_check_is_recorded_from_the_credit_and_the_credit_keeps_the_rest(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'Refvc')
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    credit = books['run']('vendor-credit.post', {
        'vendor': books['vendor'], 'date': '2026-03-11', 'number': 'VC-RF', 'memo': 'Returned stock',
        'expenses': [{'account': books['expense'], 'amount': '40.00'}]})

    b.navigate(f'{base}/vendor-credit/{credit["id"]}')
    b.wait_for('!!document.querySelector("[data-vendor-credit-refund]")')
    _contained(b, width)
    _click(b, '[data-vendor-credit-refund]')
    b.wait_for('location.pathname.endsWith("/refund") && !!document.querySelector("[data-generated-form]")')
    assert _read(b, 'f:credit') == credit['id']
    assert _read(b, 'f:number') == '' and _read(b, 'f:memo') == '', 'the credit’s own fields are not the deposit’s'
    _contained(b, width)

    _set(b, 'f:deposit_to', books['bank'])
    _set(b, 'f:date', '2026-03-20')
    _set(b, 'f:amount', '15.00')
    _set(b, 'f:check_number', '5521')
    _set(b, 'ctx:reason', 'Supplier mailed a refund check')
    _action(b, 'submit')
    # Saving returns to the credit itself, which now says what it still has free.
    b.wait_for(f'location.pathname.endsWith("/vendor-credit/{credit["id"]}") '
               '&& !!document.querySelector("[data-vendor-credit-settlement]")')
    assert 'Saved successfully' in b.evaluate('document.body.innerText')

    shown = books['run']('vendor-credit.show', {'credit': credit['id']})
    assert shown['settlement_current']['unapplied']['amount'] == '25.00'
    deposit = next(row['obligation_transaction_id'] for row in shown['applications'] if row['active'])

    assert 'Still free $25.00' in _text(b, '[data-vendor-credit-settlement]')
    link = b.evaluate('[...document.querySelectorAll("a")].find(a => a.textContent.startsWith("Refund deposit"))?.getAttribute("href")')
    assert link and link.endswith(f'/deposit/{deposit}'), link
    _contained(b, width)
