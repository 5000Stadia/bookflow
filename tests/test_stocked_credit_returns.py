"""A credit memo that returns a stocked invoice line puts the money and the goods back.

One document. The customer's account is credited and the quantity is on the shelf again at what
it cost, in the same write, and the books are the books they were before the sale:

    Bought   2 x 8.00    ->  16.00 into Inventory Asset, 16.00 owed to the supplier
    Sold     1 x 12.00   ->  12.00 receivable, 12.00 income, 8.00 cost, 8.00 left in stock
    Returned that line   ->  12.00 off the receivable, 12.00 out of income, 8.00 out of cost of
                             goods sold and back into Inventory Asset -- two units on hand
                             worth 16.00, exactly where the purchase left them

The cost that comes back is the one the *sale* took out, read off that invoice line's own issue
movement, never the price on the credit and never what the item is worth today. A line returned
in pieces gives its cost back in pieces that add up to exactly what went out, by the endpoint
rule in ``credit_returns`` that already decides the net and every tax cent.

What is still refused is a credited line that *names* a stock item instead of returning one: a
price is not a cost, and nothing in Bookflow derives what an unlinked quantity coming in is
worth -- every receipt in the stock ledger states its value, because somebody knew it.

Nothing here reads the code's own output back into an assertion about itself: every figure is
computed by hand in the test, the ledger is read through ``report general-ledger``, the stock
through ``report stock-status``, and "wrote nothing" is the whole company database compared row
for row and value for value.
"""
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.commands import credit_memo_cmds
from bookflow.company import credits
from bookflow.company.items import TRACKED_TYPES
from bookflow.company.sales_facts import SELLABLE_ITEM_TYPES, SalesLineProfile
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net  # noqa: F401
from tests.payment_raw_evidence import database

BOUGHT = '8'        # each, two of them
SOLD = '12'         # what the customer is invoiced, each
STOCK = 1600        # what the two units cost
COST = 800          # what one sold unit takes out of inventory
HELD = 800          # what the unsold unit is still worth
SALE = 1200         # what the customer is invoiced for one unit


def _path(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def _stock(books, item, date):
    rows = books['run']('report stock-status', dict(as_of=date, limit=200))['rows']
    row = next(row for row in rows if row['item_id'] == item)
    return row['quantity_on_hand'], row['asset_value']['minor_units']


def _receivable(books):
    return next(row['id'] for row in books['client'].account.query(
        company=books['company'], limit=200)['items'] if row['type'] == 'accounts_receivable')


def _stocked_invoice(books, item, quantity='1'):
    """Buy two units, sell some of them, exactly as the module header writes it out."""
    run = books['run']
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost=BOUGHT)]), reason='Receive stock')
    return run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity=quantity, unit_price=SOLD)]), reason='Sell stock')


def _service(books, name='Goodwill credit'):
    """A sold item that carries no stock: what the refusal tells the person to reach for."""
    exempt = next(row['id'] for row in books['run'](
        'sales-tax-code query', {'limit': 50})['items'] if not row['taxable'])
    return books['client'].item.create(
        company=books['company'], name=name, type='service', sales_enabled=True,
        description='Credit for a returned part', price=SOLD,
        sales_tax_code_id=exempt, income_account_id=books['income'])['id']


def _sold_line(invoice):
    return invoice['revision']['lines'][0]['line_id']


def _return(books, invoice, quantity='1', date='2017-01-03', reason='Take the unit back'):
    return books['run']('credit-memo post', dict(customer=books['customer'], date=date,
        lines=[dict(source_invoice=invoice['id'], source_line=_sold_line(invoice),
                    quantity=quantity)]), reason=reason)


def _query(books, sql, *parameters):
    """Rows read straight out of the company file, so no product summary is between us."""
    import sqlite3
    with sqlite3.connect(_path(books)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, parameters)]


def _movements(books, item):
    """The stock ledger's own rows for one item, in replay order."""
    return _query(books, 'SELECT * FROM inventory_movements WHERE item_id = ? '
                         'ORDER BY effective_date, sequence, id', item)


def _legs(books, transaction_id):
    """The posting legs one document wrote, in the order it wrote them."""
    return _query(books, 'SELECT * FROM posting_lines WHERE transaction_id = ? '
                         'ORDER BY batch_id, line_no', transaction_id)


def _shape(rows):
    return [(row['kind'], row['quantity_microunits'], row['value_minor_units']) for row in rows]


