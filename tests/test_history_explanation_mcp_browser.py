"""Bound-agent MCP posting and retry, followed by human audit readback."""
import json
from types import SimpleNamespace
import anyio
import pytest
from bookflow.core import clock
from bookflow.core.config import Config,os_login
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.adapters.http.app import create_app
from bookflow.hub import schema as hub_schema
from tests.conftest import make_actor
from tests.test_audit_projection_activity import world
from tests.test_row7_credentials import writer
from tests.test_row3_host import live as live_server
from tests.test_history_cli_mcp import environment,BINARY
from tests.test_row5_browser_acceptance import CHROME,_Cdp
from tests.test_service_sales_browser import _contained


@pytest.fixture(scope='module')
def explanation_site(world,tmp_path_factory):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters,stdio_client
    client=world['client'];company='Demo Plumbing Co'
    client.run('upgrade',{})
    cid=client.company.show(company=company)['company_id']
    principal=Config.load(world['root']/'config.toml').user_table(os_login())['user_id']
    agent=make_actor(world['root'],'explanation-agent',kind='agent',owner_user_id=principal,company_role=(cid,'owner'))
    with writer(world['root']) as db:
        db.conn.execute(hub_schema.agent_authority.insert().values(agent_user_id=agent,epoch=1))
        db.conn.execute(hub_schema.agent_principals.insert().values(agent_user_id=agent,
            principal_user_id=principal,assigned_by=principal,assigned_at=clock.now_iso()))
    token=client.token.issue(user=agent,principal=principal,label='explanation-MCP')['secret']
    human=client.token.issue(label='explanation-browser')['secret']
    bank=client.account.create(name='Explanation bank',type='bank',company=company)
    income=client.account.create(name='Explanation income',type='income',company=company)
    directive=client.directive.add(text='Post <balanced> plumbing receipts',company=company)['directive']
    host=Host(world['root'],version=client_version());host.start()
    server=live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=create_app(host,secure_cookies=False))))
    site=next(server);tmp=tmp_path_factory.mktemp('explanation-mcp')
    async def journey():
        env=environment(tmp/'absent');env.update(BOOKFLOW_TOKEN=token,BOOKFLOW_COMPANY=cid)
        params=StdioServerParameters(command=BINARY,args=['mcp','--url',site],env=env,cwd=str(tmp))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                async def run(command,data,**context):
                    reply=await session.call_tool('bookflow_run',{'command':command,'input':data,**context})
                    assert not reply.is_error,reply.structured_content
                    return reply.structured_content
                payload={'date':'2026-01-12','lines':[
                    {'account':bank['id'],'side':'debit','amount':'12.34'},
                    {'account':income['id'],'side':'credit','amount':'12.34'}]}
                context={'directive':directive['code'],'reason':'Post <today> correctly','idempotency_key':'explanation-journal'}
                journal=await run('journal post',payload,**context)
                replay=await run('journal post',payload,**context)
                assert replay=={**journal,'idempotent_replay':True}
                page=await run('audit list',{'record_type':'transaction','record_id':journal['id']})
                assert page['count']==1
                event=page['items'][0]
                assert event['actor_id']==agent and event['principal_id']==principal and event['interface']=='mcp'
                assert event['explanation']=={'reason':context['reason'],'directive_status':'available',
                    'directive':{key:directive[key] for key in ('id','code','text')}}
                return event
    try:
        event=anyio.run(journey)
        yield site,cid,human,event
    finally:
        server.close();host.stop()


@pytest.mark.timeout(180)
@pytest.mark.skipif(not CHROME.is_file(),reason='Chrome unavailable')
@pytest.mark.parametrize('width',(1280,390))
def test_mcp_explanation_visible_to_human_on_desktop_and_phone(explanation_site,tmp_path,width):
    site,cid,token,event=explanation_site
    browser=_Cdp(tmp_path/'chrome')
    try:
        browser.viewport(width,850)
        browser.call('Network.enable')
        browser.call('Network.setExtraHTTPHeaders',{'headers':{'Authorization':'Bearer '+token}})
        browser.navigate(site+'/c/'+cid+'/audit/'+event['id'])
        browser.wait_for("!!document.querySelector('.history-explanation')")
        text=browser.evaluate("document.querySelector('.history-explanation').textContent")
        assert 'Post <today> correctly' in text and 'Post <balanced> plumbing receipts' in text
        assert browser.evaluate("document.querySelectorAll('.history-explanation script').length")==0
        assert browser.evaluate("document.querySelector('.history-explanation a').getAttribute('href')")==(
            '/c/'+cid+'/directive/'+event['explanation']['directive']['id'])
        _contained(browser,width)
    finally:browser.close()
