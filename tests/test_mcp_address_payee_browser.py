"""Owned address defaults and top-level register parties through real browser controls."""
from copy import deepcopy
import json
import pytest
from bookflow.adapters.workbench import forms
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_party_address_create_patch_clear_and_omission(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name in ('customer create','customer update'):captures.append((cmd.name,deepcopy(result[0])))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id+'/customer'
    form(b,base+'/create')
    _fill(b,'f:name','Address input browser witness')
    _fill(b,'f:billing_address.line1','  Owned address  ')
    _fill(b,'f:billing_address.city','Original city')
    stage(b)
    assert captures[-1][1]['billing_address']=={'line1':'  Owned address  ','city':'Original city'}
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['billing_address']['line1']=='Owned address'
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/create")')
    saved=_command(b,env.site,'customer.show',{'customer':'Address input browser witness'})
    assert b.evaluate('location.pathname').endswith('/customer/'+saved['id'])
    assert saved['billing_address']==preview['billing_address']
    form(b,base+'/'+saved['id']+'/update')
    assert b.evaluate('document.getElementsByName("f:billing_address.line1")[0].value')=='Owned address'
    _fill(b,'f:billing_address.city','Changed city')
    stage(b)
    assert captures[-1][1]['billing_address']=={'city':'Changed city'}
    partial=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    # The owning party model decides whether child patches merge or replace.
    expected=_command(b,env.site,'customer.update?dry_run=true',captures[-1][1])
    assert partial['billing_address']==expected['billing_address']
    assert _command(b,env.site,'customer.show',{'customer':saved['id']})==saved
    b.evaluate('document.getElementsByName("clear:billing_address")[0].click()')
    stage(b)
    assert captures[-1][1]['billing_address'] is None
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['billing_address'] is None
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/update")')
    assert _command(b,env.site,'customer.show',{'customer':saved['id']})['billing_address'] is None
    form(b,base+'/'+saved['id']+'/update')
    _fill(b,'f:phone','555-0101')
    stage(b)
    assert 'billing_address' not in captures[-1][1]
    _contained(b,width)
    form(b,base+'/create')
    _fill(b,'f:name','Explicit null creation address')
    b.evaluate('document.getElementsByName("clear:billing_address")[0].click()')
    stage(b)
    assert captures[-1][1]['billing_address'] is None
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['billing_address'] is None


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_top_level_register_payee_prefill_null_replacement_and_journal_destination(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    customer=_command(b,env.site,'customer.create',{'name':'Top level payee'})['id']
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name in ('register post','register update'):captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id
    form(b,base+'/register/post')
    data={'account':env.bank['id'],'date':'2026-06-01','direction':'decrease','amount':'2.00',
          'category':env.expense['id'],'payee.name_type':'customer','payee.name_id':customer}
    for name,value in data.items():_fill(b,'f:'+name,value)
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    assert captures[-1]['payee']=={'name_type':'customer','name_id':customer}
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.includes("/journal/")')
    identifier=b.evaluate('location.pathname.split("/").pop()')
    saved=_command(b,env.site,'journal.show',{'journal':identifier})
    fields=('account_id','account_snapshot','side','amount','description','name_type','name_id')
    assert [{key:row[key] for key in fields} for row in saved['revision']['lines']]==[
        {key:row[key] for key in fields} for row in preview['revision']['lines']]
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    receipt=json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
    assert {key:receipt[key] for key in saved}==saved
    form(b,base+'/register/'+identifier+'/update')
    assert b.evaluate('document.getElementsByName("f:payee.name_type")[0].value')=='customer'
    assert b.evaluate('document.getElementsByName("f:payee.name_id")[0].value')==customer
    _fill(b,'f:memo','Replacement preserves payee')
    stage(b)
    assert captures[-1]['payee']=={'name_type':'customer','name_id':customer}
    assert _command(b,env.site,'journal.show',{'journal':identifier})==saved
    b.evaluate('document.getElementsByName("clear:payee")[0].click()')
    stage(b)
    assert captures[-1]['payee'] is None
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname.endsWith("/journal/"+'+json.dumps(identifier)+')')
    updated=_command(b,env.site,'journal.show',{'journal':identifier})
    assert updated['version']==saved['version']+1
    assert all(row['name_id'] is None for row in updated['revision']['lines'])


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_sales_address_default_explicit_null_and_return_to_current_default(register_browser,width,monkeypatch):
    from bookflow.adapters.workbench import sales
    from tests.test_service_sales_browser import _preview, _saved
    env,b=register_browser,register_browser.browser
    def call(name,data):return _command(b,env.site,name.replace(' ','.'),data)
    customer=call('customer create',{'name':'Sales address payer','billing_address':{'line1':'Original default street'}})['id']
    income=call('account create',{'name':'Address income','type':'income'})['id']
    ar=call('account create',{'name':'Address AR','type':'accounts_receivable'})['id']
    code=next(row['id'] for row in call('sales-tax-code list',{})['items'] if not row['taxable'])
    item=call('item create',{'name':'Address service','type':'service','sales_enabled':True,
        'income_account_id':income,'sales_tax_code_id':code,'price':'10.00','description':'Address service'})['id']
    captured=[];rendered=[];actual=forms.translate;detail=sales.detail_context
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name in ('invoice post','invoice update'):captured.append(deepcopy(result[0]))
        return result
    def presentation(record,*args,**kwargs):
        rendered.append(deepcopy(record))
        return detail(record,*args,**kwargs)
    monkeypatch.setattr(forms,'translate',observed)
    monkeypatch.setattr(sales,'detail_context',presentation)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id+'/invoice'
    form(b,base+'/post')
    for key,value in {'customer':customer,'ar_account':ar,'date':'2026-06-01'}.items():_fill(b,'f:'+key,value)
    b.evaluate('document.querySelector("[data-collection-path=lines] > [data-collection-add]").click()')
    for key,value in {'item':item,'quantity':'1'}.items():_fill(b,'c:lines:0:'+key,value)
    _preview(b)
    assert 'billing_address' not in captured[-1]
    profile=rendered[-1]['revision']['profile']
    assert profile['billing_address']['line1']=='Original default street'
    assert profile['origins']['billing_address']['kind']=='default'
    assert 'Original default street' in b.evaluate('document.querySelector(".sales-document").innerText')
    _fill(b,'f:billing_address.line1','Explicit browser street')
    _preview(b)
    assert captured[-1]['billing_address']=={'line1':'Explicit browser street'}
    assert rendered[-1]['revision']['profile']['origins']['billing_address']['kind']=='explicit'
    assert 'Explicit browser street' in b.evaluate('document.querySelector(".sales-document").innerText')
    b.evaluate('document.getElementsByName("clear:billing_address")[0].click()')
    _preview(b)
    assert captured[-1]['billing_address'] is None
    assert rendered[-1]['revision']['profile']['billing_address'] is None
    assert rendered[-1]['revision']['profile']['origins']['billing_address']['kind']=='explicit'
    assert 'Explicit browser street' not in b.evaluate('document.querySelector(".sales-document").innerText')
    assert call('invoice query',{'customer':customer})['items']==[]
    _contained(b,width)
    _click(b,'submit')
    identifier=_saved(b,'invoice')
    saved=call('invoice show',{'invoice':identifier})
    assert saved['revision']['profile']['billing_address'] is None
    call('customer update',{'customer':customer,'billing_address':{'line1':'Current customer street'}})
    form(b,base+'/'+identifier+'/update')
    _fill(b,'f:memo','Address omission keeps explicit null')
    _preview(b)
    assert 'billing_address' not in captured[-1]
    assert rendered[-1]['revision']['profile']['billing_address'] is None
    b.evaluate('document.querySelector("[data-collection-path=use_defaults] > [data-collection-add]").click()')
    name=b.evaluate('document.querySelector("[data-collection-path=use_defaults] [data-collection-items] select").name')
    _fill(b,name,'billing_address')
    _preview(b)
    assert captured[-1]['use_defaults']==['billing_address']
    assert rendered[-1]['revision']['profile']['billing_address']['line1']=='Current customer street'
    assert rendered[-1]['revision']['profile']['origins']['billing_address']['kind']=='default'
    assert 'Current customer street' in b.evaluate('document.querySelector(".sales-document").innerText')
    assert call('invoice show',{'invoice':identifier})==saved
    _contained(b,width)
    _click(b,'submit')
    assert _saved(b,'invoice')==identifier
    updated=call('invoice show',{'invoice':identifier})
    assert updated['version']==saved['version']+1
    assert updated['revision']['profile']['billing_address']['line1']=='Current customer street'
