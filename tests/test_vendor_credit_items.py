"""R178: a vendor credit with item rows, checked by hand against the real report commands.

Returning stock to a supplier is one document. The credit's Items tab is the bill's: an item, a
quantity and a cost, and the item decides the account. A stocked item credits Inventory Asset
at the amount the vendor credited and takes the quantity off the shelf, so the average cost of
what stays on it moves; any other item credits the account its purchase side names.

The worked example is the fake company's July (tests/fixtures/fakeco, answer/events.json):

    AFCI breakers, 30 June      18 on hand               741.24   (the old books)
    bill J11, 2 July           +12 at 42.10              505.20   -> 30 on hand, 1,246.44
    sold, 7 July                -4 at the average        166.19   -> 26 on hand, 1,080.25
    vendor credit J85, 28 July  -4 at the credited 42.10 168.40   -> 22 on hand,   911.85

911.85 is the key's July figure. Taking the four off at the average instead (4 x 1,080.25 / 26
= 166.19) would have left 914.06, which is what the old workaround did and what the anchor
product does not.

Every figure is written out in the module so a reader can add it up without running anything,
and the ledger and the stock reports are read through the real commands.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

OPENING = '741.24'       # 18 on hand at 30 June
REORDER = '505.20'       # 12 x 42.10
AFTER_BILL = '1246.44'   # 741.24 + 505.20, on hand 30
SOLD = '166.19'          # 4 x 1246.44 / 30 = 166.192
AFTER_SALE = '1080.25'   # 1246.44 - 166.19, on hand 26
CREDIT = '168.40'        # 4 x 42.10
AFTER_CREDIT = '911.85'  # 1080.25 - 168.40, on hand 22
AT_AVERAGE = '914.06'    # what taking four off at 1080.25 / 26 would have left


@pytest.fixture
def books(tmp_path, monkeypatch):
    data_root = tmp_path / 'vcitems'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Vendor credit items organization')
    company = client.company.new(legal_name='Credit items', home_currency='USD', timezone='UTC',
                                 organization='Vendor credit items organization',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    cogs = next(row['id'] for row in accounts if row['type'] == 'cost_of_goods_sold')
    inventory = next(row['id'] for row in accounts if row['full_name'] == 'Inventory Asset')
    shrink = client.account.create(company=company, name='Shrinkage', type='expense')['id']
    supplies = client.account.create(company=company, name='Job Supplies', type='expense')['id']
    freight = client.account.create(company=company, name='Freight In', type='expense')['id']
    terms = {row['name']: row['id'] for row in client.term.query(company=company, limit=50)['items']}
    vendor = client.vendor.create(company=company, name='Midland Electric Supply',
                                  terms_id=terms['Net 30'])['id']
    customer = client.customer.create(company=company, name='Adams Plumbing')['id']
    breaker = client.item.create(
        company=company, name='Breaker 20A AFCI', type='inventory_part', description='20A AFCI breaker',
        price='64.00', purchase_description='20A AFCI breaker', cost='42.10',
        cogs_account_id=cogs, income_account_id=income)['id']
    valve = client.item.create(company=company, name='Ball Valve 1in', type='non_inventory_part',
                               sales_enabled=False, purchase_enabled=True,
                               purchase_description='1in brass ball valve', cost='12.35',
                               expense_account_id=supplies)['id']
    labor = client.item.create(company=company, name='Backflow Test', type='service',
                               sales_enabled=False, purchase_enabled=True,
                               purchase_description='Certified backflow test', cost='95.00',
                               expense_account_id=supplies)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, payable=payable, income=income, cogs=cogs,
                inventory=inventory, shrink=shrink, supplies=supplies, freight=freight,
                vendor=vendor, customer=customer, breaker=breaker, valve=valve, labor=labor, run=run)


def _stock_july(books):
    """The fake company's AFCI breaker through 7 July: 26 on hand worth 1,080.25."""
    run, item, vendor = books['run'], books['breaker'], books['vendor']
    run('bill post', dict(vendor=vendor, date='2026-06-30', supplier_reference='OPENING',
                          items=[{'item': item, 'quantity': '18', 'amount': OPENING}]),
        reason='Opening stock')
    run('bill post', dict(vendor=vendor, date='2026-07-02', supplier_reference='S1189377.001',
                          items=[{'item': item, 'quantity': '12', 'unit_cost': '42.10'}]),
        reason='Stock order')
    run('inventory adjust', dict(item=item, date='2026-07-07', quantity_change='-4',
                                 adjustment_account=books['shrink'], memo='Sold to Kowalski'))


