"""R104 increment 2 review fixes: found units filling a shortfall, returns following their sale, counts worth
nothing, blind re-derivations, positions on corrections, the journal hint -- and a seeded tie checker.

The tie checker runs late paperwork (sales before or after the count, bills, transfers, counts, voids,
credit memos, found units by `new_quantity`) in a fixed random order and, after every step, checks that
placements agree with movements on every date, that every recorded count's places hold what was counted
at its position, and that the inventory asset equals the valuation.
"""
import random
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_counts import _account, _record, _shrinkage, van  # noqa: F401
from tests.test_inventory_sites import _db, _held, _part, _tie_every_date


def _asset(books):
    accounts = books['client'].account.query(company=books['company'], limit=200)['items']
    return next(a['id'] for a in accounts if a.get('full_name') == 'Inventory Asset')


def _ties(books):
    _tie_every_date(books)
    with sqlite3.connect(_db(books)) as conn:
        for count, site, date in conn.execute("select id, site_id, count_date from inventory_counts where status='recorded'").fetchall():
            for item, counted in conn.execute('select item_id, counted_microunits from inventory_count_lines '
                                              'where count_id=? and counted_microunits is not null', (count,)).fetchall():
                held = conn.execute(
                    'select coalesce(sum(quantity_microunits), 0) from inventory_placements where item_id=? and site_id=? '
                    "and (effective_date < ? or (effective_date = ? and count_position in ('before', 'count')))",
                    (item, site, date, date)).fetchone()[0]
                assert held == counted, ('a count place does not hold what was counted', date, held, counted)
    valuation = books['run']('report inventory-valuation', dict(as_of='2017-12-31'))
    assert valuation['totals']['asset_value']['minor_units'] == _net(books).get(_asset(books), 0)


@pytest.mark.parametrize('how', ('adjust', 'count'))
def test_found_units_fill_a_shortfall_and_a_late_bill_still_posts(books, how):
    run = books['run']
    widget = _part(books, 'Widget', '2.00')
    shrinkage = _account(books, 'Shrinkage')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01', items=[dict(item=widget, quantity='10', unit_cost='2.00')]),
        reason='In')
    run('invoice post', dict(customer=books['customer'], date='2017-03-02', lines=[dict(item='Widget', quantity='15')]),
        reason='Sold more than held')
    if how == 'adjust':
        run('inventory adjust', dict(item='Widget', date='2017-03-03', adjustment_account=shrinkage, quantity_change='1'),
            reason='Found one')
    else:
        count = run('inventory-count create', dict(date='2017-03-03', items=['Widget']), reason='Count')
        _record(run, count, shrinkage, Widget=0)          # books hold -5: five found, filling the shortfall
    bill = dict(vendor=books['vendor'], date='2017-03-02', items=[dict(item=widget, quantity='2', unit_cost='7.00')])
    run('bill post', bill, reason='Late bill', dry_run=True)
    run('bill post', bill, reason='Late bill')
    _ties(books)


def test_a_return_follows_a_sale_stated_after(van):
    books, run = van['books'], van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    _record(run, count, van['shrinkage'], Widget=5)
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03', site='Van 1', count_position='after',
                                    lines=[dict(item='Widget', quantity='2')]), reason='After the count')
    line = run('invoice show', dict(invoice=sale['id']))['revision']['lines'][0]['line_id']
    returned = dict(customer=books['customer'], date='2017-03-03',
                    lines=[dict(source_invoice=sale['id'], source_line=line, quantity='1')])
    with pytest.raises(BookflowError) as refused:
        run('credit-memo post', dict(returned, count_position='before'), reason='Return')
    assert refused.value.code == 'E_VALIDATION' and 'follows its sale' in str(refused.value)
    memo = run('credit-memo post', returned, reason='Return')
    assert run('credit-memo show', dict(credit_memo=memo['id']))['count_position'] == 'after'
    assert _held(books, '2017-03-03')[('Widget', 'Van 1')] == '4'
    assert run('inventory-count show', dict(count='C-1'))['lines'][0]['difference'] == '0'
    _ties(books)


def test_a_difference_worth_nothing_never_blocks_other_paperwork(van):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    free = books['client'].item.create(
        company=books['company'], name='Sample', type='inventory_part', description='Sample', price='0.00',
        purchase_description='Sample', cost='0.00', cogs_account_id=books['cogs'], income_account_id=books['income'])['id']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Sample', 'Widget']), reason='Count')
    recorded = _record(run, count, shrinkage, Sample=3, Widget=4)[1]
    assert any('Sample is worth nothing' in line and 'no journal line' in line for line in recorded['warnings'])
    assert _held(books, '2017-03-03')[('Sample', 'Van 1')] == '3'
    run('bill post', dict(vendor=books['vendor'], date='2017-03-02', items=[dict(item=free, quantity='1', unit_cost='0.00',
                                                                                 site='Van 1')]), reason='A free one came')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03', site='Van 1',
                                    lines=[dict(item='Elbow', quantity='1')]), reason='Unrelated job')
    assert sale['status'] == 'posted'
    assert _held(books, '2017-03-03')[('Sample', 'Van 1')] == '3'
    # A count of nothing but such differences records nothing yet, says so, and refuses nobody.
    other = run('inventory-count create', dict(site='Warehouse', date='2017-03-04', items=['Sample']), reason='Count')
    alone = _record(run, other, shrinkage, Sample=2)[1]
    assert alone['status'] == 'recorded' and any('not recorded yet' in line for line in alone['warnings'])
    _ties_without_unrecorded(books)


