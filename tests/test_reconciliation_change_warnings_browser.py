"""In a real browser: correcting a reconciled transfer warns on the preview and after the save.

The books: 500.00 of owner money into a new checking account on 2026-06-01 and a 100.00
transfer out of it to savings on 2026-06-05, reconciled to a statement dated 2026-06-30 whose
ending balance is 400.00. Correcting the transfer to 80.00 moves 20.00 back into checking, so
the reconciliation's cleared balance becomes 420.00 against its 400.00: off by 20.00. The person
reads that under the form before saving, and again in the saved notice after it.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_reconciliation_window_browser import _set

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

REASON = {'X-Bookflow-Reason': 'R146 browser witness'}


def _books(b, site):
    run = lambda name, payload, **h: _command(b, site, name, payload, **h)
    checking = run('account.create', {'name': 'R146 checking', 'type': 'bank'})['id']
    savings = run('account.create', {'name': 'R146 savings', 'type': 'bank'})['id']
    equity = run('account.create', {'name': 'R146 equity', 'type': 'equity'})['id']
    run('journal.post', {'date': '2026-06-01', 'lines': [
        {'account': checking, 'side': 'debit', 'amount': '500.00'},
        {'account': equity, 'side': 'credit', 'amount': '500.00'}]}, **REASON)
    transfer = run('transfer.post', {'from_account': checking, 'to_account': savings,
                                     'date': '2026-06-05', 'amount': '100.00'}, **REASON)
    opening = run('reconcile.opening.start', {
        'operation_key': 'r146-opening', 'account': checking, 'opening_date': '2026-05-31',
        'entered_balance': '0.00', 'references': [],
        'evidence': {'format': 1, 'statement_reference': None, 'entered_text': 'Adopted at zero'}}, **REASON)
    statement = run('reconcile.start', {
        'operation_key': 'r146-statement', 'account': checking, 'statement_date': '2026-06-30',
        'ending_balance': '400.00', 'opening_draft_id': opening['draft']['id']}, **REASON)
    draft = statement['draft']['id']
    page = run('reconcile.candidates', {'draft': draft, 'limit': 200})
    marked = run('reconcile.mark', {'operation_key': 'r146-mark', 'draft': draft, 'expected_version': 1,
        'entries': [{'movement': row['movement'], 'group_fingerprint': row['group_fingerprint'],
                     'action': 'mark'} for row in page['items']]}, **REASON)
    version = marked['draft']['version']
    preview = run('reconcile.preview', {'draft': draft, 'expected_version': version})
    done = run('reconcile.finish', {'operation_key': 'r146-finish', 'draft': draft, 'expected_version': version,
                                    'expected_facts_fingerprint': preview['expected_facts_fingerprint'],
                                    'dependency_guard': preview['dependency_guard']}, **REASON)
    assert done['totals']['difference'] == 0
    return transfer, checking


def test_a_person_correcting_a_reconciled_transfer_is_told_before_and_after_saving(register_browser):
    env, b = register_browser, register_browser.browser
    transfer, checking = _books(b, env.site)
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/transfer/{transfer['id']}/update")
    b.wait_for('!!document.getElementsByName("f:amount")[0]')
    _set(b, 'f:amount', '80.00')
    _set(b, 'ctx:reason', 'The transfer was 80.00')
    b.evaluate('document.querySelector("button[name=action][value=preview]").click()')
    b.wait_for('!!document.querySelector("[data-preview-warnings]")')
    shown = b.evaluate('document.querySelector("[data-preview-warnings]").textContent')
    assert 'reconciled on the R146 checking statement dated 2026-06-30' in shown, shown
    assert 'Saving this change will leave that reconciliation off by 20.00 USD' in shown, shown
    assert 'becomes 420.00 against the statement\'s ending balance of 400.00' in shown, shown

    b.evaluate('document.querySelector("button[name=action][value=submit]").click()')
    b.wait_for('!!document.querySelector(".save-feedback .warn")')
    saved = b.evaluate('document.querySelector(".save-feedback .warn").textContent')
    assert 'This change left that reconciliation off by 20.00 USD' in saved, saved
    assert 'is now 420.00' in saved, saved

    # The report the warning points to shows the same figures, on its own page.
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/report/reconciliation-discrepancy?f:account={checking}&f:as_of=2026-12-31")
    b.wait_for('document.readyState === "complete"')
    rows = b.evaluate('JSON.stringify([...document.querySelectorAll("#everyday-rows tbody tr")].map(r => r.textContent.replace(/\\s+/g, " ").trim()))')
    rows = json.loads(rows)
    assert any('Statement 2026-06-30' in row and '400.00' in row and '420.00' in row and '20.00' in row
               for row in rows), rows
    assert any('Amount changed' in row and '-100.00' in row and '-80.00' in row for row in rows), rows
