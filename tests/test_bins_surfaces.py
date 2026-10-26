"""R104 increment 5: bins and cycle counts through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy (the warehouse tracks bins A1-A3; the water heater WH-1002 sits in A1
and sealant lot S-2702 in A2; the heater is class A and the sealant class B). It adds a bin (retried under one
idempotency key, a lost response), relabels it, moves sealant between two bins, counts bin A2, reads the items
by bin, adds and updates a cycle class, classifies, reads what is due and counts the next one due. Every surface
must end with the same stock by bin, the same due list and the same valuation.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('bin activate', 'bin create', 'bin deactivate', 'bin list', 'bin show', 'bin update',
                      'cycle-class activate', 'cycle-class create', 'cycle-class deactivate', 'cycle-class list',
                      'cycle-class show', 'cycle-class update', 'cycle-count classify', 'cycle-count due',
                      'report items-by-bin'))


@pytest.mark.timeout(900)
def test_the_same_bins_and_cycle_counts_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                made = await call(surface, 'bin create', {'site': 'Warehouse', 'name': 'B1', 'barcode': 'BIN-B1'},
                                  idempotency_key='bin-b1')
                again = await call(surface, 'bin create', {'site': 'Warehouse', 'name': 'B1', 'barcode': 'BIN-B1'},
                                   idempotency_key='bin-b1')
                relabelled = await call(surface, 'bin update', {'bin': 'B1', 'expected_version': made['version'],
                                                                 'barcode': 'BIN-B1X'})
                off = await call(surface, 'bin deactivate', {'bin': 'B1'})
                on = await call(surface, 'bin activate', {'bin': 'B1'})
                shown = await call(surface, 'bin show', {'bin': 'A2', 'as_of': DEMO_AS_OF})
                listed = await call(surface, 'bin list', {'site': 'Warehouse'})
                moved = await call(surface, 'inventory-transfer post', {
                    'date': DEMO_AS_OF, 'from_site': 'Warehouse', 'to_site': 'Warehouse',
                    'lines': [{'item': 'Pipe Sealant', 'quantity': '2', 'lot': 'S-2702', 'from_bin': 'A2', 'to_bin': 'B1'}]})
                count = await call(surface, 'inventory-count create', {'site': 'Warehouse', 'date': DEMO_AS_OF,
                                                                       'bins': ['A2']})
                by_bin = await call(surface, 'report items-by-bin', {'site': 'Warehouse', 'as_of': DEMO_AS_OF})
                klass = await call(surface, 'cycle-class create', {'name': 'D', 'interval_days': 730})
                klass_updated = await call(surface, 'cycle-class update', {'cycle_class': 'D', 'interval_days': 700})
                klass_off = await call(surface, 'cycle-class deactivate', {'cycle_class': 'D'})
                klass_on = await call(surface, 'cycle-class activate', {'cycle_class': 'D'})
                klass_shown = await call(surface, 'cycle-class show', {'cycle_class': 'A'})
                classes = await call(surface, 'cycle-class list', {})
                proposal = await call(surface, 'cycle-count classify', {'date_to': DEMO_AS_OF}, dry_run=True)
                due = await call(surface, 'cycle-count due', {'site': 'Warehouse', 'as_of': DEMO_AS_OF})
                results[surface] = dict(made=made, again=again, relabelled=relabelled, off=off, on=on, shown=shown,
                                        listed=listed, moved=moved, count=count, by_bin=by_bin, klass=klass,
                                        klass_updated=klass_updated, klass_off=klass_off, klass_on=klass_on,
                                        klass_shown=klass_shown, classes=classes, proposal=proposal, due=due)
                # Undo this surface's writes that the next surface's demo copy does not share: each surface has its own.
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['made']['id'] == r['again']['id'], surface                    # one bin, retried
        assert r['relabelled']['barcode'] == 'BIN-B1X' and not r['off']['active'] and r['on']['active'], surface
        assert ('Pipe Sealant', 'S-2702', '6') in {(s['item_name'], s['lot'], s['quantity_on_hand']) for s in r['shown']['stock']}, surface
        assert {'A1', 'A2', 'A3', 'B1'} <= {b['full_name'] for b in r['listed']['bins']}, surface
        assert r['moved']['lines'][0]['from_bin'] == 'A2' and r['moved']['posts_to_ledger'] is False, surface
        assert [(l['item_name'], l['bin'], l['place'], l['expected']) for l in r['count']['lines']] == [
            ('Pipe Sealant', 'A2', 'lot S-2702', '4')], surface
        rows = sorted((row['bin'], row['item_name'], row['lot'] or '', row['serial_number'] or '', row['quantity_on_hand'])
                      for row in r['by_bin']['rows'])
        assert ('B1', 'Pipe Sealant', 'S-2702', '', '2') in rows and ('A1', 'Water Heater', '', 'WH-1002', '1') in rows, surface
        assert r['klass_updated']['interval_days'] == 700 and r['klass_shown']['item_count'] == 1, surface
        assert [c['name'] for c in r['classes']['cycle_classes']] == ['A', 'B', 'C', 'D'], surface
        assert r['proposal']['applied'] is False, surface
        assert [row['item_name'] for row in r['due']['rows']] == ['Water Heater', 'Pipe Sealant'], surface
        shapes.add((repr(rows), repr([(row['item_name'], row['cycle_class']) for row in r['due']['rows']])))
    assert len(shapes) == 1
