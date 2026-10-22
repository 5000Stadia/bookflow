"""R101 increment 4: `sales-order invoice` and `sales-order billing` through Python, the CLI, HTTP and MCP.

Each surface invoices the demo's DEMO-SO-1 (8 valves still to ship, a visit) by its default -- what
can ship -- replays the same key, and reads the order's billing. Every surface must post the same invoice.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('sales-order invoice', 'sales-order billing'))


@pytest.mark.timeout(900)
def test_the_same_order_invoice_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                order = await matrix.call(surface, 'sales-order show', {'sales_order': 'DEMO-SO-1'})
                body = {'sales_order': 'DEMO-SO-1', 'expected_version': order['version'],
                        'conversion_key': 'ship-' + surface, 'date': '2026-10-22'}
                preview = await matrix.call(surface, 'sales-order invoice', body, dry_run=True)
                invoice = await matrix.call(surface, 'sales-order invoice', body)
                replay = await matrix.call(surface, 'sales-order invoice', body)
                billing = await matrix.call(surface, 'sales-order billing', {'sales_order': 'DEMO-SO-1'})
                results[surface] = (preview, invoice, replay, billing)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, invoice, replay, billing) in results.items():
        assert preview['dry_run'] and preview['order_tax'] is not None, surface
        quantities = sorted(line['quantity'] for line in invoice['revision']['lines'])
        assert quantities == ['1', '8'], surface
        assert replay['id'] == invoice['id'] and replay['idempotent_replay'], surface
        assert all(line['remaining_net_minor_units'] == 0 for line in billing['lines']), surface
        shapes.add((invoice['total']['amount'], invoice['order_tax']['difference']['amount'], tuple(quantities)))
    assert len(shapes) == 1
