"""The Items tab on a bill, checked by hand against the real report commands.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything.

    Expenses tab   Parts Bought          184.60
                   Freight In            100.00   -> 284.60
    Items tab      2 x Backflow Test      95.00   -> 190.00  (Subcontracted Labor)
                   12 x Ball Valve        12.35   -> 148.20  (Job Supplies)
                                                  -> 338.20
    Accounts Payable credited                        622.80

The correction below moves the valve line from twelve to fifteen, which is three more at
12.35, so Job Supplies gains 37.05 and Accounts Payable owes 37.05 more; nothing else moves.
The void reverses the whole thing at its own date and every account returns to zero.

Nothing here reads the code's own output back into an assertion about itself: the ledger is
read through ``report general-ledger``, the payable through ``report ap-aging`` and
``report unpaid-bills``, and the document through ``bill show``, and the three are required
to agree with the arithmetic above.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

# The two grids and their rows. Everything below is arithmetic on these.
FITTINGS = '184.60'
DELIVERY = '100.00'
EXPENSES = '284.60'      # 184.60 + 100.00
TEST_COST = '95.00'
TESTS = '190.00'         # 2 x 95.00
VALVE_COST = '12.35'
VALVES = '148.20'        # 12 x 12.35
ITEMS = '338.20'         # 190.00 + 148.20
BILL = '622.80'          # 284.60 + 338.20
MORE_VALVES = '185.25'   # 15 x 12.35
MORE_ITEMS = '375.25'    # 190.00 + 185.25
CORRECTED = '659.85'     # 284.60 + 375.25
DELTA = 3705             # 185.25 - 148.20, in minor units
SOLD_ONLY_COST = '250.00'  # a service that is sold and never bought, entered on a bill
SOLD_ONLY_MINOR = 25000


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'items'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Item lines organization')
    company = client.company.new(legal_name='Item lines', home_currency='USD', timezone='UTC',
                                 organization='Item lines organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    cogs = next(row['id'] for row in accounts if row['type'] == 'cost_of_goods_sold')
    parts = client.account.create(company=company, name='Parts Bought', type='expense')['id']
    freight = client.account.create(company=company, name='Freight In', type='expense')['id']
    subs = client.account.create(company=company, name='Subcontracted Labor', type='expense')['id']
    supplies = client.account.create(company=company, name='Job Supplies', type='expense')['id']
    terms = {row['name']: row['id'] for row in client.term.query(company=company, limit=50)['items']}
    methods = {row['name']: row['id'] for row in
               client.run('payment-method query', {'limit': 50}, company=company)['items']}
    vendor = client.vendor.create(company=company, name='Northside Supply',
                                  terms_id=terms['Net 30'])['id']
    customer = client.customer.create(company=company, name='Adams Plumbing')['id']
    tax_codes = {row['code']: row['id'] for row in
                 client.run('sales-tax-code query', {'limit': 50}, company=company)['items']}

    labor = client.item.create(company=company, name='Backflow Test', type='service',
                               sales_enabled=False, purchase_enabled=True,
                               purchase_description='Certified backflow test',
                               cost=TEST_COST, expense_account_id=subs)['id']
    valve = client.item.create(company=company, name='Ball Valve 1in', type='non_inventory_part',
                               sales_enabled=False, purchase_enabled=True,
                               purchase_description='1in brass ball valve',
                               cost=VALVE_COST, expense_account_id=supplies)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, bank=bank, income=income,
                cogs=cogs, parts=parts, freight=freight, subs=subs, supplies=supplies,
                vendor=vendor, customer=customer, terms=terms, methods=methods,
                tax_codes=tax_codes, labor=labor, valve=valve, run=run)


def _bill(books, **extra):
    return dict(vendor=books['vendor'], date='2017-03-03', supplier_reference='INV-7742',
                memo='March work',
                expenses=[{'account': books['parts'], 'amount': FITTINGS, 'memo': 'Fittings'},
                          {'account': books['freight'], 'amount': DELIVERY, 'memo': 'Delivery'}],
                items=[{'item': books['labor'], 'quantity': '2'},
                       {'item': books['valve'], 'quantity': '12', 'unit_cost': VALVE_COST,
                        'customer': books['customer'], 'billable': True}],
                **extra)


def _net(books, date_from='2017-01-01', date_to='2017-12-31'):
    """Signed minor units per account, debits positive, from the ledger's own posting rows."""
    rows = books['run']('report general-ledger',
                        {'date_from': date_from, 'date_to': date_to, 'limit': 200})['rows']
    net = {}
    for line in rows:
        if line['kind'] != 'posting':
            continue
        net[line['account_id']] = net.get(line['account_id'], 0) + (
            line['debit']['minor_units'] - line['credit']['minor_units'])
    return {account: value for account, value in net.items() if value}


