"""Exact numeric decoding, rate-specific destination and full no-op/replay receipts."""
from copy import deepcopy
import json
import pytest
from bookflow.adapters.workbench import forms
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _command
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(150)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_rate_exact_integer_decimal_conflict_destination_noop_and_replay(register_browser,width,monkeypatch):
    env,b=register_browser,register_browser.browser
    b.viewport(width,900)
    captures=[]
    actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='rate set':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    url=env.site.base_url+'/c/'+env.site.company_id+'/rate/set'
    raw={'date':'2026-06-01','from_currency':'EUR','rate':'1.123456789012345678','expected_version':0}
    def prepare(version,key):
        form(b,url)
        for name,value in {**raw,'expected_version':version}.items():_fill(b,'f:'+name,str(value))
        _fill(b,'ctx:idempotency_key',key)
    def save():
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && !!document.querySelector(".save-feedback summary")')
        b.evaluate("""new Promise(resolve => {
            const details=document.querySelector('.save-feedback details');
            details.addEventListener('toggle', () => requestAnimationFrame(() => requestAnimationFrame(resolve)), {once:true});
            details.querySelector('summary').click();
        })""",await_promise=True)
        assert b.evaluate('document.querySelector(".save-feedback details").open')
        try:
            _contained(b,width)
        except AssertionError:
            from pathlib import Path
            evidence=Path('notes/mcp-evidence/rate-layout-full.json')
            evidence.write_text(json.dumps(b.evaluate("""[...document.querySelectorAll('body *')].map(e=>({
                tag:e.tagName,cls:e.className,text:e.textContent.slice(0,80),width:e.getBoundingClientRect().width,
                right:e.getBoundingClientRect().right,scroll:e.scrollWidth,client:e.clientWidth,overflow:getComputedStyle(e).overflowX
            })).filter(e=>e.right>391 || e.scroll>e.client+1)"""),indent=2))
            raise
        return json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
    prepare(9223372036854775807,'rate-rejected')
    stage(b)
    assert captures[-1]=={**raw,'expected_version':9223372036854775807}
    assert 'E_VERSION_CONFLICT' in b.evaluate('document.querySelector(".error").textContent')
    b.evaluate('document.querySelector(".error-details summary").click()')
    assert json.loads(b.evaluate('document.querySelector(".error-details pre").textContent'))['code']=='E_VERSION_CONFLICT'
    assert _command(b,env.site,'rate.query',{'date_from':raw['date'],'date_to':raw['date'],'from_currency':'EUR'})['items']==[]
    prepare(0,'rate-browser-save')
    stage(b)
    preview=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
    assert preview['dry_run'] and preview['changed'] and preview['rate']==raw['rate']
    assert captures[-1]==raw
    saved=save()
    assert saved['rate']==raw['rate'] and saved['changed'] and not saved['idempotent_replay']
    assert b.evaluate('location.pathname')=='/c/'+env.site.company_id+'/rate/'+saved['id']
    shown=_command(b,env.site,'rate.show',{'rate_id':saved['id']})
    assert shown['version']==1 and shown['rate']==raw['rate']
    prepare(0,'rate-browser-save')
    replay=save()
    assert replay=={**saved,'idempotent_replay':True}
    prepare(1,'rate-browser-noop')
    noop=save()
    assert not noop['changed'] and noop['version']==1 and noop['id']==saved['id']
    assert _command(b,env.site,'rate.show',{'rate_id':saved['id']})==shown
