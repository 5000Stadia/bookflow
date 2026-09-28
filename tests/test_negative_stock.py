"""Selling stock not yet on hand: provisional cost at the sale, a true-up at the receipt.

Every expected figure below is worked out by hand in the docstring beside it, from the rule the
person approved, and never read back from the engine:

- the units a sale takes below zero cost the item's weighted average; an item that has never
  held stock costs its purchase cost; with neither, zero -- and the sale says so in warnings;
- a later receipt fills the shortfall first, and for the units that filled it posts the
  difference between what they really cost and the provisional cost as a true-up between
  Inventory Asset and Cost of Goods Sold, dated at the receipt and linked to the sale;
- after the shortfall the average carries on from real receipts only.

At every step the books are checked two independent ways: the trial balance balances, and the
Inventory Asset account on the general ledger equals the stock-status total, which equals the
plain sum of the ``inventory_movements`` rows read straight out of the company file.
"""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_asset  # noqa: F401

pytestmark = pytest.mark.timeout(900)

END = '2017-12-31'


# ---------------------------------------------------------------- reading the books back


def _item(books, name, cost):
    return books['client'].item.create(
        company=books['company'], name=name, type='inventory_part', description=name,
        price='20.00', purchase_description=name, cost=cost,
        cogs_account_id=books['cogs'], income_account_id=books['income'])['id']


def _db(books):
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def _movements(books, item):
    with _db(books) as db:
        return [dict(row) for row in db.execute(
            'SELECT * FROM inventory_movements WHERE item_id = ? ORDER BY sequence', (item,))]


def _net(books, account, date_to=END):
    rows = books['run']('report general-ledger',
                        {'date_from': '2017-01-01', 'date_to': date_to, 'limit': 200})['rows']
    return sum(row['debit']['minor_units'] - row['credit']['minor_units'] for row in rows
               if row['kind'] == 'posting' and row['account_id'] == account)


def _stock(books, item, date=END):
    rows = books['run']('report stock-status', {'as_of': date, 'limit': 200})['rows']
    row = next(row for row in rows if row['item_id'] == item)
    return row['quantity_on_hand'], row['asset_value']['minor_units']


def _ties(books, date=END):
    """The trial balance balances and three independent readings of stock value agree."""
    trial = books['run']('report trial-balance', {'date_to': date, 'limit': 200})
    assert trial['totals']['debit'] == trial['totals']['credit']
    status = books['run']('report stock-status', {'as_of': date, 'limit': 200})
    with _db(books) as db:
        stored = db.execute('SELECT coalesce(sum(value_minor_units), 0) FROM inventory_movements '
                            'WHERE effective_date <= ?', (date,)).fetchone()[0]
        unbalanced = db.execute('SELECT batch_id FROM posting_lines GROUP BY batch_id HAVING '
                                'sum(debit_minor_units) != sum(credit_minor_units)').fetchall()
    assert unbalanced == []
    assert _net(books, _inventory_asset(books), date) == \
        status['totals']['asset_value']['minor_units'] == stored


def _stock_warnings(result):
    return [line for line in result['warnings'] if line.startswith('Takes ')]


def _bill(books, item, date, quantity, unit_cost, **context):
    return books['run']('bill post', dict(vendor=books['vendor'], date=date,
        items=[dict(item=item, quantity=quantity, unit_cost=unit_cost)]), reason='Receive stock',
        **context)


def _invoice(books, item, date, quantity, **context):
    return books['run']('invoice post', dict(customer=books['customer'], date=date,
        lines=[dict(item=item, quantity=quantity, unit_price='20')]), reason='Sell stock', **context)


def _issue(books, item, transaction_id):
    return next(row for row in _movements(books, item)
                if row['kind'] == 'issue' and row['transaction_id'] == transaction_id)


def _receipt(books, item, transaction_id):
    return next(row for row in _movements(books, item)
                if row['kind'] == 'receipt' and row['transaction_id'] == transaction_id)


def _true_ups(books, item):
    return [(row['effective_date'], row['corrects_movement_id'], row['filled_by_movement_id'],
             row['value_minor_units'])
            for row in _movements(books, item) if row['filled_by_movement_id'] is not None]


