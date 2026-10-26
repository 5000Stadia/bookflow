"""R104 increment 6: landed cost (design/advanced-inventory.md §5, §9 increment 6).

A freight bill comes in after the stock it carried. Its charge sits on Landed Cost Clearing until `landed-cost
post` spreads it over the receipts; each receipt's share joins its cost at the receipt's position but is
recognised at the allocation date, so stock sold before then owes a forward true-up dated at the allocation.
Throughout: the asset account equals the stock valuation, placements equal the stock ledger, the clearing account
equals the unallocated charges on every date, and the journal balances.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _net  # noqa: F401
from tests.test_inventory_sites import _db, _part, _tie_every_date


def _asset(books):
    accounts = books['client'].account.query(company=books['company'], limit=200)['items']
    return next(a['id'] for a in accounts if a.get('full_name') == 'Inventory Asset')


@pytest.fixture
def shop(books):
    run = books['run']
    clearing = run('landed-cost setup', {}, reason='Freight on stock')['account_id']
    widget = _part(books, 'Widget', cost='10.00')
    gadget = _part(books, 'Gadget', cost='4.00')
    acme = books['client'].customer.create(company=books['company'], name='Acme')['id']
    return dict(books=books, run=run, clearing=clearing, widget=widget, gadget=gadget, acme=acme)


def _buy(shop, date, quantity, cost='10.00', item=None):
    return shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, items=[dict(
        item=item or shop['widget'], quantity=str(quantity), unit_cost=cost)]), reason='Stock in')['id']


def _freight(shop, date, amount):
    return shop['run']('bill post', dict(vendor=shop['books']['vendor'], date=date, expenses=[dict(
        account=shop['clearing'], amount=amount)]), reason='Freight bill')['id']


def _sell(shop, date, quantity, item='Widget'):
    return shop['run']('invoice post', dict(customer=shop['acme'], date=date, lines=[dict(
        item=item, quantity=str(quantity), unit_price='50.00')]), reason='Job')['id']


def _post(shop, charges, targets, **extra):
    return shop['run']('landed-cost post', dict(charges=charges, targets=targets, **extra), reason='Spread freight')


def _value(shop, item='Widget', as_of='2017-12-31'):
    rows = shop['run']('report inventory-valuation', dict(as_of=as_of))['rows']
    return next((row['asset_value']['minor_units'] for row in rows if row['item_name'] == item), 0)


def _ties(shop):
    """The asset tie, the quantity tie, the clearing account equal to the unallocated charges on every date, and
    the journal balancing."""
    books = shop['books']
    _tie_every_date(books)
    net = _net(books)
    valuation = shop['run']('report inventory-valuation', dict(as_of='2017-12-31'))
    assert valuation['totals']['asset_value']['minor_units'] == net.get(_asset(books), 0)
    assert sum(net.values()) == 0
    from tests.test_landed_cost import _clearing_tie
    _clearing_tie(shop)


def _clearing_tie(shop):
    with sqlite3.connect(_db(shop['books'])) as conn:
        dates = [r[0] for r in conn.execute(
            'SELECT DISTINCT b.effective_date FROM posting_lines l JOIN posting_batches b ON b.id = l.batch_id '
            'WHERE l.account_id = ? UNION SELECT date FROM landed_cost_allocations', (shop['clearing'],))]
    for date in dates:
        report = shop['run']('landed-cost charges', dict(as_of=date, include_allocated=True))
        assert report['clearing_balance']['minor_units'] == report['total_unallocated']['minor_units'], date
        assert report['total_unallocated']['minor_units'] == sum(r['unallocated']['minor_units'] for r in report['rows'])


def _cogs(shop):
    return _net(shop['books']).get(shop['books']['cogs'], 0)


def test_90_by_quantity_over_10_and_20_gives_30_and_60(shop):
    first, second = _buy(shop, '2017-03-01', 10), _buy(shop, '2017-03-02', 20)
    freight = _freight(shop, '2017-03-05', '90.00')
    assert shop['run']('landed-cost charges', {})['total_unallocated']['minor_units'] == 9000
    result = _post(shop, [dict(document=freight)], [dict(document=first), dict(document=second)])
    assert [t['amount']['minor_units'] for t in result['allocation']['targets']] == [3000, 6000]
    assert result['allocation']['date'] == '2017-03-05'
    assert _value(shop) == 30000 + 9000
    assert shop['run']('landed-cost charges', {})['total_unallocated']['minor_units'] == 0
    _ties(shop)


def _true_ups(result):
    return [(t['effective_date'], t['delta']['minor_units']) for t in result['allocation']['true_ups']]


def test_partly_sold_stock_takes_the_sold_share_to_cost_of_goods_sold_at_the_allocation(shop):
    """The 10 were the item's only stock and 5 were sold before the allocation: forward true-ups dated at the
    allocation take 15 to cost of goods sold, and 15 stays in stock."""
    receipt = _buy(shop, '2017-03-01', 10)
    _sell(shop, '2017-03-02', 5)
    freight = _freight(shop, '2017-03-10', '30.00')
    preview = shop['run']('landed-cost post', dict(charges=[dict(document=freight)], targets=[dict(document=receipt)]),
                          reason='Spread', dry_run=True)
    target = preview['allocation']['targets'][0]
    assert (target['to_stock']['minor_units'], target['issued_before']['minor_units']) == (1500, 1500)
    result = _post(shop, [dict(document=freight)], [dict(document=receipt)])
    assert result['allocation']['date'] == '2017-03-10' and _true_ups(result) == [('2017-03-10', -1500)]
    assert _value(shop) == 5000 + 1500
    assert _net(shop['books'], date_to='2017-03-09').get(shop['books']['cogs'], 0) == 5000   # the sale kept its cost
    assert _cogs(shop) == 6500
    # A sale on or after the allocation carries the charge in its own cost: 6500 / 5 = 13.00 each.
    _sell(shop, '2017-03-11', 5)
    assert _value(shop) == 0 and _cogs(shop) == 13000
    _ties(shop)


def test_a_shortfall_filled_by_the_target_receipt_owes_its_share_forward(shop):
    """Sold 5 before any stock (provisional at the purchase cost, 10.00), then 10 received at 12.00 fill it (true-up
    at the receipt), then 30.00 freight on that receipt: the short sale owes 15 more, dated at the allocation."""
    _sell(shop, '2017-03-01', 5)
    receipt = _buy(shop, '2017-03-05', 10, cost='12.00')
    freight = _freight(shop, '2017-03-10', '30.00')
    result = _post(shop, [dict(document=freight)], [dict(document=receipt)])
    assert _true_ups(result) == [('2017-03-10', -1500)]
    assert _net(shop['books'], date_to='2017-03-09').get(shop['books']['cogs'], 0) == 6000
    assert _cogs(shop) == 7500 and _value(shop) == 7500
    _ties(shop)


def test_a_sale_backdated_before_the_allocation_owes_its_share_at_the_allocation(shop):
    receipt = _buy(shop, '2017-03-01', 10)
    freight = _freight(shop, '2017-03-10', '30.00')
    result = _post(shop, [dict(document=freight)], [dict(document=receipt)])
    assert _true_ups(result) == [] and _value(shop) == 13000
    _sell(shop, '2017-03-05', 5)          # entered later, dated before the allocation
    assert _net(shop['books'], date_to='2017-03-09').get(shop['books']['cogs'], 0) == 5000
    assert _cogs(shop) == 6500 and _value(shop) == 6500
    with sqlite3.connect(_db(shop['books'])) as conn:
        assert conn.execute("SELECT effective_date, value_minor_units FROM inventory_movements WHERE kind = 'recost' "
                            "AND filled_by_movement_id IS NOT NULL").fetchall() == [('2017-03-10', -1500)]
    _ties(shop)


def test_a_freight_bill_after_the_close_posts_wholly_in_the_open_period_through_builds(shop):
    """Stock received and partly sold, and partly built into a kit that was sold, before the close; the freight
    bill was entered before the close was set. The allocation moves to the first open date, and every share sold
    -- directly or inside the kit -- reaches cost of goods sold on that date; nothing earlier moves."""
    books, run = shop['books'], shop['run']
    kit = books['client'].item.create(company=books['company'], name='Widget Kit', type='inventory_assembly',
                                      description='Kit', price='90.00', purchase_description='Kit', cost='0.00',
                                      cogs_account_id=books['cogs'], income_account_id=books['income'],
                                      members=[dict(component_item_id=shop['widget'], quantity='2')])['id']
    receipt = _buy(shop, '2017-03-01', 10)
    _sell(shop, '2017-03-02', 4)
    run('assembly build', dict(item=kit, quantity='1', date='2017-03-03'), reason='Built a kit')
    _sell(shop, '2017-03-04', 1, item='Widget Kit')
    freight = _freight(shop, '2017-03-20', '50.00')
    run('company update', {'closing_date': '2017-03-31'})
    before = _net(books, date_to='2017-03-31')
    with pytest.raises(BookflowError) as closed:
        _post(shop, [dict(document=freight)], [dict(document=receipt)], date='2017-03-25')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    result = _post(shop, [dict(document=freight)], [dict(document=receipt)])
    assert result['allocation']['date'] == '2017-04-01'
    assert _net(books, date_to='2017-03-31') == before                         # nothing on or before the close moved
    # 5.00 a widget: 4 sold (20.00) and 2 built into the kit that was sold (10.00) reach COGS on 2017-04-01.
    assert _cogs(shop) - before.get(books['cogs'], 0) == 3000
    assert _value(shop) == 4 * 1000 + 2000 and _value(shop, 'Widget Kit') == 0
    with sqlite3.connect(_db(books)) as conn:
        assert {row[0] for row in conn.execute("SELECT effective_date FROM inventory_movements WHERE kind IN "
                                                "('recost', 'landed') AND sequence > (SELECT min(sequence) FROM "
                                                "inventory_movements WHERE kind = 'landed')")} == {'2017-04-01'}
    _ties(shop)


def test_a_freight_credit_reverses_its_share_exactly_and_never_below_zero(shop):
    first, second = _buy(shop, '2017-03-01', 10), _buy(shop, '2017-03-02', 20)
    freight = _freight(shop, '2017-03-05', '90.00')
    _post(shop, [dict(document=freight)], [dict(document=first), dict(document=second)])
    credit = shop['run']('vendor-credit post', dict(vendor=shop['books']['vendor'], date='2017-03-06',
                                                    ap_account=shop['books']['payable'],
                                                    expenses=[dict(account=shop['clearing'], amount='90.00')]),
                         reason='Freight overcharged')['id']
    rows = shop['run']('landed-cost charges', {})['rows']
    assert [row['unallocated']['minor_units'] for row in rows] == [-9000]
    with pytest.raises(BookflowError) as below:
        _post(shop, [dict(document=credit)], [dict(document=first, amount='60.00'), dict(document=second, amount='30.00')],
              basis='amounts')
    assert below.value.code == 'E_VALIDATION' and 'below zero' in below.value.message
    result = _post(shop, [dict(document=credit)], [dict(document=first), dict(document=second)])
    assert [t['amount']['minor_units'] for t in result['allocation']['targets']] == [-3000, -6000]
    assert _value(shop) == 30000 and shop['run']('landed-cost charges', {})['rows'] == []
    _ties(shop)


def test_one_charge_in_two_parts_and_a_retried_post_leave_the_clearing_account_equal_to_the_unallocated(shop):
    first, second = _buy(shop, '2017-03-01', 10), _buy(shop, '2017-03-02', 20)
    freight = _freight(shop, '2017-03-05', '90.00')
    payload = dict(charges=[dict(document=freight, amount='40.00')], targets=[dict(document=first)])
    one = shop['run']('landed-cost post', payload, reason='Part one', idempotency_key='lc-part-one')
    again = shop['run']('landed-cost post', payload, reason='Part one', idempotency_key='lc-part-one')
    assert again['allocation']['number'] == one['allocation']['number']
    assert shop['run']('landed-cost charges', {})['total_unallocated']['minor_units'] == 5000
    _clearing_tie(shop)
    _post(shop, [dict(document=freight)], [dict(document=second)])
    report = shop['run']('landed-cost charges', dict(include_allocated=True))
    assert report['total_unallocated']['minor_units'] == 0 and report['clearing_balance']['minor_units'] == 0
    assert report['rows'][0]['allocated']['minor_units'] == 9000
    with pytest.raises(BookflowError) as over:
        _post(shop, [dict(document=freight)], [dict(document=second)])
    assert over.value.code == 'E_VALIDATION'
    _ties(shop)


def test_allocating_two_charges_in_either_order_gives_the_same_shares(shop):
    receipts = [_buy(shop, '2017-03-01', 7, cost='3.10'), _buy(shop, '2017-03-01', 11, cost='2.30'),
                _buy(shop, '2017-03-02', 13, cost='1.70')]
    a, b = _freight(shop, '2017-03-05', '70.01'), _freight(shop, '2017-03-05', '33.33')
    targets = [dict(document=r) for r in receipts]

    def shares(order, basis):
        out = {}
        posted = []
        for charge in order:
            result = _post(shop, [dict(document=charge)], targets, basis=basis)
            posted.append(result['allocation']['number'])
            out[charge] = [line['amount']['minor_units'] for line in result['allocation']['lines']]
        for number in posted:
            shop['run']('landed-cost void', dict(allocation=number), reason='Again')
        return out
    for basis in ('quantity', 'value'):
        assert shares([a, b], basis) == shares([b, a], basis)
    assert sum(shares([a], 'value')[a]) == 7001
    _ties(shop)


def test_guards_a_charge_or_a_receipt_an_allocation_names_cannot_be_voided_or_reduced(shop):
    receipt = _buy(shop, '2017-03-01', 10)
    freight = _freight(shop, '2017-03-05', '90.00')
    result = _post(shop, [dict(document=freight)], [dict(document=receipt)])
    bill = shop['run']('bill show', dict(bill=freight))
    for name, payload in (('bill void', dict(bill=freight, expected_version=bill['version'])),
                          ('bill void', dict(bill=receipt, expected_version=shop['run']('bill show', dict(bill=receipt))['version']))):
        with pytest.raises(BookflowError) as refused:
            shop['run'](name, payload, reason='Oops')
        assert refused.value.code == 'E_WORK_DEPENDENCY' and 'landed-cost void' in refused.value.message
    with pytest.raises(BookflowError) as reduced:
        shop['run']('bill update', dict(bill=freight, expected_version=bill['version'],
                                        expenses=[dict(account=shop['clearing'], amount='50.00')]), reason='Less')
    assert reduced.value.code == 'E_WORK_DEPENDENCY'
    shop['run']('landed-cost void', dict(allocation=result['allocation']['number']), reason='Wrong receipt')
    assert _value(shop) == 10000
    shop['run']('bill void', dict(bill=freight, expected_version=bill['version']), reason='Wrong vendor')
    assert shop['run']('landed-cost charges', dict(include_allocated=True))['rows'] == []
    _ties(shop)


def test_a_charge_not_capitalised_goes_to_an_account_and_weight_needs_unit_weights(shop):
    receipt = _buy(shop, '2017-03-01', 10)
    gadgets = _buy(shop, '2017-03-01', 5, cost='4.00', item=shop['gadget'])
    freight = _freight(shop, '2017-03-05', '90.00')
    with pytest.raises(BookflowError) as weightless:
        _post(shop, [dict(document=freight)], [dict(document=receipt), dict(document=gadgets)], basis='weight')
    assert 'unit weight' in str(weightless.value.details)
    shop['run']('item update', dict(item=shop['widget'], unit_weight='2'), reason='Weighed')
    result = _post(shop, [dict(document=freight, amount='80.00')],
                   [dict(document=receipt), dict(document=gadgets, unit_weight='4')], basis='weight')
    assert [t['amount']['minor_units'] for t in result['allocation']['targets']] == [4000, 4000]   # 20 kg and 20 kg
    expense = shop['books']['client'].account.create(company=shop['books']['company'], name='Freight Out',
                                                     type='expense')['id']
    _post(shop, [dict(document=freight)], [dict(account=expense, amount='10.00')], basis='amounts')
    assert _net(shop['books'])[expense] == 1000
    _ties(shop)


def test_receipts_with_shipping_read_as_before_and_their_shares_are_allocation_lines(shop):
    receipt = shop['run']('item-receipt post', dict(date='2017-03-01', vendor=shop['books']['vendor'], shipping='10.00',
                                                    items=[dict(item=shop['widget'], quantity='1', unit_cost='10'),
                                                           dict(item=shop['gadget'], quantity='2', unit_cost='4')]),
                          reason='Received with freight')
    assert [line['shipping']['minor_units'] for line in receipt['items']] == [333, 667]
    assert _value(shop) == 1333 and _value(shop, 'Gadget') == 800 + 667
    listed = shop['run']('landed-cost query', dict(kind='receipt_shipping'))['rows']
    assert len(listed) == 1 and listed[0]['total']['minor_units'] == 1000 and listed[0]['number'] == receipt['number']
    shown = shop['run']('landed-cost show', dict(allocation=listed[0]['id']))
    assert [line['amount']['minor_units'] for line in shown['allocation']['lines']] == [333, 667]
    # Freight later on the same receipt: value basis reads the line's own cost, before any landed cost.
    freight = _freight(shop, '2017-03-05', '18.00')
    result = _post(shop, [dict(document=freight)], [dict(document=receipt['number'])], basis='value')
    assert [line['measure'] for line in result['allocation']['lines']] == ['10.00', '8.00']
    assert [t['amount']['minor_units'] for t in result['allocation']['targets']] == [1000, 800]
    _ties(shop)


# ---------------------------------------------------------------- the seeded tie checker
# Five seeds of 25 steps in an ordinary run; BF_LANDED_SEEDS and BF_LANDED_STEPS widen it (the review ran 60 x 40).
import os as _os
SEEDS = tuple(range(61, 61 + int(_os.environ.get('BF_LANDED_SEEDS', '5'))))
STEPS = int(_os.environ.get('BF_LANDED_STEPS', '25'))
DATES = ('2017-03-01', '2017-03-02', '2017-03-03', '2017-03-04', '2017-03-05')


def _step_ties(shop):
    """After every step: the asset tie, the quantity tie, the clearing account equal to the unallocated charges on
    every date (`landed-cost charges` refuses otherwise), and the journal balancing."""
    books = shop['books']
    _tie_every_date(books)
    net = _net(books)
    valuation = shop['run']('report inventory-valuation', dict(as_of='2017-12-31'))
    assert valuation['totals']['asset_value']['minor_units'] == net.get(_asset(books), 0)
    assert sum(net.values()) == 0
    report = shop['run']('landed-cost charges', dict(as_of='2017-12-31', include_allocated=True))
    assert report['clearing_balance']['minor_units'] == report['total_unallocated']['minor_units'] \
        == net.get(shop['clearing'], 0)


@pytest.mark.parametrize('seed', SEEDS)
def test_seeded_freight_paperwork_keeps_every_tie(shop, seed):
    """Receipts, sales, builds, freight bills and credits, allocations in parts and their voids, voids of the
    freight bills and the receipts, all entered in a random order with random dates around the allocations; after
    every step every tie holds and no valid paperwork is refused by a guard or internally."""
    import random
    books, run = shop['books'], shop['run']
    rnd = random.Random(seed)
    kit = books['client'].item.create(company=books['company'], name='Widget Kit', type='inventory_assembly',
                                      description='Kit', price='90.00', purchase_description='Kit', cost='0.00',
                                      cogs_account_id=books['cogs'], income_account_id=books['income'],
                                      members=[dict(component_item_id=shop['widget'], quantity='2')])['id']
    receipts, charges, allocations = [], [], []
    done, refused = {}, {}
    for step in range(STEPS):
        op = rnd.choice(['buy', 'buy', 'buy', 'sell', 'sell', 'freight', 'freight', 'credit', 'allocate', 'allocate',
                         'allocate', 'allocate', 'void_allocation', 'void_freight', 'void_receipt', 'build', 'sell_kit'])
        date = rnd.choice(DATES)
        try:
            if op == 'buy':
                item = rnd.choice([shop['widget'], shop['gadget']])
                receipts.append(_buy(shop, date, rnd.randint(1, 9), cost=rnd.choice(['3.10', '4.00', '7.25']), item=item))
            elif op == 'sell':
                _sell(shop, date, rnd.randint(1, 4), item=rnd.choice(['Widget', 'Gadget']))
            elif op == 'sell_kit':
                _sell(shop, date, 1, item='Widget Kit')
            elif op == 'build':
                run('assembly build', dict(item=kit, quantity='1', date=date), reason='Kit')
            elif op == 'freight':
                charges.append(_freight(shop, date, rnd.choice(['10.00', '33.33', '90.01'])))
            elif op == 'credit':
                charges.append(run('vendor-credit post', dict(
                    vendor=books['vendor'], date=date, ap_account=books['payable'],
                    expenses=[dict(account=shop['clearing'], amount=rnd.choice(['1.00', '5.55']))]),
                    reason='Freight credit')['id'])
            elif op == 'allocate' and charges and receipts:
                charge = dict(document=rnd.choice(charges))
                if rnd.random() < .4:
                    charge['amount'] = rnd.choice(['1.00', '2.50', '7.77'])
                targets = [dict(document=r) for r in rnd.sample(receipts, rnd.randint(1, min(3, len(receipts))))]
                extra = dict(basis=rnd.choice(['quantity', 'value']))
                if rnd.random() < .3:
                    extra['date'] = rnd.choice(DATES)
                allocations.append(_post(shop, [charge], targets, **extra)['allocation']['number'])
            elif op == 'void_allocation' and allocations:
                run('landed-cost void', dict(allocation=allocations.pop(rnd.randrange(len(allocations)))), reason='Undo')
            elif op == 'void_freight' and charges:
                document = rnd.choice(charges)
                charges.remove(document)
                try:
                    bill = run('bill show', dict(bill=document))
                    run('bill void', dict(bill=document, expected_version=bill['version']), reason='Void freight')
                except BookflowError as error:
                    if error.code == 'E_RECORD_NOT_FOUND':
                        credit = run('vendor-credit show', dict(credit=document))
                        run('vendor-credit void', dict(credit=document, expected_version=credit['version']),
                            reason='Void credit')
                    else:
                        raise
            elif op == 'void_receipt' and receipts:
                document = rnd.choice(receipts)
                receipts.remove(document)
                bill = run('bill show', dict(bill=document))
                run('bill void', dict(bill=document, expected_version=bill['version']), reason='Void stock')
            done[op] = done.get(op, 0) + 1
        except BookflowError as error:
            refused[(op, error.code)] = refused.get((op, error.code), 0) + 1
            if op == 'void_freight':
                charges.append(document)
            if op == 'void_receipt':
                receipts.append(document)
            if _os.environ.get('BF_LANDED_DEBUG'):
                print(op, error.message)
            # Refusals a person can act on are fine (an allocation naming the document, a closed or short build, a
            # date before a receipt, nothing left to allocate); a guard trigger or an internal error never is.
            assert error.code not in ('E_INTERNAL', 'E_IO', 'E_GUARD_REFUSED'), (seed, step, op, error)
        _step_ties(shop)
    print('landed seed', seed, done, refused)


def test_co0098_on_a_populated_company_changes_no_existing_row_and_records_receipt_shipping(shop):
    """co0098 upgrades a company with stock: every existing row reads the same, each standing item receipt's
    shipping shares become allocation lines (the same shares the receipt writes today), and the frozen DDL is the
    current metadata."""
    import importlib
    from sqlalchemy.dialects.sqlite import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable
    from bookflow.company import schema as c
    from tests.test_negative_stock_migration import _at
    m = importlib.import_module('bookflow.storage.company_migrations.versions.0098_landed_cost')
    tables = [c.metadata.tables[name] for name in ('landed_cost_allocations', 'landed_cost_allocation_lines')]
    assert m.DDL == tuple(str(CreateTable(t).compile(dialect=dialect())) for t in tables)
    assert m.INDEXES == tuple(str(CreateIndex(i).compile(dialect=dialect()))
                              for t in tables for i in sorted(t.indexes, key=lambda i: i.name))
    run = shop['run']
    run('item-receipt post', dict(date='2017-03-01', vendor=shop['books']['vendor'], shipping='10.00', items=[
        dict(item=shop['widget'], quantity='1', unit_cost='10'), dict(item=shop['gadget'], quantity='2', unit_cost='4')]),
        reason='Received with freight')
    voided = run('item-receipt post', dict(date='2017-03-01', vendor=shop['books']['vendor'], shipping='5.00', items=[
        dict(item=shop['widget'], quantity='1', unit_cost='10')]), reason='Received')
    run('item-receipt void', dict(receipt=voided['id']), reason='Never came')
    _sell(shop, '2017-03-02', 1)
    path = _db(shop['books'])
    copy = path.parent / 'rewound98.db'
    with sqlite3.connect(path) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as conn:
        made = sorted(conn.execute("SELECT l.target_movement_id, l.amount_minor_units, l.measure FROM "
                                   "landed_cost_allocation_lines l JOIN landed_cost_allocations a ON a.id = l.allocation_id "
                                   "WHERE a.status = 'posted' AND l.reverses_line_id IS NULL").fetchall())
        assert [row[1] for row in made] == sorted([333, 667])
        conn.execute('PRAGMA foreign_keys=OFF')
        for trigger in m.TRIGGERS:
            conn.execute(f'DROP TRIGGER {trigger}')
        conn.execute('DROP TRIGGER inventory_movements_true_up_link')
        conn.execute(m.OLD_TRUE_UP_GUARD)
        for table in m.TABLES:
            conn.execute(f'DROP TABLE {table}')
        conn.execute("DELETE FROM accounts WHERE system_role = 'landed_cost_clearing'")
        for column in m.ITEM_COLUMNS:
            conn.execute(f'ALTER TABLE items DROP COLUMN {column}')
        conn.execute("UPDATE alembic_version SET version_num='co0095'")
        conn.commit()
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                            "AND name != 'alembic_version'")]
        before = {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in names}
    _at(copy, 'co0098')
    with sqlite3.connect(copy) as conn:
        for name in names:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            if name == 'items':
                rows = [row[:-2] for row in rows]
            assert sorted(rows, key=repr) == before[name], name
        assert sorted(conn.execute('SELECT target_movement_id, amount_minor_units, measure FROM '
                                   'landed_cost_allocation_lines').fetchall()) == made
        assert conn.execute("SELECT count(*), min(kind), min(basis) FROM landed_cost_allocations").fetchone() == (
            1, 'receipt_shipping', 'quantity')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
