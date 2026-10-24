"""Every family that carries a unit price or cost keeps a sub-cent rate exact (R150).

The property, the same in every family: a line of 1,000 at 0.0125 is 12.50, and 12.5 at 3.459 is
43.24 (43.2375, half to even). Then an edit that keeps the lines (a memo), a void, and every
conversion the family has (estimate, work order and sales order to invoice, purchase order and
item receipt to bill, a memorized invoice entered) must re-validate and leave both amounts
unchanged. A reader of the rounded integer column alone would make the first line 1,000 x 0.01 =
10.00, so this is what proves every reader goes through the exact price.
"""
import json

import pytest

from tests.test_bill_item_lines import books  # noqa: F401  (fixture)

pytestmark = pytest.mark.timeout(900)

LINES = (('1000', '0.0125', '12.50'), ('12.5', '3.459', '43.24'))
TOTAL = '55.74'


@pytest.fixture
def shop(books):  # noqa: F811
    c, co = books['client'], books['company']
    code = next(iter(books['tax_codes'].values()))
    non_taxable = next((row['id'] for row in books['run']('sales-tax-code query', {'limit': 50})['items']
                        if not row['taxable']), code)
    screw = c.item.create(company=co, name='SC Screw', type='non_inventory_part', sales_enabled=True,
                          description='Screw', price='0.0125', sales_tax_code_id=non_taxable,
                          income_account_id=books['income'], purchase_enabled=True,
                          purchase_description='Screw', cost='0.0125', expense_account_id=books['supplies'])['id']
    fuel = c.item.create(company=co, name='SC Fuel', type='non_inventory_part', sales_enabled=True,
                         description='Fuel', price='3.459', sales_tax_code_id=non_taxable,
                         income_account_id=books['income'], purchase_enabled=True,
                         purchase_description='Fuel', cost='3.459', expense_account_id=books['supplies'])['id']
    card = c.account.create(company=co, name='SC Card', type='credit_card')['id']
    stock = lambda name: c.item.create(company=co, name=name, type='inventory_part', description=name,  # noqa: E731
                                       price='0.0125', purchase_description=name, cost='0.0125',
                                       cogs_account_id=books['cogs'], income_account_id=books['income'])['id']
    return dict(books, screw=screw, fuel=fuel, card=card, stock_screw=stock('SC Stock Screw'),
                stock_fuel=stock('SC Stock Fuel'))


def priced(lines, rate_key, amount_key):
    return [(line[rate_key]['amount'], line[amount_key]['amount']) for line in lines]


EXPECTED = [(rate, amount) for _, rate, amount in LINES]


def sale_lines(shop, key='unit_price'):
    return [dict(item=shop['screw'], quantity='1000', **{key: '0.0125'}),
            dict(item=shop['fuel'], quantity='12.5', **{key: '3.459'})]


def purchase_items(shop, stocked=False):
    screw, fuel = ('stock_screw', 'stock_fuel') if stocked else ('screw', 'fuel')
    return [dict(item=shop[screw], quantity='1000', unit_cost='0.0125'),
            dict(item=shop[fuel], quantity='12.5', unit_cost='3.459')]


def profile_cost(line):
    from bookflow.core import unit_price
    profile = line['profile']
    return unit_price.text(unit_price.join(profile['unit_cost_minor_units'], profile.get('unit_cost_fraction')), 'USD')


def _sales(noun, selector, header):
    def post(shop):
        return shop['run'](f'{noun} post', dict(customer=shop['customer'], date='2017-03-03',
                                                lines=sale_lines(shop), **header(shop)), reason='Post')
    return dict(post=post, selector=selector, lines=lambda out: priced(out['revision']['lines'], 'unit_price', 'net'))


def _work(noun, selector, conversion):
    def post(shop):
        return shop['run'](f'{noun} create', dict(customer=shop['customer'], date='2017-03-03', title='Fuel and screws',
                                                  lines=sale_lines(shop)), reason='Quote')
    return dict(post=post, selector=selector, create=True, conversion=conversion,
                lines=lambda out: priced(out['revision']['lines'], 'unit_price', 'net'))


