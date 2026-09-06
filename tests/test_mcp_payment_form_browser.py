"""Saved selection is an ordinary generated control, including phone preview."""
import json
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.timeout(120)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_saved_selection_control_previews_real_receipt(register_browser, width):
    env, browser = register_browser, register_browser.browser
    customer = _command(browser, env.site, 'customer.create', {'name': 'Selection browser payer'})['id']
    method = _command(browser, env.site, 'payment-method.create', {'name': 'Selection browser cash', 'kind': 'cash'})['id']
    selection = _command(browser, env.site, 'payment.selection.create', dict(mode='new_receipt', customer=customer,
        date='2026-06-01', amount='12.00'))
    browser.viewport(width, 900)
    browser.navigate(env.site.base_url + '/c/' + env.site.company_id + '/payment/receive')
    browser.wait_for('!!document.querySelector("[data-generated-form]") && document.readyState === "complete"')
    for name, value in {'customer': customer, 'date': '2026-06-01', 'amount': '12.00', 'payment_method': method,
        'operation_key': 'browser-selection', 'applications.mode': 'selection',
        'applications.selection': selection['id'], 'applications.expected_version': str(selection['version'])}.items():
        _fill(browser, 'f:' + name, value)
    assert browser.evaluate('document.getElementsByName("f:applications.selection")[0].disabled') is False
    assert browser.evaluate('document.getElementsByName("collection:applications.items")[0].disabled') is True
    _contained(browser, width)
    browser.evaluate('void(window.beforeForm = document.querySelector("[data-generated-form]"))')
    _click(browser, 'preview')
    browser.wait_for('!window.beforeForm.isConnected')
    assert not browser.evaluate('document.querySelector(".error")?.textContent'), browser.evaluate('document.body.innerText')
    preview = json.loads(browser.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['current']['received_minor_units'] == 1200
    assert _command(browser, env.site, 'payment.query', {'customer': customer})['items'] == []
    assert _command(browser, env.site, 'payment.selection.show', {'selection': selection['id']})['state'] == 'open'


@pytest.mark.parametrize('width', [1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_generated_payment_json_object_error_preview_and_saved_false(register_browser,width):
    from urllib.parse import parse_qs
    from tests.test_mcp_workbench_control_browser import form, stage, observe_body
    env,b = register_browser,register_browser.browser
    customer = _command(b,env.site,'customer.create',{'name':'JSON control payer'})['id']
    method = _command(b,env.site,'payment-method.create',{'name':'JSON control cash','kind':'cash'})['id']
    definition = _command(b,env.site,'custom-field.create',{'name':'Owned receipt bool','kind':'bool','scopes':['payment']})['id']
    b.viewport(width,900)
    form(b,env.site.base_url+'/c/'+env.site.company_id+'/payment/receive')
    for name,value in {'customer':customer,'date':'2026-06-01','amount':'12.00','payment_method':method,
                       'operation_key':'json-control-receipt','applications.mode':'inline'}.items():
        _fill(b,'f:'+name,value)
    assert b.evaluate('document.getElementsByName("f:custom_fields")[0].tagName')=='TEXTAREA'
    _fill(b,'f:custom_fields','{invalid JSON')
    stage(b)
    assert 'E_VALIDATION' in b.evaluate('document.body.innerText')
    assert b.evaluate('document.getElementsByName("f:custom_fields")[0].value')=='{invalid JSON'
    assert _command(b,env.site,'payment.query',{'customer':customer})['items']==[]
    raw = json.dumps({definition:False})
    _fill(b,'f:custom_fields',raw)
    _fill(b,'f:expected_custom_field_kinds',json.dumps({definition:'bool'}))
    observe_body(b)
    _contained(b,width)
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['current']['received_minor_units']==1200
    encoded=parse_qs(b.evaluate('window.lastGeneratedBody'))
    assert json.loads(encoded['f:custom_fields'][0])=={definition:False}
    assert json.loads(encoded['f:expected_custom_field_kinds'][0])=={definition:'bool'}
    assert encoded['f:operation_key']==['json-control-receipt']
    assert _command(b,env.site,'payment.query',{'customer':customer})['items']==[]
    _fill(b,'f:expected_facts_fingerprint',preview['facts_fingerprint'])
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/receive")')
    page=_command(b,env.site,'payment.query',{'customer':customer})
    assert len(page['items'])==1
    saved=_command(b,env.site,'payment.show',{'payment':page['items'][0]['id']})
    fact=saved['revision']['custom_fields_snapshot'][definition]
    assert fact['value'] is False and fact['canonical_text']=='false' and fact['kind']=='bool'
    operation=_command(b,env.site,'payment.operation.show',{'operation_key':'json-control-receipt'})
    assert operation['request']['input']['custom_fields']=={definition:False}
    assert operation['request']['input']['expected_facts_fingerprint']==preview['facts_fingerprint']
