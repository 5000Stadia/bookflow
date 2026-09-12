"""A bookkeeper reconciles a bank account, in a real browser, at desktop and phone widths.

This is the test that decides whether the Reconcile tile may be live. Not that the pages answer
-- `test_reconciliation_browser.py` checks that -- but that the errand the tile promises can be
finished: follow the tile, adopt an opening balance, see what can be cleared, tick what appears
on the statement, watch the difference fall to zero, and end holding a certificate. Nothing here
types an address or calls a command the page did not call for itself, because a flow that only
works when you know the URLs is not a flow a person has.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _contained

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

FIRST = '250.00'
SECOND = '40.00'
STATEMENT = '290.00'


def _set(b, name, value):
    b.evaluate(f'''(() => {{const e=document.getElementsByName({json.dumps(name)})[0];
        e.value={json.dumps(value)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));
        e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')


def _submit(b):
    b.evaluate('document.querySelector("button[name=action][value=submit]").click()')


def _tile(b, title):
    return (f'[...document.querySelectorAll(".flow-tile")].find(e => e.textContent.includes('
            f'{json.dumps(title)}))')


def _books(b, site):
    run = lambda name, payload, **h: _command(b, site, name, payload, **h)
    bank = run('account.create', {'name': 'Statement checking', 'type': 'bank'})['id']
    equity = run('account.create', {'name': 'Statement equity', 'type': 'equity'})['id']
    for amount, date in ((FIRST, '2026-01-10'), (SECOND, '2026-01-20')):
        run('journal.post', {'date': date, 'lines': [
            {'account': bank, 'side': 'debit', 'amount': amount},
            {'account': equity, 'side': 'credit', 'amount': amount}]},
            **{'X-Bookflow-Reason': 'Something to reconcile'})
    return bank


@pytest.mark.parametrize('width', [1280, 390])
def test_a_person_follows_the_tile_and_ends_holding_a_certificate(register_browser, width):
    env, b = register_browser, register_browser.browser
    b.viewport(width, 900)
    bank = _books(b, env.site)

    # The tile, clicked -- never an address typed.
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/')
    b.wait_for('!!document.querySelector("a.flow-tile")')
    tile = _tile(b, 'Reconcile')
    assert b.evaluate(f'!!{tile} && {tile}.tagName') == 'A', 'the Reconcile tile is not a link'
    b.evaluate(f'{tile}.click()')
    b.wait_for('!!document.getElementsByName("f:account")[0]')

    # Adopt the account at a zero opening balance.
    _set(b, 'f:account', bank)
    _set(b, 'f:opening_date', '2026-01-01')
    _set(b, 'f:entered_balance', '0.00')
    _set(b, 'f:evidence.statement_reference', 'January statement')
    _set(b, 'f:evidence.entered_text', 'Adopted at zero')
    _set(b, 'ctx:reason', 'Adopt the checking account')
    _contained(b, width)
    _submit(b)

    # Saving lands on the next step with the draft already filled in; nobody types an address.
    b.wait_for('!!document.getElementsByName("f:statement_date")[0]')
    _set(b, 'f:statement_date', '2026-01-31')
    _set(b, 'f:ending_balance', STATEMENT)
    _set(b, 'ctx:reason', 'January statement')
    _contained(b, width)
    _submit(b)

    # And that lands on the ticking page, already showing what can be cleared.
    b.wait_for('!!document.querySelector("[data-reconcile-list] .reconcile-movement")')

    rows = '[data-reconcile-list] .reconcile-movement input[type=checkbox]'
    assert b.evaluate(f'document.querySelectorAll({json.dumps(rows)}).length') == 2, \
        'both journals should be offered for clearing'
    difference = '.reconcile-difference'
    assert b.evaluate(f'document.querySelector({json.dumps(difference)}).dataset.balanced') == 'false'

    # Tick what the statement shows, and watch the difference close.
    b.evaluate(f'''document.querySelectorAll({json.dumps(rows)}).forEach(e => {{
        if(!e.checked) e.click(); }})''')
    b.wait_for(f'document.querySelector({json.dumps(difference)}).dataset.balanced === "true"')
    _contained(b, width)

    # Save the marks, come back to the same page, and finish.
    _set(b, 'ctx:reason', 'Tick the January statement')
    _submit(b)
    b.wait_for('!!document.querySelector("[data-reconcile-finish]") && '
               '!document.querySelector("[data-reconcile-finish]").disabled')
    _contained(b, width)
    b.evaluate('document.querySelector("[data-reconcile-finish]").click()')
    try:
        b.wait_for('!!document.querySelector(".reconcile-certificate:not([hidden])")')
    except AssertionError:  # surface what the page said rather than only that it never changed
        raise AssertionError('finish did not certify: ' + str(b.evaluate(
            '[...document.querySelectorAll("[role=alert],[role=status]")].map(e=>e.textContent)')))

    certified = b.evaluate('document.querySelector(".reconcile-certificate").textContent')
    assert 'Statement certified' in certified
    # The cleared balance a person is shown is the statement they entered.
    assert STATEMENT in certified, certified
    _contained(b, width)
