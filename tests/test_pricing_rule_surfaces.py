"""R110: the price-rule commands through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It sets the conflict preference, adds a rule giving
Riverside Apartments 10% off Mainline Clearing with quantity breaks, changes its rounding, shows, lists,
previews a price, and deactivates and reactivates it. Every surface must return the same figures.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('price-rule activate', 'price-rule create', 'price-rule deactivate', 'price-rule list',
                      'price-rule preferences', 'price-rule preview', 'price-rule set-preferences', 'price-rule show',
                      'price-rule update'))
CUSTOMER = 'Riverside Apartments'
SERVICE = 'Mainline Clearing'


@pytest.mark.timeout(900)
def test_the_same_price_rules_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                policy = await call('price-rule set-preferences', {'conflict_policy': 'priority'})
                shown_policy = await call('price-rule preferences', {})
                made = await call('price-rule create', {
                    'name': 'Riverside volume', 'customers': [CUSTOMER], 'items': [SERVICE],
                    'breaks': [{'min_quantity': '10', 'value': '5'}, {'min_quantity': '50', 'value': '10'}],
                    'date_to': '2026-12-31'})
                changed = await call('price-rule update', {'rule': made['id'], 'expected_version': 1, 'rounding': 'x.99'})
                shown = await call('price-rule show', {'rule': 'Riverside volume'})
                listed = await call('price-rule list', {'customer': CUSTOMER})
                few = await call('price-rule preview', {'item': SERVICE, 'customer': CUSTOMER, 'quantity': '2', 'date': '2026-11-02'})
                many = await call('price-rule preview', {'item': SERVICE, 'customer': CUSTOMER, 'quantity': '50', 'date': '2026-11-02'})
                off = await call('price-rule deactivate', {'rule': made['id'], 'expected_version': 2})
                on = await call('price-rule activate', {'rule': made['id'], 'expected_version': 3})
                results[surface] = (policy, shown_policy, changed, shown, listed, few, many, off, on)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (policy, shown_policy, changed, shown, listed, few, many, off, on) in results.items():
        assert policy['conflict_policy'] == shown_policy['conflict_policy'] == 'priority', surface
        assert changed['rounding'] == 'x.99' and shown['version'] == 2, surface
        assert [r['name'] for r in listed['rules']] == ['Riverside volume'], surface
        assert few['source'] != 'rule' and many['source'] == 'rule', surface
        assert many['unit_price'].endswith('.99') and many['note'].startswith('price from rule Riverside volume'), surface
        assert (off['active'], on['active'], on['version']) == (False, True, 4), surface
        shapes.add(repr((few['unit_price'], many['unit_price'], many['note'], shown['summary'])))
    assert len(shapes) == 1
