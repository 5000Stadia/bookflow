"""R109: the same budget, budget reports, comparisons and memorized report through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It creates a budget from the previous year's actuals,
fills Service Income, sets two months, reads the grid, the list, the overview, budget vs actual and both
comparisons, renames and clears it, memorizes a report with a relative date, lists, shows, updates, runs
and deletes it, then deletes the budget. Every surface must return the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset((
    'budget clear', 'budget create', 'budget delete', 'budget fill', 'budget list', 'budget rename', 'budget set',
    'budget show', 'memorized-report create', 'memorized-report delete', 'memorized-report list',
    'memorized-report run', 'memorized-report show', 'memorized-report update', 'report balance-sheet-comparison',
    'report budget-overview', 'report budget-vs-actual', 'report profit-and-loss-comparison'))


def _figures(result):
    return [[cell['amount'] for cell in row['cells']] for row in result['rows']], result['totals']


@pytest.mark.timeout(900)
def test_the_same_budget_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                made = await call('budget create', {'name': 'Plan 2026', 'fiscal_year': 2026, 'from_actuals': True,
                                                    'basis': 'accrual'})
                await call('budget fill', {'budget': 'Plan 2026', 'account': 'Service Income', 'amount': '1000.00',
                                           'change_percent': '2'})
                await call('budget set', {'budget': 'Plan 2026', 'account': 'Service Income', 'start_month': '2026-11',
                                          'amounts': ['1500.00', '1600.00']})
                grid = await call('budget show', {'budget': 'Plan 2026'})
                listed = await call('budget list', {})
                overview = await call('report budget-overview', {'budget': 'Plan 2026', 'columns': 'quarter'})
                bva = await call('report budget-vs-actual', {'budget': 'Plan 2026', 'columns': 'quarter',
                                                             'basis': 'accrual', 'limit': 200})
                pl = await call('report profit-and-loss-comparison', {'date_from': '2026-01-01', 'date_to': '2026-06-30',
                                                                      'compare': 'previous_period', 'limit': 200})
                bs = await call('report balance-sheet-comparison', {'date_to': '2026-12-12', 'limit': 200})
                await call('budget rename', {'budget': 'Plan 2026', 'name': 'Board plan 2026'})
                memo = await call('memorized-report create', {
                    'name': 'Budget this year', 'report': 'budget-vs-actual', 'group': 'Company',
                    'filters': {'budget': 'Board plan 2026', 'columns': 'total', 'basis': 'accrual'},
                    'date_range': 'this_fiscal_year'})
                await call('memorized-report list', {})
                await call('memorized-report show', {'memorized_report': 'Budget this year'})
                await call('memorized-report update', {'memorized_report': 'Budget this year', 'group': 'Accountant'})
                ran = await call('memorized-report run', {'memorized_report': 'Budget this year'})
                await call('memorized-report delete', {'memorized_report': 'Budget this year'})
                cleared = await call('budget clear', {'budget': 'Board plan 2026', 'account': 'Service Income'})
                gone = await call('budget delete', {'budget': 'Board plan 2026'})
                results[surface] = (made, grid, listed, overview, bva, pl, bs, memo, ran, cleared, gone)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (made, grid, listed, overview, bva, pl, bs, memo, ran, cleared, gone) in results.items():
        income = next(row for row in grid['rows'] if row['current_account_label'] == 'Service Income')
        assert [cell['amount'] for cell in income['amounts']][:3] == ['1000.00', '1020.00', '1040.40'], surface
        assert [cell['amount'] for cell in income['amounts']][10:] == ['1500.00', '1600.00'], surface
        assert [item['name'] for item in listed['items']] == ['Plan 2026'], surface
        assert memo['memorized_report']['date_range'] == 'this_fiscal_year', surface
        assert ran['command'] == 'report budget-vs-actual' and ran['result']['budget']['name'] == 'Board plan 2026', surface
        assert gone['deleted'] and cleared['changed_cells'] == 12, surface
        shapes.add(repr((made['budget']['total'], grid['total'], _figures(overview), _figures(bva), _figures(pl),
                         _figures(bs), ran['result']['totals'])))
    assert len(shapes) == 1
