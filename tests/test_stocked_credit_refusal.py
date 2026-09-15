"""A credit memo that would return stock is refused, because it cannot move the stock.

A credit memo posts income, the tax it takes back and the receivable, and nothing else. Give
it a stock-carrying item and the money goes back while the quantity stays sold and its cost
stays in Cost of Goods Sold, which is what this suite measured before the refusal existed:

    Bought   2 x 8.00    ->  16.00 into Inventory Asset, 16.00 owed to the supplier
    Sold     1 x 12.00   ->  12.00 receivable, 12.00 income, 8.00 cost, 8.00 left in stock
    Credited 2 x 12.00   ->  24.00 off the receivable and 24.00 out of income, and the stock
                             still reads one unit worth 8.00 -- two units came back and
                             inventory holds one

So both kinds of credited stock line are refused instead: the item named on a standalone
credit, and the return of a stocked invoice line. What the refusal tells the person to do is
measured here too, because advice nobody has run is a guess: credit the money with a non-stock
item, and bring the quantity back with ``inventory adjust``, which together land exactly where
a built stocked return would have.

Nothing here reads the code's own output back into an assertion about itself: the ledger is
read through ``report general-ledger``, the stock through ``report stock-status``, and "wrote
nothing" is the whole company database compared row for row and value for value.
"""
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import credits
from bookflow.company.items import TRACKED_TYPES
from bookflow.company.sales_facts import SELLABLE_ITEM_TYPES, SalesLineProfile
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net  # noqa: F401
from tests.payment_raw_evidence import database

BOUGHT = '8'        # each, two of them
SOLD = '12'         # the one unit the customer is invoiced for
STOCK = 1600        # what the two units cost
COST = 800          # what the sold unit took out of inventory
HELD = 800          # what the unsold unit is still worth
SALE = 1200         # what the customer was invoiced


def _path(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def _stock(books, item, date):
    rows = books['run']('report stock-status', dict(as_of=date, limit=200))['rows']
    row = next(row for row in rows if row['item_id'] == item)
    return row['quantity_on_hand'], row['asset_value']['minor_units']


def _receivable(books):
    return next(row['id'] for row in books['client'].account.query(
        company=books['company'], limit=200)['items'] if row['type'] == 'accounts_receivable')


def _stocked_invoice(books, item):
    """Buy two units, sell one of them, exactly as the module header writes it out."""
    run = books['run']
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='2', unit_cost=BOUGHT)]), reason='Receive stock')
    return run('invoice post', dict(customer=books['customer'], date='2017-01-02',
        lines=[dict(item=item, quantity='1', unit_price=SOLD)]), reason='Sell one unit')


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


def _legacy_stocked_credit(books, monkeypatch, lines, reason):
    """Post a stocked credit with the refusal lifted, then put it back.

    The rows this leaves are byte for byte the rows the shipped code wrote, because the shipped
    code ran no refusal at all -- which is the only way to test what a company that already
    upgraded actually holds. Only the guard is restored, so the fixture's own patches stand.
    """
    guard = credits._refuse_stocked
    monkeypatch.setattr(credits, '_refuse_stocked', lambda profile, field: None)
    stored = books['run']('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=lines), reason=reason)
    monkeypatch.setattr(credits, '_refuse_stocked', guard)
    return stored


def _captured(item_type):
    """A captured line profile of one item family, as a revision would hold it."""
    return SalesLineProfile(
        item=dict(id='01ITEM', label='Thing', version=1), item_type=item_type,
        income_account=dict(id='01ACCT', name='Income', full_name='Income', number=None,
                            type='income', normal_balance='credit'))


def test_the_refused_families_are_the_item_master_s_own_and_not_a_second_list():
    """Every stock-carrying family is refused and every other sellable family is not.

    Written against the registry rather than a list of names, so a stock-carrying family added
    later is refused the day it exists instead of being silently credited.
    """
    assert set(TRACKED_TYPES) and set(TRACKED_TYPES) < set(SELLABLE_ITEM_TYPES)
    for item_type in SELLABLE_ITEM_TYPES:
        if item_type in TRACKED_TYPES:
            with pytest.raises(BookflowError) as caught:
                credits._refuse_stocked(_captured(item_type), 'lines.0.item')
            assert caught.value.code == 'E_VALIDATION'
            assert caught.value.details['item_type'] == item_type
            assert item_type.replace('_', ' ') in caught.value.message
        else:
            assert credits._refuse_stocked(_captured(item_type), 'lines.0.item') is None


def test_a_standalone_credit_naming_a_stock_item_is_refused_and_writes_nothing(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    _stocked_invoice(books, item)
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-02') == ('1', HELD)

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(item=item, quantity='2', unit_price=SOLD)]), reason='Credit two back')

    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    assert caught.value.details['fields'] == [
        {'field': 'lines.0.item', 'problem': 'this item carries stock'}]
    # The line, the item and what to do instead, in the message a person actually reads.
    assert 'lines.0.item' in caught.value.message
    assert 'Copper Elbow' in caught.value.message
    assert 'inventory adjust' in caught.value.message
    # Nothing written: the whole company database, row identity, storage class and value.
    assert database(_path(books)) == before
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_returning_a_stocked_invoice_line_is_refused_and_writes_nothing(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(source_invoice=invoice['id'], source_line=_sold_line(invoice),
                        quantity='1')]), reason='Take the unit back')

    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    assert caught.value.details['fields'] == [
        {'field': 'lines.0.source_line', 'problem': 'this item carries stock'}]
    assert caught.value.details['item_type'] == 'inventory_part'
    assert database(_path(books)) == before
    # The source line is still whole: nothing claimed it, so it can be returned the day the
    # feature exists.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_a_preview_of_a_stocked_credit_is_refused_the_same_way(books):
    """A dry run resolves the same lines, so the refusal has to reach it or it lies twice."""
    run = books['run']
    item = _inventory_part(books)
    _stocked_invoice(books, item)
    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(item=item, quantity='1', unit_price=SOLD)]),
            reason='Preview the credit', dry_run=True)
    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    assert database(_path(books)) == before