def _bill_lines(out):
    return priced(out['revision']['items'], 'unit_cost', 'amount')


def _money_out(noun, selector, funding):
    def post(shop):
        return shop['run'](f'{noun} post', dict(account=shop[funding], date='2017-03-03', amount=TOTAL,
                                                pay_to=dict(name_type='vendor', name_id=shop['vendor']),
                                                items=purchase_items(shop)), reason='Pay')
    # A check or card charge shows its item rows' captured facts; the exact cost is in them.
    return dict(post=post, selector=selector,
                lines=lambda out: [(profile_cost(line), line['amount']['amount']) for line in out['document']['items']])


FAMILIES = {
    'invoice': _sales('invoice', 'invoice', lambda shop: {}),
    'sales-receipt': _sales('sales-receipt', 'sales_receipt', lambda shop: dict(
        deposit_to=shop['bank'], payment_method=next(iter(shop['methods'].values())))),
    'credit-memo': _sales('credit-memo', 'credit_memo', lambda shop: {}),
    'estimate': _work('estimate', 'estimate', 'invoice'),
    'work-order': _work('work-order', 'work_order', 'invoice'),
    'sales-order': _work('sales-order', 'sales_order', 'invoice'),
    'bill': dict(post=lambda shop: shop['run']('bill post', dict(vendor=shop['vendor'], date='2017-03-03',
                                                                  items=purchase_items(shop)), reason='Bill'),
                 selector='bill', lines=_bill_lines),
    'check': _money_out('check', 'check', 'bank'),
    'card-charge': _money_out('card-charge', 'card_charge', 'card'),
    'item-receipt': dict(post=lambda shop: shop['run']('item-receipt post', dict(
                             vendor=shop['vendor'], date='2017-03-03', items=purchase_items(shop, stocked=True)), reason='Receive'),
                         selector='receipt', conversion='bill',
                         lines=lambda out: [(profile_cost(line), line['product_amount']['amount']) for line in out['items']]),
    'purchase-order': dict(post=lambda shop: shop['run']('purchase-order post', dict(
                               vendor=shop['vendor'], date='2017-03-03',
                               lines=[dict(item=shop['screw'], quantity='1000', rate='0.0125'),
                                      dict(item=shop['fuel'], quantity='12.5', rate='3.459')]), reason='Order'),
                           selector='purchase_order', conversion='bill',
                           lines=lambda out: priced(out['revision']['lines'], 'rate', 'amount')),
    'vendor-credit': dict(post=lambda shop: shop['run']('vendor-credit post', dict(
                              vendor=shop['vendor'], date='2017-03-03', items=purchase_items(shop)), reason='Credit'),
                          selector='credit', lines=lambda out: priced(out['revision']['items'], 'unit_cost', 'amount')),
}


def show(shop, noun, selector, identity):
    return shop['run'](f'{noun} show', {selector: identity})


def version(out):
    return out.get('version') or (out.get('document') or {}).get('version') or (out.get('header') or {}).get('version')


def identity_of(out):
    return out.get('id') or (out.get('document') or {}).get('id') or (out.get('header') or {}).get('id')


