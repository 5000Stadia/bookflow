"""Structured money remains exact through actual browser text and host decoding."""
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
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_structured_integer_money_object_exact_bytes_and_core_rejection(register_browser,width,monkeypatch,tmp_path):
    env,b=register_browser,register_browser.browser
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    baseline=company_snapshot(root)
    captures=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='register calculate':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    url=env.site.base_url+'/c/'+env.site.company_id+'/register/calculate'
    units=9007199254740993  # One greater than the largest consecutive JS Number integer.
    money={'minor_units':units,'currency':'USD'}
    def prepare(value):
        form(b,url)
        _fill(b,'f:account',env.bank['id'])
        _fill(b,'f:direction','decrease')
        b.evaluate('document.querySelector("[data-collection-path=allocations] > [data-collection-add]").click()')
        def key(field):
            return b.evaluate('document.querySelector('+json.dumps('[data-collection-path=allocations] > [data-collection-items] [name$=":'+field+'"]')+').name')
        _fill(b,key('account'),env.expense['id'])
        _fill(b,key('amount'),value)
        return key
    key=prepare(json.dumps(money))
    b.evaluate(f'document.getElementsByName({json.dumps(key("amount"))})[0].closest(".collection-field").querySelector(".structured-input-help summary").click()')
    from bookflow.company.journal_models import MoneyInput
    shown_schema=json.loads(b.evaluate(f'document.getElementsByName({json.dumps(key("amount"))})[0].closest(".collection-field").querySelector(".structured-input-help pre").textContent'))
    assert shown_schema==MoneyInput.model_json_schema()
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !!document.querySelector(".save-feedback summary")')
    assert captures[-1]=={'account':env.bank['id'],'direction':'decrease','allocations':[{'account':env.expense['id'],'amount':money}]}
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    # Parse text in Python; JSON.parse would itself round the very integer tested.
    result=json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
    assert result['amount']['minor_units']==units and result['amount']['amount']=='90071992547409.93'
    assert company_snapshot(root)==baseline
    _contained(b,width)
    for amount in ('1.00','1+0.25'):
        bad=json.dumps({**money,'amount':amount})
        key=prepare(bad)
        stage(b,'submit')
        assert captures[-1]['allocations'][0]['amount']=={**money,'amount':amount}
        assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
        assert b.evaluate(f'document.getElementsByName({json.dumps(key("amount"))})[0].value')==bad
        assert company_snapshot(root)==baseline
        _contained(b,width)



@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_top_level_money_object_nullable_branch_and_complete_calculation(register_browser,width,monkeypatch,tmp_path):
    env,b=register_browser,register_browser.browser
    customer=_command(b,env.site,'customer.create',{'name':'Structured calculation payer'})['id']
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    baseline=company_snapshot(root)
    captures=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='payment calculate':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    raw={'mode':'new_receipt','customer':customer,'date':'2026-06-01','amount_mode':'entered',
         'amount':{'minor_units':125,'currency':'USD'},'applications':{'mode':'inline'}}
    expected=_command(b,env.site,'payment.calculate',raw)
    url=env.site.base_url+'/c/'+env.site.company_id+'/payment/calculate'
    def prepare(amount):
        form(b,url)
        for name,value in {'mode':raw['mode'],'customer':customer,'date':raw['date'],'amount_mode':'entered',
                           'amount':amount,'applications.mode':'inline'}.items():_fill(b,'f:'+name,value)
        assert b.evaluate('document.getElementsByName("f:amount")[0].dataset.mathStructured')=='object'
    prepare(json.dumps(raw['amount']))
    b.evaluate('document.getElementsByName("f:amount")[0].closest(".form-field").querySelector(".structured-input-help summary").click()')
    from bookflow.company.sales_models import SalesMoneyInput
    assert json.loads(b.evaluate('document.getElementsByName("f:amount")[0].closest(".form-field").querySelector(".structured-input-help pre").textContent'))==SalesMoneyInput.model_json_schema()
    _contained(b,width)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && !!document.querySelector(".save-feedback summary")')
    assert captures[-1]==raw
    assert b.evaluate('document.querySelector(".save-feedback > b").textContent')=='Completed — payment calculate'
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    assert json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))==expected
    assert company_snapshot(root)==baseline
    _contained(b,width)
    prepare('null')
    # Entered mode requires an amount; literal null must reach the core rather
    # than turn into omission or an arithmetic-entry error.
    stage(b,'submit')
    assert captures[-1]=={**raw,'amount':None}
    assert b.evaluate('document.querySelector(".error")?.textContent')
    assert b.evaluate('document.getElementsByName("f:amount")[0].value')=='null'
    assert company_snapshot(root)==baseline
    _contained(b,width)
