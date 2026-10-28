"""R181 increment 1: shipments through Python, the CLI, HTTP and MCP, the shared scenario extended.

Each surface gets an identical demo copy. It stocks a new part, takes a sales order for six, previews and posts a
shipment of four (retried under one idempotency key, a lost response), records tracking on it, reads it back through
show, query, history and the Shipped Not Invoiced report, invoices it, and is refused voiding it while that invoice
bills it. Every surface must end with the same shipment, the same cost waiting and the same invoice.
"""
import anyio
import pytest

from tests.demo_oracle import DEMO_AS_OF
from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('shipment post', 'shipment update', 'shipment void', 'shipment show', 'shipment query',
                      'shipment history', 'shipment invoice', 'report shipped-not-invoiced',
                      # R181 increment 2: one of the four comes back (retried), is read back, and the return voided.
                      'shipment return', 'shipment-return show', 'shipment-return query', 'shipment-return void',
                      # R181 increment 3: picking turned on, a list for an order of two, a retried pick, read back,
                      # shipped from the list; a second list closed and a third cancelled.
                      'pick-list create', 'pick-list enter', 'pick-list show', 'pick-list query', 'pick-list close',
                      'pick-list cancel',
                      # R181 increment 4: a draft made, packed (retried), shipped with its invoice; another cancelled.
                      'shipment create', 'shipment pack', 'shipment cancel',
                      # R181 increment 5: the worksheet and the shipments report, read after.
                      'report order-fulfillment', 'report shipments'))


