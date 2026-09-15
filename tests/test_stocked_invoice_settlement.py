"""Paying an invoice that carries stock, where cost and income both credit the same line.

A stock-carrying invoice line leaves four posting rows behind, not two: the customer's
receivable and the income it recognises, and beside them the cost taken out of inventory.
Only the first pair is what the customer owes, so settling the line has to say which credit
it is settling. Every figure below is written out so a reader can add it up:

    Bought   2 x 8.00    ->  16.00 into Inventory Asset, 16.00 owed to the supplier
    Sold     1 x 12.00   ->  12.00 receivable, 12.00 income, 8.00 cost, 8.00 left in stock
    Paid       12.00     ->  12.00 cash in, 12.00 receivable gone, and the stock untouched

Nothing here reads the payment's own output back into an assertion about itself: the ledger
is read through ``report general-ledger``, the stock through ``report stock-status``, the
debt through ``invoice settlement``, and the settled rows through the stored allocations.
"""
from pathlib import Path
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net  # noqa: F401
from tests.payment_raw_evidence import database

BOUGHT = '8'        # each, two of them
SOLD = '12'         # the one unit the customer is invoiced for
COST = 800          # what that one unit took out of inventory
HELD = 800          # what the other unit is still worth


def _path(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def _stock(books, item, date):
    rows = books['run']('report stock-status', dict(as_of=date, limit=200))['rows']
    row = next(row for row in rows if row['item_id'] == item)
    return row['quantity_on_hand'], row['asset_value']['minor_units']


def _payment(books, payment_id):
    return books['run']('payment show', dict(payment=payment_id))


def _cash(books, payment_id):
    """Where the money landed, read off the receipt's own captured profile."""
    return _payment(books, payment_id)['revision']['profile']['deposit_account']['id']


def _settled(books, invoice_id):
    """What each live allocation says it settled, by the accounts its two rows posted to."""
    with sqlite3.connect(_path(books).as_uri() + '?mode=ro', uri=True) as db:
        return db.execute("""
            SELECT a.logical_kind, receivable.account_id, recognised.account_id, a.amount_minor_units
              FROM application_allocations a
              JOIN posting_line_sources ar_source ON ar_source.id = a.target_ar_source_id
              JOIN posting_lines receivable ON receivable.id = ar_source.posting_line_id
              JOIN posting_line_sources recognition_source ON recognition_source.id = a.target_recognition_source_id
              JOIN posting_lines recognised ON recognised.id = recognition_source.posting_line_id
             WHERE a.target_transaction_id = ? AND a.kind = 'allocation'
               AND NOT EXISTS (SELECT 1 FROM application_allocations inverse
                               WHERE inverse.reverses_allocation_id = a.id)
             ORDER BY a.target_ordinal, a.logical_kind, a.amount_minor_units""", (invoice_id,)).fetchall()


def _stocked_invoice(books, item, price=SOLD, quantity='2', cost=BOUGHT):
    run = books['run']
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity=quantity, unit_cost=cost)]), reason='Receive stock')
    return run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price=price)]), reason='Sell one unit')


def _receipt(books, invoice, amount, key, version, date='2017-01-03'):
    return dict(customer=books['customer'], date=date, amount=amount, operation_key=key,
                payment_method=books['methods']['Cash'],
                applications=dict(mode='inline', items=[
                    dict(invoice=invoice['id'], amount=amount, expected_version=version)]))


