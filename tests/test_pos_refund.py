"""POS returns: same books as typed credit/refund, permanent recovery and bounded authority."""
import pytest
from tests.test_pos_sale import Company, _reads, _setup, twins  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401


def sale(co, *, customer=None, ticket='refund-sale', method='Cash'):
    cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '3'},
                      {'item': 'Pipe Sealant', 'quantity': '2'}, {'item': 'Mainline Clearing'}]}
    if customer:
        cart['customer'] = customer
    preview = co.run('pos sale preview', cart)
    sold = co.run('pos sale complete', dict(cart, ticket=ticket, expected_total=preview['total']['amount'],
                                          tender={'payment_method': method}))
    return sold


def request(co, sold, key='refund-one', qty='1'):
    available = co.run('pos refund lines', {'sale': sold['sales_receipt']['name']})
    return dict(operation_key=key, sale=sold['id'], lines=[{
        'source_line': available['lines'][0]['source_line'], 'quantity': qty}])


def test_partial_refund_matches_typed_credit_and_refund_books(twins):
    pos, typed = twins
    reg = _setup(pos)
    other = _setup(typed)
    sold, twin = sale(pos), sale(typed)
    before_stock = pos.run('item show', {'item': 'Brass Shutoff Valve'})['quantity_on_hand']
    body = request(pos, sold)
    preview = pos.run('pos refund', body, dry_run=True)
    assert pos.fails('pos refund show', {'refund': body['operation_key']}).code == 'E_RECORD_NOT_FOUND'
    ours = pos.run('pos refund', dict(body, expected_total=preview['total']['amount']))
    source = typed.run('pos refund lines', {'sale': twin['id']})['lines'][0]['source_line']
    memo = typed.run('credit-memo post', {'customer': other['walk_in_customer']['id'], 'lines': [{
        'source_invoice': twin['sales_receipt']['id'], 'source_line': source, 'quantity': '1',
        'site': other['site']['id']}]})
    paid = typed.run('customer-refund post', {'date': memo['date'], 'sources': [{'credit_memo': memo['id']}],
        'funding_account': other['refund_account']['id'], 'method': 'Cash'})
    # One of three valves at 24.95 returns 24.95 net; its captured tax and issued cost
    # are allocated by the same ordinary credit memo the bookkeeper enters below.
    assert ours['subtotal']['minor_units'] == 2495
    assert ours['total'] == memo['total'] == paid['total']
    assert _reads(pos) == _reads(typed)
    from decimal import Decimal
    assert Decimal(pos.run('item show', {'item': 'Brass Shutoff Valve'})['quantity_on_hand']) == Decimal(before_stock) + 1
    detail = pos.run('customer-refund show', {'refund': ours['refund']['id']})
    assert detail['funding_account_id'] == reg['refund_account']['id']
    events = pos.run('audit list', {'limit': 30})['items']
    assert not any(e['command'] in ('credit-memo post', 'customer-refund post') for e in events)
    error = pos.fails('pos sale void', {'sale': sold['id'], 'expected_version': 1})
    assert error.code == 'E_WORK_DEPENDENCY' and ours['refund']['name'] in error.message


def test_repeated_partial_quantity_bounds_retry_and_rollback(root):
    co = Company(root)
    _setup(co)
    sold = sale(co)
    body = request(co, sold)
    original = co.run('pos refund lines', {'sale': sold['id']})['lines'][0]
    one = co.run('pos refund', body)
    retry = co.run('pos refund', body)
    assert retry['id'] == one['id'] and retry['replayed']
    assert co.fails('pos refund', dict(body, settlement='store_credit')).code == 'E_IDEMPOTENCY_MISMATCH'
    assert co.fails('pos refund', request(co, sold, 'too-many', '3')).code == 'E_RETURN_EXHAUSTED'
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '2'
    two = co.run('pos refund', request(co, sold, 'refund-two'))
    three = co.run('pos refund', request(co, sold, 'refund-three'))
    assert len({one['id'], two['id'], three['id']}) == 3
    assert sum(r['subtotal']['minor_units'] for r in (one, two, three)) == original['net']['minor_units']
    assert sum(r['tax']['minor_units'] for r in (one, two, three)) == original['tax']['minor_units']
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '0'
    assert co.fails('pos refund', request(co, sold, 'refund-four')).code == 'E_RETURN_EXHAUSTED'
    other = sale(co, ticket='void-first')
    co.run('pos sale void', {'sale': other['id'], 'expected_version': 1})
    assert co.fails('pos refund lines', {'sale': other['id']}).code == 'E_WORK_DEPENDENCY'