def convert(shop, noun, family, posted):
    """Run the family's conversion and return the converted document's priced lines."""
    run = shop['run']
    if family['conversion'] == 'invoice':
        shown = show(shop, noun, family['selector'], identity_of(posted))
        if noun == 'estimate':
            shown = run('estimate update', dict(estimate=identity_of(posted), expected_version=version(shown),
                                                status='accepted', decision_note='Signed'), reason='Accepted')
        made = run(f'{noun} invoice', {family['selector']: identity_of(posted), 'expected_version': version(shown),
                                       'conversion_key': f'{noun}-subcent', 'date': '2017-03-04'}, reason='Bill it')
        invoice = run('invoice show', {'invoice': made.get('id') or made['invoice']['id']})
        return priced(invoice['revision']['lines'], 'unit_price', 'net')
    if noun == 'purchase-order':
        made = run('bill post', dict(date='2017-03-05', purchase_order=identity_of(posted)), reason='Bill the order')
        return _bill_lines(made)
    # A receipt billed as received keeps its value as the bill line's amount; billed with the
    # vendor's unit cost, the line is quantity x that exact cost. Half of each, one way each.
    receipt = posted
    rates = dict(zip((line['id'] for line in receipt['items']), ('0.0125', '3.459')))
    halves = dict(zip((line['id'] for line in receipt['items']), ('500', '6.25')))
    made = run('bill post', dict(date='2017-03-05', vendor=shop['vendor'], receipts=[
        dict(receipt_line=line['id'], expected_receipt_version=version(receipt), quantity=halves[line['id']],
             unit_cost=rates[line['id']]) for line in receipt['items']]), reason='Bill half at cost')
    assert _bill_lines(made) == [('0.0125', '6.25'), ('3.459', '21.62')]  # 21.61875 -> 21.62
    rest = run('bill post', dict(date='2017-03-06', vendor=shop['vendor'], receipts=[
        dict(receipt_line=line['id'], expected_receipt_version=version(show(shop, noun, family['selector'], identity_of(receipt))),
             quantity=halves[line['id']]) for line in receipt['items']]), reason='Bill the rest as received')
    assert [line['amount']['amount'] for line in rest['revision']['items']] == ['6.25', '21.62']
    return EXPECTED


@pytest.mark.parametrize('noun', sorted(FAMILIES))
def test_a_sub_cent_rate_is_exact_through_post_edit_void_and_conversion(shop, noun):
    family = FAMILIES[noun]
    posted = family['post'](shop)
    assert family['lines'](posted) == EXPECTED, noun
    selector, identity = family['selector'], identity_of(posted)
    if family.get('conversion'):
        assert convert(shop, noun, family, posted) == EXPECTED, noun
        # The source re-reads exactly after it has been converted.
        assert family['lines'](show(shop, noun, selector, identity)) == EXPECTED, noun
        return
    current = version(show(shop, noun, selector, identity))
    edited = shop['run'](f'{noun} update', {selector: identity, 'memo': 'Memo only', 'expected_version': current},
                         reason='Memo')
    assert family['lines'](edited) == EXPECTED, noun
    assert family['lines'](show(shop, noun, selector, identity)) == EXPECTED, noun
    shop['run'](f'{noun} void', {selector: identity}, reason='Void')
    assert family['lines'](show(shop, noun, selector, identity)) == EXPECTED, noun


@pytest.mark.parametrize('noun', ['estimate', 'work-order', 'sales-order', 'item-receipt', 'purchase-order'])
def test_a_convertible_family_also_edits_and_voids_exactly(shop, noun):
    family = FAMILIES[noun]
    posted = family['post'](shop)
    selector, identity = family['selector'], identity_of(posted)
    current = version(show(shop, noun, selector, identity))
    edited = shop['run'](f'{noun} update', {selector: identity, 'memo': 'Memo only', 'expected_version': current},
                         reason='Memo')
    assert family['lines'](show(shop, noun, selector, identity)) == EXPECTED, (noun, edited.keys())
    if noun == 'work-order':
        return  # a work order is cancelled, never voided; its conversion is the invoice above
    current = version(show(shop, noun, selector, identity))
    shop['run'](f'{noun} void', {selector: identity, 'expected_version': current}, reason='Void')
    assert family['lines'](show(shop, noun, selector, identity)) == EXPECTED, noun


def test_a_memorized_invoice_enters_exactly(shop):
    made = shop['run']('memorized create', dict(name='Fuel and screws', command='invoice post', payload=dict(
        customer=shop['customer'], date='2017-03-03', lines=sale_lines(shop))), reason='Memorize')
    entered = shop['run']('memorized enter', dict(memorized=made['id'], date='2017-03-06'), reason='Enter it')
    invoice = shop['run']('invoice show', {'invoice': entered['entered'][0]['transaction_id']})
    assert priced(invoice['revision']['lines'], 'unit_price', 'net') == EXPECTED


