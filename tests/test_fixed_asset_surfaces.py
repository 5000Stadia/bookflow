"""R119: the same fixed-asset depreciation, disposal and loan through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It adds the accounts, a fixed-asset item (a 12,000.00
van bought on the demo date's year start), sets up straight-line depreciation over 48 months with
1,200.00 salvage, previews and posts depreciation through June, posts again (nothing), reads the
schedule and the list, and sells the van; then it creates a 25,000.00 loan at 6.5% over 60 months,
changes its escrow, pays it and reads it back. Every surface must return the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('fixed-asset depreciate', 'fixed-asset dispose', 'fixed-asset list', 'fixed-asset schedule',
                      'fixed-asset setup', 'loan create', 'loan list', 'loan pay', 'loan show', 'loan update'))


@pytest.mark.timeout(900)
def test_the_same_fixed_assets_and_loan_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                ids = {}
                for name, kind in (('Vehicles', 'fixed_asset'), ('Accumulated Depreciation', 'fixed_asset'),
                                   ('Depreciation Expense', 'expense'), ('Gain on Sale of Assets', 'other_income'),
                                   ('Van Loan', 'long_term_liability'), ('Loan Interest', 'expense'),
                                   ('Loan Escrow', 'other_current_asset')):
                    ids[name] = (await call('account create', {'name': name, 'type': kind}))['id']
                await call('item create', {'name': 'Van', 'type': 'fixed_asset', 'asset_number': 'VAN-1', 'description': 'Van',
                                           'purchase_date': '2026-01-10', 'original_cost': '12000.00',
                                           'asset_account_id': ids['Vehicles'], 'disposal_status': 'in_service',
                                           'depreciation_method': 'straight_line'})
                setup = await call('fixed-asset setup', {
                    'item': 'Van', 'method': 'straight_line', 'salvage': '1200.00', 'life_months': 48,
                    'accumulated_depreciation_account': 'Accumulated Depreciation',
                    'depreciation_expense_account': 'Depreciation Expense', 'gain_loss_account': 'Gain on Sale of Assets'})
                preview = await call('fixed-asset depreciate', {'through': '2026-06-30'}, dry_run=True)
                posted = await call('fixed-asset depreciate', {'through': '2026-06-30'})
                again = await call('fixed-asset depreciate', {'through': '2026-06-30'})
                schedule = await call('fixed-asset schedule', {'item': 'Van'})
                listed = await call('fixed-asset list', {})
                sold = await call('fixed-asset dispose', {'item': 'Van', 'date': '2026-07-15', 'proceeds': '10000.00',
                                                         'deposit_to': 'Checking'})
                loan = await call('loan create', {'name': 'Van loan', 'liability_account': 'Van Loan',
                                                  'interest_account': 'Loan Interest', 'principal': '25000.00',
                                                  'annual_rate': '6.5', 'term_payments': 60,
                                                  'first_payment_date': '2026-08-01', 'payment_account': 'Checking'})
                changed = await call('loan update', {'loan': 'Van loan', 'escrow': '50.00', 'escrow_account': 'Loan Escrow'})
                paid = await call('loan pay', {'loan': 'Van loan'})
                shown = await call('loan show', {'loan': 'Van loan'})
                loans = await call('loan list', {})
                results[surface] = (setup, preview, posted, again, schedule, listed, sold, loan, changed, paid, shown, loans)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (setup, preview, posted, again, schedule, listed, sold, loan, changed, paid, shown, loans) in results.items():
        assert {row['amount']['amount'] for row in setup['schedule']['rows']} == {'225.00'}, surface
        assert preview['total'] == posted['total'] and posted['total']['amount'] == '1350.00', surface
        assert again['periods_posted'] == 0 and schedule['pending_periods'] == 42, surface
        assert 'Van' in [item['item'] for item in listed['items']], surface
        # Book value 12,000.00 - 1,350.00 = 10,650.00; sold for 10,000.00: a loss of 650.00.
        assert sold['gain']['amount'] == '-650.00', surface
        assert loan['loan']['loan']['payment']['amount'] == '489.15' and changed['loan']['loan']['escrow']['amount'] == '50.00', surface
        assert paid['total']['amount'] == '539.15' and paid['principal']['amount'] == '353.73', surface
        assert shown['loan']['balance']['amount'] == '24646.27' and [x['name'] for x in loans['items']] == ['Van loan'], surface
        shapes.add(repr((posted['total'], sold['gain'], sold['lines'], paid['total'], shown['rows'][1])))
    assert len(shapes) == 1, shapes
