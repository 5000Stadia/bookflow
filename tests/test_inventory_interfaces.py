"""The same inventory adjustment through Python, the CLI, HTTP and MCP, byte for byte.

Four real adapters over four private copies of one seed. Every document each surface returns
is normalized and compared to Python's, so a field that only the browser fills in, an amount
the CLI parses differently, or an error the MCP adapter reshapes is a failure here rather
than a surprise for whoever calls the odd one out.

The scenario is the whole vertical slice: open some stock, read it back, run both reports it
feeds, take the adjustment back out, and be refused for naming an item that carries no stock.
"""
from copy import deepcopy

import pytest

ITEM = 'Brass Shutoff Valve'
SERVICE = 'Copper Coupling'
OPENING = '114.00'        # 11400 minor units for ten units, so 11.40 each
UNITS = '10'

COMMANDS = frozenset(('inventory adjust', 'inventory show', 'inventory void',
                      'report inventory-valuation', 'report stock-status'))


@pytest.mark.timeout(300)
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
                entry = dict(item=item, date='2026-05-04',
                             adjustment_account='Opening Balance Equity',
                             quantity_change=UNITS, value_change=OPENING,
                             memo='Parity opening stock', number='PINV-1')
                assert (await call('inventory adjust', entry, dry_run=True))['dry_run']
                posted = await call('inventory adjust', entry, idempotency_key='inventory-1')
                replay = await call('inventory adjust', entry, idempotency_key='inventory-1')
                assert replay['id'] == posted['id'] and replay['idempotent_replay']
                assert posted['adjustment']['quantity_on_hand'] == UNITS
                assert posted['adjustment']['average_cost']['amount'] == '11.40'

                shown = await call('inventory show', {'adjustment': posted['id']})
                assert shown['adjustment']['inventory_value']['amount'] == OPENING
                valuation = await call('report inventory-valuation',
                                       {'as_of': '2026-12-31', 'limit': 50})
                assert valuation['totals']['asset_value']['amount'] == OPENING
                status = await call('report stock-status', {'as_of': '2026-12-31', 'limit': 50})
                assert status['totals']['asset_value']['amount'] == OPENING
                await call('inventory void', {'adjustment': posted['id'],
                                              'expected_version': posted['version']})

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