def test_a_sub_cent_bill_cost_flows_exactly_into_the_average_and_cogs(shop):
    """1,000 bought at 0.0125 is 12.50 of stock and 3 at 0.0333 is 0.10 (0.0999); 400 sold carry
    400 x 12.60 / 1,003 = 5.0249 -> 5.02 of cost, and 7.58 stays on the shelf. A cost read as the
    rounded cent would give 10.03 of stock and 4.00 of cost."""
    from tests.test_bill_item_lines import _inventory_asset, _net
    run, asset = shop['run'], _inventory_asset(shop)
    run('bill post', dict(vendor=shop['vendor'], date='2017-03-03', items=[
        dict(item=shop['stock_screw'], quantity='1000', unit_cost='0.0125')]), reason='Buy screws')
    run('bill post', dict(vendor=shop['vendor'], date='2017-03-04', items=[
        dict(item=shop['stock_screw'], quantity='3', unit_cost='0.0333')]), reason='Three more')  # 0.0999 -> 0.10
    sold = run('invoice post', dict(customer=shop['customer'], date='2017-03-05', lines=[
        dict(item=shop['stock_screw'], quantity='400')]), reason='Sell screws')
    assert priced(sold['revision']['lines'], 'unit_price', 'net') == [('0.0125', '5.00')]
    net = _net(shop)
    # Average after both receipts: 12.60 / 1003; 400 of them cost 400 x 12.60 / 1003 = 5.0249 -> 5.02.
    assert net[shop['cogs']] == 502 and net[asset] == 1260 - 502


def _item_update(shop, item, **fields):
    current = shop['run']('item show', dict(item=item))
    return shop['run']('item update', dict(item=item, expected_version=current['version'], **fields), reason='Reprice')


def test_an_item_update_below_the_cent_is_a_change(shop):
    """2.50 -> 2.5025 and 0.0125 -> 0.0149 keep their cents, so only the fraction moves; the edit
    must still save, bump the version and name the field, alone or beside another field."""
    run, c, co = shop['run'], shop['client'], shop['company']
    whole = c.item.create(company=co, name='SC Whole', type='non_inventory_part', sales_enabled=True, description='Whole',
                          price='2.50', cost='1.25', income_account_id=shop['income'], purchase_enabled=True,
                          purchase_description='Whole', expense_account_id=shop['supplies'])
    before = run('item show', dict(item=whole['id']))
    out = _item_update(shop, whole['id'], price='2.5025', cost='1.2525')
    after = run('item show', dict(item=whole['id']))
    assert (after['price']['amount'], after['cost']['amount']) == ('2.5025', '1.2525')
    assert after['version'] == before['version'] + 1 and set(out['changed_fields']) >= {'price', 'cost'}
    out = _item_update(shop, shop['screw'], price='0.0149')
    assert run('item show', dict(item=shop['screw']))['price']['amount'] == '0.0149'
    assert 'price' in out['changed_fields']
    out = _item_update(shop, shop['fuel'], price='3.4591', description='Diesel')
    assert set(out['changed_fields']) >= {'price', 'description'}
    listed = c.run('audit list', dict(record_id=shop['fuel'], limit=5), company=co)['items'][0]
    entry = c.run('audit show', dict(event=listed['id']), company=co)['entries'][0]
    # The audit records the exact before and after; no raw fraction column leaks into it.
    assert (entry['before']['price']['amount'], entry['after']['price']['amount']) == ('3.459', '3.4591')
    assert entry['after']['description'] == 'Diesel'
    assert not [key for key in entry['before'] if key.endswith('_fraction')]


