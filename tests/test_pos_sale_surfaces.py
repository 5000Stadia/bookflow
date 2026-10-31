"""R114 increment 5: ringing up through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy and acts as its owner. It is refused a preview while POS is off, turns POS
on, previews a cart, completes it in cash, completes it again with the same ticket (the sale already posted comes
back), adds a customer and finds them again. Every surface ends with the same facts and the same refusal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

COMMANDS = frozenset(('pos sale preview', 'pos sale complete', 'pos customer search', 'pos customer add'))
CART = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}, {'item': 'Mainline Clearing'}]}


@pytest.mark.timeout(900)
def test_the_same_sale_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in matrix.documents:
                call = matrix.call
                off = await call(surface, 'pos sale preview', CART, rejected=True)
                info = await call(surface, 'company show', {})
                await call(surface, 'company update', {'pos_enabled': True, 'expected_version': info['info_version']})
                shown = await call(surface, 'pos sale preview', CART)
                body = dict(CART, ticket='surface-ticket-1', expected_total=shown['total']['amount'],
                            tender={'payment_method': 'Cash', 'amount': '500'})
                sale = await call(surface, 'pos sale complete', body)
                again = await call(surface, 'pos sale complete', body)
                made = await call(surface, 'pos customer add', {'name': 'Casey Counter', 'phone': '555-0142'})
                found = await call(surface, 'pos customer search', {'q': '0142'})
                results[surface] = (
                    off.get('code') or off.get('error', {}).get('code'),
                    (shown['total']['amount'], shown['customer'], [line['item_name'] for line in shown['lines']]),
                    (sale['sales_receipt']['name'], sale['total']['amount'], sale['change']['amount'], sale['replayed']),
                    (again['sales_receipt'] == sale['sales_receipt'], again['replayed']),
                    (made['name'], made['phone']), [c['name'] for c in found['items']],
                )
            first = results['python']
            assert first[0] == 'E_FEATURE_DISABLED'
            assert first[1][1] is None and first[1][2] == ['Pipe Sealant', 'Mainline Clearing']
            assert first[2][1] == first[1][0] and first[2][3] is False and first[3] == (True, True)
            assert first[4] == ('Casey Counter', '555-0142') and first[5] == ['Casey Counter']
            assert all(value == first for value in results.values()), results
        finally:
            await matrix.close()

    anyio.run(witness)