def _inventory_part(books, name='Copper Elbow'):
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part',
        description='3/4in copper elbow', price='4.50',
        purchase_description='3/4in copper elbow', cost='1.80',
        cogs_account_id=books['cogs'], income_account_id=books['income'])['id']


def test_both_grids_debit_their_own_accounts_and_the_payable_is_credited_their_sum(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')

    assert posted['status'] == 'posted'
    assert posted['expense_total'] == {'amount': EXPENSES, 'currency': 'USD', 'minor_units': 28460}
    assert posted['item_total'] == {'amount': ITEMS, 'currency': 'USD', 'minor_units': 33820}
    assert posted['total'] == {'amount': BILL, 'currency': 'USD', 'minor_units': 62280}

    # 184.60 Parts Bought, 100.00 Freight In, 190.00 Subcontracted Labor, 148.20 Job Supplies,
    # and 622.80 owed. The four debits are 18460 + 10000 + 19000 + 14820 = 62280 minor units.
    assert _net(books) == {books['parts']: 18460, books['freight']: 10000,
                           books['subs']: 19000, books['supplies']: 14820,
                           books['payable']: -62280}

    batch = posted['revision']['batches'][0]
    assert (batch['kind'], batch['effective_date']) == ('original', '2017-03-03')
    assert batch['debit_total']['amount'] == batch['credit_total']['amount'] == BILL

    # An item line takes the item's own purchase account; nothing named one on the bill.
    first, second = posted['revision']['items']
    assert (first['item_id'], first['account_id']) == (books['labor'], books['subs'])
    assert first['quantity'] == '2' and first['unit_cost']['amount'] == TEST_COST
    assert first['amount'] == {'amount': TESTS, 'currency': 'USD', 'minor_units': 19000}
    assert first['description'] == 'Certified backflow test'
    assert first['line_snapshot']['item_type'] == 'service'
    assert first['line_snapshot']['amount_basis'] == 'unit_cost'
    assert (second['item_id'], second['account_id']) == (books['valve'], books['supplies'])
    assert second['amount'] == {'amount': VALVES, 'currency': 'USD', 'minor_units': 14820}
    assert second['billable'] is True and second['customer_id'] == books['customer']

    # Both families are on one purchase envelope, numbered straight through, expenses first.
    assert [line['position'] for line in posted['revision']['expenses']] == [1, 2]
    assert [line['position'] for line in posted['revision']['items']] == [3, 4]
    assert {line['kind'] for line in posted['revision']['expenses'] + posted['revision']['items']} == {'purchase'}

    # Accounts Payable is credited once for the whole bill, whichever grid a line came from.
    postings = [line for line in books['run'](
        'report general-ledger',
        {'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})['rows']
        if line['kind'] == 'posting']
    payable_legs = [line for line in postings if line['account_id'] == books['payable']]
    assert len(payable_legs) == 1 and payable_legs[0]['credit']['minor_units'] == 62280
    assert {line['party_name'] for line in postings} == {'Northside Supply'}


def test_the_payable_reports_and_bill_show_agree_with_the_grids(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')

    shown = books['run']('bill show', {'bill': posted['id']})
    assert shown['expense_total']['amount'] == EXPENSES
    assert shown['item_total']['amount'] == ITEMS
    assert shown['total']['amount'] == BILL
    assert shown['settlement_current']['open']['amount'] == BILL
    assert shown['settlement_current']['status'] == 'unpaid'
    assert [line['amount']['amount'] for line in shown['revision']['expenses']] == [FITTINGS, DELIVERY]
    assert [line['amount']['amount'] for line in shown['revision']['items']] == [TESTS, VALVES]

    # Net 30 from 2017-03-03 is 2017-04-02, so on 2017-04-15 the bill is thirteen days late.
    aging = books['run']('report ap-aging', {'as_of': '2017-04-15'})
    assert aging['totals']['days_1_30']['amount'] == BILL
    assert aging['totals']['total']['amount'] == BILL
    assert [row['display_vendor_label'] for row in aging['rows']] == ['Northside Supply']
    assert aging['rows'][0]['days_1_30']['amount'] == BILL

    unpaid = books['run']('report unpaid-bills', {'as_of': '2017-04-15'})
    assert unpaid['totals']['balance']['amount'] == BILL
    row = unpaid['rows'][0]
    assert (row['number'], row['due_date'], row['days_past_due']) == (posted['number'], '2017-04-02', 13)
    assert row['amount']['amount'] == BILL and row['applied']['minor_units'] == 0
    assert row['settlement_status'] == 'unpaid'

    # The obligation breaks down per entered line, both grids alike.
    components = posted['revision']['obligation']['components']
    assert sorted(row['amount_minor_units'] for row in components) == [10000, 14820, 18460, 19000]
    assert sum(row['amount_minor_units'] for row in components) == 62280


def test_correcting_one_item_line_moves_exactly_that_line(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    before = _net(books)
    identities = [line['line_id'] for line in posted['revision']['items']]

    corrected = books['run']('bill update', dict(
        bill=posted['id'], expected_version=posted['version'],
        items=[{'line_id': identities[0], 'item': books['labor'], 'quantity': '2'},
               {'line_id': identities[1], 'item': books['valve'], 'quantity': '15',
                'unit_cost': VALVE_COST, 'customer': books['customer'], 'billable': True}]),
        reason='Vendor shipped fifteen valves')

    assert corrected['expense_total']['amount'] == EXPENSES     # the other grid did not move
    assert corrected['item_total']['amount'] == MORE_ITEMS
    assert corrected['total']['amount'] == CORRECTED
    assert corrected['revision']['items'][1]['amount']['amount'] == MORE_VALVES

    after = _net(books)
    delta = {account: after.get(account, 0) - before.get(account, 0)
             for account in set(after) | set(before)}
    # Three more valves at 12.35 is 37.05. Job Supplies gains it and the payable owes it;
    # nothing else in the books moved at all.
    assert {account: value for account, value in delta.items() if value} == {
        books['supplies']: DELTA, books['payable']: -DELTA}

    assert books['run']('report ap-aging', {'as_of': '2017-04-15'}
                        )['totals']['total']['amount'] == CORRECTED
    assert books['run']('report unpaid-bills', {'as_of': '2017-04-15'}
                        )['totals']['balance']['amount'] == CORRECTED
    assert books['run']('bill show', {'bill': posted['id']}
                        )['settlement_current']['open']['amount'] == CORRECTED
    # The change report names the item line and the header total it moved, and nothing else.
    assert sorted(corrected['changed_fields']) == sorted([
        f'lines.{identities[1]}.amount_minor_units',
        f'lines.{identities[1]}.profile.quantity_microunits',
        'profile.item_total_minor_units'])

    # The superseded revision is still readable exactly as it was entered.
    first = books['run']('bill show', {'bill': posted['id'], 'revision_number': 1})
    assert first['revision']['items'][1]['amount']['amount'] == VALVES
    assert first['revision']['item_total']['amount'] == ITEMS


def test_voiding_returns_every_account_to_the_pre_post_snapshot(books):
    empty = _net(books)
    assert empty == {}
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    assert _net(books)

    voided = books['run']('bill void', dict(bill=posted['id'], expected_version=posted['version']),
                          reason='Entered against the wrong vendor')

    assert voided['status'] == 'voided'
    assert _net(books) == empty
    assert books['run']('report ap-aging', {'as_of': '2017-04-15'})['count'] == 0
    assert books['run']('report unpaid-bills', {'as_of': '2017-04-15'})['count'] == 0
    settlement = books['run']('bill show', {'bill': posted['id']})['settlement_current']
    assert (settlement['status'], settlement['open']['minor_units']) == ('voided', 0)
    # The lines are still readable on the voided document; only the accounting was reversed.
    assert len(books['run']('bill show', {'bill': posted['id']})['revision']['items']) == 2


def test_an_inventory_part_is_refused_by_name_and_nothing_is_written(books):
    stock = _inventory_part(books)

    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                       items=[{'item': stock, 'quantity': '50'}]),
                     reason='Receive stock')

    error = raised.value
    assert error.code == 'E_VALIDATION'
    assert error.details['reason'] == 'inventory_receipt_not_implemented'
    assert error.details['item_type'] == 'inventory_part'
    assert error.details['record_id'] == stock
    assert error.details['supported_item_types'] == ['service', 'non_inventory_part', 'other_charge']
    problem = error.details['fields'][0]
    assert problem['field'] == 'items.0.item'
    assert 'inventory part' in problem['problem'] and 'not implemented' in problem['problem']
    # Refused, not silently posted somewhere else: the books did not move.
    assert _net(books) == {}
    assert books['run']('bill query', {'limit': 10})['count'] == 0


def test_an_inventory_part_is_refused_on_a_correction_too(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    stock = _inventory_part(books)
    before = _net(books)

    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', dict(
            bill=posted['id'], expected_version=posted['version'],
            items=[{'item': stock, 'quantity': '50'}]), reason='Receive stock')

    assert raised.value.details['reason'] == 'inventory_receipt_not_implemented'
    assert _net(books) == before
    assert books['run']('bill show', {'bill': posted['id']})['total']['amount'] == BILL


def _sold_only(books, name='Design Review'):
    """A service that is sold and never bought: one account, and it is an income account."""
    return books['client'].item.create(
        company=books['company'], name=name, type='service',
        description='Design review', price='250.00', income_account_id=books['income'],
        sales_tax_code_id=next(iter(books['tax_codes'].values())))['id']


def test_an_item_with_no_purchase_side_posts_to_its_income_account_and_says_so(books):
    """The anchor product posts this line to the item's one account, so this does too.

    Buying back something you sell debits the income it is sold out of, which reduces that
    income rather than recording a cost. Refusing the workflow the anchor supports would be
    worse than the anchor; posting it without a word would be no better. It posts and warns.
    """
    sold_only = _sold_only(books)

    posted = books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                            items=[{'item': sold_only, 'quantity': '1',
                                                    'unit_cost': SOLD_ONLY_COST}]),
                          reason='Buy a design review')

    assert posted['total'] == {'amount': SOLD_ONLY_COST, 'currency': 'USD',
                               'minor_units': SOLD_ONLY_MINOR}
    line = posted['revision']['items'][0]
    assert line['account_id'] == books['income']
    assert line['line_snapshot']['account_basis'] == 'income'
    # 250.00 debited to the income account, 250.00 credited to Accounts Payable.
    assert _net(books) == {books['income']: SOLD_ONLY_MINOR, books['payable']: -SOLD_ONLY_MINOR}

    assert len(posted['warnings']) == 1, posted['warnings']
    said = posted['warnings'][0]
    assert 'Design Review' in said and 'no purchase account' in said, said
    assert 'income account' in said, said

    # The ledger agrees the debit landed in income, read back through the report rather than
    # through the write's own output.
    rows = books['run']('report general-ledger',
                        {'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})['rows']
    debited = [row for row in rows
               if row['kind'] == 'posting' and row['account_id'] == books['income']]
    assert len(debited) == 1 and debited[0]['debit']['amount'] == SOLD_ONLY_COST, debited


def test_an_income_account_line_is_corrected_and_voided_like_any_other(books):
    """The parity line is an ordinary bill line once it is written: it revises and reverses."""
    sold_only = _sold_only(books)
    posted = books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                            items=[{'item': sold_only, 'quantity': '1',
                                                    'unit_cost': SOLD_ONLY_COST}]),
                          reason='Buy a design review')

    corrected = books['run']('bill update', dict(bill=posted['id'],
                                                 expected_version=posted['version'],
                                                 items=[{'item': sold_only, 'quantity': '2',
                                                         'unit_cost': SOLD_ONLY_COST}]),
                             reason='Two of them, not one')

    assert corrected['total']['minor_units'] == SOLD_ONLY_MINOR * 2
    assert corrected['warnings'], corrected['warnings']
    assert _net(books) == {books['income']: SOLD_ONLY_MINOR * 2,
                           books['payable']: -SOLD_ONLY_MINOR * 2}

    books['run']('bill void', {'bill': posted['id']}, reason='Entered in error')
    assert _net(books) == {}


