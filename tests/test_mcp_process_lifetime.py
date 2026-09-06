"""Actual stdio EOF/cancellation and host restart preserve one execution identity."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest

from bookflow.adapters.mcp.runtime import Runtime
from bookflow.commands.host_cmds import start_serving
from bookflow.core import registry
from bookflow.core.context import client_version
from tests.test_row3_host import hosted, live, Hosted


class Wire:
    """Literal legacy MCP wire allows closing stdin without killing the process."""
    def __init__(self, url, fixture, directory, protocol="legacy"):
        self.protocol=protocol
        binary=os.environ.get('BOOKFLOW_MCP_TEST_BINARY',str(Path(sys.executable).with_name('bookflow')))
        self.process=subprocess.Popen([binary,'mcp','--url',url,'--client-name','process-lifetime'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=directory,
            env={**os.environ,'BOOKFLOW_TOKEN':fixture.secret,'BOOKFLOW_COMPANY':fixture.company_id,
                 'BOOKFLOW_DATA_ROOT':str(directory/'absent')})
        self.messages=queue.Queue()
        self.observed=[]
        self.stderr=[]
        def output():
            for line in self.process.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: self.messages.put({'invalid_stdout':True})
            self.messages.put(None)
        def errors():
            self.stderr.extend(self.process.stderr.readlines())
        self.reader=threading.Thread(target=output,daemon=True)
        self.error_reader=threading.Thread(target=errors,daemon=True)
        self.reader.start();self.error_reader.start()
        try:
            if protocol == 'modern':
                self.send('server/discover',{},1)
                assert 'result' in self.reply(1)
            else:
                self.send('initialize',{'protocolVersion':'2025-11-25','capabilities':{},
                    'clientInfo':{'name':'owned-lifetime-client','version':'1'}},1)
                assert 'result' in self.reply(1)
                self.send('notifications/initialized',{})
        except BaseException:
            self.close()
            raise
    def send(self,method,params,id=None):
        if self.protocol == 'modern' and id is not None:
            from mcp_types import PROTOCOL_VERSION_META_KEY, CLIENT_INFO_META_KEY, CLIENT_CAPABILITIES_META_KEY
            params={**params,'_meta':{PROTOCOL_VERSION_META_KEY:'2026-07-28',
                CLIENT_INFO_META_KEY:{'name':'owned-lifetime-client','version':'1'},CLIENT_CAPABILITIES_META_KEY:{}}}
        message={'jsonrpc':'2.0','method':method,'params':params}
        if id is not None:message['id']=id
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()
    def call(self,id,arguments):
        self.send('tools/call',{'name':'bookflow_run','arguments':arguments},id)
    def reply(self,id,timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=self.messages.get(timeout=max(.01,deadline-time.monotonic()))
            assert value is not None,'stdio closed before expected response'
            assert 'invalid_stdout' not in value,'non-JSON stdout'
            self.observed.append(value)
            if value.get('id')==id:return value
        raise AssertionError('MCP reply deadline')
    def eof(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=8)==0,'launcher did not exit cleanly on EOF'
        self.reader.join(timeout=2);self.error_reader.join(timeout=2)
        while not self.messages.empty():
            value=self.messages.get_nowait()
            if value is not None:self.observed.append(value)
        assert not self.reader.is_alive() and not self.error_reader.is_alive()
        assert all('invalid_stdout' not in message for message in self.observed)
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)
        self.reader.join(timeout=2);self.error_reader.join(timeout=2)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
            if stream and not stream.closed:stream.close()


def wait_until(predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.02)
    assert predicate(),'owned resource did not finish by deadline'


def document(reply):
    assert 'result' in reply,reply
    result=reply['result']
    assert 'structuredContent' in result,result
    return result['structuredContent'],result


def events(fixture):
    return [e for e in fixture.ok('audit.list',{'command':'company update','limit':200},company=fixture.company_id)['items']
            if e['client_name']=='process-lifetime']


@pytest.mark.timeout(150)
@pytest.mark.parametrize("protocol",["legacy","modern"])
def test_actual_stdio_eof_after_commit_and_real_host_restart_cannot_reexecute(hosted,tmp_path,monkeypatch,protocol):
    from bookflow.adapters.mcp.delivery import Delivery
    runtime=Runtime.for_host(hosted.handle.host)
    reached,release=threading.Event(),threading.Event()
    original_frames=Delivery._frames
    first=[True]
    def delayed(self,*args,**kwargs):
        for frame in original_frames(self,*args,**kwargs):
            yield frame
            if first[0]:
                first[0]=False
                reached.set()
                assert release.wait(20),'owned receipt barrier not released'
    # Install only after prepare_only, so its authenticated observation is live.
    server=live.__wrapped__(hosted)
    url=next(server)
    wire=None
    try:
        wire=Wire(url,hosted,tmp_path,protocol)
        wire.call(2,{'command':'company update','input':{'fax':'Once after EOF'},'reason':'Owned EOF witness',
                     'transport':{'prepare_only':True}})
        prepared,_=document(wire.reply(2))
        reference=prepared['operation_ref']
        monkeypatch.setattr(Delivery,'_frames',delayed)
        wire.call(3,{'operation_ref':reference,'action':'execute'})
        try:
            assert reached.wait(10)
            assert hosted.info()['info']['fax']=='Once after EOF'
            assert len(events(hosted))==1
            wire.eof()
            assert not any(m.get('id')==3 for m in wire.observed), [m for m in wire.observed if m.get('id')==3]
            assert hosted.secret not in ''.join(wire.stderr)
        finally:
            release.set()
        wait_until(lambda:not runtime.intents.active and not hosted.handle.host._transfers)
        assert hosted.handle.host._readers_attached==0
        monkeypatch.setattr(Delivery,'_frames',original_frames)
        # Another launcher observes the same old intent without a new execution.
        with ExitStack() as stack:
            second=Wire(url,hosted,tmp_path,protocol);stack.callback(second.close)
            second.call(2,{'input_ref':reference,'action':'execute'})
            observed,envelope=document(second.reply(2))
            assert envelope['_meta']['bookflow_transport']['response_kind']=='recovery_observation'
            assert observed['outcome']=='unknown' and observed['operation_ref']==reference
            second.eof()
        assert len(events(hosted))==1
    finally:
        release.set()
        if wire:wire.close()
        server.close()
    hosted.handle.stop()
    assert runtime.intents.closed and not runtime.intents.active
    restarted=start_serving(hosted.root,client_version(),bind='127.0.0.1:8765',secure_cookies=False)
    fixture=Hosted(restarted,hosted.root,hosted.login,hosted.company_id,
        {'token_id':hosted.token,'secret':hosted.secret},hosted.outsider_id,hosted.company_list)
    server=live.__wrapped__(fixture)
    try:
        url=next(server)
        command=registry.get('company update')
        def forbidden(*args,**kwargs):pytest.fail('Lost transport reference reexecuted committed write')
        monkeypatch.setattr(command,'plan',forbidden)
        with ExitStack() as stack:
            third=Wire(url,fixture,tmp_path,protocol);stack.callback(third.close)
            for index,(alias,action) in enumerate((('operation_ref','status'),('input_ref','status'),
                                                 ('operation_ref','execute'),('input_ref','execute')),2):
                third.call(index,{alias:reference,'action':action})
                observed,envelope=document(third.reply(index))
                assert envelope['isError'] and observed['code']=='E_IO'
                assert observed['details']['outcome']=='unknown'
                assert observed['details']['operation_ref']==reference
            third.eof()
        assert fixture.info()['info']['fax']=='Once after EOF' and len(events(fixture))==1
        assert not Runtime.for_host(restarted.host).intents.active
    finally:
        server.close();restarted.stop()
    assert not (tmp_path/'absent').exists()


@pytest.mark.timeout(120)
@pytest.mark.parametrize("protocol",["legacy","modern"])
def test_actual_mcp_cancellation_while_writer_queued_keeps_zero_or_one_effect(hosted,live,tmp_path,monkeypatch,protocol):
    from concurrent.futures import ThreadPoolExecutor
    from bookflow.core.config import Config
    host=hosted.handle.host
    runtime=Runtime.for_host(host)
    entered,release,queued=threading.Event(),threading.Event(),threading.Event()
    user_id=Config.load(hosted.root/'config.toml').user_table(hosted.login)['user_id']
    original_queue=runtime.intents.queue
    def observe_queue(intent):
        result=original_queue(intent)
        queued.set()
        return result
    monkeypatch.setattr(runtime.intents,'queue',observe_queue)
    before=hosted.info()
    def hold(session):
        entered.set()
        assert release.wait(20),'owned writer barrier not released'
    with ExitStack() as stack:
        wire=Wire(live,hosted,tmp_path,protocol);stack.callback(wire.close)
        wire.call(2,{'command':'company update','input':{'fax':'Cancelled queued intent'},
                     'reason':'Owned cancellation witness','transport':{'prepare_only':True}})
        prepared,_=document(wire.reply(2));reference=prepared['operation_ref']
        with ThreadPoolExecutor(1) as pool:
            holding=pool.submit(host.run_write,user_id,hosted.login,hold)
            try:
                assert entered.wait(5)
                wire.call(3,{'operation_ref':reference,'action':'execute'})
                assert queued.wait(5)
                wire.send('notifications/cancelled',{'requestId':3,'reason':'Owned test cancellation'})
                wire.send('tools/list',{},4)
                assert 'result' in wire.reply(4)
                assert not any(m.get('id')==3 and 'result' in m for m in wire.observed)
            finally:
                release.set()
            holding.result(timeout=5)
        wait_until(lambda:not runtime.intents.active and not host._transfers)
        after=hosted.info()
        effects=events(hosted)
        assert len(effects) in (0,1)
        assert after['info']['fax']==('Cancelled queued intent' if effects else before['info']['fax'])
        assert host._readers_attached==0
        # A cancelled request never becomes a later successful tool result.
        wire.send('tools/list',{},5)
        wire.reply(5)
        assert not any(m.get('id')==3 and 'result' in m for m in wire.observed)
        command=registry.get('company update')
        def forbidden(*args,**kwargs):pytest.fail('Cancelled intent was reexecuted by recovery')
        monkeypatch.setattr(command,'plan',forbidden)
        for id,alias in enumerate(('operation_ref','input_ref'),6):
            wire.call(id,{alias:reference,'action':'execute'})
            observed,envelope=document(wire.reply(id))
            assert envelope['_meta']['bookflow_transport']['response_kind'] in {'recovery_observation','verified_command_completion'}
            if envelope['_meta']['bookflow_transport']['response_kind']=='recovery_observation':
                assert observed['operation_ref']==reference and observed['outcome']=='unknown'
            else:
                assert len(effects)==1 and observed['info']['fax']=='Cancelled queued intent'
        assert events(hosted)==effects
        wire.eof()
        assert hosted.secret not in ''.join(wire.stderr)
    assert not (tmp_path/'absent').exists()
