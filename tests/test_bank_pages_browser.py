"""R108 increment 7: real Chrome imports, sorts and reverses at desktop and phone widths.

Every tested posting form submits through htmx and keeps the document alive. Screenshots
are saved outside the repository, to /tmp/m4-shots/bankpages (BF_BANK_SHOTS overrides it).
"""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, register_browser  # noqa: F401
from tests.test_bank_feeds import csv_file

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def _set(b, selector, value):
    b.evaluate(f'''(() => {{ const e = document.querySelector({json.dumps(selector)});
        e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{bubbles:true}}));
        e.dispatchEvent(new Event('change', {{bubbles:true}})); }})()''')


def _click(b, selector, workspace, *, error=False):
    b.evaluate(f'''(() => {{ window.__bankDocument = true; window.__bankOld = document.querySelector({json.dumps(workspace)});
        window.__bankFailed = null; document.body.addEventListener('htmx:responseError', e => {{
        window.__bankFailed = e.detail.xhr.status; }}); document.querySelector({json.dumps(selector)}).click(); }})()''')
    b.wait_for('!window.__bankOld.isConnected || !!window.__bankFailed', timeout=120)
    assert not b.evaluate('window.__bankFailed'), b.evaluate('window.__bankFailed')
    assert b.evaluate('window.__bankDocument === true'), 'form navigated instead of submitting through htmx'
    if not error:
        assert not b.evaluate('!!document.querySelector("[data-bank-error], [data-bank-import-error], [data-bank-rule-error]")'), b.evaluate('document.body.innerText')


def _fits(b):
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), b.evaluate(
        '[...document.querySelectorAll("body *")].filter(e => e.getBoundingClientRect().right > innerWidth + 1)'
        '.slice(0,8).map(e => e.tagName + "." + e.className).join(" ")')


def _shot(b, width, name):
    _fits(b)
    folder = Path(os.environ.get('BF_BANK_SHOTS', '/tmp/m4-shots/bankpages'))
    folder.mkdir(parents=True, exist_ok=True)
    b.evaluate('document.activeElement?.blur(); window.scrollTo(0,0)')
    height = (b.evaluate('innerHeight') if b.evaluate('!!document.querySelector("[role=dialog]")') else
              min(b.evaluate('Math.max(innerHeight, document.documentElement.scrollHeight)'), 6000))
    (folder / f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot', {
        'format':'png', 'captureBeyondViewport':True,
        'clip':{'x':0, 'y':0, 'width':width, 'height':height, 'scale':1}})['data']))


def _hub(b, name, payload):
    result = b.evaluate(f'''fetch('/commands/{name}', {{method:'POST', credentials:'same-origin',
        headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1'}},
        body:JSON.stringify({json.dumps(payload)})}}).then(async r => ({{status:r.status, body:await r.json()}}))''', await_promise=True)
    assert result['status'] == 200, result
    return result['body']


def _grant(env, *grants):
    b, site = env.browser, env.site
    member = next(x for x in _hub(b, 'membership.list', {'company':site.company_id})['items'] if x['username'] == site.login)
    _hub(b, 'membership.grant', {'user':member['user_id'], 'company':site.company_id,
        'expected_version':member['version'], 'role':member['role'], 'grants':list(set(member['grants']) | set(grants))})


def _import_browser(env, content, width, *, account=None):
    b, site = env.browser, env.site
    account = account or env.bank['id']
    base = f'{site.base_url}/c/{site.company_id}'
    b.navigate(base + '/bank-import')
    b.wait_for('!!document.querySelector("[data-bank-check]")', timeout=60)
    _set(b, '#bank-import-workspace [name=account]', account)
    _set(b, '#bank-import-workspace [name=content]', content)
    _set(b, '#bank-import-workspace [name=auto_match]', 'off')
    _shot(b, width, 'import-file')
    _click(b, '[data-bank-check]', '#bank-import-workspace')
    assert b.evaluate('!!document.querySelector("[data-bank-import-confirm]")'), b.evaluate('document.body.innerText')
    _shot(b, width, 'import-check')
    _click(b, '[data-bank-import-confirm]', '#bank-import-workspace')
    _shot(b, width, 'import-done')
    rows = _command(b, site, 'bank-feed.query', {'account':account})['imports']
    assert rows
    return rows[0]['id']


