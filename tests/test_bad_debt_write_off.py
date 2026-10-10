"""R177: a bad debt is written off without cash, as the anchor's Receive Payments for 0.00 does.

The anchor takes the open balance as a discount to a Bad Debt account on a receipt of nothing.
Bookflow's receipt already carries discounts, so the shape here is that receipt with `amount`
0.00: every invoice written off is named in `discounts`, `discount_account` is an expense
account, and a reason is required. Each figure is worked out in the comment beside it, from the
documents' own amounts, and compared with the report commands -- never with the receipt's own
output. Sales tax is left as it stood, the way the anchor's discount path leaves it.
"""
import pytest

from tests.test_row3_host import hosted  # noqa: F401  (fixture)

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'writeoff'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Write-off organization')
    company = client.company.new(legal_name='Write-offs', home_currency='USD', timezone='UTC',
                                 organization='Write-off organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    bad_debt = run('account create', dict(name='Bad Debt', type='expense'))['id']
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    agency = run('vendor create', dict(name='State tax agency', is_tax_agency=True))['id']
    liability = next(row['id'] for row in accounts if row['full_name'] == 'Sales Tax Payable')
    info = run('company show')
    run('company update', dict(expected_version=info['info_version'], sales_tax_enabled=True))
    tax = run('item create', dict(name='Eight percent', type='sales_tax_item', tax_percent='8',
                                  tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    taxable = next(row['id'] for row in run('sales-tax-code list')['items'] if row['taxable'])
    item = run('item create', dict(name='Service call', type='service', sales_enabled=True, description='Service call',
                                   sales_tax_code_id=taxable, income_account_id=income, price='100'))['id']
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    customer = run('customer create', dict(name='Bauer Builders'))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, bad_debt=bad_debt,
                methods=methods, item=item, customer=customer, tax=tax, liability=liability, taxable=taxable, exempt=exempt)


def account(books, name):
    return next(row['id'] for row in books['run']('account query', dict(limit=200))['items'] if row['full_name'] == name)


def balances(books, date_to='2026-12-31'):
    """Signed minor units per account id from the trial balance, debits positive, zeros dropped."""
    report = books['run']('report trial-balance', dict(date_to=date_to, limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def invoice(books, amount, date='2026-03-01', taxed=True, **extra):
    raw = dict(customer=books['customer'], date=date, lines=[dict(
        item=books['item'], quantity='1', unit_price=amount,
        tax_code=books['taxable'] if taxed else books['exempt'])], **extra)
    if taxed:
        raw['sales_tax_item'] = books['tax']
    return books['run']('invoice post', raw, reason='Bill the customer')


def due(books, sale):
    return books['run']('invoice settlement', dict(invoice=sale['id']))['due_minor_units']


def write_off(books, rows, *, key='wo-1', date='2026-09-30', reason='Customer is out of business', **extra):
    raw = dict(customer=books['customer'], date=date, amount='0.00', operation_key=key,
               discounts=[dict(invoice=sale['id'], amount=amount, expected_version=version)
                          for sale, amount, version in rows],
               discount_account=books['bad_debt'], **extra)
    return books['run']('payment receive', raw, reason=reason)


def test_a_zero_receipt_writes_off_a_taxed_invoice_and_leaves_the_sales_tax(books):
    sale = invoice(books, '1000.00')
    # 1,000.00 + 8% sales tax = 1,080.00 owed.
    assert due(books, sale) == 108000
    receivable = account(books, 'Accounts Receivable')
    assert balances(books) == {receivable: 108000, books['income']: -100000, books['liability']: -8000}
    done = write_off(books, [(sale, '1080.00', 1)])
    assert done['current']['received_minor_units'] == 0 and done['current']['discount_minor_units'] == 108000
    text = done['summary']['text']
    assert text.startswith('No cash received from Bauer Builders; the balance is written off to Bad Debt.') and 'Written off: 1080.00 USD' in text and 'Paid in full: 1 invoice' in text
    assert 'Early-payment' not in text and done['summary']['discount']['minor_units'] == 108000
    assert due(books, sale) == 0
    # The invoice's balance goes to Bad Debt; sales and the tax still owed to the agency stand
    # as they were: the anchor's discount path does not reduce sales tax either.
    assert balances(books) == {books['bad_debt']: 108000, books['income']: -100000, books['liability']: -8000}
    run = books['run']
    assert run('report ar-aging', dict(as_of='2026-12-31'))['totals']['total']['minor_units'] == 0
    assert run('report customer-balance-summary', dict(as_of='2026-12-31'))['totals']['balance']['minor_units'] == 0
    # Nothing is left for Make Deposits to pick up.
    assert run('payment query', {})['items'][0]['received_minor_units'] == 0
    assert run('deposit sources', dict(date='2026-12-31', limit=50, include_ineligible=True))['items'] == []


def test_the_inactive_customer_is_written_off_without_being_reactivated(books):
    run = books['run']
    sale = invoice(books, '250.00', taxed=False)
    customer = run('customer show', dict(customer=books['customer']))
    run('customer deactivate', dict(customer=books['customer'], expected_version=customer['version']), reason='Gone')
    assert run('customer show', dict(customer=books['customer']))['active'] is False
    write_off(books, [(sale, '250.00', 1)])
    assert due(books, sale) == 0
    assert run('customer show', dict(customer=books['customer']))['active'] is False
    assert balances(books) == {books['bad_debt']: 25000, books['income']: -25000}


def test_a_partial_write_off_and_a_write_off_after_cash_leave_the_rest_open(books):
    sale = invoice(books, '400.00', taxed=False)
    write_off(books, [(sale, '150.00', 1)])
    assert due(books, sale) == 25000
    assert balances(books)[books['bad_debt']] == 15000


def test_refusals_say_what_to_do(books):
    sale = invoice(books, '100.00', taxed=False)
    run = books['run']
    base = dict(customer=books['customer'], date='2026-09-30', amount='0.00', operation_key='refused',
                discounts=[dict(invoice=sale['id'], amount='100.00', expected_version=1)],
                discount_account=books['bad_debt'])

    def refused(raw, reason='Gone', code='E_VALIDATION'):
        with pytest.raises(BookflowError) as error:
            run('payment receive', raw, **({'reason': reason} if reason else {}))
        assert error.value.code == code
        return str(error.value.details) + error.value.message

    # No discounts: a zero receipt is not a write-off, and the refusal says how to make one.
    assert 'discounts' in refused(dict(base, discounts=[]))
    # No account named: the books do not guess where a bad debt goes.
    assert 'discount_account' in refused({k: v for k, v in base.items() if k != 'discount_account'})
    # An income account is a discount, not a bad debt.
    assert 'expense account' in refused(dict(base, discount_account=books['income']))
    # No reason.
    refused(base, reason=None, code='E_REASON_REQUIRED')
    # Cash applied alongside.
    refused(dict(base, applications=dict(mode='inline', items=[
        dict(invoice=sale['id'], expected_version=1, amount='10.00')])))
    # Negative cash.
    refused(dict(base, amount='-1.00'))
    assert due(books, sale) == 10000
    assert balances(books) == {account(books, 'Accounts Receivable'): 10000, books['income']: -10000}


def test_a_dry_run_changes_nothing_and_a_void_after_unapply_reopens_the_invoice(books):
    run = books['run']
    sale = invoice(books, '100.00', taxed=False)
    raw = dict(customer=books['customer'], date='2026-09-30', amount='0.00', operation_key='wo-dry',
               discounts=[dict(invoice=sale['id'], amount='100.00', expected_version=1)],
               discount_account=books['bad_debt'])
    preview = run('payment receive', raw, reason='Gone', dry_run=True)
    assert preview['current']['discount_minor_units'] == 10000 and due(books, sale) == 10000
    done = run('payment receive', raw, reason='Gone')
    assert due(books, sale) == 0
    shown = run('payment show', dict(payment=done['id']))
    assert shown['revision']['total']['minor_units'] == 0
    application = done['effect']['applications'][0]['application_id']
    run('payment unapply', dict(payment=done['id'], expected_version=done['version'], operation_key='wo-un',
        applications=[dict(application_id=application, invoice_expected_version=2)]), reason='Customer paid after all')
    after = run('payment show', dict(payment=done['id']))
    run('payment void', dict(payment=done['id'], expected_version=after['version'], operation_key='wo-void'),
        reason='Customer paid after all')
    assert due(books, sale) == 10000
    assert balances(books) == {account(books, 'Accounts Receivable'): 10000, books['income']: -10000}


def test_a_write_off_is_not_corrected_in_place(books):
    run = books['run']
    sale = invoice(books, '100.00', taxed=False)
    done = write_off(books, [(sale, '100.00', 1)])
    with pytest.raises(BookflowError) as refused:
        run('payment update', dict(payment=done['id'], expected_version=done['version'], operation_key='u1',
                                   memo='Retyped'), reason='Fix the memo')
    assert refused.value.code == 'E_VALIDATION' and 'payment void' in str(refused.value.details)


def test_an_agents_write_off_warns_it_and_lands_on_the_owners_review_list(hosted):
    """R163: an agent that writes a balance off is told so, and the owner's list names it."""
    from tests.test_entry_review import _review, _row, _setup
    person, bot, agent, bank, _ = _setup(hosted)
    income = person.ok('account create', {'name': 'Review sales', 'type': 'income'})['id']
    bad_debt = person.ok('account create', {'name': 'Review bad debt', 'type': 'expense'})['id']
    exempt = next(r['id'] for r in person.ok('sales-tax-code list', {})['items'] if not r['taxable'])
    item = person.ok('item create', dict(name='Review service', type='service', sales_enabled=True,
                                         description='Service', sales_tax_code_id=exempt,
                                         income_account_id=income, price='100'))['id']
    customer = person.ok('customer create', {'name': 'Review customer'})['id']
    sale = person.ok('invoice post', dict(customer=customer, date='2026-03-01', lines=[
        dict(item=item, quantity='1', unit_price='300.00', tax_code=exempt)]))
    raw = dict(customer=customer, date='2026-09-30', amount='0.00', operation_key='agent-wo',
               discounts=[dict(invoice=sale['id'], amount='300.00', expected_version=1)], discount_account=bad_debt)
    done = bot.ok('payment receive', raw)
    assert any('300.00' in w and 'Review bad debt' in w and 'entries-to-review' in w for w in done['warnings']), done['warnings']
    row = _row(_review(person), done['id'])
    assert row is not None and row['flags'] == ['write_off']
    assert row['actor_kind'] == 'agent' and row['account'] == 'Review bad debt'
    assert row['amount']['amount'] == '300.00' and row['transaction_type'] == 'payment'
    # The same write-off made by the owner is not on the list.
    other = person.ok('invoice post', dict(customer=customer, date='2026-03-02', lines=[
        dict(item=item, quantity='1', unit_price='50.00', tax_code=exempt)]))
    mine = person.ok('payment receive', dict(raw, operation_key='owner-wo',
        discounts=[dict(invoice=other['id'], amount='50.00', expected_version=1)]))
    assert _row(_review(person), mine['id']) is None
