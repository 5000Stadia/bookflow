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
