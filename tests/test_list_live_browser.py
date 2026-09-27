"""List controls apply as they change, and a phone keeps them in one sheet (R58)."""
import time

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import register_browser, _command, _type  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome unavailable")

VISIBLE_CONTROLS = """[...document.querySelectorAll('#{form} button, #{form} a')]
    .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
    .map(e => e.textContent.trim())"""


def test_search_applies_as_typed_and_switches_apply_on_change(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(1280, 850)
    for name in ('Livesearch alpha', 'Livesearch beta'):
        _command(b, env.site, 'customer.create', {'name': name})
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/customer')
    b.wait_for("window.bookflowList?.idle() && document.querySelector('#browse-form')?.dataset.ready === '1'")
    shown = b.evaluate(VISIBLE_CONTROLS.format(form='browse-form'))
    for gone in ('Apply', 'Clear filters', 'Reset all controls', 'Show results', 'Reset'):
        assert gone not in shown, shown
    b.evaluate("window.samePage = true; document.querySelector('.list-search input').focus()")
    _type(b, 'Livesearch')
    _type(b, ' al')
    b.wait_for("location.search.includes('query=Livesearch+al') && window.bookflowList.idle()")
    # Applied in place: the page did not reload, focus and the caret stay in the field.
    assert b.evaluate('window.samePage === true')
    assert b.evaluate("document.activeElement === document.querySelector('.list-search input')")
    assert b.evaluate("document.activeElement.selectionStart === document.activeElement.value.length")
    results = b.evaluate("document.querySelector('#list-results').innerText")
    assert 'Livesearch alpha' in results and 'Livesearch beta' not in results
    assert '1 matching records' in results
    assert 'Reset' in b.evaluate(VISIBLE_CONTROLS.format(form='browse-form'))
    # A switch applies on change, keeping the search.
    b.evaluate("document.querySelector('[name=include_inactive]').click()")
    b.wait_for("location.search.includes('include_inactive=1') && window.bookflowList.idle()")
    assert 'query=Livesearch+al' in b.evaluate('location.search') and b.evaluate('window.samePage === true')


def test_phone_filters_sheet_applies_once_on_show_results(register_browser):
    env, b = register_browser, register_browser.browser
    b.viewport(390, 850)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice')
    b.wait_for("window.bookflowList?.idle()")
    bar = b.evaluate("[...document.querySelectorAll('.list-bar a')].map(a => a.textContent.trim())")
    assert bar == ['Filters', 'Sort', '+']
    assert 'invoice' in b.evaluate("document.querySelector('.list-bar-add').getAttribute('aria-label').toLowerCase()")
    # The sheet holds the filters; changing one inside it does not reload the list behind it.
    assert not b.evaluate("document.querySelector('#list-sheet').getClientRects().length")
    b.evaluate("document.querySelector('.list-bar [data-sheet-open]').click()")
    b.wait_for("document.querySelector('#list-sheet').getClientRects().length > 0")
    b.evaluate("document.querySelector('[name=include_deleted]').click()")
    time.sleep(0.6)
    assert b.evaluate('location.search') == ''
    assert b.evaluate("window.bookflowList.idle()")
    b.evaluate("document.querySelector('#list-sheet .list-apply').click()")
    b.wait_for("location.search.includes('include_deleted=true') && window.bookflowList.idle()")
    assert not b.evaluate("document.querySelector('.list-form').classList.contains('sheet-open')")
    assert b.evaluate("document.querySelector('.list-bar a').textContent.trim()") == 'Filters (1)'
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def test_without_javascript_the_list_is_a_plain_get_form(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'/c/{env.site.company_id}/invoice'
    b.navigate(env.site.base_url + base)
    page = lambda url: b.evaluate(f"fetch({url!r}, {{credentials: 'same-origin'}}).then(r => r.text())", await_promise=True)
    opened = page(base)
    assert '<form method="get" class="list-tools list-form' in opened
    assert 'name="number"' in opened and 'type="submit" class="list-apply"' in opened
    assert 'id="list-reset" class="list-reset" data-list-sync hidden' in opened
    searched = page(base + '?number=DEMO&include_deleted=true')
    assert 'value="DEMO"' in searched and 'name="include_deleted" value="true" checked' in searched
    assert 'id="list-reset" class="list-reset" data-list-sync>' in searched


def test_ledger_rows_on_a_wide_screen_and_cards_on_a_phone(register_browser):
    """R59: fewer default columns, figures right-aligned under a heading that names the
    currency and stays put, the whole row (or card) opens the record."""
    env, b = register_browser, register_browser.browser
    _command(b, env.site, 'customer.create', {'name': 'Ledger row customer', 'phone': '555-0142'})
    _command(b, env.site, 'customer.create', {'name': 'Ledger row quiet'})
    url = f'{env.site.base_url}/c/{env.site.company_id}/customer?query=Ledger+row'
    b.viewport(1280, 850)
    b.navigate(url)
    b.wait_for("window.bookflowList?.idle() && !!document.querySelector('#master-results')")
    heads = b.evaluate("[...document.querySelectorAll('#master-results th')].map(th => th.innerText.trim())")
    assert heads == ['Name ↑', 'Phone', 'Open balance (USD)']
    style = b.evaluate("""(() => {const th = document.querySelector('#master-results th.num'), td = document.querySelector('#master-results td.num'),
        s = getComputedStyle(td), h = getComputedStyle(th);
        return [s.textAlign, s.fontVariantNumeric, h.position, h.whiteSpace, td.innerText.trim()];})()""")
    assert style == ['right', 'tabular-nums', 'sticky', 'nowrap', '0.00']
    # A click anywhere on the row, not only on the name, opens the record.
    b.evaluate("[...document.querySelectorAll('#master-results tbody tr')].find(r => r.textContent.includes('Ledger row customer')).querySelector('td.num').click()")
    b.wait_for("document.querySelector('h1')?.textContent.includes('Ledger row customer')")

    b.viewport(390, 850)
    b.navigate(url)
    b.wait_for("window.bookflowList?.idle() && document.querySelectorAll('.list-card').length === 2")
    cards = b.evaluate("""[...document.querySelectorAll('.list-card')].map(c => {
        const link = c.querySelector('.list-card-link').getBoundingClientRect(), amount = c.querySelector('.list-card-amount').getBoundingClientRect();
        return {text: c.innerText, sameLine: Math.abs(link.top - amount.top) < 6, amountRight: amount.right > link.right};})""")
    assert all(card['sameLine'] and card['amountRight'] for card in cards), cards
    assert [t for t in cards[0]['text'].split('\n') if t.strip()] == ['Ledger row customer', '$0.00', '555-0142']
    # An empty field says nothing at all ("Phone: —" is gone).
    assert [t for t in cards[1]['text'].split('\n') if t.strip()] == ['Ledger row quiet', '$0.00']
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    # The whole card is the tap target: its muted line opens the record too.
    point = b.evaluate("""(() => {const r = document.querySelector('.list-card .list-card-sub').getBoundingClientRect();
        return {x: r.right - 4, y: r.top + r.height / 2};})()""")
    for event in ('mousePressed', 'mouseReleased'):
        b.call('Input.dispatchMouseEvent', {'type': event, 'x': point['x'], 'y': point['y'], 'button': 'left', 'clickCount': 1})
    b.wait_for("document.querySelector('h1')?.textContent.includes('Ledger row customer')")
