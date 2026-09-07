"""Real uvicorn protocol on owned socketpairs; no TCP listener or remote delivery claim."""
import asyncio
import socket
import threading
from types import SimpleNamespace
import pytest
import uvicorn
from uvicorn.server import ServerState
from bookflow.adapters.http.admission import AdmissionProtocol
from bookflow.adapters.http.publication import PublicationMiddleware
from bookflow.core.publication_admission import Admission


async def connection(app, gate, *, middleware=True):
    config = uvicorn.Config(PublicationMiddleware(app, host=SimpleNamespace(publication_admission=gate)) if middleware else app,
                            http=AdmissionProtocol, loop='asyncio', lifespan='off', access_log=False)
    left, peer = socket.socketpair();peer.setblocking(False)
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_connection(
        lambda: AdmissionProtocol(config, ServerState(), {}), sock=left)
    return transport, protocol, peer


async def all_bytes(peer):
    result = bytearray();loop = asyncio.get_running_loop()
    while True:
        chunk = await asyncio.wait_for(loop.sock_recv(peer, 65536), 2)
        if not chunk: return bytes(result)
        result.extend(chunk)


@pytest.mark.parametrize('at', ['headers', 'body', 'terminal'])
def test_drain_cancel_ack_with_event_loop_frozen(at):
    async def run():
        gate = Admission(); blocked = asyncio.Event(); holder = {}
        async def app(scope, receive, send):
            async def pause():
                holder['protocol'].flow.pause_writing();blocked.set()
            if at == 'headers': await pause()
            await send({'type':'http.response.start','status':200,'headers':[]})
            if at == 'body': await pause()
            await send({'type':'http.response.body','body':b'private','more_body':True})
            if at == 'terminal': await pause()
            await send({'type':'http.response.body','body':b'','more_body':False})
        transport, protocol, peer = await connection(app, gate);holder['protocol'] = protocol
        try:
            await asyncio.get_running_loop().sock_sendall(peer, b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            await asyncio.wait_for(blocked.wait(), 2)
            # Let middleware's worker finish validation and enter uvicorn drain.
            while not protocol.flow._is_writable_event._waiters: await asyncio.sleep(0)
            trace=[]
            def revoke():
                barrier=gate.close_for_commit();trace.append('cancel-ack')
                gate.finish_commit(barrier, committed=True);trace.append('commit')
            writer=threading.Thread(target=revoke);writer.start();writer.join(1)
            assert trace == ['cancel-ack','commit']  # this loop has not progressed
            protocol.flow.resume_writing()
            data=await all_bytes(peer)
            if at == 'headers':
                assert b'200 OK' in data and data.endswith(b'7\r\nprivate\r\n0\r\n\r\n')
            else:
                assert b'0\r\n\r\n' not in data
                assert (b'private' in data) == (at == 'terminal')
                assert b'200 OK' in data
        finally: transport.close();peer.close()
    asyncio.run(run())


def test_large_body_exact_success_head_and_complete_empty_final():
    async def run(method):
        gate=Admission();body=b'a'*140000
        async def app(scope, receive, send):
            await send({'type':'http.response.start','status':200,'headers':[(b'content-length',str(len(body)).encode())]})
            await send({'type':'http.response.body','body':body})
        transport, protocol, peer=await connection(app, gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer, method+b' / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            data=await all_bytes(peer);headers, content=data.split(b'\r\n\r\n',1)
            assert b'200 OK' in headers
            assert content == (body if method==b'GET' else b'')
        finally:transport.close();peer.close()
    for method in (b'GET',b'HEAD'):asyncio.run(run(method))


def test_sse_abort_has_no_heartbeat_or_success_terminal():
    async def run():
        gate=Admission()
        async def app(scope, receive, send):
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
            await send({'type':'http.response.body','body':b'event: audit\ndata: permitted\n\n','more_body':True})
            barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True)
            # Represents a fresh current-authority denial at the next batch.
            from bookflow.adapters.http.publication import protect
            from bookflow.core.errors import BookflowError
            class Denied:
                credential=SimpleNamespace(token_id='owned')
                def check(self, **kwargs):raise BookflowError('E_PERMISSION')
            protect(Denied())
            await send({'type':'http.response.body','body':b': keep-alive\n\n','more_body':True})
        transport, protocol, peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\n\r\n')
            data=await all_bytes(peer)
            assert b'data: permitted' in data
            assert b'keep-alive' not in data and b'0\r\n\r\n' not in data
        finally:transport.close();peer.close()
    asyncio.run(run())


def test_plain_app_without_middleware_cannot_send_unframed_business_bytes():
    async def run():
        refused = []
        async def app(scope, receive, send):
            try:
                await send({'type': 'http.response.start', 'status': 200,
                            'headers': [(b'x-business', b'private-unframed')]})
            except RuntimeError as exc:
                refused.append(str(exc))
                raise
            await send({'type': 'http.response.body', 'body': b'private-unframed'})
        transport, protocol, peer = await connection(app, Admission(), middleware=False)
        try:
            await asyncio.get_running_loop().sock_sendall(
                peer, b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            data = await all_bytes(peer)
            assert refused == ['application response missing admission frame']
            assert b'500 Internal Server Error' in data and b'200 OK' not in data
            assert data.split(b'\r\n\r\n', 1)[1] == b'15\r\nInternal Server Error\r\n0\r\n\r\n'
            assert b'x-business' not in data and b'private-unframed' not in data
        finally:
            transport.close(); peer.close()
    asyncio.run(run())


def test_protocol_100_and_fixed_500_have_no_business_detail():
    async def run():
        gate=Admission()
        async def app(scope, receive, send):
            await receive()
            raise RuntimeError('never-public-business-secret')
        transport, protocol, peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'POST / HTTP/1.1\r\nHost: owned\r\nExpect: 100-continue\r\nContent-Length: 1\r\nConnection: close\r\n\r\n')
            first=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(peer,65536),2)
            assert first == b'HTTP/1.1 100 Continue\r\n\r\n'
            await asyncio.get_running_loop().sock_sendall(peer,b'x')
            rest=await all_bytes(peer)
            assert rest.split(b'\r\n\r\n',1)[1] == b'15\r\nInternal Server Error\r\n0\r\n\r\n'
            assert b'never-public' not in rest
        finally:transport.close();peer.close()
    asyncio.run(run())


