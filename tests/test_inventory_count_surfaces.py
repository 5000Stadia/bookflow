"""R104 increment 2: physical counts through Python, the CLI, HTTP and MCP, the shared scenario extended.

Each surface gets an identical demo copy (Warehouse and Van 1, the van already counted). It counts the
warehouse's shipping kits, enters twice under one idempotency key (a lost response) and once more as
a second counter, records the count from its preview, then posts a transfer from the van dated the
count date with the default position, which lands before the count and derives its difference again.
Every surface must end with the same quantities by place on every date, the same valuation and the
same differences; the person's browser half of the scenario is in ``test_inventory_count_browser``.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('inventory-count cancel', 'inventory-count create', 'inventory-count enter', 'inventory-count post',
                      'inventory-count query', 'inventory-count sheet', 'inventory-count show', 'inventory-count void',
                      'report inventory-valuation-detail', 'report physical-inventory-worksheet'))
KIT = 'Received Shipping Kit'
DATES = ('2026-12-02', '2026-12-03', DEMO_AS_OF)


@pytest.mark.timeout(900)
def test_the_same_count_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                accounts = await call(surface, 'account query', {'limit': 200})
                expense = next(a['id'] for a in accounts['items'] if a['type'] == 'cost_of_goods_sold')
                made = await call(surface, 'inventory-count create', {'site': 'Warehouse', 'date': DEMO_AS_OF, 'items': [KIT]})
                number = made['number']
                entry = {'count': number, 'entries': [{'item': KIT, 'quantity': '0.5', 'mode': 'add'}]}
                first = await call(surface, 'inventory-count enter', entry, idempotency_key='kit-a')
                again = await call(surface, 'inventory-count enter', entry, idempotency_key='kit-a')
                second = await call(surface, 'inventory-count enter', {'count': number, 'entries': [
                    {'item': KIT, 'quantity': '1', 'mode': 'add'}]}, idempotency_key='kit-b')
                sheet = await call(surface, 'inventory-count sheet', {'count': number})
                body = {'count': number, 'expected_version': made['version'], 'adjustment_account': expense}
                preview = await call(surface, 'inventory-count post', body, dry_run=True)
                stale = await call(surface, 'inventory-count post', {**body, 'preview_fingerprint': 'stale'}, rejected=True)
                recorded = await call(surface, 'inventory-count post', {**body, 'preview_fingerprint': preview['preview_fingerprint']})
                moved = await call(surface, 'inventory-transfer post', {
                    'date': DEMO_AS_OF, 'from_site': 'Van 1', 'to_site': 'Warehouse',
                    'lines': [{'item': KIT, 'quantity': '1'}], 'memo': 'Paperwork for a kit unloaded this morning'})
                shown = await call(surface, 'inventory-count show', {'count': number})
                found = await call(surface, 'inventory-count query', {'site': 'Warehouse'})
                worksheet = await call(surface, 'report physical-inventory-worksheet', {'as_of': DEMO_AS_OF, 'site': 'Warehouse'})
                detail = await call(surface, 'report inventory-valuation-detail', {
                    'date_from': '2026-12-01', 'date_to': DEMO_AS_OF, 'item': KIT, 'limit': 200})
                readings = {}
                for date in DATES:
                    report = await call(surface, 'report quantity-by-site', {'as_of': date, 'limit': 200})
                    readings[date] = sorted((r['item_name'], r['site_name'], r['quantity_on_hand'],
                                             r['asset_value']['minor_units']) for r in report['rows'])
                valuation = await call(surface, 'report inventory-valuation', {'as_of': DEMO_AS_OF, 'limit': 200})
                spare = await call(surface, 'inventory-count create', {'site': 'Van 1', 'date': DEMO_AS_OF, 'items': [KIT]})
                cancelled = await call(surface, 'inventory-count cancel', {'count': spare['number'], 'expected_version': 1})
                voided = await call(surface, 'inventory-count void', {'count': number, 'expected_version': shown['version']})
                after_void = await call(surface, 'report quantity-by-site', {'as_of': DEMO_AS_OF, 'limit': 200, 'site': 'Warehouse'})
                results[surface] = dict(cancelled=cancelled, voided=voided, after_void=after_void, first=first, again=again, second=second, sheet=sheet, preview=preview,
                                        stale=stale, recorded=recorded, moved=moved, shown=shown, found=found,
                                        worksheet=worksheet, detail=detail, readings=readings,
                                        valuation=valuation['totals']['asset_value']['minor_units'])
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['again']['entered'][0]['entries'] == r['first']['entered'][0]['entries'] == 1, surface
        assert r['second']['entered'][0]['counted'] == '1.5', surface        # 0.5 once, then 1
        assert r['sheet']['lines'][0]['expected'] == '1', surface
        assert [(d['item_name'], d['difference']) for d in r['preview']['differences']] == [(KIT, '0.5')], surface
        assert 'E_PREVIEW_STALE' in str(r['stale']), surface
        assert r['recorded']['status'] == 'recorded', surface
        assert any('derived again' in line for line in r['moved']['warnings']), surface
        # The unloaded kit landed before the count: the warehouse still holds what was counted.
        held = [row for row in r['readings'][DEMO_AS_OF] if row[0] == KIT and row[1] == 'Warehouse']
        assert held and held[0][2] == '1.5', surface
        assert r['shown']['lines'][0]['difference'] == '-0.5', surface
        assert r['found']['count'] == 1, surface
        assert [row['quantity_on_hand'] for row in r['worksheet']['rows'] if row['item_name'] == KIT] == ['1.5'], surface
        assert any(row['movement_kind'] == 'issue' and row['count_position'] == 'count' for row in r['detail']['rows']), surface
        assert r['cancelled']['status'] == 'cancelled' and r['voided']['status'] == 'voided', surface
        assert [row['quantity_on_hand'] for row in r['after_void']['rows'] if row['item_name'] == KIT] == ['2'], surface
        shapes.add((repr(r['readings']), r['valuation'], repr([(d['item_name'], d['difference'], d['value'])
                                                                 for d in r['recorded']['differences']])))
    assert len(shapes) == 1
