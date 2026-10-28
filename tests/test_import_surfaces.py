"""R107 increments 0 and 1: imports and batches through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It lists the kinds and a template, previews a customer file with one bad
row (a semicolon file with a parenthesized amount and an unknown term), runs it, reads it back through show, query,
rows and the error file, reads its batch, plans and makes its reversal, and cancels a second previewed run. Every
surface must end with the same counts, the same refusal and the same reversal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('import kinds', 'import template', 'import preview', 'import run', 'import show', 'import query',
                      'import rows', 'import errors', 'import reverse', 'import cancel', 'batch show', 'batch query'))
FILE = 'Customer;E-mail;Terms;Credit Limit\nSurface One;one@x.test;Net 30;(1.234,50)\nSurface Two;two@x.test;Net 99;10\n' \
       'Surface Three;;Net 15;2.000,00\n'


@pytest.mark.timeout(900)
def test_the_same_import_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                kinds = await call(surface, 'import kinds', {})
                template = await call(surface, 'import template', {'kind': 'customer'})
                preview = await call(surface, 'import preview', {'kind': 'customer', 'content': FILE,
                                                                 'source': 'Surface file'})
                run = await call(surface, 'import run', {'run': preview['run']['id']})
                shown = await call(surface, 'import show', {'run': preview['run']['id']})
                listed = await call(surface, 'import query', {'kind': 'customer'})
                rows = await call(surface, 'import rows', {'run': preview['run']['id'], 'status': 'refused'})
                errors = await call(surface, 'import errors', {'run': preview['run']['id']})
                batch = await call(surface, 'batch show', {'batch': run['run']['batch']['id']})
                batches = await call(surface, 'batch query', {'kind': 'import'})
                plan = await call(surface, 'import reverse', {'run': preview['run']['id']}, dry_run=True)
                undone = await call(surface, 'import reverse', {'run': preview['run']['id'],
                                                                'fingerprint': plan['fingerprint']})
                other = await call(surface, 'import preview', {'kind': 'customer', 'content': FILE})
                cancelled = await call(surface, 'import cancel', {'run': other['run']['id']})
                refused = await call(surface, 'import run', {'run': other['run']['id']}, rejected=True)
                results[surface] = dict(kinds=kinds, template=template, preview=preview, run=run, shown=shown,
                                        listed=listed, rows=rows, errors=errors, batch=batch, batches=batches,
                                        plan=plan, undone=undone, cancelled=cancelled, refused=refused)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert 'customer' in {k['kind'] for k in r['kinds']['kinds']} and r['template']['header'][0] == 'name'
        assert r['preview']['counts'] == {'create': 2, 'refused': 1}, surface
        assert r['preview']['mapping']['formats']['decimal'] == ',' and r['preview']['run']['state'] == 'previewed'
        assert r['run']['counts'] == {'created': 2, 'refused': 1, 'not_run': 0} and r['run']['run']['state'] == 'done'
        assert r['shown']['counts'] == r['run']['counts'] and r['listed']['items'][0]['id'] == r['preview']['run']['id']
        assert [(x['row'], x['code'], x['column']) for x in r['rows']['items']] == [(2, 'unknown_reference', 'Terms')]
        assert r['errors']['rows'] == 1 and 'Net 99' in r['errors']['content'], surface
        assert r['batch']['batch']['counts'] == {'created': 2} and r['batch']['batch']['reversible'] is True
        assert r['batches']['items'][0]['id'] == r['batch']['batch']['id'], surface
        assert r['plan']['remaining'] == 2 and r['plan']['blockers'] == [] and r['plan']['state'] == 'live'
        assert r['undone']['state'] == 'reversed' and r['undone']['reversed_now'] == 2, surface
        assert r['cancelled']['run']['state'] == 'cancelled' and 'E_VALIDATION' in str(r['refused']), surface
        shapes.add((tuple(sorted(r['run']['counts'].items())), r['plan']['remaining'], r['undone']['reversed_now'],
                    r['errors']['content']))
    assert len(shapes) == 1
