"""Real generated controls preserve boolean/null/omission and password boundaries."""
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs
import pytest
from argon2 import PasswordHasher
from tests.test_row5_browser_acceptance import CHROME, PASSWORD, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _click, _contained


def form(browser, url):
    browser.navigate(url)
    browser.wait_for('document.readyState === "complete" && !!document.querySelector("[data-generated-form]")')


def stage(browser, action='preview'):
    browser.evaluate('void(window.previousGeneratedForm = document.querySelector("[data-generated-form]"))')
    _click(browser, action)
    browser.wait_for('!window.previousGeneratedForm.isConnected')


def observe_body(browser):
    browser.evaluate('''(() => {const original = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(body) {
            window.lastGeneratedBody = typeof body === 'string' ? body : new URLSearchParams(body).toString();
            return original.call(this, body);
        };})()''')


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_generated_boolean_false_omission_and_null_encoding(register_browser,width):
    env,b = register_browser,register_browser.browser
    b.viewport(width,900)
    _command(b,env.site,'company.update',{'use_classes':True,'fax':'Original nullable fax'})
    url = env.site.base_url+'/c/'+env.site.company_id+'/company/self/update'
    form(b,url)
    observe_body(b)
    _fill(b,'f:use_classes','false')
    _fill(b,'ctx:reason','Browser bool witness')
    _contained(b,width)
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    preview = json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['changed_fields'] == ['use_classes']
    assert parse_qs(b.evaluate('window.lastGeneratedBody'))['f:use_classes'] == ['false']
    assert _command(b,env.site,'company.show',{})['info']['use_classes'] is True
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/company/self")')
    saved = _command(b,env.site,'company.show',{})
    assert saved['info']['use_classes'] is False
    events = _command(b,env.site,'audit.list',{'command':'company update','limit':200})['items']
    event = next(e for e in events if e['reason']=='Browser bool witness')
    assert event['interface']=='http' and event['client_name']=='bookflow-workbench'
    # Nullable input does not authorize clearing a required business preference.
    form(b,url)
    b.evaluate('document.getElementsByName("clear:use_classes")[0].checked=true')
    stage(b)
    assert 'E_VALIDATION' in b.evaluate('document.body.innerText')
    assert b.evaluate('document.getElementsByName("clear:use_classes")[0].checked')
    unchanged = _command(b,env.site,'company.show',{})
    assert unchanged['info_version']==saved['info_version'] and unchanged['info']['use_classes'] is False
    # Unset is omitted by the shared decoder, while another field really changes.
    form(b,url)
    observe_body(b)
    _fill(b,'f:use_classes','unset')
    _fill(b,'f:fax','Only the fax changes')
    stage(b)
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['changed_fields']==['fax']
    assert parse_qs(b.evaluate('window.lastGeneratedBody'))['f:use_classes']==['unset']
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/company/self")')
    saved = _command(b,env.site,'company.show',{})
    assert saved['info']['use_classes'] is False and saved['info']['fax']=='Only the fax changes'
    form(b,url)
    observe_body(b)
    b.evaluate('document.getElementsByName("clear:fax")[0].checked=true')
    stage(b)
    assert parse_qs(b.evaluate('window.lastGeneratedBody'))['clear:fax']==['1']
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['changed_fields']==['fax']
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/company/self")')
    assert _command(b,env.site,'company.show',{})['info']['fax'] is None


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(120)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_generated_password_error_preview_and_save_never_echo_secret(register_browser,width,tmp_path):
    env,b = register_browser,register_browser.browser
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    assert root.is_relative_to(tmp_path)
    def stored():
        with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro',uri=True) as db:
            return db.execute('SELECT password_hash FROM users WHERE username=?',(env.site.login,)).fetchone()[0]
    before = stored()
    secret = 'Owned browser fixture 739!'
    b.viewport(width,900)
    form(b,env.site.base_url+'/hub/user/set-password')
    assert b.evaluate('document.getElementsByName("f:password")[0].type')=='password'
    _fill(b,'f:username','missing-owned-password-target')
    _fill(b,'f:password',secret)
    _contained(b,width)
    stage(b)
    assert 'E_USER_NOT_FOUND' in b.evaluate('document.body.innerText') and stored()==before
    assert secret not in b.evaluate('document.documentElement.outerHTML')
    assert b.evaluate('document.getElementsByName("f:password")[0].value')==''
    _fill(b,'f:username',env.site.login)
    _fill(b,'f:password',secret)
    stage(b)
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['changed'] and stored()==before
    assert secret not in b.evaluate('document.documentElement.outerHTML')
    assert b.evaluate('document.getElementsByName("f:password")[0].value')==''
    _fill(b,'f:password',secret)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/hub/user")')
    after=stored()
    assert after!=before and PasswordHasher().verify(after,secret)
    assert PasswordHasher().verify(before,PASSWORD)
    assert secret not in b.evaluate('document.documentElement.outerHTML')
