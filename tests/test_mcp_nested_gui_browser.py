"""Nested model values are real form data, including null and rejected JSON."""
from copy import deepcopy
import json
import os
from pathlib import Path
from urllib.parse import parse_qs
import pytest
from bookflow.adapters.workbench import forms
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _contained, _click
from tests.test_mcp_registry_work import company_snapshot


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_nested_collection_model_object_null_and_full_error(register_browser,tmp_path,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    customer=_command(b,env.site,'customer.create',{'name':'Nested form customer'})['id']
    raw={'account':env.bank['id'],'direction':'decrease','allocations':[
        {'account':env.expense['id'],'amount':'10.00','party':{'name_type':'customer','name_id':customer}}]}
    expected=_command(b,env.site,'register.calculate',raw)
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    baseline=company_snapshot(root)
    captured=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='register calculate':captured.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    url=env.site.base_url+'/c/'+env.site.company_id+'/register/calculate'
    def key(field):
        selector='[data-collection-path=allocations] > [data-collection-items] > [data-collection-item] [name^="c:allocations:"][name$=":'+field+'"]'
        return b.evaluate(f'document.querySelector({json.dumps(selector)}).name')
    def prepare(party):
        form(b,url)
        _fill(b,'f:account',env.bank['id'])
        _fill(b,'f:direction','decrease')
        b.evaluate('document.querySelector("[data-collection-path=allocations] > [data-collection-add]").click()')
        _fill(b,key('account'),env.expense['id'])
        _fill(b,key('amount'),'10.00')
        _fill(b,key('party'),party)
        assert b.evaluate(f'document.getElementsByName({json.dumps(key("party"))})[0].tagName')=='TEXTAREA'
        b.evaluate("""(() => {const original = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(body) {
                sessionStorage.setItem('nestedWire', typeof body === 'string' ? body : new URLSearchParams(body).toString());
                return original.call(this, body);
            };})()""")
        return key('party')
    def submit_result():
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && !!document.querySelector(".save-feedback summary")')
        b.evaluate('document.querySelector(".save-feedback summary").click()')
        assert b.evaluate('document.querySelector(".save-feedback details").open')
        return json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
    party_name=prepare(json.dumps(raw['allocations'][0]['party']))
    assert submit_result()==expected
    assert captured[-1]==raw
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    assert json.loads(parse_qs(b.evaluate("sessionStorage.getItem('nestedWire')"))[party_name][0])==raw['allocations'][0]['party']
    _contained(b,width)
    assert company_snapshot(root)==baseline
    prepare('null')
    assert submit_result()==expected
    assert captured[-1]=={**raw,'allocations':[{**raw['allocations'][0],'party':None}]}
    prepare('{invalid')
    stage(b,'submit')
    assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate(f'document.getElementsByName({json.dumps(key("party"))})[0].value')=='{invalid'
    b.evaluate('document.querySelector(".error-details summary").click()')
    assert b.evaluate('document.querySelector(".error-details").open')
    error=json.loads(b.evaluate('document.querySelector(".error-details pre").textContent'))
    assert error['code']=='E_VALIDATION' and error['details']['fields']
    assert company_snapshot(root)==baseline
    _contained(b,width)


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_nullable_parent_object_clear_is_distinct_from_omission_and_child_patch(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    original={'line1':'Keep until clear','city':'Original city'}
    _command(b,env.site,'company.update',{'legal_address':original})
    captured=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='company update':captured.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    url=env.site.base_url+'/c/'+env.site.company_id+'/company/self/update'
    form(b,url)
    _fill(b,'f:legal_address.city','Changed city')
    stage(b)
    assert captured[-1]['legal_address']=={'city':'Changed city'}
    assert {key:_command(b,env.site,'company.show',{})['info']['legal_address_'+key] for key in original}==original
    # A parent null is a different input from a patch of its nullable children.
    assert b.evaluate('!!document.getElementsByName("clear:legal_address")[0]')
    b.evaluate('document.getElementsByName("clear:legal_address")[0].click()')
    stage(b)
    assert captured[-1]['legal_address'] is None
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['changed_fields']==['legal_address']
    assert {key:_command(b,env.site,'company.show',{})['info']['legal_address_'+key] for key in original}==original
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/company/self")')
    saved=_command(b,env.site,'company.show',{})
    assert all(saved['info']['legal_address_'+key] is None for key in ('line1','line2','city','state','postal_code','country'))
    form(b,url)
    _fill(b,'f:fax','Parent omission witness')
    stage(b)
    assert 'legal_address' not in captured[-1]
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['changed_fields']==['fax']


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_optional_nested_collection_order_empty_null_and_omission(register_browser,width,monkeypatch,tmp_path):
    env,b=register_browser,register_browser.browser
    field='required_employee_profile_fields'
    original=[['email','phone'],['first_name']]
    _command(b,env.site,'company.update',{field:original})
    captures=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='company update':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    url=env.site.base_url+'/c/'+env.site.company_id+'/company/self/update'
    form(b,url)
    outer='[data-collection-path='+field+'] > [data-collection-items]'
    b.evaluate(f'document.querySelector({json.dumps(outer+" > [data-collection-item]:last-child > .collection-item-actions > [data-collection-up]")}).click()')
    stage(b)
    assert captures[-1][field]==list(reversed(original))
    assert _command(b,env.site,'company.show',{})['info'][field]==original
    b.evaluate(f'document.getElementsByName("clear:{field}")[0].click()')
    stage(b)
    assert captures[-1][field] is None
    assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
    assert _command(b,env.site,'company.show',{})['info'][field]==original
    b.evaluate(f'document.getElementsByName("clear:{field}")[0].click()')
    b.evaluate(f'[...document.querySelectorAll({json.dumps(outer+" > [data-collection-item] > .collection-item-actions > [data-collection-remove]")})].forEach(button=>button.click())')
    stage(b)
    assert captures[-1][field]==[]
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['changed_fields']==[field]
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/company/self")')
    assert _command(b,env.site,'company.show',{})['info'][field]==[]
    form(b,url)
    _fill(b,'f:fax','Empty collection stays omitted')
    b.evaluate('document.getElementsByName("clear:expected_version")[0].click()')
    stage(b)
    assert field not in captures[-1] and captures[-1]['expected_version'] is None
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    before=company_snapshot(root)
    expected=_command(b,env.site,'company.update?dry_run=true',{'fax':'Empty collection stays omitted','expected_version':None})
    elapsed='seconds_since_previous_update'
    assert expected[elapsed]>=preview[elapsed]>=0
    assert {k:v for k,v in preview.items() if k!=elapsed}=={k:v for k,v in expected.items() if k!=elapsed}
    assert preview['dry_run'] and preview['changed_fields']==['fax']
    assert company_snapshot(root)==before