def test_a_sales_only_item_with_no_cost_still_asks_for_one(books):
    """The account is guessed from the item; the money never is."""
    sold_only = _sold_only(books, name='Unpriced Review')

    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                       items=[{'item': sold_only, 'quantity': '1'}]),
                     reason='Buy a design review')

    problem = raised.value.details['fields'][0]
    assert problem['field'] == 'items.0.unit_cost'
    assert 'no standard cost' in problem['problem']
    assert _net(books) == {}


def test_a_percentage_charge_is_refused(books):
    percent = books['client'].item.create(
        company=books['company'], name='Fuel Surcharge', type='other_charge',
        description='Fuel surcharge', income_account_id=books['income'],
        purchase_enabled=True, purchase_description='Fuel surcharge',
        expense_account_id=books['freight'], charge_percent='3.5',
        sales_tax_code_id=next(iter(books['tax_codes'].values())))['id']

    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                       items=[{'item': percent, 'quantity': '1'}]),
                     reason='Fuel surcharge')

    assert 'percentage charge' in raised.value.details['fields'][0]['problem']


def test_an_items_only_bill_ages_pays_and_closes_like_an_expense_only_one(books):
    """The seam the bill was built for: an item line is an obligation component, nothing more.

    Three tests at 95.00 is 285.00. It ages, it is listed unpaid, it takes a payment through
    the same command a purely typed bill takes, and afterwards Accounts Payable nets to zero
    with the cost standing in Subcontracted Labor and the money gone from Checking.
    """
    posted = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-04-01', supplier_reference='INV-8000',
        items=[{'item': books['labor'], 'quantity': '3'}]), reason='Three tests')

    assert posted['expense_total']['minor_units'] == 0
    assert posted['item_total'] == {'amount': '285.00', 'currency': 'USD', 'minor_units': 28500}
    assert posted['total']['minor_units'] == 28500
    assert _net(books) == {books['subs']: 28500, books['payable']: -28500}
    assert books['run']('report unpaid-bills', {'as_of': '2017-05-15'}
                        )['totals']['balance']['minor_units'] == 28500
    assert books['run']('report ap-aging', {'as_of': '2017-05-15'}
                        )['totals']['total']['minor_units'] == 28500

    books['run']('bill pay', dict(
        funding_account=books['bank'], method=books['methods']['Check'], date='2017-05-01',
        bills=[{'bill': posted['id']}]), reason='Pay the April bill')

    settled = books['run']('bill show', {'bill': posted['id']})['settlement_current']
    assert settled['applied']['minor_units'] == 28500
    assert settled['open']['minor_units'] == 0
    assert settled['status'] == 'paid'
    assert books['run']('report unpaid-bills', {'as_of': '2017-05-15'})['count'] == 0
    assert books['run']('report ap-aging', {'as_of': '2017-05-15'}
                        )['totals']['total']['minor_units'] == 0
    assert _net(books) == {books['subs']: 28500, books['bank']: -28500}