@pytest.mark.parametrize('complete',[False,True])
def test_mcp_client_terminal_verification_and_client_file_handoff(tmp_path,complete):
    """Actual client/decoder/output capability; no protected relay value before T."""
    import httpx2
    from bookflow.adapters.mcp.client import Client
    from bookflow.adapters.mcp.files import Directories
    from bookflow.adapters.mcp.framing import encode
    from bookflow.adapters.mcp.catalog import BRIDGE_VERSION
    from bookflow.core.errors import BookflowError
    gate=Admission();document={'private':'permitted-only-with-terminal'}
    records=list(encode(document,check=lambda:None,operation_ref='owned',binary=[b'owned-file']))
    output=tmp_path/'out';output.mkdir(mode=0o700);destination=output/'result.bin'
    async def run():
        class Body(httpx2.AsyncByteStream):
            async def __aiter__(self):
                for record in (records if complete else records[:-1]):yield record
                # Host ownership of all supplied records precedes this reduction.
                barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True)
        def handler(request):
            return httpx2.Response(200,headers={'content-type':'application/vnd.bookflow.mcp-records',
                'x-bookflow-mcp-version':str(BRIDGE_VERSION)},stream=Body())
        from contextlib import closing
        with closing(Directories([])) as inputs, closing(Directories([str(output)])) as outputs:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler),base_url='http://owned') as http:
                client=Client(http,inputs,outputs)
                if complete:
                    result,error,delivery=await client.result('owned','run',output_file=str(destination))
                    assert result==document and error is False
                    assert delivery['response_kind']=='verified_command_completion'
                else:
                    with pytest.raises(BookflowError) as caught:
                        await client.result('owned','run',output_file=str(destination))
                    assert caught.value.code=='E_IO'
    asyncio.run(run())
    if complete:assert destination.read_bytes()==b'owned-file'
    else:assert list(output.iterdir())==[]


def test_validation_in_worker_racing_commit_cannot_admit_old_read():
    async def run():
        from bookflow.adapters.http.publication import protect
        gate=Admission();reached=threading.Event();resume=threading.Event();checks=[]
        class Guard:
            credential=SimpleNamespace(token_id='owned')
            def check(self, **kwargs):
                from bookflow.core.errors import BookflowError
                checks.append(True)
                if len(checks)>1:raise BookflowError("E_PERMISSION")
                reached.set();assert resume.wait(2)
        async def app(scope,receive,send):
            protect(Guard())
            await send({'type':'http.response.start','status':200,'headers':[(b'x-private',b'old-read')]})
        transport,protocol,peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            assert await asyncio.to_thread(reached.wait,2)
            barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True);resume.set()
            data=await all_bytes(peer)
            assert len(checks)==2
            assert b'x-private' not in data and b'old-read' not in data
        finally:resume.set();transport.close();peer.close()
    asyncio.run(run())