def _legacy_return(books, monkeypatch, invoice, reason):
    """Post a stocked return with the stock half lifted, then put it back.

    The rows this leaves are the rows the shipped code wrote, because the shipped code moved no
    stock at all -- which is the only way to test what a company that already upgraded holds.
    Both halves come off: the entries the write would plan, and the independent check that
    requires a returned stock line to have moved some. Nothing else is touched, so every path
    exercised afterwards is the real one running against genuinely stock-free rows.
    """
    from bookflow.company import credit_validation
    entries, check = credits._stock_entries, credit_validation._movements
    monkeypatch.setattr(credits, '_stock_entries', lambda s, lines: [])
    monkeypatch.setattr(credit_validation, '_movements', lambda s, data: None)
    stored = _return(books, invoice, reason=reason)
    monkeypatch.setattr(credits, '_stock_entries', entries)
    monkeypatch.setattr(credit_validation, '_movements', check)
    return stored


def _captured(item_type, *, accounts=True):
    """A captured line profile of one item family, as a revision would hold it."""
    def account(name, kind):
        return dict(id='01' + name.upper(), name=name, full_name=name, number=None,
                    type=kind, normal_balance='debit')
    return SalesLineProfile(
        item=dict(id='01ITEM', label='Thing', version=1), item_type=item_type,
        income_account=dict(id='01ACCT', name='Income', full_name='Income', number=None,
                            type='income', normal_balance='credit'),
        **(dict(asset_account=account('Asset', 'other_current_asset'),
                cogs_account=account('Cogs', 'cost_of_goods_sold')) if accounts else {}))


_SOURCE = dict(transaction_id='01INV', revision_id='01REV', document_line_id='01LINE',
               line_id='01IDENT', base_quantity_microunits=1_000_000, net_minor_units=SALE)


# ------------------------------------------------------------------ which lines are refused


def test_a_stock_family_is_refused_when_named_and_allowed_when_returned():
    """Written against the item registry, so a stock-carrying family added later is covered.

    Both halves matter: naming a stock item is refused because a price is not a cost, and
    returning one is not, because the invoice line it names already says what the cost was.
    """
    assert set(TRACKED_TYPES) and set(TRACKED_TYPES) < set(SELLABLE_ITEM_TYPES)
    for item_type in SELLABLE_ITEM_TYPES:
        if item_type in TRACKED_TYPES:
            with pytest.raises(BookflowError) as caught:
                credits._refuse_stocked(_captured(item_type), 'lines.0.item')
            assert caught.value.code == 'E_VALIDATION'
            assert caught.value.details['reason'] == 'unlinked_stocked_credit_unsupported'
            assert caught.value.details['item_type'] == item_type
            assert item_type.replace('_', ' ') in caught.value.message
        else:
            assert credits._refuse_stocked(_captured(item_type), 'lines.0.item') is None
        # Returned: allowed for every family, stock-carrying or not.
        assert credits._refuse_stocked(
            _captured(item_type), 'lines.0.source_line', _SOURCE) is None


def test_a_returned_line_whose_capture_names_no_stock_accounts_is_still_refused():
    """A revision captured before the two accounts were captured has nowhere to put the cost."""
    for item_type in TRACKED_TYPES:
        with pytest.raises(BookflowError) as caught:
            credits._refuse_stocked(_captured(item_type, accounts=False),
                                    'lines.0.source_line', _SOURCE)
        assert caught.value.details['reason'] == 'stocked_capture_incomplete'
        assert caught.value.details['fields'] == [
            {'field': 'lines.0.source_line', 'problem': 'this capture names no stock accounts'}]


# ------------------------------------------------------------------ the arithmetic witness


