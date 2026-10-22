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
                   discount_account='Bad Debt', discount_class='East')
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
    paid = receive(books, '480.00', [(sale, 1, '480.00')], underpayment='write_off', discount_account='Bad Debt')
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
        receive(books, '500.00', [(sale, 1, '500.00')], underpayment='write_off', discount_account='Bad Debt')
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
    first = receive(books, '480.00', [(sale, 1, '480.00')], key='same', underpayment='write_off', discount_account='Bad Debt')
    again = receive(books, '480.00', [(sale, 1, '480.00')], key='same', underpayment='write_off', discount_account='Bad Debt')
    assert again['id'] == first['id'] and again['idempotent_replay'] is True
    assert balances(books)[books['bad_debt']] == 2000
    later = invoice(books, '500.00')
    run('company update', dict(expected_version=run('company show')['info_version'], closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        receive(books, '480.00', [(later, 1, '480.00')], key='closed', underpayment='write_off', discount_account='Bad Debt')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert due(books, later) == 50000
