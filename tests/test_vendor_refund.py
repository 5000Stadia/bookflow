"""A vendor's refund check for an open vendor credit (R149), checked through the real reports.

The worked case: Acme Supply owes us 120.00 back for stock we returned (VC-1, dated 2026-05-10).
Instead of letting us use the credit, Acme sends a check.

    full refund     120.00 banked 2026-05-20: bank +120.00, Acme's balance 0, the credit closed.
    partial refund   50.00 banked: bank +50.00, Acme still owes us 70.00, 70.00 of the credit free.
    void the refund  bank back to 0, Acme owes us 120.00 again, the whole credit free again.

and the anchor's own two steps -- a deposit line drawn from Accounts Payable with the vendor's name,
then the credit applied to that deposit -- land on the same figures.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

AS_OF = '2026-05-31'


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'refunds'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Refunds organization')
    company = client.company.new(legal_name='Refunds', home_currency='USD', timezone='UTC',
                                 organization='Refunds organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    supplies = client.account.create(company=company, name='Supplies', type='expense')['id']
    acme = client.vendor.create(company=company, name='Acme Supply')['id']
    other = client.vendor.create(company=company, name='Corner Hardware')['id']
    methods = {row['name']: row['id'] for row in
               client.run('payment-method query', {'limit': 50}, company=company)['items']}

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    credit = run('vendor-credit post', dict(
        vendor=acme, date='2026-05-10', number='VC-1', memo='Returned stock',
        expenses=[{'account': supplies, 'amount': '120.00'}]), reason='Returned stock')
    return dict(run=run, payable=payable, bank=bank, supplies=supplies, acme=acme, other=other,
                check=methods['Check'], credit=credit['id'])


def _balances(books):
    report = books['run']('report trial-balance', dict(date_to=AS_OF, include_zero=True, limit=200))
    assert report['totals']['debit'] == report['totals']['credit']
    return {row['account_id']: row['signed_net']['minor_units'] for row in report['rows']}


def _free(books):
    return books['run']('vendor-credit show', {'credit': books['credit']})['settlement_current']['unapplied_minor_units']


def _aging(books):
    rows = books['run']('report ap-aging', {'as_of': AS_OF, 'limit': 50})['rows']
    return {row['vendor_id']: row['total']['minor_units'] for row in rows}


def _vendor_balance(books):
    report = books['run']('report vendor-balance-detail', {'as_of': AS_OF, 'vendor': books['acme']})
    return report['totals']['balance']['minor_units'], report['rows']


def _deposit_detail(books):
    report = books['run']('report deposit-detail', {'date_from': '2026-05-01', 'date_to': AS_OF})
    return report['totals']['deposited']['minor_units'], report['rows']


def _refund(books, idempotency_key=None, **extra):
    raw = dict(credit=books['credit'], deposit_to=books['bank'], date='2026-05-20',
               payment_method=books['check'], check_number='8812', **extra)
    return books['run']('vendor-credit refund', raw, reason='Acme sent a refund check',
                        idempotency_key=idempotency_key)


def test_a_full_refund_banks_the_check_and_closes_the_credit(books):
    assert _free(books) == 12000 and _aging(books) == {books['acme']: -12000}

    preview = books['run']('vendor-credit refund', dict(
        credit=books['credit'], deposit_to=books['bank'], date='2026-05-20'),
        reason='Acme sent a refund check', dry_run=True)
    assert preview['dry_run'] and preview['amount_minor_units'] == 12000
    assert preview['credit_free_after_minor_units'] == 0
    assert _free(books) == 12000 and _deposit_detail(books)[0] == 0, 'a preview writes nothing'

    out = _refund(books)
    assert out['amount_minor_units'] == 12000 and out['credit_free_after_minor_units'] == 0
    assert out['vendor_id'] == books['acme'] and out['deposit_to_id'] == books['bank']

    balances = _balances(books)
    assert balances[books['bank']] == 12000, 'the check is in the bank'
    assert balances[books['payable']] == 0, 'the payable rose by exactly what the credit lowered it by'
    assert balances[books['supplies']] == -12000, 'the credit still gave the cost back; the refund moved no expense'
    assert _free(books) == 0, 'the credit is settled'
    assert books['acme'] not in _aging(books), 'Acme owes us nothing and we owe Acme nothing'
    assert _vendor_balance(books)[0] == 0
    unpaid = books['run']('report unpaid-bills', {'as_of': AS_OF})
    assert unpaid['count'] == 0

    deposited, rows = _deposit_detail(books)
    assert deposited == 12000
    line = next(row for row in rows if row['kind'] == 'line')
    assert line['party_name'] == 'Acme Supply' and line['account_id'] == books['payable']
    assert line['amount']['minor_units'] == -12000

    show = books['run']('vendor-credit show', {'credit': books['credit']})
    applied = [row for row in show['applications'] if row['active']]
    assert [(row['obligation_transaction_id'], row['amount_minor_units']) for row in applied] == [
        (out['deposit_id'], 12000)]

    # Nothing is left to refund.
    with pytest.raises(BookflowError) as refused:
        _refund(books)
    assert refused.value.code == 'E_APPLICATION_CAPACITY'


def test_a_partial_refund_leaves_the_rest_of_the_credit_open(books):
    out = _refund(books, amount='50.00')
    assert out['credit_free_after_minor_units'] == 7000

    assert _balances(books)[books['bank']] == 5000
    assert _free(books) == 7000
    assert _aging(books) == {books['acme']: -7000}, 'Acme still owes us 70.00'
    assert _vendor_balance(books)[0] == -7000
    assert _deposit_detail(books)[0] == 5000

    # The rest can be refunded later, and not a cent more.
    with pytest.raises(BookflowError) as refused:
        _refund(books, amount='70.01')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    _refund(books, amount='70.00')
    assert _free(books) == 0 and books['acme'] not in _aging(books)
    assert _balances(books)[books['bank']] == 12000


def test_a_refund_over_the_open_credit_is_refused_and_writes_nothing(books):
    before = _balances(books)
    with pytest.raises(BookflowError) as refused:
        _refund(books, amount='120.01')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert refused.value.details['available_minor_units'] == 12000
    assert _balances(books) == before and _free(books) == 12000 and _deposit_detail(books)[0] == 0

    # Nor dated before the credit existed.
    with pytest.raises(BookflowError) as early:
        books['run']('vendor-credit refund', dict(credit=books['credit'], deposit_to=books['bank'],
                                                  date='2026-05-01'), reason='too early')
    assert early.value.code == 'E_VALIDATION'


def test_voiding_the_refund_reopens_the_credit(books):
    out = _refund(books, amount='50.00')
    deposit = books['run']('deposit show', {'deposit': out['deposit_id']})
    request = dict(deposit=out['deposit_id'], expected_version=deposit['current']['version'], operation_key='void-refund')
    preview = books['run']('deposit void', request, reason='Check bounced', dry_run=True)
    books['run']('deposit void', dict(request, dependency_guard=preview['dependency_guard']),
                 reason='Check bounced')

    assert _balances(books)[books['bank']] == 0
    assert _free(books) == 12000, 'the whole credit is free again'
    assert _aging(books) == {books['acme']: -12000}
    assert _vendor_balance(books)[0] == -12000
    show = books['run']('vendor-credit show', {'credit': books['credit']})
    assert not [row for row in show['applications'] if row['active']]
    assert [row['kind'] for row in show['applications']] == ['apply', 'unapply']

    # And the credit can be refunded again.
    _refund(books)
    assert _free(books) == 0 and _balances(books)[books['bank']] == 12000


def test_the_anchors_two_steps_land_on_the_same_figures(books):
    deposit = books['run']('deposit post', dict(operation_key='acme-refund', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-05-20', additional=[dict(
            received_from=dict(kind='vendor', id=books['acme']), from_account=books['payable'],
            amount='120.00', check_number='8812', payment_method=books['check'])])),
        reason='Acme refund check')
    deposit_id = deposit['deposit']['id']
    # Banked but not yet applied: the vendor's balance is already right; the aging shows both halves.
    assert _balances(books)[books['bank']] == 12000 and _vendor_balance(books)[0] == 0
    assert _free(books) == 12000

    # A line from A/P with a customer's name is still refused.
    customer = books['run']('customer create', {'name': 'Walk-in'})['id']
    with pytest.raises(BookflowError) as refused:
        books['run']('deposit post', dict(operation_key='bad-line', document=dict(
            mode='inline', deposit_to=books['bank'], date='2026-05-20', additional=[dict(
                received_from=dict(kind='customer', id=customer), from_account=books['payable'],
                amount='1.00')])), reason='wrong')
    assert refused.value.code == 'E_VALIDATION'

    # A credit of another vendor cannot settle it.
    other = books['run']('vendor-credit post', dict(vendor=books['other'], date='2026-05-10',
        expenses=[{'account': books['supplies'], 'amount': '10.00'}]), reason='other')
    with pytest.raises(BookflowError) as crossed:
        books['run']('vendor-credit apply', dict(credit=other['id'], date='2026-05-20',
                                                 deposits=[dict(deposit=deposit_id)]), reason='wrong vendor')
    assert crossed.value.code == 'E_APPLICATION_INCOMPATIBLE'

    books['run']('vendor-credit apply', dict(credit=books['credit'], date='2026-05-20',
                                             deposits=[dict(deposit=deposit_id)]), reason='Apply VC-1 to the refund')
    assert _free(books) == 0 and books['acme'] not in _aging(books)
    assert _vendor_balance(books)[0] == 0 and _deposit_detail(books)[0] == 12000

    # A correction that would owe the vendor less than the credit settled is refused...
    current = books['run']('deposit show', {'deposit': deposit_id})
    with pytest.raises(BookflowError) as shrunk:
        books['run']('deposit update', dict(deposit=deposit_id, expected_version=current['current']['version'],
            operation_key='shrink', document=dict(mode='inline', deposit_to=books['bank'],
                date='2026-05-20', number=current['current']['number'], memo=None, cash_back=None, custom_fields={},
                expected_custom_field_kinds={}, sources=[], additional=[dict(
                    received_from=dict(kind='vendor', id=books['acme']), from_account=books['payable'],
                    amount='100.00')])), reason='shrink', dry_run=True)
    assert shrunk.value.code == 'E_DEPOSIT_DEPENDENCY'

    # ...and unapplying leaves the credit free and the deposit owed to Acme, as the anchor does.
    books['run']('vendor-credit unapply', dict(credit=books['credit'], deposits=[deposit_id]), reason='undo')
    assert _free(books) == 12000 and _vendor_balance(books)[0] == 0


def test_a_retried_refund_writes_once_and_a_closed_period_stays_closed(books):
    first = _refund(books, amount='40.00', idempotency_key='acme-check-8812')
    again = _refund(books, amount='40.00', idempotency_key='acme-check-8812')
    assert again['deposit_id'] == first['deposit_id']
    assert _free(books) == 8000 and _deposit_detail(books)[0] == 4000, 'one deposit, one settlement'

    info = books['run']('company show', {})
    books['run']('company update', dict(expected_version=info['info_version'], closing_date='2026-05-25'))
    with pytest.raises(BookflowError) as closed:
        _refund(books, amount='10.00')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert _free(books) == 8000


def test_the_co0073_guard_is_the_schema_modules():
    import importlib
    from bookflow.company.ap_settlement_schema import guard_statements
    from bookflow.storage.migrate import HEADS
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0073_vendor_refund_deposits')
    assert HEADS['company'] == m.revision == 'co0073'
    current = [s for s in guard_statements() if s.split()[2] == m.REPLACED[0]]
    assert current == [m.GUARD]
    assert "type = 'bill'" in m.OLD_GUARD and 'deposit' not in m.OLD_GUARD
