"""Served payment workspace and shared generated preview controls at both widths."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest
from bookflow.adapters.workbench import forms
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_service_sales_browser import _fill, _click, _contained
from tests.test_customer_payment_browser import field, click, wait
from tests.test_mcp_registry_work import company_snapshot


def workspace(b, url):
    # Follow the real legacy-route redirect; the generic navigate helper
    # requires the final URL to equal its input and cannot witness redirects.
    b.evaluate('void(window.previousPaymentDocument = document.documentElement)')
    b.call('Page.navigate', {'url': url})
    b.wait_for("!window.previousPaymentDocument?.isConnected && document.readyState === 'complete' && document.querySelector('#payment-workspace')?.dataset.loaded === 'true'")
    wait(b)
    assert b.evaluate('!!document.querySelector("#payment-form") && !document.querySelector("[data-generated-form]")')
    # Observe actual browser-generated requests and complete replies, without
    # replacing the encoder, response or command execution.
    b.evaluate('''(() => {window.paymentCalls=[];const original=window.fetch;
      window.fetch=async function(url,options) {
        const response=await original.call(this,url,options);
        if(String(url).includes('/commands/payment.receive'))
          window.paymentCalls.push({url:String(url),input:JSON.parse(options.body),
            status:response.status,result:await response.clone().json()});
        return response;
      };})()''')


def last_receive(b):
    calls=b.evaluate('window.paymentCalls')
    assert calls
    assert calls[-1]['status']==200, calls[-1]
    return calls[-1]


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.timeout(120)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_saved_selection_control_previews_real_receipt(register_browser, width):
    env,b = register_browser,register_browser.browser
    customer = _command(b, env.site, 'customer.create', {'name': 'Selection browser payer'})['id']
    method = _command(b, env.site, 'payment-method.create', {'name': 'Selection browser cash', 'kind': 'cash'})['id']
    selection = _command(b, env.site, 'payment.selection.create', dict(mode='new_receipt', customer=customer,
        date='2026-06-01', amount='12.00'))
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id
    workspace(b,base+'/payment/receive')
    assert b.evaluate('location.pathname').endswith('/receive-payments')
    workspace(b,base+'/receive-payments?selection='+selection['id'])
    assert b.evaluate('new URL(location.href).searchParams.get("selection")')==selection['id']
    assert b.evaluate('document.querySelector("#payment-amount").value')=='12.00'
    field(b,'method',method)
    baseline=company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))
    _contained(b,width)
    click(b,'preview')
    call=last_receive(b);preview=call['result']
    assert {key:call['input'][key] for key in ('customer','date','amount','payment_method')}=={
        'customer':customer,'date':'2026-06-01','amount':'12.00','payment_method':method}
    assert call['input']['applications']=={'mode':'selection','selection':selection['id'],'expected_version':selection['version']}
    assert 'items' not in call['input']['applications']
    assert call['input']['operation_key'].startswith('WB-')
    assert preview['dry_run'] and preview['current']['received_minor_units']==1200
    assert len(preview['facts_fingerprint'])==64
    assert '12.00' in b.evaluate('document.querySelector("#payment-preview-result").innerText')
    assert b.evaluate('document.querySelector("#payment-save").disabled') is False
    assert _command(b,env.site,'payment.query',{'customer':customer})['items']==[]
    assert _command(b,env.site,'payment.selection.show',{'selection':selection['id']})['state']=='open'
    assert company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))==baseline
    _contained(b,width)


@pytest.mark.parametrize('width', [1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
def test_generated_payment_json_object_error_preview_and_saved_false(register_browser,width,monkeypatch):
    from tests.test_mcp_workbench_control_browser import form, stage
    env,b=register_browser,register_browser.browser
    customer=_command(b,env.site,'customer.create',{'name':'JSON control payer'})['id']
    method=_command(b,env.site,'payment-method.create',{'name':'JSON control cash','kind':'cash'})['id']
    definition=_command(b,env.site,'custom-field.create',{'name':'Owned receipt bool','kind':'bool','scopes':['payment']})['id']
    data={'customer':customer,'date':'2026-06-01','amount':'12.00','payment_method':method,
          'operation_key':'json-control-preview','applications':{'mode':'inline'},
          'custom_fields':{definition:False},'expected_custom_field_kinds':{definition:'bool'}}
    preview=_command(b,env.site,'payment.receive?dry_run=true',data)
    assert preview['dry_run'] and preview['current']['received_minor_units']==1200
    request={'request':{'command':'payment receive','input':data},
             'facts_fingerprint':preview['facts_fingerprint'],'kind':'applications','limit':50}
    expected=_command(b,env.site,'payment.preview.items',request)
    captured=[];actual=forms.translate
    def observe(cmd,*args,**kwargs):
        out=actual(cmd,*args,**kwargs)
        if cmd.name=='payment preview items':captured.append(deepcopy(out[0]))
        return out
    monkeypatch.setattr(forms,'translate',observe)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id
    # This read endpoint still serves the shared generated JSON controls. It
    # previews a receive request; it cannot save a payment.
    form(b,base+'/payment%20preview/items')
    for name,value in {'request.command':'payment receive','request.input.customer':customer,
        'request.input.date':'2026-06-01','request.input.amount':'12.00','request.input.payment_method':method,
        'request.input.operation_key':'json-control-preview','request.input.applications.mode':'inline',
        'facts_fingerprint':preview['facts_fingerprint'],'kind':'applications','limit':'50'}.items():
        _fill(b,'f:'+name,value)
    control='f:request.input.custom_fields'
    assert b.evaluate(f'document.getElementsByName({json.dumps(control)})[0].tagName')=='TEXTAREA'
    _fill(b,control,'{invalid JSON')
    baseline=company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))
    stage(b,action='submit')
    assert 'E_VALIDATION' in b.evaluate('document.body.innerText')
    assert b.evaluate(f'document.getElementsByName({json.dumps(control)})[0].value')=='{invalid JSON'
    assert company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))==baseline
    _fill(b,control,json.dumps({definition:False}))
    _fill(b,'f:request.input.expected_custom_field_kinds',json.dumps({definition:'bool'}))
    _contained(b,width)
    b.evaluate('void(window.previousGeneratedForm = document.querySelector("[data-generated-form]"))')
    _click(b,'submit')
    b.wait_for('!window.previousGeneratedForm?.isConnected && (!!document.querySelector(".save-feedback summary") || !!document.querySelector(".error"))')
    assert captured[-1]==request, (captured[-1],request)
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    raw=captured[-1]['request']['input']
    assert raw['custom_fields']=={definition:False}
    assert raw['expected_custom_field_kinds']=={definition:'bool'}
    assert raw['operation_key']=='json-control-preview'
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    assert json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))==expected
    assert company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))==baseline
    assert _command(b,env.site,'payment.query',{'customer':customer})['items']==[]

    selection=_command(b,env.site,'payment.selection.create',dict(mode='new_receipt',customer=customer,
        date='2026-06-01',amount='12.00'))
    workspace(b,base+'/receive-payments?selection='+selection['id'])
    field(b,'method',method)
    b.evaluate(f'''(() => {{const input=document.querySelector('[data-definition="{definition}"]');
        input.value='false';input.dispatchEvent(new Event('change',{{bubbles:true}}));}})()''')
    wait(b)
    baseline=company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))
    click(b,'preview')
    proposal=last_receive(b);preview=proposal['result']
    assert preview['dry_run'] and preview['current']['received_minor_units']==1200
    assert proposal['input']['custom_fields']=={definition:False}
    assert proposal['input']['expected_custom_field_kinds']=={definition:'bool'}
    key=proposal['input']['operation_key'];assert key.startswith('WB-')
    assert company_snapshot(Path(os.environ['BOOKFLOW_DATA_ROOT']))==baseline
    assert _command(b,env.site,'payment.query',{'customer':customer})['items']==[]
    _contained(b,width)
    click(b,'save')
    submitted=last_receive(b)
    assert submitted['input']=={**proposal['input'],'expected_facts_fingerprint':preview['facts_fingerprint']}
    assert submitted['result']['dry_run'] is False
    page=_command(b,env.site,'payment.query',{'customer':customer})
    assert len(page['items'])==1
    saved=_command(b,env.site,'payment.show',{'payment':page['items'][0]['id']})
    assert saved['current']['received_minor_units']==1200
    fact=saved['revision']['custom_fields_snapshot'][definition]
    assert fact['value'] is False and fact['canonical_text']=='false' and fact['kind']=='bool'
    operation=_command(b,env.site,'payment.operation.show',{'operation_key':key})
    assert operation['request']['input']['custom_fields']=={definition:False}
    assert operation['request']['input']['expected_facts_fingerprint']==preview['facts_fingerprint']
    assert operation['operation_key']==key
    assert _command(b,env.site,'payment.selection.show',{'selection':selection['id']})['state']=='consumed'
    _contained(b,width)
