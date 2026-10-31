"""POS voids share the receipt reversal, preserving books, stock, history and authority."""
import pytest

import bookflow
from tests.test_pos_sale import Company, _norm, _reads, _setup, twins  # noqa: F401
from tests.test_row3_host import hosted  # noqa: F401


def _sale(co, ticket='void-ticket'):
    cart = {'lines': [{'item': 'Brass Shutoff Valve', 'quantity': '2'}, {'item': 'Mainline Clearing'}]}
    shown = co.run('pos sale preview', cart)
    return co.run('pos sale complete', dict(cart, ticket=ticket, expected_total=shown['total']['amount'],
                                            tender={'payment_method': 'Cash'})), cart


def test_void_has_the_same_books_stock_and_history_as_a_typed_receipt(twins):
    pos, typed = twins
    _setup(pos)
    register = _setup(typed)
    before = _reads(pos)
    sale, cart = _sale(pos)
    receipt = typed.run('sales-receipt post', dict(cart, customer=register['walk_in_customer']['id'],
                       site=register['site']['id'], deposit_to=register['deposit_account']['id'], payment_method='Cash'))
    preview = pos.run('pos sale void', {'sale': sale['id'], 'expected_version': 1}, dry_run=True)
    assert preview['status'] == 'voided' and preview['changed']
    assert pos.run('sales-receipt show', {'sales_receipt': sale['sales_receipt']['id']})['status'] == 'posted'
    ours = pos.run('pos sale void', {'sale': sale['ticket'], 'expected_version': 1}, reason='Customer canceled')
    theirs = typed.run('sales-receipt void', {'sales_receipt': receipt['id'], 'expected_version': 1},
                      reason='Customer canceled')
    assert {key: ours[key] for key in ('number', 'status', 'version', 'changed')} == {
        key: theirs[key] for key in ('number', 'status', 'version', 'changed')}
    assert 'revision' not in ours
    assert _norm(pos.run('sales-receipt show', {'sales_receipt': ours['id']})) == _norm(
        typed.run('sales-receipt show', {'sales_receipt': theirs['id']}))
    assert ours['status'] == 'voided' and ours['version'] == 2 and ours['changed']
    assert _reads(pos) == _reads(typed)
    assert _reads(pos)['stock'] == before['stock']
    events = pos.run('audit list', {'limit': 30})['items']
    assert any(event['command'] == 'pos sale void' for event in events)
    assert not any(event['command'] == 'sales-receipt void' for event in events)
    history = pos.run('sales-receipt history', {'sales_receipt': sale['sales_receipt']['id']})
    twin_history = typed.run('sales-receipt history', {'sales_receipt': receipt['id']})
    assert _norm(history) == _norm(twin_history)
    # Repeated void with the current version is a no-op, not another reversal.
    after = _reads(pos)
    repeated = pos.run('pos sale void', {'sale': sale['sales_receipt']['name'], 'expected_version': 2})
    assert not repeated['changed'] and repeated['version'] == 2 and _reads(pos) == after


def test_void_requires_reason_version_and_a_pos_sale(root):
    co = Company(root)
    assert co.fails('pos sale void', {'sale': 'missing', 'expected_version': 1}).code == 'E_FEATURE_DISABLED'
    register = _setup(co)
    sale, cart = _sale(co)
    with pytest.raises(bookflow.BookflowError) as error:
        co.c.run('pos sale void', {'sale': sale['id'], 'expected_version': 1}, company=co.cid, reason='')
    assert error.value.code == 'E_REASON_REQUIRED'
    assert co.fails('pos sale void', {'sale': sale['id']}).code == 'E_VALIDATION'
    stale = co.fails('pos sale void', {'sale': sale['id'], 'expected_version': 5})
    assert stale.code == 'E_VERSION_CONFLICT' and stale.details['current_version'] == 1
    ordinary = co.run('sales-receipt post', dict(cart, customer=register['walk_in_customer']['id'],
                      deposit_to=register['deposit_account']['id'], payment_method='Cash'))
    for selector in (ordinary['id'], ordinary['number'], 'missing-sale'):
        assert co.fails('pos sale void', {'sale': selector, 'expected_version': 1}).code == 'E_RECORD_NOT_FOUND'
    assert co.run('sales-receipt show', {'sales_receipt': sale['sales_receipt']['id']})['status'] == 'posted'


