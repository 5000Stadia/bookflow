"""Loaded-target isolation and shared permission commands on desktop and phone."""
import base64
import json
from pathlib import Path
import time
import pytest
from tests.test_money_out_browser import register_browser, browser_site
from tests.test_row8_register_browser import _command
from tests.test_service_sales_browser import _fill


@pytest.mark.timeout(180)
@pytest.mark.parametrize('width',[1280,390])
def test_loaded_user_permissions_do_not_follow_an_editable_target(register_browser,width):
    env=register_browser;b=env.browser;site=env.site
    b.viewport(width,900)
    def run(name,raw):
        result=b.evaluate("fetch('/commands/"+name+"',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:JSON.stringify("+json.dumps(raw)+")}).then(async r=>({status:r.status,body:await r.json()}))",await_promise=True)
        assert result['status']==200,result
        return result['body']
    a=run('user.add',dict(username='alpha',password='alpha fixture',company=site.company_id))
    z=run('user.add',dict(username='beta',password='beta fixture',company=site.company_id))
    state=run('permission.show',{})
    run('permission.activate',dict(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256']))
    run('membership.grant',dict(user=a['user_id'],company=site.company_id,expected_version=1,grants=['ledger.read']))
    run('membership.grant',dict(user=z['user_id'],company=site.company_id,expected_version=1,grants=['customer-work']))
    start=time.monotonic()
    b.navigate(f'{site.base_url}/c/{site.company_id}/users?user={a["user_id"]}')
    b.wait_for('!!document.querySelector("form[aria-label=\\"Company user permissions\\"]")')
    print('Users initial page seconds',width,time.monotonic()-start)
    edit='document.querySelector("form[aria-label=\\"Company user permissions\\"]")'
    assert b.evaluate(edit+'.elements.user.readOnly')
    assert b.evaluate(edit+'.elements.expected_version.value')=='2'
    assert json.loads(b.evaluate(edit+'.elements.other_grants.value'))==['ledger.read']
    # Explicit Load navigates; equal versions cannot carry Alpha's hidden grants into Beta.
    b.evaluate('document.querySelector("form[aria-label=\\"Load company user\\"]").elements.user.value='+json.dumps(z['user_id']))
    b.evaluate('document.querySelector("form[aria-label=\\"Load company user\\"]").requestSubmit()')
    b.wait_for(edit+'.elements.user.value=='+json.dumps(z['user_id']))
    assert b.evaluate(edit+'.elements.expected_version.value')=='2'
    assert json.loads(b.evaluate(edit+'.elements.other_grants.value'))==['customer-work']
    b.evaluate(edit+'.elements.check_delete.click()')
    b.evaluate(edit+'.elements.allow_post.click()')
    b.evaluate('window.oldPermissions='+edit+';true')
    b.evaluate(edit+'.querySelector("button[value=preview]").click()')
    b.wait_for('!window.oldPermissions.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    rows=run('membership.list',dict(company=site.company_id))['items']
    assert next(x for x in rows if x['user_id']==z['user_id'])['version']==2
    b.evaluate('window.oldPermissions='+edit+';true')
    b.evaluate(edit+'.querySelector("button[value=save]").click()')
    b.wait_for('!window.oldPermissions.isConnected')
    assert not b.evaluate('document.querySelector(".error")?.textContent')
    rows=run('membership.list',dict(company=site.company_id))['items']
    alpha=next(x for x in rows if x['user_id']==a['user_id'])
    beta=next(x for x in rows if x['user_id']==z['user_id'])
    assert alpha['version']==2 and alpha['grants']==['ledger.read'] and alpha['denies']==[]
    assert beta['version']==3 and set(beta['grants'])=={'customer-work','transaction.check.delete'} and beta['denies']==['ledger.post']
    # Unknown actions are refusals, not aliases for Save.
    b.evaluate('window.oldPermissions='+edit+';true')
    b.evaluate('(()=>{const f='+edit+';const x=document.createElement("button");x.name="action";x.value="unexpected";f.append(x);x.click()})()')
    b.wait_for('!window.oldPermissions.isConnected')
    assert 'E_VALIDATION' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate(edit+'.elements.check_delete.checked')
    assert run('membership.list',dict(company=site.company_id))['items']==rows
    # A concurrent editor advances the observed version; the stale GUI retains
    # the attempted card grant and reason while the database remains untouched.
    run('membership.grant',dict(user=z['user_id'],company=site.company_id,expected_version=3,
        grants=['customer-work','transaction.check.delete','ledger.read'],denies=['ledger.post']))
    current_rows=run('membership.list',dict(company=site.company_id))['items']
    b.evaluate(edit+'.elements.card_delete.click()')
    b.evaluate(edit+'.elements.reason.value="Retain this stale attempt"')
    b.evaluate('window.oldPermissions='+edit+';true')
    b.evaluate(edit+'.querySelector("button[value=save]").click()')
    b.wait_for('!window.oldPermissions.isConnected')
    assert 'E_VERSION_CONFLICT' in b.evaluate('document.querySelector(".error").textContent')
    assert b.evaluate(edit+'.elements.card_delete.checked')
    assert b.evaluate(edit+'.elements.reason.value')=='Retain this stale attempt'
    assert b.evaluate(edit+'.elements.expected_version.value')=='3'
    assert run('membership.list',dict(company=site.company_id))['items']==current_rows
    folder=Path('notes/permission-screenshots');folder.mkdir(parents=True,exist_ok=True)
    b.evaluate(edit+'.scrollIntoView()')
    (folder/f'users-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{'captureBeyondViewport':True,'fromSurface':True})['data']))
