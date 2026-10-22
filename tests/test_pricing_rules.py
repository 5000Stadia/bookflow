"""Price rules (R110): conditions, quantity breaks, dates, priority against lowest price, rounding, capture.

The money, all by hand. PR Fitting sells at 20.00 and costs 12.00; PR Valve sells at 12.34.

    10% off 20.00 = 18.00; 5% off = 19.00; 15% off = 17.00; 2.00 off = 18.00; 25% over cost 12.00 = 15.00.
    Breaks 10+ at 5%, 50+ at 10%: 1-9 at 20.00, 10-49 at 19.00, 50+ at 18.00. 50 x 18.00 = 900.00.
    10% off 12.34 = 11.106: to the cent 11.11 (half to even of 1110.6); a discount rounds its .99/.95 ending
    down, to 10.99 and 10.95; nearest whole unit 11.00. 10% over 12.34 = 13.574 rounds up to 13.99.
    A price level 20% off gives 16.00, below every rule: lowest price takes it, priority takes the first rule.
"""
import time

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY

pytestmark = pytest.mark.timeout(900)


def write(client, command, reason='Pricing', **data):
    return client.run(command, data, company=COMPANY, reason=reason)


def read(client, command, **data):
    return client.run(command, data, company=COMPANY)


@pytest.fixture
def shop(client):
    income = client.account.create(name='PR income', type='income', company=COMPANY)['id']
    code = next(row['id'] for row in read(client, 'sales-tax-code list')['items'] if not row['taxable'])
    fittings = write(client, 'item-category create', name='PR Pipe fittings')['id']
    item = lambda name, price, **kw: write(client, 'item create', name=name, type='service', sales_enabled=True, description=name,  # noqa: E731
                                           income_account_id=income, price=price, sales_tax_code_id=code, **kw)['id']
    expense = client.account.create(name='PR costs', type='cost_of_goods_sold', company=COMPANY)['id']
    fitting = item('PR Fitting', '20.00', cost='12.00', category_id=fittings, purchase_enabled=True,
                   purchase_description='Fitting', expense_account_id=expense)
    valve = item('PR Valve', '12.34')
    wholesale = write(client, 'customer-type create', name='PR Wholesale')['id']
    acme = write(client, 'customer create', name='PR Acme')['id']
    job = write(client, 'customer create', name='Warehouse', parent_id=acme)['id']
    beta = write(client, 'customer create', name='PR Beta', customer_type_id=wholesale)['id']
    gamma = write(client, 'customer create', name='PR Gamma')['id']
    write(client, 'company update', use_classes=True)
    east = write(client, 'class create', name='PR East')['id']
    person = write(client, 'employee create', name='PR Rep Person')['id']
    rep = write(client, 'sales-rep create', name='PR Rep', initials='PRR', name_type='employee', name_id=person)['id']
    return dict(fitting=fitting, valve=valve, category=fittings, wholesale=wholesale, acme=acme, job=job, beta=beta,
                gamma=gamma, east=east, rep=rep)


def price(client, item, customer, quantity='1', date='2026-11-02', **extra):
    out = read(client, 'price-rule preview', item=item, customer=customer, quantity=quantity, date=date, **extra)
    return out['unit_price'], (out['rule'] or {}).get('label')


def rule(client, name, **fields):
    return write(client, 'price-rule create', name=name, **fields)


def off(client, made):
    write(client, 'price-rule deactivate', rule=made['id'], expected_version=made['version'])


def invoice(client, customer, lines, date='2026-11-02', **extra):
    return write(client, 'invoice post', date=date, customer=customer, lines=lines, **extra)


