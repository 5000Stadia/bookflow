"""Day close arithmetic, durable recovery and clerk authority."""
from datetime import timedelta

import pytest

from tests.test_pos_sale import Company, _setup, _reads
from tests.test_row3_host import hosted  # noqa: F401

SURFACES = ('python',)
COMMANDS = frozenset(('pos shift open', 'pos shift close', 'report pos-day'))


def counted_register(co):
    reg = _setup(co)
    return co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'],
                                         'count_shifts': True})


def sell(co, key, *, quantity='1', method='Cash', tender=None, customer='Eddie Exempt'):
    cart = {'customer': customer, 'lines': [{'item': 'Pipe Sealant', 'quantity': quantity, 'unit_price': '10.00'}]}
    preview = co.run('pos sale preview', cart)
    payment = {'payment_method': method}
    if tender is not None:
        payment['amount'] = tender
    return co.run('pos sale complete', dict(cart, ticket=key, expected_total=preview['total']['amount'], tender=payment))


def test_shift_day_ties_by_hand(root):
    co = Company(root)
    counted_register(co)
    shift = co.run('pos shift open', {'float': '50.00', 'operation_key': 'morning'})
    cash = sell(co, 'cash-two', quantity='2', tender='30.00')
    card = sell(co, 'card-one', method='Visa')
    void = sell(co, 'void-one')
    co.run('pos sale void', {'sale': void['id'], 'expected_version': 1})
    lines = co.run('pos refund lines', {'sale': cash['id']})['lines']
    co.run('pos refund', {'operation_key': 'return-one', 'sale': cash['id'],
                         'lines': [{'source_line': lines[0]['source_line'], 'quantity': '1'}]})
    report = co.run('report pos-day', {'shift': shift['id']})
    # Two live receipts: cash 20 + card 10. One 10 cash return; the void contributes no cash.
    for name, amount in {'gross_sales': 3000, 'total_sales': 3000, 'tax': 0, 'discounts': 0,
                         'cash_sales': 2000, 'cash_refunds': 1000, 'cash_to_deposit': 1000,
                         'expected_drawer': 6000, 'voids_total': 1000, 'refunds_total': 1000}.items():
        assert report[name]['minor_units'] == amount, name
    assert report['sales_count'] == 2 and report['voids_count'] == 1
    methods = {r['payment_method']['name']: r for r in report['by_tender']}
    assert methods['Cash']['amount']['minor_units'] == 2000
    assert methods['Cash']['refund_amount']['minor_units'] == 1000
    assert methods['Visa']['amount']['minor_units'] == 1000
    assert cash['change']['minor_units'] == 1000
    before = _reads(co)
    closed = co.run('pos shift close', {'shift': shift['id'], 'counted': '59.50', 'operation_key': 'evening'})
    assert closed['expected']['minor_units'] == 6000
    assert closed['over_short']['minor_units'] == -50
    assert closed['status'] == 'closed'
    assert _reads(co) == before  # Counting posts nothing.
    assert co.fails('pos sale void', {'sale': card['id'], 'expected_version': 1}).code == 'E_WORK_DEPENDENCY'
    assert co.run('report pos-day', {'shift': shift['id']})['expected_drawer']['minor_units'] == 6000


def test_shift_unique_open_permanent_recovery_and_closed_immutable(root):
    co = Company(root)
    counted_register(co)
    body = {'float': '25', 'operation_key': 'open-once'}
    shift = co.run('pos shift open', body)
    info = co.run('company show')
    assert co.fails('company update', {'pos_enabled': False, 'expected_version': info['info_version']}).code == 'E_WORK_DEPENDENCY'
    again = co.run('pos shift open', body)
    assert again['id'] == shift['id'] and again['replayed']
    assert co.fails('pos shift open', dict(body, operation_key='open-twice')).code == 'E_WORK_DEPENDENCY'
    assert co.fails('pos shift open', dict(body, float='26')).code == 'E_IDEMPOTENCY_MISMATCH'
    close = {'shift': shift['id'], 'counted': '26', 'operation_key': 'close-once'}
    done = co.run('pos shift close', close)
    assert done['over_short']['minor_units'] == 100
    replay = co.run('pos shift close', close)
    assert replay['replayed'] and replay['id'] == done['id']
    assert co.fails('pos shift close', dict(close, counted='27')).code == 'E_IDEMPOTENCY_MISMATCH'
    assert co.fails('pos shift close', dict(close, operation_key='close-twice')).code == 'E_WORK_DEPENDENCY'
    next_shift = co.run('pos shift open', dict(body, operation_key='next-open'))
    assert next_shift['id'] != shift['id']
    # A retry of yesterday's open cannot open a second drawer or mutate the closed record.
    assert co.run('pos shift open', body)['status'] == 'closed'


