"""Real owned attachment transfers across pre-release invalidation and offline handoff."""
import io
import threading

import pytest

from bookflow.core import registry
from bookflow.core.config import Config, os_login
from bookflow.core.context import Context, Interface
from bookflow.core.publication import OSBinding
from bookflow.core.transfers import HostedTransfer
from bookflow.adapters.http.published_transfer import PublishedTransfer
from tests.test_transfer_local import local_host, invoke


@pytest.mark.parametrize('direction', ['input', 'output'])
def test_local_zero_release_wait_surrenders_lease_without_replay(root, local_host, monkeypatch, direction):
    host, _ = local_host
    data = b'captured exact body' * 4096
    raw = {'record_type':'customer', 'record_id':host.test_target['id'], 'original_filename':'f1.pdf'}
    if direction == 'output':
        uploaded = invoke(root, 'attachment add', raw, input_stream=io.BytesIO(data))
        raw = {'attachment': uploaded['attachment']['id']}
    gate = host.publication_admission
    original_send, original_suspend = gate.socket_send, HostedTransfer.suspend_publication
    original_execute = PublishedTransfer._execute
    suspended = threading.Event(); barriers = []; executions = []; evidence = []
    def execute(self, session, **options):
        executions.append(self.cmd.name)
        return original_execute(self, session, **options)
    def send(frame, sock, payload):
        if not barriers:
            barriers.append(gate.close_for_commit())
        return original_send(frame, sock, payload)
    def suspend(self):
        original_deadline = self.resource.lease._deadline
        original_suspend(self)
        evidence.append((self, original_deadline, host._readers_attached, len(host._transfers)))
        suspended.set()
    monkeypatch.setattr(gate, 'socket_send', send)
    monkeypatch.setattr(HostedTransfer, 'suspend_publication', suspend)
    monkeypatch.setattr(PublishedTransfer, '_execute', execute)
    errors = []
    def reopen():
        try:
            assert suspended.wait(5)
            # Actual writer drains company handles while the response is parked.
            host.submit(lambda: host.release_company(next(iter(host._companies), None)))
            gate.finish_commit(barriers[0], committed=False)
        except BaseException as exc:
            errors.append(exc)
            if barriers: gate.finish_commit(barriers[0], committed=False)
    writer = threading.Thread(target=reopen); writer.start()
    sink = io.BytesIO()
    try:
        result = invoke(root, 'attachment add' if direction == 'input' else 'attachment get', raw,
                        **({'input_stream':io.BytesIO(data)} if direction == 'input' else {'output_stream':sink}))
    finally: writer.join(6)
    assert not writer.is_alive() and not errors
    assert executions == ['attachment add' if direction == 'input' else 'attachment get']
    assert evidence and all(readers == leases == 0 for _, _, readers, leases in evidence)
    for transfer, deadline, _, _ in evidence:
        assert transfer.resource.lease._deadline <= deadline
        assert transfer.resource.lease.state == 'closed'
    if direction == 'output': assert sink.getvalue() == data and result['size_bytes'] == len(data)
    else: assert result['attachment']['size_bytes'] == len(data)


def test_offline_verified_output_releases_root_before_short_sink_writes(root, client):
    from bookflow.core.locks import RootLock
    from bookflow.core.transfer_run import run_transfer
    data = b'offline verified spool' * 4000
    target = client.customer.create(name='F1 offline sink', company='Demo Plumbing Co')
    ctx = Context.new(Interface.python, 'offline-boundary')
    added = run_transfer(registry.get('attachment add'),
                         {'record_type':'customer','record_id':target['id'],'original_filename':'offline.pdf'},
                         ctx, data_root=root, selector='Demo Plumbing Co', input_stream=io.BytesIO(data))
    class Sink:
        def __init__(self): self.data = bytearray(); self.calls = 0
        def write(self, chunk):
            with RootLock(root, 'independent caller sink'):
                self.calls += 1
                count = min(4096, len(chunk)); self.data.extend(chunk[:count]); return count
    sink = Sink()
    out = run_transfer(registry.get('attachment get'), {'attachment':added['attachment']['id']},
                       ctx, data_root=root, selector='Demo Plumbing Co', output_stream=sink)
    assert bytes(sink.data) == data and sink.calls > 1 and out['size_bytes'] == len(data)


from tests.test_row3_host import hosted


def test_actual_http_download_pending_header_releases_lease_then_exact_body(hosted, monkeypatch):
    import asyncio
    from bookflow.core.transfer_protocol import encode_input
    from tests.test_attachment_http import BODY, target
    from tests.test_http_transport_admission import connection, all_bytes
    record=target(hosted)
    raw={'record_type':'customer','record_id':record,'original_filename':'pending.pdf'}
    added=hosted.api.post(f'/companies/{hosted.company_id}/transfers/attachment.add',content=BODY,
        headers={**hosted.bearer,'Content-Type':'application/octet-stream','X-Bookflow-Input':encode_input(raw)})
    assert added.status_code==200,added.text
    host=hosted.handle.host;gate=host.publication_admission
    original_write=gate.transport_write;original_suspend=HostedTransfer.suspend_publication
    original_execute=PublishedTransfer._execute
    barriers=[];observed=[];executions=[]
    def execute(self,session,**options):
        executions.append(self.cmd.name);return original_execute(self,session,**options)
    def write(frame,transport,data):
        if not barriers:barriers.append(gate.close_for_commit())
        return original_write(frame,transport,data)
    def suspend(self):
        deadline=self.resource.lease._deadline
        original_suspend(self)
        observed.append((host._readers_attached,len(host._transfers),deadline,self))
        host.submit(lambda:host.release_company(hosted.company_id))
        gate.finish_commit(barriers[0],committed=False)
    monkeypatch.setattr(gate,'transport_write',write)
    monkeypatch.setattr(HostedTransfer,'suspend_publication',suspend)
    monkeypatch.setattr(PublishedTransfer,'_execute',execute)
    async def run():
        transport,protocol,peer=await connection(hosted.handle.app,gate,middleware=False)
        values=encode_input({'attachment':added.json()['attachment']['id']})
        request=(f'POST /companies/{hosted.company_id}/transfers/attachment.get HTTP/1.1\r\n'
                 f'Host: owned\r\nAuthorization: Bearer {hosted.secret}\r\nX-Bookflow-Input: {values}\r\n'
                 'Content-Length: 0\r\nConnection: close\r\n\r\n').encode()
        try:
            await asyncio.get_running_loop().sock_sendall(peer,request)
            data=await all_bytes(peer)
            assert data.count(b'200 OK')==1 and data.split(b'\r\n\r\n',1)[1]==BODY
            assert protocol.cycle.response_complete and protocol.cycle.release_state.accepted==len(data)
        finally:transport.close();peer.close()
    asyncio.run(run())
    assert executions==['attachment get'] and len(observed)==1
    readers,leases,deadline,transfer=observed[0]
    assert readers==leases==0 and transfer.resource.lease._deadline<=deadline
    assert not host._transfers and host._readers_attached==0