def _ties_without_unrecorded(books):
    _tie_every_date(books)
    valuation = books['run']('report inventory-valuation', dict(as_of='2017-12-31'))
    assert valuation['totals']['asset_value']['minor_units'] == _net(books).get(_asset(books), 0)


def test_a_blind_count_derived_again_names_no_quantities(van):
    books, run = van['books'], van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget'], blind=True), reason='Count')
    _record(run, count, van['shrinkage'], Widget=2)
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03', site='Van 1',
                                    lines=[dict(item='Widget', quantity='1')]), reason='Job')
    derived = [line for line in sale['warnings'] if 'derived again' in line]
    assert derived == ['Count C-1 of Van 1 on 2017-03-03 was derived again for Widget, so the shelf stays as counted.']


def test_a_correction_of_the_position_alone_is_a_change_and_show_says_it(van):
    books, run = van['books'], van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    _record(run, count, van['shrinkage'], Widget=3)
    sale = run('invoice post', dict(customer=books['customer'], date='2017-03-03', site='Van 1',
                                    lines=[dict(item='Widget', quantity='2')]), reason='Job')
    assert run('invoice show', dict(invoice=sale['id']))['count_position'] == 'before'
    assert run('inventory-count show', dict(count='C-1'))['lines'][0]['difference'] == '0'
    moved = run('invoice update', dict(invoice=sale['id'], expected_version=sale['version'], count_position='after'),
                reason='The job came after the count')
    assert moved['version'] == sale['version'] + 1 and 'count_position' in moved['changed_fields']
    assert run('invoice show', dict(invoice=sale['id']))['count_position'] == 'after'
    assert run('inventory-count show', dict(count='C-1'))['lines'][0]['difference'] == '-2'
    # A correction that does not restate the position keeps it.
    kept = run('invoice update', dict(invoice=sale['id'], expected_version=moved['version'], memo='Kept'), reason='Memo')
    assert run('invoice show', dict(invoice=kept['id']))['count_position'] == 'after'
    _ties(books)


def test_a_count_entry_points_at_inventory_count_void(van):
    run = van['run']
    count = run('inventory-count create', dict(site='Van 1', date='2017-03-03', items=['Widget']), reason='Count')
    recorded = _record(run, count, van['shrinkage'], Widget=4)[1]
    with pytest.raises(BookflowError) as refused:
        run('journal void', dict(journal=recorded['documents'][0]['transaction_id']), reason='Try')
    assert '`inventory-count void`' in str(refused.value)


SEEDS = (11, 12, 13, 14, 15)
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04')


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_late_paperwork_keeps_every_tie(van, seed):
    books, run, shrinkage = van['books'], van['run'], van['shrinkage']
    rnd = random.Random(seed)
    names, sites = ['Widget', 'Elbow', 'Valve'], ['Warehouse', 'Van 1']
    sales, counts = [], []
    for step in range(30):
        op = rnd.choice(['sale', 'sale', 'bill', 'transfer', 'count', 'count', 'void', 'credit', 'found'])
        date = rnd.choice(DATES)
        try:
            if op == 'sale':
                payload = dict(customer=books['customer'], date=date, site=rnd.choice(sites),
                               lines=[dict(item=rnd.choice(names), quantity=str(rnd.randint(1, 4)))])
                if rnd.random() < .3:
                    payload['count_position'] = 'after'
                sales.append(run('invoice post', payload, reason='Job')['id'])
            elif op == 'bill':
                run('bill post', dict(vendor=books['vendor'], date=date, items=[dict(
                    item=van['items'][rnd.choice(names)], quantity=str(rnd.randint(1, 5)),
                    unit_cost=rnd.choice(['1.00', '2.50', '7.00']), site=rnd.choice(sites))]), reason='Bill')
            elif op == 'transfer':
                a, b = rnd.sample(sites, 2)
                run('inventory-transfer post', dict(date=date, from_site=a, to_site=b, lines=[
                    dict(item=rnd.choice(names), quantity=str(rnd.randint(1, 3)))]), reason='Move')
            elif op == 'count':
                chosen = rnd.sample(names, rnd.randint(1, 3))
                made = run('inventory-count create', dict(site=rnd.choice(sites), date=date, items=chosen), reason='Count')
                _record(run, made, shrinkage, **{name: rnd.randint(0, 12) for name in chosen})
                counts.append(made['number'])
            elif op == 'void' and counts:
                number = counts.pop(rnd.randrange(len(counts)))
                run('inventory-count void', dict(count=number, expected_version=run(
                    'inventory-count show', dict(count=number))['version']), reason='Recount')
            elif op == 'credit' and sales:
                sale = run('invoice show', dict(invoice=rnd.choice(sales)))
                if sale['status'] == 'posted':
                    run('credit-memo post', dict(customer=books['customer'], date=sale['revision']['date'], lines=[dict(
                        source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'], quantity='1')]),
                        reason='Return')
            elif op == 'found':
                run('inventory adjust', dict(date=date, adjustment_account=shrinkage, lines=[dict(
                    item=rnd.choice(names), new_quantity=str(rnd.randint(0, 15)), site=rnd.choice(sites))]), reason='Found')
        except BookflowError as error:
            # Refusals a person can act on are fine (a place counted twice in a day, over-returning);
            # nothing internal may surface.
            assert error.code not in ('E_INTERNAL', 'E_IO'), (seed, step, op, error)
        _ties(books)
