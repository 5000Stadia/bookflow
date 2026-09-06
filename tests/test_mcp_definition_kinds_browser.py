"""Definition defaults use exact owned kinds, including empty text, null and omission."""
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
def test_definition_all_kinds_default_empty_null_omission_and_choice_visibility(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='custom-field update':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    for kind,initial,value in [('text','Original',''),('number','1','0.123456789'),('date','2026-01-01','2026-06-01'),('bool',True,False),('choice','First','Second')]:
        data={'name':'Default variant '+kind,'kind':kind,'scopes':['customer'],'default':initial}
        if kind=='choice':data['choices']=[{'value':'First'},{'value':'Second'}]
        saved=_command(b,env.site,'custom-field.create',data)
        url=env.site.base_url+'/c/'+env.site.company_id+'/custom-field/'+saved['id']+'/update'
        form(b,url)
        _fill(b,'f:default',str(value).lower() if isinstance(value,bool) else value)
        if kind=='text':
            _fill(b,'f:default','{"keep":"literal text"}')
            stage(b)
            assert captures[-1]['default']=='{"keep":"literal text"}'
            assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['default']=='{"keep":"literal text"}'
            _fill(b,'f:default','')
        if value=='':
            # An explicit empty value must have an action distinct from blank/omit.
            assert b.evaluate('!!document.getElementsByName("empty:default")[0]')
            b.evaluate('document.getElementsByName("empty:default")[0].click()')
        stage(b)
        assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
        assert captures[-1]['default']==value
        assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['default']==value
        assert _command(b,env.site,'custom-field.show',{'custom_field':saved['id']})['default']==initial
        _contained(b,width)
        if kind=='text':
            b.evaluate('document.getElementsByName("clear:default")[0].click()')
            stage(b)
            assert captures[-1]['default'] is None
            assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['default'] is None
            assert _command(b,env.site,'custom-field.show',{'custom_field':saved['id']})['default']==initial
            b.evaluate('document.getElementsByName("clear:default")[0].click()')
            stage(b)
            assert captures[-1]['default']==''
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && !location.pathname.endsWith("/update")')
        current=_command(b,env.site,'custom-field.show',{'custom_field':saved['id']})
        assert current['default']==value and current['version']==saved['version']+1
        form(b,url)
        _fill(b,'f:position','11')
        stage(b)
        assert 'default' not in captures[-1]
        assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['default']==value
        b.evaluate('document.getElementsByName("clear:default")[0].click()')
        stage(b)
        assert captures[-1]['default'] is None
        assert json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))['default'] is None
        assert _command(b,env.site,'custom-field.show',{'custom_field':saved['id']})==current
        _contained(b,width)
    # A choice-only collection is omitted after changing the actual discriminator.
    form(b,env.site.base_url+'/c/'+env.site.company_id+'/custom-field/create')
    _fill(b,'f:name','Inactive choice default witness')
    _fill(b,'f:kind','choice')
    b.evaluate('document.querySelector("[data-collection-path=choices] > [data-collection-add]").click()')
    name=b.evaluate('document.querySelector("[data-collection-path=choices] [name$=\\":value\\"]").name')
    _fill(b,name,'Only for choice')
    _fill(b,'f:kind','text')
    assert b.evaluate('document.getElementsByName("collection:choices")[0].disabled')
    b.evaluate('document.querySelector("[data-collection-path=scopes] > [data-collection-add]").click()')
    name=b.evaluate('document.querySelector("[data-collection-path=scopes] [data-collection-items] select").name')
    _fill(b,name,'customer')
    stage(b)
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['kind']=='text' and preview['choices']==[]
    _contained(b,width)
