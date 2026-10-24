"""R121: the accountant review commands through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. The owner posts a 300.00 office expense, designates an
external accountant and ends it, exports an accountant's copy through 2026-09-30 and opens it as a
separate company (as the accountant's Bookflow would). In the copy the accountant reclassifies 100.00
of the expense to repairs, posts an entry to Opening Balance Equity and marks it reviewed, marks the books
reviewed through 2026-09-30 and writes the change file. The owner previews and imports it, reads the
checklist and the changes report, then exports a second copy and cancels it. Every surface must agree.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset((
    'accountant add', 'accountant list', 'accountant mark-entry', 'accountant reclassify', 'accountant remove',
    'accountant reviewed-through', 'accountant-copy cancel', 'accountant-copy changes', 'accountant-copy export',
    'accountant-copy import', 'accountant-copy preview', 'accountant-copy status', 'report accountant-changes',
    'report accountant-review'))
THROUGH = '2026-09-30'


@pytest.mark.timeout(900)
def test_the_same_accountant_review_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                found = lambda name: str(next(matrix.roots[surface].rglob(name)))  # noqa: E731
                account = lambda name, kind: call('account create', {'name': name, 'type': kind})  # noqa: E731
                office, repairs = (await account('AS Office', 'expense'))['id'], (await account('AS Repairs', 'expense'))['id']
                bank = (await account('AS Bank', 'bank'))['id']
                spend = await call('journal post', {'date': '2026-09-15', 'memo': 'Office', 'lines': [
                    {'account': office, 'side': 'debit', 'amount': '300.00'}, {'account': bank, 'side': 'credit', 'amount': '300.00'}]})
                await call('user add', {'username': 'as-cpa', 'company': matrix.company, 'role': 'standard',
                                        'password': 'pw-as-cpa-123456'})
                added = await call('accountant add', {'user': 'as-cpa', 'through': THROUGH, 'note': 'Q3'})
                listed = await call('accountant list', {})
                removed = await call('accountant remove', {'user': 'as-cpa'})
                out = await call('accountant-copy export', {'through': THROUGH})
                status = await call('accountant-copy status', {})
                copy = (await call('company restore', {'archive': found(out['file_name']), 'as_copy': True,
                                                       'name': 'AS Accountant copy'}))['company_id']
                moved = await call('accountant reclassify', {'transaction': spend['id'], 'from_account': 'AS Office',
                                                             'to_account': 'AS Repairs', 'amount': '100.00'}, company=copy)
                equity = await call('journal post', {'date': '2026-09-20', 'memo': 'Opening fix', 'lines': [
                    {'account': 'Opening Balance Equity', 'side': 'debit', 'amount': '25.00'},
                    {'account': bank, 'side': 'credit', 'amount': '25.00'}]}, company=copy)
                marked = await call('accountant mark-entry', {'transaction': equity['id'], 'note': 'Checked'}, company=copy)
                through = await call('accountant reviewed-through', {'through': THROUGH}, company=copy)
                changes = await call('accountant-copy changes', {}, company=copy)
                path = found(changes['file_name'])
                seen = await call('accountant-copy preview', {'file': path})
                taken = await call('accountant-copy import', {'file': path})
                checklist = await call('report accountant-review', {'as_of': THROUGH})
                report = await call('report accountant-changes', {})
                again = await call('accountant-copy export', {'through': THROUGH})
                cancelled = await call('accountant-copy cancel', {}, reason='Not needed')
                results[surface] = (added, listed, removed, out, status, moved, marked, through, changes, seen, taken,
                                    checklist, report, again, cancelled)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (added, listed, removed, out, status, moved, marked, through, changes, seen, taken, checklist, report,
                  again, cancelled) in results.items():
        assert added['active'] and not removed['active'] and [a['username'] for a in listed['accountants']] == ['as-cpa'], surface
        assert out['status'] == 'out' and status['locked_through'] == THROUGH, surface
        assert moved['date'] == '2026-09-15' and marked['flags'] == ['opening_balance_equity'], surface
        assert through['through'] == THROUGH and changes['counts']['new'] == 4, surface
        assert seen['counts'] == {'new': 4, 'imported': 0, 'refused': 0}, surface
        assert len(taken['imported']) == 4 and taken['status'] == 'returned', surface
        assert checklist['reviewed_through']['through'] == THROUGH and report['reclassifications'] == 1, surface
        assert again['status'] == 'out' and cancelled['status'] == 'cancelled', surface
        shapes.add(repr(([c['kind'] for c in seen['changes']], [i['key'] for i in checklist['items']],
                         report['journal_entries'], report['review_marks'])))
    assert len(shapes) == 1
