"""Saved bills at phone width: real htmx previews, edits, stale refusal and cancellation."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _set(browser, name, value):
    browser.evaluate(f'document.querySelector({json.dumps("[name=" + chr(34) + name + chr(34) + "]")}).value = {json.dumps(value)}')


def _click(browser, selector):
    browser.evaluate("window.__draftSameDocument = true; document.querySelector('#purchase-drafts').dataset.before = '1'")
    browser.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
    browser.wait_for("!!document.querySelector('#purchase-drafts:not([data-before])')", timeout=60)
    assert browser.evaluate('window.__draftSameDocument === true')
    assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')


def test_phone_saved_bill_draft_workflow(register_browser):
    env = register_browser
    b, site = env.browser, env.site
    vendor = _command(b, site, 'vendor.create', {'name': 'Draft phone supplier'})
    base = f'{site.base_url}/c/{site.company_id}/purchase-drafts'
    b.viewport(390, 844)
    b.navigate(base + '?new=1')
    b.wait_for("!!document.querySelector('[data-draft-form]')")
    for field, value in {'vendor': vendor['id'], 'date': '2026-02-01',
                         'expense:0:account': env.expense['id'], 'expense:0:amount': '80.00',
                         'reason': 'Save supplier bill for another bookkeeper'}.items():
        _set(b, field, value)
    _click(b, '[data-draft-preview-button]')
    assert '80.00' in b.evaluate("document.querySelector('[data-draft-preview]').innerText")
    assert not b.evaluate("document.querySelector('#purchase-drafts').dataset.draftId")
    _click(b, '[data-draft-save]')
    assert b.evaluate("!!document.querySelector('[data-draft-saved]')")
    draft_id = b.evaluate("document.querySelector('#purchase-drafts').dataset.draftId")
    assert draft_id
    b.navigate(base + '?draft=' + draft_id + '&edit=1')
    b.wait_for("!!document.querySelector('[data-draft-form]')")
    shown = _command(b, site, 'purchase-draft.show', {'draft': draft_id})
    _command(b, site, 'purchase-draft.update', {**shown['payload'], 'draft': draft_id,
               'expected_version': shown['version'], 'operation_key': 'phone-other-edit', 'memo': 'Changed elsewhere'},
             **{'X-Bookflow-Reason': 'Another interface resumes the draft'})
    _set(b, 'memo', 'My retained draft edit')
    _set(b, 'reason', 'Review supplier detail')
    _click(b, '[data-draft-save]')
    assert b.evaluate("!!document.querySelector('[data-draft-error]')")
    assert b.evaluate("document.querySelector('[name=memo]').value") == 'My retained draft edit'
    assert b.evaluate("document.querySelector('[name=reason]').value") == 'Review supplier detail'
    b.navigate(base + '?draft=' + draft_id + '&edit=1')
    b.wait_for("!!document.querySelector('[data-draft-form]')")
    _set(b, 'memo', 'Resumed and checked')
    _set(b, 'reason', 'Checked latest version')
    _click(b, '[data-draft-save]')
    assert b.evaluate("!!document.querySelector('[data-draft-saved]')")
    b.evaluate("document.querySelector('details:has([data-draft-cancel])').open = true")
    _set(b, 'reason', 'Supplier withdrew this bill')
    _click(b, '[data-draft-cancel]')
    assert b.evaluate("document.querySelector('[data-draft-state]').innerText") == 'cancelled'
    assert b.evaluate("document.querySelectorAll('[data-draft-history]').length") >= 4
    assert 'Supplier withdrew this bill' in b.evaluate("document.querySelector('#purchase-drafts').innerText")
    assert not b.evaluate("!!document.querySelector('[data-draft-edit]')")
    b.navigate(base + '?state=cancelled')
    b.wait_for("!!document.querySelector('#purchase-drafts')")
    assert b.evaluate("Array.from(document.querySelector('[name=state]').options, x => x.value)") == ['', 'draft', 'pending', 'approved', 'rejected', 'committed', 'cancelled']
    assert 'Draft phone supplier' in b.evaluate("document.querySelector('#purchase-drafts').innerText")
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
