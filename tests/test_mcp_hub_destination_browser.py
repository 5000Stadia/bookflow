"""Hub rollouts publish complete receipts to the newly owned company/organization."""
from copy import deepcopy
from bookflow.adapters.workbench import forms
import json
import os
from pathlib import Path
import sqlite3
import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser
from tests.test_mcp_workbench_control_browser import form, stage
from tests.test_service_sales_browser import _fill, _click, _contained
from tests.test_mcp_registry_rollout import state


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.exists(),reason='Chrome unavailable')
def test_hub_organization_and_company_new_attach_destinations_with_complete_receipts(register_browser,width,tmp_path,monkeypatch):
    env,b=register_browser,register_browser.browser
    root=Path(os.environ['BOOKFLOW_DATA_ROOT']);assert root.is_relative_to(tmp_path)
    captures=[];actual=forms.translate
    def observed(cmd,*args,**kwargs):
        result=actual(cmd,*args,**kwargs)
        if cmd.name=='company new':captures.append(deepcopy(result[0]))
        return result
    monkeypatch.setattr(forms,'translate',observed)
    b.viewport(width,900)
    def preview():
        before=state(root)
        stage(b)
        assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
        out=json.loads(b.evaluate('document.querySelector(".warn pre").textContent'))
        assert out['dry_run'] and state(root)==before
        _contained(b,width)
        return out
    def saved(target):
        _click(b,'submit')
        b.wait_for('document.readyState === "complete" && !!document.querySelector(".save-feedback summary")')
        assert not b.evaluate('document.querySelector(".error")?.textContent'),b.evaluate('document.body.innerText')
        b.evaluate('document.querySelector(".save-feedback summary").click()')
        out=json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))
        assert not out['dry_run']
        assert b.evaluate('location.pathname')==target(out)
        _contained(b,width)
        return out
    form(b,env.site.base_url+'/hub/organization/new')
    _fill(b,'f:name','Owned browser rollout organization')
    prospective=preview()
    org=saved(lambda out:'/hub/organization/'+out['organization_id'])
    assert org['display_name']==prospective['display_name']=='Owned browser rollout organization'
    shown=b.evaluate("fetch('/commands/organization.show',{method:'POST',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:JSON.stringify({organization:"+json.dumps(org['organization_id'])+"})}).then(r=>r.json())",await_promise=True)
    assert {key:org[key] for key in shown}==shown
    assert 'Owned browser rollout organization' in b.evaluate('document.body.innerText')
    form(b,env.site.base_url+'/hub/organization/'+org['organization_id']+'/rename')
    _fill(b,'f:name','Renamed browser rollout organization')
    prospective=preview()
    renamed=saved(lambda out:'/hub/organization/'+org['organization_id'])
    assert renamed=={**prospective,'dry_run':False}
    form(b,env.site.base_url+'/hub/company/new')
    for name,value in {'legal_name':'Owned browser rollout company','home_currency':'USD','organization':org['organization_id'],'chart':'none',
                       'address.line1':'Shared contact street','legal_address.line1':'Separate legal street'}.items():_fill(b,'f:'+name,value)
    b.evaluate('document.getElementsByName("clear:ship_address")[0].click()')
    prospective=preview()
    company=saved(lambda out:'/c/'+out['company_id']+'/')
    folder=Path(company['path']);assert folder.is_relative_to(root) and (folder/'company.db').is_file()
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT organization_id FROM companies WHERE id=?',(company['company_id'],)).fetchone()==(org['organization_id'],)
    cid=company['company_id']
    result=b.evaluate(f"fetch('/companies/{cid}/commands/company.show',{{method:'POST',headers:{{'Content-Type':'application/json','X-Bookflow-Workbench':'1'}},body:'{{}}'}}).then(r=>r.json())",await_promise=True)
    assert result['info']['legal_address_line1']=='Separate legal street'
    assert captures[-1]['ship_address'] is None
    # Creation's owning core falls back to the contact address for null; it is
    # deliberately different from clearing an already stored company address.
    assert result['info']['ship_address_line1']=='Shared contact street'
    form(b,env.site.base_url+'/hub/company/detach')
    _fill(b,'f:company',cid)
    prospective=preview()
    assert saved(lambda out:'/companies')=={**prospective,'dry_run':False}
    assert (folder/'company.db').is_file()
    form(b,env.site.base_url+'/hub/company/attach')
    _fill(b,'f:path',str(folder))
    prospective=preview()
    attached=saved(lambda out:'/c/'+out['company_id']+'/')
    assert attached['company_id']==cid
    assert attached=={**prospective,'dry_run':False}
    # Hub read fallback has an ordinary list destination and a complete read
    # receipt, rather than a false saved-write label.
    form(b,env.site.base_url+'/hub/chart/list')
    expected=b.evaluate("fetch('/commands/chart.list',{method:'POST',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:'{}'}).then(r=>r.json())",await_promise=True)
    _click(b,'submit')
    b.wait_for('document.readyState === "complete" && location.pathname === "/hub/chart" && !!document.querySelector(".save-feedback summary")')
    assert 'Completed' in b.evaluate('document.querySelector(".save-feedback").innerText')
    b.evaluate('document.querySelector(".save-feedback summary").click()')
    assert json.loads(b.evaluate('document.querySelector(".save-feedback pre").textContent'))==expected
    _contained(b,width)
    # This replaces only the disposable demo created by this test fixture.
    # Both archive and replacement paths are checked inside this owned root.
    form(b,env.site.base_url+'/hub/demo/reset')
    _fill(b,'f:include_reference','false')
    prospective=preview()
    reset=saved(lambda out:'/c/'+out['company_id']+'/')
    assert Path(reset['path']).is_relative_to(root)
    assert (Path(reset['path'])/'company.db').is_file()
    if reset['trashed_path']:
        assert Path(reset['trashed_path']).is_relative_to(root)
        assert list(Path(reset['trashed_path']).rglob('company.db'))
    assert reset['display_name']==prospective['display_name']
