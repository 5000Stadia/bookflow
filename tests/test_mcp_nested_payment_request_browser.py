"""Each nested preview request uses real controls and the owning command preview."""
from copy import deepcopy
import json
import os
from pathlib import Path
import pytest
from bookflow.adapters.workbench import forms
from bookflow.core import registry
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form
from tests.test_service_sales_browser import _fill, _click, _contained
from tests.test_mcp_registry_work import company_snapshot


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(240)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls(register_browser,width,monkeypatch,tmp_path):
    env,b=register_browser,register_browser.browser
    reason='Nested request browser witness'
    def call(name,data,preview=False):
        return _command(b,env.site,name.replace(' ','.')+('?dry_run=true' if preview else ''),data,
                        **({'X-Bookflow-Reason':reason} if registry.get(name).is_write else {}))
    customer=call('customer create',{'name':'Nested request payer'})['id']
    income=call('account create',{'name':'Nested request income','type':'income'})['id']
    ar=call('account create',{'name':'Nested request AR','type':'accounts_receivable'})['id']
    code=next(row['id'] for row in call('sales-tax-code list',{})['items'] if not row['taxable'])
    item=call('item create',{'name':'Nested request service','type':'service','sales_enabled':True,
                           'income_account_id':income,'sales_tax_code_id':code,'price':'10.00','description':'Nested request service'})['id']
    invoice=call('invoice post',{'customer':customer,'ar_account':ar,'date':'2026-06-01',
                                 'lines':[{'item':item,'quantity':'1','unit_price':'10.00'}]})
    method=call('payment-method create',{'name':'Nested request cash','kind':'cash'})['id']
    receive={'customer':customer,'ar_account':ar,'date':'2026-06-01','amount':'10.00',
             'payment_method':method,'operation_key':'nested-seed-receive'}
    paid=call('payment receive',receive)
    applied=call('payment apply',{'payment':paid['id'],'expected_version':paid['version'],
        'date':'2026-06-01','operation_key':'nested-seed-apply','applications':{'mode':'inline',
        'items':[{'invoice':invoice['id'],'expected_version':invoice['version'],'amount':'0.50'}]}})
    invoice=call('invoice show',{'invoice':invoice['id']})
    invoice_version=invoice['version']
    application=applied['effect']['applications'][0]['application_id']
    payment_base={'payment':paid['id'],'expected_version':applied['version']}
    voidable=call('payment receive',{**receive,'amount':'1.00','operation_key':'nested-seed-unapplied-receive'})
    requests={
        'payment receive':{**receive,'operation_key':'nested-preview-receive'},
        'payment apply':{**payment_base,'date':'2026-06-01','operation_key':'nested-preview-apply',
            'applications':{'mode':'inline','items':[{'invoice':invoice['id'],'expected_version':invoice_version,'amount':'0.50'}]}},
        'payment unapply':{**payment_base,'operation_key':'nested-preview-unapply','applications':[
            {'application_id':application,'invoice_expected_version':invoice_version}]},
        'payment update':{**payment_base,'operation_key':'nested-preview-update','memo':'Prospective receipt note',
            'invoice_versions':[{'invoice':invoice['id'],'expected_version':invoice_version}]},
        'payment void':{'payment':voidable['id'],'expected_version':voidable['version'],'operation_key':'nested-preview-void'},
        'invoice update':{'invoice':invoice['id'],'expected_version':invoice_version,
            'operation_key':'nested-preview-invoice-update','memo':'Prospective invoice note',
            'settlement_versions':[{'payment':paid['id'],'expected_version':applied['version']}]},
    }
    request_cases=list(requests.items())
    for command,context in (
        ('payment receive',{'mode':'new_receipt','customer':customer,'ar_account':ar,'date':'2026-06-01','amount':'10.00'}),
        ('payment apply',{'mode':'existing_credit','payment':paid['id'],'date':'2026-06-01','amount':'0.50'}),
    ):
        selected=call('payment selection create',context)
        selected=call('payment selection update',{'selection':selected['id'],'expected_version':selected['version'],
            'set_items':[{'invoice':invoice['id'],'expected_version':invoice_version,'amount':'0.50','amount_origin':'entered'}]})
        request_cases.append((command,{**requests[command],
            'operation_key':requests[command]['operation_key']+'-selection',
            'applications':{'mode':'selection','selection':selected['id'],'expected_version':selected['version']}}))
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    baseline=company_snapshot(root)
    captures=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='payment preview items':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    def enter(path,value):
        if isinstance(value,dict):
            for key,item in value.items():enter(path+'.'+key if path else key,item)
        elif isinstance(value,list):
            for row in value:
                selector='[data-collection-path='+json.dumps(path)+']'
                b.evaluate(f'document.querySelector({json.dumps(selector+" > [data-collection-add]")}).click()')
                # Indexes belong to the actual DOM, not the input model or this fixture.
                for field,item in row.items():
                    control_selector=selector+' > [data-collection-items] > [data-collection-item]:last-child [name$=":'+field+'"]'
                    control=b.evaluate(f'document.querySelector({json.dumps(control_selector)}).name')
                    _fill(b,control,str(item))
        else:_fill(b,'f:'+path,str(value))
    for command,data in request_cases:
        preview=call(command,data,True)
        effect=preview['settlement'] if command=='invoice update' else preview
        raw={'request':{'command':command,'input':data,'context':{'reason':reason}},
             'facts_fingerprint':effect['facts_fingerprint'],'kind':'applications','limit':50}
        expected=call('payment preview items',raw)
        form(b,env.site.base_url+'/c/'+env.site.company_id+'/payment%20preview/items')
        if command=='payment receive':ghost_command,ghost_field,ghost_value='invoice update','invoice',invoice['id']
        elif command=='invoice update':ghost_command,ghost_field,ghost_value='payment void','payment',paid['id']
        else:ghost_command,ghost_field,ghost_value='payment receive','customer',customer
        _fill(b,'f:request.command',ghost_command)
        _fill(b,'f:request.input.'+ghost_field,ghost_value)
        _fill(b,'f:request.command',command)
        assert b.evaluate(f'document.getElementsByName({json.dumps("f:request.input."+ghost_field)})[0].disabled')
        enter('',raw)
        _contained(b,width)
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && (!!document.querySelector(".save-feedback summary") || !!document.querySelector(".error"))')
        assert captures[-1]==raw,(command,captures[-1],raw)
        assert not b.evaluate('document.querySelector(".error")?.textContent'),(command,b.evaluate('document.body.innerText'))
        b.evaluate('document.querySelector(".save-feedback summary").click()')
        result=json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
        assert result==expected,command
        assert company_snapshot(root)==baseline,command
        _contained(b,width)
