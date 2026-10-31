"""Refund, remaining lines, recovery and printing through all four transports."""
import anyio
import pytest
from tests.mcp_matrix_support import Matrix
COMMANDS = frozenset(('pos refund', 'pos refund lines', 'pos refund show', 'pos refund receipt'))
SURFACES = ('python', 'cli', 'http', 'mcp')

@pytest.mark.timeout(1200)
def test_refund_surfaces(root, tmp_path):
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
                total = (await call(surface, 'pos sale preview', cart))['total']['amount']
                sold = await call(surface, 'pos sale complete', dict(cart, ticket='refund-parity-sale', expected_total=total,
                    tender={'payment_method': 'Cash'}))
                source = await call(surface, 'pos refund lines', {'sale': sold['sales_receipt']['name']})
                body = {'operation_key': 'refund-parity', 'sale': sold['id'], 'lines': [{
                    'source_line': source['lines'][0]['source_line'], 'quantity': '1'}]}
                refunded = await call(surface, 'pos refund', body)
                replay = await call(surface, 'pos refund', body)
                shown = await call(surface, 'pos refund show', {'refund': body['operation_key']})
                receipt = await call(surface, 'pos refund receipt', {'sale': refunded['id']})
                left = await call(surface, 'pos refund lines', {'sale': sold['id']})
                results[surface] = (refunded['total'], shown['total'], receipt['kind'], replay['replayed'],
                    left['lines'][0]['available_quantity'])
            assert len(results) == 4
            assert all(r == results['python'] for r in results.values())
            assert results['python'][-3:] == ('refund', True, '1')
        finally:
            await matrix.close()
    anyio.run(witness)
