"""R124: the same customer prepayment through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. Commercial Example Customer pays a 25.00 deposit,
which is moved onto its accepted estimate DEMO-WORK-EST-1B, 10.00 of it used on its open invoice,
the use undone, and the 25.00 paid back; the report ties to the account at every step's end.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('prepayment receive', 'prepayment attach', 'prepayment apply', 'prepayment unapply',
                      'prepayment refund', 'prepayment show', 'prepayment query', 'report customer-prepayments'))
CUSTOMER = 'Commercial Example Customer'
ESTIMATE = 'estimate DEMO-WORK-EST-1B'  # accepted, the customer's own
INVOICE = 'DEMO-SALE-INV-ACTIVE'  # the customer's open invoice


@pytest.mark.timeout(900)
def test_the_same_prepayment_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, body, **options: matrix.call(surface, name, body, **options)
                body = {'customer': CUSTOMER, 'amount': '25.00', 'date': '2026-09-01', 'payment_method': 'Check'}
                preview = await call('prepayment receive', body, dry_run=True)
                got = await call('prepayment receive', body)
                moved = await call('prepayment attach', {'receipt': got['receipt']['number'], 'document': ESTIMATE})
                listed = await call('prepayment query', {'held_only': True})
                used = await call('prepayment apply', {'invoice': INVOICE, 'document': ESTIMATE, 'amount': '10.00'})
                undone = await call('prepayment unapply', {'draw': used['credit_memo']['number']})
                paid = await call('prepayment refund', {'document': ESTIMATE, 'date': '2026-10-01',
                                                        'bank_account': 'Checking', 'payment_method': 'Check'})
                shown = await call('prepayment show', {'document': ESTIMATE})
                report = await call('report customer-prepayments', {'as_of': '2026-12-31', 'include_drawn': True})
                results[surface] = (preview, got, moved, listed, used, undone, paid, shown, report)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (preview, got, moved, listed, used, undone, paid, shown, report) in results.items():
        assert preview['set_up'] and preview['held_after']['minor_units'] == 2500, surface
        assert got['receipt']['number'] == preview['receipt']['number'], surface
        assert moved['document']['number'] == 'DEMO-WORK-EST-1B', surface
        assert [row['held']['minor_units'] for row in listed['items']] == [2500], surface
        assert used['amount']['minor_units'] == 1000 and used['held_after']['minor_units'] == 1500, surface
        assert undone['held_after']['minor_units'] == 2500, surface
        assert paid['amount']['minor_units'] == 2500 and paid['refund']['number'], surface
        assert shown['held']['minor_units'] == 0 and len(shown['draws']) == 2, surface
        assert report['total_held']['minor_units'] == 0 == report['account_balance']['minor_units'], surface
        shapes.add((got['receipt']['number'], used['credit_memo']['number'], paid['refund']['number'],
                    tuple((row['kind'], row['amount']['minor_units']) for row in report['rows'])))
    assert len(shapes) == 1
