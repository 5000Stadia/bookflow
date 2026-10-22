"""R101: the sales-order commands through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, quotes a stocked valve and a service visit, accepts
the quote, turns it into a sales order (and replays that), takes a second order directly, revises
it, closes it, copies it, voids it, and reads the orders back with show, query and history. Every
surface must produce the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('estimate sales-order', 'sales-order copy', 'sales-order create', 'sales-order history',
                      'sales-order query', 'sales-order show', 'sales-order update', 'sales-order void'))
CUSTOMER = 'Riverside Apartments'
VALVE = 'Brass Shutoff Valve'   # the demo's inventory part
VISIT = 'Mainline Clearing'     # the demo's service item


@pytest.mark.timeout(900)
def test_the_same_orders_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                quote = await call(surface, 'estimate create', dict(customer=CUSTOMER, date='2026-10-20', title='Valves',
                                   lines=[dict(item=VALVE, quantity='4'), dict(item=VISIT, quantity='1')]))
                accepted = await call(surface, 'estimate update', dict(estimate=quote['id'], expected_version=1,
                                      status='accepted', decision_note='Signed by Jamie'))
                body = dict(estimate=quote['id'], expected_version=accepted['version'], date='2026-10-21',
                            conversion_key='so-' + surface, ship_date='2026-10-28')
                converted = await call(surface, 'estimate sales-order', body)
                replay = await call(surface, 'estimate sales-order', body)
                preview = await call(surface, 'sales-order create', dict(customer=CUSTOMER, date='2026-10-22',
                                     lines=[dict(item=VALVE, quantity='10')]), dry_run=True)
                order = await call(surface, 'sales-order create', dict(customer=CUSTOMER, date='2026-10-22',
                                   lines=[dict(item=VALVE, quantity='10'), dict(item=VISIT, quantity='1')]))
                line = order['revision']['lines'][0]['line_id']
                visit = order['revision']['lines'][1]['line_id']
                revised = await call(surface, 'sales-order update', dict(sales_order=order['number'], expected_version=1,
                                     lines=[dict(line_id=line, item=VALVE, cancelled_quantity='4'),
                                            dict(line_id=visit, item=VISIT)]))
                closed = await call(surface, 'sales-order update', dict(sales_order=order['id'], expected_version=2,
                                    status='closed'))
                copied = await call(surface, 'sales-order copy', dict(sales_order=order['id'], expected_version=3,
                                    date='2026-10-23'))
                voided = await call(surface, 'sales-order void', dict(sales_order=order['id'], expected_version=3))
                shown = await call(surface, 'sales-order show', dict(sales_order=order['id'], revision_number=2))
                history = await call(surface, 'sales-order history', dict(sales_order=order['id'], limit=10))
                found = await call(surface, 'sales-order query', dict(customer=CUSTOMER, limit=20))
                results[surface] = (converted, replay, preview, order, revised, closed, copied, voided, shown, history, found)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (converted, replay, preview, order, revised, closed, copied, voided, shown, history, found) in results.items():
        assert converted['kind'] == 'sales_order' and converted['status'] == 'open', surface
        assert converted['revision']['facts']['profile']['ship_date'] == '2026-10-28', surface
        assert replay['id'] == converted['id'] and replay['idempotent_replay'], surface
        assert preview['dry_run'] and preview['revision']['lines'][0]['order']['open'] == '10', surface
        assert order['title'] == CUSTOMER and order['order']['progress'] == 'open', surface
        assert revised['revision']['lines'][0]['order']['open'] == '6', surface
        assert closed['status'] == 'closed' and closed['order']['open_amount']['minor_units'] == 0, surface
        assert copied['status'] == 'open' and copied['revision']['lines'][0]['cancelled_quantity'] == '0', surface
        assert voided['status'] == 'voided' and voided['revision']['decision_note'] == 'Registry parity', surface
        assert shown['revision']['revision_number'] == 2 and shown['revision']['lines'][0]['cancelled_quantity'] == '4', surface
        assert [row['status'] for row in history['items']] == ['open', 'open', 'closed', 'voided'], surface
        # Active orders: the voided one is not among them.
        listed = {row['id'] for row in found['items']}
        assert {converted['id'], copied['id']} <= listed and order['id'] not in listed, surface
        shapes.add((order['number'], order['net']['amount'], revised['order']['open_amount']['amount'],
                    copied['net']['amount'], converted['net']['amount']))
    assert len(shapes) == 1
