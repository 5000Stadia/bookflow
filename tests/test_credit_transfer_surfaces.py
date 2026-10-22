"""R148: the same credit transfer through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, whose DEMO-PAY-P2 leaves 10.00 of credit on Payment
Example Customer:Job B. It previews moving that credit to Job A, records it, records it again (nothing
written), reads the A/R aging and voids the transfer.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('customer-credit transfer', 'customer-credit transfer-void'))
JOB_A, JOB_B = 'Payment Example Customer:Job A', 'Payment Example Customer:Job B'


@pytest.mark.timeout(900)
def test_the_same_credit_transfer_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                raw = {'from_customer': JOB_B, 'to_customer': JOB_A, 'amount': '10.00', 'date': '2026-10-20',
                       'operation_key': 'xfer-b-to-a'}
                preview = await matrix.call(surface, 'customer-credit transfer', raw, dry_run=True)
                done = await matrix.call(surface, 'customer-credit transfer', raw)
                again = await matrix.call(surface, 'customer-credit transfer', raw)
                aging = await matrix.call(surface, 'report ar-aging', {'as_of': '2026-12-31', 'limit': 200})
                voided = await matrix.call(surface, 'customer-credit transfer-void', {'transfer': done['transfer_id']})
                results[surface] = (preview, done, again, aging, voided)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, again, aging, voided) in results.items():
        assert preview['transfer_id'] is None and preview['dry_run'], surface
        assert done['changed'] and done['transfer_id'] and done['status'] == 'posted', surface
        assert again['idempotent_replay'] and not again['changed'] and again['transfer_id'] == done['transfer_id'], surface
        assert voided['status'] == 'voided' and voided['changed'], surface
        rows = {row['display_customer_label']: row['total']['minor_units'] for row in aging['rows']}
        shapes.add((done['amount']['minor_units'], done['charge']['customer'], done['credit_memo']['customer'],
                    rows.get(JOB_B), tuple(row['type'] for row in done['sources'])))
    assert len(shapes) == 1, shapes
