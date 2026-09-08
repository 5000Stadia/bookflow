"""Registered history with actual uvicorn admission on owned socketpairs."""
import asyncio
import time
import pytest
from starlette.concurrency import run_in_threadpool
from bookflow.adapters.http.app import create_app
from bookflow.hub.identity_admin import PutMembership, Version
from bookflow.hub.permission_catalog import ScopeKey
from tests.test_history_public_stream_resume import feed
from tests.test_audit_projection_publication import apply
from tests.test_http_transport_admission import connection, all_bytes


# Actual producer setup, fresh stream validation and B2 mutation exceeded60s;
# the production response deadline below is unchanged and explicitly asserted.
@pytest.mark.timeout(120)
def test_socket_stream_cancels_paused_next_frame_before_authority_commit(feed):
    host,path,start,first,second=feed
    app=create_app(host,secure_cookies=False)
    async def exercise():
        first_sent=asyncio.Event();holder={}
        async def instrument(scope,receive,send):
            assert scope['bookflow.transport_admission'] is True
            async def observe_send(message):
                await send(message)
                if (message['type']=='http.response.body' and b'event: audit' in message.get('body',b'')
                        and not first_sent.is_set()):
                    holder['protocol'].flow.pause_writing()
                    first_sent.set()
            await app(scope,receive,observe_send)
        transport,protocol,peer=await connection(instrument,host.publication_admission,middleware=False)
        holder['protocol']=protocol
        try:
            loop=asyncio.get_running_loop()
            await loop.sock_sendall(peer,('GET /hub-events?limit=2 HTTP/1.1\r\nHost: owned\r\nAuthorization: Bearer secret-A\r\nLast-Event-ID: '+start+'\r\n\r\n').encode())
            await asyncio.wait_for(first_sent.wait(),30)
            async def blocked():
                while not protocol.flow._is_writable_event._waiters:
                    await asyncio.sleep(0.001)
            await asyncio.wait_for(blocked(),10)
            # Normal B2 commit must cancel/acknowledge the paused delivery before
            # it can complete; resuming transport is deliberately later.
            changed=await run_in_threadpool(apply,host,path,
                PutMembership('A',ScopeKey('organization','O'),Version(1),'readonly'),'W')
            assert changed and not protocol.flow._is_writable_event.is_set()
            assert time.monotonic() < protocol.cycle.release_state.cutoff
            protocol.flow.resume_writing()
            wire=await all_bytes(peer)
            assert b'200 OK' in wire and first.encode() in wire
            assert second.encode() not in wire
            assert b'keep-alive' not in wire and not wire.endswith(b'0\r\n\r\n')
            assert protocol.cycle.release_state.aborted
        finally:
            transport.close();peer.close()
    asyncio.run(exercise())
    assert host._readers_attached==0
