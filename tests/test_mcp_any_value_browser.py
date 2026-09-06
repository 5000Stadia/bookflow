"""Explicit JSON values for registry Any fields, with text-mode values unchanged."""
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
def test_any_money_top_level_and_nested_json_values_and_unchanged_text(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        captures.append((cmd.name,deepcopy(result[0])))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id
    money={'minor_units':125,'currency':'USD'}
    expected=_command(b,env.site,'customer.create?dry_run=true',{'name':'Any money browser customer','credit_limit':money})
    form(b,base+'/customer/create')
    _fill(b,'f:name','Any money browser customer')
    _fill(b,'f:credit_limit',json.dumps(money))
    assert b.evaluate('!!document.getElementsByName("json:f:credit_limit")[0]')
    b.evaluate('document.getElementsByName("json:f:credit_limit")[0].click()')
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    assert captures[-1][1]['credit_limit']==money
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['credit_limit']==expected['credit_limit']
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/create")')
    customer=_command(b,env.site,'customer.show',{'customer':'Any money browser customer'})
    assert customer['credit_limit']==expected['credit_limit']
    form(b,base+'/customer/'+customer['id']+'/update')
    _fill(b,'f:phone','555-0102')
    stage(b)
    assert 'credit_limit' not in captures[-1][1]
    # Explicit JSON mode distinguishes null from omission; malformed JSON stays
    # in the same control and never changes the stored customer.
    b.evaluate('document.getElementsByName("json:f:credit_limit")[0].click()')
    for invalid in ('{invalid','NaN','1 2',''):
        _fill(b,'f:credit_limit',invalid)
        stage(b)
        assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
        assert b.evaluate('document.getElementsByName("f:credit_limit")[0].value')==invalid
        assert _command(b,env.site,'customer.show',{'customer':customer['id']})==customer
    _fill(b,'f:credit_limit','null')
    stage(b)
    assert captures[-1][1]['credit_limit'] is None
    assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['credit_limit'] is None
    assert _command(b,env.site,'customer.show',{'customer':customer['id']})==customer
    vendor=_command(b,env.site,'vendor.create',{'name':'Any money supplier'})['id']
    form(b,base+'/item/create')
    for name,value in {'name':'Any nested purchase cost','type':'non_inventory_part','purchase_enabled':'true',
                        'sales_enabled':'false','expense_account_id':env.expense['id'],'purchase_description':'Owned purchase','cost':'1.00'}.items():_fill(b,'f:'+name,value)
    b.evaluate('document.querySelector("[data-collection-path=vendor_profiles] > [data-collection-add]").click()')
    def key(field):
        selector='[data-collection-path=vendor_profiles] > [data-collection-items] > [data-collection-item] [name$=":'+field+'"]'
        return b.evaluate('document.querySelector('+json.dumps(selector)+').name')
    _fill(b,key('vendor_id'),vendor)
    _fill(b,key('preferred_rank'),'1')
    _fill(b,key('purchase_cost'),json.dumps(money))
    b.evaluate('document.getElementsByName('+json.dumps('json:'+key('purchase_cost'))+')[0].click()')
    second=_command(b,env.site,'vendor.create',{'name':'Any decimal supplier'})['id']
    b.evaluate('document.querySelector("[data-collection-path=vendor_profiles] > [data-collection-add]").click()')
    for field,value in {'vendor_id':second,'preferred_rank':'2','purchase_cost':'3.00'}.items():
        selector='[data-collection-path=vendor_profiles] > [data-collection-items] > [data-collection-item]:last-child [name$=":'+field+'"]'
        _fill(b,b.evaluate('document.querySelector('+json.dumps(selector)+').name'),value)
    b.evaluate('document.querySelector("[data-collection-path=vendor_profiles] > [data-collection-items] > [data-collection-item]:last-child > .collection-item-actions > [data-collection-up]").click()')
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
    assert captures[-1][1]['vendor_profiles'][0]['purchase_cost']=='3.00'
    assert captures[-1][1]['vendor_profiles'][1]['purchase_cost']==money
    assert b.evaluate('document.getElementsByName("json:c:vendor_profiles:0:purchase_cost")[0].checked') is False
    assert b.evaluate('document.getElementsByName("json:c:vendor_profiles:1:purchase_cost")[0].checked') is True
    prospective=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert {row['vendor_id']:row['purchase_cost']['minor_units'] for row in prospective['vendor_profiles']}=={vendor:125,second:300}
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/create")')
    item=_command(b,env.site,'item.show',{'item':'Any nested purchase cost'})
    assert {row['vendor_id']:row['purchase_cost']['minor_units'] for row in item['vendor_profiles']}=={vendor:125,second:300}
    assert captures[-1][1]['vendor_profiles'][0]['purchase_cost']=='3.00'
    assert captures[-1][1]['vendor_profiles'][1]['purchase_cost']==money