def test_a_partial_payment_lands_on_a_mixed_bill_exactly_as_on_a_typed_one(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')

    books['run']('bill pay', dict(
        funding_account=books['bank'], method=books['methods']['Check'], date='2017-04-10',
        bills=[{'bill': posted['id'], 'amount': '200.00'}]), reason='Pay part of it')

    settled = books['run']('bill show', {'bill': posted['id']})['settlement_current']
    assert settled['applied']['amount'] == '200.00'
    assert settled['open']['amount'] == '422.80'     # 622.80 - 200.00
    assert settled['status'] == 'partial'
    unpaid = books['run']('report unpaid-bills', {'as_of': '2017-04-15'})
    assert unpaid['rows'][0]['settlement_status'] == 'partly_paid'
    assert unpaid['totals']['balance']['amount'] == '422.80'
    assert books['run']('report ap-aging', {'as_of': '2017-04-15'}
                        )['totals']['total']['amount'] == '422.80'
    # And a paid bill refuses correction on both grids alike.
    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', dict(bill=posted['id'], items=[]), reason='Drop the items')
    assert raised.value.code == 'E_HAS_APPLICATIONS'


def test_supplying_one_grid_leaves_the_other_exactly_as_captured(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    item_ids = [line['line_id'] for line in posted['revision']['items']]

    # Replace the Expenses grid alone: the item lines keep their identities and their money.
    expenses_only = books['run']('bill update', dict(
        bill=posted['id'], expected_version=posted['version'],
        expenses=[{'account': books['parts'], 'amount': FITTINGS, 'memo': 'Fittings',
                   'line_id': posted['revision']['expenses'][0]['line_id']}]),
        reason='The delivery was billed separately')
    assert expenses_only['expense_total']['amount'] == FITTINGS
    assert expenses_only['item_total']['amount'] == ITEMS
    assert [line['line_id'] for line in expenses_only['revision']['items']] == item_ids
    assert expenses_only['total']['amount'] == '522.80'    # 184.60 + 338.20

    # A header-only correction keeps both grids.
    header_only = books['run']('bill update', dict(
        bill=posted['id'], expected_version=expenses_only['version'], memo='March work, revised'),
        reason='Fix the memo')
    assert header_only['expense_total']['amount'] == FITTINGS
    assert header_only['item_total']['amount'] == ITEMS
    assert [line['line_id'] for line in header_only['revision']['items']] == item_ids

    # An empty list clears that grid, and the bill stands on the other one alone.
    cleared = books['run']('bill update', dict(
        bill=posted['id'], expected_version=header_only['version'], items=[]),
        reason='The items went on a different bill')
    assert cleared['item_total']['minor_units'] == 0
    assert cleared['revision']['items'] == []
    assert cleared['total']['amount'] == FITTINGS
    assert _net(books) == {books['parts']: 18460, books['payable']: -18460}


def test_a_bill_cannot_be_left_with_nothing_on_either_grid(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    before = _net(books)

    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', dict(bill=posted['id'], expected_version=posted['version'],
                                         expenses=[], items=[]), reason='Empty it')
    assert raised.value.code == 'E_VALIDATION'
    assert _net(books) == before

    with pytest.raises(BookflowError):
        books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-03'),
                     reason='A bill with no lines')