def test_full_payment_of_a_stocked_invoice_moves_cash_and_leaves_the_stock_alone(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    receivable = invoice['revision']['profile']['control_account']['id']
    assert _net(books) == {asset: HELD, books['payable']: -1600, receivable: 1200,
                           books['income']: -1200, books['cogs']: COST}
    assert _stock(books, item, '2017-01-02') == ('1', HELD)

    args = _receipt(books, invoice, '12', 'stocked-full', 1)
    before = database(_path(books))
    preview = run('payment receive', args, reason='Pay the invoice', dry_run=True)
    assert [(row['logical_kind'], row['amount']['minor_units']) for row in preview['effect']['allocations']] == [('net', 1200)]
    assert database(_path(books)) == before

    paid = run('payment receive', args, reason='Pay the invoice')
    cash = _cash(books, paid['id'])
    assert (paid['current']['applied_minor_units'], paid['current']['available_minor_units']) == (1200, 0)
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -1200,
                           books['cogs']: COST, cash: 1200}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 1200)]

    settled = database(_path(books))
    retry = run('payment receive', args, reason='Pay the invoice')
    assert retry['idempotent_replay'] and database(_path(books)) == settled

    application = paid['effect']['applications'][0]['application_id']
    undone = run('payment unapply', dict(payment=paid['id'], expected_version=1, operation_key='stocked-unapply',
        applications=[dict(application_id=application, invoice_expected_version=2)]), reason='Undo the allocation')
    assert undone['current']['available_minor_units'] == 1200
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 1200
    # Undoing the application is not a ledger event: the receipt's own receivable credit
    # stands, so the account still nets to nothing and only the debt moves back.
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -1200,
                           books['cogs']: COST, cash: 1200}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)

    again = run('payment apply', dict(payment=paid['id'], expected_version=2, date='2017-01-03',
        operation_key='stocked-reapply', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], amount='12', expected_version=3)])), reason='Apply it again')
    assert again['current']['applied_minor_units'] == 1200
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -1200,
                           books['cogs']: COST, cash: 1200}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 1200)]


def test_two_installments_settle_the_captured_income_and_never_the_inventory(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    receivable = invoice['revision']['profile']['control_account']['id']

    first = run('payment receive', _receipt(books, invoice, '5', 'stocked-part-one', 1), reason='Part payment')
    cash = _cash(books, first['id'])
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 700
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 500)]

    stale = run('payment receive', _receipt(books, invoice, '7', 'stocked-part-stale', 2),
                reason='Pay the rest', dry_run=True)
    run('invoice update', dict(invoice=invoice['id'], expected_version=2, memo='Customer reference',
        operation_key='stocked-memo', settlement_versions=[dict(payment=first['id'], expected_version=1)]),
        reason='Record the customer reference')
    before = database(_path(books))
    with pytest.raises(BookflowError) as refused:
        run('payment receive', dict(_receipt(books, invoice, '7', 'stocked-part-stale', 3),
            expected_facts_fingerprint=stale['facts_fingerprint']), reason='Pay the rest')
    assert refused.value.code == 'E_PREVIEW_STALE' and database(_path(books)) == before

    with pytest.raises(BookflowError) as capacity:
        run('payment receive', _receipt(books, invoice, '9', 'stocked-part-too-much', 3), reason='Pay too much')
    assert capacity.value.code == 'E_APPLICATION_CAPACITY' and database(_path(books)) == before

    run('payment receive', _receipt(books, invoice, '7', 'stocked-part-two', 3), reason='Pay the rest')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 500),
                                              ('net', receivable, books['income'], 700)]
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -1200,
                           books['cogs']: COST, cash: 1200}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_correcting_a_paid_stocked_invoice_restates_its_allocations(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    receivable = invoice['revision']['profile']['control_account']['id']
    line = invoice['revision']['lines'][0]['line_id']
    paid = run('payment receive', _receipt(books, invoice, '12', 'correction-cash', 1), reason='Pay the invoice')
    cash = _cash(books, paid['id'])

    # The customer was undercharged by three dollars; the twelve already paid stays where it is.
    corrected = run('invoice update', dict(invoice=invoice['id'], expected_version=2, operation_key='correction-price',
        settlement_versions=[dict(payment=paid['id'], expected_version=1)],
        lines=[dict(line_id=line, item=item, quantity='1', unit_price='15')]), reason='Correct the price')
    assert corrected['settlement']['current']['due_minor_units'] == 300
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 1200)]
    assert _net(books) == {asset: HELD, books['payable']: -1600, receivable: 300,
                           books['income']: -1500, books['cogs']: COST, cash: 1200}
    assert _stock(books, item, '2017-01-04') == ('1', HELD)

    # Two units for the same fifteen dollars: the stock moves, the money does not, and the
    # settled recipe is unchanged, so the stored allocation is left exactly as it was.
    before = _settled(books, invoice['id'])
    run('invoice update', dict(invoice=invoice['id'], expected_version=3, operation_key='correction-quantity',
        settlement_versions=[dict(payment=paid['id'], expected_version=_payment(books, paid['id'])['version'])],
        lines=[dict(line_id=line, item=item, quantity='2', net_amount='15')]), reason='Two units were delivered')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 300
    assert _settled(books, invoice['id']) == before
    assert _stock(books, item, '2017-01-04') == ('0', 0)
    assert _net(books) == {books['payable']: -1600, receivable: 300, books['income']: -1500,
                           books['cogs']: 1600, cash: 1200}

    history = run('payment history', dict(payment=paid['id']))
    run('payment unapply', dict(payment=paid['id'], expected_version=_payment(books, paid['id'])['version'],
        operation_key='correction-unapply',
        applications=[dict(application_id=paid['effect']['applications'][0]['application_id'],
                           invoice_expected_version=4)]), reason='Undo the allocation')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 1500
    assert _settled(books, invoice['id']) == []
    assert _net(books) == {books['payable']: -1600, receivable: 300, books['income']: -1500,
                           books['cogs']: 1600, cash: 1200}
    assert _stock(books, item, '2017-01-04') == ('0', 0)
    later = {row['id']: row for row in run('payment history', dict(payment=paid['id']))['items']}
    assert history['items'] and all(later[row['id']] == row for row in history['items'])


