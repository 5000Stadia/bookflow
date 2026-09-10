"""The transfer document window, driven in a real browser.

Three things are checked here that nothing else can check: that the window posts the same
command an agent sends, that a refusal reaches the page naming the account, and that at 390px
the form itself has nothing to scroll sideways -- the form's own scrollWidth against its own
clientWidth, and the same for every band inside it, not merely a page that does not move.

A transfer has no line grid, so the container measured is the document window rather than a
grid inside it.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained, _fill, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

MOVED = '500.00'
CARD_PAYMENT = '250.00'

VOLATILE = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at',
            'audit_event_id', 'number', 'current_revision_id', 'idempotency_key', 'position',
            'updated_at', 'version'}


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _pick(b, name, label):
    """Choose a record in a reference picker by typing its name and clicking the match."""
    _fill(b, 'label:' + name, label)
    picker = f'document.getElementsByName({json.dumps(name)})[0].closest("[data-reference]")'
    b.wait_for(f'{picker}.querySelectorAll("[role=option]").length > 0')
    b.evaluate(f'''(() => {{const options=[...{picker}.querySelectorAll('[role=option]')];
        const option=options.find(e=>e.textContent.includes({json.dumps(label)}));
        if(!option) throw new Error('Missing choice'); option.click();}})()''')
    assert _value(b, name)


def _act(b, action):
    """Preview swaps the form in place; the page that comes back is the one to read."""
    b.evaluate('void (window.transferForm = document.querySelector("[data-generated-form]"))')
    _click(b, action)
    b.wait_for('!window.transferForm.isConnected')


def _save(b):
    """Saving navigates to the posted journal; the form's own window is gone by then."""
    _click(b, 'submit')
    b.wait_for('location.pathname.includes("/journal/")')
    return b.evaluate('location.pathname').rsplit('/', 1)[-1]


def _totals(b):
    return b.evaluate('''(() => {const list = document.querySelector(".totals-list");
        if (!list) return null;
        const terms = [...list.querySelectorAll("dt")].map(e => e.textContent.trim());
        const values = [...list.querySelectorAll("dd")].map(e => e.textContent.trim());
        return Object.fromEntries(terms.map((t, i) => [t, values[i]]));})()''')


def _books(b, site, tag):
    run = lambda name, payload: _command(b, site, name, payload)
    return dict(
        checking=run('account.create', {'name': f'{tag} checking', 'type': 'bank'})['id'],
        savings=run('account.create', {'name': f'{tag} savings', 'type': 'bank'})['id'],
        card=run('account.create', {'name': f'{tag} card', 'type': 'credit_card'})['id'],
        income=run('account.create', {'name': f'{tag} sales', 'type': 'income'})['id'],
        run=run, tag=tag)


def _open(b, env, noun='transfer'):
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')


def _enter(b, books, *, to, amount=MOVED, memo='Sweep to savings', date='2026-05-04'):
    _pick(b, 'f:from_account', f'{books["tag"]} checking')
    _pick(b, 'f:to_account', to)
    _fill(b, 'f:date', date)
    _fill(b, 'f:amount', amount)
    _fill(b, 'f:memo', memo)


def test_the_transfer_window_posts_the_transfer_the_command_posts(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Parity')

    _open(b, env)
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New transfer'
    # Header only: no line grid, no Add a line, no payee, no number.
    assert not b.evaluate('!!document.querySelector(".line-grid")')
    assert not b.evaluate('!!document.querySelector("[data-collection-add]")')
    assert not b.evaluate('!!document.getElementsByName("f:pay_to.name_id")[0]')
    assert not b.evaluate('!!document.getElementsByName("f:number")[0]')
    # The two ends read as the words a person would use for them.
    band = lambda name: b.evaluate(f'''
        [...document.querySelectorAll('[aria-label="Transfer {name}"] .row.form-field > label')]
            .map(e => e.textContent.split("\\n")[0].replace(/ \\*$/, "").trim())''')
    assert band('header') == ['Transfer Funds From', 'Transfer Funds To', 'Date',
                              'Transfer Amount'], band('header')
    assert band('footer') == ['Memo'], band('footer')
    # One control per input leaf and no leaf without one. The whole-registry gate in
    # tests/test_row3_host.py stops at an unrelated pre-existing failure before it reaches
    # this command, so the same rule is applied here for the transfer alone.
    controls = b.evaluate('''[...document.querySelectorAll('[name^="f:"]')]
        .map(e => e.name).sort()''')
    assert controls == ['f:amount', 'f:date', 'f:from_account', 'f:memo',
                        'f:to_account'], controls

    _enter(b, books, to=f'{books["tag"]} savings')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b) == {f'Out of {books["tag"]} checking': f'{MOVED} USD',
                          f'Into {books["tag"]} savings': f'{MOVED} USD'}
    said = b.evaluate('document.querySelector("[data-reconciliation]").textContent')
    assert said == (f'{books["tag"]} checking goes down {MOVED} USD and {books["tag"]} savings '
                    f'goes up {MOVED} USD. Neither end is income or expense, so this changes '
                    f'no profit.')

    written = books['run']('journal.show', {'journal': _save(b)})

    # The same transfer, sent the way an agent sends it.
    sent = books['run']('transfer.post', {
        'from_account': books['checking'], 'to_account': books['savings'],
        'date': '2026-05-04', 'amount': MOVED, 'memo': 'Sweep to savings'})
    assert _scrub(written['revision'])['lines'] == _scrub(sent['revision'])['lines']
    assert _scrub(written['revision'])['debit_total'] == {'amount': MOVED, 'currency': 'USD',
                                                          'minor_units': 50000}
    sides = {line['account_id']: line['side'] for line in written['revision']['lines']}
    assert sides == {books['checking']: 'credit', books['savings']: 'debit'}


