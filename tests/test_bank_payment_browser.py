"""R183 run 3: a bank payment (a check posted with no number) reads as one in the register and on its own
page, never under its journal reference as though that were a check number."""
from __future__ import annotations

from tests.test_row8_register_browser import _command, browser_site, register_browser  # noqa: F401


def test_a_bank_payment_reads_as_one_in_the_register_and_on_its_page(register_browser):
    env, b = register_browser, register_browser.browser
    paid = _command(b, env.site, 'check.post', {'account': env.bank['id'], 'date': '2026-10-20', 'amount': '12.00',
                                               'no_number': True, 'memo': 'Debit card fuel',
                                               'expenses': [{'account': env.expense['id'], 'amount': '12.00'}]})
    cheque = _command(b, env.site, 'check.post', {'account': env.bank['id'], 'date': '2026-10-20', 'amount': '5.00',
                                                 'memo': 'Paper cheque',
                                                 'expenses': [{'account': env.expense['id'], 'amount': '5.00'}]})
    b.navigate(f"{env.url}?date_from=2026-10-01&date_to=2026-10-31")
    b.wait_for("[...document.querySelectorAll('#register-history .reg-memo')].some(e => e.textContent.includes('Paper cheque'))")
    refs = b.evaluate("""[...document.querySelectorAll('#register-history tr[data-kind=posting]')].map(r =>
      [r.querySelector('.reg-memo').textContent, r.querySelector('.reg-ref').textContent])""")
    by_memo = {memo: ref for memo, ref in refs}
    assert by_memo['Debit card fuel'].startswith('Bank payment'), refs
    # A bank payment's own result carries no number (R183 fix round 3, 619cb8c0); its journal's document
    # reference is still there behind it, and the register must not show it as though it were a check number.
    assert paid['number'] is None, paid
    rows = _command(b, env.site, 'register.query', {'account': env.bank['id'], 'date_from': '2026-10-01',
                                                    'date_to': '2026-10-31'})['rows']
    [reference] = [r['transaction_number'] for r in rows if r['transaction_id'] == paid['id']]
    assert reference and reference not in by_memo['Debit card fuel'].split(' · ')[0], (reference, refs)
    assert by_memo['Paper cheque'].startswith(f"{cheque['document']['check_number']} · Check"), refs

    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/check/{paid['id']}")
    b.wait_for("!!document.querySelector('h1.record-heading')")
    assert b.evaluate("document.querySelector('h1.record-heading').textContent.trim()") == 'Bank payment'
    assert b.evaluate("document.title").startswith('Bank payment')
    b.navigate(f"{env.site.base_url}/c/{env.site.company_id}/check/{cheque['id']}")
    b.wait_for("!!document.querySelector('h1.record-heading')")
    assert b.evaluate("document.querySelector('h1.record-heading').textContent.trim()") == f"Check {cheque['document']['check_number']}"
