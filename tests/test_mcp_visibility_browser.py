"""Presentation discriminators exclude inactive attempted controls on real submissions."""
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
def test_command_visibility_families_exclude_old_controls_and_save_to_exact_record(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        captures.append((cmd.name,deepcopy(result[0])))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    cases=[
        ('term','kind','standard',{'due_days':'29'},'date_driven',{'due_day_of_month':'15','due_next_month_if_within_days':'5'}),
        ('account','type','bank',{'routing_number_last4':'1234','next_check_number':'42'},'income',{}),
        ('item','type','discount',{'discount_percent':'2.5'},'subtotal',{'description':'Owned subtotal'}),
        ('price-level','kind','fixed_percent',{'percent':'1.25'},'per_item',{}),
    ]
    for noun,discriminator,old,old_fields,new,new_fields in cases:
        url=env.site.base_url+'/c/'+env.site.company_id+'/'+noun
        form(b,url+'/create')
        name='Visibility browser '+noun
        _fill(b,'f:name',name)
        if noun=='item':
            from bookflow.company.items import ITEM_PROFILES
            # Every data-driven profile is checked in the actual DOM. The
            # subsequent authored subtotal save exercises the common encoder.
            for profile_name,profile in ITEM_PROFILES.items():
                _fill(b,'f:type',profile_name)
                controls=b.evaluate('''[...document.querySelectorAll('[data-when-field="type"]')].map(row=>({
                    field:[...row.querySelectorAll('[name]')].find(e=>e.name.startsWith('f:')||e.name.startsWith('collection:'))?.name,
                    hidden:row.hidden, disabled:[...row.querySelectorAll('input,select,textarea')].every(e=>e.disabled)
                }))''')
                assert controls
                for control in controls:
                    field=control['field'].split(':',1)[1]
                    assert control['hidden']==(field not in profile.allowed),(profile_name,field,control)
                    assert control['disabled']==control['hidden'],(profile_name,field,control)
        _fill(b,'f:'+discriminator,old)
        for key,value in old_fields.items():_fill(b,'f:'+key,value)
        _fill(b,'f:'+discriminator,new)
        for key in old_fields:
            assert b.evaluate(f'document.getElementsByName({json.dumps("f:"+key)})[0].disabled')
        for key,value in new_fields.items():_fill(b,'f:'+key,value)
        if noun=='price-level':
            b.evaluate('document.querySelector("[data-collection-path=items] > [data-collection-add]").click()')
            ghost=b.evaluate('document.querySelector('+json.dumps('[data-collection-path=items] [name$=":percent"]')+').name')
            _fill(b,ghost,'not a percentage')
            b.evaluate('document.getElementsByName("empty:items")[0].click()')
            assert b.evaluate('bookflowMathContext.active(document.getElementsByName('+json.dumps(ghost)+')[0])') is False
        stage(b)
        assert not b.evaluate('document.querySelector(".error")?.textContent'),(noun,b.evaluate('document.body.innerText'))
        raw=captures[-1][1]
        assert captures[-1][0]==noun+' create'
        assert raw[discriminator]==new
        assert not set(old_fields)&raw.keys()
        preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
        assert preview[discriminator]==new
        if noun=='price-level':assert raw['items']==[] and preview['items']==[]
        _contained(b,width)
        if noun=='price-level':
            b.evaluate('document.getElementsByName("clear:items")[0].click()')
            stage(b)
            assert captures[-1][1]['items'] is None
            assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
            assert b.evaluate('document.getElementsByName("empty:items")[0].checked')
            b.evaluate('document.getElementsByName("clear:items")[0].click()')
            stage(b)
            assert not b.evaluate('document.querySelector(".error")?.textContent')
            assert captures[-1][1]['items']==[]
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/create")')
        shown=_command(b,env.site,noun+'.show',{noun.replace('-','_'):name})
        assert b.evaluate('location.pathname').endswith('/'+noun+'/'+shown['id'])
        assert shown[discriminator]==new
        b.evaluate('document.querySelector(".save-feedback summary").click()')
        receipt=json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
        assert {key:receipt[key] for key in shown}==shown
        _contained(b,width)
        # Immutable discriminators in update forms remain pinned to the record.
        if noun in ('term','price-level'):
            form(b,url+'/'+shown['id']+'/update')
            assert b.evaluate(f'document.getElementsByName({json.dumps("f:"+discriminator)})[0].type')=='hidden'
            assert b.evaluate(f'document.getElementsByName({json.dumps("f:"+discriminator)})[0].value')==new
            _fill(b,'f:name',name+' renamed')
            stage(b)
            assert not b.evaluate('document.querySelector(".error")?.textContent')
            assert discriminator not in captures[-1][1]
            assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))[discriminator]==new
            assert _command(b,env.site,noun+'.show',{noun.replace('-','_'):shown['id']})==shown
            _contained(b,width)
