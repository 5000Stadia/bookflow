"""Early-payment discounts, checked by hand.

Every company here is its own, with the "2% 10 Net 30" terms the standard profile ships: a
document dated 2026-03-01 can be discounted through 2026-03-11 and is due 2026-03-31. Each
figure asserted is worked out in the comment beside it, from the documents' own amounts, and
compared with what the report commands say -- never with the payment's own output.

The rules under test (``company/early_discounts.py``):
- the suggested discount is the terms percentage of the document total, sales tax included,
  less any discount already taken on it, at most what is open, and zero after the discount date;
- a discount is taken only when named, and one named after the discount date is taken with a
  warning;
- a customer discount posts Dr Discounts Given / Cr Accounts Receivable on the receipt, a vendor
  discount Dr Accounts Payable / Cr Discounts Taken on the bill payment, and the document is
  settled by the cash plus the discount;
- on the cash basis the discount counts as payment: the document's income or expense is
  recognized in full on the payment date, beside the discount itself.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

TERMS = '2% 10 Net 30'


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'discounts'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Discount organization')
    company = client.company.new(legal_name='Discounts', home_currency='USD', timezone='UTC',
                                 organization='Discount organization', chart='general')['company_id']

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    accounts = run('account query', dict(limit=200))['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    parts = run('account create', dict(name='Parts Bought', type='expense'))['id']
    terms = {row['name']: row['id'] for row in run('term query', dict(limit=50))['items']}
    methods = {row['name']: row['id'] for row in run('payment-method query', dict(limit=50))['items']}
    exempt = next(row['id'] for row in run('sales-tax-code list')['items'] if not row['taxable'])
    item = run('item create', dict(name='Service call', type='service', sales_enabled=True,
                                   description='Service call', sales_tax_code_id=exempt,
                                   income_account_id=income, price='100'))['id']
    customer = run('customer create', dict(name='Adams Plumbing', terms_id=terms[TERMS]))['id']
    vendor = run('vendor create', dict(name='Northside Supply', terms_id=terms[TERMS]))['id']
    return dict(client=client, company=company, run=run, bank=bank, income=income, parts=parts,
                terms=terms, methods=methods, item=item, customer=customer, vendor=vendor)


# ------------------------------------------------------------------ helpers reading the books


def account(books, name):
    return next(row['id'] for row in books['run']('account query', dict(limit=200))['items'] if row['full_name'] == name)


def balances(books, date_to='2026-12-31'):
    """Signed minor units per account id from the trial balance, debits positive, zeros dropped."""
    report = books['run']('report trial-balance', dict(date_to=date_to, limit=200))
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows'] if row['debit']['minor_units'] != row['credit']['minor_units']}


def pnl(books, date_from, date_to, basis):
    """Profit-and-loss amounts by account id, and net income, on one basis."""
    report = books['run']('report profit-and-loss', dict(date_from=date_from, date_to=date_to, basis=basis, limit=200))
    rows = {row['account_id']: row['amount']['minor_units'] for row in report['rows'] if row['amount']['minor_units']}
    return rows, report['totals']['net_income']['minor_units']


def invoice(books, amount, date='2026-03-01', **extra):
    return books['run']('invoice post', dict(customer=books['customer'], date=date,
                        lines=[dict(item=books['item'], quantity='1', unit_price=amount)], **extra),
                        reason='Bill the customer')


def receive(books, amount, applications, *, date='2026-03-08', key='receive-1', discounts=None, **extra):
    raw = dict(customer=books['customer'], date=date, amount=amount, payment_method=books['methods']['Check'],
               deposit_to=books['bank'], operation_key=key,
               applications=dict(mode='inline', items=[
                   dict(invoice=sale['id'], expected_version=version, amount=cash)
                   for sale, version, cash in applications]), **extra)
    if discounts is not None:
        raw['discounts'] = discounts
    return books['run']('payment receive', raw, reason='Customer paid')


def settlement(books, sale):
    return books['run']('invoice settlement', dict(invoice=sale['id']))


def version(books, sale):
    return settlement(books, sale)['version']


def suggested(books, sale, date):
    listed = books['run']('payment invoices', dict(mode='new_receipt', customer=books['customer'], date=date))
    row = next(item for item in listed['items'] if item['invoice_id'] == sale['id'])
    return row['discount_date'], row['suggested_discount_minor_units']


def bill(books, amount, date='2026-03-01'):
    return books['run']('bill post', dict(vendor=books['vendor'], date=date,
                        expenses=[dict(account=books['parts'], amount=amount)]), reason='Enter the bill')


def pay(books, rows, date='2026-03-08', **extra):
    return books['run']('bill pay', dict(date=date, bills=rows, funding_account=books['bank'],
                                         method=books['methods']['Check'], **extra), reason='Pay the vendor')


def bill_row(books, sale):
    return next(row for row in books['run']('bill query', dict(limit=50))['items'] if row['id'] == sale['id'])


def reports(books, as_of='2026-03-31'):
    """Receivable reports' totals: open invoices balance, A/R aging total, customer balance."""
    run = books['run']
    return (run('report open-invoices', dict(as_of=as_of))['totals']['balance']['minor_units'],
            run('report ar-aging', dict(as_of=as_of))['totals']['total']['minor_units'],
            run('report customer-balance-summary', dict(as_of=as_of))['totals']['balance']['minor_units'])


