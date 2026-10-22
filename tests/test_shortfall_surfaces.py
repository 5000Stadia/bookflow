"""R101 increment 5: `sales-order purchase-order` and `work-order purchase-order` through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy, raises DEMO-SO-1 past what is on hand, orders the shortfall
from the valve's preferred vendor, and orders spares for the stocked work order by hand.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('sales-order purchase-order', 'work-order purchase-order'))


@pytest.mark.timeout(900)
def test_the_same_shortfall_purchase_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                order = await matrix.call(surface, 'sales-order create', {'customer': 'Riverside Apartments',
                    'date': '2026-10-22', 'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '40'}]})
                made = await matrix.call(surface, 'sales-order purchase-order', {'sales_order': order['number'], 'date': '2026-10-22'})
                again = await matrix.call(surface, 'sales-order purchase-order', {'sales_order': order['number'], 'date': '2026-10-22'},
                                          rejected=True)
                vendor = made['purchase_order']['vendor_id']
                spare = await matrix.call(surface, 'work-order purchase-order', {'work_order': 'DEMO-SO-WO-1', 'date': '2026-10-22',
                    'vendor': vendor, 'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '3'}]})
                results[surface] = (made, again, spare)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (made, again, spare) in results.items():
        assert made['links'][0]['linked'] == made['purchase_order']['revision']['lines'][0]['quantity'], surface
        assert 'E_VALIDATION' in str(again), surface
        assert spare['purchase_order']['revision']['lines'][0]['quantity'] == '3' and spare['links'][0]['work_kind'] == 'work_order', surface
        shapes.add((made['purchase_order']['revision']['lines'][0]['quantity'], made['purchase_order']['total']['amount']))
    assert len(shapes) == 1
