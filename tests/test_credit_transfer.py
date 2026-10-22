"""R148: moving a customer's credit from one job to another, the way the anchor's guides do it.

The anchor has no ready-made transfer: its guides charge the job that has the credit (which that
credit then pays) and credit the job that gets it, through a clearing account that nets to zero.
`customer-credit transfer` writes exactly that pair in one step and `transfer-void` takes it back.
Each job's balance is worked out beside the assertion and read back from aging, customer balance
detail and the statement.
"""
import pytest

from tests.test_row3_host import hosted  # noqa: F401  (fixture)

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'transfer'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Transfer organization')
    company = client.company.new(legal_name='Credit transfers', home_currency='USD', timezone='UTC',
                                 organization='Transfer organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    item = run('item create', dict(name='Remodel work', type='service', sales_enabled=True, description='Work',
                                   sales_tax_code_id=exempt, income_account_id=income, price='100'))['id']
    acme = run('customer create', dict(name='Acme'))['id']
    kitchen = run('customer create', dict(name='Kitchen', parent_id=acme))['id']
    bath = run('customer create', dict(name='Bath', parent_id=acme))['id']
    other = run('customer create', dict(name='Other Co'))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, methods=methods, item=item,
                acme=acme, kitchen=kitchen, bath=bath, other=other)


def invoice(books, customer, amount, date='2026-03-01'):
    return books['run']('invoice post', dict(customer=customer, date=date,
                        lines=[dict(item=books['item'], quantity='1', unit_price=amount)]), reason='Bill the job')


def overpay(books, customer, sale, cash, applied, key='pay-1', date='2026-03-05'):
    return books['run']('payment receive', dict(customer=customer, date=date, amount=cash, operation_key=key,
        payment_method=books['methods']['Check'], deposit_to=books['bank'],
        applications=dict(mode='inline', items=[dict(invoice=sale['id'], expected_version=1, amount=applied)])),
        reason='Customer paid')


def credit_memo(books, customer, amount, date='2026-03-03'):
    return books['run']('credit-memo post', dict(customer=customer, date=date,
                        lines=[dict(item=books['item'], quantity='1', unit_price=amount)]), reason='Goodwill credit')


def transfer(books, source, target, amount, *, key='xfer-1', date='2026-03-10', **extra):
    return books['run']('customer-credit transfer', dict(from_customer=source, to_customer=target, amount=amount,
                        date=date, operation_key=key, **extra), reason='Customer asked to use the credit on the other job')


def balance(books, customer, as_of='2026-12-31'):
    """(aging total, balance detail total, statement closing) of one customer or job, in minor units."""
    run = books['run']
    aging = {row['customer_id']: row['total']['minor_units']
             for row in run('report ar-aging', dict(as_of=as_of))['rows']}.get(customer, 0)
    detail = run('report customer-balance-detail', dict(as_of=as_of, customer=customer))['totals']['balance']['minor_units']
    statement = run('report statement', dict(date_from='2026-01-01', date_to=as_of, customer=customer))
    closing = next(row for row in statement['rows'] if row['kind'] == 'closing')['balance']['minor_units']
    return aging, detail, closing


