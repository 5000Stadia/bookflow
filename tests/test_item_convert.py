"""R154: converting a non-inventory part into an inventory part, one way, without rewriting history.

Buy and sell it as a non-inventory part, convert it, buy and sell again: the earlier purchase stays
expensed and the earlier sale stays without cost of goods, stock starts at zero on the conversion
date, and from then on purchases go to stock and sales relieve it at average cost.
"""
import pytest

from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_asset, _net  # noqa: F401


def _valve(books, name='Brass Valve', **extra):
    fields = dict(name=name, type='non_inventory_part', description='1/2in brass valve', price='9.00',
                  income_account_id=books['income'], purchase_enabled=True,
                  purchase_description='1/2in brass valve', cost='5.00', expense_account_id=books['supplies'])
    fields.update(extra)
    return books['client'].item.create(company=books['company'], **fields)['id']


def _row(books, report, as_of, item):
    out = books['run'](report, dict(as_of=as_of, limit=200))
    return next((row for row in out['rows'] if row['item_id'] == item), None), out


def test_buy_convert_buy_sell_keeps_history_and_stocks_from_the_conversion(books):
    run = books['run']
    valve = _valve(books)
    asset = _inventory_asset(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-01-10',
                          items=[dict(item=valve, quantity='10', unit_cost='5.00')]), reason='Bought before')
    run('invoice post', dict(date='2017-01-20', customer=books['customer'],
                             lines=[dict(item=valve, quantity='2')]), reason='Sold before')
    january = _net(books, date_to='2017-01-31')
    assert january[books['supplies']] == 5000 and asset not in january and books['cogs'] not in january

    body = dict(item=valve, to='inventory', date='2017-02-01')
    preview = run('item convert', body, reason='We stock these now', dry_run=True)
    assert preview['conversion_id'] is None and preview['to_type'] == 'inventory_part' and preview['one_way']
    assert preview['cogs_account_id'] == books['cogs'] and preview['asset_account_id'] == asset
    assert preview['dropped_expense_account'] == 'Job Supplies' and preview['quantity_on_hand'] == '0'
    assert preview['last_document_date'] == '2017-01-20'
    assert any('inventory adjust' in note for note in preview['notes'])
    assert run('item show', {'item': valve})['type'] == 'non_inventory_part'

    done = run('item convert', body, reason='We stock these now', idempotency_key='convert-valve')
    again = run('item convert', body, reason='We stock these now', idempotency_key='convert-valve')
    assert done['conversion_id'] and again['conversion_id'] == done['conversion_id']
    shown = run('item show', {'item': valve})
    assert shown['id'] == valve and shown['type'] == 'inventory_part' and shown['version'] == done['version']
    assert shown['quantity_on_hand'] == '0' and shown['income_account_id'] == books['income']

    # One-way: converting again is refused, and so is turning it back with item update.
    with pytest.raises(BookflowError) as error:
        run('item convert', body, reason='Again')
    assert error.value.code == 'E_TYPE_CHANGE' and 'one-way' in error.value.message
    with pytest.raises(BookflowError) as error:
        run('item update', {'item': valve, 'type': 'non_inventory_part'}, reason='Back')
    assert error.value.code == 'E_TYPE_CHANGE'

    # No stock before the conversion date: a back-dated bill naming it is refused.
    with pytest.raises(BookflowError) as error:
        run('bill post', dict(vendor=books['vendor'], date='2017-01-25',
                              items=[dict(item=valve, quantity='1', unit_cost='5.00')]), reason='Back-dated')
    assert error.value.code == 'E_VALIDATION' and error.value.details['reason'] == 'before_inventory_conversion'
    with pytest.raises(BookflowError) as error:
        run('inventory adjust', dict(item=valve, date='2017-01-31', quantity_change='3', value_change='15.00',
                                         adjustment_account=books['cogs']),
            reason='Count before')
    assert error.value.code == 'E_VALIDATION' and error.value.details['inventory_since'] == '2017-02-01'

    run('bill post', dict(vendor=books['vendor'], date='2017-02-05',
                          items=[dict(item=valve, quantity='10', unit_cost='6.00')]), reason='Bought after')
    run('invoice post', dict(date='2017-02-10', customer=books['customer'],
                             lines=[dict(item=valve, quantity='4')]), reason='Sold after')

    # History unchanged: January reads exactly as it did before the conversion.
    assert _net(books, date_to='2017-01-31') == january
    february = _net(books, date_from='2017-02-01', date_to='2017-02-28')
    assert february[asset] == 6000 - 2400 and february[books['cogs']] == 2400
    assert books['supplies'] not in february

    row, _ = _row(books, 'report stock-status', '2017-01-31', valve)
    assert row is None or row['quantity_on_hand'] == '0'
    row, status = _row(books, 'report stock-status', '2017-02-28', valve)
    assert row['quantity_on_hand'] == '6' and row['asset_value']['minor_units'] == 3600
    row, valuation = _row(books, 'report inventory-valuation', '2017-02-28', valve)
    assert row['asset_value']['minor_units'] == 3600
    assert valuation['totals']['asset_value']['minor_units'] == _net(books, date_to='2017-02-28')[asset]

    # The audit trail names the act and the reason.
    events = run('audit list', {'command': 'item convert', 'limit': 5})['items']
    assert len(events) == 1