def test_no_receipt_stock_and_store_credit(root):
    co = Company(root)
    _setup(co)
    body = {'operation_key': 'no-receipt', 'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '1', 'unit_price': '17.00'}]}
    preview = co.run('pos refund', body, dry_run=True)
    paid = co.run('pos refund', dict(body, expected_total=preview['total']['amount']))
    assert paid['refund'] and not paid['sale_id'] and not paid['customer']
    assert co.fails('pos refund', dict(body, operation_key='anonymous-credit', settlement='store_credit')).code == 'E_VALIDATION'
    stored = co.run('pos refund', dict(body, operation_key='named-credit', settlement='store_credit', customer='Tina Taxed'))
    assert stored['refund'] is None and stored['customer']['name'] == 'Tina Taxed'
    memo = co.run('credit-memo show', {'credit_memo': stored['credit_memo']['id']})
    assert memo['source_current']['available_minor_units'] == stored['total']['minor_units']


def test_original_method_and_invalid_total_leave_no_return(root):
    co = Company(root)
    _setup(co)
    sold = sale(co, method='Visa')
    body = dict(request(co, sold), settlement='original', reference='terminal-refund-42')
    assert co.fails('pos refund', dict(body, expected_total='0.01')).code == 'E_PREVIEW_STALE'
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '3'
    done = co.run('pos refund', body)
    assert done['payment_method']['name'] == 'Visa' and done['reference'] == 'terminal-refund-42'


def test_plain_clerk_refused_returns_clerk_allowed(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos
    cid = hosted.company_id
    _pos(hosted, True)
    cart = {'lines': [{'item': 'Pipe Sealant', 'quantity': '2'}]}
    preview = hosted.ok('pos.sale.preview', cart)
    sold = hosted.ok('pos.sale.complete', dict(cart, ticket='returns-authority-sale',
        expected_total=preview['total']['amount'], tender={'payment_method': 'Cash'}), headers=HEADERS)
    source = hosted.ok('pos.refund.lines', {'sale': sold['id']})['lines'][0]['source_line']
    for preset, username, permitted in [('pos-clerk', 'plain-refund', False), ('pos-clerk-returns', 'returns-refund', True)]:
        settings = pos_presets.preset(preset)
        user = hosted.ok('user.add', {'username': username, 'password': _password(username), 'company': cid,
                                     'role': 'readonly'}, headers=HEADERS)['user_id']
        hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly', 'grants': settings['grants'],
                   'denies': settings['denies'], 'expected_version': _membership_version(hosted, user)}, headers=HEADERS)
        with TestClient(hosted.handle.app) as till:
            assert till.post('/login', json={'username': username, 'password': _password(username)}).status_code == 200
            page = till.get(f'/c/{cid}/pos')
            assert page.status_code == 200
            assert ('data-pos-refund-open' in page.text) is permitted
            assert ('data-pos-refund-last' in page.text) is permitted
            response = till.post(f'/companies/{cid}/commands/pos.refund', headers={**H, **HEADERS}, json={
                'operation_key': username, 'lines': [{'item': 'Pipe Sealant', 'unit_price': '5.00'}]})
            if permitted:
                assert response.status_code == 200 and response.json()['refund'], response.text
            else:
                assert response.status_code == 403 and response.json()['details']['capability'] == 'pos.refund'
            linked = till.post(f'/companies/{cid}/commands/pos.refund', headers={**H, **HEADERS}, json={
                'operation_key': username + '-linked', 'sale': sold['id'],
                'lines': [{'source_line': source, 'quantity': '1'}]})
            if permitted:
                assert linked.status_code == 200 and linked.json()['refund'], linked.text
                # The internal admission does not grant the clerk ordinary accounting writes.
                direct = till.post(f'/companies/{cid}/commands/customer-refund.post', headers={**H, **HEADERS}, json={
                    'date': linked.json()['date'], 'sources': [{'credit_memo': linked.json()['credit_memo']['id']}],
                    'funding_account': 'Checking', 'method': 'Cash'})
                assert direct.status_code == 403 and direct.json()['details']['capability'] == 'ledger.post'
            else:
                assert linked.status_code == 403 and linked.json()['details']['capability'] == 'pos.refund'