# ---------------------------------------------------------------- the cases


def test_below_zero_at_the_average_then_trued_up_at_a_higher_and_a_lower_cost(books):
    """Widget, every step by hand (minor units; COGS is the Cost of Goods Sold balance).

    1 Jan  bill 3 @ 10.00 = 3000               on hand 3 / 3000
    5 Jan  sell 5: 3 on hand take all 3000, 2 below zero at the 1000 average = 2000;
           COGS 5000; on hand -2 / 3000 - 5000 = -2000
    10 Jan bill 5 @ 12.00 = 6000: 2 fill the shortfall; they cost 6000 * 2/5 = 2400 against a
           provisional 2000, true-up 2000 - 2400 = -400 on the asset (COGS +400);
           on hand 3 / -2000 + 6000 - 400 = 3600 -- 12.00 each, the receipt's own cost
    15 Jan sell 5: 3 take 3600, 2 below zero at the 1200 average = 2400; COGS +6000;
           on hand -2 / -2400
    20 Jan bill 4 @ 9.00 = 3600: 2 fill; they cost 1800 against a provisional 2400,
           true-up 2400 - 1800 = +600 (COGS -600); on hand 2 / -2400 + 3600 + 600 = 1800
    COGS in all: 5000 + 400 + 6000 - 600 = 10800
    """
    item = _item(books, 'Widget', '10.00')
    first = _bill(books, item, '2017-01-01', '3', '10')
    _ties(books)

    sale = _invoice(books, item, '2017-01-05', '5')
    assert _stock_warnings(sale) == [
        'Takes Widget to -2 on 2017-01-05; its cost is provisional, at the average cost, '
        'until a receipt brings the item back up and trues it up.']
    assert _issue(books, item, sale['id'])['value_minor_units'] == -5000
    assert _stock(books, item) == ('-2', -2000)
    assert _net(books, books['cogs']) == 5000
    _ties(books)

    higher = _bill(books, item, '2017-01-10', '5', '12')
    assert _stock_warnings(higher) == []
    assert _true_ups(books, item) == [
        ('2017-01-10', _issue(books, item, sale['id'])['id'],
         _receipt(books, item, higher['id'])['id'], -400)]
    assert _stock(books, item) == ('3', 3600)
    assert _stock(books, item, '2017-01-09') == ('-2', -2000)   # the true-up is not backdated
    assert _net(books, books['cogs']) == 5400
    assert _net(books, books['cogs'], '2017-01-09') == 5000
    _ties(books)
    _ties(books, '2017-01-09')

    second = _invoice(books, item, '2017-01-15', '5')
    assert [line.split(';')[0] for line in _stock_warnings(second)] == ['Takes Widget to -2 on 2017-01-15']
    assert _issue(books, item, second['id'])['value_minor_units'] == -6000
    assert _stock(books, item) == ('-2', -2400)
    _ties(books)

    lower = _bill(books, item, '2017-01-20', '4', '9')
    assert _true_ups(books, item)[1] == (
        '2017-01-20', _issue(books, item, second['id'])['id'],
        _receipt(books, item, lower['id'])['id'], 600)
    assert _stock(books, item) == ('2', 1800)
    assert _net(books, books['cogs']) == 10800
    _ties(books)
    assert first['status'] == 'posted'


