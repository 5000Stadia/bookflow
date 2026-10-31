"""Returns preserve a receipt's cost; an unknown sale requires an explicit stock value."""
import pytest
from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_part  # noqa: F401
from tests.test_stocked_credit_returns import _stock, _legs, _movements


def test_receipt_returns_stock_and_preserves_claims(books):
    run = books['run']
    item = _inventory_part(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='3', unit_cost='8')]), reason='Stock')
    sale = run('sales-receipt post', dict(customer=books['customer'], date='2017-01-02',
        payment_method=books['methods']['Cash'], deposit_to=books['bank'],
        lines=[dict(item=item, quantity='3', unit_price='12')]), reason='Sell')
    def returned(quantity):
        return run('credit-memo post', dict(customer=books['customer'], date='2017-01-03',
            lines=[dict(source_invoice=sale['id'], source_line=sale['revision']['lines'][0]['line_id'],
                        quantity=quantity)]), reason='Return')
    first = returned('1')
    assert first['total']['minor_units'] == 1200
    assert first['revision']['lines'][0]['source_document_type'] == 'sales_receipt'
    from bookflow.adapters.workbench.credits import detail_context
    view = detail_context('credit-memo', first, books['company'])
    assert view['returned_from'].endswith('/sales-receipt/' + sale['id'])
    assert _stock(books, item, '2017-01-03') == ('1', 800)
    assert _movements(books, item)[-1]['value_minor_units'] == 800
    with pytest.raises(BookflowError) as e:
        run('sales-receipt void', dict(sales_receipt=sale['id'], expected_version=1), reason='Void')
    assert e.value.code == 'E_WORK_DEPENDENCY'
    assert first['number'] in str(e.value)
    returned('2')
    assert _stock(books, item, '2017-01-03') == ('3', 2400)
    with pytest.raises(BookflowError):
        returned('1')


def test_explicit_unlinked_stock_value_survives_correction_and_void(books):
    run = books['run']
    item = _inventory_part(books)
    inp = dict(customer=books['customer'], date='2017-01-03',
        lines=[dict(item=item, quantity='2', unit_price='12', return_cost='15')])
    credit = run('credit-memo post', inp, reason='Receive unknown sale')
    assert credit['total']['minor_units'] == 2400
    assert _stock(books, item, '2017-01-03') == ('2', 1500)
    assert sum(r['credit_minor_units'] for r in _legs(books, credit['id']) if r['account_id'] == books['cogs']) == 1500
    updated = run('credit-memo update', dict(credit_memo=credit['id'], expected_version=1,
        memo='Inspected'), reason='Add memo')
    assert _stock(books, item, '2017-01-03') == ('2', 1500)
    with pytest.raises(BookflowError) as error:
        run('credit-memo update', dict(credit_memo=credit['id'], expected_version=updated['version'],
            lines=[dict(line_id=updated['revision']['lines'][0]['line_id'], item=item, quantity='3')]),
            reason='Change quantity without valuing it')
    assert error.value.code == 'E_VALIDATION'
    assert _stock(books, item, '2017-01-03') == ('2', 1500)
    run('credit-memo void', dict(credit_memo=credit['id'], expected_version=updated['version']), reason='Undo')
    assert _stock(books, item, '2017-01-03') == ('0', 0)


def test_no_receipt_default_sales_pack_values_base_quantity(books):
    run = books['run']
    run('company update', dict(pos_enabled=True, units_of_measure_mode='multiple_related_units'))
    units = run('unit-of-measure create', dict(name='Return packs', units=[
        dict(name='Each', abbreviation='ea', is_base=True, base_factor='1'),
        dict(name='Pack', abbreviation='pk', is_base=False, base_factor='3')]))
    pack = next(u['id'] for u in units['units'] if not u['is_base'])
    run('unit-of-measure update', dict(unit_of_measure=units['id'], expected_version=1,
        default_sales_unit_id=pack))
    item = run('item create', dict(name='Packaged return', type='inventory_part', price='12', cost='8',
        description='Returned pack', purchase_description='Stock', cogs_account_id=books['cogs'],
        income_account_id=books['income'], unit_of_measure_set_id=units['id']))['id']
    result = run('pos refund', dict(operation_key='pack-return',
        lines=[dict(item=item, quantity='2')]), reason='Return two packs')
    # Two packs contain six base units; fallback purchase cost is 8.00 per base unit.
    movements = _movements(books, item)
    assert movements[-1]['quantity_microunits'] == 6_000_000
    assert movements[-1]['value_minor_units'] == 4800
    assert result['total']['minor_units'] == 7200
