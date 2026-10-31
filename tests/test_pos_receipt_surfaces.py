"""A posted receipt and its voided reprint through Python, CLI, HTTP and MCP."""
import anyio
import pytest
from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('pos receipt show',))

@pytest.mark.timeout(900)
def test_receipt_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                call = matrix.call
                off = await call(surface, 'pos receipt show', {'sale':'missing'}, rejected=True)
                info = await call(surface, 'company show', {})
                await call(surface, 'company update', {'pos_enabled':True, 'expected_version':info['info_version']})
                cart = {'lines':[{'item':'Pipe Sealant', 'quantity':'2'}]}
                total = (await call(surface, 'pos sale preview', cart))['total']['amount']
                sold = await call(surface, 'pos sale complete', dict(cart, ticket='receipt-parity', expected_total=total,
                                   tender={'payment_method':'Cash'}))
                shown = await call(surface, 'pos receipt show', {'sale':sold['sales_receipt']['name']})
                await call(surface, 'pos sale void', {'sale':sold['id'], 'expected_version':1})
                void = await call(surface, 'pos receipt show', {'sale':sold['id'], 'reprint':True})
                results[surface] = (off.get('code') or off.get('error',{}).get('code'), shown['lines'],
                    shown['total'], void['total'], void['status'], void['reprint'])
            assert len(results) == 4
            assert all(r == results['python'] for r in results.values())
            assert results['python'][0] == 'E_FEATURE_DISABLED'
            assert results['python'][2] == results['python'][3]
            assert results['python'][-2:] == ('voided', True)
        finally:
            await matrix.close()
    anyio.run(witness)