def test_a_shortfall_filled_by_two_receipts_trues_up_at_each_and_links_the_sale(books):
    """Gear.

    1 Jan  bill 2 @ 9.00 = 1800
    25 Jan sell 5: 1800 for the 2 on hand, 3 below zero at 900 = 2700; on hand -3 / -2700
    28 Jan bill 1 @ 11.00: fills 1 of 3; provisional released 2700 * 1/3 = 900, actual 1100,
           true-up -200; on hand -2 / -2700 + 1100 - 200 = -1800
    30 Jan bill 4 @ 10.00 = 4000: fills the last 2; released 2700 - 900 = 1800, actual
           4000 * 2/4 = 2000, true-up -200; on hand 2 / -1800 + 4000 - 200 = 2000
    The sale's settled cost is 4500 + 200 + 200 = 4900 = 1800 + 1100 + 2000: every unit at
    what it really cost.
    """
    item = _item(books, 'Gear', '9.00')
    _bill(books, item, '2017-01-01', '2', '9')
    sale = _invoice(books, item, '2017-01-25', '5')
    assert _issue(books, item, sale['id'])['value_minor_units'] == -4500
    assert _stock(books, item) == ('-3', -2700)
    one = _bill(books, item, '2017-01-28', '1', '11')
    assert _stock(books, item) == ('-2', -1800)
    _ties(books)
    two = _bill(books, item, '2017-01-30', '4', '10')
    assert _stock(books, item) == ('2', 2000)
    issue = _issue(books, item, sale['id'])['id']
    assert _true_ups(books, item) == [
        ('2017-01-28', issue, _receipt(books, item, one['id'])['id'], -200),
        ('2017-01-30', issue, _receipt(books, item, two['id'])['id'], -200)]
    # A sale's profitability can read its settled cost: its own issue plus every movement
    # that names it.
    with _db(books) as db:
        settled = db.execute('SELECT sum(value_minor_units) FROM inventory_movements '
                             'WHERE id = ? OR corrects_movement_id = ?', (issue, issue)).fetchone()[0]
    assert settled == -4900
    _ties(books)


def test_an_item_that_never_held_stock_is_provisional_at_its_purchase_cost(books):
    """Gadget, purchase cost 7.50, sold 2 on a sales receipt with nothing ever on hand:
    2 * 750 = 1500 provisional; then 3 bought at 8.00 fill 2 at 1600, true-up -100; 1 left
    at 800."""
    item = _item(books, 'Gadget', '7.50')
    sale = books['run']('sales-receipt post', dict(customer=books['customer'], date='2017-02-01',
        deposit_to=books['bank'], payment_method=next(iter(books['methods'].values())),
        lines=[dict(item=item, quantity='2', unit_price='20')]),
        reason='Cash sale')
    assert _stock_warnings(sale) == [
        'Takes Gadget to -2 on 2017-02-01; its cost is provisional, at the purchase cost on the '
        'item record, because it has never had stock, until a receipt brings the item back up '
        'and trues it up.']
    issue = _issue(books, item, sale['id'])
    assert (issue['value_minor_units'], issue['fallback_unit_cost_minor_units']) == (-1500, 750)
    assert _stock(books, item) == ('-2', -1500)
    _ties(books)
    _bill(books, item, '2017-02-10', '3', '8')
    assert [row[3] for row in _true_ups(books, item)] == [-100]
    assert _stock(books, item) == ('1', 800)
    _ties(books)


def test_with_no_average_and_no_purchase_cost_the_sale_costs_zero_and_says_so(books):
    """Gizmo, purchase cost 0.00: selling 1 posts no cost at all; the receipt of 1 at 5.00
    then trues it up by the whole 500 (COGS +500) and leaves 0 / 0."""
    item = _item(books, 'Gizmo', '0.00')
    sale = _invoice(books, item, '2017-03-01', '1')
    assert _stock_warnings(sale) == [
        'Takes Gizmo to -1 on 2017-03-01; its cost is provisional, at zero, because the item '
        'has no average cost and no purchase cost, until a receipt brings the item back up and '
        'trues it up.']
    assert _issue(books, item, sale['id'])['value_minor_units'] == 0
    assert _net(books, books['cogs']) == 0
    assert _stock(books, item) == ('-1', 0)
    _ties(books)
    _bill(books, item, '2017-03-05', '1', '5')
    assert [row[3] for row in _true_ups(books, item)] == [-500]
    assert _stock(books, item) == ('0', 0)
    assert _net(books, books['cogs']) == 500
    _ties(books)


