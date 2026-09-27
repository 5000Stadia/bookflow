"""A quieter document form, measured in a real browser.

What is checked here: Preview / Save stay at the bottom of the window while the form
scrolls and never cover the last field at its end; Class and Unit of measure are columns
only for a company that turned them on, their controls otherwise still in the row's own
panel; the row controls' column carries no "Row" label; a picker offers Clear only once it
holds something and lists "Add new …" as its last choice, reachable from the keyboard; the
calendar sits inside the date field and typed dates still post; and why a change is
recorded is one folded "Recording details" section. On a new record a custom field shows
only its value.
"""
import json
import re

import pytest

from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row5_workbench_forms import _browser
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_sales_document_browser import _add_line, _fixture, _preview, _saved
from tests.test_service_sales_browser import _choose, _click, _contained, _fill

DESKTOP, PHONE = 1280, 390

browser_only = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _picker(name):
    return f'document.getElementsByName({json.dumps(name)})[0].closest("[data-reference]")'


def _open(b, site):
    b.navigate(f'{site.base_url}/c/{site.company_id}/invoice/post')
    b.wait_for('!!document.querySelector("[data-sales-form]") && !!document.querySelector(".date-field")')
    _add_line(b)
    b.wait_for('!!document.querySelector(".line-row .line-cell")')


def _heads(b):
    return b.evaluate('[...document.querySelectorAll(".line-head .line-cell")].map(e => e.textContent.trim())')


