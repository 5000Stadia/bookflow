"""R149: a vendor's refund check for an open vendor credit, through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo, enters a 120.00 vendor credit for the demo's 1099
subcontractor, previews a 50.00 refund check against it, records it, and reads back the credit (70.00
still free), the deposit it banked, and the vendor's balance.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('vendor-credit refund',))
VENDOR = "Summit Pipe Contracting"


@pytest.mark.timeout(900)
def test_the_same_vendor_refund_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                credit = await matrix.call(surface, 'vendor-credit post', {
                    'vendor': VENDOR, 'date': '2026-11-02', 'number': 'VC-REFUND',
                    'expenses': [{'account': 'Professional Fees', 'amount': '120.00'}]})
                raw = {'credit': credit['id'], 'deposit_to': 'Checking', 'date': '2026-11-09',
                       'amount': '50.00', 'payment_method': 'Check', 'check_number': '4410'}
                preview = await matrix.call(surface, 'vendor-credit refund', raw, dry_run=True)
                done = await matrix.call(surface, 'vendor-credit refund', raw)
                shown = await matrix.call(surface, 'vendor-credit show', {'credit': credit['id']})
                balance = await matrix.call(surface, 'report vendor-balance-detail',
                                            {'as_of': '2026-12-31', 'vendor': VENDOR})
                results[surface] = (preview, done, shown, balance)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, done, shown, balance) in results.items():
        assert preview['dry_run'] and preview['deposit_id'] == 'new-deposit', surface
        assert preview['credit_free_after_minor_units'] == 7000, surface
        assert done['changed'] and done['deposit_id'] != 'new-deposit', surface
        assert done['amount_minor_units'] == 5000 and done['credit_free_after_minor_units'] == 7000, surface
        assert shown['settlement_current']['unapplied_minor_units'] == 7000, surface
        assert [row['obligation_transaction_id'] for row in shown['applications'] if row['active']] == [
            done['deposit_id']], surface
        shapes.add((done['amount_minor_units'], balance['totals']['balance']['minor_units']))
    assert len(shapes) == 1, shapes