def test_each_condition_kind_prices_only_what_it_names(client, shop):
    s = shop
    cases = [
        (dict(items=['PR Fitting'], value='10'), [((s['fitting'], s['gamma']), {}, '18.00'),
                                                  ((s['valve'], s['gamma']), {}, '12.34')]),
        (dict(item_categories=['PR Pipe fittings'], value='5'), [((s['fitting'], s['gamma']), {}, '19.00'),
                                                                 ((s['valve'], s['gamma']), {}, '12.34')]),
        # A customer covers its jobs.
        (dict(customers=['PR Acme'], kind='amount', value='2.00'), [((s['fitting'], 'PR Acme:Warehouse'), {}, '18.00'),
                                                                     ((s['fitting'], s['beta']), {}, '20.00')]),
        (dict(customer_types=['PR Wholesale'], value='15'), [((s['fitting'], s['beta']), {}, '17.00'),
                                                             ((s['fitting'], s['gamma']), {}, '20.00')]),
        (dict(classes=['PR East'], kind='amount', value='1'), [((s['fitting'], s['gamma']), {'class_id': 'PR East'}, '19.00'),
                                                               ((s['fitting'], s['gamma']), {}, '20.00')]),
        (dict(sales_reps=['PRR'], kind='amount', value='1'), [((s['fitting'], s['gamma']), {'sales_rep': s['rep']}, '19.00'),
                                                              ((s['fitting'], s['gamma']), {}, '20.00')]),
        (dict(min_quantity='10', value='10'), [((s['fitting'], s['gamma']), {'quantity': '10'}, '18.00'),
                                               ((s['fitting'], s['gamma']), {'quantity': '9'}, '20.00')]),
        # From cost, up: a markup.
        (dict(items=['PR Fitting'], basis='cost', direction='up', value='25'), [((s['fitting'], s['gamma']), {}, '15.00')]),
    ]
    for index, (fields, checks) in enumerate(cases):
        made = rule(client, f'PR condition {index}', **fields)
        for (item, customer), extra, expected in checks:
            got, by = price(client, item, customer, **extra)
            assert got == expected, (fields, extra, got)
            assert (by == made['name']) == (expected not in ('20.00', '12.34')), (fields, by)
        off(client, made)
    shown = read(client, 'price-rule show', rule='PR condition 2')
    assert shown['customers'] == [{'id': s['acme'], 'label': 'PR Acme'}] and not shown['active']
    assert shown['summary'] == '2.00 off sales price for customers PR Acme; rounded to the cent'
    # The rule a line uses is on the posted invoice line.
    made = rule(client, 'PR Acme fittings', customers=['PR Acme'], item_categories=['PR Pipe fittings'], value='10',
                date_to='2026-12-31')
    posted = invoice(client, s['job'], [dict(item=s['fitting'], quantity='2'), dict(item=s['valve'], quantity='1')])
    first, second = posted['revision']['lines']
    assert (first['unit_price']['amount'], first['net']['amount']) == ('18.00', '36.00')
    captured = first['item_snapshot']['pricing_rule']
    assert (captured['id'], captured['version'], captured['price_minor_units'], captured['base_minor_units']) == (made['id'], 1, 1800, 2000)
    assert captured['summary'] == 'price from rule PR Acme fittings: 10% off sales price; rounded to the cent'
    assert first['item_snapshot']['origins']['unit_price'] == {'kind': 'default', 'source_id': made['id']}
    assert 'pricing_rule' not in second['item_snapshot'] and second['unit_price']['amount'] == '12.34'
    # An explicit price is the person's, not the rule's.
    typed = invoice(client, s['job'], [dict(item=s['fitting'], quantity='1', unit_price='19.50')])
    assert 'pricing_rule' not in typed['revision']['lines'][0]['item_snapshot']


def test_quantity_breaks_price_by_quantity(client, shop):
    rule(client, 'PR volume', items=['PR Fitting'], breaks=[dict(min_quantity='10', value='5'), dict(min_quantity='50', value='10')])
    got = {q: price(client, shop['fitting'], shop['gamma'], quantity=q)[0] for q in ('1', '9', '10', '49', '50', '500')}
    assert got == {'1': '20.00', '9': '20.00', '10': '19.00', '49': '19.00', '50': '18.00', '500': '18.00'}
    posted = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='50')])
    line = posted['revision']['lines'][0]
    assert (line['unit_price']['amount'], line['net']['amount']) == ('18.00', '900.00')
    assert line['item_snapshot']['pricing_rule']['value'] == 10_000_000
    shown = read(client, 'price-rule show', rule='PR volume')
    assert shown['breaks'] == [{'min_quantity': '10', 'value': '5'}, {'min_quantity': '50', 'value': '10'}]
    assert shown['summary'].startswith('10+ at 5% off; 50+ at 10% off sales price for items PR Fitting')
    with pytest.raises(BookflowError) as e:
        rule(client, 'PR bad breaks', breaks=[dict(min_quantity='50', value='5'), dict(min_quantity='10', value='10')])
    assert e.value.code == 'E_VALIDATION'


