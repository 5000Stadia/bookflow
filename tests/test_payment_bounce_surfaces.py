"""R176: the same bounced check through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, previews the return of the demo's DEMO-PAY-P2 (40.00
from Payment Example Customer:Job B, 30.00 applied to an invoice), records it with the bank's 15.00
fee and a 25.00 returned-check fee billed through the Payment Example Labor item, records it again (nothing
written), and reads the receipt, the A/R aging and the new invoice back.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('payment bounce',))


@pytest.mark.timeout(900)
def test_the_same_bounce_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                receipt = await matrix.call(surface, 'payment show', {'payment': 'DEMO-PAY-P2'})
                raw = {'payment': 'DEMO-PAY-P2', 'expected_version': receipt['version'], 'date': '2026-10-20',
                       'operation_key': 'bounce-p2', 'bank_fee': {'amount': '15.00', 'account': 'Bank Fees'},
                       'customer_fee': {'amount': '25.00', 'item': 'Payment Example Labor'}}
                before = await matrix.call(surface, 'report ar-aging', {'as_of': DEMO_AS_OF, 'limit': 200})
                preview = await matrix.call(surface, 'payment bounce', raw, dry_run=True)
                done = await matrix.call(surface, 'payment bounce', raw)
                again = await matrix.call(surface, 'payment bounce', raw)
                shown = await matrix.call(surface, 'payment show', {'payment': 'DEMO-PAY-P2'})
                invoice = await matrix.call(surface, 'invoice show', {'invoice': done['customer_fee']['id']})
                after = await matrix.call(surface, 'report ar-aging', {'as_of': '2026-12-31', 'limit': 200})
                results[surface] = (before, preview, done, again, shown, invoice, after)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (before, preview, done, again, shown, invoice, after) in results.items():
        assert preview['bounce_id'] is None and preview['dry_run'], surface
        assert done['changed'] and done['bounce_id'] and not again['changed'], surface
        assert again['idempotent_replay'] and again['bounce_id'] == done['bounce_id'], surface
        assert done['returned']['minor_units'] == 4000, surface
        assert [(row['reopened']['minor_units']) for row in done['reopened_invoices']] == [3000], surface
        assert shown['bounce']['note'] == 'bounced on 2026-10-20', surface
        assert invoice['total']['minor_units'] == 2500 and invoice['status'] == 'posted', surface
        shapes.add((done['returned']['minor_units'], done['bank_fee']['amount']['minor_units'],
                    done['customer_fee']['amount']['minor_units'], len(done['reopened_invoices']),
                    after['totals']['total']['minor_units'] - before['totals']['total']['minor_units']))
    assert len(shapes) == 1, shapes