def payables(books, as_of='2026-03-31'):
    run = books['run']
    return (run('report unpaid-bills', dict(as_of=as_of))['totals']['balance']['minor_units'],
            run('report ap-aging', dict(as_of=as_of))['totals']['total']['minor_units'],
            run('report vendor-balance-summary', dict(as_of=as_of))['totals']['balance']['minor_units'])


# ------------------------------------------------------------------ customer side


def test_customer_discount_inside_the_window_settles_the_invoice(books):
    sale = invoice(books, '1000.00')
    # 2% of 1,000.00 = 20.00, available through 2026-03-01 + 10 days = 2026-03-11.
    assert suggested(books, sale, '2026-03-08') == ('2026-03-11', 2000)
    paid = receive(books, '980.00', [(sale, 1, '980.00')], discounts=[dict(invoice=sale['id'], amount='20.00')])
    assert paid['warnings'] == []
    application = paid['effect']['applications'][0]
    assert application['amount']['minor_units'] == 100000       # 980.00 cash + 20.00 discount
    assert application['discount']['minor_units'] == 2000
    assert application['suggested_discount']['minor_units'] == 2000
    assert paid['current']['received_minor_units'] == 98000 and paid['current']['discount_minor_units'] == 2000
    assert paid['current']['available_minor_units'] == 0
    assert settlement(books, sale)['due_minor_units'] == 0
    given = account(books, 'Discounts Given')
    # Bank +980.00, Discounts Given +20.00, receivable 1,000.00 - 1,000.00 = 0, Sales -1,000.00.
    assert balances(books) == {books['bank']: 98000, given: 2000, books['income']: -100000}
    assert reports(books) == (0, 0, 0)
    # The settlement history names the discount on the edge that carries it.
    history = settlement(books, sale)['applications']
    assert [(row['amount_minor_units'], row['discount_minor_units']) for row in history] == [(100000, 2000)]