def test_a_date_window_holds_inside_and_not_outside(client, shop):
    rule(client, 'PR December', items=['PR Fitting'], value='10', date_from='2026-10-01', date_to='2026-12-31')
    got = {d: price(client, shop['fitting'], shop['gamma'], date=d)[0]
           for d in ('2026-09-30', '2026-10-01', '2026-12-31', '2027-01-01')}
    assert got == {'2026-09-30': '20.00', '2026-10-01': '18.00', '2026-12-31': '18.00', '2027-01-01': '20.00'}
    inside = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='1')], date='2026-12-31')
    outside = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='1')], date='2027-01-01')
    assert (inside['total_minor_units'], outside['total_minor_units']) == (1800, 2000)


def test_priority_against_lowest_price_with_a_price_level(client, shop):
    first = rule(client, 'PR first', items=['PR Fitting'], value='5', priority=1)
    rule(client, 'PR second', items=['PR Fitting'], value='10', priority=2)
    assert read(client, 'price-rule preferences')['conflict_policy'] == 'lowest_price'
    assert price(client, shop['fitting'], shop['gamma']) == ('18.00', 'PR second')
    write(client, 'price-rule set-preferences', conflict_policy='priority')
    assert price(client, shop['fitting'], shop['gamma']) == ('19.00', 'PR first')
    # A price level and rules together.
    write(client, 'company update', enable_price_levels=True)
    level = write(client, 'price-level create', name='PR Level 20', kind='fixed_percent', percent='-20')['id']
    write(client, 'customer update', customer=shop['beta'], price_level_id=level, expected_version=1)
    assert price(client, shop['fitting'], shop['beta']) == ('19.00', 'PR first')
    by_priority = invoice(client, shop['beta'], [dict(item=shop['fitting'], quantity='1')])
    assert by_priority['revision']['lines'][0]['item_snapshot']['pricing_rule']['conflict_policy'] == 'priority'
    write(client, 'price-rule set-preferences', conflict_policy='lowest_price')
    preview = read(client, 'price-rule preview', item=shop['fitting'], customer=shop['beta'], date='2026-11-02')
    assert (preview['unit_price'], preview['source'], preview['note']) == ('16.00', 'price_level', 'price from price level PR Level 20')
    assert [(c['source'], c['unit_price'], c['chosen']) for c in preview['candidates']] == [
        ('item', '20.00', False), ('price_level', '16.00', True), ('rule', '19.00', False), ('rule', '18.00', False)]
    by_level = invoice(client, shop['beta'], [dict(item=shop['fitting'], quantity='1')])
    line = by_level['revision']['lines'][0]
    assert line['unit_price']['amount'] == '16.00' and 'pricing_rule' not in line['item_snapshot'], line['item_snapshot'].get('pricing_rule')
    # A rule below the level wins under lowest price.
    write(client, 'price-rule update', rule=first['id'], expected_version=1, value='25')
    assert price(client, shop['fitting'], shop['beta']) == ('15.00', 'PR first')
    # The posted invoices kept their prices.
    assert read(client, 'invoice show', invoice=by_priority['id'])['total_minor_units'] == 1900


@pytest.mark.parametrize('rounding, direction, expected', [
    ('cent', 'down', '11.11'), ('x.99', 'down', '10.99'), ('x.95', 'down', '10.95'), ('dollar', 'down', '11.00'),
    ('x.99', 'up', '13.99')])
def test_rounding_modes(client, shop, rounding, direction, expected):
    made = rule(client, 'PR valve deal', items=['PR Valve'], value='10', rounding=rounding, direction=direction)
    assert price(client, shop['valve'], shop['gamma']) == (expected, 'PR valve deal')
    assert made['rounding'] == rounding


