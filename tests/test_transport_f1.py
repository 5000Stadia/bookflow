"""Actual owned HTTP/local boundary witnesses for F1, without live activation."""
import asyncio
import socket
import threading
from types import SimpleNamespace

import h11
import pytest

from bookflow.core.publication_admission import Admission, AdmissionCancelled, ResponseRelease
from tests.test_http_transport_admission import connection, all_bytes
from tests.test_commit_hooks import owner_host


@pytest.mark.parametrize('race', ['drain', 'encoded'])
def test_headers_encode_once_revalidate_without_reexecuting(race):
    async def run():
        gate = Admission(); entered = asyncio.Event(); holder = {}; runs = []; checks = []; encoded = []
        from bookflow.adapters.http.publication import protect
        class Guard:
            credential = SimpleNamespace(token_id='owned')
            def check(self, **kwargs): checks.append('current')
        async def app(scope, receive, send):
            runs.append('one durable execution')
            protect(Guard())
            if race == 'drain':
                holder['protocol'].flow.pause_writing(); entered.set()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'captured-result'})
        transport, protocol, peer = await connection(app, gate); holder['protocol']=protocol
        original_handle = protocol.handle_events
        def handle():
            original_handle()
            if protocol.cycle is not None and not encoded:
                original = protocol.conn.send
                def send(event):
                    encoded.append(type(event))
                    return original(event)
                protocol.conn.send = send
        protocol.handle_events = handle
        real_write = gate.transport_write
        raced = []
        def write(frame, raw, data):
            if race == 'encoded' and not raced:
                raced.append(True)
                barrier=gate.close_for_commit();gate.finish_commit(barrier, committed=True)
            return real_write(frame, raw, data)
        gate.transport_write=write
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            if race == 'drain':
                await asyncio.wait_for(entered.wait(),2)
                while not protocol.flow._is_writable_event._waiters: await asyncio.sleep(0)
                barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True)
                protocol.flow.resume_writing()
            data=await all_bytes(peer)
            assert runs == ['one durable execution']
            assert len(checks) >= 3  # initial header, fresh header, body
            assert encoded.count(h11.Response)==1 and encoded.count(h11.EndOfMessage)==1
            assert data.count(b'200 OK')==1 and data.endswith(b'f\r\ncaptured-result\r\n0\r\n\r\n')
            assert protocol.cycle.response_complete
            assert protocol.cycle.release_state.accepted == len(data)
        finally:transport.close();peer.close()
    asyncio.run(run())


def test_interim_100_does_not_consume_final_response_retry():
    async def run():
        gate=Admission();closed=asyncio.Event();holder={}
        async def app(scope,receive,send):
            message=await receive();assert message['body']==b'x'
            holder['barrier']=gate.close_for_commit();closed.set()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'committed-once'})
        transport,protocol,peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'POST / HTTP/1.1\r\nHost: owned\r\nExpect: 100-continue\r\nContent-Length: 1\r\nConnection: close\r\n\r\n')
            interim=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(peer,65536),2)
            assert interim==b'HTTP/1.1 100 Continue\r\n\r\n'
            await asyncio.get_running_loop().sock_sendall(peer,b'x')
            await asyncio.wait_for(closed.wait(),2)
            while gate._reopened is None: await asyncio.sleep(0)
            assert protocol.cycle.release_state.accepted==0
            gate.finish_commit(holder['barrier'],committed=True)
            data=await all_bytes(peer)
            assert b'committed-once' in data and data.endswith(b'0\r\n\r\n')
            assert protocol.cycle.release_state.interim_accepted==len(interim)
            assert protocol.cycle.release_state.accepted==len(data)
        finally:transport.close();peer.close()
    asyncio.run(run())


def test_actual_first_release_cutoff_starts_after_long_computation(monkeypatch):
    from bookflow.core import publication_admission as module
    clock=[10.0]
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    async def run():
        gate=Admission();closed=asyncio.Event();holder={}
        async def app(scope,receive,send):
            clock[0]+=40  # legitimate computation precedes any release attempt
            holder['barrier']=gate.close_for_commit();closed.set()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'long-write-result'})
        transport,protocol,peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            await closed.wait()
            while gate._reopened is None:await asyncio.sleep(0)
            assert protocol.cycle.release_state.cutoff==80.0
            gate.finish_commit(holder['barrier'],committed=True)
            assert b'long-write-result' in await all_bytes(peer)
            assert protocol.cycle.release_state.cutoff==80.0
        finally:transport.close();peer.close()
    asyncio.run(run())


def test_repeated_windows_do_not_reset_cutoff_or_revalidate_after_expiry(monkeypatch):
    from bookflow.core import publication_admission as module
    clock=[10.0];monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    async def run():
        gate=Admission();state=ResponseRelease(deadline=12.0);state.start()
        barrier=gate.close_for_commit()
        pending=asyncio.create_task(gate.wait_open(state,lambda:False))
        while gate._reopened is None:await asyncio.sleep(0)
        clock[0]=11;gate.finish_commit(barrier,committed=False)
        barrier=gate.close_for_commit();clock[0]=13
        with pytest.raises(AdmissionCancelled):await pending
        assert state.cutoff==12
        gate.finish_commit(barrier,committed=False)
    asyncio.run(run())