def test_returning_a_stocked_invoice_line_puts_the_money_and_the_goods_back(books):
    """The whole ask, in one document, against figures computed here and not by the product.

    Bought 2 at 8.00, sold 1 at 12.00, returned that 1. Every number below is arithmetic done
    in this test: two units cost 2 x 800 = 1600 minor units; the weighted average of the one
    unit that left is 1600 / 2 = 800; returning it puts 800 back and leaves 1600 on the shelf,
    which is where the purchase left it. Nothing is read off the credit to assert about it.
    """
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)

    # Where the sale left the books: 12.00 owed, 12.00 earned, 8.00 of cost, 8.00 of stock.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-02') == ('1', HELD)

    credit = _return(books, invoice)

    assert credit['status'] == 'posted'
    assert credit['total']['minor_units'] == SALE
    # Exactly where the books stood before the sale: the goods bought and not yet sold, and the
    # supplier owed for them. No receivable, no income, no cost of goods sold.
    assert _net(books) == {asset: STOCK, books['payable']: -STOCK}
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)

    # And the pair the credit posted, leg for leg: 8.00 into the asset, 8.00 out of cost, with
    # the receivable still credited the whole gross and the income still debited it.
    assert {leg['account_id']: (leg['debit_minor_units'], leg['credit_minor_units'])
            for leg in _legs(books, credit['id'])} == {
        asset: (COST, 0), books['cogs']: (0, COST),
        books['income']: (SALE, 0), receivable: (0, SALE)}

    # One receipt movement, at the cost the issue took out, tied to the leg that carries it.
    rows = _movements(books, item)
    assert _shape(rows) == [('receipt', 2_000_000, STOCK), ('issue', -1_000_000, -COST),
                            ('receipt', 1_000_000, COST)]
    assert rows[2]['transaction_id'] == credit['id'] and rows[2]['posting_line_id'] is not None


def test_the_returned_cost_is_the_sale_s_own_and_not_what_the_item_is_worth_now(books):
    """Buy cheap, sell, buy dear, then return: the return gives back what the sale took.

    Two at 8.00 and then one at 20.00 makes the shelf worth (800 + 2000) / 2 = 1400 a unit on
    average, but the unit that left cost 800 and 800 is what comes back. So the three units on
    hand afterwards are worth 1600 + 2000 = 3600, never 800 + 2000 + 1400.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-04',
        items=[dict(item=item, quantity='1', unit_cost='20')]), reason='Receive dearer stock')
    assert _stock(books, item, '2017-01-04') == ('2', HELD + 2000)

    _return(books, invoice, date='2017-01-05')

    assert _stock(books, item, '2017-01-05') == ('3', STOCK + 2000)
    assert _net(books) == {asset: STOCK + 2000, books['payable']: -(STOCK + 2000)}


def test_returning_a_line_in_pieces_gives_back_exactly_what_it_took(books):
    """Three units costing 1000 in total: 333, 333 and 334 come back, never 999.

    Bought 2 at 3.00 and 1 at 4.00, so three units cost 600 + 400 = 1000 and selling all three
    takes 1000 out. The endpoint rule owns 1000*1//3 = 333 for the first unit returned,
    1000*2//3 - 1000*1//3 = 666 - 333 = 333 for the second and 1000 - 666 = 334 for the third.
    The three add to 1000: no cent is lost and none is claimed twice.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='3')]), reason='Receive stock')
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='1', unit_cost='4')]), reason='Receive dearer stock')
    invoice = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='3', unit_price=SOLD)]), reason='Sell all three')
    assert _net(books)[books['cogs']] == 1000
    assert _stock(books, item, '2017-01-02') == ('0', 0)

    _return(books, invoice, date='2017-01-03', reason='One back')
    assert _stock(books, item, '2017-01-03') == ('1', 333)
    assert _net(books)[books['cogs']] == 1000 - 333

    _return(books, invoice, date='2017-01-04', reason='A second back')
    assert _stock(books, item, '2017-01-04') == ('2', 333 + 333)
    assert _net(books)[books['cogs']] == 1000 - 666

    _return(books, invoice, date='2017-01-05', reason='The last one back')
    # 334 for the last, so the three shares are exactly the 1000 the sale took out.
    assert _stock(books, item, '2017-01-05') == ('3', 1000)
    assert _net(books) == {asset: 1000, books['payable']: -1000}


def test_two_units_returned_on_one_line_come_back_together(books):
    """The case as it was asked: two elbows bought, two sold, both brought back on one line."""
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item, quantity='2')
    assert _net(books)[books['cogs']] == STOCK

    credit = _return(books, invoice, quantity='2', reason='Both elbows back')

    assert credit['total']['minor_units'] == 2 * SALE
    assert _net(books) == {asset: STOCK, books['payable']: -STOCK}
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)


# ------------------------------------------------------------------ voiding and correcting


