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


def _reach_the_ticking_page(env, b, width):
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

    return bank


@pytest.mark.parametrize('width', [1280, 390])
def test_a_person_follows_the_tile_and_ends_holding_a_certificate(register_browser, width):
    env, b = register_browser, register_browser.browser
    _reach_the_ticking_page(env, b, width)

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
    assert b.evaluate('document.querySelector("[data-reconcile-finish]").disabled'), \
        'a certified statement must not offer another finish action'
    _contained(b, width)


def test_mark_all_ticks_everything_in_one_step_and_finishes(register_browser):
    """The Mark all control: one press ticks the list and saves it, Clear all marks undoes it."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    _reach_the_ticking_page(env, b, 1280)
    rows = '[data-reconcile-list] .reconcile-movement input[type=checkbox]'
    difference = '.reconcile-difference'
    both = f'document.querySelectorAll({json.dumps(rows)}).length === 2'
    unticked = f'{both} && [...document.querySelectorAll({json.dumps(rows)})].every(e => !e.checked)'
    ticked = f'{both} && [...document.querySelectorAll({json.dumps(rows)})].every(e => e.checked)'
    assert b.evaluate(unticked)

    b.evaluate('document.querySelector("[data-reconcile-mark-all]").click()')
    b.wait_for(f'document.querySelector({json.dumps(difference)}).dataset.balanced === "true"')
    b.wait_for(ticked)       # the list is read again after the save, so the boxes follow it
    _contained(b, 1280)

    # It is saved, not just shown: clearing and marking again moves the draft on each time.
    b.evaluate('document.querySelector("[data-reconcile-clear-all]").click()')
    b.wait_for(f'document.querySelector({json.dumps(difference)}).dataset.balanced === "false"')
    b.wait_for(unticked)
    b.evaluate('document.querySelector("[data-reconcile-mark-all]").click()')
    b.wait_for(ticked)
    b.wait_for(f'document.querySelector({json.dumps(difference)}).dataset.balanced === "true"')

    # Finishing needs no separate save: the marks are already on the draft.
    b.wait_for('!!document.querySelector("[data-reconcile-finish]") && '
               '!document.querySelector("[data-reconcile-finish]").disabled')
    b.evaluate('document.querySelector("[data-reconcile-finish]").click()')
    try:
        b.wait_for('!!document.querySelector(".reconcile-certificate:not([hidden])")')
    except AssertionError:
        raise AssertionError('finish did not certify: ' + str(b.evaluate(
            '[...document.querySelectorAll("[role=alert],[role=status]")].map(e=>e.textContent)')))
    assert STATEMENT in b.evaluate('document.querySelector(".reconcile-certificate").textContent')


@pytest.mark.parametrize('width', [1280, 390])
def test_a_person_finishes_a_statement_that_will_not_tie_with_an_adjustment(register_browser, width):
    """Two journals tick to 290.00 against a statement of 290.00; one ticked leaves 40.00.

    Before anything is ticked the offer names the whole 290.00; an unsaved tick withdraws it.
    Saving only the first leaves a 40.00 difference the person cannot find. The page offers
    "Finish with adjustment" naming the amount and the account it posts to; with a reason it
    certifies the statement and says which journal it posted.
    """
    env, b = register_browser, register_browser.browser
    _reach_the_ticking_page(env, b, width)
    rows = '[data-reconcile-list] .reconcile-movement input[type=checkbox]'
    panel = '[data-reconcile-adjust]'
    b.wait_for(f'!document.querySelector({json.dumps(panel)}).hidden')
    assert '290.00' in b.evaluate(f'document.querySelector({json.dumps(panel)}).textContent')

    # Tick only the 250.00 journal: an unsaved tick withdraws the offer, whose amount would be stale.
    b.evaluate(f'''[...document.querySelectorAll({json.dumps(rows)})].find(e =>
        e.closest('.reconcile-movement').textContent.includes('250.00')).click()''')
    assert b.evaluate(f'document.querySelector({json.dumps(panel)}).hidden'), \
        'an unsaved tick must withdraw the adjustment offer'
    # Saved, 40.00 remains, and the offer comes back naming it.
    _set(b, 'ctx:reason', 'Tick what the statement shows')
    _submit(b)
    b.wait_for(f'!!document.querySelector({json.dumps(panel)}) && !document.querySelector({json.dumps(panel)}).hidden'
               f' && document.querySelector({json.dumps(panel)}).textContent.includes("40.00")')
    offered = b.evaluate(f'document.querySelector({json.dumps(panel)}).textContent')
    assert 'Finish with adjustment' in offered and '40.00' in offered, offered
    assert 'Reconciliation Discrepancies' in offered and '2026-01-31' in offered, offered
    assert b.evaluate('document.querySelector("[data-reconcile-adjust-finish]").disabled'), \
        'a reason is required before the adjustment can be posted'
    assert b.evaluate('document.querySelector("[data-reconcile-finish]").disabled')
    _contained(b, width)

    b.evaluate('''(() => {const e=document.querySelector("[data-reconcile-adjust-reason]");
        e.value="Bank shows 40.00 we cannot trace"; e.dispatchEvent(new Event("input",{bubbles:true}));})()''')
    b.wait_for('!document.querySelector("[data-reconcile-adjust-finish]").disabled')
    b.evaluate('document.querySelector("[data-reconcile-adjust-finish]").click()')
    try:
        b.wait_for('!!document.querySelector(".reconcile-certificate:not([hidden])")')
    except AssertionError:
        raise AssertionError('adjusted finish did not certify: ' + str(b.evaluate(
            '[...document.querySelectorAll("[role=alert],[role=status]")].map(e=>e.textContent)')))
    certified = b.evaluate('document.querySelector(".reconcile-certificate").textContent')
    assert 'with an adjustment' in certified and '40.00' in certified, certified
    assert 'Reconciliation Discrepancies' in certified and STATEMENT in certified, certified
    assert b.evaluate(f'document.querySelector({json.dumps(panel)}).hidden')
    _contained(b, width)
