"""A registered valid input/result beyond receipt budgets remains complete."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import anyio
import pytest

from tests.test_row3_host import hosted, live


@pytest.mark.timeout(180)
def test_installed_json_file_large_valid_write_full_receipt_and_no_reexecution(hosted, live, tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    inbox, outbox = tmp_path / 'inbox', tmp_path / 'outbox'
    inbox.mkdir(mode=0o700)
    outbox.mkdir(mode=0o700)
    # Custom-field text has no business size cap. This exercises the real registry
    # and storage, not a synthetic command or a permissive decoder-only fixture.
    value = 'Receipt import é\\"\n' * 500_000
    raw = {'name': 'Large complete default', 'kind': 'text', 'scopes': ['customer'], 'default': value}
    source = inbox / 'business.json'
    source.write_text(json.dumps(raw), encoding='utf-8')
    assert source.stat().st_size > 8 * 1024 * 1024
    destination = outbox / 'complete.json'
    binary = os.environ.get('BOOKFLOW_MCP_TEST_BINARY', str(Path(sys.executable).with_name('bookflow')))

    async def witness():
        params = StdioServerParameters(command=binary, args=['mcp', '--url', live,
            '--input-dir', str(inbox), '--output-dir', str(outbox)],
            env={'BOOKFLOW_TOKEN': hosted.secret, 'BOOKFLOW_COMPANY': hosted.company_id,
                 'BOOKFLOW_DATA_ROOT': str(tmp_path / 'absent')}, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                receipt = await session.call_tool('bookflow_run', {'command': 'custom-field create',
                    'reason': 'Large registered input witness', 'transport': {
                        'input_json_file': str(source), 'result_file': str(destination)}})
                assert not receipt.is_error, receipt.structured_content
                delivered = receipt.structured_content
                assert delivered['delivery'] == 'complete_json_file'
                data = destination.read_bytes()
                assert len(data) > 8 * 1024 * 1024
                assert delivered['sha256'] == hashlib.sha256(data).hexdigest()
                original = json.loads(data)
                assert original['default'] == value
                assert original['name'] == raw['name']
                # A receipt too large to retain is not truncated or advertised as
                # recoverable; observing either alias must never call its planner.
                assert not delivered['recovery']['receipt_available']
                reference = delivered['operation_ref']
                for alias in ('input_ref', 'operation_ref'):
                    observed = await session.call_tool('bookflow_run', {alias: reference, 'action': 'execute'})
                    doc = observed.structured_content
                    assert doc.get('default') is None
                    assert doc != original
                return original['id']
    field = anyio.run(witness)
    with sqlite3.connect((hosted.root / 'hub.db').as_uri() + '?mode=ro', uri=True) as db:
        relative = db.execute('SELECT path FROM companies WHERE id=?', (hosted.company_id,)).fetchone()[0]
    with sqlite3.connect((hosted.root / relative / 'company.db').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT default_canonical_text FROM custom_field_defs WHERE id=?', (field,)).fetchone() == (value,)
        assert db.execute("SELECT count(*) FROM audit_events WHERE reason='Large registered input witness'").fetchone() == (1,)
    assert not list(outbox.glob('.bookflow-mcp-*'))
    assert not hosted.handle.host._mcp_runtime.intents.active