def test_mixed_service_and_stock_taxable_line_cents_settle_independently(books):
    run = books['run']
    client = books['client']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    taxable = next(row['id'] for row in run('sales-tax-code list', {})['items'] if row['taxable'])
    service = client.item.create(company=books['company'], name='Site visit', type='service',
                                 sales_enabled=True, purchase_enabled=False, description='Site visit',
                                 price='0.31', income_account_id=books['income'], sales_tax_code_id=taxable)['id']
    agency = client.vendor.create(company=books['company'], name='Stocked tax agency', is_tax_agency=True)['id']
    with sqlite3.connect(_path(books).as_uri() + '?mode=ro', uri=True) as db:
        liability = db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    run('company update', dict(sales_tax_enabled=True), reason='Charge sales tax')
    tax = run('item create', dict(name='Stocked eight percent', type='sales_tax_item', tax_percent='8',
        tax_agency_vendor_id=agency, liability_account_id=liability), reason='Register the tax')['id']

    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost=BOUGHT)]), reason='Receive stock')
    # 0.31 of service and 0.31 of stock is 0.62, and eight percent of that is 4.96 cents,
    # which is five: three cents on the first line and two on the second. The invoice owes
    # four separate things -- 31 and 3, then 31 and 2 -- and 0.67 in total.
    invoice = run('invoice post', dict(customer=books['customer'], date='2017-01-02', sales_tax_item=tax,
        lines=[dict(item=service, quantity='1', unit_price='0.31', tax_code=taxable),
               dict(item=item, quantity='1', unit_price='0.31', tax_code=taxable)]), reason='Sell both')
    receivable = invoice['revision']['profile']['control_account']['id']
    assert invoice['total_minor_units'] == 67
    assert [(line['net_minor_units'], line['tax_minor_units']) for line in invoice['revision']['lines']] == [(31, 3), (31, 2)]

    # 33 cents of 67, largest remainder: the whole parts are 15, 1, 15 and 0, which is 31,
    # and the two cents left go to the two largest remainders -- the second line's tax at
    # 66/67 and the first line's tax at 32/67 -- not to either of the tied halves at 18/67.
    first = run('payment receive', _receipt(books, invoice, '0.33', 'mixed-part-one', 1), reason='Part payment')
    cash = _cash(books, first['id'])
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 15),
                                              ('tax', receivable, liability, 2),
                                              ('net', receivable, books['income'], 15),
                                              ('tax', receivable, liability, 1)]
    run('payment receive', _receipt(books, invoice, '0.34', 'mixed-part-two', 2), reason='Pay the rest')
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    # What is left after the first payment -- 16, 1, 16 and 1 -- is exactly the second one.
    assert [row[3] for row in _settled(books, invoice['id'])] == [15, 16, 1, 2, 15, 16, 1, 1]
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -62,
                           books['cogs']: COST, liability: -5, cash: 67}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_zero_cost_stock_is_paid_without_inventing_a_cost_posting(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item, cost='0.00')
    receivable = invoice['revision']['profile']['control_account']['id']
    assert _net(books) == {receivable: 1200, books['income']: -1200}

    paid = run('payment receive', _receipt(books, invoice, '12', 'free-stock-cash', 1), reason='Pay the invoice')
    cash = _cash(books, paid['id'])
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 1200)]
    assert _net(books) == {books['income']: -1200, cash: 1200}
    assert _stock(books, item, '2017-01-03') == ('1', 0)
    with sqlite3.connect(_path(books).as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT count(*) FROM posting_lines WHERE account_id IN (?,?)',
                          (books['cogs'], asset)).fetchone()[0] == 0

    # An invoice for nothing owes nothing, and there is no payment to make against it.
    free = run('invoice post', dict(customer=books['customer'], date='2017-01-04',
        lines=[dict(item=item, quantity='1', unit_price='0.00')]), reason='Give one away')
    assert run('invoice settlement', dict(invoice=free['id']))['due_minor_units'] == 0
    before = database(_path(books))
    with pytest.raises(BookflowError) as refused:
        run('payment receive', _receipt(books, free, '1', 'free-invoice-cash', 1, date='2017-01-04'),
            reason='Pay for the free one')
    assert refused.value.code == 'E_APPLICATION_CAPACITY' and database(_path(books)) == before


