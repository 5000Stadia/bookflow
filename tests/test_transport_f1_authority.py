"""Actual command COMMIT and current token proof at the real h11 boundary."""
import asyncio
import json
import threading

import pytest

from bookflow.core.commit_hooks import CommitHooks
from tests.test_commit_hooks import observe
from tests.test_http_transport_admission import connection, all_bytes
from tests.test_row3_host import hosted


@pytest.mark.parametrize('revoke', [False, True])
def test_committed_mutation_is_never_replayed_at_delayed_actual_http_release(hosted, monkeypatch, revoke):
    host=hosted.handle.host
    observer=hosted.ok('token.issue',{'label':'F1 owner observer'})['secret']
    prior=hosted.ok('audit.list',{'command':'company update','limit':200},company=hosted.company_id)
    prior_ids={item['id'] for item in prior['items']}
    events=observe(monkeypatch,host)
    original=CommitHooks._before
    threads=[]
    def confined(self,*args):
        if self is host._commit_hooks: threads.append(threading.get_ident());assert threading.get_ident()==host._writer.ident
        return original(self,*args)
    monkeypatch.setattr(CommitHooks,'_before',confined)
    async def run():
        entered=asyncio.Event();holder={}
        async def app(scope,receive,send):
            async def delayed(message):
                if message['type']=='http.response.start':
                    holder['protocol'].flow.pause_writing();entered.set()
                await send(message)
            await hosted.handle.app(scope,receive,delayed)
        transport,protocol,peer=await connection(app,host.publication_admission,middleware=False)
        holder['protocol']=protocol
        body=json.dumps({'fax':'F1 once committed'}).encode()
        request=(f'POST /companies/{hosted.company_id}/commands/company.update HTTP/1.1\r\n'
                 f'Host: owned\r\nAuthorization: Bearer {hosted.secret}\r\nContent-Type: application/json\r\n'
                 f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()+body
        try:
            await asyncio.get_running_loop().sock_sendall(peer,request)
            await asyncio.wait_for(entered.wait(),5)
            while not protocol.flow._is_writable_event._waiters:await asyncio.sleep(0)
            if revoke:
                response=await asyncio.to_thread(hosted.call,'token.revoke',{'token':hosted.token},
                                                 headers={'Authorization':'Bearer '+observer})
                assert response.status_code==200,response.text
            protocol.flow.resume_writing()
            data=await all_bytes(peer)
            if revoke: assert data==b'' and not protocol.cycle.response_complete
            else:
                assert b'200 OK' in data
                output=json.loads(data.split(b'\r\n\r\n',1)[1]);assert output['changed_fields']==['fax']
        finally:transport.close();peer.close()
    asyncio.run(run())
    current=hosted.ok('company.show',company=hosted.company_id,headers={'Authorization':'Bearer '+observer})
    assert current['info']['fax']=='F1 once committed'
    audit=hosted.ok('audit.list',{'command':'company update','limit':200},company=hosted.company_id,
                    headers={'Authorization':'Bearer '+observer})
    assert len([item for item in audit['items'] if item['id'] not in prior_ids])==1
    assert threads and all(thread==host._writer.ident for thread in threads)
    assert events and host._readers_attached==0
