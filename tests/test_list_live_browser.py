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
