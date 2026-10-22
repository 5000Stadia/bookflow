"""R102: `report open-sales-orders` through Python, the CLI, HTTP and MCP, and stock status on the demo.

Each surface reads an identical copy of the demo, whose DEMO-SO-1 orders ten valves (two cancelled)
and a visit and whose DEMO-SO-WO-1 needs two valves, by customer and by item; every surface must
give the same rows. Stock status on the demo is timed and its committed figure checked.
"""
import time

import anyio
import pytest

import bookflow
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('report open-sales-orders',))
VALVE = 'Brass Shutoff Valve'


@pytest.mark.timeout(900)
def test_the_same_open_orders_report_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            return {surface: (await matrix.call(surface, 'report open-sales-orders', {}),
                              await matrix.call(surface, 'report open-sales-orders', {'by': 'item', 'limit': 10}))
                    for surface in SURFACES}
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (by_customer, by_item) in results.items():
        valve = next(row for row in by_item['rows'] if row['item_name'] == VALVE)
        assert (valve['number'], valve['ordered'], valve['cancelled'], valve['backordered']) == ('DEMO-SO-1', '10', '2', '8'), surface
        assert valve['quantity_available'] is not None and valve['stocked'], surface
        assert by_customer['totals']['lines'] == 2 and by_customer['by'] == 'customer', surface
        shapes.add((tuple((row['number'], row['item_name'], row['backordered'], row['open_amount']['amount'])
                          for row in by_customer['rows']), valve['quantity_on_hand'], valve['quantity_available']))
    assert len(shapes) == 1


def test_stock_status_on_the_demo_counts_its_order_and_work_order(client):
    company = 'Demo Plumbing Co'
    started = time.perf_counter()
    report = client.run('report stock-status', {'as_of': '2099-12-31', 'limit': 200}, company=company)
    seconds = time.perf_counter() - started
    print(f'\nstock-status on the demo: {seconds:.3f}s')
    row = next(row for row in report['rows'] if row['item_name'] == VALVE)
    # DEMO-SO-1 still wants 8 valves, DEMO-SO-WO-1 needs 2.
    assert row['quantity_committed'] == '10'
    assert int(row['quantity_available']) == int(row['quantity_on_hand']) - 10
    assert seconds < 30
