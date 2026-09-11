"""The check and card charge document windows, driven in a real browser.

Two things are checked here that nothing else can check: that the window posts the same
command an agent sends, and that at 390px the Expenses grid has nothing to scroll sideways --
the grid's own scrollWidth against its own clientWidth, not merely a page that does not move.
"""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_service_sales_browser import _click, _contained, _fill, _value

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

CHECK = '284.60'
FIRST = '184.60'
SECOND = '100.00'

ROWS = ('[data-collection-path=expenses] > [data-collection-items] > [data-collection-item]')

VOLATILE = {'id', 'transaction_id', 'revision_id', 'line_id', 'document_line_id', 'created_at',
            'audit_event_id', 'number', 'current_revision_id', 'idempotency_key', 'position',
            'updated_at', 'version'}


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _line(b, index, field):
    return b.evaluate(f'''document.querySelectorAll({json.dumps(ROWS)})[{index}]
        .querySelector('[name^="c:"][name$=":{field}"]').name''')


def _add_line(b):
    b.evaluate('document.querySelector("[data-collection-path=expenses] > '
               '[data-collection-add]").click()')


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
    b.evaluate('void (window.moneyOutForm = document.querySelector("[data-generated-form]"))')
    _click(b, action)
    b.wait_for('!window.moneyOutForm.isConnected')


def _save(b):
    """Saving navigates to the posted document; the form's own window is gone by then."""
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
        bank=run('account.create', {'name': f'{tag} checking', 'type': 'bank'})['id'],
        card=run('account.create', {'name': f'{tag} card', 'type': 'credit_card'})['id'],
        first=run('account.create', {'name': f'{tag} parts', 'type': 'expense'})['id'],
        second=run('account.create', {'name': f'{tag} fuel', 'type': 'expense'})['id'],
        vendor=run('vendor.create', {'name': f'{tag} supply'})['id'],
        job=run('class.create', {'name': f'{tag} job'})['id'], run=run, tag=tag)


def _write_check(b, env, books, *, amounts=(FIRST, SECOND), amount=CHECK):
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/check/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _pick(b, 'f:account', f'{books["tag"]} checking')
    # The kind of name opens on Vendor, so a check to a vendor takes no extra choice.
    assert _value(b, 'f:pay_to.name_type') == 'vendor'
    _pick(b, 'f:pay_to.name_id', f'{books["tag"]} supply')
    _fill(b, 'f:date', '2026-03-04')
    _fill(b, 'f:number', f'{books["tag"]}-1042')
    _fill(b, 'f:amount', amount)
    _fill(b, 'f:memo', 'March supplies')
    for index, (name, value) in enumerate(zip((f'{books["tag"]} parts', f'{books["tag"]} fuel'),
                                              amounts)):
        _add_line(b)
        _pick(b, _line(b, index, 'account'), name)
        _fill(b, _line(b, index, 'amount'), value)
        _fill(b, _line(b, index, 'memo'), 'Line ' + str(index + 1))
    # A class chosen in the Class column is that line's class, with nothing else to set.
    _pick(b, _line(b, 0, 'class_id'), f'{books["tag"]} job')


