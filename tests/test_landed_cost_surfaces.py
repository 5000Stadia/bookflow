"""R104 increment 6: landed cost through Python, the CLI, HTTP and MCP, the shared scenario extended.

Each surface gets an identical demo copy (copper pipe on DEMO-PIPE-1 and DEMO-PIPE-2, five sold, the DEMO-FREIGHT-1
freight already spread as DEMO-LC-1). It enters a second freight bill on Landed Cost Clearing, finds it with
`landed-cost charges`, previews and posts its allocation by value over both pipe receipts (retried under one
idempotency key, a lost response), reads it back through show and query, voids it and posts it again by quantity.
Every surface must end with the same shares, the same true-ups and the same valuation.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('landed-cost setup', 'landed-cost charges', 'landed-cost post', 'landed-cost void',
                      'landed-cost show', 'landed-cost query'))


@pytest.mark.timeout(900)
def test_the_same_landed_cost_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                setup = await call(surface, 'landed-cost setup', {})
                vendors = await call(surface, 'vendor query', {'limit': 1})
                freight = await call(surface, 'bill post', {'vendor': vendors['items'][0]['id'], 'date': DEMO_AS_OF,
                                                            'expenses': [{'account': setup['account_id'],
                                                                          'amount': '12.00'}]})
                charges = await call(surface, 'landed-cost charges', {'as_of': DEMO_AS_OF})
                payload = {'charges': [{'charge': charges['rows'][0]['charge']}], 'basis': 'value',
                           'targets': [{'document': 'DEMO-PIPE-1'}, {'document': 'DEMO-PIPE-2'}]}
                preview = await call(surface, 'landed-cost post', payload, dry_run=True)
                first = await call(surface, 'landed-cost post', payload, idempotency_key='freight-a')
                again = await call(surface, 'landed-cost post', payload, idempotency_key='freight-a')
                over = await call(surface, 'landed-cost post', payload, rejected=True)
                shown = await call(surface, 'landed-cost show', {'allocation': first['allocation']['number']})
                listed = await call(surface, 'landed-cost query', {})
                voided = await call(surface, 'landed-cost void', {'allocation': first['allocation']['number']})
                payload['basis'] = 'quantity'
                second = await call(surface, 'landed-cost post', payload)
                valuation = await call(surface, 'report inventory-valuation', {'as_of': DEMO_AS_OF, 'limit': 200})
                results[surface] = dict(setup=setup, freight=freight, charges=charges, preview=preview, first=first,
                                        again=again, over=over, shown=shown, listed=listed, voided=voided,
                                        second=second, valuation=valuation['totals']['asset_value']['minor_units'])
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert not r['setup']['created'], surface                              # the demo made it already
        assert [row['unallocated']['minor_units'] for row in r['charges']['rows']] == [1200], surface
        assert r['first']['allocation']['number'] == r['again']['allocation']['number'], surface
        assert 'nothing of that charge is left' in str(r['over']), surface
        shares = [t['amount']['minor_units'] for t in r['first']['allocation']['targets']]
        assert shares == [400, 800] == [t['amount']['minor_units'] for t in r['preview']['allocation']['targets']], surface
        assert r['shown']['allocation']['status'] == 'posted' and r['voided']['allocation']['status'] == 'voided', surface
        assert [row['number'] for row in r['listed']['rows']][:2] == [r['first']['allocation']['number'], 'DEMO-LC-1'], surface
        second = r['second']['allocation']
        shapes.add((repr([t['amount']['minor_units'] for t in second['targets']]),
                    repr([(t['effective_date'], t['delta']['minor_units']) for t in second['true_ups']]), r['valuation']))
    assert len(shapes) == 1