def test_a_stocked_invoice_made_from_finished_work_is_paid_the_same_way(books):
    """Work billed into an invoice, with stock added to the same invoice afterwards.

    Quotes and work orders may not name stock -- they post nothing, so they cannot promise
    what the inventory ledger has not issued -- and the stock goes on as an extra charge on
    the invoice the finished work produced. The paying side then has to settle a line the
    billing owner captured beside one the sales owner did.
    """
    run = books['run']
    client = books['client']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    exempt = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    service = client.item.create(company=books['company'], name='Fit the elbow', type='service',
                                 sales_enabled=True, purchase_enabled=False, description='Fit the elbow',
                                 price='5.00', income_account_id=books['income'], sales_tax_code_id=exempt)['id']
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost=BOUGHT)]), reason='Receive stock')
    estimate = run('estimate create', dict(date='2017-01-02', title='Replace the elbow',
        customer=books['customer'], lines=[dict(item=service, quantity='1', unit_price='5.00')]),
        reason='Quote the work')
    accepted = run('estimate update', dict(estimate=estimate['id'], expected_version=1, status='accepted',
        decision_note='Customer accepted the quote'), reason='Record acceptance')
    billed = run('estimate invoice', dict(estimate=estimate['id'], expected_version=accepted['version'],
        conversion_key='stocked-work-bill', date='2017-01-03'), reason='Bill the finished work')
    assert billed['total_minor_units'] == 500 and billed['revision']['billing_sources']

    invoice = run('invoice update', dict(invoice=billed['id'], expected_version=billed['version'], lines=[
        dict(line_id=billed['revision']['lines'][0]['line_id'], item=service),
        dict(item=item, quantity='1', unit_price=SOLD)]), reason='Add the part that was fitted')
    receivable = invoice['revision']['profile']['control_account']['id']
    assert invoice['total_minor_units'] == 1700
    assert _net(books) == {asset: HELD, books['payable']: -1600, receivable: 1700,
                           books['income']: -1700, books['cogs']: COST}

    paid = run('payment receive', _receipt(books, invoice, '17', 'stocked-work-cash', invoice['version'],
                                           date='2017-01-04'), reason='Pay the invoice')
    cash = _cash(books, paid['id'])
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 500),
                                              ('net', receivable, books['income'], 1200)]
    assert _net(books) == {asset: HELD, books['payable']: -1600, books['income']: -1700,
                           books['cogs']: COST, cash: 1700}
    assert _stock(books, item, '2017-01-04') == ('1', HELD)