def test_the_check_window_posts_the_check_the_command_posts(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Parity')

    _write_check(b, env, books)
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    # The footer shows the number the server settled on for this bank account's chequebook,
    # which is what the register and `report missing-checks` will show for the same cheque.
    assert _totals(b) == {'Check number': f'{books["tag"]}-1042',
                          'Expenses': f'{CHECK} USD', 'Amount of this check': f'{CHECK} USD'}
    assert 'add up' in b.evaluate('document.querySelector("[data-reconciliation]").textContent')

    written = books['run']('journal.show', {'journal': _save(b)})

    # The same check, sent the way an agent sends it.
    sent = books['run']('check.post', {
        'account': books['bank'],
        'pay_to': {'name_type': 'vendor', 'name_id': books['vendor']},
        'date': '2026-03-04', 'number': f'{books["tag"]}-agent', 'amount': CHECK,
        'memo': 'March supplies',
        'expenses': [{'account': books['first'], 'amount': FIRST, 'memo': 'Line 1',
                      'class_id': books['job']},
                     {'account': books['second'], 'amount': SECOND, 'memo': 'Line 2'}]})
    assert _scrub(written['revision'])['lines'] == _scrub(sent['revision'])['lines']
    assert _scrub(written['revision'])['debit_total'] == {'amount': CHECK, 'currency': 'USD',
                                                          'minor_units': 28460}
    assert written['revision']['lines'][1]['class_id'] == books['job']


def test_lines_that_do_not_add_up_refuse_in_the_window_and_show_the_difference(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Short')

    _write_check(b, env, books, amounts=(FIRST, '95.40'))
    _act(b, 'preview')
    error = b.evaluate('document.querySelector(".error").textContent')
    assert 'E_UNBALANCED_ENTRY' in error and '4.60' in error and '280.00' in error
    assert _totals(b) == {'Expenses': '280.00 USD', 'Amount of this check': f'{CHECK} USD',
                          'Short by': '4.60 USD'}
    # The entered values survive the refusal, so the difference can be fixed in place.
    assert _value(b, 'f:amount') == CHECK
    assert _value(b, _line(b, 1, 'amount')) == '95.40'
    assert b.evaluate('document.querySelectorAll(%s).length' % json.dumps(ROWS)) == 2


def test_a_card_charge_window_funds_from_the_card(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 900)
    books = _books(b, env.site, 'Card')

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/card-charge/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    assert b.evaluate('document.querySelector("h1").textContent').strip() == \
        'New credit card charge'
    # A card charge has no check number to enter.
    assert not b.evaluate('!!document.getElementsByName("f:number")[0]')
    _pick(b, 'f:account', 'Card card')
    _fill(b, 'f:date', '2026-03-05')
    _fill(b, 'f:amount', '75.25')
    _add_line(b)
    _pick(b, _line(b, 0, 'account'), 'Card fuel')
    _fill(b, _line(b, 0, 'amount'), '75.25')
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    assert _totals(b) == {'Expenses': '75.25 USD', 'Amount of this charge': '75.25 USD'}
    written = books['run']('journal.show', {'journal': _save(b)})
    sides = {line['account_id']: line['side'] for line in written['revision']['lines']}
    assert sides[books['card']] == 'credit' and sides[books['second']] == 'debit'
    assert books['bank'] not in sides


@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_the_expenses_grid_has_nothing_to_scroll_sideways_at_phone_width(register_browser, noun):
    """Below 700px a line stops being a row of a wide table and becomes its own block."""
    env, b = register_browser, register_browser.browser
    b.viewport(390, 844)
    tag = 'Phone ' + noun.replace('-', ' ')
    books = _books(b, env.site, tag)

    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/{noun}/post')
    b.wait_for('!!document.querySelector("[data-generated-form]")')
    _pick(b, 'f:account', f'{tag} checking' if noun == 'check' else f'{tag} card')
    _fill(b, 'f:date', '2026-03-04')
    _fill(b, 'f:amount', CHECK)
    for index, value in enumerate((FIRST, SECOND)):
        _add_line(b)
        _pick(b, _line(b, index, 'account'), f'{tag} parts' if index == 0 else f'{tag} fuel')
        _fill(b, _line(b, index, 'amount'), value)
        _fill(b, _line(b, index, 'memo'), 'A memo long enough to wrap onto a second line')

    _contained(b, 390)
    grid = b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return {scroll: g.scrollWidth, client: g.clientWidth,
                overflow: getComputedStyle(g).overflowX};})()''')
    assert grid['scroll'] == grid['client'], grid
    # Each block carries its own visible field names, because the column heads are gone.
    assert b.evaluate('getComputedStyle(document.querySelector(".line-head")).display') == 'none'
    assert b.evaluate('getComputedStyle(document.querySelector(".line-cell-label")).display') \
        == 'block'
    assert b.evaluate(f'''[...document.querySelectorAll({json.dumps(ROWS)} + " .line-cell")]
        .every(e => e.scrollWidth <= e.clientWidth + 1
                    && e.getBoundingClientRect().right <= innerWidth + 1)''')
    # The block's own reading order: what it was spent on and what it was for run the whole
    # width, and the two small numbers sit beside each other on one line.
    boxes = b.evaluate(f'''(() => {{const row = document.querySelector({json.dumps(ROWS)});
        const cell = name => row.querySelector(`[data-line-column="${{name}}"]`).getBoundingClientRect();
        const out = {{}};
        for (const name of ['account', 'amount', 'memo', 'class_id'])
            out[name] = {{top: Math.round(cell(name).top), width: Math.round(cell(name).width)}};
        return out;}})()''')
    assert boxes['amount']['top'] == boxes['class_id']['top'], boxes
    assert boxes['account']['width'] > boxes['amount']['width'] * 1.8, boxes
    assert boxes['memo']['width'] == boxes['account']['width'], boxes
    assert boxes['memo']['top'] > boxes['amount']['top'], boxes

    # Nothing was entered in the payee picker, so no payee is sent at all: the kind of name
    # standing at its opening value is not a half-filled name.
    assert _value(b, 'f:pay_to.name_type') == 'vendor'
    assert _value(b, 'f:pay_to.name_id') == ''
    _act(b, 'preview')
    assert not b.evaluate('document.querySelector(".error")?.textContent'), \
        b.evaluate('document.body.innerText')[:900]
    _contained(b, 390)
    assert b.evaluate('''(() => {const g = document.querySelector(".line-grid");
        return g.scrollWidth === g.clientWidth;})()''')
