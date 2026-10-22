"""R148: short payments and overpayments, as the anchor's Receive Payments asks about them.

Cash less than the invoices it pays is an underpayment: "Leave this as an underpayment" (the
default, the invoice keeps owing it) or "Write off the extra amount" (to an account, with a class,
in the same receipt). Cash more than the invoices is an overpayment: "Leave the credit to be used
later" (the default) or "Refund the amount to the customer" (the refund the result names). Every
figure is worked out beside it from the documents' own amounts and read back from the reports.
"""
import sqlite3
from pathlib import Path

import pytest

from tests.test_row3_host import hosted  # noqa: F401  (fixture)

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'short'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Short organization')
    company = client.company.new(legal_name='Short payments', home_currency='USD', timezone='UTC',
                                 organization='Short organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    receivable = next(row['id'] for row in accounts if row['full_name'] == 'Accounts Receivable')
    bad_debt = run('account create', dict(name='Bad Debt', type='expense'))['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    item = run('item create', dict(name='Service call', type='service', sales_enabled=True, description='Service call',
                                   sales_tax_code_id=exempt, income_account_id=income, price='100'))['id']
    customer = run('customer create', dict(name='Acme'))['id']
    east = run('class create', dict(name='East'))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, receivable=receivable,
                bad_debt=bad_debt, methods=methods, item=item, customer=customer, east=east)


def balances(books, date_to='2026-12-31'):
    report = books['run']('report trial-balance', dict(date_to=date_to, limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def pnl(books, basis, date_from='2026-01-01', date_to='2026-12-31'):
    report = books['run']('report profit-and-loss', dict(date_from=date_from, date_to=date_to, basis=basis, limit=200))
    return {row['account_id']: row['amount']['minor_units'] for row in report['rows'] if row['amount']['minor_units']}


def invoice(books, amount, date='2026-03-01'):
    return books['run']('invoice post', dict(customer=books['customer'], date=date,
                        lines=[dict(item=books['item'], quantity='1', unit_price=amount)]), reason='Bill the customer')


def receive(books, amount, rows, *, key='receive-1', date='2026-03-10', **extra):
    raw = dict(customer=books['customer'], date=date, amount=amount, payment_method=books['methods']['Check'],
               deposit_to=books['bank'], operation_key=key,
               applications=dict(mode='inline', items=[dict(invoice=sale['id'], expected_version=version, amount=cash)
                                                       for sale, version, cash in rows]), **extra)
    return books['run']('payment receive', raw, reason='Customer paid')


def due(books, sale):
    return books['run']('invoice settlement', dict(invoice=sale['id']))['due_minor_units']


def receivables(books, as_of='2026-12-31'):
    """(aging total, customer balance detail total, statement closing) for the one customer."""
    run = books['run']
    aging = run('report ar-aging', dict(as_of=as_of))['totals']['total']['minor_units']
    detail = run('report customer-balance-detail', dict(as_of=as_of))['totals']['balance']['minor_units']
    statement = run('report statement', dict(date_from='2026-01-01', date_to=as_of, customer=books['customer']))
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')['balance']['minor_units']
    return aging, detail, closing


def test_an_underpayment_left_open_keeps_owing_and_says_how_to_write_it_off(books):
    sale = invoice(books, '500.00')
    paid = receive(books, '480.00', [(sale, 1, '480.00')])
    under = paid['summary']['underpayment']
    assert under['choice'] == 'left_open' and under['amount']['minor_units'] == 2000
    assert 'Underpayment of 20.00 USD left open' in paid['summary']['text']
    assert 'overpayment' not in paid['summary']
    # 500.00 - 480.00 = 20.00 still owed, everywhere the customer's balance is read.
    assert due(books, sale) == 2000
    assert receivables(books) == (2000, 2000, 2000)
    # Accrual: the whole sale is income; cash: only the 480.00 received is.
    assert pnl(books, 'accrual') == {books['income']: 50000}
    assert pnl(books, 'cash') == {books['income']: 48000}
    # The result's own next step writes the 20.00 off later, the anchor's bad-debt receipt for 0.00.
    step = under['next']
    assert step['command'] == 'payment receive'
    later = dict(step['input'], discount_account=books['bad_debt'], operation_key='write-off-later', date='2026-04-30')
    books['run'](step['command'], later, reason='Acme will not pay the 20.00')
    assert due(books, sale) == 0 and receivables(books) == (0, 0, 0)
    assert balances(books)[books['bad_debt']] == 2000


def test_an_underpayment_written_off_in_the_same_receipt_on_both_bases(books):
    sale = invoice(books, '500.00')
    paid = receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off',
                   write_off_account='Bad Debt', write_off_class='East')
    under = paid['summary']['underpayment']
    assert under == dict(amount=dict(amount='20.00', currency='USD', minor_units=2000), choice='written_off',
                         account='Bad Debt', text='Underpayment of 20.00 USD written off to Bad Debt.')
    assert 'Early-payment' not in paid['summary']['text'] and paid['warnings'] == []
    assert paid['current']['received_minor_units'] == 48000 and paid['current']['discount_minor_units'] == 2000
    assert due(books, sale) == 0 and receivables(books) == (0, 0, 0)
    # Bank 480.00, Bad Debt 20.00, income 500.00; the receivable is gone.
    assert balances(books) == {books['bank']: 48000, books['bad_debt']: 2000, books['income']: -50000}
    # Cash basis counts the write-off as payment, as a discount: all 500.00 of income, 20.00 of bad debt.
    assert pnl(books, 'accrual') == pnl(books, 'cash') == {books['income']: 50000, books['bad_debt']: 2000}
    # The write-off carries its class, as the anchor's Write Off Amount asks.
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT class_id, debit_minor_units FROM posting_lines WHERE account_id=?',
                          (books['bad_debt'],)).fetchall() == [(books['east'], 2000)]