def test_zero_final_waiters_use_no_validation_pool_slot(owner_host, monkeypatch):
    from tests.test_commit_hooks import command, observe
    from bookflow.core.commit_hooks import CommitHooks
    host = owner_host[0]
    issued = command(owner_host, 'token issue', {'label':'pool owner revoke'}, company=False)
    events = observe(monkeypatch, host)
    actual_before = CommitHooks._before
    closed, commit_release = threading.Event(), threading.Event()
    trace, errors = [], []
    def before(self, *args):
        actual_before(self, *args)
        if self is host._commit_hooks:
            assert threading.get_ident() == host._writer.ident
            closed.set()
            assert commit_release.wait(5)
    monkeypatch.setattr(CommitHooks, '_before', before)
    def revoke():
        try:
            command(owner_host, 'token revoke', {'token':issued['token_id']}, company=False)
            trace.append('writer-finished')
        except BaseException as exc: errors.append(exc)
    writer = threading.Thread(target=revoke)
    async def run():
        import anyio
        gate=host.publication_admission; entered=threading.Event();release=threading.Event()
        writer.start()
        while not closed.is_set():await asyncio.sleep(0)
        limiter=anyio.to_thread.current_default_thread_limiter();old=limiter.total_tokens;limiter.total_tokens=1
        def occupy():entered.set();assert release.wait(5)
        blocked=asyncio.create_task(anyio.to_thread.run_sync(occupy))
        while not entered.is_set():await asyncio.sleep(0)
        connections=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'complete'})
        try:
            for _ in range(8):
                transport,protocol,peer=await connection(app,gate);connections.append((transport,protocol,peer))
                await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            while gate._reopened is None:await asyncio.sleep(0)
            commit_release.set()
            writer.join(1)  # event loop frozen while the real revoke COMMIT finishes
            assert trace==['writer-finished'] and not release.is_set()
            release.set();await blocked
            for _,_,peer in connections:assert b'complete' in await all_bytes(peer)
        finally:
            commit_release.set();writer.join(2)
            release.set();await blocked;limiter.total_tokens=old
            for transport,_,peer in connections:transport.close();peer.close()
    asyncio.run(run())
    assert not errors and events
    assert host.submit(lambda:host._hub.raw.execute('SELECT revoked_at FROM api_tokens WHERE id=?',
        (issued['token_id'],)).fetchone()[0]) is not None


def test_truncated_local_reply_never_returns_fallback(tmp_path):
    from bookflow.core.forward import call_host
    from bookflow.core.errors import BookflowError
    import tempfile
    directory=tempfile.TemporaryDirectory(prefix='bf-f1-')
    path=str(__import__('pathlib').Path(directory.name)/'owned.sock');server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    server.bind(path);server.listen(1);received=[]
    def respond():
        conn,_=server.accept()
        with conn:
            received.append(conn.recv(65536))
            conn.sendall((30).to_bytes(4,'big')+b'{"output":')
    thread=threading.Thread(target=respond);thread.start()
    try:
        with pytest.raises(BookflowError) as exc:call_host(path,{'command':'durable'})
        assert exc.value.code=='E_IO' and exc.value.details['outcome']=='unknown'
        assert received
    finally:thread.join(2);server.close();directory.cleanup()


@pytest.mark.parametrize('cancel', ['deadline', 'disconnect', 'shutdown'])
def test_actual_closed_response_wait_aborts_without_terminal(cancel):
    async def run():
        gate=Admission();barrier=gate.close_for_commit();entered=asyncio.Event();holder={}
        async def app(scope,receive,send):
            if cancel=='deadline':
                scope['bookflow.response_release'].deadline=asyncio.get_running_loop().time()+.08
            entered.set()
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'never'})
        from bookflow.adapters.http.publication import PublicationMiddleware
        host=SimpleNamespace(publication_admission=gate,_stopping=False)
        transport,protocol,peer=await connection(PublicationMiddleware(app,host=host),gate,middleware=False)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            await entered.wait()
            while gate._reopened is None:await asyncio.sleep(0)
            if cancel=='shutdown':host._stopping=True
            elif cancel=='disconnect':peer.shutdown(socket.SHUT_RDWR)
            assert await all_bytes(peer)==b''
            while protocol.tasks:await asyncio.sleep(0)
            assert not protocol.cycle.response_complete and protocol.cycle.release_state.accepted==0
        finally:gate.finish_commit(barrier,committed=False);transport.close();peer.close()
    asyncio.run(run())


def test_pending_header_keeps_pipeline_order_and_one_completion():
    async def run():
        gate=Admission();starts=[];ends=[];holder={};barriers=[]
        async def app(scope,receive,send):
            starts.append(scope['path'])
            await send({'type':'http.response.start','status':200,'headers':[(b'content-length',b'2')]})
            await send({'type':'http.response.body','body':scope['path'].encode()})
            ends.append(scope['path'])
        transport,protocol,peer=await connection(app,gate)
        original=gate.transport_write
        def finish(barrier):gate.finish_commit(barrier,committed=True)
        def write(frame,raw,data):
            if not barriers:
                barrier=gate.close_for_commit();barriers.append(barrier)
                asyncio.get_running_loop().call_soon(finish,barrier)
            return original(frame,raw,data)
        gate.transport_write=write
        try:
            await asyncio.get_running_loop().sock_sendall(peer,
                b'GET /1 HTTP/1.1\r\nHost: owned\r\n\r\nGET /2 HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            data=await all_bytes(peer)
            assert starts==ends==['/1','/2']
            assert data.count(b'200 OK')==2 and data.count(b'\r\n\r\n/1')==data.count(b'\r\n\r\n/2')==1
            assert data.index(b'/1') < data.index(b'/2') and protocol.cycle.response_complete
        finally:transport.close();peer.close()
    asyncio.run(run())