def test_a_saved_invoice_is_unchanged_after_a_rule_edit(client, shop):
    made = rule(client, 'PR edit me', items=['PR Fitting'], value='10')
    posted = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='2')])
    assert posted['total_minor_units'] == 3600
    write(client, 'price-rule update', rule=made['id'], expected_version=1, value='50', rounding='dollar')
    again = read(client, 'invoice show', invoice=posted['id'])
    assert again['total_minor_units'] == 3600 and again['revision']['lines'][0]['item_snapshot']['pricing_rule']['version'] == 1
    line_id = posted['revision']['lines'][0]['line_id']
    memo = write(client, 'invoice update', invoice=posted['id'], expected_version=1, memo='Same lines',
                 lines=[dict(item=shop['fitting'], line_id=line_id)])
    assert memo['total_minor_units'] == 3600 and memo['revision']['lines'][0]['item_snapshot']['pricing_rule']['version'] == 1
    # A new quantity keeps the adjustment the line captured while its rule still matches: 3 x 18.00.
    more = write(client, 'invoice update', invoice=posted['id'], expected_version=2,
                 lines=[dict(item=shop['fitting'], line_id=line_id, quantity='3')])
    assert more['total_minor_units'] == 5400 and more['revision']['lines'][0]['item_snapshot']['pricing_rule']['version'] == 1
    # Deactivated, the rule prices nothing new and the invoice stands.
    write(client, 'price-rule deactivate', rule=made['id'], expected_version=2)
    assert price(client, shop['fitting'], shop['gamma']) == ('20.00', None)
    assert read(client, 'invoice show', invoice=posted['id'])['total_minor_units'] == 5400
    # Once its rule no longer matches, a quantity edit re-prices from today's rules, and says why.
    again = write(client, 'invoice update', invoice=posted['id'], expected_version=3,
                  lines=[dict(item=shop['fitting'], line_id=line_id, quantity='4')])
    assert again['total_minor_units'] == 8000 and 'pricing_rule' not in again['revision']['lines'][0]['item_snapshot']
    said = ' '.join(again.get('warnings') or [])
    assert 'unit_price: 18.00 became 20.00 because the quantity changed; no price rule applies now' in said, said
    listed = read(client, 'price-rule list', include_inactive=True, item=shop['fitting'])
    assert [r['name'] for r in listed['rules']] == ['PR edit me'] and listed['conflict_policy'] == 'lowest_price'
    assert read(client, 'price-rule list')['rules'] == []


def test_rule_validation(client, shop):
    for fields in (dict(), dict(value='5', breaks=[dict(min_quantity='2', value='1')]), dict(value='101'),
                   dict(value='5', date_from='2026-12-01', date_to='2026-11-01'), dict(value='5', min_quantity='0')):
        with pytest.raises(BookflowError) as e:
            rule(client, 'PR invalid', **fields)
        assert e.value.code in ('E_VALIDATION', 'E_VALUE_RANGE'), fields
    rule(client, 'PR taken', value='1')
    with pytest.raises(BookflowError) as e:
        rule(client, 'pr TAKEN', value='1')
    assert e.value.code == 'E_NAME_TAKEN'
    with pytest.raises(BookflowError) as e:
        rule(client, 'PR nobody', customers=['No such customer'], value='1')
    assert e.value.code == 'E_RECORD_NOT_FOUND'


def test_two_hundred_lines_against_a_hundred_rules_measured(client, shop):
    lines = [dict(item=shop['fitting'], quantity='1')] * 200
    started = time.perf_counter()
    assert invoice(client, shop['gamma'], lines)['total_minor_units'] == 200 * 2000
    baseline = time.perf_counter() - started
    # 100 rules: 99 for other customers' items or dates, and the one that prices these lines.
    for index in range(99):
        rule(client, f'PR noise {index:02d}', customers=['PR Beta'] if index % 3 else [], items=['PR Valve'] if index % 2 else [],
             value=str(1 + index % 7), date_from='2027-01-01' if index % 3 == 0 else None)
    rule(client, 'PR the one', items=['PR Fitting'], customers=['PR Gamma'], value='10', priority=500)
    started = time.perf_counter()
    posted = invoice(client, shop['gamma'], lines)
    ruled = time.perf_counter() - started
    assert posted['total_minor_units'] == 200 * 1800
    assert {line['item_snapshot']['pricing_rule']['label'] for line in posted['revision']['lines']} == {'PR the one'}
    print(f'\nR110 measured: a 200-line invoice posted in {baseline:.2f} s with no rules, {ruled:.2f} s against 100 rules')
    # Measured 2026-10-22: 3.55 s and 4.11 s. The bound leaves room for a loaded machine.
    assert ruled < baseline * 2 + 2