def test_voiding_the_credit_takes_the_stock_back_out_exactly(books):
    """A void undoes the whole document, the goods included, leaving the sale standing."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)
    credit = _return(books, invoice)
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)

    voided = run('credit-memo void', dict(credit_memo=credit['id'], expected_version=1),
                 reason='The customer changed their mind')

    assert voided['status'] == 'voided'
    # Exactly where the sale left them, to the cent and to the unit.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    rows = _movements(books, item)
    assert _shape(rows) == [('receipt', 2_000_000, STOCK), ('issue', -1_000_000, -COST),
                            ('receipt', 1_000_000, COST), ('reversal', -1_000_000, -COST)]
    assert rows[3]['reverses_movement_id'] == rows[2]['id']
    # The source line is whole again and can be returned a second time.
    assert _return(books, invoice, date='2017-01-04', reason='Back again')['status'] == 'posted'
    assert _stock(books, item, '2017-01-04') == ('2', STOCK)


def test_voiding_a_return_whose_goods_were_sold_on_is_refused_and_writes_nothing(books):
    """Stock cannot come back out of a shelf that no longer holds it, so nothing is written."""
    run = books['run']
    item = _inventory_part(books)
    invoice = _stocked_invoice(books, item)
    credit = _return(books, invoice)
    run('invoice post', dict(customer=books['customer'], date='2017-01-04',
        lines=[dict(item=item, quantity='2', unit_price=SOLD)]), reason='Sell both on')
    assert _stock(books, item, '2017-01-04') == ('0', 0)

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo void', dict(credit_memo=credit['id'], expected_version=1),
            reason='Try to take it back')

    assert caught.value.code == 'E_VALIDATION'
    assert database(_path(books)) == before
    assert run('credit-memo show', dict(credit_memo=credit['id']))['status'] == 'posted'


def test_correcting_a_stocked_return_retires_its_receipt_and_takes_the_new_quantity(books):
    """Two back, then corrected to one: the ledger holds one live receipt, for one unit."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item, quantity='2')
    credit = _return(books, invoice, quantity='2', reason='Both back')
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)

    corrected = run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1,
        lines=[dict(source_invoice=invoice['id'], source_line=_sold_line(invoice),
                    quantity='1')]), reason='Only one came back')

    assert corrected['changed'] is True and corrected['version'] == 2
    assert corrected['total']['minor_units'] == SALE
    # One unit back at its 800, one still sold: 12.00 still owed and 8.00 still a cost.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert _shape(_movements(books, item)) == [
        ('receipt', 2_000_000, STOCK), ('issue', -2_000_000, -STOCK),
        ('receipt', 2_000_000, STOCK), ('reversal', -2_000_000, -STOCK),
        ('receipt', 1_000_000, COST)]