def test_day_tax_and_old_held_ticket(root, monkeypatch):
    from bookflow.core import clock
    co = Company(root)
    _setup(co)
    now = clock.now()
    with monkeypatch.context() as patch:
        patch.setattr(clock, 'now', lambda: now - timedelta(days=8))
        held = co.run('pos ticket hold', {'ticket': 'old-held', 'label': 'Old special order',
                                         'lines': [{'item': 'Pipe Sealant'}]})
    sale = sell(co, 'taxed-sale', quantity='2', customer='Tina Taxed')
    receipt = co.run('sales-receipt show', {'sales_receipt': sale['sales_receipt']['id']})
    report = co.run('report pos-day')
    assert report['tax'] == receipt['tax']
    assert report['tax']['minor_units'] > 0
    assert report['total_sales'] == sale['total']
    old = next(row for row in report['held_tickets'] if row['id'] == held['id'])
    assert old['older_than_seven_days'] and old['label'] == 'Old special order'


def test_plain_clerk_can_count_but_cannot_prepare_or_post_deposit(hosted):
    from fastapi.testclient import TestClient
    from bookflow.hub import pos_presets
    from tests.test_item_pictures_review import _membership_version
    from tests.test_pos_layer import H, REASON, _password, _pos
    _pos(hosted, True)
    reg = hosted.ok('pos-register.show', {'pos_register': 'Main'})
    hosted.ok('pos-register.update', {'pos_register': reg['id'], 'expected_version': reg['version'],
                                      'count_shifts': True}, headers=REASON)
    settings = pos_presets.preset('pos-clerk')
    user = hosted.ok('user.add', {'username': 'drawer-clerk', 'password': _password('drawer-clerk'),
        'company': hosted.company_id, 'role': 'readonly'}, headers=REASON)['user_id']
    hosted.ok('membership.grant', {'user': user, 'company': hosted.company_id, 'role': 'readonly',
        'grants': settings['grants'], 'denies': settings['denies'],
        'expected_version': _membership_version(hosted, user)}, headers=REASON)
    with TestClient(hosted.handle.app) as api:
        assert api.post('/login', json={'username': 'drawer-clerk', 'password': _password('drawer-clerk')}).status_code == 200
        def call(command, body):
            return api.post(f'/companies/{hosted.company_id}/commands/{command}', headers={**H, **REASON}, json=body)
        opened = call('pos.shift.open', {'float': '50', 'operation_key': 'clerk-open'})
        assert opened.status_code == 200, opened.text
        shift = opened.json()['id']
        closed = call('pos.shift.close', {'shift': shift, 'counted': '50', 'operation_key': 'clerk-close'})
        assert closed.status_code == 200, closed.text
        assert call('report.pos-day', {'shift': shift}).status_code == 200
        denied = call('pos.deposit.prepare', {'shift': shift})
        assert denied.status_code == 403 and denied.json()['details']['capability'] == 'ledger.post'
        denied = call('deposit.post', {'operation_key': 'clerk-deposit-denied',
            'document': {'mode': 'inline', 'deposit_to': reg['refund_account']['id'], 'date': opened.json()['date']}})
        assert denied.status_code == 403 and denied.json()['details']['capability'] == 'ledger.post'


