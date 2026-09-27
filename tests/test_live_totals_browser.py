"""Totals that follow the form: the server's preview, run as the person edits.

What is checked here: without pressing Preview the footer shows the subtotal, tax, total and
the due date the core derived from the customer's terms, and each line's amount; the figures
are the ones the command then saves; refreshing them neither moves focus nor touches what is
being typed; a refused preview says why quietly beside the totals and nothing breaks; and
the Preview button still works as before.
"""
import json

import pytest

from bookflow.adapters.workbench import display as Display
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_sales_document_browser import _add_line, _fixture, _preview, _saved, _totals
from tests.test_service_sales_browser import _choose, _click, _fill

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')

LIVE = 'document.querySelector("[data-live-totals]")'


def _settled(b):
    """The background preview has answered and nothing is pending."""
    b.wait_for(f'!{LIVE}.hasAttribute("aria-busy")', timeout=30)


def test_totals_and_the_due_date_follow_the_form_without_preview(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    books = _fixture(run, 'Live')
    terms = run('term.create', {'name': 'Live Net 30', 'kind': 'standard', 'due_days': 30})['id']
    run('customer.update', {'customer': books['customer'], 'terms_id': terms,
                            'expected_version': run('customer.show', {'customer': books['customer']})['version']})
    b.viewport(1280, 900)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    b.evaluate('void (window.liveForm = document.querySelector("[data-sales-form]"))')
    assert 'Preview' not in b.evaluate(f'{LIVE}.textContent')

    _fill(b, 'f:date', '2026-05-06')
    _choose(b, 'f:customer', 'Live customer')
    _choose(b, 'f:ar_account', 'Live receivables')
    _add_line(b)
    _choose(b, 'c:lines:0:item', 'Live service')
    _fill(b, 'c:lines:0:quantity', '3')
    b.wait_for(f'!!{LIVE}.querySelector(".totals-list")', timeout=30)
    _settled(b)
    # The page was never replaced: this is the same form, updated in place.
    assert b.evaluate('window.liveForm.isConnected')
    shown = _totals(b)
    assert shown['Total'] == '$37.02', shown  # 3 x 12.34, no tax on this item
    assert b.evaluate('document.querySelector(".line-row .line-amount").textContent.trim()') == '37.02'
    assert b.evaluate(f'{LIVE}.querySelector("[data-totals-due]").textContent.trim()') == 'Due Jun 5, 2026 · Live Net 30'

    # Typing is never interrupted: focus and the half-typed value stay put through a refresh.
    b.evaluate('''(() => {const q = document.querySelector('.line-row input[name$=":quantity"]');
        q.focus(); q.value = '4'; q.dispatchEvent(new Event('input', {bubbles: true}));})()''')
    b.wait_for(f'{LIVE}.textContent.includes("49.36")', timeout=30)
    _settled(b)
    assert b.evaluate('document.activeElement.name.endsWith(":quantity") && document.activeElement.value === "4"')

    # A refused preview is a quiet note beside the totals, not an error page.
    b.evaluate('''(() => {const q = document.querySelector('.line-row input[name$=":quantity"]');
        q.value = '-'; q.dispatchEvent(new Event('change', {bubbles: true}));})()''')
    b.wait_for('!!document.querySelector("[data-live-totals-status]").textContent.includes("not up to date")', timeout=30)
    assert b.evaluate('window.liveForm.isConnected')
    assert not b.evaluate('document.querySelector("[data-submit-error-top]")')
    _fill(b, 'c:lines:0:quantity', '4')
    b.wait_for('document.querySelector("[data-live-totals-status]").textContent === ""', timeout=30)
    _settled(b)

    # What was shown is what saves: Save needs no separate Preview after a live one.
    shown = _totals(b)
    _click(b, 'submit')
    saved = run('invoice.show', {'invoice': _saved(b, 'invoice')})
    assert shown['Total'] == Display.money(saved['revision']['total']['amount'], 'USD')
    assert saved['revision']['profile']['due_date'] == '2026-06-05'


def test_the_preview_button_still_previews(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    _fixture(run, 'Button')
    b.viewport(390, 844)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-05-07')
    _choose(b, 'f:customer', 'Button customer')
    _choose(b, 'f:ar_account', 'Button receivables')
    _add_line(b)
    _choose(b, 'c:lines:0:item', 'Button service')
    _fill(b, 'c:lines:0:quantity', '1')
    _preview(b)
    assert _totals(b)['Total'] == '$12.34', _totals(b)
    assert json.loads(b.evaluate('JSON.stringify(document.documentElement.scrollWidth <= innerWidth + 1)'))


def test_a_totals_answer_that_is_not_the_form_says_so_quietly(register_browser):
    """An error page or a login page in answer leaves the totals alone and says they are not updated."""
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    _fixture(run, 'Lost')
    b.viewport(1280, 900)
    b.navigate(f'{env.site.base_url}/c/{env.site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]")')
    _fill(b, 'f:date', '2026-05-08')
    _choose(b, 'f:customer', 'Lost customer')
    _choose(b, 'f:ar_account', 'Lost receivables')
    _add_line(b)
    _choose(b, 'c:lines:0:item', 'Lost service')
    _fill(b, 'c:lines:0:quantity', '2')
    b.wait_for(f'{LIVE}.textContent.includes("24.68")', timeout=30)
    _settled(b)
    status = 'document.querySelector("[data-live-totals-status]").textContent'
    quiet = 'Totals could not be updated just now. Preview still checks them.'

    # The preview route answers with an error page.
    b.evaluate('''(() => {window.realFetch = window.fetch;
        window.fetch = () => Promise.resolve(new Response('<!doctype html><h1>Error</h1><p class="error">E_INTERNAL</p>', {status: 500}));})()''')
    _fill(b, 'c:lines:0:quantity', '3')
    b.wait_for(f'{status} === {json.dumps(quiet)}', timeout=30)
    _settled(b)
    assert b.evaluate(f'{LIVE}.textContent.includes("24.68")'), 'the last good totals stay'
    b.evaluate('window.fetch = window.realFetch')
    _fill(b, 'c:lines:0:quantity', '4')
    b.wait_for(f'{status} === "" && {LIVE}.textContent.includes("49.36")', timeout=30)
    _settled(b)

    # Signed out elsewhere: the answer is the login page, reached by a redirect.
    b.evaluate('''fetch("/logout", {method: "POST", credentials: "same-origin",
        headers: {"X-Bookflow-Workbench": "1", "HX-Request": "true"}})''', await_promise=True)
    _fill(b, 'c:lines:0:quantity', '5')
    b.wait_for(f'{status} === {json.dumps(quiet)}', timeout=30)
    assert b.evaluate(f'{LIVE}.textContent.includes("49.36")')
    assert b.evaluate('!!document.querySelector("[data-sales-form]")')