def test_an_omitted_grid_correction_of_a_stocked_return_moves_it_whole(books):
    """The chokepoint still judges the resolved grid; what it now allows, it allows wholly.

    ``credit-memo update`` with no ``lines`` retains the captured grid and posts the document
    again on a new revision. On a stocked return that is the receipt retired and taken again at
    the new date -- not the stock left behind on the old one.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    credit = _return(books, invoice)

    moved = run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1,
                                           date='2017-01-05'), reason='Move it to the fifth')

    assert moved['changed'] is True and moved['revision']['date'] == '2017-01-05'
    # Nothing back on the fourth; both units back on the fifth.
    assert _stock(books, item, '2017-01-04') == ('1', HELD)
    assert _stock(books, item, '2017-01-05') == ('2', STOCK)
    assert _net(books) == {asset: STOCK, books['payable']: -STOCK}


def test_a_return_landing_behind_a_later_sale_corrects_that_sale_s_cost(books):
    """The dated correction a backdated return owes, posted as its own journal entry.

    Bought 2 at 10.00 on the first, sold 1 on the second at an average of 1000, bought 1 at
    20.00 on the fourth, sold 1 on the fifth at an average of (1000 + 2000) / 2 = 1500. Now the
    unit sold on the second comes back on the third at the 1000 it took. The fifth's sale then
    stands in front of three units worth 1000 + 1000 + 2000 = 4000, so it should have cost
    4000 * 1 / 3 = 1333, not 1500 -- and the 167 difference is posted at the fifth, where it
    belongs, rather than folded into the return. The correction is +167 on the asset side,
    because less was consumed than was posted, which is 167 out of cost of goods sold.
    """
    run = books['run']
    item = _inventory_part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='10')]), reason='Receive stock')
    first = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price=SOLD)]), reason='Sell one')
    run('bill post', dict(vendor=books['vendor'], date='2017-01-04',
        items=[dict(item=item, quantity='1', unit_cost='20')]), reason='Receive dearer stock')
    run('invoice post', dict(customer=books['customer'], date='2017-01-05',
        lines=[dict(item=item, quantity='1', unit_price=SOLD)]), reason='Sell another')
    assert _net(books)[books['cogs']] == 1000 + 1500

    _return(books, first, date='2017-01-03', reason='The first one back')

    # 1000 out of cost for the return, and 167 more out of it for the fifth's correction.
    assert _net(books)[books['cogs']] == 1000 + 1500 - 1000 - 167
    assert _stock(books, item, '2017-01-05') == ('2', 4000 - 1333)
    assert [(row['effective_date'], row['value_minor_units'])
            for row in _movements(books, item) if row['kind'] == 'recost'] == [('2017-01-05', 167)]


def test_a_zero_cost_return_moves_the_quantity_with_no_monetary_leg(books):
    """Zero value keeps its ownership and its batch and posts nothing, as zero value does."""
    run = books['run']
    item = _inventory_part(books)
    receivable = _receivable(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost='0')]), reason='Receive free samples')
    invoice = run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price=SOLD)]), reason='Sell a sample')
    assert _net(books) == {receivable: SALE, books['income']: -SALE}

    _return(books, invoice)

    assert _net(books) == {}
    assert _stock(books, item, '2017-01-03') == ('2', 0)
    receipt = _movements(books, item)[-1]
    assert _shape([receipt]) == [('receipt', 1_000_000, 0)]
    # The CHECK's own rule: no value, no posting line -- and the batch carries it instead.
    assert receipt['posting_line_id'] is None and receipt['posting_batch_id'] is not None


# ------------------------------------------------------------------ what is still refused


def test_a_standalone_credit_naming_a_stock_item_is_refused_and_writes_nothing(books):
    """A price is not a cost, so the line is refused and told the two ways that work."""
    run = books['run']
    item = _inventory_part(books)
    _stocked_invoice(books, item)

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(item=item, quantity='2', unit_price=SOLD)]), reason='Credit two back')

    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['reason'] == 'unlinked_stocked_credit_unsupported'
    assert caught.value.details['fields'] == [
        {'field': 'lines.0.item', 'problem': 'this item carries stock'}]
    # The line, the item and both ways out, in the message a person actually reads.
    assert 'lines.0.item' in caught.value.message
    assert 'Copper Elbow' in caught.value.message
    assert 'source_invoice' in caught.value.message and 'inventory adjust' in caught.value.message
    # Nothing written: the whole company database, row identity, storage class and value.
    assert database(_path(books)) == before
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_a_preview_of_a_standalone_stocked_credit_is_refused_the_same_way(books):
    """A dry run resolves the same grid, so the refusal has to reach it or it lies twice."""
    run = books['run']
    item = _inventory_part(books)
    _stocked_invoice(books, item)
    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(item=item, quantity='1', unit_price=SOLD)]),
            reason='Preview the credit', dry_run=True)
    assert caught.value.details['reason'] == 'unlinked_stocked_credit_unsupported'
    assert database(_path(books)) == before


def test_a_preview_of_a_stocked_return_writes_nothing_and_posts_when_run(books):
    """The supported case has to be as clean on a dry run as the refused one was."""
    run = books['run']
    item = _inventory_part(books)
    invoice = _stocked_invoice(books, item)

    before = database(_path(books))
    previewed = run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(source_invoice=invoice['id'], source_line=_sold_line(invoice),
                    quantity='1')]), reason='Preview the return', dry_run=True)

    assert previewed['total']['minor_units'] == SALE
    assert database(_path(books)) == before
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert _return(books, invoice)['status'] == 'posted'
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)


def test_correcting_a_credit_onto_a_named_stock_item_is_refused_and_writes_nothing(books):
    """`credit-memo update` resolves supplied lines through the same resolver, so it refuses."""
    run = books['run']
    item = _inventory_part(books)
    service = _service(books)
    _stocked_invoice(books, item)
    credit = run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(item=service, quantity='1', unit_price=SOLD)]), reason='Credit the money')

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1,
            lines=[dict(item=item, quantity='1', unit_price=SOLD)]),
            reason='Point the credit at the part')
    assert caught.value.details['reason'] == 'unlinked_stocked_credit_unsupported'
    assert database(_path(books)) == before


def test_a_credit_that_carries_no_stock_posts_exactly_as_it_did(books):
    """Nothing about a non-stock credit changed: no movement, no pair, the same two accounts."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    service = _service(books)
    _stocked_invoice(books, item)

    credit = run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(item=service, quantity='1', unit_price=SOLD)]), reason='Credit the money')

    assert credit['status'] == 'posted'
    assert credit['total']['minor_units'] == SALE
    # 12.00 off the receivable and 12.00 out of income; the stock is untouched.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert not [row for row in _movements(books, item) if row['transaction_id'] == credit['id']]


