"""R104 increment 1: inventory sites, transfers and Quantity on Hand by Site through Python, the CLI, HTTP and MCP.

The first shared scenario. Each surface gets an identical demo copy (Warehouse, Van 1 and a service kit
already on the van) and interleaves a receipt at the van, a transfer, a van sale and a retry under one
idempotency key, then corrects and voids the transfer. Every surface must end with the same quantities
by place on every date of the scenario and the same valuation.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset((
    'inventory-site activate', 'inventory-site create', 'inventory-site deactivate', 'inventory-site list',
    'inventory-site show', 'inventory-site update', 'inventory-transfer history', 'inventory-transfer post',
    'inventory-transfer query', 'inventory-transfer show', 'inventory-transfer update', 'inventory-transfer void',
    'report quantity-by-site'))
KIT = 'Service Call Kit'
DATES = ('2026-12-02', '2026-12-03', DEMO_AS_OF)


@pytest.mark.timeout(900)
def test_the_same_sites_and_transfers_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                listed = await call(surface, 'inventory-site list', {})
                made = await call(surface, 'inventory-site create', {'name': 'Van 2', 'description': 'Second van'})
                await call(surface, 'inventory-site update', {'site': 'Van 2', 'address': '12 Depot Rd'})
                await call(surface, 'inventory-site deactivate', {'site': 'Van 2'})
                await call(surface, 'inventory-site activate', {'site': 'Van 2'})
                body = {'date': DEMO_AS_OF, 'from_site': 'Warehouse', 'to_site': 'Van 2',
                        'lines': [{'item': KIT, 'quantity': '1'}], 'memo': 'Load van 2'}
                moved = await call(surface, 'inventory-transfer post', body, idempotency_key='van-2-load')
                again = await call(surface, 'inventory-transfer post', body, idempotency_key='van-2-load')
                fixed = await call(surface, 'inventory-transfer update', {
                    'transfer': moved['number'], 'expected_version': 1, 'lines': [{'item': KIT, 'quantity': '2'}]})
                shown = await call(surface, 'inventory-transfer show', {'transfer': moved['number']})
                found = await call(surface, 'inventory-transfer query', {'site': 'Van 2'})
                history = await call(surface, 'inventory-transfer history', {'transfer': moved['number']})
                site = await call(surface, 'inventory-site show', {'site': 'Van 2'})
                busy = await call(surface, 'inventory-site deactivate', {'site': 'Van 2'}, rejected=True)
                voided = await call(surface, 'inventory-transfer void', {'transfer': moved['number'], 'expected_version': 2})
                readings = {}
                for date in DATES:
                    report = await call(surface, 'report quantity-by-site', {'as_of': date, 'limit': 200})
                    readings[date] = sorted((r['item_name'], r['site_name'], r['quantity_on_hand'],
                                             r['asset_value']['minor_units']) for r in report['rows'])
                valuation = await call(surface, 'report inventory-valuation', {'as_of': DEMO_AS_OF, 'by': 'site', 'limit': 200})
                results[surface] = dict(listed=listed, made=made, moved=moved, again=again, fixed=fixed, shown=shown,
                                        found=found, history=history, site=site, busy=busy, voided=voided,
                                        readings=readings, valuation=valuation['totals']['asset_value']['minor_units'])
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert [s['name'] for s in r['listed']['sites']] == ['Van 1', 'Warehouse'], surface
        assert r['again']['number'] == r['moved']['number'], surface               # the retry is the same transfer
        assert r['fixed']['version'] == 2 and r['shown']['lines'][0]['quantity'] == '2', surface
        assert [h['operation'] for h in r['history']['history']] == ['post', 'update'], surface
        assert r['found']['count'] == 1 and r['site']['stock'][0]['quantity_on_hand'] == '2', surface
        assert 'E_RECORD_IN_USE' in str(r['busy']), surface
        assert r['voided']['status'] == 'voided', surface
        # The van sample left from Van 1, and the other half kit went back to the warehouse before the
        # demo's van count (R104 increment 2): none is left there.
        assert r['readings']['2026-12-03'] and [row for row in r['readings']['2026-12-03']
                                                if row[0] == KIT and row[1] == 'Van 1'][0][2] == '0.5', surface
        assert not [row for row in r['readings'][DEMO_AS_OF] if row[0] == KIT and row[1] == 'Van 1'], surface
        shapes.add((repr(r['readings']), r['valuation']))
    assert len(shapes) == 1
