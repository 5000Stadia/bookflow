"""R153: the same partial purchase order billing through Python, the CLI, HTTP, MCP and the workbench.

Each surface gets an identical copy of the demo: order ten copper elbows at 10.00, the vendor
bills four of them against the order ("the supplier billed us for 4 of the 10 on PO 1007"), the
rest is closed by hand, and the order and the open purchase orders report are read back. Every
surface must return the same figures. The workbench then does the same through its Bill or close
lines page.
"""
import anyio
import pytest
from fastapi.testclient import TestClient

from tests.mcp_matrix_support import Matrix
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

SURFACES = ('python', 'cli', 'http', 'mcp')


async def _order(call):
    income = (await call('account create', {'name': 'Elbow sales', 'type': 'income'}))['id']
    cogs = (await call('account create', {'name': 'Elbow cost', 'type': 'cost_of_goods_sold'}))['id']
    item = (await call('item create', {'name': 'Copper elbow R153', 'type': 'inventory_part', 'description': 'Elbow',
                                       'purchase_description': 'Elbow', 'price': '4.50', 'cost': '1.80',
                                       'cogs_account_id': cogs, 'income_account_id': income}))['id']
    vendor = (await call('vendor create', {'name': 'Elbow supply R153'}))['id']
    return await call('purchase-order post', {'date': '2026-03-01', 'vendor': vendor,
                                              'lines': [{'item': item, 'quantity': '10', 'rate': '10.00'}]})


@pytest.mark.timeout(900)
def test_the_same_partial_order_billing_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                po = await _order(call)
                bill = await call('bill post', {'purchase_order': po['id'], 'date': '2026-03-05',
                                                'order_lines': [{'line': '1', 'quantity': '4'}]})
                part = await call('purchase-order show', {'purchase_order': po['id']})
                report = await call('report open-purchase-orders', {'date_to': '2026-12-31'})
                closed = await call('purchase-order close-lines', {'purchase_order': po['id'], 'lines': ['1']})
                after = await call('report open-purchase-orders', {'date_to': '2026-12-31'})
                results[surface] = (po, bill, part, report, closed, after)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (po, bill, part, report, closed, after) in results.items():
        assert bill['total']['amount'] == '40.00' and bill['purchase_order_id'] == po['id'], surface
        line = part['line_tracking'][0]
        assert (line['received_quantity'], line['billed_quantity'], line['open_quantity']) == ('4', '4', '6'), surface
        assert part['status'] == 'partly_received' and part['part_bill_ids'] == [bill['id']], surface
        row = next(r for r in report['rows'] if r['purchase_order_id'] == po['id'])
        assert row['open_balance']['amount'] == '60.00' and row['received']['amount'] == '40.00', surface
        assert closed['status'] == 'closed' and closed['line_tracking'][0]['closed'] is True, surface
        assert po['id'] not in {r['purchase_order_id'] for r in after['rows']}, surface
        shapes.add(repr((bill['total'], [{k: v for k, v in x.items() if k not in ('line_id', 'item_id')}
                                          for x in part['line_tracking']], row['open_balance'], closed['status'])))
    assert len(shapes) == 1, shapes


HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def test_bill_part_and_close_lines_in_the_workbench(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id

    async def call(name, body):
        from bookflow.core import registry
        reason = {"X-Bookflow-Reason": "partial order witness"} if registry.get(name).is_write else {}
        answered = browser.post(f"/companies/{company}/commands/{name.replace(' ', '.')}", json=body,
                                headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    po = anyio.run(_order, call)
    line = po['revision']['lines'][0]['line_id']
    detail = browser.get(f"/c/{company}/purchase-order/{po['id']}", headers=HEADERS)
    assert detail.status_code == 200 and f"/purchase-order/{po['id']}/bill-part" in detail.text
    page = browser.get(f"/c/{company}/purchase-order/{po['id']}/bill-part", headers=HEADERS)
    assert page.status_code == 200 and f'name="qty:{line}"' in page.text and 'data-open>10<' in page.text

    over = browser.post(f"/c/{company}/purchase-order/{po['id']}/bill-part", headers=HEADERS,
                        data={"action": "bill", f"qty:{line}": "11", "date": "2026-03-05", "ctx:reason": "too many"})
    assert over.status_code == 400 and "data-error" in over.text and "remain open" in over.text

    billed = browser.post(f"/c/{company}/purchase-order/{po['id']}/bill-part", headers=HEADERS,
                          data={"action": "bill", f"qty:{line}": "4", "date": "2026-03-05",
                                "supplier_reference": "INV-1007", "ctx:reason": "Billed 4 of 10"})
    assert billed.status_code == 200 and "data-notice" in billed.text and "40.00" in billed.text
    assert 'data-open>6<' in billed.text and "/bill/" in billed.text

    closed = browser.post(f"/c/{company}/purchase-order/{po['id']}/bill-part", headers=HEADERS,
                          data={"action": "close", f"close:{line}": "1", "ctx:reason": "Rest backordered"})
    assert closed.status_code == 200 and "now closed" in closed.text and "data-closed" in closed.text
    reopened = browser.post(f"/c/{company}/purchase-order/{po['id']}/bill-part", headers=HEADERS,
                            data={"action": "reopen", f"close:{line}": "1", "ctx:reason": "Found them"})
    assert reopened.status_code == 200 and "now partly received" in reopened.text