@pytest.mark.parametrize('width', (1440,390))
def test_phone_import_sort_and_undo(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    b.viewport(width,900)
    _grant(env, 'transaction.check.delete')
    _command(b, site, 'bank-rule.create', {'name':'Browser fuel', 'account':env.bank['id'],
        'conditions':[{'field':'payee','op':'contains','value':'BROWSER FUEL'}],
        'payee':'Central Supply', 'category':env.expense['id']})
    _command(b, site, 'journal.post', {'date':'2026-10-06', 'memo':'Browser existing charge', 'lines':[
        {'account':env.bank['id'], 'side':'credit', 'amount':'23.00'},
        {'account':env.expense['id'], 'side':'debit', 'amount':'23.00'}]})
    before = _command(b, site, 'account.show', {'account':env.bank['id']})['balance']
    _command(b, site, 'bank-feed.import', {'account':env.bank['id'],
        'content':csv_file([('2026-10-07','IGNORE TEST 1','-9.00')])})
    imported = _import_browser(env, csv_file([('2026-10-06','BROWSER FUEL 12','-45.00'),
        ('2026-10-06','EXISTING CHARGE','-23.00'), ('2026-10-07','IGNORE TEST','-9.00')]), width)
    base = f'{site.base_url}/c/{site.company_id}'
    b.navigate(base + '/bank-inbox?' + urlencode({'account':env.bank['id']}))
    b.wait_for('!!document.querySelector("#bank-workspace")', timeout=60)
    lines = _command(b, site, 'bank-line.query', {'import_id':imported})['lines']
    fuel = next(x for x in lines if 'FUEL' in x['payee'])
    ignore = next(x for x in lines if x['payee'] == 'IGNORE TEST')
    matched = next(x for x in lines if x['payee'] == 'EXISTING CHARGE')
    assert matched['state'] == 'waiting'
    assert ignore['possible_duplicate_of']
    assert 'Held for review: possible duplicate' in b.evaluate('document.body.innerText')
    assert 'Browser fuel' in b.evaluate('document.body.innerText')
    _shot(b, width, 'inbox-proposals')
    _click(b, f'[data-bank-line="{fuel["id"]}"] [data-bank-add]', '#bank-workspace')
    for line, action in ((matched, 'match'), (ignore, 'ignore')):
        selector = f'[data-bank-line="{line["id"]}"] [data-bank-{action}]'
        b.evaluate(f'document.querySelector({json.dumps(selector)}).click()')
        b.wait_for('!!document.querySelector("[data-bank-sort]")', timeout=60)
        _shot(b, width, action + '-sheet')
        _click(b, '[data-bank-save]', '#bank-workspace')
    states = {x['id']:x['state'] for x in _command(b, site, 'bank-line.query', {'import_id':imported})['lines']}
    assert states == {fuel['id']:'added', ignore['id']:'ignored', matched['id']:'matched'}
    _shot(b, width, 'inbox-sorted')
    b.navigate(base + '/bank-import?' + urlencode({'import':imported}))
    b.wait_for('!!document.querySelector("[data-bank-undo-plan]")', timeout=60)
    _click(b, '[data-bank-undo-plan]', '#bank-import-workspace')
    _shot(b, width, 'undo-plan')
    _click(b, '[data-bank-undo-confirm]', '#bank-import-workspace')
    assert _command(b, site, 'bank-line.query', {'import_id':imported})['lines'] == []
    assert _command(b, site, 'account.show', {'account':env.bank['id']})['balance'] == before
    _shot(b, width, 'undo-done')


@pytest.mark.parametrize('width', (1440,390))
def test_rules_exact_confirmation_and_edit(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    b.viewport(width,900)
    base = f'{site.base_url}/c/{site.company_id}'
    b.navigate(base + '/bank-rules')
    b.wait_for('!!document.querySelector("[data-bank-rule-save]")', timeout=60)
    for name,value in {'name':'Browser ignore rule', 'account':env.bank['id'],
        'condition_0_value':'RULE IGNORE', 'rule_action':'ignore', 'ignore_reason':'duplicate'}.items():
        _set(b, f'#rule-editor [name="{name}"]', value)
    _click(b, '[data-bank-rule-save]', '#bank-rules')
    rule = next(x for x in _command(b, site, 'bank-rule.query', {})['rules'] if x['name'] == 'Browser ignore rule')
    assert rule['state'] == 'draft'
    _shot(b, width, 'rules-draft')
    _click(b, f'[data-bank-rule="{rule["id"]}"] [data-rule-live]', '#bank-rules')
    assert b.evaluate('!!document.querySelector("[data-rule-confirm] input[name=confirm]")')
    assert _command(b, site, 'bank-rule.show', {'rule':rule['id']})['rule']['state'] == 'draft'
    _shot(b, width, 'rules-exact-confirm')
    _click(b, '[data-rule-confirm-save]', '#bank-rules')
    assert _command(b, site, 'bank-rule.show', {'rule':rule['id']})['rule']['state'] == 'live'
    # A live edit earns a new exact-details review, including the operative new condition.
    b.navigate(base + '/bank-rules?' + urlencode({'rule':rule['id']}))
    b.wait_for('!!document.querySelector("[data-bank-rule-save]")', timeout=60)
    _set(b, '#rule-editor [name=condition_0_value]', 'RULE IGNORE EDITED')
    _click(b, '[data-bank-rule-save]', '#bank-rules')
    assert 'RULE IGNORE EDITED' in b.evaluate('document.querySelector("[data-rule-confirm]").innerText')
    old = _command(b, site, 'bank-rule.show', {'rule':rule['id']})['rule']
    assert old['conditions'][0]['value'] == 'RULE IGNORE'
    _click(b, '[data-rule-confirm-save]', '#bank-rules')
    edited = _command(b, site, 'bank-rule.show', {'rule':rule['id']})['rule']
    assert edited['state'] == 'live' and edited['conditions'][0]['value'] == 'RULE IGNORE EDITED'
    _shot(b, width, 'rules-live')
    imported = _command(b, site, 'bank-feed.import', {'account':env.bank['id'],
        'content':csv_file([('2026-10-11','RULE IGNORE EDITED','-6.00')])})
    assert imported['lines'][0]['state'] == 'ignored'


@pytest.mark.parametrize('width', (1440,390))
def test_move_import_checks_target_and_keeps_lines(register_browser, width):
    env,b = register_browser, register_browser.browser
    site = env.site
    b.viewport(width,900)
    target = _command(b, site, 'account.create', {'name':'Browser right bank','type':'bank'})['id']
    imported = _command(b, site, 'bank-feed.import', {'account':env.bank['id'],
        'content':csv_file([('2026-10-10','MOVE ME','-7.50')])})['import_id']
    b.navigate(f'{site.base_url}/c/{site.company_id}/bank-import?' + urlencode({'import':imported}))
    b.wait_for('!!document.querySelector("[data-bank-move-plan]")', timeout=60)
    _set(b, '#bank-import-workspace [name=to]', target)
    _click(b, '[data-bank-move-plan]', '#bank-import-workspace')
    _shot(b, width, 'move-check')
    _click(b, '[data-bank-move-confirm]', '#bank-import-workspace')
    assert _command(b, site, 'bank-line.query', {'account':env.bank['id']})['lines'] == []
    moved = _command(b, site, 'bank-line.query', {'account':target})['lines']
    assert len(moved) == 1 and moved[0]['payee'] == 'MOVE ME'
    _shot(b, width, 'move-done')


def test_phone_resumes_an_undo_after_reload(register_browser, monkeypatch):
    from bookflow.adapters.workbench import bank_import_pages
    monkeypatch.setattr(bank_import_pages, 'REVERSE_SLICE', 1)
    env,b = register_browser, register_browser.browser
    site = env.site
    b.viewport(390,844)
    _grant(env, 'transaction.check.delete')
    imported = _command(b, site, 'bank-feed.import', {'account':env.bank['id'],
        'content':csv_file([('2026-10-10','SLICE ONE','-7.50'),('2026-10-11','SLICE TWO','-8.50')])})
    for line in imported['lines']:
        _command(b, site, 'bank-line.add', {'line':line['id'], 'payee':'Central Supply', 'account':env.expense['id']})
    url = f'{site.base_url}/c/{site.company_id}/bank-import?' + urlencode({'import':imported['import_id']})
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-bank-undo-plan]")', timeout=60)
    _click(b, '[data-bank-undo-plan]', '#bank-import-workspace')
    _click(b, '[data-bank-undo-confirm]', '#bank-import-workspace')
    assert b.evaluate('!!document.querySelector("[data-bank-progress]")')
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-bank-resume]")', timeout=60)
    assert '1 of 2' in b.evaluate('document.querySelector("[data-bank-progress]").innerText')
    _shot(b,390,'undo-resume')
    _click(b, '[data-bank-resume]', '#bank-import-workspace')
    assert _command(b, site, 'bank-line.query', {'import_id':imported['import_id']})['lines'] == []
    _shot(b,390,'undo-resumed-done')


