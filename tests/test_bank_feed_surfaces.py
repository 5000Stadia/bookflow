"""R108 increment 1: the bank inbox through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It previews the sample OFX statement into Checking, imports it, refuses the
same file again and an OFX file for another account number, then reads the import back through show and query and
the inbox through bank-line query. Every surface must end with the same counts, keys and refusals.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_bank_feeds import ofx, sample

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('bank-feed import', 'bank-feed show', 'bank-feed query', 'bank-line query'))


@pytest.mark.timeout(900)
def test_the_same_bank_import_through_python_cli_http_and_mcp(root, tmp_path):
    content = sample('checking-2026-09.ofx')
    other = ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001')

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                preview = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content},
                                     dry_run=True)
                done = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content})
                again = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': content},
                                   rejected=True)
                mismatch = await call(surface, 'bank-feed import', {'account': 'Checking', 'content': other},
                                      rejected=True)
                shown = await call(surface, 'bank-feed show', {'import_id': done['import_id'], 'state': 'waiting'})
                listed = await call(surface, 'bank-feed query', {'account': 'Checking'})
                inbox = await call(surface, 'bank-line query', {'account': 'Checking', 'state': 'waiting'})
                results[surface] = dict(preview=preview, done=done, again=again, mismatch=mismatch, shown=shown,
                                        listed=listed, inbox=inbox)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['preview']['import_id'] is None and r['preview']['counts'] == r['done']['counts'], surface
        assert (r['done']['counts']['new'], r['done']['counts']['matched']) == (10, 8), surface
        assert 'E_ALREADY_IMPORTED' in str(r['again']) and 'E_ACCOUNT_MISMATCH' in str(r['mismatch']), surface
        assert r['shown']['by_state'] == {'matched': 8, 'waiting': 2} and len(r['shown']['lines']) == 2
        assert r['listed']['imports'][0]['id'] == r['done']['import_id'], surface
        assert r['inbox']['counts'] == {'matched': 8, 'waiting': 2}, surface
        shapes.add((tuple(sorted(r['done']['counts'].items())), tuple(v['line_key'] for v in r['done']['lines']),
                    tuple((v['date'], v['amount'], v['state']) for v in r['inbox']['lines'])))
    assert len(shapes) == 1
