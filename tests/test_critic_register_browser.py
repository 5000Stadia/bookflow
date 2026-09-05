"""Independent positive regressions for critic F1/F2, using actual Chrome."""
import base64
import json
from pathlib import Path
import pytest
from tests.test_row8_register_browser import (browser_site, register_browser, _simple_draft,
    _tab_to, _key, _type, _command, _record_keyboard)

REASON='修理 receipt + 100% / %E4 😀'
SOURCE='工事/é😀 + %25'

def draft(env):
    b=env.browser
    _simple_draft(env,'12.34')
    _tab_to(b,'[name=reason]'); _type(b,REASON)
    _tab_to(b,'[name=source_ref]'); _type(b,SOURCE)

def audit_receipt(env):
    b=env.browser
    jid=b.evaluate("document.querySelector('#register-receipt a').href").rsplit('/',1)[1]
    journal=_command(b,env.site,'journal.show',{'journal':jid})
    event=_command(b,env.site,'audit.show',{'event':journal['revision']['audit_event_id']})
    assert event['reason']==REASON and event['source_ref']==SOURCE
    assert event['interface']=='http' and event['client_name']=='bookflow-workbench'
    assert _command(b,env.site,'account.show',{'account':env.bank['id']})['balance']['minor_units']==-1234
    assert b.evaluate("sessionStorage.getItem('bookflow-register-pending-v1')") is None
    assert not b.evaluate("document.querySelector('#register-fields').disabled")
    return journal

@pytest.mark.parametrize('width,height',[(1280,900),(390,844)])
def test_f1_unicode_posts_exact_attribution(register_browser,tmp_path,width,height):
    env=register_browser; b=env.browser
    b.viewport(width,height)
    draft(env); _record_keyboard(b)
    audit_receipt(env)
    assert b.evaluate('document.documentElement.scrollWidth') <= width+1
    picture=b.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    (tmp_path/f'fixed-unicode-{width}.png').write_bytes(base64.b64decode(picture['data']))

def test_f1_unicode_uncertain_reload_replays_once(register_browser):
    env=register_browser; b=env.browser
    draft(env)
    b.evaluate("""(() => {const original=window.fetch; let once=true;
      window.fetch=async (url,options)=>{if(String(url).endsWith('/commands/register.post') && once){
        once=false; const r=await original(url,options); await r.clone().text();
        throw new TypeError('independent postcommit response loss');
      } return original(url,options);};})()""")
    _tab_to(b,'#register-record'); _key(b,'Enter')
    b.wait_for("document.querySelector('#register-error').textContent.includes('TRANSPORT') && !document.querySelector('#register-retry').disabled")
    pending=b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1'))")
    assert pending['context']['X-Bookflow-Reason']==REASON
    assert pending['context']['X-Bookflow-Source-Ref']==SOURCE
    assert pending['possibly_sent'] is True
    assert _command(b,env.site,'account.show',{'account':env.bank['id']})['balance']['minor_units']==-1234
    b.navigate(env.url)
    assert b.evaluate("JSON.parse(sessionStorage.getItem('bookflow-register-pending-v1')).key")==pending['key']
    _tab_to(b,'#register-retry'); _key(b,'Enter')
    b.wait_for("!document.querySelector('#register-receipt').hidden && !document.querySelector('#register-fields').disabled")
    audit_receipt(env)

@pytest.mark.parametrize('memo',[None,''])
def test_f2_split_inspection_is_exact_noop(register_browser,memo):
    env=register_browser; b=env.browser
    today=b.evaluate("document.querySelector('#register-date').value")
    first=_command(b,env.site,'register.post',{'account':env.bank['id'],'category':env.expense['id'],
        'date':today,'direction':'decrease','amount':'12.34','memo':memo})
    audit_before=_command(b,env.site,'audit.tail',{})['high_water']
    b.navigate(env.url+'?edit='+first['id'])
    b.wait_for("document.querySelector('#register-entry-title').textContent.includes('version 1')")
    _tab_to(b,'#register-splits-open'); _key(b,'Enter')
    _tab_to(b,'#register-split-close'); _key(b,'Enter')
    _record_keyboard(b)
    after=_command(b,env.site,'journal.show',{'journal':first['id']})
    assert after['version']==1 and after['revision']==first['revision']
    assert len(_command(b,env.site,'journal.history',{'journal':first['id']})['items'])==1
    assert _command(b,env.site,'audit.tail',{})['high_water']==audit_before