def test_an_assembly_counts_a_component_cost_below_half_a_cent(shop):
    """A kit of 1,000 bolts from stock at 0.0125, 1,000 packing at 0.0125 and 1,000 labels at
    0.004 costs 12.50 + 12.50 + 4.00 = 29.00; a label rounded alone to 0 cents would make it 25.00.
    A kit whose only cost is below half a cent is still buildable: 1,000 labels are 4.00."""
    from tests.test_assembly_builds import _buy, _kit, _part
    c, co = shop['client'], shop['company']
    cost_item = lambda name, cost: c.item.create(  # noqa: E731
        company=co, name=name, type='non_inventory_part', description=name, price='0.02', cost=cost,
        income_account_id=shop['income'], purchase_enabled=True, purchase_description=name,
        expense_account_id=shop['supplies'])['id']
    packing, label = cost_item('SC Packing', '0.0125'), cost_item('SC Label', '0.004')
    bolt = _part(shop, 'SC Bolt', '0.0125')
    _buy(shop, bolt, '5000', '0.0125', '2017-03-01')
    kit = _kit(shop, [(bolt, '1000'), (packing, '1000'), (label, '1000')])
    built = shop['run']('assembly build', dict(item=kit, quantity='1', date='2017-03-10'), reason='Build')
    assert built['assembly_cost']['amount'] == '29.00'
    labels = _kit(shop, [(label, '1000')], name='SC Label pack')
    built = shop['run']('assembly build', dict(item=labels, quantity='1', date='2017-03-10'), reason='Build')
    assert built['assembly_cost']['amount'] == '4.00'


def test_the_invoice_window_keeps_a_sub_cent_price_basis(shop):
    from bookflow.adapters.workbench import sales
    shop['run']('company update', dict(enable_price_levels=True), reason='Levels')
    shop['run']('price-level create', dict(name='SC Ten off', kind='fixed_percent', percent='-10'), reason='Level')
    sold = shop['run']('invoice post', dict(customer=shop['customer'], date='2017-03-05', lines=[
        dict(item=shop['screw'], quantity='1000', price_level='SC Ten off', price_basis_amount='0.0333')]), reason='Sell')
    shown = shop['run']('invoice show', {'invoice': sold['id']})
    assert sales.editable_values(shown)['lines'][0]['price_basis_amount'] == '0.0333'


def test_averages_show_at_the_price_scale(shop):
    """1,000 at 0.0125 bought and sold: every average reads 0.0125, not 0.01."""
    run = shop['run']
    run('bill post', dict(vendor=shop['vendor'], date='2017-03-03', items=[
        dict(item=shop['stock_screw'], quantity='1000', unit_cost='0.0125')]), reason='Buy')
    run('invoice post', dict(customer=shop['customer'], date='2017-03-05', lines=[
        dict(item=shop['screw'], quantity='1000')]), reason='Sell')
    period = dict(date_from='2017-01-01', date_to='2017-12-31', limit=50)
    row = lambda rows, item: next(r for r in rows if r['item_id'] == item)  # noqa: E731
    assert row(run('report sales-by-item', period)['rows'], shop['screw'])['average_price']['amount'] == '0.0125'
    assert row(run('report purchases-by-item', period)['rows'], shop['stock_screw'])['average_cost']['amount'] == '0.0125'
    valuation = run('report inventory-valuation', dict(as_of='2017-12-31', limit=50))['rows']
    assert row(valuation, shop['stock_screw'])['average_cost']['amount'] == '0.0125'
    assert run('item show', dict(item=shop['stock_screw']))['average_cost']['amount'] == '0.0125'


def test_a_purchase_order_rate_takes_the_price_scale_and_an_exchange_rate_does_not():
    from tests.test_numeric_entry_metadata import children, descriptors
    price = {'currency': 'company', 'extra_places': 3}
    assert children('purchase-order post', 'lines')['rate']['math'] == price
    assert descriptors('time-activity create')['rate']['math'] == price
    assert descriptors('journal post')['rate']['math'] == {'scale': 18}


