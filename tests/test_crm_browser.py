"""A name-only lead, attributed note and native follow-up through real 390 px forms."""
import json

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401
from tests.crm_support import company as _company

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


@pytest.fixture
def crm_site(tmp_path, monkeypatch):
    # This journey needs an empty company, not the complete accounting demo.
    books = _company(tmp_path / 'minimal', monkeypatch, 'UTC')
    yield from browser_site.__wrapped__(tmp_path, monkeypatch, books.root)


def test_name_only_lead_note_followup_at_390(crm_site, tmp_path):
    browser_site = crm_site
    b = _Cdp(tmp_path / 'crm-chrome')
    try:
        b.viewport(390, 844)
        b.navigate(browser_site.base_url + '/login')
        b.evaluate(f'''(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('[name="next"]').value = '/c/{browser_site.company_id}/lead?search=Phone%20enquiry';
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()''')
        b.wait_for("!!document.querySelector('#crm-workspace')")
        base = f'{browser_site.base_url}/c/{browser_site.company_id}'
        b.evaluate('''(() => {
          document.querySelector('#new-lead').open = true;
          document.querySelector('#lead-create [name="display_name"]').value = 'Phone enquiry';
          document.querySelector('#lead-create button[type="submit"]').click();
        })()''')
        b.wait_for("!!document.querySelector('#follow-up-form')")
        assert b.evaluate("document.querySelector('#crm-workspace h1').textContent") == 'Phone enquiry'
        lead_id = b.evaluate("document.querySelector('#crm-workspace').dataset.lead")
        b.wait_for("document.querySelector('[data-annotations]')?.dataset.ready === 'true'")
        b.evaluate('''(() => {
          document.querySelector('[data-note-add] textarea').value = 'Asked for a callback after lunch.';
          document.querySelector('[data-note-add] button').click();
        })()''')
        b.wait_for("document.querySelector('[data-note-add] [data-status]').textContent.includes('Note added.') || document.querySelector('[data-note-add] [data-status]').dataset.failed === 'true'", timeout=4)
        assert b.evaluate("document.querySelector('[data-note-add] [data-status]').textContent") == 'Note added.'
        b.wait_for("document.querySelector('[data-section=notes] [data-items]').textContent.includes('Asked for a callback after lunch.')", timeout=4)
        assert b.evaluate("document.querySelector('[data-section=notes] [data-items] .muted').textContent")
        b.evaluate('''(() => {
          document.querySelector('#follow-up-form [name="title"]').value = 'Return enquiry call';
          document.querySelector('#follow-up-form [name="due_date"]').value = '2026-01-01';
          document.querySelector('#follow-up-form').requestSubmit();
        })()''')
        assert b.evaluate("document.querySelector('#follow-up-form').checkValidity()")
        b.wait_for("document.querySelector('#crm-workspace').textContent.includes('Follow-up added.')", timeout=3)
        assert b.evaluate("document.querySelector('#crm-workspace').textContent.includes('Follow-up added.')"), b.evaluate("document.querySelector('#crm-workspace').textContent")
        assert b.evaluate("document.querySelectorAll('[data-todo]').length") == 1
        todo_id = b.evaluate("document.querySelector('[data-todo]').dataset.todo")
        assert b.evaluate('document.documentElement.scrollWidth') <= 391
        assert b.evaluate("[...document.querySelectorAll('.crm-actions a')].every(e => e.getBoundingClientRect().height >= 44)")
        b.navigate(base + '/reminders?as_of=2026-01-02')
        b.wait_for("!!document.querySelector('[data-reminder-section=todos_due]')")
        selector = f'[data-reminder-section=todos_due] [data-todo="{todo_id}"]'
        assert b.evaluate(f'document.querySelectorAll({json.dumps(selector)}).length') == 1
        assert 'Return enquiry call' in b.evaluate("document.querySelector('[data-reminder-section=todos_due]').textContent")
    finally:
        b.close()


def test_lead_edit_preserves_owner_selector():
    from bookflow.adapters.workbench.pages import _editable_values
    shown = {'id': 'lead-id', 'display_name': 'Enquiry', 'owner_id': 'member-id', 'contacts': []}
    assert _editable_values('lead', shown)['owner'] == 'member-id'
    assert shown == {'id': 'lead-id', 'display_name': 'Enquiry', 'owner_id': 'member-id', 'contacts': []}