def test_the_suggestion_includes_sales_tax_and_a_dry_run_shows_it(books):
    run = books['run']
    agency = run('vendor create', dict(name='State tax agency', is_tax_agency=True))['id']
    taxable = next(row['id'] for row in run('sales-tax-code list')['items'] if row['taxable'])
    liability = account(books, 'Sales Tax Payable')
    info = run('company show')
    run('company update', dict(expected_version=info['info_version'], sales_tax_enabled=True))
    tax = run('item create', dict(name='Eight percent', type='sales_tax_item', tax_percent='8',
                                  tax_agency_vendor_id=agency, liability_account_id=liability))['id']
    sale = run('invoice post', dict(customer=books['customer'], date='2026-03-01', sales_tax_item=tax,
               lines=[dict(item=books['item'], quantity='1', unit_price='1000.00', tax_code=taxable)]), reason='Taxed sale')
    # 1,000.00 + 8% tax = 1,080.00; 2% of the whole total = 21.60 (the anchor's rule: tax included).
    assert settlement(books, sale)['gross_minor_units'] == 108000
    assert suggested(books, sale, '2026-03-11') == ('2026-03-11', 2160)
    # The day after the discount date nothing is suggested.
    assert suggested(books, sale, '2026-03-12') == ('2026-03-11', 0)
    raw = dict(customer=books['customer'], date='2026-03-10', amount='1058.40', payment_method=books['methods']['Check'],
               deposit_to=books['bank'], operation_key='taxed',
               applications=dict(mode='inline', items=[dict(invoice=sale['id'], expected_version=1, amount='1058.40')]),
               discounts=[dict(invoice=sale['id'], amount='21.60')])
    before = balances(books)
    preview = run('payment receive', raw, reason='Taxed receipt', dry_run=True)
    assert preview['effect']['applications'][0]['suggested_discount']['minor_units'] == 2160
    assert balances(books) == before
    run('payment receive', raw, reason='Taxed receipt')
    given = account(books, 'Discounts Given')
    # Bank 1,058.40 + discount 21.60 = 1,080.00 against Sales 1,000.00 and tax 80.00.
    assert balances(books) == {books['bank']: 105840, given: 2160, books['income']: -100000, liability: -8000}
    assert settlement(books, sale)['due_minor_units'] == 0


def test_an_edited_discount_and_two_partial_payments(books):
    sale = invoice(books, '500.00')
    assert suggested(books, sale, '2026-03-05') == ('2026-03-11', 1000)     # 2% of 500.00
    # First payment: 200.00 cash plus a discount edited down to 7.50 -> 292.50 still due.
    receive(books, '200.00', [(sale, 1, '200.00')], date='2026-03-05', key='part-1',
            discounts=[dict(invoice=sale['id'], amount='7.50')])
    assert settlement(books, sale)['due_minor_units'] == 29250
    # The terms offered 10.00 in all; 7.50 is taken, so 2.50 is what is still suggested.
    assert suggested(books, sale, '2026-03-06') == ('2026-03-11', 250)
    receive(books, '290.00', [(sale, version(books, sale), '290.00')], date='2026-03-06', key='part-2',
            discounts=[dict(invoice=sale['id'], amount='2.50')])
    assert settlement(books, sale)['due_minor_units'] == 0
    given = account(books, 'Discounts Given')
    # Cash 490.00 + discounts 10.00 = 500.00.
    assert balances(books) == {books['bank']: 49000, given: 1000, books['income']: -50000}
    assert reports(books) == (0, 0, 0)


def test_a_discount_after_the_window_is_taken_only_when_named_and_warns(books):
    sale = invoice(books, '300.00')
    assert suggested(books, sale, '2026-03-20') == ('2026-03-11', 0)
    paid = receive(books, '294.00', [(sale, 1, '294.00')], date='2026-03-20',
                   discounts=[dict(invoice=sale['id'], amount='6.00')])
    assert len(paid['warnings']) == 1 and '2026-03-11' in paid['warnings'][0]
    application = paid['effect']['applications'][0]
    assert application['discount']['minor_units'] == 600 and application['suggested_discount']['minor_units'] == 0
    assert settlement(books, sale)['due_minor_units'] == 0
    # Without a discount the same short payment leaves 6.00 open: nothing is taken silently.
    other = invoice(books, '300.00')
    receive(books, '294.00', [(other, 1, '294.00')], date='2026-03-09', key='plain')
    assert settlement(books, other)['due_minor_units'] == 600


