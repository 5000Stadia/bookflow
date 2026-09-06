import os
import sys
import threading
from pathlib import Path

import anyio
import pytest

from tests.test_row3_host import hosted, live


@pytest.mark.timeout(120)
def test_real_mcp_revocation_mid_download_has_unknown_outcome_and_no_partial_file(hosted, live, tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from bookflow.adapters.mcp import transport
    from tests.test_attachment_http import BODY, target, upload
    record = target(hosted)
    added = upload(hosted, record, BODY * 10)
    assert added.status_code == 200
    attachment = added.json()['attachment']['id']
    observer = hosted.ok('token.issue', {'label': 'download fault observer'})['secret']
    outbox = tmp_path / 'outbox'
    outbox.mkdir(mode=0o700)
    destination = outbox / 'must-not-publish.pdf'
    reached, release = threading.Event(), threading.Event()
    original = transport.binary_chunks

    def delayed(transfer):
        for index, chunk in enumerate(original(transfer)):
            yield chunk
            if index == 0:
                reached.set()
                assert release.wait(15)
    monkeypatch.setattr(transport, 'binary_chunks', delayed)

    async def witness():
        params = StdioServerParameters(command=os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow'))),
            args=['mcp', '--url', live, '--output-dir', str(outbox)],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                replies = []
                async def download():
                    replies.append(await session.call_tool('bookflow_run', {'command': 'attachment get',
                        'input': {'attachment': attachment}, 'transport': {'output_file': str(destination)}}))
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(download)
                    try:
                        assert await anyio.to_thread.run_sync(reached.wait, 15)
                        def revoke():
                            return hosted.call('token.revoke', {'token': hosted.token},
                                headers={'Authorization': 'Bearer ' + observer})
                        result = await anyio.to_thread.run_sync(revoke)
                        assert result.status_code == 200
                    finally:
                        release.set()
                reply = replies[0]
                assert reply.is_error, reply
                assert reply.structured_content['code'] == 'E_IO'
                assert reply.structured_content['details']['outcome'] == 'unknown'
                assert reply.structured_content['details']['operation_ref']
    anyio.run(witness)
    assert not destination.exists()
    assert not list(outbox.iterdir())
    assert not hosted.handle.host._transfers
    assert not hosted.handle.host._mcp_runtime.intents.active