@pytest.mark.parametrize('width', (1440,390))
def test_transfer_deposit_and_pay_bills_sheets(register_browser, width):
    env,b = register_browser, register_browser.browser
    site = env.site
    b.viewport(width,900)
    base = f'{site.base_url}/c/{site.company_id}'
    methods = _command(b, site, 'payment-method.list', {})['items']
    method = next(x['id'] for x in methods if x['name'] == 'Bank Transfer')
    cash = next(x['id'] for x in methods if x['name'] == 'Cash')
    customer = _command(b, site, 'customer.create', {'name':'Browser deposit customer'})['id']
    payments = [_command(b, site, 'payment.receive', {'customer':customer,'date':'2026-10-01',
        'amount':amount, 'payment_method':cash, 'operation_key':'browser-bank-payment-'+amount})['id']
        for amount in ('40.00','60.00')]
    bill = _command(b, site, 'bill.post', {'vendor':'Central Supply','date':'2026-10-01',
        'expenses':[{'account':env.expense['id'], 'amount':'60.00'}]})['id']
    other = _command(b, site, 'account.create', {'name':'Browser transfer bank','type':'bank'})['id']
    imported = _command(b, site, 'bank-feed.import', {'account':env.bank['id'], 'auto_match':False,
        'content':csv_file([('2026-10-07','BANKED PAYMENTS','100.00'),('2026-10-08','BILLS PAID','-60.00'),
                           ('2026-10-09','TRANSFER OUT','-40.00')])})
    paired = _command(b, site, 'bank-feed.import', {'account':other, 'account_confirmed':True,
        'content':csv_file([('2026-10-09','TRANSFER IN','40.00')])})
    lines = {x['payee']:x['id'] for x in imported['lines']}
    def sheet(payee, mode):
        b.navigate(base+'/bank-inbox/'+mode+'?'+urlencode({'account':env.bank['id'],'line':lines[payee]}))
        b.wait_for('!!document.querySelector("[data-bank-sort]")', timeout=60)
    sheet('BANKED PAYMENTS','deposit')
    b.evaluate(f'document.querySelector(\'[name=payment][value="{payments[0]}"]\').click()')
    _click(b,'[data-bank-check]','#bank-workspace',error=True)
    assert '60.00' in b.evaluate('document.querySelector("[data-bank-sheet-error]").innerText')
    b.evaluate(f'document.querySelector(\'[name=payment][value="{payments[1]}"]\').click()')
    _click(b,'[data-bank-check]','#bank-workspace')
    assert 'Difference remaining: 0.00' in b.evaluate('document.querySelector("[data-bank-preview]").innerText')
    _shot(b,width,'deposit-preview')
    _click(b,'[data-bank-save]','#bank-workspace')
    sheet('BILLS PAID','pay-bills')
    b.evaluate(f'document.querySelector(\'[name=bill][value="{bill}"]\').click()')
    _set(b,'[data-bank-sort] [name=method]',method)
    _click(b,'[data-bank-check]','#bank-workspace')
    assert 'Difference remaining: 0.00' in b.evaluate('document.querySelector("[data-bank-preview]").innerText')
    _shot(b,width,'pay-bills-preview')
    _click(b,'[data-bank-save]','#bank-workspace')
    sheet('TRANSFER OUT','transfer')
    _shot(b,width,'transfer-pair')
    _click(b,'[data-bank-save]','#bank-workspace')
    states = {x['payee']:x for x in _command(b,site,'bank-line.query',{'import_id':imported['import_id']})['lines']}
    assert states['BANKED PAYMENTS']['state'] == states['BILLS PAID']['state'] == 'added'
    other_line = _command(b,site,'bank-line.query',{'import_id':paired['import_id']})['lines'][0]
    assert states['TRANSFER OUT']['state'] == other_line['state'] == 'transfer'
    assert states['TRANSFER OUT']['entry_id'] == other_line['entry_id']
    _shot(b,width,'details-sorted')