@pytest.mark.parametrize('side', ['receivable', 'recognition'])
def test_a_line_whose_two_rows_cannot_be_named_refuses_and_writes_nothing(books, side):
    """Narrowing by the captured account must not become a licence to pick a row.

    A second row is put behind the product's back on each side in turn -- another receivable
    for the same component, then another recognition for it -- and the payment has to refuse
    both rather than settle against whichever one it happened to see first. The removal of a
    row is not witnessed here because the ledger's own triggers refuse to delete or rewrite
    posted history at all, which is a stronger guarantee than this test could make.
    """
    run = books['run']
    item = _inventory_part(books)
    invoice = _stocked_invoice(books, item)
    path = _path(books)
    account = invoice['revision']['profile']['control_account']['id'] if side == 'receivable' else books['income']
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        original = dict(db.execute(
            'SELECT source.* FROM posting_line_sources source JOIN posting_lines leg ON leg.id=source.posting_line_id '
            'WHERE leg.account_id=? AND source.tax_component_id IS NULL AND source.amount_minor_units=1200',
            (account,)).fetchone())
        twin = dict(original, id=new_id())
        db.execute('INSERT INTO posting_line_sources (%s) VALUES (%s)' % (
            ','.join(twin), ','.join('?' for _ in twin)), tuple(twin.values()))
    before = database(path)
    with pytest.raises(BookflowError) as refused:
        run('payment receive', _receipt(books, invoice, '12', 'damaged-cash', 1), reason='Pay the invoice')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'] == [
        {'field': 'invoice', 'problem': 'stored component has ambiguous accounting attribution'}]
    assert database(path) == before


# ---------------------------------------------------------------- settling one with credit

"""Settling a stocked invoice with a credit memo rather than with cash.

`credit_settlement` routes through the same `payments._target_components` the cash side uses,
so it inherits the four-row problem and the fix for it: a stock-carrying line's receivable and
income rows are what a credit settles, and its cost pair is not a candidate. Everything below
reads the settled rows out of the stored allocations by the accounts they posted to, so a
credit that reached the inventory credit instead of the income credit would show here.

    Bought   2 x 8.00    ->  16.00 into Inventory Asset, 16.00 owed to the supplier
    Sold     1 x 12.00   ->  12.00 receivable, 12.00 income, 8.00 cost, 8.00 left in stock
    Credit      5.00     ->   5.00 off the income, 5.00 off the receivable, stock untouched
"""

GOODWILL = '5.00'   # the credit written against the same customer
CREDITED = 500      # what that credit is worth


def _exempt_code(books):
    return next(row['id'] for row in books['run']('sales-tax-code list', {})['items']
                if not row['taxable'])


def _sellable_service(books, name='Goodwill'):
    """A plain service item, so the credit memo itself carries no stock of its own."""
    return books['client'].item.create(
        company=books['company'], name=name, type='service', sales_enabled=True,
        purchase_enabled=False, description=name, price=GOODWILL,
        income_account_id=books['income'], sales_tax_code_id=_exempt_code(books))['id']


