"""Split sale returns reverse every original posting and allocate payouts in tender order."""
from decimal import Decimal

import pytest

from tests.test_pos_sale import Company, _reads, _setup


def split(co, key='split-returns'):
    cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '2'}]}
    total = co.run('pos sale preview', cart)['total']['amount']
    return co.run('pos sale complete', dict(cart, ticket=key, expected_total=total, tenders=[
        {'payment_method': 'Cash', 'amount': '15.00'},
        {'payment_method': 'Visa', 'amount': str(Decimal(total) - 10)}]))


def refund_request(co, sold, key):
    source = co.run('pos refund lines', {'sale': sold['id']})['lines'][0]
    return dict(sale=sold['id'], operation_key=key, settlement='original',
                lines=[{'source_line': source['source_line'], 'quantity': '1'}])


def test_split_void_reverses_all_parts_and_retry_is_noop(root):
    co = Company(root)
    _setup(co)
    before = _reads(co)
    sold = split(co)
    invoice = co.run('invoice show', {'invoice': sold['sales_receipt']['id']})
    body = {'sale': sold['id'], 'expected_version': invoice['version']}
    preview = co.run('pos sale void', body, dry_run=True)
    assert preview['changed']
    assert co.run('invoice show', {'invoice': invoice['id']})['status'] == 'posted'
    done = co.run('pos sale void', body)
    assert done['status'] == 'voided' and done['version'] == preview['version']
    after = _reads(co)
    for key in ('trial', 'stock', 'valuation', 'undeposited'):
        assert after[key] == before[key], key
    repeated = co.run('pos sale void', {'sale': sold['id'], 'expected_version': done['version']})
    assert not repeated['changed'] and repeated['version'] == done['version']
    assert co.run('pos receipt show', {'sale': sold['id']})['status'] == 'voided'
    assert co.fails('pos refund lines', {'sale': sold['id']}).code == 'E_WORK_DEPENDENCY'


def test_split_partial_refund_uses_original_order_and_remaining_capacity(root):
    co = Company(root)
    _setup(co)
    sold = split(co)
    assert sold['change']['amount'] == '5.00'
    body = refund_request(co, sold, 'split-refund-one')
    one = co.run('pos refund', body)
    assert [(p['payment_method']['name'], p['amount']['amount']) for p in one['tenders']] == [
        ('Cash', '10.00'), ('Visa', str(Decimal(one['total']['amount']) - 10))]
    assert one['subtotal']['minor_units'] == 2495
    assert one['refund'] == one['tenders'][0]['refund']
    for payout in one['tenders']:
        actual = co.run('customer-refund show', {'refund': payout['refund']['id']})
        assert actual['total'] == payout['amount']
        assert actual['payment_method_id'] == payout['payment_method']['id']
    receipt = co.run('pos refund receipt', {'sale': one['id']})
    assert [p['amount'] for p in receipt['sale']['tenders']] == [p['amount'] for p in one['tenders']]
    retry = co.run('pos refund', body)
    assert retry['replayed'] and retry['tenders'] == one['tenders']
    two = co.run('pos refund', refund_request(co, sold, 'split-refund-two'))
    assert len(two['tenders']) == 1 and two['tenders'][0]['payment_method']['name'] == 'Visa'
    assert one['total']['minor_units'] + two['total']['minor_units'] == sold['total']['minor_units']
    assert co.fails('pos sale void', {'sale': sold['id'], 'expected_version': sold['version']}).code == 'E_WORK_DEPENDENCY'


def test_split_second_payout_failure_rolls_back_credit_first_payout_and_claim(root, monkeypatch):
    from bookflow.company import refunds
    from bookflow.core.errors import BookflowError
    co = Company(root)
    _setup(co)
    sold = split(co)
    body = refund_request(co, sold, 'split-refund-atomic')
    original = refunds.apply
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise BookflowError('E_VALIDATION', message='Second payout refused')
        return original(*args, **kwargs)
    before = _reads(co)
    with monkeypatch.context() as patch:
        patch.setattr(refunds, 'apply', fail_second)
        assert co.fails('pos refund', body).code == 'E_VALIDATION'
    assert _reads(co) == before
    assert co.run('pos refund lines', {'sale': sold['id']})['lines'][0]['available_quantity'] == '2'
    assert co.fails('pos refund show', {'refund': body['operation_key']}).code == 'E_RECORD_NOT_FOUND'


from tests.test_row3_host import hosted  # noqa: E402,F401


def test_returns_clerk_can_void_and_refund_split_without_ledger_permission(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos
    _pos(hosted, True)
    cid = hosted.company_id
    user = hosted.ok('user.add', {'username': 'split-returns-clerk', 'password': _password('split-returns-clerk'),
        'company': cid, 'role': 'readonly'}, headers=HEADERS)['user_id']
    preset = pos_presets.preset('pos-clerk-returns')
    hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly',
        'grants': preset['grants'], 'denies': preset['denies'],
        'expected_version': _membership_version(hosted, user)}, headers=HEADERS)
    with TestClient(hosted.handle.app) as till:
        assert till.post('/login', json={'username': 'split-returns-clerk',
            'password': _password('split-returns-clerk')}).status_code == 200
        def call(name, body):
            response = till.post(f'/companies/{cid}/commands/{name}', headers={**H, **HEADERS}, json=body)
            assert response.status_code == 200, response.text
            return response.json()
        cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '2'}]}
        total = call('pos.sale.preview', cart)['total']['amount']
        for action in ('void', 'refund'):
            sold = call('pos.sale.complete', dict(cart, ticket='split-clerk-' + action, expected_total=total,
                tenders=[{'payment_method': 'Cash', 'amount': '10'},
                         {'payment_method': 'Visa', 'amount': str(Decimal(total) - 10)}]))
            if action == 'void':
                assert call('pos.sale.void', {'sale': sold['id'], 'expected_version': sold['version']})['status'] == 'voided'
            else:
                line = call('pos.refund.lines', {'sale': sold['id']})['lines'][0]
                returned = call('pos.refund', {'sale': sold['id'], 'operation_key': 'clerk-split-refund',
                    'settlement': 'original', 'lines': [{'source_line': line['source_line'], 'quantity': '1'}]})
                assert len(returned['tenders']) == 2


def test_split_banked_second_part_refuses_entire_void(root):
    co = Company(root)
    reg = _setup(co)
    sold = split(co)
    card = co.run('payment query', {'customer': reg['walk_in_customer']['id'], 'payment_method': 'Visa'})['items'][0]
    deposit = co.run('deposit post', {'operation_key': 'split-bank-card', 'document': {
        'mode': 'inline', 'deposit_to': reg['refund_account']['id'], 'date': sold['date'],
        'sources': [{'source_type': 'payment', 'source': card['id'], 'expected_version': card['version']}]}})['deposit']
    before = _reads(co)
    refused = co.fails('pos sale void', {'sale': sold['id'], 'expected_version': sold['version']})
    assert refused.code == 'E_DEPOSIT_DEPENDENCY' and refused.details['deposit'] == deposit['id']
    assert _reads(co) == before
    assert co.run('invoice show', {'invoice': sold['sales_receipt']['id']})['status'] == 'posted'
