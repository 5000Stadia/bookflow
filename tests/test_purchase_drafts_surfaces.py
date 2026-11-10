"""Saved bill drafts use the same six commands through all four adapters."""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset('purchase-draft ' + verb for verb in
                     ('create', 'update', 'preview', 'cancel', 'show', 'list'))


@pytest.mark.timeout(900)
def test_drafts_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in SURFACES:
                async def call(verb, data):
                    return await matrix.call(surface, 'purchase-draft ' + verb, data)
                vendor = await matrix.call(surface, 'vendor create', {'name': 'Draft parity vendor'})
                account = await matrix.call(surface, 'account create',
                                            {'name': 'Draft parity expense', 'type': 'expense'})
                payload = dict(vendor=vendor['id'], date='2026-06-15',
                               expenses=[dict(account=account['id'], amount='80.00')])
                saved = await call('create', dict(payload, operation_key='parity-save'))
                assert saved['preview']['total']['minor_units'] == 8000
                assert (await call('preview', {'draft': saved['id']}))['preview']['accounting_effect'] == 'none'
                updated = await call('update', dict(payload, draft=saved['id'], expected_version=1,
                                                   operation_key='parity-edit', memo='Resumed'))
                assert updated['version'] == 2
                assert await call('show', {'draft': saved['id']}) == updated
                assert saved['id'] in [row['id'] for row in (await call('list', {}))['items']]
                cancelled = await call('cancel', dict(draft=saved['id'], expected_version=2,
                                                     operation_key='parity-cancel'))
                assert cancelled['state'] == 'cancelled'
                assert len(cancelled['history']) == 3
        finally:
            await matrix.close()
    anyio.run(witness)
