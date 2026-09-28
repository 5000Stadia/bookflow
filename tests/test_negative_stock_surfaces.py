"""Selling below zero reads the same over Python, the CLI, HTTP and MCP, and in the browser.

One case, by hand: 3 bought at 10.00; an invoice for 5 takes 3000 for the three on hand and
2 x 1000 at the average for the two below zero -- -2 on hand worth -2000, with one warning.
A bill for 5 at 12.00 then fills the shortfall: those two really cost 2400, so the true-up is
-400 dated at the bill, and 3 are left at 3600. Every surface starts from its own identical
copy of the same books and must say exactly that, warning text included.
"""
import asyncio
import html
import re
import sqlite3
from pathlib import Path

import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_bill_item_lines import books  # noqa: F401
from tests.test_row3_host import WB, hosted  # noqa: F401
from tests.test_row5_workbench_forms import _browser, _encode_collection

WARNING = ('Takes Parity Widget to -2 on 2017-01-05; its cost is provisional, at the average '
           'cost, until a receipt brings the item back up and trues it up.')


@pytest.mark.timeout(900)
def test_the_same_sale_below_zero_over_python_cli_http_and_mcp(books, tmp_path):
    pytest.importorskip('mcp')
    item = books['client'].item.create(
        company=books['company'], name='Parity Widget', type='inventory_part',
        description='Parity Widget', price='20.00', purchase_description='Parity Widget',
        cost='10.00', cogs_account_id=books['cogs'], income_account_id=books['income'])['id']
    books['run']('bill post', dict(vendor=books['vendor'], date='2017-01-01',
        items=[dict(item=item, quantity='3', unit_cost='10')]), reason='Receive stock')
    sale = dict(customer=books['customer'], date='2017-01-05',
                lines=[dict(item=item, quantity='5', unit_price='20')])
    bill = dict(vendor=books['vendor'], date='2017-01-10',
                items=[dict(item=item, quantity='5', unit_cost='12')])

    async def witness():
        matrix = Matrix()
        seen = {}
        try:
            await matrix.open(Path(books['client'].data_root), tmp_path / 'surfaces')
            for surface in ('python', 'cli', 'http', 'mcp'):
                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                async def stock():
                    page = await call('report stock-status', dict(as_of='2017-12-31', limit=200))
                    row = next(row for row in page['rows'] if row['item_id'] == item)
                    return row['quantity_on_hand'], row['asset_value']['minor_units']

                if surface != 'python':
                    state = await call('permission show', {})
                    await call('permission activate', dict(
                        expected_generation=state['generation'],
                        expected_catalog_sha256=state['catalog_sha256']))
                path = Path((await call('company show', {}))['path']) / 'company.db'
                preview = await call('invoice post', sale, dry_run=True)
                posted = await call('invoice post', sale)
                after_sale = await stock()
                received = await call('bill post', bill)
                with sqlite3.connect(path) as db:
                    true_ups = db.execute(
                        'SELECT effective_date, value_minor_units FROM inventory_movements '
                        'WHERE item_id = ? AND filled_by_movement_id IS NOT NULL', (item,)).fetchall()
                seen[surface] = dict(
                    preview=[w for w in preview['warnings'] if w.startswith('Takes ')],
                    posted=[w for w in posted['warnings'] if w.startswith('Takes ')],
                    after_sale=after_sale, true_ups=true_ups, after_bill=await stock(),
                    bill_warnings=[w for w in received['warnings'] if w.startswith('Takes ')])
        finally:
            await matrix.close()
        return seen

    seen = asyncio.run(witness())
    expected = dict(preview=[WARNING], posted=[WARNING], after_sale=('-2', -2000),
                    true_ups=[('2017-01-10', -400)], after_bill=('3', 3600), bill_warnings=[])
    assert seen == {surface: expected for surface in ('python', 'cli', 'http', 'mcp')}


@pytest.mark.timeout(900)
def test_the_browser_shows_the_warning_on_the_preview_and_after_saving(hosted):
    company = hosted.company_id
    accounts = {row['full_name']: row['id'] for row in
                hosted.ok('account.query', {'limit': 200}, company=company)['items']}
    item = hosted.ok('item.create', dict(
        name='Browser Widget', type='inventory_part', description='Browser Widget', price='20.00',
        purchase_description='Browser Widget', cost='4.00',
        income_account_id=accounts['Construction Income'],
        cogs_account_id=accounts['Cost of Goods Sold']), company=company)['id']
    customer = hosted.ok('customer.create', dict(name='Browser buyer'), company=company)['id']
    browser = _browser(hosted)
    route = f'/c/{company}/invoice/post'
    browser.get(route)
    form = {'f:customer': customer, 'f:date': '2026-09-01',
            **_encode_collection('lines', [dict(item=item, quantity='2', unit_price='20')])}
    previewed = browser.post(route, headers=WB, data={**form, 'action': 'preview'})
    assert previewed.status_code == 200
    text = html.unescape(previewed.text)
    warning = ('Takes Browser Widget to -2 on 2026-09-01; its cost is provisional, at the purchase '
               'cost on the item record, because it has never had stock')
    assert warning in text
    fingerprint = re.search(r'name="f:expected_facts_fingerprint" value="([0-9a-f]{64})"',
                            previewed.text).group(1)
    saved = browser.post(route, headers=WB, data={**form, 'action': 'submit',
                                                   'f:expected_facts_fingerprint': fingerprint},
                         follow_redirects=True)
    assert saved.status_code == 200
    assert warning in html.unescape(saved.text)