def test_a_line_identity_cannot_cross_from_one_grid_to_the_other(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    expense_id = posted['revision']['expenses'][0]['line_id']

    with pytest.raises(BookflowError) as raised:
        books['run']('bill update', dict(
            bill=posted['id'], expected_version=posted['version'],
            items=[{'line_id': expense_id, 'item': books['labor'], 'quantity': '2'}]),
            reason='Move a line across the tabs')

    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'items.line_id'
    assert books['run']('bill show', {'bill': posted['id']})['total']['amount'] == BILL


def test_the_amount_is_derived_entered_or_taken_from_the_item_but_never_two_at_once(books):
    # Entered outright: the amount is what was typed and no unit cost is recorded.
    entered = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-05',
        items=[{'item': books['valve'], 'quantity': '12', 'amount': '150.00'}]),
        reason='The vendor billed a round number')
    line = entered['revision']['items'][0]
    assert line['amount']['amount'] == '150.00'
    assert line['unit_cost'] is None and line['unit_cost_minor_units'] is None
    assert line['line_snapshot']['amount_basis'] == 'amount'
    assert line['line_snapshot']['standard_cost_minor_units'] == 1235
    assert _net(books)[books['supplies']] == 15000

    # Both at once is refused before anything is resolved.
    with pytest.raises(BookflowError) as raised:
        books['run']('bill post', dict(
            vendor=books['vendor'], date='2017-03-06',
            items=[{'item': books['valve'], 'quantity': '1', 'unit_cost': VALVE_COST,
                    'amount': '12.35'}]), reason='Both bases')
    assert raised.value.code == 'E_VALIDATION'