def test_the_closing_date_is_respected_as_for_any_entry(books):
    """A sale short in a period that is later closed is trued up by a receipt in the open
    period, dated there; a receipt dated inside the closed period is refused as ever and
    writes nothing; so is a sale dated there."""
    item = _item(books, 'Fitting', '4.00')
    _invoice(books, item, '2017-02-01', '2')                         # provisional 800
    books['run']('company update', {'closing_date': '2017-03-31'})
    before = _movements(books, item)
    with pytest.raises(BookflowError) as closed:
        _bill(books, item, '2017-03-15', '2', '5')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    with pytest.raises(BookflowError) as closed_sale:
        _invoice(books, item, '2017-03-20', '1')
    assert closed_sale.value.code == 'E_PERIOD_CLOSED'
    assert _movements(books, item) == before
    _bill(books, item, '2017-04-05', '2', '5')                        # actual 1000
    assert [(row[0], row[3]) for row in _true_ups(books, item)] == [('2017-04-05', -200)]
    assert _stock(books, item) == ('0', 0)
    _ties(books)
    _ties(books, '2017-03-31')


def test_a_purchase_backdated_before_a_short_sale_recosts_it_and_backs_out_its_true_up(books):
    """Valve, purchase cost 10.00.

    10 Feb sell 2 with nothing on hand: provisional at purchase cost, 2000; on hand -2 / -2000
    20 Feb bill 5 @ 12.00 = 6000: true-up 2000 - 2400 = -400 dated 20 Feb; on hand 3 / 3600
    Then a bill for 4 @ 11.00 = 4400 is entered dated 5 Feb. On 10 Feb the sale is no longer
    short: it takes 2 of 4 at 1100 = 2200, so it owes -200 at its own date (the existing
    backdating rule), and the true-up it no longer needs is backed out, +400 at 20 Feb.
    On hand 7 / 4400 - 2200 + 6000 = 8200; the sale's settled cost is 2000 + 200 + 400 - 400
    = 2200, exactly two units at 11.00. The backdated bill warns of nothing.
    """
    item = _item(books, 'Valve', '10.00')
    sale = _invoice(books, item, '2017-02-10', '2')
    later = _bill(books, item, '2017-02-20', '5', '12')
    assert _stock(books, item) == ('3', 3600)
    backdated = _bill(books, item, '2017-02-05', '4', '11')
    assert _stock_warnings(backdated) == []
    issue = _issue(books, item, sale['id'])['id']
    recosts = [(row['effective_date'], row['filled_by_movement_id'], row['value_minor_units'])
               for row in _movements(books, item) if row['kind'] == 'recost']
    receipt = _receipt(books, item, later['id'])['id']
    assert recosts == [('2017-02-20', receipt, -400), ('2017-02-10', None, -200),
                       ('2017-02-20', receipt, 400)]
    assert _stock(books, item) == ('7', 8200)
    assert _stock(books, item, '2017-02-10') == ('2', 2200)
    with _db(books) as db:
        settled = db.execute('SELECT sum(value_minor_units) FROM inventory_movements '
                             'WHERE id = ? OR corrects_movement_id = ?', (issue, issue)).fetchone()[0]
    assert settled == -2200
    _ties(books)
    _ties(books, '2017-02-10')


def _return(books, sale, quantity, date):
    line = sale['revision']['lines'][0]['line_id']
    return books['run']('credit-memo post', dict(customer=books['customer'], date=date,
        lines=[dict(source_invoice=sale['id'], source_line=line, quantity=quantity)]),
        reason='Customer return')


def test_a_provisional_sale_fully_returned_before_stock_arrives_nets_to_nothing(books):
    """The person: "if it was sold it should be able to be returned." Purchase cost 5.00, none on hand.

    1 May  sell 2: provisional 2 x 500 = 1000; COGS 1000; on hand -2 / -1000
    3 May  both come back: the two unfilled units are cancelled at their provisional 1000;
           COGS 1000 - 1000 = 0; on hand 0 / 0
    5 May  bill 3 @ 6.00 = 1800: nothing is short any more, so no true-up at all; 3 / 1800
    """
    item = _item(books, 'Fully Returned', '5.00')
    sale = _invoice(books, item, '2017-05-01', '2')
    assert _net(books, books['cogs']) == 1000
    credit = _return(books, sale, '2', '2017-05-03')
    assert _receipt(books, item, credit['id'])['value_minor_units'] == 1000
    assert _stock(books, item) == ('0', 0)
    assert _net(books, books['cogs']) == 0
    _ties(books)
    _bill(books, item, '2017-05-05', '3', '6')
    assert _true_ups(books, item) == []
    assert _stock(books, item) == ('3', 1800)
    assert _net(books, books['cogs']) == 0
    _ties(books)