def test_refusals(books):
    sale = invoice(books, '100.00')
    other = invoice(books, '100.00')
    cases = [
        # A discount on an invoice given no cash must name that invoice's version.
        dict(applications=[(sale, 1, '98.00')], discounts=[dict(invoice=other['id'], amount='2.00')]),
        # ...and a stale one is refused.
        dict(applications=[(sale, 1, '98.00')], discounts=[dict(invoice=other['id'], amount='2.00', expected_version=9)]),
        # A discount alone cannot exceed what is due either.
        dict(applications=[(sale, 1, '98.00')], discounts=[dict(invoice=other['id'], amount='100.01', expected_version=1)]),
        # Cash plus discount beyond what is due.
        dict(applications=[(sale, 1, '98.00')], discounts=[dict(invoice=sale['id'], amount='2.01')]),
        # A discount of nothing.
        dict(applications=[(sale, 1, '98.00')], discounts=[dict(invoice=sale['id'], amount='0.00')]),
    ]
    for case in cases:
        with pytest.raises(BookflowError) as refused:
            receive(books, '98.00', case['applications'], discounts=case['discounts'], key='refused')
        assert refused.value.code in ('E_VALIDATION', 'E_APPLICATION_CAPACITY', 'E_VERSION_CONFLICT')
    with pytest.raises(BookflowError):
        receive(books, '98.00', [(sale, 1, '98.00')], key='revenue-account',
                discounts=[dict(invoice=sale['id'], amount='2.00')], discount_account=books['bank'])
    assert balances(books) == {account(books, 'Accounts Receivable'): 20000, books['income']: -20000}


def test_unapply_leaves_the_discount_as_credit_and_void_reverses_it(books):
    run = books['run']
    sale = invoice(books, '1000.00')
    paid = receive(books, '980.00', [(sale, 1, '980.00')], discounts=[dict(invoice=sale['id'], amount='20.00')])
    application = paid['effect']['applications'][0]['application_id']
    run('payment unapply', dict(payment=paid['id'], expected_version=paid['version'], operation_key='unapply',
        applications=[dict(application_id=application, invoice_expected_version=version(books, sale))]),
        reason='Wrong invoice')
    shown = run('payment show', dict(payment=paid['id']))
    # The anchor's rule: the discount on an invoice no longer paid stays with the payment as credit.
    assert shown['current']['available_minor_units'] == 100000 and shown['current']['discount_minor_units'] == 2000
    assert settlement(books, sale)['due_minor_units'] == 100000
    # Invoice 1,000.00 open, credit 1,000.00 unapplied: the customer owes nothing net.
    assert reports(books)[2] == 0
    run('payment void', dict(payment=paid['id'], expected_version=shown['version'], operation_key='void'),
        reason='Entered in error')
    given = account(books, 'Discounts Given')
    # Only the invoice is left: receivable 1,000.00 against Sales; the discount is gone.
    assert balances(books) == {account(books, 'Accounts Receivable'): 100000, books['income']: -100000}
    assert given not in balances(books)
    assert reports(books) == (100000, 100000, 100000)


def test_a_correction_restates_the_discount(books):
    run = books['run']
    sale = invoice(books, '500.00')
    # 600.00 received for a 500.00 invoice taking 10.00 discount: 490.00 settles, 110.00 is credit.
    paid = receive(books, '600.00', [(sale, 1, '490.00')], discounts=[dict(invoice=sale['id'], amount='10.00')])
    assert paid['current']['available_minor_units'] == 11000
    corrected = run('payment update', dict(payment=paid['id'], expected_version=paid['version'], amount='550.00',
        operation_key='correct', invoice_versions=[dict(invoice=sale['id'], expected_version=version(books, sale))]),
        reason='Check was 550.00')
    assert corrected['current']['received_minor_units'] == 55000
    assert corrected['current']['discount_minor_units'] == 1000
    assert corrected['current']['available_minor_units'] == 6000       # 550.00 - 490.00
    given = account(books, 'Discounts Given')
    receivable = account(books, 'Accounts Receivable')
    # Bank 550.00, discount 10.00; receivable 500.00 - 500.00 - 60.00 credit = -60.00.
    assert balances(books) == {books['bank']: 55000, given: 1000, receivable: -6000, books['income']: -50000}
    assert settlement(books, sale)['due_minor_units'] == 0
    # Cash cannot drop below what settled the invoice.
    with pytest.raises(BookflowError) as refused:
        run('payment update', dict(payment=paid['id'], expected_version=corrected['version'], amount='480.00',
            operation_key='too-low', invoice_versions=[dict(invoice=sale['id'], expected_version=version(books, sale))]),
            reason='Too low')
    assert refused.value.code == 'E_APPLIED_EXCEEDS_TOTAL'


