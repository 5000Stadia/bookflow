"""Day close hand-off has exactly the ordinary deposit's ledger, including differences."""
import pytest
from bookflow.core.money import Money
from tests.test_pos_sale import Company, _setup, _reads, twins  # noqa: F401
from tests.test_pos_refund import sale, request


def _day(co, variance):
    reg = _setup(co)
    co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'], 'count_shifts': True})
    shift = co.run('pos shift open', {'float': '50.00', 'operation_key': 'open-day'})
    cash = sale(co)
    sale(co, ticket='card', method='Visa')
    void = sale(co, ticket='void')
    co.run('pos sale void', {'sale': void['id'], 'expected_version': 1})
    refund = co.run('pos refund', request(co, cash))
    expected = 5000 + cash['total']['minor_units'] - refund['total']['minor_units']
    closed = co.run('pos shift close', {'shift': shift['id'], 'counted': Money(expected + variance, 'USD').amount, 'operation_key': 'close-day'})
    assert closed['expected']['minor_units'] == expected
    return reg, cash, refund, closed


@pytest.mark.parametrize('variance', [-200, 300])
def test_deposit_handoff_matches_manual(twins, variance):
    pos, typed = twins
    reg, cash, refund, closed = _day(pos, variance)
    other, twin, twin_refund, twin_shift = _day(typed, variance)
    handoff = pos.run('pos deposit prepare', {'shift': closed['id']})
    doc = handoff['document']
    assert [x['source'] for x in doc['sources']] == [cash['sales_receipt']['id']]
    account = typed.run('account create', {'name': 'Cash Over and Short', 'type': 'expense'})
    manual = {'mode': 'inline', 'deposit_to': other['refund_account']['id'], 'date': twin['date'],
              'memo': doc['memo'], 'sources': [{'source_type': 'sales_receipt', 'source': twin['sales_receipt']['id'], 'expected_version': 1}]}
    if variance > 0:
        manual['additional'] = [{'received_from': {'kind': 'customer', 'id': other['walk_in_customer']['id']},
            'from_account': account['id'], 'amount': '3.00', 'memo': 'Drawer overage'}]
    else:
        manual['cash_back'] = {'account': account['id'], 'amount': '2.00', 'memo': 'Drawer shortage'}
    pos.run('deposit post', {'operation_key': 'deposit-day', 'document': doc})
    typed.run('deposit post', {'operation_key': 'deposit-day', 'document': manual})
    assert _reads(pos) == _reads(typed)
    assert pos.fails('pos deposit prepare', {'shift': closed['id']}).code == 'E_WORK_DEPENDENCY'
    # The card receipt is still in UF, and a repeated deposit cannot claim the cash again.
    remaining = pos.run('deposit sources', {'date': cash['date'], 'date_from': cash['date'], 'date_to': cash['date']})
    assert remaining['total_count'] == 1


def test_handoff_excludes_banked_receipts_without_repeating_variance(root):
    co = Company(root)
    reg, cash, refund, closed = _day(co, 0)
    doc = co.run('pos deposit prepare', {'shift': closed['id']})['document']
    co.run('deposit post', {'operation_key': 'first-bank', 'document': doc})
    assert co.fails('pos deposit prepare', {'shift': closed['id']}).code == 'E_WORK_DEPENDENCY'
    all_methods = co.run('pos deposit prepare', {'date': cash['date'], 'payment_methods': ['Cash', 'Visa']})
    assert all_methods['excluded_sources'] == [cash['sales_receipt']['id']]
    assert len(all_methods['document']['sources']) == 1
    assert not all_methods['document']['additional'] and all_methods['document']['cash_back'] is None