def trial_balance(books):
    report = books['run']('report trial-balance', dict(date_to='2026-12-31', limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['current_account_name']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def due(books, sale):
    return books['run']('invoice settlement', dict(invoice=sale['id']))['due_minor_units']


def test_an_overpayment_on_one_job_pays_another_and_the_void_puts_it_back(books):
    run = books['run']
    kitchen_sale = invoice(books, books['kitchen'], '100.00')
    bath_sale = invoice(books, books['bath'], '300.00', date='2026-03-02')
    # Kitchen paid 220.00 on a 100.00 invoice: 120.00 of credit sits on Kitchen.
    paid = overpay(books, books['kitchen'], kitchen_sale, '220.00', '100.00')
    assert balance(books, books['kitchen']) == (-12000, -12000, -12000)
    assert balance(books, books['bath']) == (30000, 30000, 30000)
    before = trial_balance(books)
    preview = books['client'].run('customer-credit transfer', dict(
        from_customer=books['kitchen'], to_customer=books['bath'], amount='120.00', date='2026-03-10',
        operation_key='xfer-1', apply_to=[dict(invoice=bath_sale['id'], expected_version=1)]),
        company=books['company'], reason='Use the Kitchen credit on Bath', dry_run=True)
    assert preview['transfer_id'] is None and trial_balance(books) == before
    moved = transfer(books, books['kitchen'], books['bath'], '120.00',
                     apply_to=[dict(invoice=bath_sale['id'], expected_version=1)],
                     expected_facts_fingerprint=preview['facts_fingerprint'])
    assert moved['status'] == 'posted' and moved['amount']['minor_units'] == 12000
    assert [(row['type'], row['id'], row['amount']['minor_units']) for row in moved['sources']] == [('payment', paid['id'], 12000)]
    assert moved['applied'][0]['still_due']['minor_units'] == 18000
    assert 'Acme:Kitchen' in moved['summary'] and 'transfer-void' in moved['summary']
    # Kitchen's credit is used up; Bath owes 300.00 - 120.00 = 180.00.
    assert balance(books, books['kitchen']) == (0, 0, 0)
    assert balance(books, books['bath']) == (18000, 18000, 18000)
    assert due(books, bath_sale) == 18000
    assert run('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 0
    # Nothing but the receivable moved between the jobs; the clearing account is back at zero.
    assert trial_balance(books) == before
    assert 'Credit Transfer Clearing' not in trial_balance(books)
    # The statements name the two documents.
    kitchen_rows = [row['entry'] for row in run('report statement', dict(
        date_from='2026-01-01', date_to='2026-12-31', customer=books['kitchen']))['rows'] if row['kind'] == 'activity']
    assert 'invoice' in kitchen_rows
    bath_rows = [row['entry'] for row in run('report statement', dict(
        date_from='2026-01-01', date_to='2026-12-31', customer=books['bath']))['rows'] if row['kind'] == 'activity']
    assert 'credit_memo' in bath_rows

    voided = run('customer-credit transfer-void', dict(transfer=moved['transfer_id']), reason='Customer changed their mind')
    assert voided['status'] == 'voided' and [row['invoice_id'] for row in voided['reopened']] == [bath_sale['id']]
    assert balance(books, books['kitchen']) == (-12000, -12000, -12000)
    assert balance(books, books['bath']) == (30000, 30000, 30000)
    assert run('payment show', dict(payment=paid['id']))['current']['available_minor_units'] == 12000
    assert trial_balance(books) == before
    # Voiding again writes nothing.
    again = run('customer-credit transfer-void', dict(transfer=moved['transfer_id']), reason='Again')
    assert again['changed'] is False
    assert trial_balance(books) == before


def test_a_parent_credit_memo_moves_to_a_job_and_is_applied_later(books):
    run = books['run']
    memo = credit_memo(books, books['acme'], '50.00')
    bath_sale = invoice(books, books['bath'], '300.00')
    assert balance(books, books['acme']) == (-5000, -5000, -5000)
    moved = transfer(books, books['acme'], books['bath'], '50.00')
    assert moved['sources'] == [dict(type='credit_memo', id=memo['id'], number=memo['number'],
                                     amount=dict(amount='50.00', currency='USD', minor_units=5000))]
    assert moved['applied'] == []
    # The parent's credit is gone; Bath has 300.00 owed and a 50.00 credit: 250.00 net.
    assert balance(books, books['acme']) == (0, 0, 0)
    assert balance(books, books['bath']) == (25000, 25000, 25000)
    assert due(books, bath_sale) == 30000
    new_memo = run('credit-memo show', dict(credit_memo=moved['credit_memo']['id']))
    run('customer-credit apply', dict(credit_memo=new_memo['id'], expected_version=new_memo['version'],
        applications=[dict(invoice=bath_sale['id'], expected_version=1)]), reason='Use it')
    assert due(books, bath_sale) == 25000
    # The void unapplies the later use too: Bath owes the whole 300.00 again, Acme has its credit back.
    run('customer-credit transfer-void', dict(transfer=moved['transfer_id']), reason='Wrong job')
    assert due(books, bath_sale) == 30000
    assert balance(books, books['acme']) == (-5000, -5000, -5000)
    assert balance(books, books['bath']) == (30000, 30000, 30000)
    assert run('credit-memo show', dict(credit_memo=memo['id']))['source_current']['available_minor_units'] == 5000


def test_cash_basis_is_untouched_by_a_transfer(books):
    run = books['run']
    sale = invoice(books, books['kitchen'], '100.00')
    overpay(books, books['kitchen'], sale, '220.00', '100.00')
    report = lambda basis: {row['account_id']: row['amount']['minor_units'] for row in run('report profit-and-loss', dict(
        date_from='2026-01-01', date_to='2026-12-31', basis=basis, limit=200))['rows'] if row['amount']['minor_units']}
    before = report('cash'), report('accrual')
    transfer(books, books['kitchen'], books['bath'], '120.00')
    assert (report('cash'), report('accrual')) == before


def test_refusals_say_what_to_do(books):
    sale = invoice(books, books['kitchen'], '100.00')
    overpay(books, books['kitchen'], sale, '150.00', '100.00')
    with pytest.raises(BookflowError) as short:
        transfer(books, books['kitchen'], books['bath'], '80.00')
    assert short.value.code == 'E_CREDIT_UNAVAILABLE' and short.value.details['available']['minor_units'] == 5000
    with pytest.raises(BookflowError) as stranger:
        transfer(books, books['kitchen'], books['other'], '10.00')
    assert stranger.value.code == 'E_VALIDATION' and 'same customer' in str(stranger.value.details)
    with pytest.raises(BookflowError) as early:
        transfer(books, books['kitchen'], books['bath'], '10.00', date='2026-03-01')
    assert early.value.code == 'E_CREDIT_UNAVAILABLE'
    with pytest.raises(BookflowError) as reasonless:
        books['run']('customer-credit transfer', dict(from_customer=books['kitchen'], to_customer=books['bath'],
                     amount='10.00', date='2026-03-10', operation_key='no-reason'))
    assert reasonless.value.code == 'E_REASON_REQUIRED'
    assert balance(books, books['kitchen'])[0] == -5000


def test_closed_period_and_idempotent_retry(books):
    run = books['run']
    sale = invoice(books, books['kitchen'], '100.00')
    overpay(books, books['kitchen'], sale, '220.00', '100.00')
    first = transfer(books, books['kitchen'], books['bath'], '60.00', key='same')
    again = transfer(books, books['kitchen'], books['bath'], '60.00', key='same')
    assert again['idempotent_replay'] is True and again['transfer_id'] == first['transfer_id']
    # Only 60.00 moved: Kitchen still has the other 60.00.
    assert balance(books, books['kitchen'])[0] == -6000 and balance(books, books['bath'])[0] == -6000
    with pytest.raises(BookflowError) as reused:
        transfer(books, books['kitchen'], books['acme'], '10.00', key='same')
    assert reused.value.code == 'E_PAYMENT_OPERATION_KEY_REUSED'
    run('company update', dict(expected_version=run('company show')['info_version'], closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        transfer(books, books['kitchen'], books['bath'], '10.00', key='closed', date='2026-03-20')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    with pytest.raises(BookflowError) as closed_void:
        run('customer-credit transfer-void', dict(transfer=first['transfer_id']), reason='Too late')
    assert closed_void.value.code == 'E_PERIOD_CLOSED'
    assert balance(books, books['bath'])[0] == -6000
    # After the closing date, a transfer is dated in the open period.
    later = transfer(books, books['kitchen'], books['bath'], '60.00', key='later', date='2026-04-02')
    assert later['status'] == 'posted' and balance(books, books['kitchen'])[0] == 0
