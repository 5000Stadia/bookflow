"""Every family that carries a unit price or cost keeps a sub-cent rate exact (R150).

The property, the same in every family: a line of 1,000 at 0.0125 is 12.50, and 12.5 at 3.459 is
43.24 (43.2375, half to even). Then an edit that keeps the lines (a memo), a void, and every
conversion the family has (estimate, work order and sales order to invoice, purchase order and
item receipt to bill, a memorized invoice entered) must re-validate and leave both amounts
unchanged. A reader of the rounded integer column alone would make the first line 1,000 x 0.01 =
10.00, so this is what proves every reader goes through the exact price.
"""
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
