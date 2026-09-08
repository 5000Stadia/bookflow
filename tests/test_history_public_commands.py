"""Registered command execution uses opaque projected history on both owners."""
from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.dispatch import run
from bookflow.adapters.http.execution import run_hosted
from tests.test_history_cursors import world
from tests.test_audit_projection_publication import credential
from tests.test_permission_runtime import path as root_fixture


def context():
    return Context.new('http','Public history').model_copy(update={'request_id':'REQUEST'})


def test_registered_hosted_list_and_show_have_projected_contract(world):
    registry.load_all()
    host,path,first,second = world
    cmd = registry.get('hub audit list')
    page = run_hosted(host,cmd,{'limit':1},context(),credential(host,'R'),None,'none',False)
    assert page['projection_version'] == 2
    assert page['items'][0]['id'] == second
    assert 'seq' not in page['items'][0]
    assert isinstance(page['next_before'],str)
    page.check()
    more = run_hosted(host,cmd,{'limit':1,'before':page['next_before']},context(),credential(host,'R'),None,'none',False)
    assert more['items'][0]['id'] == first
    shown = run_hosted(host,registry.get('hub audit show'),{'event':second},context(),credential(host,'R'),None,'none',False)
    assert shown['projection_version'] == 2 and shown['entries']
    assert 'seq' not in shown
    shown.check()


def test_registered_offline_dispatch_uses_reader_before_root_lock(tmp_path):
    registry.load_all()
    path = root_fixture.__wrapped__(tmp_path)
    page = run(registry.get('hub audit list'),{},context(),data_root=str(path.parent))
    assert page == {'projection_version':2,'items':[],'count':0,'next_before':None}
    tail = run(registry.get('hub audit tail'),{},context(),data_root=str(path.parent))
    assert tail['projection_version'] == 2
    assert isinstance(tail['next_after'],str)
    assert 'high_water' not in tail


def test_bound_os_agent_bookmarks_match_hosted_and_offline(tmp_path):
    from bookflow.core.config import Config, os_login
    from bookflow.core.host import Host
    from bookflow.core.context import client_version
    from bookflow.core.publication import OSBinding
    from bookflow.core import history_offline
    from bookflow.hub.audit_projection import HistorySelection
    from bookflow.adapters.http.execution import run_history
    registry.load_all()
    path = root_fixture.__wrapped__(tmp_path)
    config = Config.load(path.parent/'config.toml')
    config.set_user(os_login(),'G');config.save()
    ctx = context().model_copy(update={'on_behalf_of':'P'})
    selection = HistorySelection(mode='tail',limit=100)
    local = history_offline.read(path.parent,selection,ctx)
    public_local = run(registry.get('hub audit tail'),{},ctx,data_root=str(path.parent))
    assert public_local == local
    host = Host(path.parent,version=client_version());host.start()
    try:
        cred = OSBinding.capture(host,os_login(),principal='P')
        hosted = run_history(host,selection,ctx,cred,wire=True)
        assert hosted == local
        following = run_hosted(host,registry.get('hub audit tail'),{'after':local['next_after']},ctx,cred,None,'none',False)
        following.check()
        assert following == local
    finally:
        host.stop()
    resumed = history_offline.read(path.parent,selection,ctx,bookmark=local['next_after'])
    assert resumed == following


def test_registered_sse_route_issues_opaque_checkpoint(world):
    import asyncio
    from bookflow.adapters.http.app import create_app
    from tests.test_history_stream import fields
    host,*_ = world
    app = create_app(host,secure_cookies=False)
    delivered=[]
    async def exercise():
        disconnected=asyncio.Event()
        initial=True
        async def receive():
            nonlocal initial
            if initial:
                initial=False
                return {'type':'http.request','body':b'','more_body':False}
            await disconnected.wait()
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.start':
                assert message['status']==200
            if message['type']=='http.response.body' and message.get('body'):
                delivered.append(message['body'].decode())
                disconnected.set()
        await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
                   'scheme':'http','path':'/hub-events','raw_path':b'/hub-events','query_string':b'limit=1',
                   'root_path':'','headers':[(b'authorization',b'Bearer secret-R')],
                   'server':('testserver',80),'client':('127.0.0.1',1)},receive,send)
    asyncio.run(exercise())
    assert delivered
    frame=fields(delivered[0])
    assert frame['event']=='checkpoint'
    assert '.' in frame['id'] and not frame['id'].isdigit()
    assert frame['data']=='{"projection_version":2}'
    assert host._readers_attached==0