def test_returns_clerk_voids_but_plain_clerk_cannot_and_banked_sale_names_deposit(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON as HEADERS, _password, _pos

    cid = hosted.company_id
    _pos(hosted, True)

    def owner(name, data):
        from bookflow.core import registry
        return hosted.ok(name.replace(' ', '.'), data, company=cid,
                         headers=HEADERS if registry.get(name).is_write else None)

    cart = {'lines': [{'item': 'Pipe Sealant'}]}
    total = owner('pos sale preview', cart)['total']['amount']
    sale = owner('pos sale complete', dict(cart, ticket='clerk-void', expected_total=total,
                                          tender={'payment_method': 'Cash'}))
    banked = owner('pos sale complete', dict(cart, ticket='clerk-banked', expected_total=total,
                                            tender={'payment_method': 'Cash'}))
    bank = owner('account create', {'name': 'POS void bank', 'type': 'bank'})['id']
    deposit = owner('deposit post', {'operation_key': 'pos-void-deposit', 'document': {
        'mode': 'inline', 'deposit_to': bank, 'date': banked['date'], 'sources': [{
            'source_type': 'sales_receipt', 'source': banked['sales_receipt']['id'], 'expected_version': 1}]}})['deposit']
    for preset, username, permitted in [('pos-clerk', 'plain-void', False),
                                         ('pos-clerk-returns', 'returns-void', True)]:
        settings = pos_presets.preset(preset)
        user = hosted.ok('user.add', {'username': username, 'password': _password(username), 'company': cid,
                                     'role': 'readonly'}, headers=HEADERS)['user_id']
        hosted.ok('membership.grant', {'user': user, 'company': cid, 'role': 'readonly', 'grants': settings['grants'],
                   'denies': settings['denies'], 'expected_version': _membership_version(hosted, user)}, headers=HEADERS)
        with TestClient(hosted.handle.app) as till:
            assert till.post('/login', json={'username': username, 'password': _password(username)}).status_code == 200
            url = f'/companies/{cid}/commands/pos.sale.void'
            response = till.post(url, headers={**H, **HEADERS}, json={'sale': sale['id'], 'expected_version': 1})
            if permitted:
                assert response.status_code == 200 and response.json()['status'] == 'voided', response.text
                assert set(response.json()) == {'sale_id', 'ticket', 'id', 'number', 'status', 'version',
                                                'changed', 'warnings', 'dry_run'}, response.text
                refused = till.post(url, headers={**H, **HEADERS}, json={'sale': banked['id'], 'expected_version': 1})
                assert refused.json()['code'] == 'E_DEPOSIT_DEPENDENCY', refused.text
                assert refused.json()['details']['deposit'] == deposit['id']
                assert refused.json()['details']['deposit_number'] == deposit['number']
                assert deposit['number'] in refused.json()['message']
                # POS authority does not grant the general receipt command.
                ordinary = till.post(f'/companies/{cid}/commands/sales-receipt.void', headers={**H, **HEADERS},
                                     json={'sales_receipt': banked['sales_receipt']['id'], 'expected_version': 1})
                assert ordinary.status_code == 403
            else:
                assert response.status_code == 403 and response.json()['details']['capability'] == 'pos.void', response.text
    assert owner('sales-receipt show', {'sales_receipt': banked['sales_receipt']['id']})['status'] == 'posted'


def test_a_reconciled_direct_to_bank_pos_sale_is_refused(root):
    from tests.test_card_reconciliation import _statement
    co = Company(root)
    register = _setup(co)
    bank = co.run('account create', {'name': 'POS reconciled bank', 'type': 'bank'})['id']
    co.run('pos-register update', {'pos_register': register['id'], 'expected_version': register['version'],
                                   'deposit_account': bank})
    sale, _ = _sale(co)
    opening = co.run('reconcile opening start', {'operation_key': 'pos-opening', 'account': bank,
        'opening_date': '2020-01-01', 'entered_balance': '0.00',
        'evidence': {'format': 1, 'statement_reference': None, 'entered_text': 'Adopted at zero'}, 'references': []})
    _statement({'run': co.run}, sale['date'], sale['total']['amount'], opening['draft']['id'], account=bank)
    before = _reads(co)
    refused = co.fails('pos sale void', {'sale': sale['id'], 'expected_version': 1})
    assert refused.code == 'E_RECONCILIATION_DEPENDENCY'
    assert refused.details['sales_receipt_id'] == sale['sales_receipt']['id']
    assert _reads(co) == before
