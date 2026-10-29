"""R191: each person's Home through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy and acts as its owner. It sets the Home to the profit and loss report,
reads it back (available, with its URL), is refused a page that is not a workbench page, clears it, and reads that
no Home is set. Every surface ends with the same facts and the same refusal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('user home set', 'user home show', 'user home clear'))


@pytest.mark.timeout(900)
def test_the_same_home_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            company = matrix.company
            results = {}
            for surface in matrix.documents:
                call = matrix.call
                done = await call(surface, 'user home set', {'page': 'report/profit-and-loss', 'company': company})
                shown = await call(surface, 'user home show', {})
                refused = await call(surface, 'user home set', {'page': 'no-such-page', 'company': company},
                                     rejected=True)
                cleared = await call(surface, 'user home clear', {})
                after = await call(surface, 'user home show', {})
                results[surface] = (
                    (done['changed'], done['home']['path'], done['home']['available']),
                    (shown['home']['url'], shown['home']['available'], shown['home']['version']),
                    refused.get('code') or refused.get('error', {}).get('code'),
                    (cleared['changed'], cleared['home']),
                    after['home'],
                )
            first = results['python']
            assert first[0] == (True, 'report/profit-and-loss', True)
            assert first[1] == (f'/c/{company}/report/profit-and-loss', True, 1)
            assert first[2] == 'E_VALIDATION' and first[3] == (True, None) and first[4] is None
            assert all(value == first for value in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)
