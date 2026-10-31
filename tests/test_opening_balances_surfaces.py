"""R107 increment 7: guided opening balances through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy (its books start in 2025, so an opening as of 2024-12-31 has nothing before
it). It starts a draft with a balance and two open invoices, adds a bill, plans, posts, ties out, reads it back by show
and query, and plans the reversal (refused for want of the delete grants, naming them); a second draft is discarded. Every surface must end with the same plan
counts, tie-out checks and states.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('opening-draft create', 'opening-draft update', 'opening-draft show', 'opening-draft query',
                      'opening-draft discard', 'opening-draft plan', 'opening-draft apply', 'opening-draft tie-out',
                      'opening-draft reverse'))
AS_OF = '2024-12-31'


@pytest.mark.timeout(900)
def test_the_same_opening_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                made = await call(surface, 'opening-draft create', {
                    'as_of': AS_OF, 'given': {'receivables': '350.00'},
                    'balances': {'content': 'Account,Balance\nChecking,1000.00\n'},
                    'open_invoices': {'content': 'Customer,Number,Date,Terms,Open amount\n'
                                                 f'Surface {surface} Cafe,SF-{surface}-1,12/02/2024,Net 30,300.00\n'
                                                 f'Surface {surface} Cafe,SF-{surface}-2,12/20/2024,Net 30,50.00\n'}})
                draft = made['id']
                bad = await call(surface, 'opening-draft update', {'draft': draft, 'expected_version': 9}, rejected=True)
                updated = await call(surface, 'opening-draft update', {
                    'draft': draft, 'expected_version': 1,
                    'unpaid_bills': {'content': 'Vendor,Reference,Date,Terms,Open amount\n'
                                                f'Surface {surface} Supply,SS-1,12/14/2024,Net 30,120.00\n'}})
                planned = await call(surface, 'opening-draft plan', {'draft': draft})
                applied = await call(surface, 'opening-draft apply', {'draft': draft})
                tied = await call(surface, 'opening-draft tie-out', {'draft': draft})
                shown = await call(surface, 'opening-draft show', {'draft': draft})
                listed = await call(surface, 'opening-draft query', {'state': 'applied'})
                # Undoing deletes the opening's invoices, bills and journal: the caller's own explicit delete grants,
                # which the demo owner does not hold, so the plan names them and nothing changes.
                plan = await call(surface, 'opening-draft reverse', {'draft': draft}, dry_run=True, rejected=True)
                other = await call(surface, 'opening-draft create', {'as_of': AS_OF})
                discarded = await call(surface, 'opening-draft discard', {'draft': other['id']})
                results[surface] = dict(made=made, bad=bad, updated=updated, planned=planned, applied=applied,
                                        tied=tied, shown=shown, listed=listed, plan=plan,
                                        discarded=discarded, draft=draft)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert [s['rows'] for s in r['made']['steps']] == [1, 2], surface
        assert 'E_VERSION_CONFLICT' in str(r['bad']), surface
        assert [s['step'] for s in r['updated']['steps']] == ['balances', 'open_invoices', 'unpaid_bills'], surface
        assert r['planned']['plan']['ready'], (surface, r['planned']['plan']['blocking'])
        assert r['applied']['draft']['state'] == 'applied', surface
        tie = r['tied']['tie_out']
        assert tie['tied'], (surface, tie['summary'])
        checks = sorted((c['name'], c['basis'], c['tied']) for c in tie['checks'])
        assert ('Accounts Receivable', 'given', True) in checks and ('Accounts Payable', 'derived', True) in checks
        assert r['shown']['state'] == 'applied' and r['draft'] in [d['id'] for d in r['listed']['rows']], surface
        assert 'E_BATCH_BLOCKED' in str(r['plan']) and 'transaction.journal_entry.delete' in str(r['plan']), surface
        assert r['discarded']['state'] == 'discarded', surface
        counts = tuple(sorted((c['kind'], c['create']) for c in r['planned']['plan']['counts']))
        shapes.add((counts, tuple(checks)))
    assert len(shapes) == 1