# A3 checks the real generator's batch certificates, not only an imitation SSE app.
from tests.test_row3_host import hosted


def test_actual_sse_batch_attaches_permit_and_keeps_heartbeat_guard(hosted,monkeypatch):
    from bookflow.core.publication import PublicationPermit
    from bookflow.adapters.http import publication
    from bookflow.adapters.http.execution import PublishedDocument
    batches = []
    replace = publication.replace_guard
    def capture_batch(previous, document):
        replace(previous, document)
        pending = tuple(item for item, _ in publication._guards.get()
                        if isinstance(item, PublishedDocument))
        batches.append((document, pending))
    monkeypatch.setattr(publication, 'replace_guard', capture_batch)
    observed=[];original=PublicationPermit.check
    def check(self,*args,**kwargs):
        if self.cmd.name=='audit tail':observed.append(self.retained())
        return original(self,*args,**kwargs)
    monkeypatch.setattr(PublicationPermit,'check',check)
    async def run():
        transport,protocol,peer=await connection(hosted.handle.app,hosted.handle.host.publication_admission,middleware=False)
        try:
            request=(f'GET /companies/{hosted.company_id}/events?after=0 HTTP/1.1\r\nHost: owned\r\nAuthorization: Bearer {hosted.secret}\r\n\r\n').encode()
            await asyncio.get_running_loop().sock_sendall(peer,request)
            data=bytearray()
            while b': ready\n\n' not in data:
                chunk=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(peer,65536),10)
                assert chunk
                data.extend(chunk)
            assert b'event: audit' in data and observed
            # A real committed HTTP write wakes the subscribed generator for a
            # second nonempty audit batch, not just its initial empty drain.
            marker = 'transport-second-batch-555'
            response = await asyncio.to_thread(hosted.call, 'company.update',
                {'phone': marker}, company=hosted.company_id,
                headers={'X-Bookflow-Reason': marker})
            assert response.status_code == 200, response.text
            while marker.encode() not in data:
                chunk = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(peer,65536),10)
                assert chunk
                data.extend(chunk)
            nonempty = [document for document, _ in batches if document['items']]
            assert len(nonempty) >= 2
            assert nonempty[0].permit is not nonempty[-1].permit
            assert nonempty[0]['next_after'] < nonempty[-1]['next_after']
            # Observe the actual generator's guard context, leaving credential
            # guards intact. Every replacement keeps exactly its current permit.
            assert all(len(pending) == 1 and pending[0] is document
                       for document, pending in batches)
            assert all(x['command']=='audit tail' for x in observed)
        finally:
            transport.close();peer.close()
            for _ in range(20):
                if not protocol.tasks:break
                await asyncio.sleep(.01)
    asyncio.run(run())


