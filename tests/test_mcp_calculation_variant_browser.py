"""Actual calculation controls retain unresolved/entered rows and selection identity."""
from copy import deepcopy
import json
import os
from pathlib import Path
import pytest
from bookflow.adapters.workbench import forms
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained
from tests.test_mcp_registry_work import company_snapshot


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_calculation_inline_null_origin_rejections_and_saved_selection(register_browser,width,monkeypatch,tmp_path):
    env,b=register_browser,register_browser.browser
    def call(name,data):return _command(b,env.site,name.replace(' ','.'),data)
    customer=call('customer create',{'name':'Calculation variant payer'})['id']
    income=call('account create',{'name':'Calculation variant income','type':'income'})['id']
    ar=call('account create',{'name':'Calculation variant AR','type':'accounts_receivable'})['id']
    code=next(row['id'] for row in call('sales-tax-code list',{})['items'] if not row['taxable'])
    item=call('item create',{'name':'Calculation variant service','type':'service','sales_enabled':True,
        'income_account_id':income,'sales_tax_code_id':code,'price':'10.00','description':'Calculation variant'})['id']
    invoice=call('invoice post',{'customer':customer,'ar_account':ar,'date':'2026-06-01',
        'lines':[{'item':item,'quantity':'1','unit_price':'10.00'}]})
    context={'mode':'new_receipt','customer':customer,'ar_account':ar,'date':'2026-06-01'}
    selection=call('payment selection create',{**context,'amount':'5.00'})
    row={'invoice':invoice['id'],'expected_version':invoice['version'],'amount':None,'amount_origin':'unresolved'}
    selection=call('payment selection update',{'selection':selection['id'],'expected_version':selection['version'],'set_items':[row]})
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    baseline=company_snapshot(root)
    captured=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='payment calculate':captured.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    url=env.site.base_url+'/c/'+env.site.company_id+'/payment/calculate'
    def prepare(value,origin):
        form(b,url)
        for name,value_ in {**context,'amount_mode':'entered','amount':'5.00','applications.mode':'inline'}.items():_fill(b,'f:'+name,str(value_))
        b.evaluate('document.querySelector("[data-collection-path=\\"applications.items\\"] > [data-collection-add]").click()')
        for field,value_ in {**row,'amount':value,'amount_origin':origin}.items():
            selector='[data-collection-path="applications.items"] > [data-collection-items] > [data-collection-item] [name$=":'+field+'"]'
            name=b.evaluate(f'document.querySelector({json.dumps(selector)}).name')
            _fill(b,name,'null' if value_ is None else json.dumps(value_) if isinstance(value_,dict) else str(value_))
    def receipt(raw):
        expected=call('payment calculate',raw)
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && (!!document.querySelector(".save-feedback summary") || !!document.querySelector(".error"))')
        assert captured[-1]==raw
        assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
        b.evaluate('document.querySelector(".save-feedback summary").click()')
        assert json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))==expected
        assert company_snapshot(root)==baseline
        _contained(b,width)
    for value,origin in [(None,'unresolved'),({'minor_units':125,'currency':'USD'},'entered')]:
        prepare(value,origin)
        receipt({**context,'amount_mode':'entered','amount':'5.00','applications':{'mode':'inline','items':[{**row,'amount':value,'amount_origin':origin}]}})
    for value,origin in [('1.00','unresolved'),(None,'entered')]:
        prepare(value,origin)
        stage(b,'submit')
        assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
        assert captured[-1]['applications']['items'][0]['amount']==value
        assert captured[-1]['applications']['items'][0]['amount_origin']==origin
        assert company_snapshot(root)==baseline
        _contained(b,width)
    prepare('1.00','unresolved')
    _fill(b,'f:applications.mode','selection')
    _fill(b,'f:applications.selection',selection['id'])
    _fill(b,'f:applications.expected_version',str(selection['version']))
    assert b.evaluate('document.getElementsByName("collection:applications.items")[0].disabled')
    receipt({**context,'amount_mode':'entered','amount':'5.00','applications':{'mode':'selection','selection':selection['id'],'expected_version':selection['version']}})
