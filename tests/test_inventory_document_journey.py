"""Inventory costing through real purchases and sales, with independent dated totals."""
from pathlib import Path
import sqlite3

import pytest
from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net  # noqa: F401


def test_bill_invoice_and_receipt_recost_at_each_sale_date(books):
    run = books['run']
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    method = run('payment-method create', {'name': 'Inventory cash', 'kind': 'cash'})['id']

    def buy(date, cost):
        return dict(vendor=books['vendor'], date=date,
                    items=[dict(item=item, quantity='10', unit_cost=cost)])

    run('bill post', buy('2017-01-01', '10.00'), reason='Receive ten units')
    invoice = run('invoice post', dict(date='2017-02-10', customer=books['customer'],
                  lines=[dict(item=item, quantity='4')]), reason='Sell four on account')
    receipt = run('sales-receipt post', dict(date='2017-03-10', customer=books['customer'],
                  deposit_to=books['bank'], payment_method=method,
                  lines=[dict(item=item, quantity='6')]), reason='Sell the last six for cash')

    def balances(date, quantity, value, cogs):
        status = run('report stock-status', dict(as_of=date, limit=200))
        valuation = run('report inventory-valuation', dict(as_of=date, limit=200))
        row = next(row for row in status['rows'] if row['item_id'] == item)
        assert row['quantity_on_hand'] == str(quantity)
        assert row['asset_value']['minor_units'] == value
        assert status['totals']['asset_value']['minor_units'] == value
        assert valuation['totals']['asset_value']['minor_units'] == value
        net = _net(books, date_to=date)
        assert net.get(asset, 0) == value
        assert net.get(books['cogs'], 0) == cogs
        sheet = run('report balance-sheet', dict(date_to=date, limit=200, include_zero=True))
        held = [r for r in sheet['rows'] if r['display_account_label'].endswith('Inventory Asset')]
        assert len(held) == 1 and held[0]['amount']['minor_units'] == value

    balances('2017-01-31', 10, 10000, 0)
    balances('2017-02-28', 6, 6000, 4000)
    balances('2017-03-31', 0, 0, 10000)

    later_input = buy('2017-01-20', '30.00')
    later = run('bill post', later_input, reason='Enter delayed supplier bill',
                idempotency_key='inventory-delayed-bill')
    repeated = run('bill post', later_input, reason='Enter delayed supplier bill',
                   idempotency_key='inventory-delayed-bill')
    assert repeated['id'] == later['id']
    balances('2017-01-31', 20, 40000, 0)
    balances('2017-02-28', 16, 32000, 8000)
    balances('2017-03-31', 10, 20000, 20000)
    # Recosting must not rewrite the commercial documents the customer received.
    assert run('invoice show', {'invoice': invoice['id']})['version'] == invoice['version']
    assert run('sales-receipt show', {'sales_receipt': receipt['id']})['version'] == receipt['version']

    run('bill void', dict(bill=later['id'], expected_version=later['version']),
        reason='Delayed bill was a duplicate')
    balances('2017-01-31', 10, 10000, 0)
    balances('2017-02-28', 6, 6000, 4000)
    balances('2017-03-31', 0, 0, 10000)


def test_purchase_refusals_preserve_documents_movements_and_postings(books):
    run = books['run']
    item = _inventory_part(books)
    purchase = run('bill post', dict(vendor=books['vendor'], date='2017-01-01',
                   items=[dict(item=item, quantity='10', unit_cost='10.00')]), reason='Receive stock')
    run('invoice post', dict(customer=books['customer'], date='2017-02-10',
        lines=[dict(item=item, quantity='4')]), reason='Sell received stock')

    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'

    def snapshot():
        # Check actual business rows, not just the number of item rows in a report.
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            return {table: sorted(db.execute('SELECT * FROM ' + table).fetchall(), key=repr)
                    for table in ('transactions', 'transaction_revisions', 'document_lines',
                                  'inventory_movements', 'posting_batches', 'posting_lines')}

    before = snapshot()
    with pytest.raises(BookflowError) as negative:
        run('bill void', dict(bill=purchase['id'], expected_version=purchase['version']),
            reason='Attempt to remove already sold stock')
    assert negative.value.code == 'E_VALIDATION'
    assert 'negative' in str(negative.value).lower()
    assert snapshot() == before

    run('company update', {'closing_date': '2017-02-28'}, reason='Close February')
    before = snapshot()
    with pytest.raises(BookflowError) as closed:
        run('bill update', dict(bill=purchase['id'], expected_version=purchase['version'],
            items=[dict(item=item, quantity='10', unit_cost='30.00')]), reason='Change closed purchase')
    assert closed.value.code == 'E_PERIOD_CLOSED'
    assert closed.value.details['closing_date'] == '2017-02-28'
    assert snapshot() == before