def test_agent_converted_lead_continues_in_browser_at_390(crm_site, tmp_path, monkeypatch):
    """An agent converts a lead over its bearer; a person opens it at 390 px and sees one unbroken history, then
    converts a second lead through the review sheet."""
    import os
    from pathlib import Path
    import httpx
    from bookflow.core.config import Config
    from tests.conftest import browser_call, make_agent
    site = crm_site
    b = _Cdp(tmp_path / 'crm-convert-chrome')
    try:
        b.viewport(390, 844)
        b.navigate(site.base_url + '/login')
        b.evaluate(f'''(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('[name="next"]').value = '/c/{site.company_id}/lead';
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()''')
        b.wait_for("!!document.querySelector('#crm-workspace')")
        root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
        principal = Config.load(root / 'config.toml').user_table(site.login)['user_id']
        agent = make_agent(browser_call(b), 'crm-agent', principals=principal, company=site.company_id)
        issued = browser_call(b)('token issue', dict(user=agent, principal=principal, label='CRM conversion witness'))
        prefix = f'/companies/{site.company_id}/commands/'
        with httpx.Client(base_url=site.base_url, trust_env=False, timeout=15, headers={
                'Authorization': f"Bearer {issued['secret']}", 'X-Bookflow-Client-Name': 'crm-agent',
                'X-Bookflow-Reason': 'Won the roof job'}) as api:
            def call(name, data):
                response = api.post(prefix + name.replace(' ', '.'), json=data)
                assert response.status_code == 200, response.text
                return response.json()
            lead = call('lead create', {'display_name': 'Agent roof enquiry', 'contacts': [
                {'role': 'primary', 'display_name': 'Bo', 'primary_email': 'bo@example.test'}]})
            call('note add', {'record_type': 'lead', 'record_id': lead['id'], 'body': 'Agent called back'})
            out = call('lead convert', {'lead': lead['id'], 'expected_version': lead['version'], 'target': 'new'})
            second = call('lead create', {'display_name': 'Walk-in enquiry'})
        base = f'{site.base_url}/c/{site.company_id}'
        b.navigate(f"{base}/lead/{lead['id']}")
        b.wait_for("!!document.querySelector('#converted-to')")
        assert 'Agent roof enquiry' in b.evaluate("document.querySelector('#converted-to').textContent")
        history = b.evaluate("document.querySelector('ol.crm-history').textContent")
        assert 'Converted' in history and 'Agent called back' in history, history
        assert not b.evaluate("[...document.querySelectorAll('.crm-actions a')].some(a => a.textContent === 'Convert to customer')")
        assert b.evaluate('document.documentElement.scrollWidth') <= 391
        b.evaluate("document.querySelector('#converted-to a').click()")
        b.wait_for(f"location.pathname.endsWith('/customer/{out['conversion']['customer_id']}')")
        b.navigate(f"{base}/crm/history?customer={out['conversion']['customer_id']}")
        b.wait_for("!!document.querySelector('ol.crm-history')")
        assert 'From lead Agent roof enquiry' in b.evaluate("document.querySelector('ol.crm-history').textContent")
        # The person converts the second lead through the review sheet.
        b.navigate(f"{base}/lead/{second['id']}")
        b.wait_for("!!document.querySelector('.crm-actions')")
        b.evaluate("[...document.querySelectorAll('.crm-actions a')].find(a => a.textContent === 'Convert to customer').click()")
        b.wait_for("!!document.querySelector('#convert-cancel')")
        assert b.evaluate("document.querySelector('#convert-cancel').getBoundingClientRect().height") >= 44
        b.evaluate("document.querySelector('#crm-workspace form[method=get] button').click()")
        b.wait_for("!!document.querySelector('#convert-form')")
        assert 'Creates customer Walk-in enquiry' in b.evaluate("document.querySelector('#convert-review').parentElement.textContent")
        assert b.evaluate('document.documentElement.scrollWidth') <= 391
        assert b.evaluate("[...document.querySelectorAll('#crm-workspace button')].every(e => e.getBoundingClientRect().height >= 44)")
        b.evaluate("document.querySelector('#convert-form').requestSubmit()")
        b.wait_for("!!document.querySelector('#converted-to')", timeout=6)
        assert 'Walk-in enquiry' in b.evaluate("document.querySelector('#converted-to').textContent")
        assert b.evaluate('document.documentElement.scrollWidth') <= 391
    finally:
        b.close()
