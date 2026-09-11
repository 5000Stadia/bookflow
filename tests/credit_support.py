"""A real migrated company for the credit settlement, refund and lifecycle suites.

Its own company every time, so every reported total is only the test's own -- there is no
demo seed underneath to add a figure nobody wrote down. Everything here is a fixture or a
read helper; the money lives in the tests, written out at the top of each file.
"""
import pytest
import sqlalchemy as sa

import bookflow

UNIT_PRICE = '25.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'credits'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Credits organization')
    company = client.company.new(legal_name='Credits', home_currency='USD', timezone='UTC',
                                 organization='Credits organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    from bookflow.company import schema as c
    from bookflow.storage.engine import open_database
    database = next(data_root.rglob('company.db'))
    with open_database(database, writable=True) as handle:
        liability = handle.conn.execute(sa.select(c.accounts.c.id).where(
            c.accounts.c.system_role == 'sales_tax_payable')).scalar_one()
        handle.conn.execute(c.company_info.update().values(
            sales_tax_enabled=True, sales_tax_liability_basis='invoice_date'))
        handle.conn.commit()

    accounts = client.account.query(company=company, limit=200)['items']
    receivable = next(row['id'] for row in accounts if row['type'] == 'accounts_receivable')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    checking = next(row['id'] for row in accounts if row['type'] == 'bank')
    codes = {row['taxable']: row['id'] for row in run('sales-tax-code list', {})['items']}
    agency = client.vendor.create(company=company, name='State Tax', is_tax_agency=True)['id']
    tax_item = run('item create', dict(name='Eight percent', type='sales_tax_item', tax_percent='8',
                                       tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    service = run('item create', dict(name='Site visit', type='service', sales_enabled=True,
                                      description='Site visit', income_account_id=income,
                                      price=UNIT_PRICE, sales_tax_code_id=codes[False]))['id']
    widget = run('item create', dict(name='Widget', type='non_inventory_part', sales_enabled=True,
                                     description='Widget', income_account_id=income, price=UNIT_PRICE,
                                     sales_tax_code_id=codes[True]))['id']
    ridge = client.customer.create(company=company, name='Ridge')['id']
    kerr = client.customer.create(company=company, name='Kerr')['id']
    check = next(row['id'] for row in run('payment-method list', {})['items'] if row['kind'] == 'check')
    return dict(client=client, company=company, run=run, database=database, receivable=receivable,
                income=income, checking=checking, liability=liability, tax_item=tax_item,
                service=service, widget=widget, ridge=ridge, kerr=kerr, method=check,
                taxable=codes[True], exempt=codes[False])


def balances(books, date='2026-04-30'):
    """Signed minor units per account from the real trial balance, debits positive."""
    report = books['run']('report trial-balance', dict(date_to=date, limit=200))
    rows = {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows']}
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return rows, report


def aging(books, as_of='2026-04-30'):
    report = books['run']('report ar-aging', dict(as_of=as_of))
    return {row['display_customer_label']: row for row in report['rows']}, report


def ties_to_the_receivable(books, as_of='2026-04-30'):
    """The A/R aging total is the receivable control balance. True at every step or not at all."""
    rows, report = aging(books, as_of)
    ledger, _ = balances(books, as_of)
    assert report['totals']['total']['minor_units'] == ledger.get(books['receivable'], 0)
    return rows, report


def settlement(books, invoice_id):
    return books['run']('invoice show', {'invoice': invoice_id})['settlement_current']


def worth(books, credit_id):
    return books['run']('credit-memo show', {'credit_memo': credit_id})['source_current']


def version(books, noun, identifier):
    return books['run'](noun + ' show', {noun.split()[-1].replace('-', '_'): identifier})['version']


def invoice(books, customer=None, date='2026-03-02', due='2026-04-01', lines=None, **extra):
    values = dict(customer=customer or books['ridge'], date=date, due_date=due,
                  lines=lines or [{'item': books['service'], 'quantity': '4', 'unit_price': UNIT_PRICE}])
    values.update(extra)
    return books['run']('invoice post', values, reason='Bill the customer')


def taxed_invoice(books, customer=None, quantity='4', date='2026-03-02'):
    return books['run']('invoice post', dict(
        customer=customer or books['kerr'], date=date, due_date='2026-04-01',
        sales_tax_item=books['tax_item'], sales_tax_calculation='line_component_half_even',
        lines=[{'item': books['widget'], 'quantity': quantity, 'unit_price': UNIT_PRICE,
                'tax_code': books['taxable']}]), reason='Bill the customer')


def goodwill_credit(books, amount, customer=None, date='2026-03-10'):
    return books['run']('credit-memo post', dict(
        customer=customer or books['ridge'], date=date,
        lines=[{'item': books['service'], 'quantity': '1', 'unit_price': amount}]),
        reason='Goodwill credit')


def returned_credit(books, invoice_id, line_id, quantity='1', customer=None, date='2026-04-05'):
    return books['run']('credit-memo post', dict(
        customer=customer or books['kerr'], date=date,
        lines=[{'source_invoice': invoice_id, 'source_line': line_id, 'quantity': quantity}]),
        reason='A unit came back')


def apply_credit(books, credit, invoice_id, amount=None, date=None):
    item = {'invoice': invoice_id, 'expected_version': books['run'](
        'invoice show', {'invoice': invoice_id})['version']}
    if amount is not None:
        item['amount'] = amount
    request = dict(credit_memo=credit['id'],
                   expected_version=worth_version(books, credit['id']), applications=[item])
    if date is not None:
        request['date'] = date
    return books['run']('customer-credit apply', request, reason='Apply the credit')


def worth_version(books, credit_id):
    return books['run']('credit-memo show', {'credit_memo': credit_id})['version']


def refund(books, credit_id, amount=None, date='2026-03-20', **extra):
    source = {'credit_memo': credit_id}
    if amount is not None:
        source['amount'] = amount
    request = dict(date=date, funding_account=books['checking'], method=books['method'],
                   sources=[source])
    request.update(extra)
    return books['run']('customer-refund post', request, reason='Refund the customer')