def test_counted_cash_history_cannot_be_corrected_or_rewritten_after_close(root):
    co = Company(root)
    counted_register(co)
    shift = co.run('pos shift open', {'float': '50', 'operation_key': 'guard-open'})
    sale = sell(co, 'guard-sale', quantity='2')
    other = sell(co, 'guard-other')
    assert co.fails('sales-receipt update', {'sales_receipt': other['sales_receipt']['id'],
        'expected_version': 1, 'memo': 'changed'}).code == 'E_WORK_DEPENDENCY'
    line = co.run('pos refund lines', {'sale': sale['id']})['lines'][0]
    refund = co.run('pos refund', {'operation_key': 'guard-return', 'sale': sale['id'],
        'lines': [{'source_line': line['source_line'], 'quantity': '1'}]})
    assert co.fails('customer-refund update', {'refund': refund['refund']['id'],
        'expected_version': 1, 'memo': 'changed'}).code == 'E_WORK_DEPENDENCY'
    co.run('pos shift close', {'shift': shift['id'], 'counted': '70', 'operation_key': 'guard-close'})
    for command, body in [
        ('sales-receipt update', {'sales_receipt': other['sales_receipt']['id'], 'expected_version': 1, 'memo': 'changed'}),
        ('sales-receipt void', {'sales_receipt': other['sales_receipt']['id'], 'expected_version': 1}),
        ('pos sale void', {'sale': other['id'], 'expected_version': 1}),
        ('customer-refund void', {'refund': refund['refund']['id'], 'expected_version': 1}),
    ]:
        assert co.fails(command, body).code == 'E_WORK_DEPENDENCY', command


def test_voided_refund_restores_open_drawer_expected(root):
    co = Company(root)
    counted_register(co)
    shift = co.run('pos shift open', {'float': '50', 'operation_key': 'return-void-open'})
    sale = sell(co, 'return-void-sale', quantity='2')
    line = co.run('pos refund lines', {'sale': sale['id']})['lines'][0]
    refund = co.run('pos refund', {'operation_key': 'return-void', 'sale': sale['id'],
        'lines': [{'source_line': line['source_line'], 'quantity': '1'}]})
    assert co.run('report pos-day', {'shift': shift['id']})['expected_drawer']['minor_units'] == 6000
    co.run('customer-refund void', {'refund': refund['refund']['id'], 'expected_version': 1})
    report = co.run('report pos-day', {'shift': shift['id']})
    assert report['expected_drawer']['minor_units'] == 7000
    assert report['cash_refunds']['minor_units'] == 0


def test_uncounted_receipt_correction_updates_daily_cash_facts(root):
    co = Company(root)
    _setup(co)
    sale = sell(co, 'corrected-uncounted')
    co.run('sales-receipt update', {'sales_receipt': sale['sales_receipt']['id'], 'expected_version': 1,
        'lines': [{'item': 'Pipe Sealant', 'quantity': '2', 'unit_price': '10.00'}]})
    day = co.run('report pos-day')
    assert day['cash_sales']['minor_units'] == 2000
    assert day['total_sales']['minor_units'] == 2000
    assert day['expected_drawer']['minor_units'] == 2000


def test_day_summary_keeps_overnight_shift_after_close(root, monkeypatch):
    from bookflow.core import clock
    co = Company(root)
    counted_register(co)
    now = clock.now()
    with monkeypatch.context() as patch:
        patch.setattr(clock, 'now', lambda: now - timedelta(days=1))
        shift = co.run('pos shift open', {'float': '50', 'operation_key': 'overnight-open'})
        sell(co, 'overnight-sale')
    day = co.run('report pos-day')
    assert any(s['id'] == shift['id'] and s['status'] == 'open' for s in day['shifts'])
    assert day['expected_drawer']['minor_units'] == 6000
    co.run('pos shift close', {'shift': shift['id'], 'counted': '60', 'operation_key': 'overnight-close'})
    day = co.run('report pos-day')
    assert any(s['id'] == shift['id'] and s['status'] == 'closed' for s in day['shifts'])


def test_refund_cash_kind_is_captured_not_current_method(root):
    co = Company(root)
    counted_register(co)
    shift = co.run('pos shift open', {'float': '50', 'operation_key': 'kind-open'})
    sold = sell(co, 'kind-sale', quantity='2')
    line = co.run('pos refund lines', {'sale': sold['id']})['lines'][0]
    co.run('pos refund', {'sale': sold['id'], 'operation_key': 'kind-return',
                         'lines': [{'source_line': line['source_line'], 'quantity': '1'}]})
    method = co.run('payment-method show', {'payment_method': 'Cash'})
    co.run('payment-method update', {'payment_method': method['id'], 'expected_version': method['version'], 'kind': 'credit_card'})
    report = co.run('report pos-day', {'shift': shift['id']})
    assert report['cash_refunds']['minor_units'] == 1000
    assert report['expected_drawer']['minor_units'] == 6000
    assert report['by_tender'][0]['payment_method']['kind'] == 'cash'
