"""R104 increment 3: barcodes through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy. It looks up the Copper Coupling's code, adds a part with no
code and numbers it with `barcode generate` (a retry under the same key is the same run), posts an
invoice naming the new code as `item`, and is refused a second part carrying the coupling's code.
Every surface must give the same answers.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('barcode generate', 'barcode lookup'))


@pytest.mark.timeout(900)
def test_the_same_barcodes_through_python_cli_http_and_mcp(root, tmp_path):
    from tests.mcp_matrix_support import Matrix

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                looked = await call('barcode lookup', {'code': '0012345678905', 'as_of': DEMO_AS_OF})
                unknown = await call('barcode lookup', {'code': '2999999999999'})
                accounts = await call('account query', {'limit': 200})
                income = next(row['id'] for row in accounts['items'] if row['type'] == 'income')
                await call('item create', {'name': 'Scan Tee', 'type': 'non_inventory_part', 'description': 'Tee',
                                           'price': '4.00', 'income_account_id': income})
                made = await call('barcode generate', {'items': ['Scan Tee']}, idempotency_key='label-tee')
                again = await call('barcode generate', {'items': ['Scan Tee']}, idempotency_key='label-tee')
                code = made['assigned'][0]['barcode']
                sale = await call('invoice post', {'customer': 'Riverside Apartments', 'date': DEMO_AS_OF,
                                                   'lines': [{'item': code, 'quantity': '3'}]})
                shown = await call('invoice show', {'invoice': sale['id']})
                refused = await call('item create', {'name': 'Scan Copy', 'type': 'non_inventory_part',
                                                      'description': 'Copy', 'income_account_id': income,
                                                      'barcode': '0012345678905'}, rejected=True)
                results[surface] = dict(looked=looked, unknown=unknown, made=made, again=again, code=code,
                                        line=shown['revision']['lines'][0], refused=refused)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['looked']['found'] and r['looked']['match']['name'] == 'Copper Coupling', surface
        assert not r['unknown']['found'] and 'item update' in r['unknown']['message'], surface
        assert r['again'].get('idempotent_replay', True) is True, surface             # the retry is the same run
        assert {k: v for k, v in r['again'].items() if k != 'idempotent_replay'} == r['made'], surface
        assert r['code'].startswith('2') and len(r['code']) == 13, surface
        assert r['line']['quantity'] == '3' and 'Scan Tee' in repr(r['line']), surface
        assert 'E_VALIDATION' in str(r['refused']) and 'Copper Coupling' in str(r['refused']), surface
        shapes.add((r['code'], r['looked']['match']['line_fields']['item'], len(r['looked']['match']['on_hand'])))
    assert len(shapes) == 1
