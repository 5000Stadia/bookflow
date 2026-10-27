"""R104 increment 7: the costing method through Python, the CLI, HTTP and MCP, the shared scenario extended.

Each surface gets an identical demo copy (on average, as the demo stays). A person switches it to FIFO from the
demo's as-of date: previewed, posted under one idempotency key and retried (a lost response), refused when the
same switch is asked again, read back through show and the layers report, then a later switch scheduled and
withdrawn. Every surface must end with the same switches, the same layers and the same valuation.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('costing-method set', 'costing-method withdraw', 'costing-method show',
                      'report inventory-layers'))


@pytest.mark.timeout(900)
def test_the_same_costing_method_switch_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                before = await call(surface, 'costing-method show', {})
                payload = {'method': 'fifo', 'effective_from': DEMO_AS_OF}
                preview = await call(surface, 'costing-method set', payload, dry_run=True)
                first = await call(surface, 'costing-method set', payload, idempotency_key='fifo-a')
                again = await call(surface, 'costing-method set', payload, idempotency_key='fifo-a')
                twice = await call(surface, 'costing-method set', payload, rejected=True)
                later = await call(surface, 'costing-method set', {'method': 'average', 'effective_from': '2099-01-01'})
                withdrawn = await call(surface, 'costing-method withdraw', {'switch': '2099-01-01'})
                shown = await call(surface, 'costing-method show', {})
                layers = await call(surface, 'report inventory-layers', {'as_of': DEMO_AS_OF, 'limit': 200})
                valuation = await call(surface, 'report inventory-valuation', {'as_of': DEMO_AS_OF, 'limit': 200})
                results[surface] = dict(before=before, preview=preview, first=first, again=again, twice=twice,
                                        later=later, withdrawn=withdrawn, shown=shown, layers=layers,
                                        valuation=valuation['totals']['asset_value']['minor_units'])
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['before']['method_today'] == 'average' and r['before']['switches'] == [], surface   # the demo
        assert r['first']['switch']['id'] == r['again']['switch']['id'], surface
        assert 'must be dated after it' in str(r['twice']), surface
        assert r['preview']['entries'] == r['first']['entries'], surface
        assert r['withdrawn']['switch']['status'] == 'withdrawn', surface
        assert [(row['method'], row['status']) for row in r['shown']['switches']] == [
            ('fifo', 'standing'), ('average', 'withdrawn')], surface
        assert r['layers']['method'] == 'fifo' and r['layers']['totals']['asset_value']['minor_units'] == r['valuation'], surface
        shapes.add((repr([(e['date'], e['amount_minor_units']) for e in r['first']['entries']]),
                    repr([(row['item_name'], row['source'], row['quantity'], row['value']['minor_units'])
                          for row in r['layers']['rows']]), r['valuation']))
    assert len(shapes) == 1