def test_correcting_a_credit_onto_a_stock_item_is_refused_and_writes_nothing(books):
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
    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    assert database(_path(books)) == before


def test_a_credit_that_carries_no_stock_posts_exactly_as_it_did(books):
    """The refusal is the stock-carrying families and nothing else."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    service = _service(books)
    _stocked_invoice(books, item)

    credit = run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(item=service, quantity='1', unit_price=SOLD)]), reason='Credit the money')

    assert credit['status'] == 'posted'
    assert credit['total']['minor_units'] == SALE
    # 12.00 off the receivable and 12.00 out of income; the stock is untouched, which is
    # exactly the honest half of what a stocked return would have done.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}
    assert _stock(books, item, '2017-01-03') == ('1', HELD)


def test_the_workaround_the_refusal_names_lands_where_a_stocked_return_would(books):
    """Credit the money with a non-stock item, bring the quantity back with an adjustment."""
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    service = _service(books)
    _stocked_invoice(books, item)

    run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(item=service, quantity='1', unit_price=SOLD)]), reason='Credit the money')
    run('inventory adjust', dict(item=item, date='2017-01-03', adjustment_account=books['cogs'],
        quantity_change='1', value_change='8.00', memo='Part returned by the customer'),
        reason='Put the returned unit back on the shelf')

    # Both units are on the shelf again at what they cost, nothing is owed, nothing was
    # earned, and no cost of goods sold stands against a sale that was undone.
    assert _net(books) == {asset: STOCK, books['payable']: -STOCK}
    assert _stock(books, item, '2017-01-03') == ('2', STOCK)


def test_a_stocked_credit_stored_before_the_refusal_still_reads_voids_and_pages(books, monkeypatch):
    """Migration-era data keeps working: this refuses new credits, not stored ones.

    The document is written with the refusal lifted, so the stored rows are exactly the rows
    the shipped code wrote. Every read path then runs against them with the refusal back in
    place, because a company that can no longer open its own history would be the worse defect.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)

    stored = _legacy_stocked_credit(books, monkeypatch,
        [dict(source_invoice=invoice['id'], source_line=_sold_line(invoice), quantity='1')],
        'A credit written before the refusal existed')

    # Refused again now, which is what makes the rest of this a test of stored data.
    with pytest.raises(BookflowError):
        run('credit-memo post', dict(customer=books['customer'], date='2017-01-04',
            lines=[dict(item=item, quantity='1', unit_price=SOLD)]), reason='Not any more')

    shown = run('credit-memo show', dict(credit_memo=stored['id']))
    assert shown['status'] == 'posted'
    assert shown['total']['minor_units'] == SALE
    assert shown['revision']['lines'][0]['item_snapshot']['item_type'] == 'inventory_part'

    history = run('credit-memo history', dict(credit_memo=stored['id']))
    assert [row['revision_number'] for row in history['items']] == [1]

    listed = run('credit-memo query', dict(customer=books['customer'], limit=50))['items']
    assert [row['id'] for row in listed] == [stored['id']]

    voided = run('credit-memo void', dict(credit_memo=stored['id'], expected_version=1),
                 reason='Undo the pre-refusal credit')
    assert voided['status'] == 'voided'
    # The void reverses what the credit actually posted -- income and the receivable -- and
    # leaves the stock exactly where the credit left it, because the credit never moved it.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, receivable: SALE,
                           books['income']: -SALE, books['cogs']: COST}
    assert _stock(books, item, '2017-01-04') == ('1', HELD)
    assert run('credit-memo show', dict(credit_memo=stored['id'], revision_number=1))['status'] == 'voided'


