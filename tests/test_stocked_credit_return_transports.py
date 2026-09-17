"""Linked stock returns and unlinked-stock refusals over actual CLI, HTTP and MCP.

Buy two units for $16, sell one for $12 at $8 cost, then return that unit:
inventory returns to two units/$16, COGS to zero, and AP remains $16.
Each activated-catalog surface starts from its own identical sold-stock copy.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset  # noqa: F401
from tests.test_stocked_credit_returns import _stocked_invoice, _receivable, _sold_line
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


@pytest.mark.timeout(600)
def test_stocked_return_and_unlinked_refusal_cross_cli_http_and_mcp(books, tmp_path):
    pytest.importorskip('mcp')
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    receivable = _receivable(books)
    invoice = _stocked_invoice(books, item)
    before_net = {asset: 800, books['payable']: -1600, receivable: 1200,
                  books['income']: -1200, books['cogs']: 800}
    after_net = {asset: 1600, books['payable']: -1600}

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
                assert await stock('2017-01-02') == ('1', 800), surface

                before = database(path)
                refused = await call('credit-memo post', dict(
                    customer=books['customer'], date='2017-01-03',
                    lines=[dict(item=item, quantity='1', unit_price='12')]), rejected=True)
                assert refused['code'] == 'E_VALIDATION', surface
                assert refused['details']['reason'] == 'unlinked_stocked_credit_unsupported', surface
                assert database(path) == before, surface

                raw = dict(customer=books['customer'], date='2017-01-03',
                    lines=[dict(source_invoice=invoice['id'], source_line=_sold_line(invoice),
                                quantity='1')])
                preview = await call('credit-memo post', raw, dry_run=True)
                assert preview['dry_run'], surface
                assert preview['total']['minor_units'] == 1200, surface
                assert database(path) == before, surface

                credit = await call('credit-memo post', raw)
                assert credit['status'] == 'posted', surface
                assert credit['total']['minor_units'] == 1200, surface
                assert await ledger() == after_net, surface
                assert await stock('2017-01-03') == ('2', 1600), surface
                assert await stock('2017-01-02') == ('1', 800), surface

                with sqlite3.connect(path) as db:
                    legs = db.execute('SELECT account_id, debit_minor_units, credit_minor_units '
                        'FROM posting_lines WHERE transaction_id=?', (credit['id'],)).fetchall()
                    assert sorted(legs) == sorted([
                        (asset, 800, 0), (books['cogs'], 0, 800),
                        (books['income'], 1200, 0), (receivable, 0, 1200)]), surface
                    # Every batch balances, not merely the aggregate company balance.
                    assert db.execute('SELECT batch_id FROM posting_lines GROUP BY batch_id '
                        'HAVING sum(debit_minor_units) != sum(credit_minor_units)').fetchall() == [], surface
                    movements = db.execute('SELECT kind, quantity_microunits, value_minor_units '
                        'FROM inventory_movements WHERE item_id=? '
                        'ORDER BY effective_date, sequence, id', (item,)).fetchall()
                    assert movements == [('receipt', 2_000_000, 1600),
                        ('issue', -1_000_000, -800), ('receipt', 1_000_000, 800)], surface
                    assert db.execute('SELECT created_via FROM inventory_movements '
                        'WHERE transaction_id=?', (credit['id'],)).fetchall() == [(surface,)], surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                completed.add(surface)
            assert completed == {'cli', 'http', 'mcp'}
        finally:
            await matrix.close()

    asyncio.run(witness())