def test_a_written_off_receipt_is_unapplied_and_voided_like_any_other(books):
    sale = invoice(books, '500.00')
    paid = receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off', write_off_account='Bad Debt')
    run = books['run']
    application = paid['effect']['applications'][0]
    undone = run('payment unapply', dict(payment=paid['id'], expected_version=paid['version'], operation_key='undo',
                 applications=[dict(application_id=application['application_id'],
                                    invoice_expected_version=application['invoice_version'])]), reason='Wrong')
    assert due(books, sale) == 50000
    run('payment void', dict(payment=paid['id'], expected_version=undone['version'], operation_key='void'), reason='Wrong')
    assert balances(books) == {books['receivable']: 50000, books['income']: -50000}


def test_write_off_refusals_say_what_to_do(books):
    sale = invoice(books, '500.00')
    with pytest.raises(BookflowError) as missing:
        receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off')
    assert missing.value.code == 'E_VALIDATION' and 'Bad Debt' in str(missing.value.details)
    with pytest.raises(BookflowError) as nothing:
        receive(books, '500.00', [(sale, 1, '500.00')], underpayment='write_off', write_off_account='Bad Debt')
    assert nothing.value.code == 'E_VALIDATION' and 'nothing is short' in str(nothing.value.details)
    assert due(books, sale) == 50000


