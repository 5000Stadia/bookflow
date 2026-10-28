"""R107 increment 6: a grid edit through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It lists the item columns and the grid, previews a 5 percent price rise on
three items rounded to 0.05 (and a bad value refused), applies it, reads it back by show and query, then reverses it:
the dry run, the reversal refused once an item was edited again, and `skip_changed` restoring the other two. Every
surface must end with the same diff, outcomes and prices.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('bulk-edit columns', 'bulk-edit grid', 'bulk-edit preview', 'bulk-edit apply',
                      'bulk-edit reverse', 'bulk-edit show', 'bulk-edit query'))


@pytest.mark.timeout(900)
def test_the_same_grid_edit_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                cols = await call(surface, 'bulk-edit columns', {'kind': 'item'})
                grid = await call(surface, 'bulk-edit grid', {'kind': 'item', 'columns': ['price'], 'limit': 200})
                priced = [r['label'] for r in grid['records'] if r['values']['price'] not in ('', '0', '0.00')][:3]
                bad = await call(surface, 'bulk-edit preview', {
                    'kind': 'item', 'records': priced, 'operations': [{'op': 'set', 'field': 'price',
                                                                       'value': '12.5x'}]}, rejected=True)
                ask = {'kind': 'item', 'records': priced,
                       'operations': [{'op': 'percent', 'field': 'price', 'by': '5', 'round': '0.05'}]}
                preview = await call(surface, 'bulk-edit preview', ask)
                run = preview['run']['id']
                applied = await call(surface, 'bulk-edit apply', {'run': run})
                shown = await call(surface, 'bulk-edit show', {'run': run})
                listed = await call(surface, 'bulk-edit query', {'kind': 'item'})
                plan = await call(surface, 'bulk-edit reverse', {'run': run}, dry_run=True)
                await call(surface, 'item update', {'item': priced[0], 'price': '1.00'})
                blocked = await call(surface, 'bulk-edit reverse', {'run': run}, rejected=True)
                partly = await call(surface, 'bulk-edit reverse', {'run': run, 'skip_changed': True})
                after = await call(surface, 'bulk-edit grid', {'kind': 'item', 'records': priced,
                                                               'columns': ['price']})
                results[surface] = dict(cols=cols, grid=grid, priced=priced, bad=bad, preview=preview,
                                        applied=applied, shown=shown, listed=listed, plan=plan, blocked=blocked,
                                        partly=partly, after=after)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert any(c['field'] == 'price' and c['type'] == 'money' for c in r['cols']['columns']), surface
        assert len(r['priced']) == 3 and 'E_VALIDATION' in str(r['bad']), surface
        diff = [(row['label'], row['changes'][0]['before'], row['changes'][0]['after']) for row in r['preview']['rows']]
        assert r['preview']['counts']['change'] == len(diff) and r['applied']['run']['state'] == 'applied', surface
        assert {row['outcome'] for row in r['shown']['rows']} == {'updated'}, surface
        assert r['listed']['items'][0]['id'] == r['preview']['run']['id'], surface
        assert len(r['plan']['steps']) == len(diff) and r['plan']['fingerprint'], surface
        assert 'E_BATCH_BLOCKED' in str(r['blocked']) and r['priced'][0] in str(r['blocked']), surface
        assert r['partly']['state'] == 'reversed_partly' and len(r['partly']['skipped']) == 1, surface
        before = {row['label']: row['changes'][0]['before'] for row in r['preview']['rows']}
        restored = {row['label']: row['values']['price'] for row in r['after']['records']}
        assert restored[r['priced'][0]] == '1.00', surface
        assert all(restored[label] == before[label] for label in r['priced'][1:] if label in before), surface
        shapes.add((tuple(diff), tuple(sorted(r['preview']['totals']['price'].items())), tuple(sorted(restored.items()))))
    assert len(shapes) == 1