def test_an_omitted_grid_update_of_a_stored_stocked_return_is_refused_and_writes_nothing(
        books, monkeypatch):
    """The bypass: a correction supplies no line, so judging entered lines judged nothing.

    ``credit-memo update`` without ``lines`` retains every captured line and posts the document
    again on a new revision. On a credit already stored against a stock item that is the shipped
    wrong accounting posted a second time, which reading one never is -- so the refusal is put on
    the grid the write would post, and a date change is refused like any other reposting.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    invoice = _stocked_invoice(books, item)
    stored = _legacy_stocked_credit(books, monkeypatch,
        [dict(source_invoice=invoice['id'], source_line=_sold_line(invoice), quantity='1')],
        'A credit written before the refusal existed')

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1,
                                       date='2017-01-04'), reason='Move it to the next day')

    assert caught.value.code == 'E_VALIDATION'
    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    # Named by where it sits in the grid that would have been posted, not by what was typed:
    # nothing was typed.
    assert caught.value.details['fields'] == [
        {'field': 'lines.0.source_line', 'problem': 'this item carries stock'}]
    assert caught.value.details['item_type'] == 'inventory_part'
    assert 'inventory adjust' in caught.value.message
    # Nothing written: the whole company database, row identity, storage class and value.
    assert database(_path(books)) == before
    # And the stored credit is exactly as unreadable as it was before, which is to say not.
    assert run('credit-memo show', dict(credit_memo=stored['id']))['version'] == 1
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}
    assert _stock(books, item, '2017-01-04') == ('1', HELD)


def test_an_omitted_grid_update_of_a_stored_stocked_item_line_is_refused_the_same_way(
        books, monkeypatch):
    """The other stored shape: a named stock item rather than a return."""
    run = books['run']
    item = _inventory_part(books)
    _stocked_invoice(books, item)
    stored = _legacy_stocked_credit(books, monkeypatch,
        [dict(item=item, quantity='1', unit_price=SOLD)],
        'A standalone credit written before the refusal existed')

    before = database(_path(books))
    with pytest.raises(BookflowError) as caught:
        run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1,
                                       memo='Filed under the wrong job'), reason='Fix the memo')

    assert caught.value.details['reason'] == 'stocked_credit_unsupported'
    assert caught.value.details['fields'] == [
        {'field': 'lines.0.item', 'problem': 'this item carries stock'}]
    assert database(_path(books)) == before


def test_a_correction_that_posts_nothing_is_left_alone_and_writes_nothing(books, monkeypatch):
    """The two corrections that are genuinely not a reposting, measured rather than argued.

    A patch naming no field is answered before a grid is resolved at all; a patch whose values
    equal the stored ones is answered by ``unchanged`` with the same ``changed=False``. Neither
    writes a revision, a posting or a stock movement -- and "neither writes" is the whole company
    database compared row for row and value for value, before and after each one, not a claim
    about which code path ran.
    """
    run = books['run']
    item = _inventory_part(books)
    _stocked_invoice(books, item)
    stored = _legacy_stocked_credit(books, monkeypatch,
        [dict(item=item, quantity='1', unit_price=SOLD)],
        'A credit written before the refusal existed')

    before = database(_path(books))
    empty = run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1),
                reason='Touch nothing')
    assert empty['changed'] is False
    assert database(_path(books)) == before

    same = run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1,
                                          date='2017-01-03'), reason='Set the date it already has')
    assert same['changed'] is False
    assert database(_path(books)) == before

    # Still version 1, still one revision, still the same books.
    assert run('credit-memo show', dict(credit_memo=stored['id']))['version'] == 1
    assert [row['revision_number'] for row in
            run('credit-memo history', dict(credit_memo=stored['id']))['items']] == [1]


def test_a_stored_stocked_credit_is_corrected_by_replacing_its_grid_with_non_stock_lines(
        books, monkeypatch):
    """A person must be able to turn a bad document into a good one, and then correct that.

    The refusal is about the grid a write would post, not about the document's past: replace the
    stock-carrying line with the non-stock line the refusal names and the correction posts, after
    which an omitted-grid correction of the same credit retains a clean grid and posts too.
    """
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    service = _service(books)
    _stocked_invoice(books, item)
    stored = _legacy_stocked_credit(books, monkeypatch,
        [dict(item=item, quantity='1', unit_price=SOLD)],
        'A credit written before the refusal existed')

    corrected = run('credit-memo update', dict(credit_memo=stored['id'], expected_version=1,
        lines=[dict(item=service, quantity='1', unit_price=SOLD)]),
        reason='Put the credit on a non-stock item')

    assert corrected['changed'] is True and corrected['version'] == 2
    assert corrected['revision']['lines'][0]['item_snapshot']['item_type'] == 'service'
    assert corrected['total']['minor_units'] == SALE
    # The money is where it was -- 12.00 off the receivable and out of income -- and the stock
    # is where the credit always left it, because it never moved any.
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}
    assert _stock(books, item, '2017-01-04') == ('1', HELD)

    # The grid it now retains carries no stock, so the correction that was refused above posts.
    again = run('credit-memo update', dict(credit_memo=stored['id'], expected_version=2,
                                           date='2017-01-04'), reason='Move it to the next day')
    assert again['changed'] is True and again['revision']['date'] == '2017-01-04'
    assert _net(books) == {asset: HELD, books['payable']: -STOCK, books['cogs']: COST}