def _stock(books, as_of='2026-12-31', item=None):
    rows = books['run']('report stock-status', {'as_of': as_of, 'limit': 50})['rows']
    row = next(r for r in rows if r['item_id'] == (item or books['breaker']))
    return row['quantity_on_hand'], row['asset_value']['amount'], row['average_cost']['amount']


def _net(books, date_to='2026-12-31'):
    """Signed minor units per account, debits positive, from the ledger's own posting rows."""
    net = {}
    cursor = None
    while True:
        page = books['run']('report general-ledger', {'date_from': '2026-01-01', 'date_to': date_to,
                                                      'limit': 200, **({'cursor': cursor} if cursor else {})})
        for line in page['rows']:
            if line['kind'] == 'posting':
                net[line['account_id']] = net.get(line['account_id'], 0) + (
                    line['debit']['minor_units'] - line['credit']['minor_units'])
        cursor = page.get('next_cursor')
        if not cursor:
            return {account: value for account, value in net.items() if value}


def _return(books, **extra):
    row = dict(vendor=books['vendor'], date='2026-07-28', supplier_reference='CM S1191012',
               items=[{'item': books['breaker'], 'quantity': '4', 'unit_cost': '42.10'}])
    row.update(extra)
    return row


def test_the_july_return_is_one_document_that_takes_the_items_out_at_the_credited_cost(books):
    _stock_july(books)
    assert _stock(books, '2026-07-27')[:2] == ('26', AFTER_SALE)

    credit = books['run']('vendor-credit post', _return(books), reason='Wrong style, sent back')

    # One document, and it is worth exactly what the vendor credited.
    assert credit['total']['amount'] == CREDIT
    assert credit['expense_total']['minor_units'] == 0
    assert credit['item_total']['amount'] == CREDIT
    row = credit['revision']['items'][0]
    assert (row['item_id'], row['quantity'], row['account_id']) == (books['breaker'], '4', books['inventory'])
    assert row['unit_cost']['amount'] == '42.10' and row['amount']['amount'] == CREDIT
    assert row['line_snapshot']['account_basis'] == 'asset'
    assert credit['revision']['expenses'] == []

    # 22 on hand worth 911.85: the key's July figure, not 914.06, and the average moved from
    # 1080.25 / 26 = 41.55 to 911.85 / 22 = 41.45 because the credit was at 42.10.
    assert _stock(books) == ('22', AFTER_CREDIT, '41.45')
    assert _stock(books)[1] != AT_AVERAGE

    # Inventory Asset: 741.24 + 505.20 - 166.19 - 168.40 = 911.85, the same figure the stock
    # report prints. Accounts Payable owes 741.24 + 505.20 less the 168.40 credit. No clearing
    # account was needed and no adjustment other than the sale was written.
    assert _net(books) == {books['inventory']: 91185, books['shrink']: 16619,
                           books['payable']: -(74124 + 50520 - 16840)}