def test_a_partly_returned_provisional_sale_is_trued_up_only_for_what_stayed_sold(books):
    """Purchase cost 5.00, nothing on hand.

    1 May  sell 3: provisional 1500; on hand -3 / -1500
    3 May  1 comes back: one unfilled unit cancelled at 500; on hand -2 / -1000; COGS 1000
    5 May  bill 4 @ 6.00 = 2400 fills the 2 still sold: provisional released 1000, actual
           2 x 600 = 1200, true-up -200 (COGS +200); on hand 2 / -1000 + 2400 - 200 = 1200
    The sale's net cost is 1500 - 500 + 200 = 1200: the two units kept, at 6.00 each.
    """
    item = _item(books, 'Partly Returned', '5.00')
    sale = _invoice(books, item, '2017-05-01', '3')
    credit = _return(books, sale, '1', '2017-05-03')
    assert _receipt(books, item, credit['id'])['value_minor_units'] == 500
    assert _stock(books, item) == ('-2', -1000)
    _ties(books)
    bill = _bill(books, item, '2017-05-05', '4', '6')
    issue = _issue(books, item, sale['id'])['id']
    assert _true_ups(books, item) == [
        ('2017-05-05', issue, _receipt(books, item, bill['id'])['id'], -200)]
    assert _stock(books, item) == ('2', 1200)
    assert _net(books, books['cogs']) == 1200
    _ties(books)


def test_a_return_after_the_sale_was_trued_up_takes_its_share_of_the_settled_cost(books):
    """The existing rule. Purchase cost 5.00: sell 2 short at 1000; 2 May bill 4 @ 6.00 fills
    them, true-up -200, 2 / 1200; 3 May one comes back at half the settled 1200 = 600:
    3 / 1800, COGS 1000 + 200 - 600 = 600."""
    item = _item(books, 'Returnable', '5.00')
    sale = _invoice(books, item, '2017-05-01', '2')
    _bill(books, item, '2017-05-02', '4', '6')
    assert _stock(books, item) == ('2', 1200)
    credit = _return(books, sale, '1', '2017-05-03')
    assert _receipt(books, item, credit['id'])['value_minor_units'] == 600
    assert _stock(books, item) == ('3', 1800)
    assert _net(books, books['cogs']) == 600
    _ties(books)


def test_an_adjustment_below_zero_goes_through_with_the_same_warning(books):
    """An inventory adjustment is a stock-reducing path too: 2 on hand at 3.00 (600), take out
    3 -> 600 + 300 below zero = 900 out; -1 / -300, and the result warns."""
    item = _item(books, 'Adjusted', '3.00')
    _bill(books, item, '2017-06-01', '2', '3')
    out = books['run']('inventory adjust', dict(item=item, date='2017-06-02',
        adjustment_account=books['cogs'], quantity_change='-3'), reason='Shrinkage')
    assert out['adjustment']['value_change']['minor_units'] == -900
    assert [line.split(';')[0] for line in _stock_warnings(out)] == ['Takes Adjusted to -1 on 2017-06-02']
    assert _stock(books, item) == ('-1', -300)
    _ties(books)


def test_a_preview_warns_before_anything_is_saved(books):
    item = _item(books, 'Previewed', '2.00')
    before = _movements(books, item)
    preview = books['client'].run('invoice post', dict(customer=books['customer'], date='2017-07-01',
        lines=[dict(item=item, quantity='1', unit_price='20')]), company=books['company'],
        dry_run=True)
    assert preview['dry_run'] is True
    assert [line.split(';')[0] for line in _stock_warnings(preview)] == ['Takes Previewed to -1 on 2017-07-01']
    assert _movements(books, item) == before