def test_a_rule_never_makes_a_line_free_or_dearer(client, shop):
    s = shop
    code = next(row['id'] for row in read(client, 'sales-tax-code list')['items'] if not row['taxable'])
    income = read(client, 'item show', item=s['valve'])['income_account_id']
    costs = client.account.create(name='PR costs 2', type='cost_of_goods_sold', company=COMPANY)['id']

    def item(name, price_, cost=None):
        extra = dict(cost=cost, purchase_enabled=True, purchase_description=name, expense_account_id=costs) if cost else {}
        return write(client, 'item create', name=name, type='service', sales_enabled=True, description=name,
                     income_account_id=income, price=price_, sales_tax_code_id=code, **extra)['id']

    free_cost = item('PR Free cost', '20.00', cost='0.00')
    dear_cost = item('PR Dear cost', '10.00', cost='12.00')
    cheap = item('PR Cheap', '0.50')
    dime = item('PR Dime', '0.40')
    edge = item('PR Edge', '10.60')
    cases = [
        # A cost of 0.00 is no basis: the item price applies.
        (dict(items=['PR Free cost'], basis='cost', direction='up', value='25'), free_cost, '20.00', None),
        # An amount discount to 0.00 or below is skipped, never clamped to free.
        (dict(items=['PR Fitting'], kind='amount', value='20.00'), s['fitting'], '20.00', None),
        (dict(items=['PR Fitting'], kind='amount', value='25.00'), s['fitting'], '20.00', None),
        # Under lowest price the item's own price is a candidate: 5% off cost 12.00 = 11.40 > 10.00.
        (dict(items=['PR Dear cost'], basis='cost', value='5'), dear_cost, '10.00', None),
        # 10% off 0.50 = 0.45; down to .99 would be below zero, so it rounds to the cent.
        (dict(items=['PR Cheap'], value='10', rounding='x.99'), cheap, '0.45', 'rule'),
        # 10% off 0.40 = 0.36 rounds to 0.00 by the dollar: skipped.
        (dict(items=['PR Dime'], value='10', rounding='dollar'), dime, '0.40', None),
        # 0.1% off 10.60 = 10.5894 rounds up to 11.00 by the dollar, above the base: to the cent instead.
        (dict(items=['PR Edge'], value='0.1', rounding='dollar'), edge, '10.59', 'rule'),
    ]
    for index, (fields, item_id, expected, by) in enumerate(cases):
        made = rule(client, f'PR edge {index}', **fields)
        try:
            got, name = price(client, item_id, s["gamma"])
        except BookflowError as e:
            raise AssertionError((index, e.details))
        assert (got, name) == (expected, made['name'] if by else None), (fields, got, name)
        off(client, made)


def test_rules_follow_merged_items_and_customers(client, shop):
    s = shop
    code = next(row['id'] for row in read(client, 'sales-tax-code list')['items'] if not row['taxable'])
    income = read(client, 'item show', item=s['valve'])['income_account_id']
    write(client, 'item create', name='PR Fitting dup', type='service', sales_enabled=True, description='Dup',
          income_account_id=income, price='20.00', sales_tax_code_id=code)
    write(client, 'customer create', name='PR Gamma dup')
    rule(client, 'PR on the dup item', items=['PR Fitting dup'], value='10')
    rule(client, 'PR on the dup customer', customers=['PR Gamma dup'], items=['PR Valve'], value='50')
    assert price(client, s['fitting'], s['gamma']) == ('20.00', None)
    write(client, 'item merge', merged='PR Fitting dup', into='PR Fitting')
    write(client, 'customer merge', merged='PR Gamma dup', into='PR Gamma')
    assert price(client, s['fitting'], s['beta']) == ('18.00', 'PR on the dup item')
    assert price(client, s['valve'], s['gamma']) == ('6.17', 'PR on the dup customer')  # 50% of 12.34
    posted = invoice(client, s['gamma'], [dict(item=s['fitting'], quantity='1'), dict(item=s['valve'], quantity='1')])
    assert posted['total_minor_units'] == 1800 + 617
    # Undone, the rules name the entries they always named.
    write(client, 'item unmerge', merged='PR Fitting dup')
    assert price(client, s['fitting'], s['beta']) == ('20.00', None)