def test_the_credit_reads_back_whole_and_applies_to_a_bill_as_before(books):
    _stock_july(books)
    credit = books['run']('vendor-credit post', _return(books), reason='Sent back')

    shown = books['run']('vendor-credit show', {'credit': credit['id']})
    assert shown['revision']['items'][0]['quantity'] == '4'
    assert shown['settlement_current']['unapplied']['amount'] == CREDIT
    assert [row['id'] for row in books['run']('vendor-credit query', {})['items']] == [credit['id']]
    # The credit is a source, never a payable, so it is on no unpaid list.
    assert books['run']('report unpaid-bills', {'as_of': '2026-07-31'})['count'] == 2

    bill = books['run']('bill post', dict(vendor=books['vendor'], date='2026-08-03',
                                          expenses=[{'account': books['freight'], 'amount': '200.00'}]),
                        reason='Freight')
    books['run']('vendor-credit apply', dict(credit=credit['id'], expected_version=credit['version'],
                                             bills=[{'bill': bill['id'], 'amount': CREDIT}],
                                             date='2026-08-04'))
    after = books['run']('bill show', {'bill': bill['id']})['settlement_current']
    assert after['open']['amount'] == '31.60'   # 200.00 - 168.40
    # Settling moved no stock and no account.
    assert _stock(books)[:2] == ('22', AFTER_CREDIT)


def test_a_non_stocked_item_credits_its_expense_account_beside_an_expenses_row(books):
    credit = books['run']('vendor-credit post', dict(
        vendor=books['vendor'], date='2026-07-10',
        expenses=[{'account': books['freight'], 'amount': '10.00', 'memo': 'Freight refunded'}],
        items=[{'item': books['valve'], 'quantity': '2', 'customer': books['customer'], 'billable': True},
               {'item': books['labor'], 'amount': '95.00', 'description': 'Test not performed'}]),
        reason='Credit note')

    # 10.00 + 2 x 12.35 (24.70, the item's standard cost) + 95.00 = 129.70
    assert (credit['expense_total']['amount'], credit['item_total']['amount'],
            credit['total']['amount']) == ('10.00', '119.70', '129.70')
    valve, labor = credit['revision']['items']
    assert (valve['account_id'], valve['amount']['amount']) == (books['supplies'], '24.70')
    assert valve['billable'] is True and valve['customer_id'] == books['customer']
    assert (labor['unit_cost'], labor['amount']['amount'], labor['description']) == (
        None, '95.00', 'Test not performed')
    # Expenses first, then items, numbered straight through on one envelope family.
    assert [r['position'] for r in credit['revision']['expenses']] == [1]
    assert [r['position'] for r in credit['revision']['items']] == [2, 3]

    # Job Supplies is credited 24.70 + 95.00; Freight In 10.00; Payable debited 129.70 once.
    assert _net(books) == {books['supplies']: -11970, books['freight']: -1000, books['payable']: 12970}
    # Nothing moved stock: these items hold none.
    assert books['run']('report stock-status', {'as_of': '2026-12-31', 'limit': 50})['totals'][
        'asset_value']['minor_units'] == 0


def test_an_item_credit_nets_against_what_was_bought_in_purchases_by_item_and_vendor(books):
    _stock_july(books)
    books['run']('vendor-credit post', _return(books), reason='Sent back')

    by_item = books['run']('report purchases-by-item',
                           {'date_from': '2026-07-01', 'date_to': '2026-07-31', 'limit': 50})
    row = next(r for r in by_item['rows'] if r['item_id'] == books['breaker'])
    # Bought 12 for 505.20 in July, 4 went back for 168.40.
    assert (row['quantity'], row['amount']['amount']) == ('8', '336.80')
    by_vendor = books['run']('report purchases-by-vendor',
                             {'date_from': '2026-07-01', 'date_to': '2026-07-31', 'limit': 50})
    assert by_vendor['rows'][0]['amount']['amount'] == '336.80'


