"""Real Chrome/CDP register witnesses; all services/data remain test-local."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.test_row5_browser_acceptance import CHROME, PASSWORD, _Cdp, browser_site  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")


def _key(browser, key, *, shift=False):
    values = {"key": key, "code": key, "modifiers": 8 if shift else 0}
    if key in {"Enter", "Tab"}:
        values["windowsVirtualKeyCode"] = 13 if key == "Enter" else 9
    down = {"type": "keyDown", **values}
    if key == "Enter":
        down["text"] = "\r"
    browser.call("Input.dispatchKeyEvent", down)
    browser.call("Input.dispatchKeyEvent", {"type": "keyUp", **values})


def _tab_to(browser, selector, *, shift=False):
    for _ in range(100):
        if browser.evaluate(f"document.activeElement.matches({json.dumps(selector)})"):
            return
        _key(browser, "Tab", shift=shift)
    raise AssertionError(f"Tab could not reach {selector}")


def _type(browser, text):
    browser.call("Input.insertText", {"text": text})


def _command(browser, site, name, payload, **headers):
    result = browser.evaluate(f"""fetch('/companies/{site.company_id}/commands/{name}', {{
      method: 'POST', credentials: 'same-origin', headers: {{'Content-Type':'application/json',
      'X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench', ...{json.dumps(headers)}}},
      body: JSON.stringify({json.dumps(payload)})}}).then(async r => ({{status:r.status, body:await r.json()}}))""", await_promise=True)
    assert result["status"] == 200, result
    return result["body"]


@pytest.fixture
def register_browser(browser_site, tmp_path):
    browser = _Cdp(tmp_path / "register-chrome")
    try:
        browser.navigate(browser_site.base_url + "/login")
        browser.evaluate(f"""(() => {{
          document.querySelector('[name="username"]').value = {json.dumps(browser_site.login)};
          document.querySelector('[name="password"]').value = {json.dumps(PASSWORD)};
          document.querySelector('form[hx-post="/login"]').requestSubmit();
        }})()""")
        browser.wait_for("!!document.querySelector('.nav-group')")
        bank = _command(browser, browser_site, "account.create", {"name": "CDP bank", "type": "bank"})
        expense = _command(browser, browser_site, "account.create", {"name": "CDP supplies", "type": "expense"})
        url = f"{browser_site.base_url}/c/{browser_site.company_id}/account/{bank['id']}/register"
        browser.navigate(url)
        browser.wait_for("document.activeElement.id === 'register-date' && !document.querySelector('#register-current').textContent.includes('loading')")
        yield SimpleNamespace(browser=browser, site=browser_site, bank=bank, expense=expense, url=url)
    finally:
        browser.close()


def _choose(browser, selector, query):
    _tab_to(browser, selector)
    _type(browser, query)
    browser.wait_for(f"!document.querySelector({json.dumps(selector)}).closest('.register-picker').querySelector('.register-options').hidden")
    _key(browser, "ArrowDown")
    _key(browser, "Enter")
    assert browser.evaluate("document.querySelector('#register-receipt').hidden")


def _simple_draft(env, amount="100.00"):
    b = env.browser
    _tab_to(b, '[name="amount"]')
    _type(b, amount)
    _choose(b, '#register-category input[role="combobox"]', "CDP supplies")


def _record_keyboard(browser):
    _tab_to(browser, '#register-record')
    _key(browser, 'Enter')
    browser.wait_for("!document.querySelector('#register-receipt').hidden || !!document.querySelector('#register-error').textContent")
    assert browser.evaluate("document.querySelector('#register-error').textContent") == ""
    browser.wait_for("document.activeElement.id === 'register-date'")


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_keyboard_payment_deposit_receipt_and_contained_history(register_browser, width, height, tmp_path):
    env, b = register_browser, register_browser.browser
    b.viewport(width, height)
    _key(b, "t")
    today = b.evaluate("JSON.parse(document.querySelector('#register-config').textContent).today")
    assert b.evaluate("document.querySelector('#register-date').value") == today
    _key(b, "+")
    _key(b, "-")
    assert b.evaluate("document.querySelector('#register-date').value") == today
    _simple_draft(env)
    # Closed and empty reference Enter must never submit; Shift+Tab reverses focus.
    _key(b, "Enter")
    _key(b, "Tab", shift=True)
    assert b.evaluate("document.querySelector('#register-receipt').hidden")
    _record_keyboard(b)
    b.wait_for("document.querySelector('#register-current').textContent.includes('-100.00')")
    assert b.evaluate("document.querySelector('#register-receipt').textContent.includes('100.00')")
    assert b.evaluate("location.pathname.endsWith('/register')")
    # Deposit is a native select, driven with real keyboard events.
    _tab_to(b, '[name="direction"]')
    _key(b, "ArrowDown")
    _tab_to(b, '[name="amount"]')
    _type(b, "40.25")
    # Receipt from the previous save remains visible during the new draft.
    _tab_to(b, '#register-category input[role="combobox"]')
    _type(b, "CDP supplies")
    b.wait_for("!document.querySelector('#register-category .register-options').hidden")
    _key(b, 'ArrowDown'); _key(b, 'Enter')
    _record_keyboard(b)
    b.wait_for("document.querySelector('#register-current').textContent.includes('-59.75')")
    balance = _command(b, env.site, "account.show", {"account": env.bank["id"]})["balance"]
    assert balance["amount"] == "-59.75"
    assert b.evaluate("document.documentElement.scrollWidth") <= width + 1
    if width == 390:
        assert b.evaluate("document.querySelector('.table-wrap').scrollWidth > document.querySelector('.table-wrap').clientWidth")
    screenshot = b.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
    (tmp_path / f'register-{width}.png').write_bytes(base64.b64decode(screenshot['data']))
    # Reduced visual viewport witness, not a physical phone-keyboard claim.
    b.viewport(width, 360)
    _tab_to(b, '#register-record')
    b.wait_for("(() => {const r=document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.bottom <= visualViewport.height && r.right <= innerWidth;})()")
    b.call('Emulation.setPageScaleFactor', {'pageScaleFactor': 1.5})
    assert b.evaluate("document.documentElement.scrollWidth") <= width + 1


def test_keyboard_mixed_splits_recalculate_and_stable_edit(register_browser):
    env, b = register_browser, register_browser.browser
    _tab_to(b, '#register-splits-open'); _key(b, 'Enter')
    _choose(b, '#register-allocations .register-picker input', 'CDP supplies')
    _tab_to(b, '#register-allocations input[inputmode="decimal"]'); _type(b, '120.00')
    _tab_to(b, '#register-split-add'); _key(b, 'Enter')
    _type(b, 'CDP supplies')
    b.wait_for("!document.querySelector('#register-allocations fieldset:last-child .register-options').hidden")
    _key(b, 'ArrowDown'); _key(b, 'Enter')
    _tab_to(b, '#register-allocations fieldset:last-child input[inputmode="decimal"]'); _type(b, '20.00')
    _key(b, 'Tab'); _key(b, 'ArrowDown'); _key(b, 'ArrowDown')  # Deposit / increase offset
    _tab_to(b, '#register-recalculate'); _key(b, 'Enter')
    b.wait_for("document.querySelector('[name=amount]').value === '100.00'")
    _tab_to(b, '#register-split-close'); _key(b, 'Enter')
    assert b.evaluate("document.activeElement.id") == 'register-splits-open'
    _record_keyboard(b)
    b.wait_for("document.querySelector('#register-current').textContent.includes('-100.00')")
    receipt_url = b.evaluate("document.querySelector('#register-receipt a').href")
    journal = receipt_url.rsplit('/', 1)[1]
    before = _command(b, env.site, 'journal.show', {'journal': journal})
    assert [x['amount']['amount'] for x in before['revision']['lines']] == ['100.00', '120.00', '20.00']
    assert [x['side'] for x in before['revision']['lines']] == ['credit', 'debit', 'credit']
    b.navigate(env.url + '?edit=' + journal)
    b.wait_for("!!document.querySelector('#register-allocations fieldset')")
    _record_keyboard(b)
    after = _command(b, env.site, 'journal.show', {'journal': journal})
    assert after['version'] == before['version']
    assert after['revision'] == before['revision']


@pytest.mark.parametrize('failure', ['transport', 'E_PARTIAL_WRITE'])
def test_committed_response_loss_reload_retry_preserves_exact_intent(register_browser, failure):
    env, b = register_browser, register_browser.browser
    _simple_draft(env, '17.43')
    b.evaluate(f"""(() => {{const original = window.fetch; let once = true;
      window.fetch = async (url, options) => {{
        if (String(url).endsWith('/commands/register.post') && once) {{once=false;
          const result=await original(url,options); await result.clone().text();
          if ({json.dumps(failure)} === 'transport') throw new TypeError('simulated lost response after commit');
          return new Response(JSON.stringify({{code:'E_PARTIAL_WRITE',message:'Committed with interrupted response'}}),{{status:500,headers:{{'Content-Type':'application/json'}}}});
        }} return original(url,options);
      }};
    }})()""")
    _tab_to(b, '#register-record'); _key(b, 'Enter')
    b.wait_for("!document.querySelector('#register-pending').hidden && !document.querySelector('#register-retry').disabled")
    saved = b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')")
    assert json.loads(saved)['payload']['amount'] == '17.43'
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    b.evaluate("document.querySelector('#register-restore').click()")
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") == saved
    b.navigate(env.url)
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") == saved
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    _tab_to(b, '#register-retry'); _key(b, 'Enter')
    b.wait_for("!document.querySelector('#register-receipt').hidden")
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    b.wait_for("document.querySelector('#register-current').textContent.includes('-17.43')")
    result = _command(b, env.site, 'register.query', {'account': env.bank['id'], 'date_from': '0001-01-01', 'date_to': '9999-12-31'})
    assert len([r for r in result['rows'] if r['kind'] == 'posting']) == 1


def test_stale_edit_keeps_attempted_version_and_draft(register_browser):
    env, b = register_browser, register_browser.browser
    today = b.evaluate("document.querySelector('#register-date').value")
    first = _command(b, env.site, 'register.post', {'account': env.bank['id'], 'date': today,
        'direction': 'decrease', 'amount': '9.00', 'category': env.expense['id']})
    b.navigate(env.url + '?edit=' + first['id'])
    b.wait_for("document.querySelector('#register-entry-title').textContent.includes('version 1')")
    lines = first['revision']['lines']
    _command(b, env.site, 'register.update', {'journal': first['id'], 'expected_version': first['version'],
        'selected_line_id': lines[0]['line_id'], 'category_line_id': lines[1]['line_id'],
        'account': env.bank['id'], 'date': today, 'direction': 'decrease', 'amount': '10.00',
        'category': env.expense['id'], 'memo': 'Concurrent edit'}, **{'X-Bookflow-Reason': 'another writer'})
    _tab_to(b, '[name=memo]'); _type(b, 'My attempted memo')
    _tab_to(b, '#register-record'); _key(b, 'Enter')
    b.wait_for("document.querySelector('#register-error').textContent.includes('E_VERSION_CONFLICT')")
    assert b.evaluate("document.querySelector('[name=memo]').value") == 'My attempted memo'
    assert b.evaluate("document.querySelector('#register-entry-title').textContent.includes('version 1')")
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    current = _command(b, env.site, 'journal.show', {'journal': first['id']})
    assert current['memo'] == 'Concurrent edit'


@pytest.mark.parametrize('date', ['2000-01-02', '2099-12-29'])
def test_outside_period_receipt_survives_read_failure(register_browser, date):
    env, b = register_browser, register_browser.browser
    _type(b, date)
    _simple_draft(env, '6.19')
    b.evaluate("""(() => {const original=window.fetch;
      window.fetch=(url,options) => String(url).endsWith('/commands/register.query')
        ? Promise.reject(new TypeError('read unavailable')) : original(url,options);
      window.restoreRegisterFetch=()=>{window.fetch=original;};
    })()""")
    _record_keyboard(b)
    b.wait_for("document.querySelector('#register-query-error').textContent.includes('refresh failed')")
    assert b.evaluate("document.querySelector('#register-receipt').textContent.includes('6.19')")
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    b.evaluate("window.restoreRegisterFetch(); document.querySelector('#register-period').requestSubmit()")
    b.wait_for("document.querySelector('#register-current').textContent.includes('-6.19')")
    assert b.evaluate("document.querySelectorAll('#register-history tr[data-kind=posting]').length") == 0
    assert b.evaluate("document.querySelector('#register-receipt').textContent.includes('6.19')")


def test_storage_failure_never_sends_and_expired_intent_never_retries(register_browser):
    env, b = register_browser, register_browser.browser
    _simple_draft(env, '3.21')
    b.evaluate("""(() => {window.sentRegister=0; const fetch=window.fetch;
      window.fetch=(url,options)=>{if(String(url).endsWith('/commands/register.post')) window.sentRegister++; return fetch(url,options);};
      const set=Storage.prototype.setItem; Storage.prototype.setItem=function(){throw new DOMException('quota','QuotaExceededError');};
      window.restoreRegisterStorage=()=>{Storage.prototype.setItem=set;};
    })()""")
    _tab_to(b, '#register-record'); _key(b, 'Enter')
    b.wait_for("document.querySelector('#register-error').textContent.includes('Not sent')")
    assert b.evaluate('window.sentRegister') == 0
    b.evaluate("""(() => { window.restoreRegisterStorage(); document.querySelector('#register-restore').click();
      const c=JSON.parse(document.querySelector('#register-config').textContent);
      const p={account:c.account,date:c.today,direction:'decrease',amount:'3.21',category:'test-only'};
      sessionStorage.setItem('bookflow-register-pending-v1',JSON.stringify({actor:c.actor,company:c.company,account:c.account,
        payload:p,wire:JSON.stringify(p),labels:{category:'Prior choice'},context:{},key:'expired-original-key',
        command:'register.post',time:Date.now()-31*86400000}));
    })()""")
    b.navigate(env.url)
    b.wait_for("document.querySelector('#register-pending-message').textContent.includes('30-day')")
    assert b.evaluate("document.querySelector('#register-retry').disabled")
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    assert 'expired-original-key' in b.evaluate("document.querySelector('#register-review').href")
    assert b.evaluate("!document.querySelector('#register-reconciliation').hidden")


def test_stale_next_page_restarts_without_changing_draft(register_browser):
    env, b = register_browser, register_browser.browser
    today = b.evaluate("document.querySelector('#register-date').value")
    entry = {'account': env.bank['id'], 'date': today, 'direction': 'decrease', 'amount': '1.00', 'category': env.expense['id']}
    for _ in range(3):
        _command(b, env.site, 'register.post', entry)
    # Bound pages to two rows for the continuation witness; the actual server
    # signs and validates these pages, including its own stale response.
    b.evaluate("""(() => {const original=window.fetch; window.registerStale=0;
      window.fetch=async (url,options)=>{if(String(url).endsWith('/commands/register.query')) {
        const input=JSON.parse(options.body); input.limit=2; options={...options,body:JSON.stringify(input)};
        const response=await original(url,options); const data=await response.clone().json();
        if(data.code==='E_QUERY_STALE') window.registerStale++; return response;
      } return original(url,options);};
      document.querySelector('#register-period').requestSubmit();
    })()""")
    b.wait_for("!document.querySelector('#register-next').hidden && !document.querySelector('#register-next').disabled")
    _tab_to(b, '[name=amount]'); _type(b, '88.19')
    _command(b, env.site, 'register.post', entry)
    b.evaluate("document.querySelector('#register-next').click()")
    b.wait_for("window.registerStale === 1 && document.querySelector('#register-current').textContent.includes('-4.00')")
    assert b.evaluate("document.querySelector('[name=amount]').value") == '88.19'
    assert b.evaluate("document.querySelector('#register-history tbody').rows.length") == 2


def test_real_save_without_secure_context_random_uuid(register_browser):
    env, b = register_browser, register_browser.browser
    b.evaluate("""(() => {
      Object.defineProperty(crypto, 'randomUUID', {value: undefined, configurable: true});
      const original = window.fetch;
      window.fetch = (url, options) => {
        if (String(url).endsWith('/commands/register.post')) {
          window.fallbackSaveKey = options.headers['Idempotency-Key'];
          window.fallbackPersistedKey = JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1')).key;
        }
        return original(url, options);
      };
    })()""")
    assert b.evaluate("typeof crypto.randomUUID") == 'undefined'
    _simple_draft(env, '2.31')
    _record_keyboard(b)
    b.wait_for("document.querySelector('#register-current').textContent.includes('-2.31')")
    key = b.evaluate('window.fallbackSaveKey')
    assert UUID(key).version == 4
    assert b.evaluate('window.fallbackPersistedKey') == key
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['amount'] == '-2.31'


def test_date_separators_partial_edit_and_input_shortcuts(register_browser):
    b = register_browser.browser
    _type(b, '2026')
    _type(b, '-')
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-'
    _type(b, '02-28')
    # The date is complete; a keypad/input event can increment across a month.
    _type(b, '+')
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-03-01'
    _type(b, '−')
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-02-28'
    b.evaluate("document.querySelector('#register-date').setSelectionRange(4, 5)")
    _type(b, '-')
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-02-28'
    _key(b, 't')
    assert b.evaluate("document.querySelector('#register-date').value === JSON.parse(document.querySelector('#register-config').textContent).today")
    assert b.evaluate("document.querySelector('#register-receipt').hidden")


@pytest.mark.parametrize('retry_error,legacy', [('E_PERMISSION', False), ('E_VALIDATION', True)])
def test_lost_committed_response_then_precommit_retry_error_keeps_intent(register_browser, retry_error, legacy):
    env, b = register_browser, register_browser.browser
    _simple_draft(env, '12.73')
    b.evaluate("""(() => { const original = window.fetch; let once = true;
      window.fetch = async (url, options) => {
        if (String(url).endsWith('/commands/register.post') && once) {
          once = false;
          window.firstAttemptState = JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'));
          const response = await original(url, options); await response.clone().text();
          throw new TypeError('response lost after commit');
        }
        return original(url, options);
      };
    })()""")
    _tab_to(b, '#register-record'); _key(b, 'Enter')
    b.wait_for("document.querySelector('#register-error').textContent.includes('TRANSPORT') && !document.querySelector('#register-retry').disabled")
    first = b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'))")
    assert first['possibly_sent'] is True
    assert b.evaluate('window.firstAttemptState.possibly_sent') is True
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['amount'] == '-12.73'
    if legacy:
        b.evaluate("""(() => {const intent=JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'));
          delete intent.possibly_sent; sessionStorage.setItem('bookflow-register-pending-v1',JSON.stringify(intent));
        })()""")
    b.navigate(env.url)
    b.evaluate(f"""(() => {{const original=window.fetch; window.retryKeys=[];
      window.fetch=(url,options)=>{{if(String(url).endsWith('/commands/register.post')) {{
        window.retryKeys.push(options.headers['Idempotency-Key']);
        return Promise.resolve(new Response(JSON.stringify({{code:{json.dumps(retry_error)},message:'Rejected before idempotency lookup'}}),
          {{status:403,headers:{{'Content-Type':'application/json'}}}}));
      }} return original(url,options);}};
    }})()""")
    _tab_to(b, '#register-retry'); _key(b, 'Enter')
    b.wait_for(f"document.querySelector('#register-error').textContent.includes({json.dumps(retry_error)}) && !document.querySelector('#register-retry').disabled")
    retained = b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'))")
    for key in ['actor', 'company', 'account', 'command', 'payload', 'wire', 'context', 'key', 'time']:
        assert retained[key] == first[key]
    assert retained['possibly_sent'] is True
    assert b.evaluate('window.retryKeys') == [first['key']]
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    b.evaluate("document.querySelector('#register-restore').click(); document.querySelector('#register-record').click()")
    assert b.evaluate('window.retryKeys') == [first['key']]
    assert b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1')).key") == first['key']


def test_attempt_state_storage_failure_never_starts_fetch(register_browser):
    env, b = register_browser, register_browser.browser
    _simple_draft(env, '4.17')
    b.evaluate("""(() => {const originalFetch=window.fetch, originalSet=Storage.prototype.setItem;
      window.registerWriteFetches=0;
      window.fetch=(url,options)=>{if(String(url).endsWith('/commands/register.post')) window.registerWriteFetches++; return originalFetch(url,options);};
      Storage.prototype.setItem=function(key,value) {
        if(key==='bookflow-register-pending-v1' && JSON.parse(value).possibly_sent === true) throw new DOMException('quota','QuotaExceededError');
        return originalSet.call(this,key,value);
      };
    })()""")
    _tab_to(b, '#register-record'); _key(b, 'Enter')
    b.wait_for("document.querySelector('#register-error').textContent.includes('could not persist the attempt state')")
    assert b.evaluate('window.registerWriteFetches') == 0
    assert b.evaluate("document.querySelector('#register-fields').disabled")
    assert b.evaluate("document.querySelector('#register-retry').disabled")
    assert b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1')).possibly_sent") is False
    assert _command(b, env.site, 'account.show', {'account': env.bank['id']})['balance']['amount'] == '0.00'