def test_phone_accepts_and_declines_learned_rules(register_browser):
    env,b = register_browser, register_browser.browser
    site = env.site
    b.viewport(390,844)
    imported = _command(b,site,'bank-feed.import',{'account':env.bank['id'],'content':csv_file([
        ('2026-10-01','BROWSER COFFEE #1','-4.00'), ('2026-10-02','BROWSER COFFEE #2','-5.00'),
        ('2026-10-03','BROWSER FUEL #1','-6.00'), ('2026-10-04','BROWSER FUEL #2','-7.00')])})
    for line in imported['lines']:
        _command(b,site,'bank-line.add',{'line':line['id'],'payee':'Central Supply','account':env.expense['id']})
    suggested = _command(b,site,'bank-rule.query',{'account':env.bank['id'],'status':'suggested'})['rules']
    assert len(suggested) == 2
    b.navigate(f'{site.base_url}/c/{site.company_id}/bank-rules')
    b.wait_for('!!document.querySelector("[data-rule-accept]")',timeout=60)
    _shot(b,390,'learned-suggestions')
    _click(b,f'[data-bank-rule="{suggested[0]["id"]}"] [data-rule-accept]','#bank-rules')
    _click(b,f'[data-bank-rule="{suggested[1]["id"]}"] [data-rule-decline]','#bank-rules')
    assert _command(b,site,'bank-rule.show',{'rule':suggested[0]['id']})['rule']['status'] == 'active'
    assert _command(b,site,'bank-rule.show',{'rule':suggested[1]['id']})['rule']['status'] == 'retired'
    _click(b,'[data-rule-apply-check]','#bank-rules')
    assert b.evaluate('!!document.querySelector("[data-rule-apply-preview]")')
    _click(b,'[data-rule-apply]','#bank-rules')
    _shot(b,390,'learned-rules-decided')


