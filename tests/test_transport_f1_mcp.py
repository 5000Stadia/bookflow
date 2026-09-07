"""Actual SDK stdio -> owned HTTP protocol -> shared commands and verified files."""
import os
from pathlib import Path
import socket
import sys
import threading
import time

import anyio
import pytest
import uvicorn

from bookflow.adapters.http.admission import AdmissionProtocol
from tests.test_row3_host import hosted


def test_sdk_current_source_commands_errors_and_file_terminals(hosted, tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from tests.test_attachment_http import BODY, target
    target_id=target(hosted)
    # Compare the complete actual captured result, including computed age fields,
    # rather than running the query again across a wall-clock second boundary.
    from copy import deepcopy
    from bookflow.adapters.http.execution import PublishedDocument
    captured=[]
    original=PublishedDocument.__init__
    def capture(self, document, permit, host, credential):
        original(self, document, permit, host, credential)
        if permit.cmd.name == 'company show': captured.append(deepcopy(document))
    monkeypatch.setattr(PublishedDocument, '__init__', capture)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
    url=f'http://127.0.0.1:{sock.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(hosted.handle.app,http=AdmissionProtocol,loop='asyncio',log_level='warning',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]));thread.start()
    inputs=tmp_path/'inputs';outputs=tmp_path/'outputs';inputs.mkdir();outputs.mkdir()
    source=inputs/'receipt.pdf';source.write_bytes(BODY)
    code=str(Path(__file__).resolve().parents[1]/'src')
    async def run():
        params=StdioServerParameters(command=str(Path(sys.executable).with_name('bookflow')),args=['mcp','--url',url,
            '--input-dir',str(inputs),'--output-dir',str(outputs)],env={
            'PYTHONPATH':code,'BOOKFLOW_TOKEN':hosted.secret,'BOOKFLOW_COMPANY':hosted.company_id,
            'BOOKFLOW_DATA_ROOT':str(tmp_path/'absent'),'TMPDIR':str(tmp_path)},cwd=str(tmp_path))
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.discover()
                async def call(command,raw,**options):
                    return await client.call_tool('bookflow_run',{'command':command,'input':raw,**options})
                updated=await call('company update',{'fax':'actual F1 SDK'})
                assert not updated.is_error and updated.structured_content['changed_fields']==['fax']
                readback=await call('company show',{})
                assert captured and not readback.is_error and readback.structured_content==captured[-1]
                assert readback.structured_content['info']['fax']=='actual F1 SDK'
                invalid=await call('company update',{'unknown_field':True})
                assert invalid.is_error and invalid.structured_content['code']=='E_VALIDATION'
                assert invalid.structured_content['details']['fields']
                added=await call('attachment add',{'record_type':'customer','record_id':target_id,
                    'original_filename':source.name},transport={'input_file':str(source)})
                assert not added.is_error,added
                destination=outputs/'verified.pdf'
                downloaded=await call('attachment get',{'attachment':added.structured_content['attachment']['id']},
                    transport={'output_file':str(destination)})
                assert not downloaded.is_error and destination.read_bytes()==BODY
                recovered=outputs/'recovered.pdf'
                again=await client.call_tool('bookflow_run',{'input_ref':downloaded.meta['bookflow_transport']['operation_ref'],
                    'action':'execute','output_file':str(recovered)})
                assert not again.is_error and again.structured_content==downloaded.structured_content
                assert recovered.read_bytes()==BODY and not list(outputs.glob('.bookflow-mcp-*'))
    try:
        cutoff=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<cutoff:time.sleep(.01)
        assert server.started
        anyio.run(run)
        assert not hosted.handle.host._transfers
        assert not (tmp_path/'absent').exists()
    finally:
        server.should_exit=True;thread.join(10);sock.close()
    assert not thread.is_alive()