def test_variance_requires_all_cash_methods(root):
    co = Company(root)
    reg = _setup(co)
    method = co.run('payment-method create', {'name': 'Second cash', 'kind': 'cash'})
    co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'],
        'count_shifts': True, 'payment_methods': [x['id'] for x in reg['payment_methods']] + [method['id']]})
    shift = co.run('pos shift open', {'float': '50', 'operation_key': 'two-cash-open'})
    sale(co, ticket='cash-a')
    sale(co, ticket='cash-b', method='Second cash')
    report = co.run('report pos-day', {'shift': shift['id']})
    co.run('pos shift close', {'shift': shift['id'], 'operation_key': 'two-cash-close',
        'counted': Money(report['expected_drawer']['minor_units'] + 100, 'USD').amount})
    for method_name in ('Cash', 'Second cash'):
        assert co.fails('pos deposit prepare', {'shift': shift['id'], 'payment_methods': [method_name]}).code == 'E_WORK_DEPENDENCY'
    document = co.run('pos deposit prepare', {'shift': shift['id']})['document']
    assert len(document['sources']) == 2 and document['additional'][0]['amount'] == '1.00'


def test_corrected_uncounted_receipt_handoff_follows_current_method(root):
    from tests.test_pos_day import sell
    co = Company(root)
    _setup(co)
    sold = sell(co, 'corrected-method')
    co.run('sales-receipt update', {'sales_receipt': sold['sales_receipt']['id'], 'expected_version': 1,
                                   'payment_method': 'Visa'})
    assert co.fails('pos deposit prepare', {}).code == 'E_WORK_DEPENDENCY'
    handoff = co.run('pos deposit prepare', {'payment_methods': ['Visa']})
    assert handoff['document']['sources'][0]['expected_version'] == 2


def test_overnight_variance_handoff_requires_whole_shift(root, monkeypatch):
    from datetime import timedelta
    from bookflow.core import clock
    from tests.test_pos_day import counted_register, sell
    co = Company(root)
    counted_register(co)
    now = clock.now()
    with monkeypatch.context() as patch:
        patch.setattr(clock, 'now', lambda: now - timedelta(days=1))
        shift = co.run('pos shift open', {'float': '50', 'operation_key': 'overnight'})
        sell(co, 'before-midnight')
    today = sell(co, 'after-midnight')['date']
    co.run('pos shift close', {'shift': shift['id'], 'counted': '69', 'operation_key': 'overnight-end'})
    assert co.fails('pos deposit prepare', {'date': today}).code == 'E_WORK_DEPENDENCY'
    handoff = co.run('pos deposit prepare', {'shift': shift['id']})
    assert len(handoff['document']['sources']) == 2
    assert handoff['document']['date'] == today
    co.run('deposit post', {'operation_key': 'overnight-deposit', 'document': handoff['document']})


def test_handoff_can_select_retired_historical_method(root):
    from tests.test_pos_day import sell
    co = Company(root)
    reg = _setup(co)
    sold = sell(co, 'retired-cash')
    method = co.run('payment-method show', {'payment_method': 'Cash'})
    co.run('pos-register update', {'pos_register': reg['id'], 'expected_version': reg['version'],
        'payment_methods': [x['id'] for x in reg['payment_methods'] if x['id'] != method['id']]})
    co.run('payment-method deactivate', {'payment_method': method['id'], 'expected_version': method['version']})
    handoff = co.run('pos deposit prepare', {'payment_methods': [method['id']]})
    assert handoff['document']['sources'][0]['source'] == sold['sales_receipt']['id']
    co.run('deposit post', {'operation_key': 'retired-method-deposit', 'document': handoff['document']})


def test_corrected_receipt_keeps_method_kind_at_revision(root):
    from tests.test_pos_day import sell
    co = Company(root)
    _setup(co)
    sold = sell(co, 'corrected-kind-history')
    co.run('sales-receipt update', {'sales_receipt': sold['sales_receipt']['id'], 'expected_version': 1,
        'lines': [{'item': 'Pipe Sealant', 'quantity': '2', 'unit_price': '10.00'}]})
    method = co.run('payment-method show', {'payment_method': 'Cash'})
    co.run('payment-method update', {'payment_method': method['id'], 'expected_version': method['version'], 'kind': 'credit_card'})
    report = co.run('report pos-day')
    assert report['cash_sales']['minor_units'] == 2000
    assert co.run('pos deposit prepare')['document']['sources'][0]['source'] == sold['sales_receipt']['id']
