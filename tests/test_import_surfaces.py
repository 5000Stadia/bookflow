"""R107 increments 0 to 2: imports, batches and saved mappings through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It lists the kinds and a template, checks and previews a customer file (a
semicolon file with a parenthesized amount and an unknown term), decides the term once and remembers it, runs it, reads
it back through show, query, rows and the error file, reads its batch, saves the mapping, runs next month's file from
it in two calls (criterion 3) with one junk row refused under `partial`, plans and makes the first run's reversal, and
cancels a further previewed run. Every surface must end with the same counts, the same refusal and the same reversal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('import kinds', 'import template', 'import preview', 'import run', 'import show', 'import query',
                      'import rows', 'import errors', 'import reverse', 'import cancel', 'batch show', 'batch query',
                      'import check', 'import decide', 'import-mapping save', 'import-mapping show',
                      'import-mapping history', 'import-mapping query', 'import-mapping match',
                      'import-mapping deactivate'))
MONTH2 = 'Customer;E-mail;Terms;Credit Limit\nSurface One;one@x.test;Net 30;(1.234,50)\nSurface Four;;Net 99;1,00\n' \
         'Surface Five;;Net 15;12x\n'
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
                checked = await call(surface, 'import check', {'kind': 'customer', 'content': FILE})
                preview = await call(surface, 'import preview', {'kind': 'customer', 'content': FILE,
                                                                 'source': 'Surface file'})
                decided = await call(surface, 'import decide', {'run': preview['run']['id'],
                                                                'map': ['Net 99=Net 30'], 'remember': True})
                run = await call(surface, 'import run', {'run': preview['run']['id']})
                saved = await call(surface, 'import-mapping show', {'source': 'Surface file'})
                history = await call(surface, 'import-mapping history', {'source': 'Surface file'})
                sources = await call(surface, 'import-mapping query', {'kind': 'customer'})
                matched = await call(surface, 'import-mapping match', {'kind': 'customer', 'content': MONTH2})
                month2 = await call(surface, 'import preview', {'kind': 'customer', 'content': MONTH2,
                                                                'partial': True})
                ran2 = await call(surface, 'import run', {'run': month2['run']['id']})
                shown = await call(surface, 'import show', {'run': preview['run']['id']})
                listed = await call(surface, 'import query', {'kind': 'customer'})
                rows = await call(surface, 'import rows', {'run': month2['run']['id'], 'status': 'refused'})
                errors = await call(surface, 'import errors', {'run': month2['run']['id']})
                batch = await call(surface, 'batch show', {'batch': run['run']['batch']['id']})
                batches = await call(surface, 'batch query', {'kind': 'import'})
                plan = await call(surface, 'import reverse', {'run': preview['run']['id']}, dry_run=True)
                undone = await call(surface, 'import reverse', {'run': preview['run']['id'],
                                                                'fingerprint': plan['fingerprint']})
                other = await call(surface, 'import preview', {'kind': 'customer', 'content': FILE})
                cancelled = await call(surface, 'import cancel', {'run': other['run']['id']})
                refused = await call(surface, 'import run', {'run': other['run']['id']}, rejected=True)
                retired = await call(surface, 'import-mapping deactivate', {'source': 'Surface file'})
                results[surface] = dict(kinds=kinds, template=template, preview=preview, run=run, shown=shown,
                                        checked=checked, decided=decided, saved=saved, month2=month2, ran2=ran2,
                                        history=history, sources=sources, matched=matched, retired=retired,
                                        listed=listed, rows=rows, errors=errors, batch=batch, batches=batches,
                                        plan=plan, undone=undone, cancelled=cancelled, refused=refused)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert 'customer' in {k['kind'] for k in r['kinds']['kinds']} and r['template']['header'][0] == 'name'
        assert r['checked']['ok'] is True and [u['value'] for u in r['checked']['unknown_references']] == ['Net 99']
        assert r['preview']['counts'] == {'create': 2, 'waiting': 1}, surface
        assert r['preview']['mapping']['formats']['decimal'] == ',' and r['preview']['run']['state'] == 'waiting'
        assert r['decided']['run']['state'] == 'previewed' and r['decided']['decisions'] == []
        assert r['run']['counts'] == {'created': 3, 'not_run': 0} and r['run']['run']['state'] == 'done'
        assert r['saved']['mapping']['value_maps'] == {'terms_id': {'net 99': 'Net 30'}}, surface
        assert [v['version'] for v in r['history']['versions']] == [1]
        assert [x['name'] for x in r['sources']['items']] == ['Surface file']
        assert [(c['source'], c['match']) for c in r['matched']['candidates']] == [('Surface file', 'exact')]
        assert r['retired']['source']['active'] is False, surface
        # Next month, two calls: the saved mapping matches exactly and Net 99 is not asked again.
        assert r['month2']['matched']['match'] == 'exact' and r['month2']['decisions'] == []
        assert r['month2']['counts'] == {'already_in': 1, 'create': 1, 'refused': 1}, surface
        assert r['ran2']['counts'] == {'already_in': 1, 'created': 1, 'refused': 1, 'not_run': 0}
        assert r['shown']['counts'] == r['run']['counts'] and r['listed']['items'][0]['id'] == r['month2']['run']['id']
        assert [(x['row'], x['code'], x['column']) for x in r['rows']['items']] == [(3, 'bad_value', 'Credit Limit')]
        assert r['errors']['rows'] == 1 and '12x' in r['errors']['content'], surface
        assert r['batch']['batch']['counts'] == {'created': 3} and r['batch']['batch']['reversible'] is True
        assert r['batches']['items'][1]['id'] == r['batch']['batch']['id'], surface
        assert r['plan']['remaining'] == 3 and r['plan']['blockers'] == [] and r['plan']['state'] == 'live'
        assert r['undone']['state'] == 'reversed' and r['undone']['reversed_now'] == 3, surface
        assert r['cancelled']['run']['state'] == 'cancelled' and 'E_VALIDATION' in str(r['refused']), surface
        shapes.add((tuple(sorted(r['run']['counts'].items())), r['plan']['remaining'], r['undone']['reversed_now'],
                    r['errors']['content']))
    assert len(shapes) == 1
