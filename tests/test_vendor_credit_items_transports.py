"""Stock sent back to a vendor, over actual CLI, HTTP and MCP.

Buy 50 units at their 1.80 standard cost (90.00), then send 10 back on a vendor credit that the
vendor allowed 2.00 each for (20.00). Inventory Asset falls by the credited 20.00 and not by the
10 x 1.80 = 18.00 the average would have taken, Accounts Payable owes 90.00 - 20.00 = 70.00, and
the shelf holds 40 worth 70.00. Sending back more than is on hand is refused and writes nothing,
and a preview writes nothing either. Each surface starts from its own identical copy.
"""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset  # noqa: F401
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database



@pytest.mark.timeout(600)
def test_stock_returned_to_a_vendor_crosses_cli_http_and_mcp(books, tmp_path):
    pytest.importorskip('mcp')
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    books['run']('bill post', dict(vendor=books['vendor'], date='2017-03-20',
                                   items=[{'item': item, 'quantity': '50'}]), reason='Receive stock')
    before_net = {asset: 9000, books['payable']: -9000}
    after_net = {asset: 7000, books['payable']: -7000}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(Path(books['client'].data_root), tmp_path / 'surfaces')
            completed = set()
            for surface in ('cli', 'http', 'mcp'):
                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                async def ledger():
                    page = await call('report general-ledger', dict(
                        date_from='2017-01-01', date_to='2017-12-31', limit=200))
                    net = {}
                    for row in page['rows']:
                        if row['kind'] == 'posting':
                            net[row['account_id']] = net.get(row['account_id'], 0) + (
                                row['debit']['minor_units'] - row['credit']['minor_units'])
                    return {account: value for account, value in net.items() if value}

                async def stock(date):
                    page = await call('report stock-status', dict(as_of=date, limit=200))
                    row = next(row for row in page['rows'] if row['item_id'] == item)
                    return row['quantity_on_hand'], row['asset_value']['minor_units']

                state = await call('permission show', {})
                await call('permission activate', dict(
                    expected_generation=state['generation'],
                    expected_catalog_sha256=state['catalog_sha256']))
                path = Path((await call('company show', {}))['path']) / 'company.db'
                assert await ledger() == before_net, surface
                assert await stock('2017-03-21') == ('50', 9000), surface

                before = database(path)
                too_many = dict(vendor=books['vendor'], date='2017-03-25', supplier_reference='CM-TOO-MANY',
                                items=[dict(item=item, quantity='51', unit_cost='2.00')])
                refused = await call('vendor-credit post', too_many, rejected=True)
                assert refused['code'] == 'E_VALIDATION', surface
                assert 'only 50 on hand' in refused['message'], surface
                assert database(path) == before, surface

                raw = dict(vendor=books['vendor'], date='2017-03-25', supplier_reference='CM-1',
                           items=[dict(item=item, quantity='10', unit_cost='2.00')])
                preview = await call('vendor-credit post', raw, dry_run=True)
                assert preview['dry_run'], surface
                assert preview['total']['minor_units'] == 2000, surface
                assert database(path) == before, surface

                credit = await call('vendor-credit post', raw)
                assert credit['status'] == 'posted', surface
                assert credit['item_total']['minor_units'] == 2000, surface
                assert credit['revision']['items'][0]['quantity'] == '10', surface
                assert await ledger() == after_net, surface
                assert await stock('2017-03-25') == ('40', 7000), surface
                # Before the credit's date the shelf is as it was.
                assert await stock('2017-03-24') == ('50', 9000), surface

                with sqlite3.connect(path) as db:
                    legs = db.execute('SELECT account_id, debit_minor_units, credit_minor_units '
                                      'FROM posting_lines WHERE transaction_id=?',
                                      (credit['id'],)).fetchall()
                    assert sorted(legs) == sorted([(books['payable'], 2000, 0), (asset, 0, 2000)]), surface
                    assert db.execute('SELECT batch_id FROM posting_lines GROUP BY batch_id '
                                      'HAVING sum(debit_minor_units) != sum(credit_minor_units)').fetchall() == [], surface
                    movements = db.execute('SELECT kind, quantity_microunits, value_minor_units '
                                           'FROM inventory_movements WHERE item_id=? '
                                           'ORDER BY effective_date, sequence, id', (item,)).fetchall()
                    assert movements == [('receipt', 50_000_000, 9000),
                                         ('vendor_return', -10_000_000, -2000)], surface
                    assert db.execute('SELECT created_via FROM inventory_movements '
                                      'WHERE transaction_id=?', (credit['id'],)).fetchall() == [(surface,)], surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                completed.add(surface)
            assert completed == {'cli', 'http', 'mcp'}
        finally:
            await matrix.close()

    asyncio.run(witness())
