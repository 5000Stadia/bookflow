"""R103: assembly builds consume components at average cost and receive the assembly at their sum.

A water heater kit is one tank and two valves. Valves are bought at two prices, kits are built, sold,
voided, rebuilt, built short (refused, or marked pending and finalized later), built across two
inventory asset accounts, recosted by a purchase entered behind the build, and refused in a closed
period. Throughout, stock valuation ties to the balance sheet (the report refuses otherwise).
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_asset, _net  # noqa: F401


def _part(books, name, cost, asset=None):
    fields = dict(name=name, type='inventory_part', description=name, price='1.00', purchase_description=name,
                  cost=cost, cogs_account_id=books['cogs'], income_account_id=books['income'])
    if asset:
        fields['asset_account_id'] = asset
    return books['client'].item.create(company=books['company'], **fields)['id']


def _kit(books, members, name='Water Heater Kit', asset=None):
    fields = dict(name=name, type='inventory_assembly', description=name, price='500.00',
                  purchase_description=name, cost='0.00', cogs_account_id=books['cogs'],
                  income_account_id=books['income'],
                  members=[dict(component_item_id=item, quantity=quantity) for item, quantity in members])
    if asset:
        fields['asset_account_id'] = asset
    return books['client'].item.create(company=books['company'], **fields)['id']


def _buy(books, item, quantity, cost, date):
    books['run']('bill post', dict(vendor=books['vendor'], date=date,
                                   items=[dict(item=item, quantity=quantity, unit_cost=cost)]), reason='Stock in')


def _stock(books, item, as_of='2017-12-31'):
    out = books['run']('report inventory-valuation', dict(as_of=as_of, limit=200))
    row = next(row for row in out['rows'] if row['item_id'] == item)
    return row['quantity_on_hand'], row['asset_value']['minor_units']


@pytest.fixture
def kit(books):
    tank = _part(books, 'Heater Tank', '100.00')
    valve = _part(books, 'Relief Valve', '10.00')
    kit = _kit(books, [(tank, '1'), (valve, '2')])
    _buy(books, tank, '5', '100.00', '2017-03-01')
    _buy(books, valve, '4', '10.00', '2017-03-01')
    _buy(books, valve, '6', '12.50', '2017-03-02')      # two prices: average 11.50
    return dict(books=books, tank=tank, valve=valve, kit=kit)


def test_build_then_sell_the_kit_cogs_is_the_components_cost(kit):
    books, run = kit['books'], kit['books']['run']
    asset = _inventory_asset(books)
    before = _net(books)
    preview = run('assembly build', dict(item=kit['kit'], quantity='3', date='2017-03-10', memo='Morning batch'),
                  reason='We built 3 water heater kits today', dry_run=True)
    assert preview['status'] == 'will_build' and preview['max_buildable'] == '5' and not preview['shortfall']
    by_name = {row['item_name']: row for row in preview['components']}
    assert by_name['Heater Tank']['quantity_needed'] == '3' and by_name['Heater Tank']['quantity_on_hand'] == '5'
    assert by_name['Relief Valve']['quantity_needed'] == '6' and by_name['Relief Valve']['max_buildable'] == '5'
    # 3 tanks at 100.00 and 6 valves at the 11.50 average: 300.00 + 69.00.
    assert preview['assembly_cost']['minor_units'] == 36900 and preview['unit_cost']['amount'] == '123.00'
    assert preview['posts_to_ledger'] is False

    done = run('assembly build', dict(item=kit['kit'], quantity='3', date='2017-03-10', memo='Morning batch'),
               reason='We built 3 water heater kits today')
    assert done['status'] == 'built' and done['version'] == 1 and done['number']
    assert _net(books) == before            # one inventory asset account: the ledger does not move
    assert _stock(books, kit['tank']) == ('2', 20000)
    assert _stock(books, kit['valve']) == ('4', 4600)
    assert _stock(books, kit['kit']) == ('3', 36900)

    run('invoice post', dict(date='2017-03-15', customer=books['customer'],
                             lines=[dict(item=kit['kit'], quantity='2')]), reason='Sold two kits')
    sold = _net(books, date_from='2017-03-15', date_to='2017-03-15')
    assert sold[books['cogs']] == 24600 and sold[asset] == -24600
    assert _stock(books, kit['kit']) == ('1', 12300)

    shown = run('assembly show', {'build': done['build_id']})
    assert shown['assembly_cost']['minor_units'] == 36900 and shown['status'] == 'built'
    assert run('assembly show', {'build': done['number']})['build_id'] == done['build_id']
    listed = run('assembly list', {})
    assert [row['build_id'] for row in listed['rows']] == [done['build_id']]

    # The journal editor cannot touch half of a build.
    with pytest.raises(BookflowError) as error:
        run('journal void', {'journal': done['transaction_id']}, reason='Try')
    assert 'assembly void' in error.value.message


def test_void_and_rebuild_and_edit_restate_exactly(kit):
    books, run = kit['books'], kit['books']['run']
    done = run('assembly build', dict(item=kit['kit'], quantity='2', date='2017-03-10'), reason='Build')
    voided = run('assembly void', {'build': done['build_id'], 'expected_version': 1}, reason='Wrong count')
    assert voided['status'] == 'voided' and voided['version'] == 2
    assert _stock(books, kit['tank']) == ('5', 50000)
    assert _stock(books, kit['valve']) == ('10', 11500)
    assert _stock(books, kit['kit']) == ('0', 0)
    with pytest.raises(BookflowError) as error:
        run('assembly void', {'build': done['build_id']}, reason='Again')
    assert error.value.code == 'E_VALIDATION'

    again = run('assembly build', dict(item=kit['kit'], quantity='4', date='2017-03-11'), reason='Rebuild')
    assert _stock(books, kit['kit']) == ('4', 49200)
    with pytest.raises(BookflowError) as error:
        run('assembly update', {'build': again['build_id'], 'expected_version': 9, 'quantity': '1'}, reason='Stale')
    assert error.value.code == 'E_VERSION_CONFLICT'
    edited = run('assembly update', {'build': again['build_id'], 'quantity': '5'}, reason='Five, not four')
    assert edited['version'] == 2 and edited['quantity'] == '5' and edited['assembly_cost']['minor_units'] == 61500
    assert _stock(books, kit['kit']) == ('5', 61500)
    assert _stock(books, kit['tank']) == ('0', 0) and _stock(books, kit['valve']) == ('0', 0)

    deleted = run('assembly delete', {'build': again['build_id']}, reason='Never happened')
    assert deleted['status'] == 'deleted'
    assert _stock(books, kit['kit']) == ('0', 0) and _stock(books, kit['tank']) == ('5', 50000)
    assert [row['status'] for row in run('assembly list', {})['rows']] == ['voided']
    assert len(run('assembly show', {'build': again['build_id']})['history']) == 3


def test_short_components_are_refused_or_marked_pending_and_finalized(kit):
    books, run = kit['books'], kit['books']['run']
    with pytest.raises(BookflowError) as error:
        run('assembly build', dict(item=kit['kit'], quantity='6', date='2017-03-10'), reason='Too many')
    assert error.value.code == 'E_VALIDATION' and error.value.details['max_buildable'] == '5'
    assert {row['item_name'] for row in error.value.details['shortages']} == {'Heater Tank', 'Relief Valve'}

    pending = run('assembly build', dict(item=kit['kit'], quantity='6', date='2017-03-10', pending=True),
                  reason='Waiting on parts')
    assert pending['status'] == 'pending' and pending['number'] is None and pending['shortfall']
    assert _stock(books, kit['kit']) == ('0', 0)
    report = run('assembly list', {'status': 'pending'})
    assert report['count'] == 1 and report['reserved'] == {kit['tank']: '6', kit['valve']: '12'}
    with pytest.raises(BookflowError) as error:
        run('assembly finalize', {'build': pending['build_id']}, reason='Not yet')
    assert error.value.details['reason'] == 'insufficient_components'

    _buy(books, kit['tank'], '1', '106.00', '2017-03-12')
    _buy(books, kit['valve'], '2', '11.50', '2017-03-12')
    final = run('assembly finalize', {'build': pending['build_id'], 'date': '2017-03-12'}, reason='Parts arrived')
    assert final['status'] == 'built' and final['version'] == 2 and final['number']
    # 5 tanks at 100.00 and one at 106.00, 12 valves at 11.50.
    assert final['assembly_cost']['minor_units'] == 60600 + 13800
    assert _stock(books, kit['kit']) == ('6', 74400)
    assert run('assembly list', {'status': 'pending'})['count'] == 0


def test_a_non_stock_component_moves_the_ledger_balanced(books):
    """One inventory asset account per company (a unique system role), so the build moves the general
    ledger only when it adds a non-stock component's cost: the anchor credits that item's expense account."""
    run = books['run']
    asset = _inventory_asset(books)
    tank = _part(books, 'Heater Tank', '100.00')
    labor = books['client'].item.create(company=books['company'], name='Assembly Labor', type='service',
                                        sales_enabled=False, purchase_enabled=True, purchase_description='Labor',
                                        cost='25.00', expense_account_id=books['subs'])['id']
    kit = _kit(books, [(tank, '1'), (labor, '0.5')])
    _buy(books, tank, '2', '100.00', '2017-03-01')
    done = run('assembly build', dict(item=kit, quantity='2', date='2017-03-10'), reason='Build')
    assert done['posts_to_ledger'] is True and done['assembly_cost']['minor_units'] == 22500
    labor_row = next(row for row in done['components'] if row['kind'] == 'cost')
    assert labor_row['cost']['minor_units'] == 2500 and labor_row['account_id'] == books['subs']
    assert _net(books, date_from='2017-03-10', date_to='2017-03-10') == {asset: 2500, books['subs']: -2500}
    assert _stock(books, kit) == ('2', 22500)


def test_a_purchase_entered_behind_the_build_recosts_the_build_and_the_assembly_sale(kit):
    books, run = kit['books'], kit['books']['run']
    run('assembly build', dict(item=kit['kit'], quantity='2', date='2017-03-10'), reason='Build')
    run('invoice post', dict(date='2017-03-20', customer=books['customer'],
                             lines=[dict(item=kit['kit'], quantity='2')]), reason='Sold both')
    cogs_before = _net(books)[books['cogs']]
    assert cogs_before == 24600
    # A bill for 10 more valves at 8.50, dated before the build, moves the valve average to 10.00.
    _buy(books, kit['valve'], '10', '8.50', '2017-03-05')
    # 2 tanks at 100.00 + 4 valves at (4x10 + 6x12.50 + 10x8.50)/20 = 10.00: 240.00, all sold.
    assert _net(books)[books['cogs']] == 24000
    assert _stock(books, kit['kit']) == ('0', 0)
    assert _stock(books, kit['valve']) == ('16', 16000)
    # The build's own cost moved with it.
    build = run('assembly list', {})['rows'][0]
    assert build['assembly_cost']['minor_units'] == 24000
    # A backdated adjustment reaches it the same way.
    run('inventory adjust', dict(item=kit['tank'], date='2017-03-02', value_change='50.00',
                                 adjustment_account=books['cogs']), reason='Freight on tanks')
    assert _stock(books, kit['kit']) == ('0', 0) and _stock(books, kit['tank']) == ('3', 30000 + 3000)


def test_a_change_that_would_leave_a_build_short_is_refused(kit):
    books, run = kit['books'], kit['books']['run']
    run('assembly build', dict(item=kit['kit'], quantity='5', date='2017-03-10'), reason='All the tanks')
    with pytest.raises(BookflowError) as error:
        run('invoice post', dict(date='2017-03-05', customer=books['customer'],
                                 lines=[dict(item=kit['tank'], quantity='1')]), reason='Backdated sale')
    assert error.value.details['reason'] == 'build_left_short'


def test_nested_assembly_is_consumed_as_stock_never_built(kit):
    books, run = kit['books'], kit['books']['run']
    pallet = _kit(books, [(kit['kit'], '2')], name='Kit Pallet')
    with pytest.raises(BookflowError) as error:
        run('assembly build', dict(item=pallet, quantity='1', date='2017-03-10'), reason='No kits yet')
    assert error.value.details['max_buildable'] == '0'
    run('assembly build', dict(item=kit['kit'], quantity='2', date='2017-03-10'), reason='Kits')
    done = run('assembly build', dict(item=pallet, quantity='1', date='2017-03-11'), reason='Pallet')
    assert done['assembly_cost']['minor_units'] == 24600 and _stock(books, kit['kit']) == ('0', 0)
    # A cheaper valve bought before both builds flows through the kit into the pallet.
    _buy(books, kit['valve'], '10', '8.50', '2017-03-05')
    assert _stock(books, pallet) == ('1', 24000)


def test_a_closed_period_is_refused(kit):
    books, run = kit['books'], kit['books']['run']
    done = run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-10'), reason='Build')
    books['run']('company update', {'closing_date': '2017-03-31'})
    for name, body in (('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-20')),
                       ('assembly void', {'build': done['build_id']}),
                       ('assembly update', {'build': done['build_id'], 'date': '2017-04-02'})):
        with pytest.raises(BookflowError) as error:
            run(name, body, reason='Closed')
        assert error.value.code == 'E_PERIOD_CLOSED', name
    run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-04-02'), reason='Open period')


def _members(books, item, members):
    run = books['run']
    current = run('item show', {'item': item})
    run('item update', {'item': item, 'expected_version': current['version'],
                        'members': [dict(component_item_id=part, quantity=quantity) for part, quantity in members]},
        reason='New bill of materials')


def test_a_build_a_later_build_consumed_can_be_edited_but_not_left_short(kit):
    books, run = kit['books'], kit['books']['run']
    pallet = _kit(books, [(kit['kit'], '2')], name='Kit Pallet')
    first = run('assembly build', dict(item=kit['kit'], quantity='2', date='2017-03-10'), reason='Kits')
    run('assembly build', dict(item=pallet, quantity='1', date='2017-03-11'), reason='Pallet')
    memo = run('assembly update', {'build': first['build_id'], 'memo': 'Morning shift'}, reason='Memo only')
    assert memo['memo'] == 'Morning shift' and memo['quantity'] == '2'
    assert run('assembly update', {'build': first['build_id'], 'quantity': '3'}, reason='Three')['quantity'] == '3'
    assert run('assembly update', {'build': first['build_id'], 'quantity': '4'}, reason='Four')['quantity'] == '4'
    assert _stock(books, kit['kit']) == ('2', 24600) and _stock(books, pallet) == ('1', 24600)
    with pytest.raises(BookflowError) as error:
        run('assembly update', {'build': first['build_id'], 'quantity': '1'}, reason='One')
    assert error.value.details['reason'] == 'build_left_short'


def test_show_after_a_component_recost_adds_up_to_the_assembly_cost(kit):
    books, run = kit['books'], kit['books']['run']
    done = run('assembly build', dict(item=kit['kit'], quantity='2', date='2017-03-10'), reason='Build')
    _buy(books, kit['valve'], '10', '8.50', '2017-03-05')
    shown = run('assembly show', {'build': done['build_id']})
    assert shown['assembly_cost']['minor_units'] == 24000
    assert sum(row['cost']['minor_units'] for row in shown['components']) == 24000


def test_a_cheap_component_rounding_to_nothing_still_builds_and_a_worthless_one_is_refused(kit):
    books, run = kit['books'], kit['books']['run']
    washer = _part(books, 'Washer', '0.01')
    run('inventory adjust', dict(item=washer, date='2017-03-01', quantity_change='1000', value_change='4.00',
                                 adjustment_account=books['cogs']), reason='Box of washers')
    _members(books, kit['kit'], [(kit['tank'], '1'), (washer, '1')])
    done = run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-10'), reason='One kit')
    assert done['assembly_cost']['minor_units'] == 10000
    assert _stock(books, washer) == ('999', 400) and _stock(books, kit['kit']) == ('1', 10000)
    free = _part(books, 'Free Gasket', '0.01')
    run('bill post', dict(vendor=books['vendor'], date='2017-03-01',
                          items=[dict(item=free, quantity='5', unit_cost='0.00')]), reason='Samples')
    _members(books, kit['kit'], [(kit['tank'], '1'), (free, '1')])
    with pytest.raises(BookflowError) as error:
        run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-10'), reason='Kit with a sample')
    assert error.value.details['reason'] == 'component_without_value' and 'inventory adjust' in error.value.message


def test_a_build_that_would_close_a_cycle_through_history_is_refused(kit):
    books, run = kit['books'], kit['books']['run']
    pallet = _kit(books, [(kit['kit'], '1')], name='Kit Pallet')
    run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-10'), reason='Kit')
    run('assembly build', dict(item=pallet, quantity='1', date='2017-03-11'), reason='Pallet from the kit')
    _members(books, pallet, [(kit['tank'], '1')])
    _members(books, kit['kit'], [(pallet, '1')])
    with pytest.raises(BookflowError) as error:
        run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-12'), reason='Kit from the pallet')
    assert error.value.code == 'E_VALIDATION' and error.value.details['reason'] == 'assembly_cycle'
    _buy(books, kit['valve'], '10', '8.50', '2017-03-05')   # a backdated bill still recosts cleanly
    assert _stock(books, pallet) == ('1', 12000)


def test_register_update_on_a_build_points_at_the_assembly_commands(kit):
    books, run = kit['books'], kit['books']['run']
    done = run('assembly build', dict(item=kit['kit'], quantity='1', date='2017-03-10'), reason='Build')
    asset = _inventory_asset(books)
    with pytest.raises(BookflowError) as error:
        run('register update', {'journal': done['transaction_id'], 'expected_version': 1, 'account': asset,
                                'date': '2017-03-10', 'direction': 'increase', 'amount': '1.00',
                                'category': books['cogs'], 'selected_line_id': done['transaction_id']},
            reason='Try the register')
    assert 'assembly update' in error.value.message
