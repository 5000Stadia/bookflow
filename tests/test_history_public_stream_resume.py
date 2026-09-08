"""Actual registered SSE disconnect and authority changes between body sends."""
import asyncio
import json
import pytest
from starlette.concurrency import run_in_threadpool
from bookflow.core import registry
from bookflow.core.context import Context, client_version
from bookflow.core.host import Host
from bookflow.adapters.http.app import create_app
from bookflow.adapters.http.execution import run_hosted
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.test_permission_runtime import path as root_fixture
from tests.test_audit_projection_publication import apply, credential
from tests.test_history_stream import fields


@pytest.fixture(scope='module')
def feed(tmp_path_factory):
    path=root_fixture.__wrapped__(tmp_path_factory.mktemp('public-stream-resume'))
    registry.load_all();host=Host(path.parent,version=client_version());host.start()
    try:
        ctx=Context.new('http','Stream setup').model_copy(update={'request_id':'REQUEST'})
        start=run_hosted(host,registry.get('hub audit tail'),{'limit':2},ctx,credential(host,'A'),None,'none',False)['next_after']
        first=apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(1),'readonly'),'A')
        second=apply(host,path,admin.PutMembership('R',ScopeKey('organization','O'),admin.Version(2),'standard'),'A')
        yield host,path,start,first,second
    finally:host.stop()


def request_frames(host,bookmark,*,after_first=None):
    app=create_app(host,secure_cookies=False)
    received=[]
    async def exercise():
        disconnected=asyncio.Event();initial=True
        async def receive():
            nonlocal initial
            if initial:
                initial=False
                return {'type':'http.request','body':b'','more_body':False}
            await disconnected.wait()
            return {'type':'http.disconnect'}
        async def send(message):
            if message['type']=='http.response.start':assert message['status']==200
            if message['type']=='http.response.body' and message.get('body'):
                received.append(message['body'].decode())
                if len(received)==1 and after_first is not None:
                    await run_in_threadpool(after_first)
                else:
                    disconnected.set()
                await asyncio.sleep(0)
        await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
            'scheme':'http','path':'/hub-events','raw_path':b'/hub-events','query_string':b'limit=2',
            'root_path':'','headers':[(b'authorization',b'Bearer secret-A'),(b'last-event-id',bookmark.encode())],
            'server':('testserver',80),'client':('127.0.0.1',1)},receive,send)
    aborted=False
    try:
        asyncio.run(exercise())
    except ConnectionAbortedError as exc:
        if after_first is None or str(exc) != 'Publication authority changed':
            raise
        aborted=True
    assert aborted == (after_first is not None)
    return [fields(frame) for frame in received if fields(frame).get('event')]


def test_registered_stream_resumes_after_first_processed_event(feed):
    host,path,start,first,second=feed
    partial=request_frames(host,start)
    assert len(partial)==1 and partial[0]['event']=='audit'
    assert json.loads(partial[0]['data'])['id']==first
    following=request_frames(host,partial[0]['id'])
    assert len(following)==1 and following[0]['event']=='audit'
    assert json.loads(following[0]['data'])['id']==second
    assert partial[0]['id'] != following[0]['id']
    assert host._readers_attached==0


def test_registered_stream_rechecks_authority_between_frames(feed):
    host,path,start,first,second=feed
    frames=request_frames(host,start,after_first=lambda:apply(host,path,
        admin.PutMembership('A',ScopeKey('organization','O'),admin.Version(1),'readonly'),'W'))
    audits=[json.loads(frame['data'])['id'] for frame in frames if frame['event']=='audit']
    assert audits==[first]
    assert all(second not in frame.get('data','') for frame in frames)
    assert host._readers_attached==0