@browser_only
def test_the_new_invoice_form_is_quiet_and_still_posts(register_browser):
    env, b = register_browser, register_browser.browser

    def run(name, payload):
        return _command(b, env.site, name, payload)

    run('company.update', {'use_classes': False, 'units_of_measure_mode': 'disabled'})
    books = _fixture(run, 'Quiet')
    b.viewport(DESKTOP, 900)
    _open(b, env.site)

    # Columns that match the business; the actions column needs no "Row" label.
    heads = _heads(b)
    assert 'Class' not in heads and not any(h.startswith('Unit of measure') for h in heads), heads
    assert heads[-1] == '', heads
    # The hidden columns' controls are still in the form, in the row's own panel.
    for field in ('class_id', 'unit'):
        assert b.evaluate(f'!!document.querySelector(".line-row .line-extras [name$=\\":{field}\\"]")'), field

    # Clear is offered only once the picker holds something.
    clear = f'getComputedStyle({_picker("f:ar_account")}.querySelector("[data-ref-clear]")).display'
    assert b.evaluate(clear) == 'none'
    _choose(b, 'f:ar_account', 'Quiet receivables')
    assert b.evaluate(clear) != 'none'

    # "Add new …" is the last choice in the list, not a link under the field.
    customer = _picker('f:customer')
    assert b.evaluate(f'getComputedStyle({customer}.querySelector(".reference-actions")).display') == 'none'
    _fill(b, 'label:f:customer', 'Quiet')
    b.wait_for(f'{customer}.querySelectorAll("[role=option]").length > 1')
    options = b.evaluate(f'[...{customer}.querySelectorAll("[role=option]")].map(e => e.textContent)')
    assert options[0] == 'Quiet customer' and options[-1].startswith('Add new'), options
    # From the keyboard: ↑ from the first choice wraps to Add new.
    b.evaluate(f'{customer}.querySelector("[role=option]").focus()')
    b.evaluate('document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowUp", bubbles: true}))')
    assert b.evaluate('document.activeElement.textContent').startswith('Add new')
    # An empty field asked to open (↓) offers only what can be added.
    b.evaluate(f'''(() => {{const s = {customer}.querySelector("[data-ref-search]"); s.value = "";
        s.dispatchEvent(new Event("input", {{bubbles: true}})); s.focus();
        s.dispatchEvent(new KeyboardEvent("keydown", {{key: "ArrowDown", bubbles: true}}));}})()''')
    b.wait_for(f'!{customer}.querySelector("[data-ref-options]").hidden')
    only = b.evaluate(f'[...{customer}.querySelectorAll("[role=option]")].map(e => e.textContent)')
    assert only and all(text.startswith('Add new') for text in only), only

    # The calendar is inside the date field's own box.
    box = b.evaluate('''(() => {const i = document.querySelector('[name="f:date"]');
        const c = i.parentElement.querySelector(".date-calendar");
        const r = e => e.getBoundingClientRect();
        return {i: [r(i).left, r(i).top, r(i).right, r(i).bottom], c: [r(c).left, r(c).top, r(c).right, r(c).bottom],
                name: c.getAttribute("aria-label")};})()''')
    i, c = box['i'], box['c']
    assert c[0] >= i[0] and c[2] <= i[2] + 1 and c[1] >= i[1] - 1 and c[3] <= i[3] + 1, box
    assert box['name'].startswith('Open calendar for'), box

    # Why this is recorded: one folded section, still in the form.
    context = b.evaluate('''(() => {const d = document.querySelector(".document-context");
        return {tag: d.tagName, open: d.open, reason: !!d.querySelector('[name="ctx:reason"]'),
                summary: d.querySelector("summary").textContent.trim()};})()''')
    assert context == {'tag': 'DETAILS', 'open': False, 'reason': True, 'summary': 'Recording details'}, context

    # Save stays at the bottom of the window while the form scrolls under it...
    b.evaluate('window.scrollTo(0, 0)')
    bar = b.evaluate('(() => {const r = document.querySelector(".document-actions").getBoundingClientRect(); return {top: r.top, bottom: r.bottom, h: innerHeight};})()')
    assert abs(bar['bottom'] - bar['h']) <= 1, bar
    # ...and at the end of the form it is back below the last field, covering nothing.
    b.evaluate('document.querySelector(".document-others").scrollIntoView({block: "end"})')
    end = b.evaluate('''(() => {const bar = document.querySelector(".document-actions").getBoundingClientRect();
        const last = document.querySelector(".document-context").getBoundingClientRect();
        return {bar: bar.top, last: last.bottom};})()''')
    assert end['last'] <= end['bar'], end

    # Typed values, including a typed date, still post the invoice the command would.
    _fill(b, 'f:date', '2026-05-06')
    _choose(b, 'f:customer', 'Quiet customer')
    _choose(b, 'c:lines:0:item', 'Quiet service')
    _fill(b, 'c:lines:0:quantity', '2')
    _fill(b, 'ctx:reason', 'Folded but still recorded')
    _preview(b)
    _click(b, 'submit')
    posted = run('invoice.show', dict(invoice=_saved(b, 'invoice')))
    assert posted['revision']['date'] == '2026-05-06'
    assert posted['revision']['lines'][0]['quantity'] == '2'

    # A company that uses classes and units sees both columns.
    run('company.update', {'use_classes': True, 'units_of_measure_mode': 'single_unit_per_item'})
    _open(b, env.site)
    heads = _heads(b)
    assert 'Class' in heads and any(h.startswith('Unit of measure') for h in heads), heads
    run('company.update', {'use_classes': False, 'units_of_measure_mode': 'disabled'})

    b.viewport(PHONE, 844)
    _open(b, env.site)
    _contained(b, PHONE)


def test_a_new_record_shows_a_custom_field_as_its_value_only(hosted):
    company = hosted.company_id
    field = hosted.ok('custom-field.create', {'name': 'Quiet witness', 'kind': 'text', 'scopes': ['invoice']},
                      company=company)['id']
    browser = _browser(hosted)
    page = browser.get(f'/c/{company}/invoice/post')
    assert page.status_code == 200, page.text[:600]
    assert re.search(rf'<input type="hidden" name="cf-state:{field}" value="keep">', page.text)
    assert f'<select name="cf-state:{field}"' not in page.text
    assert f'name="cf:{field}"' in page.text
    assert 'Keep stored value or absence' not in page.text
