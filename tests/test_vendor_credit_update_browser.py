"""A vendor credit entered, applied, corrected and read back in a real browser.

The journey a person actually walks: enter the credit from the home board's own tile, point it
at a bill, open the correction from the saved credit by clicking the link the page offers, read
the stored vendor, payable, reference and credited grid off the controls, change what was typed
wrong, preview it, save it, and then read the revision it replaced. Nothing here is typed as an
address and nothing is asserted about a command being registered -- every step is a click on
what the page renders.

The figure that has to survive the correction is the bill's. A credit answering a bill is the
one thing a correction could silently move, so the bill's own page is read after the save.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401
from tests.test_credit_windows_browser import (
    _add_row, _books, _contained, _name, _open_from_the_board, _pick, _preview, _read, _save,
    _set, _text, _totals,
)

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

BILL = '100.00'
CREDITED = '15.00'
CORRECTED = '18.00'


def _click(b, selector):
    b.evaluate(f'''(() => {{const e = document.querySelector({json.dumps(selector)});
        if (!e) throw new Error('nothing matches ' + {json.dumps(selector)}); e.click();}})()''')


@pytest.mark.parametrize('width', [1280, 390])
def test_a_vendor_credit_is_corrected_from_its_own_page_and_the_bill_still_owes_what_it_owed(
        register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'Fixvc')
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    bill = books['run']('bill.post', {
        'vendor': books['vendor'], 'date': '2026-03-01', 'due_date': '2026-03-31',
        'expenses': [{'account': books['expense'], 'amount': BILL, 'memo': 'March supply'}]})

    # Record: the credit is entered through the tile the home board offers.
    _open_from_the_board(b, env, 'Vendor credit', '/vendor-credit/post')
    _pick(b, 'f:vendor', 'Fixvc supply')
    _set(b, 'f:date', '2026-03-11')
    _set(b, 'f:supplier_reference', 'CN-42')
    _set(b, 'f:memo', 'Returned two boxes')
    _add_row(b, 'expenses')
    _pick(b, _name(b, 'expenses', 0, 'account'), 'Fixvc parts')
    _set(b, _name(b, 'expenses', 0, 'amount'), CREDITED)
    _set(b, _name(b, 'expenses', 0, 'memo'), 'Two boxes back')
    _preview(b)
    _contained(b, width)
    credit_id = _save(b, 'vendor-credit', '.vendor-credit-lines')

    # Point it at the bill, so the correction below has a settlement to preserve.
    books['run']('vendor-credit.apply', {'credit': credit_id, 'date': '2026-03-12',
                                         'bills': [{'bill': bill['id']}]})

    # The saved credit offers the correction and says a wrong credit is corrected, not voided.
    b.navigate(f'{base}/vendor-credit/{credit_id}')
    b.wait_for('!!document.querySelector(".vendor-credit-lines")')
    shown = _text(b, '.sales-document')
    assert 'corrected rather than voided' in shown, shown[:1500]
    assert 'Still free 0.00 USD' in _text(b, '[data-vendor-credit-settlement]')
    _contained(b, width)
    _click(b, '[data-vendor-credit-correct]')
    b.wait_for('location.pathname.endsWith("/update") '
               '&& !!document.querySelector("[data-generated-form]")')

    # Correct: everything the credit stored is on the controls, in a person's words as well as
    # in ids, so what is being corrected can be read before anything is typed.
    assert _read(b, 'f:expected_version') == '2', 'applying the credit advanced its version'
    assert _read(b, 'f:date') == '2026-03-11'
    assert _read(b, 'f:supplier_reference') == 'CN-42'
    assert _read(b, 'f:memo') == 'Returned two boxes'
    assert _read(b, 'label:f:vendor') == 'Fixvc supply'
    assert 'Accounts Payable' in _read(b, 'label:f:ap_account')
    assert _read(b, _name(b, 'expenses', 0, 'account')) == books['expense']
    assert _read(b, 'label:' + _name(b, 'expenses', 0, 'account')) == 'Fixvc parts'
    assert _read(b, _name(b, 'expenses', 0, 'amount')) == CREDITED
    assert _read(b, _name(b, 'expenses', 0, 'memo')) == 'Two boxes back'
    assert _read(b, _name(b, 'expenses', 0, 'line_id'))

    _set(b, 'ctx:reason', 'A third box came back as well')
    _set(b, _name(b, 'expenses', 0, 'amount'), CORRECTED)
    _set(b, 'f:memo', 'Returned three boxes')
    _preview(b)
    # A correction is not the document window, so what comes back is the corrected document
    # itself, marked as written nowhere. `_totals` is the post window's footer and there is
    # none here: what this preview does not yet show is what the corrected credit would still
    # have free, which the saved page shows and the preview suppresses.
    previewed = _text(b, '.sales-document')
    assert 'Preview' in previewed and 'nothing written' in previewed, previewed[:800]
    assert CORRECTED in previewed and 'Returned three boxes' in previewed, previewed[:800]
    assert _totals(b) is None
    _contained(b, width)
    assert _save(b, 'vendor-credit', '.vendor-credit-lines') == credit_id

    # One document at the corrected figure, and the bill owes exactly what it owed.
    corrected = books['run']('vendor-credit.show', {'credit': credit_id})
    assert corrected['revision']['revision_number'] == 2
    assert corrected['total']['amount'] == CORRECTED
    assert corrected['supplier_reference'] == 'CN-42'
    assert corrected['settlement_current']['unapplied']['amount'] == '3.00'
    page = _text(b, '.sales-document')
    assert CORRECTED in page and 'Returned three boxes' in page
    _contained(b, width)

    b.navigate(f'{base}/bill/{bill["id"]}')
    b.wait_for('!!document.querySelector(".sales-document")')
    assert '85.00' in _text(b, '.sales-document'), 'the bill no longer owes what it owed'

    # Read back: the revision the correction replaced is still there to read, and the history
    # page lists both.
    b.navigate(f'{base}/vendor-credit/{credit_id}')
    b.wait_for('!!document.querySelector(".vendor-credit-lines")')
    _click(b, 'nav[aria-label="Vendor credit revisions"] a')
    b.wait_for('location.search.includes("revision_number=1") '
               '&& !!document.querySelector(".vendor-credit-lines")')
    previous = _text(b, '.sales-document')
    assert CREDITED in previous and 'Returned two boxes' in previous
    assert 'Returned three boxes' not in previous
    _contained(b, width)

    b.navigate(f'{base}/vendor-credit/{credit_id}')
    b.wait_for('!!document.querySelector("nav[aria-label=\\"Vendor credit revisions\\"]")')
    _click(b, 'nav[aria-label="Vendor credit revisions"] a[href$="/history"]')
    b.wait_for('location.pathname.endsWith("/history") && !!document.querySelector("table td")')
    history = b.evaluate('document.body.innerText')
    assert 'Revision history for' in history
    assert 'Returned two boxes' in history and 'Returned three boxes' in history
    assert 'replacement' in history and 'reversal' in history
    _contained(b, width)