def _short_item(shop, name, cost):
    return shop['client'].item.create(company=shop['company'], name=name, type='inventory_part', description=name,
                                      price='0.02', purchase_description=name, cost=cost,
                                      cogs_account_id=shop['cogs'], income_account_id=shop['income'])['id']


@pytest.mark.parametrize('cost, provisional', [('0.0125', 1250), ('0.004', 400)])
def test_a_short_sale_takes_its_provisional_cost_exactly(shop, cost, provisional):
    """Selling 1,000 never stocked at a 0.0125 purchase cost carries 12.50 of provisional cost, and
    at 0.004 it carries 4.00 with no 'no purchase cost' warning: the exact unit cost times the
    shortfall, rounded once. Read as whole cents per unit they would be 10.00 and 0.00."""
    from tests.test_negative_stock import _bill, _net, _ties
    item = _short_item(shop, 'SC Short', cost)
    sold = shop['run']('invoice post', dict(customer=shop['customer'], date='2017-03-05',
                                            lines=[dict(item=item, quantity='1000')]), reason='Sell short')
    assert not [w for w in sold.get('warnings', []) if 'no purchase cost' in w]
    assert _net(shop, shop['cogs']) == provisional
    _bill(shop, item, '2017-03-06', '1000', cost)
    assert _net(shop, shop['cogs']) == provisional  # the true-up at the receipt moves nothing
    _ties(shop)


def test_an_old_style_fallback_row_replays_exactly_as_before(shop):
    """A whole-cent fallback stores no fraction, and a row with no fraction column at all (as every
    movement written before co0091) gives the provisional the old formula gave."""
    from bookflow.company import inventory_costing
    from bookflow.core.exact import round_ratio_half_even
    from tests.test_negative_stock import _db, _net
    item = _short_item(shop, 'SC Whole short', '1.80')
    shop['run']('invoice post', dict(customer=shop['customer'], date='2017-03-05',
                                     lines=[dict(item=item, quantity='3.5')]), reason='Sell short')
    assert _net(shop, shop['cogs']) == 630
    with _db(shop) as db:
        db.row_factory = __import__('sqlite3').Row
        rows = [dict(row) for row in db.execute('SELECT * FROM inventory_movements WHERE item_id=?', (item,))]
    issue = next(row for row in rows if row['kind'] == 'issue')
    assert issue['fallback_unit_cost_minor_units'] == 180 and issue['fallback_unit_cost_fraction'] is None
    old = [{k: v for k, v in row.items() if k != 'fallback_unit_cost_fraction'} for row in rows]
    assert inventory_costing.replay(old).targets == inventory_costing.replay(rows).targets
    assert inventory_costing.replay(old).targets[issue['id']] == -round_ratio_half_even(180 * 3_500_000, 1_000_000)


def test_a_price_level_and_a_vendor_cost_change_below_the_cent(shop):
    run = shop['run']
    run('company update', dict(enable_price_levels=True), reason='Levels')
    level = run('price-level create', dict(name='SC Contract', kind='per_item', items=[
        dict(item_id=shop['screw'], price='0.0115', adjustment_basis='standard_price')]), reason='Level')
    shown = run('price-level show', dict(price_level=level['id']))
    run('price-level update', dict(price_level=level['id'], expected_version=shown['version'], items=[
        dict(item_id=shop['screw'], price='0.0124', adjustment_basis='standard_price')]), reason='Level')
    after = run('price-level show', dict(price_level=level['id']))
    assert [row['price']['amount'] for row in after['items'] if row['active']] == ['0.0124']
    _item_update(shop, shop['screw'], vendor_profiles=[dict(vendor_id=shop['vendor'], preferred_rank=1, purchase_cost='0.0115')])
    _item_update(shop, shop['screw'], vendor_profiles=[dict(vendor_id=shop['vendor'], preferred_rank=1, purchase_cost='0.0124')])
    profiles = run('item show', dict(item=shop['screw']))['vendor_profiles']
    assert [row['purchase_cost']['amount'] for row in profiles if row['active']] == ['0.0124']