@pytest.mark.timeout(900)
def test_the_same_shipment_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = matrix.call
                accounts = (await call(surface, 'account query', {'limit': 200}))['items']
                cogs = next(a['id'] for a in accounts if a['type'] == 'cost_of_goods_sold')
                income = next(a['id'] for a in accounts if a['type'] == 'income')
                item = await call(surface, 'item create', {'name': 'Shipping Test Part', 'type': 'inventory_part',
                                                           'description': 'Part', 'price': '20.00', 'cost': '3.00',
                                                           'purchase_description': 'Part', 'cogs_account_id': cogs,
                                                           'income_account_id': income})
                vendor = (await call(surface, 'vendor query', {'limit': 1}))['items'][0]['id']
                customer = (await call(surface, 'customer query', {'limit': 1}))['items'][0]['id']
                await call(surface, 'bill post', {'vendor': vendor, 'date': DEMO_AS_OF, 'items': [
                    {'item': item['id'], 'quantity': '10', 'unit_cost': '3.00'}]})
                order = await call(surface, 'sales-order create', {'customer': customer, 'date': DEMO_AS_OF, 'lines': [
                    {'item': item['id'], 'quantity': '6', 'unit_price': '20.00'}]})
                payload = {'sales_order': order['number'], 'date': DEMO_AS_OF,
                           'lines': [{'item': item['id'], 'quantity': '4'}]}
                preview = await call(surface, 'shipment post', payload, dry_run=True)
                first = await call(surface, 'shipment post', payload, idempotency_key='ship-a')
                again = await call(surface, 'shipment post', payload, idempotency_key='ship-a')
                tracked = await call(surface, 'shipment update', {'shipment': first['number'], 'expected_version': 1,
                                                                  'tracking_numbers': ['1Z999']})
                shown = await call(surface, 'shipment show', {'shipment': first['number']})
                listed = await call(surface, 'shipment query', {'not_invoiced': True})
                history = await call(surface, 'shipment history', {'shipment': first['number']})
                waiting = await call(surface, 'report shipped-not-invoiced', {'as_of': DEMO_AS_OF})
                back_input = {'shipment': first['number'], 'date': DEMO_AS_OF,
                              'lines': [{'shipment_line': '1', 'quantity': '1'}]}
                back = await call(surface, 'shipment return', back_input, idempotency_key='back-a')
                back_again = await call(surface, 'shipment return', back_input, idempotency_key='back-a')
                back_shown = await call(surface, 'shipment-return show', {'shipment_return': back['number']})
                back_listed = await call(surface, 'shipment-return query', {'shipment': first['number']})
                back_void = await call(surface, 'shipment-return void', {'shipment_return': back['number'],
                                                                         'expected_version': 1})
                invoice = await call(surface, 'shipment invoice', {'shipment': first['number'], 'date': DEMO_AS_OF})
                refused = await call(surface, 'shipment void', {'shipment': first['number']}, rejected=True)
                after = await call(surface, 'report shipped-not-invoiced', {'as_of': DEMO_AS_OF})
                await call(surface, 'company update', {'pick_and_pack_enabled': True})
                picked_order = await call(surface, 'sales-order create', {'customer': customer, 'date': DEMO_AS_OF,
                                                                          'lines': [{'item': item['id'], 'quantity': '2',
                                                                                     'unit_price': '20.00'}]})
                pick = await call(surface, 'pick-list create', {'orders': [picked_order['number']], 'date': DEMO_AS_OF})
                pick_input = {'pick_list': pick['number'], 'date': DEMO_AS_OF,
                              'entries': [{'item': item['id'], 'quantity': '2'}]}
                picked = await call(surface, 'pick-list enter', pick_input, idempotency_key='pick-a')
                picked_again = await call(surface, 'pick-list enter', pick_input, idempotency_key='pick-a')
                pick_shown = await call(surface, 'pick-list show', {'pick_list': pick['number']})
                pick_listed = await call(surface, 'pick-list query', {'status': 'open'})
                from_list = await call(surface, 'shipment post', {'pick_list': pick['number'], 'date': DEMO_AS_OF})
                spare = await call(surface, 'sales-order create', {'customer': customer, 'date': DEMO_AS_OF,
                                                                   'lines': [{'item': item['id'], 'quantity': '1',
                                                                              'unit_price': '20.00'}]})
                second = await call(surface, 'pick-list create', {'orders': [spare['number']], 'date': DEMO_AS_OF})
                closed = await call(surface, 'pick-list close', {'pick_list': second['number']})
                cancelled = await call(surface, 'pick-list cancel', {'pick_list': second['number']})
                draft = await call(surface, 'shipment create', {'sales_order': spare['number'], 'date': DEMO_AS_OF})
                pack_input = {'shipment': draft['number'], 'line': '1', 'box': 1, 'expected_version': 1}
                packed = await call(surface, 'shipment pack', pack_input, idempotency_key='pack-a')
                packed_again = await call(surface, 'shipment pack', pack_input, idempotency_key='pack-a')
                from_draft = await call(surface, 'shipment post', {'shipment': draft['number'], 'date': DEMO_AS_OF,
                                                                   'invoice': True})
                third = await call(surface, 'sales-order create', {'customer': customer, 'date': DEMO_AS_OF,
                                                                   'lines': [{'item': item['id'], 'quantity': '1',
                                                                              'unit_price': '20.00'}]})
                dropped = await call(surface, 'shipment create', {'sales_order': third['number']})
                dropped = await call(surface, 'shipment cancel', {'shipment': dropped['number'], 'expected_version': 1})
                worksheet = await call(surface, 'report order-fulfillment', {'customer': customer, 'limit': 200})
                shipped = await call(surface, 'report shipments', {'date_from': DEMO_AS_OF, 'date_to': DEMO_AS_OF,
                                                                   'customer': customer})
                results[surface] = dict(worksheet=worksheet, shipped=shipped, draft=draft, packed=packed, packed_again=packed_again, from_draft=from_draft,
                                        dropped=dropped, pick=pick, picked=picked, picked_again=picked_again, pick_shown=pick_shown,
                                        pick_listed=pick_listed, from_list=from_list, closed=closed, cancelled=cancelled,
                                        preview=preview, first=first, again=again, tracked=tracked, shown=shown,
                                        listed=listed, history=history, waiting=waiting, invoice=invoice,
                                        refused=refused, after=after, back=back, back_again=back_again,
                                        back_shown=back_shown, back_listed=back_listed, back_void=back_void)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, r in results.items():
        assert r['first']['number'] == r['again']['number'], surface
        assert r['preview']['cost'] == r['first']['cost'] and r['first']['cost']['amount'] == '12.00', surface
        assert r['tracked']['version'] == 2 and r['shown']['tracking_numbers'] == ['1Z999'], surface
        assert [row['number'] for row in r['listed']['rows']] == [r['first']['number']], surface
        assert [h['version'] for h in r['history']['history']] == [1, 2], surface
        assert r['waiting']['total'] == r['waiting']['account_balance'], surface
        assert [line['quantity'] for line in r['invoice']['revision']['lines']] == ['4'], surface
        assert 'E_WORK_DEPENDENCY' in str(r['refused']), surface
        assert r['after']['total']['minor_units'] == r['after']['account_balance']['minor_units'], surface
        assert r['back']['number'] == r['back_again']['number'] and r['back']['value']['amount'] == '3.00', surface
        assert r['back_shown']['status'] == 'returned' and [x['number'] for x in r['back_listed']['rows']] == [r['back']['number']]
        assert r['back_void']['status'] == 'voided' and r['back_void']['version'] == 2, surface
        shapes.add((r['first']['number'], r['first']['cost']['minor_units'], r['waiting']['total']['minor_units'],
                    r['after']['total']['minor_units'], r['invoice']['total']['minor_units']))
        assert r['picked']['lines'][0]['picked'] == '2' and r['picked_again']['lines'][0]['picked'] == '2', surface
        assert r['pick_shown']['lines'][0]['picked'] == '2' and r['pick']['number'] in [
            row['number'] for row in r['pick_listed']['rows']], surface
        assert [line['quantity'] for line in r['from_list']['lines']] == ['2'], surface
        assert r['closed']['status'] == 'done' and r['cancelled']['status'] == 'cancelled', surface
        shapes.add(('pick', r['pick']['number'], r['from_list']['cost']['minor_units']))
        assert r['draft']['status'] == 'draft' and r['packed']['packing']['packed'] is True, surface
        assert r['packed_again']['version'] == r['packed']['version'] == 2, surface
        assert r['from_draft']['number'] == r['draft']['number'] and r['from_draft']['invoice']['id'], surface
        assert len(r['from_draft']['packing']['packages']) == 1 and r['dropped']['status'] == 'cancelled', surface
        shapes.add(('pack', r['draft']['number'], r['from_draft']['invoice']['total']['minor_units']))
        shipped = [row['shipment_number'] for row in r['shipped']['rows']]
        assert r['from_draft']['number'] in shipped and r['first']['number'] in shipped, surface
        assert r['shipped']['rows'][shipped.index(r['from_draft']['number'])]['boxes'] == 1, surface
        shapes.add(('sheet', tuple((row['sales_order_number'], row['fulfillment_status'], row['shippable_amount']['amount'])
                                   for row in r['worksheet']['rows']), tuple(shipped)))
    assert len(shapes) == 4
