"""An Other Charge item may post to a balance-sheet account, as the anchor allows.

A customer deposit held as a liability, or an open balance brought in against a clearing account
when a company moves in. Every expected figure is worked out by hand: a 100.00 taxable labor line
at 10% and a 50.00 non-taxable deposit line make a 160.00 invoice whose deposit credits the
liability, never income; sales by item and the profit and loss see only the labor; cash-basis
income recognises only the labor's share of what is paid.
"""
import pytest

from bookflow import BookflowError

COMPANY = 'Deposit Co'
DATE = '2026-06-01'


def _minor(row, field):
    return row[field]['minor_units']


@pytest.fixture
def deposit(client):
    client.run('company new', dict(organization='Demo Holdings LLC', legal_name='Deposit Co LLC', display_name=COMPANY,
                                   home_currency='USD', timezone='America/Chicago'))
    run = lambda name, data: client.run(name, data, company=COMPANY)
    run('company update', dict(sales_tax_enabled=True))
    account = lambda name, kind: run('account create', dict(name=name, type=kind))['id']
    deposits = account('Customer Deposits', 'other_current_liability')
    clearing = account('Cutover Clearing', 'other_current_asset')
    roles = {a['system_role']: a['id'] for a in run('account list', {})['items'] if a.get('system_role')}
    income = run('account show', dict(account='Service Income'))['id']
    codes = run('sales-tax-code list', {})['items']
    taxable = next(c['id'] for c in codes if c['taxable'])
    exempt = next(c['id'] for c in codes if not c['taxable'])
    agency = run('vendor create', dict(name='Deposit tax agency', is_tax_agency=True))['id']
    tax = run('item create', dict(name='Deposit ten percent', type='sales_tax_item', tax_percent='10',
                                  tax_agency_vendor_id=agency, liability_account_id=roles['sales_tax_payable']))['id']
    labor = run('item create', dict(name='Labor', type='service', description='Labor', price='100.00',
                                    income_account_id=income, sales_tax_code_id=taxable))['id']
    held = run('item create', dict(name='Deposit', type='other_charge', description='Deposit held', price='50.00',
                                   income_account_id=deposits, sales_tax_code_id=exempt))['id']
    customer = run('customer create', dict(name='Deposit customer', sales_tax_code_id=taxable))['id']
    return dict(run=run, deposits=deposits, clearing=clearing, income=income, roles=roles, tax=tax, labor=labor,
                held=held, customer=customer, exempt=exempt)


def _balances(run, date_to):
    rows = run('report trial-balance', dict(date_to=date_to, limit=200))['rows']
    return {row['account_id']: _minor(row, 'signed_net') for row in rows}


def test_an_other_charge_names_a_balance_sheet_account_no_other_ledger_owns(deposit):
    run = deposit['run']
    accounts = {a['full_name']: a['id'] for a in run('account list', {})['items']}
    refused = []
    for name in ('Undeposited Funds', 'Opening Balance Equity', 'Accounts Receivable', 'Checking'):
        with pytest.raises(BookflowError) as caught:
            run('item create', dict(name='Bad ' + name, type='other_charge', description='x', price='1.00',
                                    income_account_id=accounts[name], sales_tax_code_id=deposit['exempt']))
        assert caught.value.code == 'E_VALIDATION'
        refused.append(caught.value.details['fields'][0]['field'])
    assert refused == ['income_account_id'] * 4
    with pytest.raises(BookflowError) as caught:
        run('item create', dict(name='Service on a liability', type='service', description='x', price='1.00',
                                income_account_id=deposit['deposits'], sales_tax_code_id=deposit['exempt']))
    assert caught.value.details['fields'][0] == {'field': 'income_account_id',
                                                 'problem': 'must reference an account of type income, other_income'}
    clearing = run('item create', dict(name='Opening balance', type='other_charge', description='Open balance',
                                       price='0.00', income_account_id=deposit['clearing'], sales_tax_code_id=deposit['exempt']))
    assert clearing['income_account_id'] == deposit['clearing']


def test_the_deposit_credits_the_liability_and_only_the_labor_is_taxed_and_sold(deposit):
    run = deposit['run']
    invoice = run('invoice post', dict(date=DATE, customer=deposit['customer'], sales_tax_item=deposit['tax'],
                                       lines=[dict(item=deposit['labor']), dict(item=deposit['held'])]))
    assert invoice['revision']['total_minor_units'] == 16000
    balances = _balances(run, '2026-06-30')
    assert balances[deposit['roles']['accounts_receivable']] == 16000
    assert balances[deposit['income']] == -10000
    assert balances[deposit['deposits']] == -5000
    assert balances[deposit['roles']['sales_tax_payable']] == -1000
    liability = run('sales-tax liability', dict(date_to='2026-06-30'))
    assert _minor(liability['totals'], 'balance') == 1000
    for basis in ('accrual', 'cash'):
        sold = run('report sales-by-item', dict(date_from='2026-06-01', date_to='2026-06-30', basis=basis))
        assert deposit['held'] not in {row.get('item_id') for row in sold['rows']}
    sold = run('report sales-by-item', dict(date_from='2026-06-01', date_to='2026-06-30', basis='accrual'))
    assert {row.get('item_id'): _minor(row, 'income') for row in sold['rows']} == {deposit['labor']: 10000}
    pnl = run('report profit-and-loss', dict(date_from='2026-06-01', date_to='2026-06-30', basis='accrual'))
    assert deposit['deposits'] not in {row.get('account_id') for row in pnl['rows']}

    # A correction re-checks the captured account and still accepts the liability.
    run('invoice update', dict(invoice=invoice['id'], expected_version=invoice['version'], memo='Deposit taken'))

    # A credit memo of the deposit debits the liability back.
    run('credit-memo post', dict(date='2026-06-02', customer=deposit['customer'], lines=[dict(item=deposit['held'])]))
    assert _balances(run, '2026-06-30').get(deposit['deposits'], 0) == 0


def test_cash_basis_recognises_only_the_labor_share_of_a_payment(deposit):
    run = deposit['run']
    invoice = run('invoice post', dict(date=DATE, customer=deposit['customer'], sales_tax_item=deposit['tax'],
                                       lines=[dict(item=deposit['labor']), dict(item=deposit['held'])]))

    def cash_income():
        pnl = run('report profit-and-loss', dict(date_from='2026-06-01', date_to='2026-06-30', basis='cash'))
        assert deposit['deposits'] not in {row.get('account_id') for row in pnl['rows']}
        return sum(_minor(row, 'amount') for row in pnl['rows'] if row.get('account_id') == deposit['income'])

    assert cash_income() == 0
    def pay(key, amount):
        version = run('invoice show', dict(invoice=invoice['id']))['version']
        run('payment receive', dict(customer=deposit['customer'], date='2026-06-10', amount=amount, operation_key=key,
                                    payment_method='Check',
                                    applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=version,
                                                                                 amount=amount)])))
    pay('deposit-half', '80.00')
    half = cash_income()
    assert 0 < half < 10000
    pay('deposit-rest', '80.00')
    assert cash_income() == 10000
