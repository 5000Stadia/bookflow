"""The generic reversal door uses the same command through all four adapters."""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('batch reverse',))


@pytest.mark.timeout(900)
def test_batch_reverse_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in SURFACES:
                preview = await matrix.call(surface, 'import preview', {
                    'kind': 'customer', 'content': 'Name\nReverse One\nReverse Two\n'})
                run = await matrix.call(surface, 'import run', {'run': preview['run']['id']})
                batch = run['run']['batch']['id']
                plan = await matrix.call(surface, 'batch reverse', {'batch': batch}, dry_run=True)
                assert len(plan['steps']) == 2
                assert all(s['steps'] == ['customer update', 'customer deactivate'] for s in plan['steps'])
                first = await matrix.call(surface, 'batch reverse', {
                    'batch': batch, 'fingerprint': plan['fingerprint'], 'limit': 1})
                assert first['state'] == 'reversing' and first['remaining'] == 1
                final = await matrix.call(surface, 'batch reverse', {'batch': batch})
                assert final['state'] == 'reversed' and final['remaining'] == 0
        finally:
            await matrix.close()
    anyio.run(witness)
