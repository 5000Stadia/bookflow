"""Production closure at actual revoke, after final HTTP headers were released."""
import asyncio
from tests.test_http_transport_admission import connection,all_bytes
from tests.test_row3_host import hosted


def test_actual_revoke_after_headers_aborts_captured_body(hosted):
    host=hosted.handle.host
    observer=hosted.ok('token.issue',{'label':'routing observer'})['secret']
    async def run():
        entered=asyncio.Event();holder={}
        async def app(scope,receive,send):
            async def delayed(message):
                if message['type']=='http.response.body':
                    holder['protocol'].flow.pause_writing();entered.set()
                await send(message)
            await hosted.handle.app(scope,receive,delayed)
        transport,protocol,peer=await connection(app,host.publication_admission,middleware=False)
        holder['protocol']=protocol
        request=(f'POST /companies/{hosted.company_id}/commands/company.show HTTP/1.1\r\nHost: owned\r\n'
                 f'Authorization: Bearer {hosted.secret}\r\nContent-Type: application/json\r\n'
                 'Content-Length: 2\r\nConnection: close\r\n\r\n{}').encode()
        try:
            await asyncio.get_running_loop().sock_sendall(peer,request)
            await asyncio.wait_for(entered.wait(),5)
            assert protocol.cycle.release_state.accepted>0
            revoked=await asyncio.to_thread(hosted.call,'token.revoke',{'token':hosted.token},
                headers={'Authorization':'Bearer '+observer})
            assert revoked.status_code==200,revoked.text
            protocol.flow.resume_writing()
            data=await all_bytes(peer)
            assert data.startswith(b'HTTP/1.1 200 OK\r\n')
            assert data.split(b'\r\n\r\n',1)[1]==b''
            assert not protocol.cycle.response_complete
        finally:transport.close();peer.close()
    asyncio.run(run())
    assert hosted.ok('company.show',company=hosted.company_id,headers={'Authorization':'Bearer '+observer})['id']==hosted.company_id
