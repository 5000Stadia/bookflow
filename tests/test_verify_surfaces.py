"""R180 stage 2: `company verify` through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It verifies the whole company, then Checking alone by name, then an
account that does not exist. Every surface must report the same accounts, the same outcomes and counts, and the
same refusal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('company verify',))


@pytest.mark.timeout(900)
def test_the_same_verification_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                whole = await call(surface, 'company verify', {})
                one = await call(surface, 'company verify', {'account': 'Checking'})
                unknown = await call(surface, 'company verify', {'account': 'No such account'}, rejected=True)
                results[surface] = dict(whole=whole, one=one, unknown=unknown)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['whole']['ok'] and r['whole']['accounts'], surface
        assert all(v['outcome'] in ('proved', 'unsupported') for v in r['whole']['accounts']), surface
        assert [v['name'] for v in r['one']['accounts']] == ['Checking'], surface
        assert r['one']['accounts'][0]['outcome'] == 'proved' and r['one']['accounts'][0]['documents'] > 0, surface
        assert 'E_RECORD_NOT_FOUND' in str(r['unknown']), surface
        shapes.add(repr([(v['name'], v['outcome'], v['check'], v['documents'], v['effect_versions'], v['watermark'])
                         for v in r['whole']['accounts']]))
    assert len(shapes) == 1