def test_an_overpayment_is_left_as_credit_or_refunded(books):
    sale = invoice(books, '100.00')
    paid = receive(books, '150.00', [(sale, 1, '100.00')])
    over = paid['summary']['overpayment']
    assert over['choice'] == 'left_as_credit' and over['amount']['minor_units'] == 5000
    assert 'underpayment' not in paid['summary']
    # 100.00 - 150.00: the customer is owed 50.00.
    assert receivables(books) == (-5000, -5000, -5000)
    step = over['next']
    assert step['command'] == 'customer-refund post'
    refund = books['run'](step['command'], dict(step['input'], funding_account=books['bank'],
                          method=books['methods']['Check']), reason='Send back the overpayment')
    assert refund['id']
    assert receivables(books) == (0, 0, 0)
    # Bank: 150.00 in, 50.00 out.
    assert balances(books) == {books['bank']: 10000, books['income']: -10000}
    assert books['run']('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 0


def test_closed_period_and_idempotent_retry(books):
    run = books['run']
    sale = invoice(books, '500.00')
    first = receive(books, '480.00', [(sale, 1, '480.00')], key='same', underpayment='write_off', write_off_account='Bad Debt')
    again = receive(books, '480.00', [(sale, 1, '480.00')], key='same', underpayment='write_off', write_off_account='Bad Debt')
    assert again['id'] == first['id'] and again['idempotent_replay'] is True
    assert balances(books)[books['bad_debt']] == 2000
    later = invoice(books, '500.00')
    run('company update', dict(expected_version=run('company show')['info_version'], closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        receive(books, '480.00', [(later, 1, '480.00')], key='closed', underpayment='write_off', write_off_account='Bad Debt')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert due(books, later) == 50000


def _refund(books, payment, amount, key='refund'):
    return books['run']('customer-refund post', dict(date='2026-05-01', sources=[dict(payment=payment, amount=amount)],
                        funding_account=books['bank'], method=books['methods']['Check']), reason='Pay the customer back')


def _unapply(books, paid, key='undo'):
    application = paid['effect']['applications'][0]
    return books['run']('payment unapply', dict(payment=paid['id'], expected_version=paid['version'], operation_key=key,
        applications=[dict(application_id=application['application_id'],
                           invoice_expected_version=application['invoice_version'])]), reason='Applied to the wrong invoice')


def test_unapplying_a_written_off_receipt_takes_the_write_off_back_and_caps_a_refund_at_the_cash(books):
    run = books['run']
    sale = invoice(books, '500.00')
    paid = receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off', write_off_account='Bad Debt')
    undone = _unapply(books, paid)
    # The write-off left with its application: the invoice owes all 500.00, Bad Debt is back at zero,
    # and the receipt's free credit is its 480.00 of cash.
    assert due(books, sale) == 50000
    assert undone['current']['available_minor_units'] == 48000
    assert run('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 48000
    assert balances(books) == {books['bank']: 48000, books['receivable']: 2000, books['income']: -50000}
    # Cash basis: the invoice is unpaid again, so none of it is income yet.
    assert pnl(books, 'cash') == {}
    with pytest.raises(BookflowError) as too_much:
        _refund(books, paid['id'], '500.00')
    assert too_much.value.code in ('E_CREDIT_UNAVAILABLE', 'E_APPLICATION_CAPACITY')
    _refund(books, paid['id'], '480.00')
    assert balances(books) == {books['receivable']: 50000, books['income']: -50000}
    assert receivables(books) == (50000, 50000, 50000)


def test_unapplying_an_early_discount_takes_it_back(books):
    run = books['run']
    discounts = run('account create', dict(name='Discounts Given', type='income'))['id']
    sale = invoice(books, '1000.00')
    paid = receive(books, '980.00', [(sale, 1, '980.00')], discounts=[dict(invoice=sale['id'], amount='20.00')])
    assert due(books, sale) == 0 and balances(books)[discounts] == 2000
    _unapply(books, paid)
    assert due(books, sale) == 100000
    assert discounts not in balances(books)
    assert run('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 98000
    with pytest.raises(BookflowError):
        _refund(books, paid['id'], '1000.00')
    # Applied again, it pays 980.00 and the invoice owes the 20.00 the discount no longer covers.
    shown = run('payment show', dict(payment=paid['id']))
    run('payment apply', dict(payment=paid['id'], expected_version=shown['version'], date='2026-03-20', operation_key='again',
        applications=dict(mode='inline', items=[dict(invoice=sale['id'], expected_version=run('invoice show', dict(invoice=sale['id']))['version'], amount='980.00')])), reason='Reapply')
    assert due(books, sale) == 2000


def test_unapplying_a_bad_debt_receipt_voids_it(books):
    sale = invoice(books, '250.00')
    done = books['run']('payment receive', dict(customer=books['customer'], date='2026-09-30', amount='0.00', operation_key='wo',
        discounts=[dict(invoice=sale['id'], amount='250.00', expected_version=1)], discount_account=books['bad_debt']),
        reason='Customer is gone')
    _unapply(books, done)
    assert due(books, sale) == 25000
    assert books['run']('payment show', dict(payment=done['id']))['status'] == 'voided'
    assert balances(books) == {books['receivable']: 25000, books['income']: -25000}


def test_a_bounced_check_returns_only_the_cash_and_the_write_off_stays_on_the_invoice(books):
    run = books['run']
    fees = run('account create', dict(name='Bank Service Charges', type='expense'))['id']
    sale = invoice(books, '500.00')
    paid = receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off', write_off_account='Bad Debt')
    bounced = run('payment bounce', dict(payment=paid['id'], expected_version=paid['version'], date='2026-03-20',
                  operation_key='bounce', bank_fee_amount='10.00', bank_fee_account=fees), reason='Bank returned it')
    assert bounced['returned']['minor_units'] == 48000
    assert [row['due']['minor_units'] for row in bounced['reopened_invoices']] == [48000]
    # The 480.00 never arrived, so it is owed again; the 20.00 written off stays written off.
    assert due(books, sale) == 48000
    assert run('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 0
    assert balances(books) == {books['bank']: -1000, fees: 1000, books['bad_debt']: 2000,
                               books['receivable']: 48000, books['income']: -50000}
    with pytest.raises(BookflowError):
        _refund(books, paid['id'], '20.00')


def test_an_early_discount_and_a_write_off_on_one_invoice_post_to_their_own_accounts(books):
    run = books['run']
    discounts = run('account create', dict(name='Discounts Given', type='income'))['id']
    terms = {row['name']: row['id'] for row in run('term query', dict(limit=50))['items']}
    sale = run('invoice post', dict(customer=books['customer'], date='2026-03-01', terms=terms['2% 10 Net 30'],
               lines=[dict(item=books['item'], quantity='1', unit_price='1000.00')]), reason='Bill')
    # 1,000.00 less the 20.00 early discount is 980.00; Acme sent 950.00, so 30.00 is written off.
    paid = receive(books, '950.00', [(sale, 1, '950.00')], date='2026-03-08', discounts=[dict(invoice=sale['id'], amount='20.00')],
                   underpayment='write_off', write_off_account='Bad Debt')
    application = paid['effect']['applications'][0]
    assert application['discount']['minor_units'] == 2000 and application['write_off']['minor_units'] == 3000
    assert paid['warnings'] == []
    assert due(books, sale) == 0
    assert balances(books) == {books['bank']: 95000, discounts: 2000, books['bad_debt']: 3000, books['income']: -100000}
    # The same after the discount date: the discount is late and says so; the write-off is not a discount.
    later = run('invoice post', dict(customer=books['customer'], date='2026-03-01', terms=terms['2% 10 Net 30'],
                lines=[dict(item=books['item'], quantity='1', unit_price='1000.00')]), reason='Bill')
    late = receive(books, '950.00', [(later, 1, '950.00')], key='late', date='2026-03-25',
                   discounts=[dict(invoice=later['id'], amount='20.00')], underpayment='write_off', write_off_account='Bad Debt')
    assert len(late['warnings']) == 1
    # Unapplied, both leave with the application.
    _unapply(books, paid, key='undo-mixed')
    assert due(books, sale) == 100000
    assert balances(books)[discounts] == 2000 and balances(books)[books['bad_debt']] == 3000