@pytest.mark.parametrize('complete',[False,True])
def test_actual_launcher_sdk_stdout_releases_only_verified_result(monkeypatch,tmp_path,complete):
    """Real launcher, SDK server/session and stdio serialization; controlled host HTTP."""
    import json
    import anyio
    import httpx2
    from contextlib import asynccontextmanager
    from mcp import ClientSession, types
    from mcp.shared._context_streams import create_context_streams
    from mcp.shared.message import SessionMessage
    from mcp.server import stdio
    from bookflow.adapters.mcp import launcher
    from bookflow.adapters.mcp.client import Client
    from bookflow.adapters.mcp.files import Directories
    from bookflow.adapters.mcp.framing import encode
    from bookflow.adapters.mcp.catalog import BRIDGE_VERSION
    gate=Admission();private='protected-relay-value'
    records=list(encode({'value':private},check=lambda:None,operation_ref='owned'))
    stdout=[]
    class Body(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for record in (records if complete else records[:-1]):yield record
            barrier=gate.close_for_commit();gate.finish_commit(barrier,committed=True)
    def host(request):
        if request.url.path.endswith('/run'):
            return httpx2.Response(200,headers={'content-type':'application/vnd.bookflow.mcp-records',
                'x-bookflow-mcp-version':str(BRIDGE_VERSION)},stream=Body())
        return httpx2.Response(200,headers={'x-bookflow-mcp-version':str(BRIDGE_VERSION)},json={'scope':'hub'})
    original_client=httpx2.AsyncClient
    monkeypatch.setattr(httpx2,'AsyncClient',lambda **kw:original_client(**kw,transport=httpx2.MockTransport(host)))
    async def run_result(self,*args,**kwargs):return await self.result('owned','run')
    monkeypatch.setattr(Client,'run',run_result)  # isolates admission/upload setup, not relay/decoder
    original_stdio=stdio.stdio_server
    async def run():
        requests,request_read=create_context_streams(1)
        responses,response_read=create_context_streams(1)
        class Input:
            async def __aiter__(self):
                async for item in request_read:
                    yield item.message.model_dump_json(by_alias=True,exclude_unset=True)+'\n'
        class Output:
            async def write(self,line):
                stdout.append(line)
                await responses.send(SessionMessage(types.jsonrpc_message_adapter.validate_json(line,by_name=False)))
            async def flush(self):pass
        @asynccontextmanager
        async def owned_stdio():
            async with original_stdio(stdin=Input(),stdout=Output()) as streams:yield streams
        monkeypatch.setattr(stdio,'stdio_server',owned_stdio)
        inputs,outputs=Directories([]),Directories([])
        done=anyio.Event()
        async def launch():
            try:await launcher.serve(SimpleNamespace(label='owned',selection_root=str(tmp_path)), 'http://owned','owned-token',inputs,outputs)
            finally:done.set()
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(launch)
                async with ClientSession(response_read,requests) as session:
                    await session.discover()
                    reply=await session.call_tool('bookflow_run',{'command':'company list','input':{}})
                    if complete:
                        assert reply.structured_content=={'value':private} and not reply.is_error
                    else:
                        assert reply.is_error and reply.structured_content['code']=='E_IO'
                        assert reply.structured_content['details']['outcome']=='unknown'
                await requests.aclose()
                with anyio.fail_after(2):await done.wait()
        finally:inputs.close();outputs.close()
    anyio.run(run)
    wire=''.join(stdout)
    assert (private in wire) is complete
    (tmp_path/'sdk-stdout.jsonl').write_text(wire)


def test_flow_control_callback_runs_after_mutex_release():
    async def run():
        gate=Admission();seen=[]
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'complete'})
        transport,protocol,peer=await connection(app,gate)
        original=protocol.pause_writing
        def pause():
            assert not gate._mutex.locked()
            seen.append('pause outside mutex');original()
        protocol.pause_writing=pause
        transport.set_write_buffer_limits(high=1,low=0)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n')
            data=await all_bytes(peer)
            assert seen and data.endswith(b'8\r\ncomplete\r\n0\r\n\r\n')
        finally:transport.close();peer.close()
    asyncio.run(run())


def test_oversized_headers_and_protocol_parse_error_are_fixed_only():
    async def run(request):
        gate=Admission()
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[(b'private',b'x'*65536)]})
        transport,protocol,peer=await connection(app,gate)
        try:
            await asyncio.get_running_loop().sock_sendall(peer,request)
            data=await all_bytes(peer)
            assert b'private' not in data and b'x'*100 not in data and b'200 OK' not in data
            assert b'400 Bad Request' in data or b'500 Internal Server Error' in data
        finally:transport.close();peer.close()
    asyncio.run(run(b'GET / HTTP/1.1\r\nHost: owned\r\nConnection: close\r\n\r\n'))
    asyncio.run(run(b'bad request syntax\r\n\r\n'))


@pytest.mark.parametrize('code',['E_PERMISSION','E_IO'])
def test_actual_sse_error_preserves_category_without_private_details(hosted,monkeypatch,code):
    from bookflow.core import dispatch
    from bookflow.core.errors import BookflowError
    original=dispatch.execute
    def fail(cmd,*args,**kwargs):
        if cmd.name=='audit tail':raise BookflowError(code,message='private-error-message',details={'private':'value'})
        return original(cmd,*args,**kwargs)
    monkeypatch.setattr(dispatch,'execute',fail)
    response=hosted.api.get(f'/companies/{hosted.company_id}/events?after=0',headers=hosted.bearer)
    import json
    assert response.status_code==200
    assert response.text == 'event: error\ndata: '+json.dumps(BookflowError(code,details={'stage':'publication','outcome':'unknown'}).to_dict())+'\n\n'
    assert 'private' not in response.text
