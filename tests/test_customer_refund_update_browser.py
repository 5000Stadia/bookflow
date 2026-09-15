"""A refund recorded, corrected and read back in a real browser, at desktop and phone widths.

The journey a person actually walks: refund a credit from the credit memo's own page, open the
correction from the saved refund by clicking the link the page offers, read the stored bank
account, method and credit off the controls, change what was typed wrong, preview it, save it,
and then read the revision it replaced. Nothing here is typed as an address and nothing is
asserted about a command being registered -- every step is a click on what the page renders.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401
from tests.test_credit_windows_browser import (
    _books, _contained, _credit, _name, _pick, _preview, _read, _save, _set, _text,
)

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

REFUNDED = '40.00'
CORRECTED = '30.00'


def _click(b, selector):
    b.evaluate(f'''(() => {{const e = document.querySelector({json.dumps(selector)});
        if (!e) throw new Error('nothing matches ' + {json.dumps(selector)}); e.click();}})()''')


@pytest.mark.parametrize('width', [1280, 390])
def test_a_refund_is_corrected_from_its_own_page_and_the_old_revision_stays_readable(
        register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'Fix')
    credit = _credit(books, REFUNDED)
    base = f'{env.site.base_url}/c/{env.site.company_id}'

    # Record: the refund is written against the credit, from the credit's own page.
    b.navigate(f'{base}/credit-memo/{credit["id"]}')
    b.wait_for('!!document.querySelector("[data-credit-refund]")')
    _click(b, '[data-credit-refund]')
    b.wait_for('!!document.querySelector("[data-generated-form]") '
               '&& location.pathname.endsWith("/customer-refund/post")')
    _set(b, 'f:date', '2026-03-20')
    _pick(b, 'f:funding_account', 'Fix checking')
    _pick(b, 'f:method', 'Check')
    _set(b, 'f:check_number', '2041')
    _set(b, 'f:reference', 'Refund for March')
    _set(b, 'f:memo', 'Cheque 2041')
    _preview(b)
    refund_id = _save(b, 'customer-refund', '.refund-sources')

    # The saved refund offers the correction and no longer says there is none.
    shown = _text(b, '.sales-document')
    assert 'There is no correction' not in shown, shown[:1200]
    assert 'corrected rather than voided' in shown, shown[:1200]
    _contained(b, width)
    _click(b, '[data-refund-correct]')
    b.wait_for('location.pathname.endsWith("/update") '
               '&& !!document.querySelector("[data-generated-form]")')

    # Correct: everything the refund stored is on the controls, in a person's words as well
    # as in ids, so what is being corrected can be read before anything is typed.
    assert _read(b, 'f:expected_version') == '1'
    assert _read(b, 'f:funding_account') == books['bank']
    assert _read(b, 'label:f:funding_account') == 'Fix checking'
    assert _read(b, 'label:f:method') == 'Check'
    assert _read(b, 'label:f:customer') == 'Fix homeowner'
    assert _read(b, 'f:check_number') == '2041'
    assert _read(b, 'f:reference') == 'Refund for March'
    assert _read(b, 'f:memo') == 'Cheque 2041'
    assert _read(b, _name(b, 'sources', 0, 'credit_memo')) == credit['id']
    assert _read(b, _name(b, 'sources', 0, 'amount')) == REFUNDED

    _set(b, 'ctx:reason', 'The cheque was written for thirty')
    _set(b, _name(b, 'sources', 0, 'amount'), CORRECTED)
    _set(b, 'f:memo', 'Cheque 2041, thirty not forty')
    _preview(b)
    _contained(b, width)
    assert _save(b, 'customer-refund', '.refund-sources') == refund_id

    # One document, at the corrected figure, with the credit worth the difference again.
    corrected = books['run']('customer-refund.show', {'refund': refund_id})
    assert corrected['revision']['revision_number'] == 2
    assert corrected['total']['amount'] == CORRECTED
    assert corrected['check_number'] == '2041' and corrected['reference'] == 'Refund for March'
    assert [row['credit_memo_id'] for row in corrected['revision']['profile']['sources']] == \
        [credit['id']]
    assert books['run']('credit-memo.show', {'credit_memo': credit['id']})[
        'source_current']['available']['amount'] == '10.00'
    page = _text(b, '.sales-document')
    assert CORRECTED in page and 'Cheque 2041, thirty not forty' in page
    _contained(b, width)

    # Read back: the revision the correction replaced is still there to read.
    _click(b, 'nav[aria-label="Customer refund revisions"] a')
    b.wait_for('location.search.includes("revision_number=1") '
               '&& !!document.querySelector(".refund-sources")')
    previous = _text(b, '.sales-document')
    assert REFUNDED in previous and 'Cheque 2041' in previous
    assert 'Cheque 2041, thirty not forty' not in previous
    _contained(b, width)
