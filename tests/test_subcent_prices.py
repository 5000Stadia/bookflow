"""Unit prices and costs finer than a cent (R150).

The money, by hand:
    fuel 12.5 x 3.459 = 43.2375 -> 43.24 (half to even to the cent);
    1,000 x 0.0125 = 12.50 exactly;
    a price level 10% off 0.0125 = 0.01125 (kept to five places at the default one-cent increment);
    a rule 10% over 3.459 = 3.8049, rounded up to the next .99 ending -> 3.99;
    a case of 12 at 0.0325 each = 0.39 per case (0.3900);
    six decimal places is refused: E_AMOUNT_PRECISION, allowed_places 5.
"""
import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY

pytestmark = pytest.mark.timeout(900)


def write(client, command, reason='Sub-cent', **data):
    return client.run(command, data, company=COMPANY, reason=reason)


def read(client, command, **data):
    return client.run(command, data, company=COMPANY)


@pytest.fixture
def shop(client):
    income = client.account.create(name='SC income', type='income', company=COMPANY)['id']
    expense = client.account.create(name='SC costs', type='expense', company=COMPANY)['id']
    code = next(row['id'] for row in read(client, 'sales-tax-code list')['items'] if not row['taxable'])
    item = lambda name, price, **kw: write(client, 'item create', name=name, type='service', sales_enabled=True,  # noqa: E731
                                           description=name, income_account_id=income, price=price,
                                           sales_tax_code_id=code, purchase_enabled=True, purchase_description=name,
                                           expense_account_id=expense, **kw)
    fuel = item('SC Fuel', '3.459', cost='3.1015')
    screw = item('SC Screw', '0.0125', cost='0.0075')
    customer = write(client, 'customer create', name='SC Haulage')['id']
    vendor = write(client, 'vendor create', name='SC Supply')['id']
    return dict(fuel=fuel, screw=screw, customer=customer, vendor=vendor, income=income, expense=expense, code=code)


def amounts(revision):
    return [(line['unit_price']['amount'] if line.get('unit_price') else None, line['net']['amount'])
            for line in revision['lines']]


def test_item_prices_keep_five_places(client, shop):
    assert shop['fuel']['price']['amount'] == '3.459'
    assert shop['screw']['price']['amount'] == '0.0125'
    assert shop['screw']['cost']['amount'] == '0.0075'
    shown = read(client, 'item show', item=shop['screw']['id'])
    assert shown['price'] == {'amount': '0.0125', 'currency': 'USD', 'minor_units': 1}


def test_invoice_lines_extend_once_half_even(client, shop):
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'], lines=[
        dict(item=shop['fuel']['id'], quantity='12.5'), dict(item=shop['screw']['id'], quantity='1000'),
        dict(item=shop['fuel']['id'], quantity='1', unit_price='3.45900')])
    assert amounts(out['revision']) == [('3.459', '43.24'), ('0.0125', '12.50'), ('3.459', '3.46')]
    assert out['revision']['total']['amount'] == '59.20'


def test_six_places_are_refused(client, shop):
    with pytest.raises(BookflowError) as caught:
        write(client, 'invoice post', date='2026-11-02', customer=shop['customer'],
              lines=[dict(item=shop['fuel']['id'], quantity='1', unit_price='3.459001')])
    assert caught.value.code == 'E_AMOUNT_PRECISION'
    assert caught.value.details['allowed_places'] == 5
    with pytest.raises(BookflowError) as caught:
        write(client, 'item create', name='SC Bad', type='service', sales_enabled=True, description='x',
              income_account_id=shop['income'], price='0.000001', sales_tax_code_id=shop['code'])
    assert caught.value.code == 'E_AMOUNT_PRECISION'


def test_a_price_level_percent_keeps_a_sub_cent_price_exact(client, shop):
    write(client, 'company update', enable_price_levels=True)
    write(client, 'price-level create', name='SC Ten off', kind='fixed_percent', percent='-10')
    write(client, 'price-level create', name='SC Contract', kind='per_item',
          items=[dict(item_id=shop['screw']['id'], price='0.0115', adjustment_basis='standard_price')])
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'], lines=[
        # 10% off 0.0125 is 0.01125: at the default one-cent increment a sub-cent base keeps five places.
        dict(item=shop['screw']['id'], quantity='1000', price_level='SC Ten off'),
        dict(item=shop['screw']['id'], quantity='1000', price_level='SC Contract'),
        # 10% off the sub-cent 3.459 is 3.1131, kept exact; 10 x 3.1131 = 31.131 -> 31.13.
        dict(item=shop['fuel']['id'], quantity='10', price_level='SC Ten off')])
    assert amounts(out['revision']) == [('0.01125', '11.25'), ('0.0115', '11.50'), ('3.1131', '31.13')]


def test_a_whole_cent_price_level_still_rounds_to_the_cent(client, shop):
    write(client, 'company update', enable_price_levels=True)
    write(client, 'price-level create', name='SC Ten off', kind='fixed_percent', percent='-10')
    whole = write(client, 'item create', name='SC Whole', type='service', sales_enabled=True, description='Whole',
                  income_account_id=shop['income'], price='3.46', sales_tax_code_id=shop['code'])
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'], lines=[
        dict(item=whole['id'], quantity='10', price_level='SC Ten off')])
    assert amounts(out['revision']) == [('3.11', '31.10')]
    assert 'unit_price_fraction' not in out['revision']['lines'][0]


def test_a_price_rule_rounds_a_sub_cent_base_to_its_ending(client, shop):
    write(client, 'price-rule create', name='SC Fuel markup', items=['SC Fuel'], direction='up', value='10',
          rounding='x.99')
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'],
                lines=[dict(item=shop['fuel']['id'], quantity='12.5')])
    # 10% over 3.459 is 3.8049, rounded up to the next .99 ending: 3.99; 12.5 x 3.99 = 49.875 -> 49.88.
    assert amounts(out['revision']) == [('3.99', '49.88')]
    captured = out['revision']['lines'][0]['item_snapshot']['pricing_rule']
    assert (captured['base_minor_units'], captured['base_fraction'], captured['price_minor_units']) == (346, -100, 399)
    write(client, 'price-rule create', name='SC Screw cent', items=['SC Screw'], direction='down', value='10')
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'],
                lines=[dict(item=shop['screw']['id'], quantity='1000')])
    # A cent-rounding rule's price is whole cents: 10% off 0.0125 = 0.01125 -> 0.01.
    assert amounts(out['revision']) == [('0.01', '10.00')]


def test_a_unit_of_measure_converts_a_sub_cent_price_exactly(client, shop):
    write(client, 'company update', units_of_measure_mode='multiple_related_units')
    units = write(client, 'unit-of-measure create', name='SC Count', units=[
        dict(name='Each', abbreviation='ea', is_base=True, base_factor='1'),
        dict(name='Case', abbreviation='cs', is_base=False, base_factor='12')])
    fitting = write(client, 'item create', name='SC Fitting', type='service', sales_enabled=True, description='Fitting',
                    income_account_id=shop['income'], price='0.0325', sales_tax_code_id=shop['code'],
                    unit_of_measure_set_id=units['id'])
    out = write(client, 'invoice post', date='2026-11-02', customer=shop['customer'], lines=[
        dict(item=fitting['id'], quantity='2', unit='cs'), dict(item=fitting['id'], quantity='7', unit='ea')])
    # A case of 12 at 0.0325 each is 0.39 per case; 2 cases 0.78. Seven each at 0.0325 = 0.2275 -> 0.23.
    assert amounts(out['revision']) == [('0.39', '0.78'), ('0.0325', '0.23')]
