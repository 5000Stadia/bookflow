"""Bank browser pages: capability navigation, stale live checks and learned decisions."""
import html
import re

from tests.test_bank_feeds import csv_file
from tests.test_menus_and_home import H, _login, _member
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _call(hosted, name, raw):
    from bookflow.core import registry
    headers = {'X-Bookflow-Reason': 'Bank page test'} if registry.get(name.replace('.', ' ')).writes else {}
    return hosted.ok(name, raw, company=hosted.company_id, headers=headers)


def _hidden(page, name):
    found = re.search(r'<input[^>]*name="' + name + r'"[^>]*value="([^"]*)"', page)
    assert found, name
    return html.unescape(found.group(1))


def test_bank_pages_and_links_follow_capabilities(hosted):
    cid = hosted.company_id
    _member(hosted, 'bank-reader', 'readonly')
    _member(hosted, 'bank-denied', 'standard', ['bank-feed'])
    _member(hosted, 'bank-editor', 'standard', ['automate.bank-rule'])
    _call(hosted, 'bank-rule.create', dict(name='A proposed ignore', conditions=[
        {'field':'payee','op':'contains','value':'DUPLICATE'}], action='ignore'))
    reader = _login(hosted, 'bank-reader')
    section = reader.get(f'/c/{cid}/_group/banking')
    assert section.status_code == 200
    assert f'href="/c/{cid}/bank-inbox"' in section.text
    assert f'href="/c/{cid}/bank-rules"' in section.text
    rules = reader.get(f'/c/{cid}/bank-rules')
    assert rules.status_code == 200 and 'A proposed ignore' in rules.text
    assert 'data-bank-rule-save' not in rules.text and 'data-rule-live' not in rules.text
    editor = _login(hosted, 'bank-editor').get(f'/c/{cid}/bank-rules')
    assert editor.status_code == 200 and 'data-bank-rule-save' in editor.text
    assert 'data-rule-live' not in editor.text
    denied = _login(hosted, 'bank-denied')
    section = denied.get(f'/c/{cid}/_group/banking')
    assert f'href="/c/{cid}/bank-inbox"' not in section.text
    assert f'href="/c/{cid}/bank-rules"' not in section.text
    for page in ('bank-inbox','bank-rules','bank-import'):
        assert denied.get(f'/c/{cid}/{page}').status_code == 403


def test_bank_rule_confirmation_rejects_stale_and_changed_details(hosted):
    cid = hosted.company_id
    api = _login(hosted, hosted.login, PASSWORD)
    url = f'/c/{cid}/bank-rules'
    rule = _call(hosted, 'bank-rule.create', dict(name='Exact details', action='ignore',
        conditions=[{'field':'payee','op':'contains','value':'ONE'}]))['rule']
    plan = api.post(url, headers=H, data={'action':'live-check','rule':rule['id'],
                                         'expected_version':str(rule['version'])})
    assert plan.status_code == 200 and 'data-rule-confirm-save' in plan.text
    payload, fingerprint = _hidden(plan.text, 'payload'), _hidden(plan.text, 'confirm')
    refused = api.post(url, headers=H, data={'action':'confirm','rule':rule['id'],
                                           'payload':payload,'confirm':'0'*16})
    assert 'data-bank-rule-error' in refused.text and 'E_VALIDATION' in refused.text
    assert _call(hosted, 'bank-rule.show', {'rule':rule['id']})['rule']['state'] == 'draft'
    _call(hosted, 'bank-rule.update', {'rule':rule['id'],
                                      'conditions':[{'field':'payee','op':'contains','value':'TWO'}]})
    stale = api.post(url, headers=H, data={'action':'confirm','rule':rule['id'],
                                         'payload':payload,'confirm':fingerprint})
    assert 'E_VERSION_CONFLICT' in stale.text
    after = _call(hosted, 'bank-rule.show', {'rule':rule['id']})['rule']
    assert after['state'] == 'draft' and after['conditions'][0]['value'] == 'TWO'