def test_agent_without_returns_capability_is_refused(hosted):
    from fastapi.testclient import TestClient
    from tests.conftest import hosted_call, make_agent
    from tests.test_pos_layer import _pos, REASON
    _pos(hosted, True)
    cid = hosted.company_id
    owner = hosted.ok('user.home.show', {})['user_id']
    agent = make_agent(hosted_call(hosted), 'refund-bot', principals=owner, company=cid, role='admin')
    issued = hosted.ok('token.issue', {'user': agent, 'principal': owner, 'label': 'refund-test'})
    with TestClient(hosted.handle.app) as api:
        response = api.post(f'/companies/{cid}/commands/pos.refund',
            headers={'Authorization': f'Bearer {issued["secret"]}', **REASON},
            json={'operation_key': 'agent-refund', 'lines': [{'item': 'Pipe Sealant'}]})
        assert response.status_code == 403 and response.json()['details']['capability'] == 'pos.refund'


def test_manual_return_consumes_available_quantity_and_failed_payout_is_atomic(root, monkeypatch):
    from bookflow.company import refunds
    from bookflow.core.errors import BookflowError
    co = Company(root)
    reg = _setup(co)
    sold = sale(co)
    body = request(co, sold)
    memo = co.run('credit-memo post', {'customer': reg['walk_in_customer']['id'], 'lines': [{
        'source_invoice': sold['sales_receipt']['id'], 'source_line': body['lines'][0]['source_line'], 'quantity': '1'}]})
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '2'
    before = _reads(co)
    def refuse(*args, **kwargs):
        raise BookflowError('E_VALIDATION', message='Injected payout refusal')
    with monkeypatch.context() as patch:
        patch.setattr(refunds, 'apply', refuse)
        assert co.fails('pos refund', body).code == 'E_VALIDATION'
    assert _reads(co) == before
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '2'
    assert co.fails('pos refund show', {'refund': body['operation_key']}).code == 'E_RECORD_NOT_FOUND'
    assert co.run('pos refund', body)['refund']


def test_invalid_refund_account_refuses_preview_and_corrected_receipt_refuses_till(root):
    co = Company(root)
    reg = _setup(co)
    sold = sale(co)
    body = request(co, sold)
    co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'], 'refund_account': None})
    from bookflow import BookflowError
    with pytest.raises(BookflowError) as error:
        co.run('pos refund', body, dry_run=True)
    assert error.value.code == 'E_VALIDATION'
    co.run('sales-receipt update', {'sales_receipt': sold['sales_receipt']['id'], 'expected_version': 1, 'memo': 'Corrected'})
    assert co.fails('pos refund lines', {'sale': sold['id']}).code == 'E_WORK_DEPENDENCY'


def test_configured_walk_in_customer_cannot_hold_store_credit(root):
    co = Company(root)
    reg = _setup(co)
    co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'],
                                  'walk_in_customer': 'Tina Taxed'})
    body = {'operation_key': 'custom-walk-in', 'settlement': 'store_credit',
            'lines': [{'item': 'Mainline Clearing'}]}
    refusal = co.fails('pos refund', body)
    assert refusal.code == 'E_VALIDATION' and 'walk-in' in refusal.message
    cash = co.run('pos refund', dict(body, settlement='cash'))
    assert cash['customer'] is None
