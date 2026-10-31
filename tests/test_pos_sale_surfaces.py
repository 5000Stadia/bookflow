"""R114 increment 5: ringing up through Python, the CLI, HTTP and MCP.

Each surface gets an identical demo copy and acts as its owner. It is refused a preview while POS is off, turns POS
on, previews a cart, completes it in cash, completes it again with the same ticket (the sale already posted comes
back), adds a customer and finds them again. Every surface ends with the same facts and the same refusal.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_row3_host import hosted  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401 - fixture

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


def test_price_override_checks_levels_rules_and_subcent_prices(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos

    cid = hosted.company_id
    _pos(hosted, True)

    def owner(name, body):
        from bookflow.core import registry
        headers = HEADERS if registry.get(name.replace('.', ' ')).is_write else None
        return hosted.ok(name, body, company=cid, headers=headers)

    info = owner('company.show', {})
    owner('company.update', {'enable_price_levels': True, 'expected_version': info['info_version']})
    income = owner('account.create', {'name': 'POS price test income', 'type': 'income'})['id']
    tax_code = owner('sales-tax-code.list', {})['items'][0]['id']
    items = {}
    for name, price in [('Subcent', '0.001'), ('Level', '0.00'), ('Rule', '0.00'), ('Free', '0.00')]:
        items[name] = owner('item.create', {'name': f'POS {name}', 'type': 'service', 'sales_enabled': True,
                                           'description': name, 'income_account_id': income, 'sales_tax_code_id': tax_code, 'price': price})['id']
    level = owner('price-level.create', {'name': 'POS nonzero level', 'kind': 'per_item', 'items': [
        {'item_id': items['Level'], 'price': '7.00', 'adjustment_basis': 'standard_price'}]})['id']
    customer = owner('customer.create', {'name': 'POS level customer', 'price_level_id': level})['id']
    owner('price-rule.set-preferences', {'conflict_policy': 'priority'})
    owner('price-rule.create', {'name': 'POS nonzero rule', 'items': [items['Rule']], 'kind': 'amount',
                                'direction': 'up', 'value': '4.00'})
    clerk = pos_presets.preset('pos-clerk')
    user = hosted.ok('user.add', {'username': 'price-till', 'password': _password('price-till'), 'company': cid,
                                  'role': 'readonly'}, headers=HEADERS)['user_id']
    hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly', 'grants': clerk['grants'],
                                   'denies': clerk['denies'], 'expected_version': _membership_version(hosted, user)},
              headers=HEADERS)
    with TestClient(hosted.handle.app) as till:
        assert till.post('/login', json={'username': 'price-till', 'password': _password('price-till')}).status_code == 200
        for name, expected in [('Subcent', '0.001'), ('Level', '7.00'), ('Rule', '4.00'), ('Free', '0.00')]:
            cart = {'customer': customer, 'lines': [{'item': items[name]}]}
            shown = owner('pos.sale.preview', cart)
            assert shown['lines'][0]['unit_price']['amount'] == expected
            cart['lines'][0]['unit_price'] = '2.00'
            response = till.post(f'/companies/{cid}/commands/pos.sale.preview', headers=H, json=cart)
            if name == 'Free':
                assert response.status_code == 200, response.text
                body = dict(cart, ticket='price-changed-retry', expected_total=response.json()['total']['amount'],
                            tender={'payment_method': 'Cash'})
                url = f'/companies/{cid}/commands/pos.sale.complete'
                first = till.post(url, headers=H, json=body)
                assert first.status_code == 200, first.text
                owner('price-rule.create', {'name': 'Free now priced', 'items': [items['Free']],
                                            'kind': 'amount', 'direction': 'up', 'value': '5.00'})
                replay = till.post(url, headers=H, json=body)
                assert replay.status_code == 200 and replay.json()['replayed'], replay.text
                assert replay.json()['sales_receipt'] == first.json()['sales_receipt']
            else:
                assert response.status_code == 403, response.text
                assert response.json()['details']['capability'] == 'pos.price-override'