def test_a_derived_amount_rounds_the_way_an_invoice_line_does(books):
    """0.333333 x 12.35 is 4.11666255, which rounds half to even to 4.12."""
    posted = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-07',
        items=[{'item': books['valve'], 'quantity': '0.333333'}]),
        reason='A third of a box')

    line = posted['revision']['items'][0]
    assert line['quantity'] == '0.333333'
    assert line['quantity_microunits'] == 333333
    assert line['unit_cost']['amount'] == VALVE_COST
    assert line['amount'] == {'amount': '4.12', 'currency': 'USD', 'minor_units': 412}
    assert posted['total']['minor_units'] == 412
    assert _net(books) == {books['supplies']: 412, books['payable']: -412}


def test_the_captured_account_is_what_the_bill_posted_however_the_item_changes_later(books):
    posted = books['run']('bill post', dict(
        vendor=books['vendor'], date='2017-03-08',
        items=[{'item': books['valve'], 'quantity': '4'}]), reason='Four valves')
    assert _net(books) == {books['supplies']: 4940, books['payable']: -4940}

    books['client'].item.update(company=books['company'], item=books['valve'],
                                expense_account_id=books['parts'], expected_version=1)

    shown = books['run']('bill show', {'bill': posted['id']})
    assert shown['revision']['items'][0]['account_id'] == books['supplies']
    assert shown['revision']['items'][0]['line_snapshot']['account']['full_name'] == 'Job Supplies'
    assert _net(books) == {books['supplies']: 4940, books['payable']: -4940}

    # A correction that leaves the Items grid alone keeps the captured account too.
    corrected = books['run']('bill update', dict(
        bill=posted['id'], expected_version=posted['version'], memo='Four valves, revised'),
        reason='Fix the memo')
    assert corrected['revision']['items'][0]['account_id'] == books['supplies']
    assert _net(books) == {books['supplies']: 4940, books['payable']: -4940}

    # Naming the row again re-resolves it, which is what replacing a grid means.
    replaced = books['run']('bill update', dict(
        bill=posted['id'], expected_version=corrected['version'],
        items=[{'line_id': corrected['revision']['items'][0]['line_id'],
                'item': books['valve'], 'quantity': '4'}]), reason='Re-enter the row')
    assert replaced['revision']['items'][0]['account_id'] == books['parts']
    assert _net(books) == {books['parts']: 4940, books['payable']: -4940}


def test_a_bill_with_item_lines_reads_back_through_history_and_query(books):
    posted = books['run']('bill post', _bill(books), reason='Enter the March bill')
    books['run']('bill update', dict(bill=posted['id'], expected_version=posted['version'],
                                     items=[{'item': books['labor'], 'quantity': '2'}]),
                 reason='Drop the valves')

    history = books['run']('bill history', {'bill': posted['id'], 'limit': 10})
    assert [row['revision_number'] for row in history['items']] == [1, 2]
    assert [row['item_total']['amount'] for row in history['items']] == [ITEMS, TESTS]
    assert [row['total']['amount'] for row in history['items']] == [BILL, '474.60']

    page = books['run']('bill query', {'limit': 10})
    assert page['items'][0]['item_total']['amount'] == TESTS
    assert page['items'][0]['expense_total']['amount'] == EXPENSES
    assert page['items'][0]['total']['amount'] == '474.60'      # 284.60 + 190.00
