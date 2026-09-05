"""Register route authority and lossless current-revision edit projections."""
import copy
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench.register import edit_projection
from tests.conftest import make_actor
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser


def _config(page):
    assert page.status_code == 200, page.text
    return json.loads(re.search(r'<script type="application/json" id="register-config">(.*?)</script>', page.text, re.S)[1])


@pytest.fixture
def register_records(hosted):
    company = hosted.company_id
    bank = hosted.ok('account.create', {'name': 'Workbench register bank', 'type': 'bank'}, company=company)
    expense = hosted.ok('account.create', {'name': 'Workbench register expense', 'type': 'expense'}, company=company)
    first = hosted.ok('register.post', {'account': bank['id'], 'date': '2026-02-12',
        'direction': 'decrease', 'amount': '100.00', 'category': expense['id'],
        'memo': '</script><script>alert(1)</script>'}, company=company)
    return bank, expense, first


def test_route_credential_today_escaped_edit_and_readonly_denial(hosted, root, register_records):
    bank, expense, first = register_records
    browser = _browser(hosted)
    path = f'/c/{hosted.company_id}/account/{bank["id"]}/register'
    page = browser.get(path + '?edit=' + first['id'])
    config = _config(page)
    assert config['actor'] and config['actor'] != hosted.token
    assert config['today'] == datetime.now(ZoneInfo(config['timezone'])).date().isoformat()
    assert config['edit']['payload']['memo'] == first['memo']
    assert '</script><script>alert(1)</script>' not in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert 'id="register-form"' in page.text
    assert config['edit']['payload']['category_line_id'] == first['revision']['lines'][1]['line_id']
    make_actor(root, 'register-reader', company_role=(hosted.company_id, 'readonly'))
    token = hosted.ok('token.issue', {'user': 'register-reader', 'label': 'read-only register'})
    reader = TestClient(hosted.handle.app)
    headers = {'Authorization': 'Bearer ' + token['secret']}
    readonly = reader.get(path + '?edit=' + first['id'], headers=headers)
    readonly_config = _config(readonly)
    assert not readonly_config['writable'] and readonly_config['edit'] is None
    assert 'id="register-form"' not in readonly.text
    assert readonly_config['actor'] != config['actor']
    forged = reader.post(f'/companies/{hosted.company_id}/commands/register.post', headers=headers,
        json={'account': bank['id'], 'date': '2026-02-12', 'amount': '1.00', 'direction': 'decrease', 'category': expense['id']})
    assert forged.status_code == 403, forged.text


def test_projection_noop_full_ids_and_incompatible_fallback(hosted, register_records):
    bank, expense, first = register_records
    projection = edit_projection(first, bank, lambda row: row['full_name'])
    out = hosted.ok('register.update', projection['payload'], company=hosted.company_id)
    assert out['changed'] is False
    assert out['version'] == first['version']
    assert out['revision'] == first['revision']
    cases = []
    foreign = copy.deepcopy(first); foreign['revision']['lines'][0]['original_currency'] = 'JPY'; cases.append(foreign)
    multiple = copy.deepcopy(first); multiple['revision']['lines'][1]['account_id'] = bank['id']; cases.append(multiple)
    selected_class = copy.deepcopy(first); selected_class['revision']['lines'][0]['class_id'] = 'some-class'; cases.append(selected_class)
    description = copy.deepcopy(first); description['revision']['lines'][0]['description'] = 'different'; cases.append(description)
    reordered = copy.deepcopy(first); reordered['revision']['lines'].reverse(); cases.append(reordered)
    dimension = copy.deepcopy(first); dimension['revision']['name_type'] = 'customer'; dimension['revision']['name_id'] = 'unsupported-header-party'; cases.append(dimension)
    for journal in cases:
        assert edit_projection(journal, bank, lambda row: row['full_name']) is None
    # One offset with a distinct description must remain a split, not a category.
    journal = hosted.ok('journal.post', {'date': '2026-02-13', 'memo': 'header', 'lines': [
        {'account': bank['id'], 'side': 'credit', 'amount': '5.00', 'description': 'header'},
        {'account': expense['id'], 'side': 'debit', 'amount': '5.00', 'description': ''},
    ]}, company=hosted.company_id)
    projection = edit_projection(journal, bank, lambda row: row['full_name'])
    assert 'category' not in projection['payload']
    assert projection['payload']['allocations'][0]['memo'] == ''
    assert projection['payload']['allocations'][0]['class_mode'] == 'none'
    out = hosted.ok('register.update', projection['payload'], company=hosted.company_id)
    assert out['changed'] is False


def test_income_and_nonposting_have_no_composer(hosted):
    browser = _browser(hosted)
    for account_type in ['expense', 'non_posting']:
        account = hosted.ok('account.create', {'name': 'No register ' + account_type, 'type': account_type}, company=hosted.company_id)
        page = browser.get(f'/c/{hosted.company_id}/account/{account["id"]}/register')
        config = _config(page)
        assert not config['writable'] and not config['supported']
        assert 'id="register-form"' not in page.text
        assert 'Account general ledger' in page.text


def test_incompatible_journal_opens_complete_editor(hosted, register_records):
    bank, expense, _ = register_records
    journal = hosted.ok('journal.post', {'date': '2026-02-13', 'lines': [
        {'account': bank['id'], 'side': 'credit', 'amount': '5.00'},
        {'account': bank['id'], 'side': 'debit', 'amount': '1.00'},
        {'account': expense['id'], 'side': 'debit', 'amount': '4.00'},
    ]}, company=hosted.company_id)
    page = _browser(hosted).get(f'/c/{hosted.company_id}/account/{bank["id"]}/register?edit={journal["id"]}')
    assert page.status_code == 303
    assert page.headers['location'] == f'/c/{hosted.company_id}/journal/{journal["id"]}/update'