def test_bank_rule_suggestions_accept_decline_and_apply_preview(hosted):
    cid = hosted.company_id
    api = _login(hosted, hosted.login, PASSWORD)
    url = f'/c/{cid}/bank-rules'
    imported = _call(hosted, 'bank-feed.import', {'account':'Checking','content':csv_file([
        ('2026-10-01','SUNNY FUEL ONE','-11.00'),('2026-10-02','SUNNY FUEL ONE','-12.00'),
        ('2026-10-03','HAPPY COFFEE TWO','-13.00'),('2026-10-04','HAPPY COFFEE TWO','-14.00')])})
    for line in imported['lines']:
        _call(hosted, 'bank-line.add', {'line':line['id'],'payee':'Central Supply','account':'Office Supplies'})
    suggested = _call(hosted, 'bank-rule.query', {'status':'suggested'})['rules']
    assert len(suggested) == 2
    listed = api.get(url)
    assert 'data-rule-accept' in listed.text and 'data-rule-decline' in listed.text
    for rule, action, status in zip(suggested, ('accept','decline'), ('active','retired')):
        posted = api.post(url, headers=H, data={'action':action,'rule':rule['id']})
        assert posted.status_code == 200 and 'data-bank-rule-error' not in posted.text
        assert _call(hosted, 'bank-rule.show', {'rule':rule['id']})['rule']['status'] == status
    accepted = suggested[0]
    # Preview and apply both use the command, never post entries while refreshing proposals.
    waiting = _call(hosted, 'bank-feed.import', {'account':'Checking','content':csv_file([
        ('2026-10-20',accepted['conditions'][0]['value'],'-20.00')])})['lines'][0]
    checked = api.post(url, headers=H, data={'action':'apply-check','rule':accepted['id']})
    assert checked.status_code == 200 and 'data-rule-apply-preview' in checked.text
    applied = api.post(url, headers=H, data={'action':'apply','rule':accepted['id']})
    assert applied.status_code == 200 and 'data-bank-rule-error' not in applied.text
    lines = _call(hosted, 'bank-line.query', {'account':'Checking'})['lines']
    assert next(line for line in lines if line['id']==waiting['id'])['state'] == 'waiting'


def test_posting_controls_require_nested_command_permissions(hosted):
    cid = hosted.company_id
    _member(hosted, 'no-ledger-post', 'standard', ['ledger.post'])
    line = _call(hosted, 'bank-feed.import', {'account':'Checking', 'content':csv_file([
        ('2026-10-01','PERMISSION TEST','-11.00'),
        ('2026-10-02','PERMISSION DEPOSIT','12.00')])})['lines'][0]
    client = _login(hosted, 'no-ledger-post')
    page = client.get(f'/c/{cid}/bank-inbox')
    assert page.status_code == 200
    for marker in ('data-bank-add', 'data-bank-details', 'data-bank-transfer', 'data-bank-pay-bills', 'data-bank-deposit'):
        assert marker not in page.text
    assert 'value="add"' not in page.text
    assert client.get(f'/c/{cid}/bank-inbox/add?line={line["id"]}').status_code == 403


def test_readonly_direct_sort_sheet_refuses(hosted):
    _member(hosted, 'sheet-reader', 'readonly')
    line = _call(hosted, 'bank-feed.import', {'account':'Checking', 'content':csv_file([
        ('2026-10-01','READONLY TEST','-11.00')])})['lines'][0]
    page = _login(hosted, 'sheet-reader').get(f'/c/{hosted.company_id}/bank-inbox/add?line={line["id"]}')
    assert page.status_code == 403
    assert 'data-bank-sort' not in page.text


def test_legacy_automation_deny_hides_live_control(hosted):
    from bookflow.hub.user_homes import Effective
    from tests.conftest import make_legacy
    _member(hosted, 'legacy-rule-denied', 'standard', ['automate.bank-rule'])
    # Preserve an older installation's stored deny while clearing its activated policy.
    make_legacy(hosted.root)
    _call(hosted, 'bank-rule.create', dict(name='Legacy draft', action='ignore',
        conditions=[{'field':'payee','op':'contains','value':'LEGACY'}]))
    effective = hosted.ok('membership.effective', {'company':hosted.company_id})
    assert Effective.from_output(effective).holds('automate.bank-rule') is None
    page = _login(hosted, 'legacy-rule-denied').get(f'/c/{hosted.company_id}/bank-rules')
    assert page.status_code == 200 and 'data-bank-rule-save' in page.text
    assert 'data-rule-live' not in page.text


def test_oversized_upload_refuses_inside_workspace(hosted):
    client = _login(hosted, hosted.login, PASSWORD)
    for size in (20_000_001, 22_000_001):
        response = client.post(f'/c/{hosted.company_id}/bank-import', headers={**H, 'HX-Request':'true'},
            data={'action':'check', 'account':'Checking'}, files={'file':('large.csv', b'x' * size, 'text/csv')})
        assert response.status_code == 200
        assert 'id="bank-import-workspace"' in response.text
        assert 'data-bank-import-error' in response.text
        assert '20 MB maximum' in response.text
