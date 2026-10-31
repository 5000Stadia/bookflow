"""The POS void contract through Python, CLI, HTTP and actual MCP."""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('pos sale void',))


@pytest.mark.timeout(900)
def test_void_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                call = matrix.call
                info = await call(surface, 'company show', {})
                await call(surface, 'company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                cart = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}
                shown = await call(surface, 'pos sale preview', cart)
                sale = await call(surface, 'pos sale complete', dict(cart, ticket='void-parity',
                    expected_total=shown['total']['amount'], tender={'payment_method': 'Cash'}))
                body = {'sale': sale['sales_receipt']['name'], 'expected_version': 1}
                preview = await call(surface, 'pos sale void', body, dry_run=True)
                voided = await call(surface, 'pos sale void', body)
                stale = await call(surface, 'pos sale void', body, rejected=True)
                again = await call(surface, 'pos sale void', dict(body, expected_version=2))
                results[surface] = (preview['status'], voided['status'], voided['version'], voided['changed'],
                                     stale.get('code') or stale.get('error', {}).get('code'), again['changed'])
            assert all(result == ('voided', 'voided', 2, True, 'E_VERSION_CONFLICT', False)
                       for result in results.values()), results
        finally:
            await matrix.close()
    anyio.run(witness)