def test_phone_previews_preserve_match_adjustment_and_bulk_choices(register_browser):
    """A preview or refusal must retain the choices the following Save will actually submit."""
    env,b = register_browser, register_browser.browser
    site = env.site
    b.viewport(390,844)
    base = f'{site.base_url}/c/{site.company_id}'
    entry = _command(b,site,'journal.post',{'date':'2026-09-01','memo':'Older chosen bank entry','lines':[
        {'account':env.bank['id'],'side':'credit','amount':'7.25'},
        {'account':env.expense['id'],'side':'debit','amount':'7.25'}]})['id']
    cash = next(x['id'] for x in _command(b,site,'payment-method.list',{})['items'] if x['name']=='Cash')
    customer = _command(b,site,'customer.create',{'name':'Browser retained deposit'})['id']
    payment = _command(b,site,'payment.receive',{'customer':customer,'date':'2026-10-01','amount':'100.00',
        'payment_method':cash,'operation_key':'browser-retained-deposit'})['id']
    imported = _command(b,site,'bank-feed.import',{'account':env.bank['id'],'auto_match':False,'content':csv_file([
        ('2026-10-20','OLDER ENTRY','-7.25'),('2026-10-20','DEPOSIT PLUS VENDOR','105.00'),
        ('2026-10-21','BULK UNKNOWN A','-8.00'),('2026-10-22','BULK UNKNOWN B','-9.00')])})
    lines = {x['payee']:x['id'] for x in imported['lines']}
    b.navigate(base+'/bank-inbox/match?'+urlencode({'account':env.bank['id'],'line':lines['OLDER ENTRY'],
        'search_from':'2026-09-01','search_to':'2026-09-02'}))
    b.wait_for('!!document.querySelector("[data-bank-sort]")',timeout=60)
    _set(b,'[data-bank-sort] [name=entry]',entry)
    _click(b,'[data-bank-check]','#bank-workspace')
    assert b.evaluate('document.querySelector("[data-bank-sort] [name=entry]").value') == entry
    assert b.evaluate('document.querySelector("[name=search_from]").value') == '2026-09-01'
    _click(b,'[data-bank-save]','#bank-workspace')
    actual = _command(b,site,'bank-line.query',{'import_id':imported['import_id']})['lines']
    assert next(x for x in actual if x['id']==lines['OLDER ENTRY'])['entry_id'] == entry

    b.navigate(base+'/bank-inbox/deposit?'+urlencode({'account':env.bank['id'],'line':lines['DEPOSIT PLUS VENDOR']}))
    b.wait_for('!!document.querySelector("[data-bank-sort]")',timeout=60)
    b.evaluate(f'document.querySelector(\'[name=payment][value="{payment}"]\').click()')
    for name,value in {'adjustment_account':env.expense['id'], 'adjustment_payee':'Central Supply',
                       'adjustment_payee_type':'vendor'}.items():
        _set(b,f'[data-bank-sort] [name={name}]',value)
    _click(b,'[data-bank-check]','#bank-workspace')
    assert b.evaluate('document.querySelector("[name=adjustment_payee_type]").value') == 'vendor'
    assert b.evaluate('document.querySelector("[name=adjustment_payee]").value') == 'Central Supply'
    assert b.evaluate(f'document.querySelector(\'[name=payment][value="{payment}"]\').checked')
    _click(b,'[data-bank-save]','#bank-workspace')
    actual = _command(b,site,'bank-line.query',{'import_id':imported['import_id']})['lines']
    assert next(x for x in actual if x['id']==lines['DEPOSIT PLUS VENDOR'])['state'] == 'added'

    selected = [lines['BULK UNKNOWN A'],lines['BULK UNKNOWN B']]
    for line in selected:
        b.evaluate(f'document.querySelector(\'[name=selected][value="{line}"]\').click()')
    b.evaluate('document.querySelector("#bank-bulk details").open = true')
    _click(b,'#bank-bulk [name=action][value=add]','#bank-workspace',error=True)
    assert b.evaluate('!!document.querySelector("[data-bank-error]")')
    assert set(b.evaluate('[...document.querySelectorAll("[name=selected]:checked")].map(e=>e.value)')) == set(selected)
    actual = _command(b,site,'bank-line.query',{'import_id':imported['import_id']})['lines']
    assert all(x['state']=='waiting' for x in actual if x['id'] in selected)