def test_paying_the_card_down_in_the_window_says_what_you_owe_goes_down(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Card')

    _open(b, env)
    _enter(b, books, to=f'{books["tag"]} card', amount=CARD_PAYMENT, memo='Pay the card down')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    said = b.evaluate('document.querySelector("[data-reconciliation]").textContent')
    assert (f'what you owe on {books["tag"]} card goes down {CARD_PAYMENT} USD') in said

    written = books['run']('journal.show', {'journal': _save(b)})
    sides = {line['account_id']: line['side'] for line in written['revision']['lines']}
    # The card is debited, which is what makes what is owed on it fall.
    assert sides == {books['checking']: 'credit', books['card']: 'debit'}


def test_a_refused_end_says_which_account_and_why_in_the_window(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Refused')

    _open(b, env)
    _enter(b, books, to=f'{books["tag"]} sales')
    _act(b, 'preview')
    error = b.evaluate('document.querySelector(".error").textContent')
    assert 'E_VALIDATION' in error
    assert f'{books["tag"]} sales' in error and 'is an income account' in error
    assert 'never changes profit' in error
    # The entered values survive the refusal, so the wrong end can be fixed in place.
    assert _value(b, 'f:amount') == MOVED
    assert _value(b, 'f:from_account') == books['checking']

    # And the same account at both ends is refused by name.
    _open(b, env)
    _enter(b, books, to=f'{books["tag"]} checking')
    _act(b, 'preview')
    error = b.evaluate('document.querySelector(".error").textContent')
    assert f'{books["tag"]} checking' in error and 'both ends' in error


def test_the_transfer_form_has_nothing_to_scroll_sideways_at_phone_width(register_browser):
    """The criterion is the form's own scrollWidth against its own clientWidth, not the page."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    books = _books(b, env.site, 'Phone')

    _open(b, env)
    _enter(b, books, to=f'{books["tag"]} savings',
           memo='A memo long enough to wrap onto a second line on a phone')

    _contained(b, 390)
    form = b.evaluate('''(() => {const f = document.querySelector("[data-generated-form]");
        return {scroll: f.scrollWidth, client: f.clientWidth};})()''')
    assert form['scroll'] == form['client'], form
    bands = b.evaluate('''[...document.querySelectorAll(".document-band")]
        .map(e => ({scroll: e.scrollWidth, client: e.clientWidth,
                    label: e.getAttribute("aria-label")}))''')
    assert bands and all(band['scroll'] == band['client'] for band in bands), bands
    # Each control stays inside its own box and inside the window.
    assert b.evaluate('''[...document.querySelectorAll(".document-band .row.form-field")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')
    # The header stacks one field per row on a phone: every control starts at the same left.
    lefts = b.evaluate('''[...document.querySelectorAll(".document-row > .row.form-field")]
        .map(e => Math.round(e.getBoundingClientRect().left))''')
    assert len(set(lefts)) == 1, lefts

    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _contained(b, 390)
    after = b.evaluate('''(() => {const f = document.querySelector("[data-generated-form]");
        return {scroll: f.scrollWidth, client: f.clientWidth};})()''')
    assert after['scroll'] == after['client'], after
    assert all(band['scroll'] == band['client'] for band in b.evaluate(
        '''[...document.querySelectorAll(".document-band")]
           .map(e => ({scroll: e.scrollWidth, client: e.clientWidth}))''')), 'footer band'


def test_the_banking_tile_opens_the_transfer_window(register_browser):
    """Banking's Transfer funds tile is a live link, and it lands on a usable form."""
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/')
    b.wait_for('!!document.querySelector(".flow-board")')
    tile = b.evaluate('''(() => {
        const found = [...document.querySelectorAll(".flow-tile")].find(
            e => e.querySelector(".flow-tile-title")?.textContent.trim() === "Transfer funds");
        if (!found) throw new Error("no Transfer funds tile");
        return {tag: found.tagName.toLowerCase(),
                href: found.href ? new URL(found.href).pathname : null,
                planned: found.classList.contains("flow-planned")};})()''')
    assert tile == {'tag': 'a', 'planned': False,
                    'href': f'/c/{env.site.company_id}/transfer/post'}
    b.evaluate('''[...document.querySelectorAll(".flow-tile")].find(
        e => e.querySelector(".flow-tile-title")?.textContent.trim() === "Transfer funds").click()''')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == 'New transfer'
    assert b.evaluate('!!document.getElementsByName("f:from_account")[0]')