def test_the_closing_date_holds_discounts(books):
    run = books['run']
    sale = invoice(books, '100.00')
    paid = receive(books, '98.00', [(sale, 1, '98.00')], discounts=[dict(invoice=sale['id'], amount='2.00')])
    later = invoice(books, '100.00')
    run('company update', dict(expected_version=run('company show')['info_version'], closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        receive(books, '98.00', [(later, 1, '98.00')], key='closed', discounts=[dict(invoice=later['id'], amount='2.00')])
    assert closed.value.code == 'E_PERIOD_CLOSED'
    with pytest.raises(BookflowError) as closed:
        run('payment void', dict(payment=paid['id'], expected_version=paid['version'], operation_key='void-closed'),
            reason='Too late')
    assert closed.value.code in ('E_PERIOD_CLOSED', 'E_HAS_APPLICATIONS')


def test_cash_basis_counts_the_discount_as_payment(books):
    # Invoiced in February, paid in March with the discount (discount date 2026-03-02).
    sale = invoice(books, '1000.00', date='2026-02-20')
    receive(books, '980.00', [(sale, 1, '980.00')], date='2026-03-01', discounts=[dict(invoice=sale['id'], amount='20.00')])
    given = account(books, 'Discounts Given')
    # Accrual: February earns 1,000.00; March carries only the 20.00 discount.
    assert pnl(books, '2026-02-01', '2026-02-28', 'accrual') == ({books['income']: 100000}, 100000)
    assert pnl(books, '2026-03-01', '2026-03-31', 'accrual') == ({given: -2000}, -2000)
    # Cash: February earns nothing; March earns the whole 1,000.00 less the 20.00 discount = the cash.
    assert pnl(books, '2026-02-01', '2026-02-28', 'cash') == ({}, 0)
    assert pnl(books, '2026-03-01', '2026-03-31', 'cash') == ({books['income']: 100000, given: -2000}, 98000)


# ------------------------------------------------------------------ vendor side


def test_vendor_discount_inside_the_window(books):
    sale = bill(books, '1000.00')
    row = bill_row(books, sale)
    assert (row['discount_date'], row['early_discount_minor_units']) == ('2026-03-11', 2000)
    paid = pay(books, [dict(bill=sale['id'], amount='980.00', discount='20.00')])
    assert paid['warnings'] == [] and paid['paid_minor_units'] == 98000 and paid['discount_minor_units'] == 2000
    line = paid['payments'][0]['revision']['lines'][0]
    assert (line['amount_minor_units'], line['discount_minor_units'], line['suggested_discount_minor_units']) == (100000, 2000, 2000)
    shown = books['run']('bill show', dict(bill=sale['id']))['settlement_current']
    assert shown['open_minor_units'] == 0
    assert {row['source_type']: row['applied_minor_units'] for row in shown['sources']} == {
        'bill_payment': 98000, 'early_discount': 2000}
    taken = account(books, 'Discounts Taken')
    # Parts +1,000.00, bank -980.00, Discounts Taken -20.00, payable 1,000.00 - 1,000.00 = 0.
    assert balances(books) == {books['parts']: 100000, books['bank']: -98000, taken: -2000}
    assert payables(books) == (0, 0, 0)
    # The check is written for the money, not the discount.
    assert paid['payments'][0]['total_minor_units'] == 98000


def test_vendor_partial_edited_and_late_discounts(books):
    sale = bill(books, '500.00')
    # 200.00 now with 5.00 of the 10.00 the terms offer.
    pay(books, [dict(bill=sale['id'], amount='200.00', discount='5.00')], date='2026-03-05')
    assert bill_row(books, sale)['early_discount_minor_units'] == 500      # 10.00 - 5.00 taken
    assert bill_row(books, sale)['settlement_current']['open_minor_units'] == 29500
    # The rest after the window, taking the remaining 5.00 anyway: taken, with a warning.
    late = pay(books, [dict(bill=sale['id'], amount='290.00', discount='5.00')], date='2026-03-20')
    assert len(late['warnings']) == 1 and '2026-03-11' in late['warnings'][0]
    assert late['payments'][0]['revision']['lines'][0]['suggested_discount_minor_units'] == 0
    taken = account(books, 'Discounts Taken')
    assert balances(books) == {books['parts']: 50000, books['bank']: -49000, taken: -1000}
    # With no discount and no amount, the whole open balance is paid.
    other = bill(books, '100.00')
    plain = pay(books, [dict(bill=other['id'])], date='2026-03-09')
    assert plain['paid_minor_units'] == 10000 and plain['discount_minor_units'] == 0
    # A discount with no amount pays what is open less the discount.
    third = bill(books, '100.00')
    implied = pay(books, [dict(bill=third['id'], discount='2.00')], date='2026-03-09')
    assert implied['paid_minor_units'] == 9800
    with pytest.raises(BookflowError) as refused:
        pay(books, [dict(bill=bill(books, '100.00')['id'], amount='99.00', discount='2.00')])
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    # A payment must still pay some money: a discount alone, to a vendor paid nothing, is refused.
    with pytest.raises(BookflowError) as refused:
        pay(books, [dict(bill=bill(books, '100.00')['id'], amount='0.00', discount='2.00')])
    assert refused.value.code == 'E_VALIDATION'


def test_vendor_unapply_and_void_reverse_the_discount(books):
    run = books['run']
    sale = bill(books, '1000.00')
    paid = pay(books, [dict(bill=sale['id'], amount='980.00', discount='20.00')])['payments'][0]
    freed = run('bill payment unapply', dict(payment=paid['id'], expected_version=paid['version']), reason='Wrong bill')
    # The whole 1,000.00 -- money and discount -- is now an unapplied debit against the vendor.
    assert freed['settlement_current']['unapplied_minor_units'] == 100000
    assert bill_row(books, sale)['settlement_current']['open_minor_units'] == 100000
    assert payables(books)[2] == 0
    # A discount is taken only when a bill is paid, never when capacity is re-pointed.
    with pytest.raises(BookflowError):
        run('bill payment apply', dict(payment=paid['id'], expected_version=freed['version'],
                                       bills=[dict(bill=sale['id'], discount='1.00')]), reason='Re-point')
    run('bill payment void', dict(payment=paid['id'], expected_version=freed['version']), reason='Entered in error')
    taken = account(books, 'Discounts Taken')
    payable = account(books, 'Accounts Payable')
    assert balances(books) == {books['parts']: 100000, payable: -100000}
    assert taken not in balances(books)
    assert payables(books) == (100000, 100000, 100000)


def test_vendor_closing_date_and_cash_basis(books):
    run = books['run']
    sale = bill(books, '1000.00', date='2026-02-20')
    paid = pay(books, [dict(bill=sale['id'], amount='980.00', discount='20.00')], date='2026-03-01')['payments'][0]
    taken = account(books, 'Discounts Taken')
    # Accrual: February carries the 1,000.00 expense; March the 20.00 discount taken.
    assert pnl(books, '2026-02-01', '2026-02-28', 'accrual') == ({books['parts']: 100000}, -100000)
    assert pnl(books, '2026-03-01', '2026-03-31', 'accrual') == ({taken: 2000}, 2000)
    # Cash: nothing in February; March recognizes the whole expense and the discount: net -980.00.
    assert pnl(books, '2026-02-01', '2026-02-28', 'cash') == ({}, 0)
    assert pnl(books, '2026-03-01', '2026-03-31', 'cash') == ({books['parts']: 100000, taken: 2000}, -98000)
    run('company update', dict(expected_version=run('company show')['info_version'], closing_date='2026-03-31'))
    with pytest.raises(BookflowError) as closed:
        pay(books, [dict(bill=bill(books, '100.00', date='2026-04-01')['id'], amount='98.00', discount='2.00')],
            date='2026-03-31')
    assert closed.value.code in ('E_PERIOD_CLOSED', 'E_VALIDATION')
    with pytest.raises(BookflowError) as closed:
        run('bill payment unapply', dict(payment=paid['id'], expected_version=paid['version']), reason='Too late')
    assert closed.value.code == 'E_PERIOD_CLOSED'


def test_the_discount_account_preferences(books):
    run = books['run']
    mine = run('account create', dict(name='Early Pay Discounts', type='other_income'))['id']
    run('company update', dict(expected_version=run('company show')['info_version'], vendor_discount_account_id=mine))
    sale = bill(books, '100.00')
    pay(books, [dict(bill=sale['id'], amount='98.00', discount='2.00')])
    assert balances(books)[mine] == -200
    assert not [row for row in run('account query', dict(limit=200))['items'] if row['full_name'] == 'Discounts Taken']
    with pytest.raises(BookflowError):
        run('company update', dict(expected_version=run('company show')['info_version'],
                                   customer_discount_account_id=books['bank']))


def test_deleting_a_discounted_receipt_cancels_the_discount(books):
    from tests.test_purchase_deletion import enable
    run = books['run']
    sale = invoice(books, '1000.00')
    paid = receive(books, '980.00', [(sale, 1, '980.00')], discounts=[dict(invoice=sale['id'], amount='20.00')])
    application = paid['effect']['applications'][0]['application_id']
    freed = run('payment unapply', dict(payment=paid['id'], expected_version=paid['version'], operation_key='unapply',
        applications=[dict(application_id=application, invoice_expected_version=version(books, sale))]), reason='Duplicate')
    enable(books, 'payment', deny_post=False)
    deleted = run('payment delete', dict(payment=paid['id'], expected_version=freed['version'],
                                         operation_key='delete'), reason='Duplicate receipt')
    # Cash, discount and receivable credits all cancelled: 1 cash + 1 discount + 1 receivable leg.
    assert deleted['status'] == 'deleted' and deleted['cancelled_posting_lines'] == 3
    assert balances(books) == {account(books, 'Accounts Receivable'): 100000, books['income']: -100000}
    assert reports(books) == (100000, 100000, 100000)



# ------------------------------------------------------------------ a discount with no cash beside it
#
# The anchor's windows take a discount on a document the payment gives no money: a customer
# short-pays one invoice and takes the discount on another. The payment itself still records cash.


def test_customer_discount_on_an_invoice_given_no_cash(books):
    first = invoice(books, '500.00')
    second = invoice(books, '300.00')
    # 490.00 settles the first with its 10.00 discount; the second takes its 6.00 discount alone.
    paid = receive(books, '490.00', [(first, 1, '490.00')], discounts=[
        dict(invoice=first['id'], amount='10.00'), dict(invoice=second['id'], amount='6.00', expected_version=1)])
    assert paid['warnings'] == []
    edges = {row['invoice_id']: (row['amount']['minor_units'], row['discount']['minor_units'])
             for row in paid['effect']['applications']}
    assert edges == {first['id']: (50000, 1000), second['id']: (600, 600)}
    assert paid['current']['received_minor_units'] == 49000 and paid['current']['discount_minor_units'] == 1600
    assert settlement(books, first)['due_minor_units'] == 0
    assert settlement(books, second)['due_minor_units'] == 29400          # 300.00 - 6.00
    given, receivable = account(books, 'Discounts Given'), account(books, 'Accounts Receivable')
    # Bank 490.00, discounts 16.00; receivable 800.00 - 500.00 - 6.00 = 294.00; Sales -800.00.
    assert balances(books) == {books['bank']: 49000, given: 1600, receivable: 29400, books['income']: -80000}
    assert reports(books) == (29400, 29400, 29400)
    # The rest of the second invoice arrives later with no discount.
    receive(books, '294.00', [(second, version(books, second), '294.00')], date='2026-03-20', key='rest')
    assert balances(books) == {books['bank']: 78400, given: 1600, books['income']: -80000}
    # Unapplied, the discount-only edge gives its 6.00 back to the receipt as the customer's
    # credit, and the second invoice has those 6.00 open again.
    shown = books['run']('payment show', dict(payment=paid['id']))
    edge = next(row['application_id'] for row in paid['effect']['applications'] if row['invoice_id'] == second['id'])
    freed = books['run']('payment unapply', dict(payment=paid['id'], expected_version=shown['version'],
        operation_key='free-second', applications=[dict(application_id=edge, invoice_expected_version=version(books, second))]),
        reason='Discount not allowed after all')
    assert freed['current']['available_minor_units'] == 600
    assert settlement(books, second)['due_minor_units'] == 600


def test_a_job_discounted_with_no_cash_and_the_receipt_deposited(books):
    """The payer's cash settles its own invoice; its job's invoice takes only a discount.

    The job's receipt component then carries no cash at all, which is what a deposit reads: the
    deposit takes the 196.00 cash, not the 196.00 + 4.00 + 2.00 the components carry.
    """
    run = books['run']
    job = run('customer create', dict(name='Kitchen remodel', parent_id=books['customer'],
                                      terms_id=books['terms'][TERMS]))['id']
    own = invoice(books, '200.00')
    theirs = run('invoice post', dict(customer=job, date='2026-03-01',
                 lines=[dict(item=books['item'], quantity='1', unit_price='100.00')]), reason='Bill the job')
    paid = run('payment receive', dict(customer=books['customer'], date='2026-03-08', amount='196.00',
               payment_method=books['methods']['Check'], operation_key='job-discount',
               applications=dict(mode='inline', items=[dict(invoice=own['id'], expected_version=1, amount='196.00')]),
               discounts=[dict(invoice=own['id'], amount='4.00'), dict(invoice=theirs['id'], amount='2.00', expected_version=1)]),
               reason='Parent paid; the job took its discount')
    assert {row['party_id']: row['received_minor_units'] for row in paid['current']['components']} == {
        books['customer']: 20000, job: 200}
    available = run('deposit sources', dict(date='2026-03-09'))
    row = next(item for item in available['items'] if item['source'] == paid['id'])
    assert row['eligible'] and row['amount']['minor_units'] == 19600
    run('deposit post', dict(operation_key='bank-it', document=dict(mode='inline', deposit_to=books['bank'],
        date='2026-03-09', sources=[dict(source_type='payment', source=paid['id'], expected_version=row['expected_version'])])),
        reason='Bank the check')
    given, receivable = account(books, 'Discounts Given'), account(books, 'Accounts Receivable')
    # Bank 196.00, discounts 6.00, receivable 300.00 - 200.00 - 2.00 = 98.00, Sales -300.00.
    assert balances(books) == {books['bank']: 19600, given: 600, receivable: 9800, books['income']: -30000}


def test_vendor_discount_on_a_bill_given_no_money(books):
    first = bill(books, '500.00')
    second = bill(books, '300.00')
    # 490.00 pays the first with its 10.00 discount; the second takes its 6.00 discount alone.
    paid = pay(books, [dict(bill=first['id'], amount='490.00', discount='10.00'),
                       dict(bill=second['id'], amount='0.00', discount='6.00')])
    assert paid['group_count'] == 1 and paid['paid_minor_units'] == 49000 and paid['discount_minor_units'] == 1600
    lines = {line['bill_id']: (line['amount_minor_units'], line['discount_minor_units'])
             for line in paid['payments'][0]['revision']['lines']}
    assert lines == {first['id']: (50000, 1000), second['id']: (600, 600)}
    assert bill_row(books, second)['settlement_current']['open_minor_units'] == 29400
    taken, payable = account(books, 'Discounts Taken'), account(books, 'Accounts Payable')
    # Parts 800.00; bank -490.00; Discounts Taken -16.00; payable 800.00 - 506.00 = -294.00.
    assert balances(books) == {books['parts']: 80000, books['bank']: -49000, taken: -1600, payable: -29400}
    assert payables(books) == (29400, 29400, 29400)
    # A discount with no amount and nothing else left open pays nothing on that row.
    third = bill(books, '100.00')
    implied = pay(books, [dict(bill=third['id'], discount='100.00'), dict(bill=second['id'])], date='2026-03-09')
    assert implied['paid_minor_units'] == 29400 and implied['discount_minor_units'] == 10000
    payment = implied['payments'][0]
    freed = books['run']('bill payment unapply', dict(payment=payment['id'], expected_version=payment['version']),
                         reason='Undo')
    books['run']('bill payment void', dict(payment=payment['id'], expected_version=freed['version']), reason='Undo')
    assert balances(books)[payable] == -29400 - 10000