def test_sending_back_more_than_is_on_hand_is_refused_and_writes_nothing(books):
    _stock_july(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('vendor-credit post',
                     _return(books, items=[{'item': books['breaker'], 'quantity': '27', 'unit_cost': '42.10'}]),
                     reason='Too many')
    assert caught.value.code == 'E_VALIDATION'
    assert 'Breaker 20A AFCI' in caught.value.message and 'only 26 on hand' in caught.value.message
    assert caught.value.details['fields'][0]['field'] == 'items'
    assert books['run']('vendor-credit query', {})['items'] == []
    assert _stock(books)[:2] == ('26', AFTER_SALE)


def test_sending_back_all_the_stock_must_credit_exactly_what_it_is_worth(books):
    _stock_july(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('vendor-credit post',
                     _return(books, items=[{'item': books['breaker'], 'quantity': '26', 'amount': '1000.00'}]),
                     reason='All of it')
    assert caught.value.code == 'E_VALIDATION'
    assert 'exactly what that stock is worth, 1080.25 USD' in caught.value.message
    # At exactly the value on hand it stands, and the shelf is empty.
    credit = books['run']('vendor-credit post',
                          _return(books, items=[{'item': books['breaker'], 'quantity': '26', 'amount': AFTER_SALE}]),
                          reason='All of it')
    assert credit['total']['amount'] == AFTER_SALE
    assert _stock(books)[:2] == ('0', '0.00')


def test_a_correction_moves_the_stock_it_took_and_a_void_puts_it_all_back(books):
    _stock_july(books)
    credit = books['run']('vendor-credit post', _return(books), reason='Sent back')

    # Five went back, not four: 5 x 42.10 = 210.50, so 21 on hand worth 1080.25 - 210.50.
    corrected = books['run']('vendor-credit update', dict(
        credit=credit['id'], expected_version=credit['version'],
        items=[{'item': books['breaker'], 'quantity': '5', 'unit_cost': '42.10'}]),
        reason='Five, not four')
    assert corrected['total']['amount'] == '210.50'
    assert corrected['changed_fields']
    assert _stock(books)[:2] == ('21', '869.75')
    assert _net(books)[books['inventory']] == 86975

    # Correcting the date alone keeps the item row and the stock exactly as they were.
    redated = books['run']('vendor-credit update', dict(
        credit=credit['id'], expected_version=corrected['version'], memo='Wrong style'),
        reason='Add the memo')
    assert redated['revision']['items'][0]['quantity'] == '5'
    assert redated['revision']['items'][0]['line_id'] == corrected['revision']['items'][0]['line_id']
    assert _stock(books)[:2] == ('21', '869.75')

    books['run']('vendor-credit void', dict(credit=credit['id'], expected_version=redated['version']),
                 reason='Vendor refused the return')
    assert _stock(books)[:2] == ('26', AFTER_SALE)
    assert _net(books) == {books['inventory']: 108025, books['shrink']: 16619,
                           books['payable']: -(74124 + 50520)}
    assert len(books['run']('vendor-credit show', {'credit': credit['id']})['revision']['items']) == 1


def test_the_next_sale_costs_at_the_average_the_return_left_behind(books):
    _stock_july(books)
    books['run']('vendor-credit post', _return(books), reason='Sent back')
    # 22 on hand worth 911.85: two more out of it take 2 x 911.85 / 22 = 82.8954, which is 82.90.
    books['run']('inventory adjust', dict(item=books['breaker'], date='2026-08-02', quantity_change='-2',
                                          adjustment_account=books['shrink'], memo='Sold'))
    assert _stock(books) == ('20', '828.95', '41.45')


def test_a_return_dated_before_a_sale_recosts_the_sale_at_its_own_date(books):
    _stock_july(books)
    # The sale on 7 July took 4 at 1246.44 / 30 = 41.55 (166.19). A credit dated 3 July for four
    # at 42.10 leaves 26 worth 1078.04 before the sale, so the sale takes 4 x 1078.04 / 26 =
    # 165.85 instead and is owed 0.34 back, written as its own recost entry dated 7 July.
    books['run']('vendor-credit post', _return(books, date='2026-07-03'), reason='Sent back')

    assert _stock(books, '2026-07-06')[:2] == ('26', '1078.04')
    assert _stock(books)[:2] == ('22', '912.19')    # 1078.04 - 165.85
    entries = books['run']('report general-ledger',
                           {'date_from': '2026-07-07', 'date_to': '2026-07-07', 'limit': 50})['rows']
    assert any('Weighted-average cost correction' in (line.get('memo') or line.get('description') or '')
               for line in entries if line['kind'] == 'posting')


def test_voiding_a_return_that_sits_before_a_sale_recosts_that_sale_back(books):
    _stock_july(books)
    credit = books['run']('vendor-credit post', _return(books, date='2026-07-03'), reason='Sent back')
    books['run']('vendor-credit void', dict(credit=credit['id'], expected_version=credit['version']),
                 reason='Refused')
    assert _stock(books)[:2] == ('26', AFTER_SALE)
    assert _net(books) == {books['inventory']: 108025, books['shrink']: 16619,
                           books['payable']: -(74124 + 50520)}


def test_a_credit_needs_a_row_and_an_item_row_obeys_the_bills_rules(books):
    with pytest.raises(Exception) as caught:
        books['run']('vendor-credit post', dict(vendor=books['vendor'], date='2026-07-10', items=[]),
                     reason='Nothing')
    assert caught.value.code == 'E_VALIDATION' and 'at least one' in str(caught.value.details)
    with pytest.raises(Exception) as caught:
        books['run']('vendor-credit post', dict(vendor=books['vendor'], date='2026-07-10',
                                                items=[{'item': books['valve'], 'billable': True}]),
                     reason='Billable to nobody')
    assert 'billable requires a customer' in str(caught.value.details)
    with pytest.raises(BookflowError) as caught:
        books['run']('vendor-credit post', dict(vendor=books['vendor'], date='2026-07-10',
                                                items=[{'item': books['valve'], 'quantity': '1', 'amount': '0.00'}]),
                     reason='Worth nothing')
    assert caught.value.code == 'E_VALIDATION'
    # A row cannot name a line that is not already on the credit.
    with pytest.raises(Exception):
        books['run']('vendor-credit post', dict(vendor=books['vendor'], date='2026-07-10',
                                                items=[{'item': books['valve'], 'line_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}]),
                     reason='Borrowed identity')


def test_an_item_sold_but_never_bought_credits_its_income_account_and_says_so(books):
    sold_only = books['client'].item.create(
        company=books['company'], name='Design Review', type='service', description='Design review',
        price='250.00', income_account_id=books['income'],
        sales_tax_code_id=next(iter({row['code']: row['id'] for row in books['client'].run(
            'sales-tax-code query', {'limit': 50}, company=books['company'])['items']}.values())))['id']
    credit = books['run']('vendor-credit post', dict(
        vendor=books['vendor'], date='2026-07-10', items=[{'item': sold_only, 'amount': '250.00'}]),
        reason='Refund of a buyback')
    assert any('Design Review' in warning and 'raises that income' in warning for warning in credit['warnings'])
    assert _net(books) == {books['income']: -25000, books['payable']: 25000}


def test_correcting_the_expenses_tab_keeps_the_items_tab_and_the_stock(books):
    _stock_july(books)
    credit = books['run']('vendor-credit post', _return(
        books, expenses=[{'account': books['freight'], 'amount': '10.00'}]), reason='Sent back')
    assert credit['total']['amount'] == '178.40'
    corrected = books['run']('vendor-credit update', dict(
        credit=credit['id'], expected_version=credit['version'],
        expenses=[{'account': books['freight'], 'amount': '15.00'}]), reason='Freight was 15')
    assert corrected['total']['amount'] == '183.40'
    assert corrected['revision']['items'][0]['quantity'] == '4'
    assert _stock(books) == ('22', AFTER_CREDIT, '41.45')
    history = books['run']('vendor-credit history', {'credit': credit['id']})
    assert history['count'] == 2 and history['items'][1]['item_total']['amount'] == CREDIT
