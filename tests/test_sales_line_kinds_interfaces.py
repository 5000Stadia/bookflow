"""R147: one invoice with a subtotal, a percentage discount, a group and a percentage charge
through Python, the CLI, the HTTP host and MCP.

Four clones of the demo company run the identical sequence -- preview, post, show, correct,
a refused entry, void -- and the four transcripts are compared after generated identifiers and
timestamps are normalized: the same inputs give the same outputs and the same errors.
"""
from copy import deepcopy

import pytest

LINES = [{'item': 'Mainline Clearing', 'quantity': '2'}, {'item': 'Copper Coupling', 'quantity': '4'},
         {'item': 'Work Order Subtotal'}, {'item': 'Loyalty Discount', 'percent': '10'},
         {'item': 'Drain Service Bundle'}, {'item': 'Trip Charge'}]
DATE = '2026-09-01'


@pytest.mark.timeout(900)
def test_the_same_line_kinds_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                entry = dict(date=DATE, customer='Commercial Example Customer',
                             sales_tax_calculation='invoice_combined_half_up', lines=deepcopy(LINES))
                preview = await matrix.call(surface, 'invoice post', entry, dry_run=True)
                posted = await matrix.call(surface, 'invoice post',
                                           dict(entry, expected_facts_fingerprint=preview['facts_fingerprint']))
                kinds = [line.get('line_kind', 'item') for line in posted['revision']['lines']]
                assert kinds == ['item', 'item', 'subtotal', 'discount', 'item', 'charge'], surface
                await matrix.call(surface, 'invoice show', dict(invoice=posted['id']))
                ids = [line['line_id'] for line in posted['revision']['lines']]
                items = [line['item_id'] for line in posted['revision']['lines']]
                lines = [dict(item=item, line_id=identity) for item, identity in zip(items, ids)]
                lines[0]['quantity'] = '3'
                await matrix.call(surface, 'invoice update', dict(invoice=posted['id'], expected_version=1, lines=lines))
                refused = await matrix.call(surface, 'invoice post', dict(entry, lines=[{'item': 'Loyalty Discount'}]),
                                            rejected=True)
                assert refused['code'] == 'E_VALIDATION'
                await matrix.call(surface, 'invoice void', dict(invoice=posted['id'], expected_version=2))
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
