"""Money-object correction, forbidden null and explicit return to a current price default."""
from copy import deepcopy
import json
import pytest
from bookflow.adapters.workbench import forms, sales
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained, _preview, _saved


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_sales_line_money_object_null_rejection_and_current_default_origin(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    def call(name,data):return _command(b,env.site,name.replace(' ','.'),data)
    customer=call('customer create',{'name':'Line origin payer'})['id']
    income=call('account create',{'name':'Line origin income','type':'income'})['id']
    ar=call('account create',{'name':'Line origin AR','type':'accounts_receivable'})['id']
    code=next(row['id'] for row in call('sales-tax-code list',{})['items'] if not row['taxable'])
    item=call('item create',{'name':'Line origin service','type':'service','sales_enabled':True,
        'income_account_id':income,'sales_tax_code_id':code,'price':'10.00','description':'Line origin service'})['id']
    saved=call('invoice post',{'customer':customer,'ar_account':ar,'date':'2026-06-01','lines':[{'item':item,'quantity':'1'}]})
    captured=[];rendered=[];actual=forms.translate;detail=sales.detail_context
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='invoice update':captured.append(deepcopy(result[0]))
        return result
    def presentation(record,*args,**kwargs):
        rendered.append(deepcopy(record))
        return detail(record,*args,**kwargs)
    monkeypatch.setattr(forms,'translate',observed)
    monkeypatch.setattr(sales,'detail_context',presentation)
    b.viewport(width,900)
    base=env.site.base_url+'/c/'+env.site.company_id+'/invoice/'+saved['id']
    form(b,base+'/update')
    def key(field):
        selector='[data-collection-path=lines] > [data-collection-items] > [data-collection-item] [name$=":'+field+'"]'
        return b.evaluate(f'document.querySelector({json.dumps(selector)}).name')
    _fill(b,key('unit_price'),'null')
    stage(b)
    assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
    assert captured[-1]['lines'][0]['unit_price'] is None
    assert b.evaluate(f'document.getElementsByName({json.dumps(key("unit_price"))})[0].value')=='null'
    assert call('invoice show',{'invoice':saved['id']})['revision']==saved['revision']
    _fill(b,key('unit_price'),json.dumps({'minor_units':125,'currency':'USD'}))
    _preview(b)
    assert captured[-1]['lines'][0]['unit_price']=={'minor_units':125,'currency':'USD'}
    line=rendered[-1]['revision']['lines'][0]
    assert line['unit_price']['minor_units']==125
    assert line['item_snapshot']['origins']['unit_price']['kind']=='explicit'
    assert '1.25' in b.evaluate('document.querySelector(".sales-document").innerText')
    _contained(b,width)
    _click(b,'submit')
    assert _saved(b,'invoice')==saved['id']
    explicit=call('invoice show',{'invoice':saved['id']})
    assert explicit['revision']['lines'][0]['line_id']==saved['revision']['lines'][0]['line_id']
    assert explicit['revision']['lines'][0]['unit_price']['minor_units']==125
    call('item update',{'item':item,'price':'12.00'})
    form(b,base+'/update')
    _fill(b,'f:memo','Keep explicit line origin')
    _preview(b)
    assert rendered[-1]['revision']['lines'][0]['unit_price']['minor_units']==125
    assert rendered[-1]['revision']['lines'][0]['item_snapshot']['origins']['unit_price']['kind']=='explicit'
    assert call('invoice show',{'invoice':saved['id']})==explicit
    # Returning to a default is an authored action, not an explicit null price.
    selector='[data-collection-path=lines] > [data-collection-items] > [data-collection-item] [data-collection-path$=":use_defaults"]'
    b.evaluate(f'document.querySelector({json.dumps(selector+" > [data-collection-add]")}).click()')
    name=b.evaluate(f'document.querySelector({json.dumps(selector+" > [data-collection-items] select")}).name')
    _fill(b,name,'unit_price')
    _preview(b)
    assert captured[-1]['lines'][0]['use_defaults']==['unit_price']
    line=rendered[-1]['revision']['lines'][0]
    assert line['unit_price']['minor_units']==1000
    assert line['item_snapshot']['origins']['unit_price']['kind']=='default'
    # use_defaults alone uses the captured price facts; explicit refresh adopts
    # the current catalog. These are separate owning-core operations.
    _fill(b,key('refresh_defaults'),'true')
    _preview(b)
    line=rendered[-1]['revision']['lines'][0]
    assert line['unit_price']['minor_units']==1200
    assert line['item_snapshot']['origins']['unit_price']['kind']=='default'
    assert '12.00' in b.evaluate('document.querySelector(".sales-document").innerText')
    assert call('invoice show',{'invoice':saved['id']})==explicit
    _contained(b,width)
    _click(b,'submit')
    assert _saved(b,'invoice')==saved['id']
    final=call('invoice show',{'invoice':saved['id']})
    assert final['version']==explicit['version']+1
    assert final['revision']['lines'][0]['unit_price']['minor_units']==1200
