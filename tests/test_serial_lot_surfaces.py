"""R104 increment 4: serial and lot numbers through Python, the CLI, HTTP and MCP, the shared scenario extended.

Each surface gets an identical demo copy (water heaters WH-1001 on the van and WH-1002 in the warehouse, sealant
lots S-2611 and S-2702). It sells WH-1001 from the van, takes the number off WH-1002 and gives the unit another
(retried under one idempotency key, a lost response), updates a lot and a serial number, and reads the numbers
back through every show, list and report. Every surface must end with the same numbers by place, the same
history and the same valuation.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('inventory assign', 'lot list', 'lot show', 'lot update', 'report lot-trace',
                      'report serial-lot-history', 'report serial-lot-stock', 'serial-number list',
                      'serial-number show', 'serial-number update'))


@pytest.mark.timeout(900)
def test_the_same_numbers_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                customers = await call(surface, 'customer query', {'limit': 1})
                customer = customers['items'][0]['id']
                sold = await call(surface, 'invoice post', {'customer': customer, 'date': DEMO_AS_OF, 'site': 'Van 1',
                                                            'lines': [{'item': 'Water Heater', 'quantity': '1',
                                                                       'serial_numbers': ['WH-1001']}]})
                twice = await call(surface, 'invoice post', {'customer': customer, 'date': DEMO_AS_OF, 'lines': [
                    {'item': 'Water Heater', 'quantity': '1', 'serial_numbers': ['WH-1001']}]}, rejected=True)
                off = {'item': 'Water Heater', 'serial_numbers': ['WH-1002'], 'site': 'Warehouse', 'date': DEMO_AS_OF,
                       'direction': 'unassign'}
                preview = await call(surface, 'inventory assign', off, dry_run=True)
                unassigned = await call(surface, 'inventory assign', off)
                on = {'item': 'Water Heater', 'serial_numbers': ['WH-2002'], 'site': 'Warehouse', 'date': DEMO_AS_OF}
                first = await call(surface, 'inventory assign', on, idempotency_key='label-a')
                again = await call(surface, 'inventory assign', on, idempotency_key='label-a')
                lot = await call(surface, 'lot show', {'number': 'S-2611', 'as_of': DEMO_AS_OF})
                lot_updated = await call(surface, 'lot update', {'number': 'S-2611', 'expected_version': lot['version'],
                                                                'memo': 'Recall check: fine'})
                serial = await call(surface, 'serial-number show', {'number': 'WH-2002', 'as_of': DEMO_AS_OF})
                serial_updated = await call(surface, 'serial-number update', {'number': 'WH-2002', 'memo': 'Warranty to 2036'})
                lots = await call(surface, 'lot list', {'expiring_by': '2027-06-30'})
                serials = await call(surface, 'serial-number list', {'in_stock': True, 'as_of': DEMO_AS_OF})
                stock = await call(surface, 'report serial-lot-stock', {'as_of': DEMO_AS_OF})
                history = await call(surface, 'report serial-lot-history', {'number': 'WH-1001'})
                trace = await call(surface, 'report lot-trace', {'number': 'S-2611'})
                valuation = await call(surface, 'report inventory-valuation', {'as_of': DEMO_AS_OF, 'limit': 200})
                results[surface] = dict(sold=sold, twice=twice, preview=preview, unassigned=unassigned, first=first,
                                        again=again, lot=lot, lot_updated=lot_updated, serial=serial,
                                        serial_updated=serial_updated, lots=lots, serials=serials, stock=stock,
                                        history=history, trace=trace,
                                        valuation=valuation['totals']['asset_value']['minor_units'])
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert 'already left stock' in str(r['twice']), surface
        assert r['preview']['status'] == 'will_assign' and r['unassigned']['status'] == 'assigned', surface
        assert r['first']['id'] == r['again']['id'], surface                  # one assignment, retried
        assert r['lot']['on_hand'] == '4' and r['lot']['expiry_date'] == '2027-03-31', surface
        assert r['lot_updated']['memo'] == 'Recall check: fine', surface
        assert r['serial']['on_hand'] == '1' and r['serial_updated']['memo'] == 'Warranty to 2036', surface
        assert [n['number'] for n in r['lots']['numbers']] == ['S-2611'], surface
        assert [n['number'] for n in r['serials']['numbers']] == ['WH-2002'], surface
        rows = sorted((row['item_name'], row['number'] or '', row['site_name'], row['quantity']) for row in r['stock']['rows'])
        assert ('Water Heater', 'WH-2002', 'Warehouse', '1') in rows and ('Pipe Sealant', 'S-2611', 'Van 1', '2') in rows, surface
        assert [row['balance'] for row in r['history']['rows']] == ['1', '0', '1', '0'], surface
        assert r['trace']['vendors'] and r['trace']['rows'][0]['document'].startswith('bill '), surface
        shapes.add((repr(rows), repr([(row['document_type'], row['site_name'], row['balance'])
                                      for row in r['history']['rows']]), r['valuation']))
    assert len(shapes) == 1
