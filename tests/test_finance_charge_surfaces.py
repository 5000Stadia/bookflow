"""R120: the same finance charge assessment through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, sets the finance charge policy (18%, 2.00 minimum,
credited to a new Finance Charge Income account), previews the charges as of the demo date, assesses
them, assesses again (nothing written), and reads the A/R aging back up by exactly the total charged.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('finance-charge assess',))


@pytest.mark.timeout(900)
def test_the_same_finance_charges_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                await matrix.call(surface, 'account create', {'name': 'Finance Charge Income', 'type': 'other_income'})
                await matrix.call(surface, 'company update', {
                    'finance_charge_annual_rate': '18', 'finance_charge_minimum': '2.00',
                    'finance_charge_account_id': 'Finance Charge Income'})
                before = await matrix.call(surface, 'report ar-aging', {'as_of': DEMO_AS_OF, 'limit': 200})
                preview = await matrix.call(surface, 'finance-charge assess', {'date': DEMO_AS_OF}, dry_run=True)
                done = await matrix.call(surface, 'finance-charge assess', {
                    'date': DEMO_AS_OF, 'expected_facts_fingerprint': preview['facts_fingerprint']})
                again = await matrix.call(surface, 'finance-charge assess', {'date': DEMO_AS_OF})
                after = await matrix.call(surface, 'report ar-aging', {'as_of': DEMO_AS_OF, 'limit': 200})
                results[surface] = (before, preview, done, again, after)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (before, preview, done, again, after) in results.items():
        assert preview['assessed_count'] > 0 and all(row['status'] == 'will_assess' for row in preview['rows']), surface
        assert done['total'] == preview['total'] and all(row['status'] == 'assessed' for row in done['rows']), surface
        assert again['assessed_count'] == 0 and all(row['status'] == 'already_assessed' for row in again['rows']), surface
        moved = after['totals']['total']['minor_units'] - before['totals']['total']['minor_units']
        assert moved == done['total']['minor_units'], surface
        shapes.add((done['assessed_count'], done['total']['minor_units'], done['item']))
    assert len(shapes) == 1, shapes
