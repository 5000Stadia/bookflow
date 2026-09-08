"""Real subprocess CLI and SDK stdio journeys on small synthetic hub roots."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import anyio
import pytest

from bookflow.core.config import Config, os_login
from bookflow.core.context import client_version
from bookflow.core.host import Host
from bookflow.adapters.http.app import create_app
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.test_permission_runtime import path as root_fixture
from tests.test_audit_projection_publication import apply
from tests.test_row3_host import live as live_fixture

SOURCE = Path(__file__).resolve().parents[1]
BINARY = str(Path(sys.executable).with_name('bookflow'))


def environment(root):
    # No ambient endpoint, company, or real credential may enter these journeys.
    env = {k:v for k,v in os.environ.items() if not k.startswith('BOOKFLOW_')}
    return env | {'BOOKFLOW_DATA_ROOT':str(root), 'PYTHONPATH':os.pathsep.join([str(SOURCE/'src'),os.environ.get('PYTHONPATH','')]), 'NO_COLOR':'1'}


def create_world(root):
    root.mkdir()
    path = root_fixture.__wrapped__(root)
    config = Config.load(root/'config.toml'); config.set_user(os_login(),'A'); config.save()
    host = Host(root,version=client_version()); host.start()
    try:
        first = apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(1),'readonly'),'A')
        second = apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(2),'standard'),'A')
    finally:
        host.stop()
    return path,first,second


def safe(doc):
    if isinstance(doc,dict):
        assert not {'seq','high_water','reason','source_ref','request_id','token_hash'} & doc.keys()
        for value in doc.values(): safe(value)
    elif isinstance(doc,list):
        for value in doc: safe(value)


def invalid(doc):
    assert doc['code']=='E_VALIDATION' and doc['details']=={'reason':'invalid_cursor'}
    assert 'Restart' in doc['message'] or 'restart' in doc['message']


def test_cli_help_paged_show_tail_and_numeric_refusal(tmp_path):
    path,first,second = create_world(tmp_path/'cli-root')
    observations=[]
    def call(*args,error=False,help=False):
        proc=subprocess.run([BINARY,*args,*([] if help else ['--json'])],cwd=tmp_path,
            env=environment(path.parent),capture_output=True,text=True,timeout=30)
        observations.append(dict(args=args,code=proc.returncode,stdout=proc.stdout,stderr=proc.stderr))
        assert proc.returncode==(1 if error else 0), observations[-1]
        return proc.stdout if help else json.loads(proc.stderr.strip().splitlines()[-1] if error else proc.stdout)
    try:
        help_text=call('hub','audit','list','--help',help=True)
        assert '--before' in help_text and 'Opaque' in help_text and '--version' not in help_text
        page=call('hub','audit','list','--limit','1')
        assert page['projection_version']==2 and [x['id'] for x in page['items']]==[second]
        assert isinstance(page['next_before'],str) and not page['next_before'].isdigit()
        more=call('hub','audit','list','--limit','1','--before',page['next_before'])
        assert [x['id'] for x in more['items']]==[first] and more['next_before'] is None
        shown=call('hub','audit','show',second)
        assert shown['id']==second and shown['projection_version']==2
        membership=[e for e in shown['entries'] if e['identity']['kind']=='membership']
        assert len(membership)==1 and membership[0]['before']['role']=='readonly' and membership[0]['after']['role']=='standard'
        tail=call('hub','audit','tail','--limit','1')
        assert tail['projection_version']==2 and tail['items']==[] and isinstance(tail['next_after'],str)
        assert call('hub','audit','tail','--limit','1','--after',tail['next_after'])==tail
        invalid(call('hub','audit','list','--before','3',error=True))
        invalid(call('hub','audit','tail','--after','3',error=True))
        for doc in (page,more,shown,tail):safe(doc)
    finally:
        (tmp_path/'cli-receipt.json').write_text(json.dumps(observations,indent=2))


@pytest.fixture(scope="module")
def hosted(tmp_path_factory):
    path,first,second=create_world(tmp_path_factory.mktemp("sdk-world")/'mcp-root')
    host=Host(path.parent,version=client_version());host.start()
    try:
        yield SimpleNamespace(handle=SimpleNamespace(app=create_app(host,secure_cookies=False)),host=host,
                              path=path,first=first,second=second)
        assert host._readers_attached==0
    finally:host.stop()


@pytest.fixture(scope="module")
def live(hosted):
    yield from live_fixture.__wrapped__(hosted)


# Each phase includes fresh setup and multiple real requests; production
# per-response deadlines remain unchanged and are exercised by the server.
@pytest.mark.timeout(180)
@pytest.mark.parametrize('phase',('discovery','list','tail','invalid','agent','recovery'))
def test_sdk_discover_help_and_execute_history(hosted,live,tmp_path,phase):
    identity='agent' if phase=='agent' else 'human'
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    observations=[]
    async def journey():
        env=environment(tmp_path/'absent-client-root')
        env['BOOKFLOW_TOKEN']='secret-A' if identity=='human' else 'secret-GP-live'
        params=StdioServerParameters(command=BINARY,args=['mcp','--url',live],env=env,cwd=str(tmp_path))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                assert {t.name for t in (await session.list_tools()).tools}=={'bookflow_list_commands','bookflow_help','bookflow_run'}
                async def tool(name,args,error=False):
                    started=time.monotonic()
                    observations.append(dict(tool=name,args=args,status='started'))
                    reply=await session.call_tool(name,args)
                    observations[-1].update(status='completed',elapsed=time.monotonic()-started,error=reply.is_error,document=reply.structured_content,metadata=reply.meta)
                    assert bool(reply.is_error)==error,reply
                    return reply.structured_content
                if phase=='discovery':
                    commands=await tool('bookflow_list_commands',{'prefix':'hub audit','limit':20})
                    assert [x['name'] for x in commands['commands']]==['hub audit list','hub audit show','hub audit tail']
                    for mode,bookmark in [('list','before'),('show',None),('tail','after')]:
                        help_doc=await tool('bookflow_help',{'command':'hub audit '+mode,'view':'full'})
                        inputs=help_doc['input_schema']['properties'];outputs=help_doc['output_schema']['properties']
                        assert 'version' not in inputs and outputs['projection_version']['const']==2
                        if bookmark:
                            assert {x['type'] for x in inputs[bookmark]['anyOf']}=={'string','null'}
                            assert 'Opaque' in inputs[bookmark]['description']
                    return
                async def run(mode,inp,error=False):
                    doc=await tool('bookflow_run',{'command':'hub audit '+mode,'input':inp},error)
                    if not error:safe(doc)
                    return doc
                if phase=='list':
                    page=await run('list',{'limit':1})
                    assert [x['id'] for x in page['items']]==[hosted.second]
                    more=await run('list',{'limit':1,'before':page['next_before']})
                    assert [x['id'] for x in more['items']]==[hosted.first] and more['next_before'] is None
                    shown=await run('show',{'event':hosted.second})
                    assert shown['id']==hosted.second and shown['projection_version']==2 and shown['entries']
                    assert next(x for x in shown['entries'] if x['identity']['kind']=='membership')['after']['role']=='standard'
                elif phase=='tail':
                    tail=await run('tail',{'limit':1})
                    assert tail['items']==[] and isinstance(tail['next_after'],str)
                    third=apply(hosted.host,hosted.path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(3),'readonly'),'A')
                    resumed=await run('tail',{'limit':1,'after':tail['next_after']})
                    assert [x['id'] for x in resumed['items']]==[third]
                    following=await run('tail',{'limit':1,'after':resumed['items'][0]['resume_after']})
                    assert following['items']==[] and following['next_after']==resumed['next_after']
                elif phase=='recovery':
                    page=await run('list',{'limit':1})
                    reference=observations[-1]['metadata']['bookflow_transport']['operation_ref']
                    recovered=await tool('bookflow_run',{'operation_ref':reference,'action':'execute'})
                    assert recovered==page
                    # Reusing a stored result must still check today's authority.
                    apply(hosted.host,hosted.path,admin.PutMembership('A',ScopeKey('organization','O'),admin.Version(1),'readonly'),'W')
                    rejected=await tool('bookflow_run',{'operation_ref':reference,'action':'execute'},True)
                    assert rejected['code']=='E_PERMISSION'
                    assert 'items' not in rejected and hosted.second not in json.dumps(rejected)
                elif phase=='agent':
                    page=await run('list',{'limit':1})
                    assert page=={'projection_version':2,'items':[],'count':0,'next_before':None}
                    denied=await run('show',{'event':hosted.second},True)
                    assert denied['code']=='E_EVENT_NOT_FOUND' and hosted.second not in json.dumps(denied)
                elif phase=='invalid':
                    for mode,key in [('list','before'),('tail','after')]:
                        for value in (3,'3'):invalid(await run(mode,{key:value},True))
        assert not (tmp_path/'absent-client-root').exists()
    try:anyio.run(journey)
    finally:
        (tmp_path/'mcp-receipt.json').write_text(json.dumps(observations,indent=2))
        assert not (tmp_path/'absent-client-root').exists()