def test_preview_resolves_as_posting_does_for_a_job(client, shop):
    s = shop
    write(client, 'company update', enable_price_levels=True)
    level = write(client, 'price-level create', name='PR Job level', kind='fixed_percent', percent='-20')['id']
    job = read(client, 'customer show', customer=s['job'])
    write(client, 'customer update', customer=s['job'], expected_version=job['version'], price_level_id=level,
          job_sales_rep_id=s['rep'])
    rule(client, 'PR rep deal', sales_reps=['PRR'], items=['PR Fitting'], value='25')  # 15.00, below the level's 16.00
    rule(client, 'PR valve rep deal', sales_reps=['PRR'], items=['PR Valve'], value='10')  # 11.11, above the level's 9.87
    shown = {item: read(client, 'price-rule preview', item=item, customer='PR Acme:Warehouse', quantity='2', date='2026-11-02')
             for item in (s['fitting'], s['valve'])}
    posted = invoice(client, s['job'], [dict(item=s['fitting'], quantity='2'), dict(item=s['valve'], quantity='2')])
    lines = posted['revision']['lines']
    assert [shown[s['fitting']]['unit_price'], shown[s['valve']]['unit_price']] == [l['unit_price']['amount'] for l in lines]
    assert (shown[s['fitting']]['source'], shown[s['valve']]['source']) == ('rule', 'price_level')
    assert [l['unit_price']['amount'] for l in lines] == ['15.00', '9.87']


def test_header_class_rep_and_date_changes_re_price_defaulted_lines(client, shop):
    s = shop
    rule(client, 'PR east', classes=['PR East'], items=['PR Fitting'], kind='amount', value='1')
    rule(client, 'PR rep', sales_reps=['PRR'], items=['PR Valve'], kind='amount', value='1')
    rule(client, 'PR december', items=['PR Fitting'], customers=['PR Beta'], value='10', date_from='2026-12-01')
    posted = invoice(client, s['gamma'], [dict(item=s['fitting'], quantity='1'), dict(item=s['valve'], quantity='1')])
    assert posted['total_minor_units'] == 2000 + 1234
    ids = [dict(item=l['item_snapshot']['item']['id'], line_id=l['line_id']) for l in posted['revision']['lines']]
    moved = write(client, 'invoice update', invoice=posted['id'], expected_version=1, class_id='PR East', sales_rep=s['rep'],
                  lines=ids)
    assert [l['unit_price']['amount'] for l in moved['revision']['lines']] == ['19.00', '11.34']
    dated = invoice(client, s['beta'], [dict(item=s['fitting'], quantity='1')], date='2026-11-20')
    assert dated['total_minor_units'] == 2000
    line = dict(item=s['fitting'], line_id=dated['revision']['lines'][0]['line_id'])
    later = write(client, 'invoice update', invoice=dated['id'], expected_version=1, date='2026-12-02', lines=[line])
    assert later['total_minor_units'] == 1800


def test_an_explicitly_picked_line_level_stands_under_priority(client, shop):
    write(client, 'company update', enable_price_levels=True)
    write(client, 'price-level create', name='PR Picked', kind='fixed_percent', percent='-20')
    rule(client, 'PR fitting deal', items=['PR Fitting'], value='5')
    write(client, 'price-rule set-preferences', conflict_policy='priority')
    picked = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='1', price_level='PR Picked')])
    plain = invoice(client, shop['gamma'], [dict(item=shop['fitting'], quantity='1')])
    assert (picked['total_minor_units'], plain['total_minor_units']) == (1600, 1900)
