"""A correction reached from the actual credit list, at desktop and phone widths."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser  # noqa: F401
from tests.test_credit_windows_browser import (
    _books, _credit, _invoice, _open_from_the_board, _set, _preview, _save, _name, _contained,
)

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.mark.parametrize('width', [1280, 390])
def test_credit_correction_is_reachable_and_keeps_the_saved_grid(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    books = _books(b, env.site, 'Correction')
    credit = _credit(books)
    target = _invoice(books)
    from tests.test_row8_register_browser import _command
    _command(b, env.site, 'customer-credit.apply', dict(
        credit_memo=credit['id'], expected_version=credit['version'], date='2026-03-15',
        applications=[dict(invoice=target['id'], expected_version=target['version'], amount='10.00')]),
        **{'X-Bookflow-Reason': 'Apply before correction'})
    _command(b, env.site, 'customer-refund.post', dict(
        date='2026-03-20', funding_account=books['bank'], method=books['method'],
        sources=[dict(credit_memo=credit['id'], amount='5.00')]),
        **{'X-Bookflow-Reason': 'Refund before correction'})
    credit = books['run']('credit-memo.show', dict(credit_memo=credit['id']))
    _open_from_the_board(b, env, 'Credit memo', '/credit-memo/post')
    b.evaluate('document.querySelector(".document-nav-find").click()')
    b.wait_for('location.pathname.endsWith("/credit-memo") && !!document.querySelector("table td")')
    b.evaluate(f'document.querySelector("a[href$=\\"/{credit["id"]}\\"]").click()')
    b.wait_for('!!document.querySelector("[data-credit-worth]")')
    b.evaluate("document.querySelector(" + json.dumps('a[href$="/update"]') + ").click()")
    b.wait_for('location.pathname.endsWith("/update") && !!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.getElementsByName("f:expected_version")[0].value') == str(credit['version'])
    assert b.evaluate(f'document.getElementsByName({json.dumps(_name(b, "lines", 0, "quantity"))})[0].value') == '1'
    _set(b, 'ctx:reason', 'Correct used credit')
    _set(b, 'f:memo', 'Corrected from the browser')
    _preview(b)
    _contained(b, width)
    identifier = _save(b, 'credit-memo', '[data-credit-worth]')
    assert identifier == credit['id']
    shown = books['run']('credit-memo.show', {'credit_memo': identifier})
    assert shown['revision']['memo'] == 'Corrected from the browser'
    assert shown['total_minor_units'] == 3000
    assert shown['revision']['lines'][0]['item_snapshot'] == credit['revision']['lines'][0]['item_snapshot']
    b.evaluate("document.querySelector(" + json.dumps('a[href$="/update"]') + ").click()")
    b.wait_for('location.pathname.endsWith("/update") && !!document.querySelector("[data-generated-form]")')
    _set(b, 'ctx:reason', 'Correct used quantity')
    _set(b, _name(b, 'lines', 0, 'quantity'), '2')
    _preview(b)
    _contained(b, width)
    assert _save(b, 'credit-memo', '[data-credit-worth]') == credit['id']
    changed = books['run']('credit-memo.show', {'credit_memo': credit['id']})
    assert changed['total_minor_units'] == 6000
    assert changed['source_current']['available_minor_units'] == 4500
    assert changed['source_current']['applied_minor_units'] == 1000
    assert changed['source_current']['refunded_minor_units'] == 500
    assert changed['revision']['lines'][0]['line_id'] == credit['revision']['lines'][0]['line_id']