# ------------------------------------------------------------------ documents stored before this


def test_a_stocked_credit_stored_before_this_still_reads_voids_and_pages(books, monkeypatch):
    """Migration-era data keeps working: a credit that moved no stock still voids without it.

    The document is written with the stock half lifted, so the stored rows are the rows the
    shipped code wrote -- money only, no movement. Voiding it must reverse what it did post and
    nothing else, because a void of a document that never moved stock has no stock to move.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)

    stored = _legacy_return(books, monkeypatch, invoice,
                            'A credit written before returns moved stock')

    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    shown = run('credit-memo show', dict(credit_memo=stored['id']))
    assert shown['status'] == 'posted' and shown['total']['minor_units'] == SALE
    assert [row['revision_number'] for row in
            run('credit-memo history', dict(credit_memo=stored['id']))['items']] == [1]
    assert [row['id'] for row in run('credit-memo query', dict(
        customer=books['customer'], limit=50))['items']] == [stored['id']]

    voided = run('credit-memo void', dict(credit_memo=stored['id'], expected_version=1),
                 reason='Undo the pre-return credit')

    assert voided['status'] == 'voided'
    # The void reverses what the credit did post -- income and the receivable -- and leaves the
    # stock exactly where the credit left it, because the credit never moved it.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)
    assert not [row for row in _movements(books, item) if row['transaction_id'] == stored['id']]


def test_correcting_a_credit_stored_before_this_brings_the_stock_back(books, monkeypatch):
    """Which is the point of correcting it: the document was wrong and is made right."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    stored = _legacy_return(books, monkeypatch, invoice,
                            'A credit written before returns moved stock')
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}

    run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1,
                                   memo='Filed against the job'), reason='Fix the memo')

    assert _net(books) == {asset: STOCK, books['payable']: -STOCK}
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)


def test_a_correction_that_posts_nothing_is_left_alone_and_writes_nothing(books):
    """The two corrections that are genuinely not a reposting, measured rather than argued.

    A patch naming no field is answered before a grid is resolved at all; a patch whose values
    equal the stored ones is answered by ``unchanged`` with ``changed=False``. Neither writes a
    revision, a posting or a stock movement -- and "neither writes" is the whole company
    database compared row for row and value for value, not a claim about which path ran.
    """
    run = books['run']
    item = _inventory_part(books)
    invoice = _stocked_invoice(books, item)
    credit = _return(books, invoice)

    before = database(_path(books))
    empty = run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1),
                reason='Touch nothing')
    assert empty['changed'] is False
    assert database(_path(books)) == before

    same = run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1,
                                          date='2017-01-03'), reason='Set the date it already has')
    assert same['changed'] is False
    assert database(_path(books)) == before
    assert run('credit-memo show', dict(credit_memo=credit['id']))['version'] == 1


# ------------------------------------------------------------------ the help is true


def test_the_command_help_says_what_the_command_actually_does():
    """This help claimed the opposite of the truth once already, in the other direction.

    So it is checked against behaviour rather than read: the sentence saying stocked lines were
    refused whether named or returned is gone, the supported case is described, and the remedy
    the standalone refusal names is the remedy the help names.
    """
    post = credit_memo_cmds.DESCRIPTIONS['post']
    void = credit_memo_cmds.DESCRIPTIONS['void']
    with pytest.raises(BookflowError) as caught:
        credits._refuse_stocked(_captured('inventory_part'), 'lines.0.item')
    message = caught.value.message
    # What the help promises of a return is what the suite above measured.
    assert 'cost comes out of cost of goods sold and back into the inventory asset' in post
    assert 'not at the price on the credit and not at what the item is worth today' in post
    assert 'Voiding the credit takes the quantity and the cost out again' in post
    assert 'any stock it brought back goes out again' in void
    update = credit_memo_cmds.credit_memo_update.description
    assert 'Stock follows the corrected grid' in update
    assert 'leaves the grid out and only moves the date, which moves the goods to that' in update
    # What it says is refused is only the unlinked line, and it names the same two ways out.
    assert 'names a stock-carrying item instead of returning the invoice line' in post
    assert 'a credit memo moves no inventory and restores no cost' not in update
    assert 'moving the inventory back and restoring the cost is not built' not in post
    for remedy in ('source_invoice', 'source_line', 'inventory adjust'):
        assert remedy in message and remedy in post
