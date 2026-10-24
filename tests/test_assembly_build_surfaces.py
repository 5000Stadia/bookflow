"""R103: the same assembly builds through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, previews and builds one Two-Valve Kit, edits it to two,
marks a build far beyond the valves on hand pending, edits that pending build down and finalizes it,
voids it, deletes the first, and reads the builds back as a list and one by one.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('assembly build', 'assembly update', 'assembly finalize', 'assembly void',
                      'assembly delete', 'assembly show', 'assembly list'))
KIT = 'Two-Valve Kit'


@pytest.mark.timeout(900)
def test_the_same_assembly_builds_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                body = {'item': KIT, 'quantity': '1', 'date': DEMO_AS_OF, 'memo': 'Witness kit'}
                preview = await matrix.call(surface, 'assembly build', body, dry_run=True)
                built = await matrix.call(surface, 'assembly build', body)
                edited = await matrix.call(surface, 'assembly update', {
                    'build': built['build_id'], 'expected_version': 1, 'quantity': '2'})
                pending = await matrix.call(surface, 'assembly build', {
                    'item': KIT, 'quantity': '100000', 'date': DEMO_AS_OF, 'pending': True})
                report = await matrix.call(surface, 'assembly list', {'status': 'pending'})
                await matrix.call(surface, 'assembly update', {'build': pending['build_id'], 'quantity': '1'})
                final = await matrix.call(surface, 'assembly finalize', {'build': pending['build_id']})
                voided = await matrix.call(surface, 'assembly void', {'build': pending['build_id']})
                deleted = await matrix.call(surface, 'assembly delete', {'build': built['build_id']})
                shown = await matrix.call(surface, 'assembly show', {'build': built['build_id']})
                listed = await matrix.call(surface, 'assembly list', {'status': 'all'})
                results[surface] = (preview, built, edited, pending, report, final, voided, deleted, shown, listed)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, built, edited, pending, report, final, voided, deleted, shown, listed) in results.items():
        assert preview['status'] == 'will_build' and preview['build_id'] is None, surface
        assert built['status'] == 'built' and built['assembly_cost'] == preview['assembly_cost'], surface
        assert edited['version'] == 2 and edited['quantity'] == '2', surface
        assert pending['status'] == 'pending' and pending['shortfall'] and report['count'] == 1, surface
        assert final['status'] == 'built' and final['number'], surface
        assert voided['status'] == 'voided' and deleted['status'] == 'deleted', surface
        assert shown['status'] == 'deleted' and len(shown['history']) == 3, surface
        assert {row['status'] for row in listed['rows']} == {'voided', 'deleted'}, surface
        shapes.add((preview['assembly_cost']['minor_units'], edited['assembly_cost']['minor_units'],
                    preview['max_buildable'], final['assembly_cost']['minor_units']))
    assert len(shapes) == 1, shapes