def test_refusals(books):
    run = books['run']
    # A type that cannot convert, and an item already carrying stock.
    with pytest.raises(BookflowError) as error:
        run('item convert', dict(item=books['labor'], to='inventory'), reason='Try a service')
    assert error.value.code == 'E_TYPE_CHANGE' and 'only a non-inventory part or an other charge' in error.value.message

    # The conversion date cannot precede the last document that used it.
    valve = _valve(books)
    run('bill post', dict(vendor=books['vendor'], date='2017-03-10',
                          items=[dict(item=valve, quantity='1', unit_cost='5.00')]), reason='Bought')
    with pytest.raises(BookflowError) as error:
        run('item convert', dict(item=valve, to='inventory', date='2017-03-01'), reason='Too early')
    assert error.value.code == 'E_VALIDATION' and error.value.details['last_use']['date'] == '2017-03-10'

    # No reason, no conversion.
    with pytest.raises(BookflowError) as error:
        run('item convert', dict(item=valve, to='inventory', date='2017-03-10'))
    assert error.value.code == 'E_REASON_REQUIRED'

    # An open purchase order naming it holds the conversion back.
    order = run('purchase-order post', dict(vendor=books['vendor'], date='2017-03-12',
                                            lines=[dict(item=valve, quantity='5', rate='5.00')]), reason='Order more')
    with pytest.raises(BookflowError) as error:
        run('item convert', dict(item=valve, to='inventory', date='2017-03-12'), reason='Stock it')
    assert error.value.code == 'E_RECORD_IN_USE' and error.value.details['documents'][0]['id'] == order['id']

    # A percentage charge has no quantity to stock.
    fee = books['client'].item.create(company=books['company'], name='Fuel surcharge', type='other_charge',
                                      description='Fuel', charge_percent='5', income_account_id=books['income'])['id']
    with pytest.raises(BookflowError) as error:
        run('item convert', dict(item=fee, to='inventory'), reason='Stock it')
    assert error.value.code == 'E_TYPE_CHANGE' and 'percentage' in error.value.message


def test_an_other_charge_converts_too(books):
    run = books['run']
    crate = books['client'].item.create(company=books['company'], name='Shipping crate', type='other_charge',
                                        description='Wooden crate', price='20.00',
                                        income_account_id=books['income'])['id']
    done = run('item convert', dict(item=crate, to='inventory', date='2017-04-01', cost='8.00'),
               reason='Crates are stocked now')
    assert done['from_type'] == 'other_charge' and done['to_type'] == 'inventory_part'
    shown = run('item show', {'item': crate})
    assert shown['type'] == 'inventory_part' and shown['purchase_description'] == 'Wooden crate'
    assert shown['cost']['amount'] == '8.00' and shown['price']['amount'] == '20.00'