@pytest.mark.parametrize('width', (1440,390))
def test_enter_and_double_click_bulk_submission(register_browser, width):
    env, b = register_browser, register_browser.browser
    site = env.site
    b.viewport(width, 900)
    _command(b, site, 'bank-rule.create', {'name':'Keyboard and bulk', 'account':env.bank['id'],
        'conditions':[{'field':'payee','op':'contains','value':'KEYBOARD BULK'}],
        'payee':'Central Supply', 'category':env.expense['id']})
    imported = _command(b, site, 'bank-feed.import', {'account':env.bank['id'], 'auto_match':False,
        'content':csv_file([('2026-10-01','KEYBOARD BULK ONE','-11.00'),
                           ('2026-10-02','KEYBOARD BULK TWO','-12.00'),
                           ('2026-10-03','KEYBOARD BULK THREE','-13.00')])})
    first, *others = imported['lines']
    b.navigate(f'{site.base_url}/c/{site.company_id}/bank-inbox/add?' + urlencode({'line':first['id']}))
    b.wait_for('!!document.querySelector("[data-bank-sort]")', timeout=60)
    _set(b, '[data-bank-sort] [name=payee]', 'Central Supply')
    _set(b, '[data-bank-sort] [name=category]', env.expense['id'])
    b.evaluate('''window.__bankDocument = true; window.__bankOld = document.querySelector('#bank-workspace');
        window.__bankRequests = 0;
        document.body.addEventListener('htmx:beforeRequest', e => {
            if (e.detail.elt.matches('form[hx-post]')) window.__bankRequests++;
        });
        document.querySelector('[data-bank-sort] [name="ctx:reason"]').focus();''')
    for kind in ('keyDown', 'keyUp'):
        b.call('Input.dispatchKeyEvent', {'type':kind, 'key':'Enter', 'code':'Enter', 'windowsVirtualKeyCode':13,
                                          **({'text':'\r'} if kind == 'keyDown' else {})})
    b.wait_for('!window.__bankOld.isConnected', timeout=120)
    assert b.evaluate('window.__bankDocument === true && window.__bankRequests === 1')
    assert b.evaluate('!!document.querySelector("[data-bank-preview]")')
    _click(b, '[data-bank-save]', '#bank-workspace')
    for line in others:
        b.evaluate(f'document.querySelector(\'[name=selected][value="{line["id"]}"]\').click()')
    b.evaluate('''window.__bankRequests = 0; window.__bankOld = document.querySelector('#bank-workspace');
        const button = document.querySelector('#bank-bulk [name=action][value=add]');
        button.click(); button.click();''')
    b.wait_for('!window.__bankOld.isConnected', timeout=120)
    assert b.evaluate('window.__bankDocument === true && window.__bankRequests === 1')
    assert b.evaluate('!!document.querySelector("[data-bank-result]") && !document.querySelector("[data-bank-error]")')
    lines = _command(b, site, 'bank-line.query', {'import_id':imported['import_id']})['lines']
    assert len(lines) == 3 and all(line['state'] == 'added' for line in lines)
    assert len({line['entry_id'] for line in lines}) == 3
    assert b.evaluate('window.__bankRequests === 1')