def _credit(books, amount=GOODWILL, date='2017-01-03', name='Goodwill'):
    return books['run']('credit-memo post', dict(
        customer=books['customer'], date=date,
        lines=[dict(item=_sellable_service(books, name), quantity='1', unit_price=amount)]),
        reason='Credit the customer')


def _invoice_version(books, invoice_id):
    return books['run']('invoice show', dict(invoice=invoice_id))['version']


def _apply_credit(books, credit, invoice_id, amount, date='2017-01-03'):
    return books['run']('customer-credit apply', dict(
        credit_memo=credit['id'], expected_version=credit['version'], date=date,
        applications=[dict(invoice=invoice_id, amount=amount,
                           expected_version=_invoice_version(books, invoice_id))]),
        reason='Apply the credit')


def test_a_credit_applied_to_a_stocked_invoice_settles_its_income_never_its_inventory(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    receivable = invoice['revision']['profile']['control_account']['id']
    credit = _credit(books)

    # The credit memo itself is the only thing that moves money: income down, receivable down.
    before = _net(books)
    assert before == {asset: HELD, books['payable']: -1600, receivable: 1200 - CREDITED,
                      books['income']: -1200 + CREDITED, books['cogs']: COST}

    applied = _apply_credit(books, credit, invoice['id'], GOODWILL)

    # Applying it posts nothing at all -- not "still balances", identical, cent for cent.
    assert _net(books) == before
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 1200 - CREDITED
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    # The settled rows are the receivable debit and the income credit the sale captured.
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], CREDITED)]
    assert {row[2] for row in _settled(books, invoice['id'])}.isdisjoint({asset, books['cogs']})

    application = applied['effect']['applications'][0]['application_id']
    undone = run('customer-credit unapply', dict(
        credit_memo=credit['id'], expected_version=applied['version'],
        applications=[dict(application_id=application,
                           invoice_expected_version=_invoice_version(books, invoice['id']))]),
        reason='Take the credit back off')
    assert undone['current']['available_minor_units'] == CREDITED
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 1200
    assert _settled(books, invoice['id']) == []
    assert _net(books) == before
    assert _stock(books, item, '2017-01-03') == ('1', HELD)

    again = _apply_credit(books, dict(credit, version=undone['version']), invoice['id'], GOODWILL)
    assert again['current']['applied_minor_units'] == CREDITED
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], CREDITED)]
    assert _net(books) == before
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_a_credit_settles_what_the_cash_left_of_a_stocked_invoice(books):
    """Cash first, then the credit: the credit may only reach what the payment did not take."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    receivable = invoice['revision']['profile']['control_account']['id']

    paid = run('payment receive', _receipt(books, invoice, '7', 'stocked-credit-cash', 1),
               reason='Part payment')
    cash = _cash(books, paid['id'])
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 500
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], 700)]

    credit = _credit(books)
    before = _net(books)
    applied = _apply_credit(books, credit, invoice['id'], GOODWILL)

    assert _net(books) == before == {asset: HELD, books['payable']: -1600,
                                     books['income']: -1200 + CREDITED, books['cogs']: COST,
                                     cash: 700}
    assert run('invoice settlement', dict(invoice=invoice['id']))['due_minor_units'] == 0
    assert applied['current']['available_minor_units'] == 0
    # Two settlements of the same line, the cash and the credit, both against its income row.
    assert _settled(books, invoice['id']) == [('net', receivable, books['income'], CREDITED),
                                              ('net', receivable, books['income'], 700)]
    assert {row[2] for row in _settled(books, invoice['id'])}.isdisjoint({asset, books['cogs']})
    assert _stock(books, item, '2017-01-03') == ('1', HELD)

    # And nothing is left for a second credit to reach.
    spare = _credit(books, name='Goodwill again')
    before = database(_path(books))
    with pytest.raises(BookflowError) as refused:
        _apply_credit(books, spare, invoice['id'], GOODWILL)
    assert refused.value.code == 'E_APPLICATION_CAPACITY' and database(_path(books)) == before
