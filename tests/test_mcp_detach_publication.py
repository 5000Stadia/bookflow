"""Own detach has a receipt postcondition, never an independent authority bypass."""

import os
from pathlib import Path
import sqlite3
import sys
import threading

import anyio
import pytest
import sqlalchemy as sa

from bookflow.core.publication import PublicationPermit
from bookflow.hub import schema as h
from tests.test_row3_host import hosted, live


@pytest.mark.timeout(90)
@pytest.mark.parametrize('loss', ['none', 'revoke', 'downgrade'])
def test_actual_mcp_own_detach_receipt_and_independent_loss(hosted, live, tmp_path, monkeypatch, loss):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    observer = hosted.ok('token.issue', {'label': 'Detach observer'})['secret']
    folder = Path(hosted.info()['path'])
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check

    def barrier(self, *args, **kwargs):
        if loss != 'none' and self.cmd.name == 'company detach' and self.committed and not reached.is_set():
            reached.set()
            assert release.wait(20)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PublicationPermit, 'check', barrier)

    async def witness():
        params = StdioServerParameters(command=os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow'))),
            args=['mcp', '--url', live], cwd=str(tmp_path),
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                preview = await session.call_tool('bookflow_run', {'command': 'company detach',
                    'input': {'company': hosted.company_id}, 'dry_run': True, 'reason': 'Detach witness'})
                assert not preview.is_error and preview.structured_content['dry_run']
                assert hosted.info()['company_id'] == hosted.company_id
                replies = []
                async def detach():
                    replies.append(await session.call_tool('bookflow_run', {'command': 'company detach',
                        'input': {'company': hosted.company_id}, 'reason': 'Detach witness'}))
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(detach)
                    if loss != 'none':
                        try:
                            assert await anyio.to_thread.run_sync(reached.wait, 20)
                            if loss == 'revoke':
                                response = await anyio.to_thread.run_sync(lambda: hosted.call('token.revoke',
                                    {'token': hosted.token}, headers={'Authorization': 'Bearer ' + observer}))
                                assert response.status_code == 200
                            else:
                                host = hosted.handle.host
                                def downgrade():
                                    db = host._hub
                                    db.raw.execute('BEGIN IMMEDIATE')
                                    actor = db.conn.execute(sa.select(h.api_tokens.c.user_id).where(h.api_tokens.c.id == hosted.token)).scalar_one()
                                    db.conn.execute(h.users.update().where(h.users.c.id == actor).values(hub_admin=False))
                                    db.raw.execute('COMMIT')
                                await anyio.to_thread.run_sync(lambda: host.submit(downgrade))
                        finally:
                            release.set()
                reply = replies[0]
                if loss == 'none':
                    assert not reply.is_error, reply.structured_content
                    assert reply.structured_content['company_id'] == hosted.company_id
                    ref = reply.meta['bookflow_delivery']['operation_ref']
                    recovered = await session.call_tool('bookflow_run', {'input_ref': ref, 'action': 'execute'})
                    assert not recovered.is_error
                    assert recovered.structured_content == reply.structured_content
                else:
                    assert reply.is_error
                    assert reply.structured_content['details']['outcome'] == 'unknown'
                    assert 'path' not in reply.structured_content
    anyio.run(witness)
    assert (folder / 'company.db').is_file()
    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT count(*) FROM companies WHERE id=?', (hosted.company_id,)).fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM audit_events WHERE command='company detach' AND reason='Detach witness'").fetchone() == (1,)
    assert not hosted.handle.host._mcp_runtime.intents.active
    assert hosted.handle.host._readers_attached == 0
