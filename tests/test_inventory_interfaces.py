"""The same inventory adjustment through Python, the CLI, HTTP and MCP, byte for byte.

Four real adapters over four private copies of one seed. Every document each surface returns
is normalized and compared to Python's, so a field that only the browser fills in, an amount
the CLI parses differently, or an error the MCP adapter reshapes is a failure here rather
than a surprise for whoever calls the odd one out.

The scenario is the whole vertical slice: open some stock, read it back, run both reports it
feeds, take the adjustment back out, and be refused for naming an item that carries no stock.

**Every figure is derived from the company in front of it, never pinned.** The seeded demo
already holds this item and already sells it, so the adjustment is one movement among many and
the company's asset total is not this adjustment's total. What is asserted is what the
adjustment is answerable for: the quantity rises by exactly `quantity_change` and the void puts
it back, and the stock asset rises by exactly `value_change` **plus the corrections the
adjustment itself declares**.

That second clause is the part worth having. This adjustment is dated 2026-05-04 into a company
that issued this item later in the year, so backdating a receipt re-costs that later issue under
average costing. The product posts the correction and names it in `corrections`, with its date,
the movement it corrects and its delta. Requiring the two reports to have moved by exactly
value_change plus those declared corrections proves the re-costing did what it said it did --
which a total pinned to an empty company never could, and could not survive a reseed either.
"""
from copy import deepcopy
from decimal import Decimal

import pytest

ITEM = 'Brass Shutoff Valve'
SERVICE = 'Copper Coupling'
OPENING = '114.00'        # what this adjustment puts in
OPENING_MINOR = 11400
UNITS = '10'

COMMANDS = frozenset(('inventory adjust', 'inventory show', 'inventory void',
                      'report inventory-valuation', 'report stock-status'))


def _row(report, item):
    return next(row for row in report['rows'] if row['item_id'] == item)


@pytest.mark.timeout(600)
def test_the_same_inventory_adjustment_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                item = (await matrix.call(surface, 'item show', {'item': ITEM}))['id']
                service = (await matrix.call(surface, 'item show', {'item': SERVICE}))['id']

                # What this company already holds, before the adjustment touches it.
                opening = await call('report inventory-valuation',
                                     {'as_of': '2026-12-31', 'limit': 50})
                held = _row(opening, item)
                opening_units = Decimal(held['quantity_on_hand'])
                opening_item = held['asset_value']['minor_units']
                opening_company = opening['totals']['asset_value']['minor_units']

                entry = dict(item=item, date='2026-05-04',
                             adjustment_account='Opening Balance Equity',
                             quantity_change=UNITS, value_change=OPENING,
                             memo='Parity opening stock', number='PINV-1')
                assert (await call('inventory adjust', entry, dry_run=True))['dry_run']
                posted = await call('inventory adjust', entry, idempotency_key='inventory-1')
                replay = await call('inventory adjust', entry, idempotency_key='inventory-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']

                adjustment = posted['adjustment']
                assert adjustment['value_change']['minor_units'] == OPENING_MINOR
                assert Decimal(adjustment['quantity_on_hand']) == opening_units + Decimal(UNITS)
                # Backdating a receipt into a company that already issued this item re-costs
                # the later issues. The adjustment names every correction that forced, so the
                # stock asset moves by what went in less what those corrections took back.
                corrections = sum(row['delta']['minor_units'] for row in adjustment['corrections'])
                moved = OPENING_MINOR + corrections
                assert adjustment['inventory_value']['minor_units'] == opening_item + moved

                shown = await call('inventory show', {'adjustment': posted['id']})
                assert shown['adjustment']['value_change']['minor_units'] == OPENING_MINOR
                assert shown['adjustment']['inventory_value'] == adjustment['inventory_value']
                assert shown['adjustment']['average_cost'] == adjustment['average_cost']

                valuation = await call('report inventory-valuation',
                                       {'as_of': '2026-12-31', 'limit': 50})
                assert valuation['totals']['asset_value']['minor_units'] == opening_company + moved
                after = _row(valuation, item)
                assert Decimal(after['quantity_on_hand']) == opening_units + Decimal(UNITS)
                # The writer and the report are two readers of one number, and they agree.
                assert after['asset_value'] == adjustment['inventory_value']
                assert after['average_cost'] == adjustment['average_cost']

                status = await call('report stock-status', {'as_of': '2026-12-31', 'limit': 50})
                assert status['totals']['asset_value']['minor_units'] == opening_company + moved
                assert _row(status, item)['asset_value'] == adjustment['inventory_value']

                await call('inventory void', {'adjustment': posted['id'],
                                              'expected_version': posted['version']})
                # The void gives back the stock and the money, corrections included.
                restored = await call('report stock-status', {'as_of': '2026-12-31', 'limit': 50})
                assert restored['totals']['asset_value']['minor_units'] == opening_company
                back = _row(restored, item)
                assert Decimal(back['quantity_on_hand']) == opening_units
                assert back['asset_value']['minor_units'] == opening_item

                refused = await call('inventory adjust', {
                    **entry, 'number': 'PINV-2', 'item': service}, rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                assert 'carries no stock' in refused['details']['fields'][0]['problem']
                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
