"""Actual CLI, HTTP and source-bound MCP deletion of a vendor bill.

The figures are the fixture's, and every one is written out here so a reader can add them up:
each bill is 2 x 8.00 of an inventory part plus 4.83 of freight, so 20.83 on Accounts Payable,
16.00 on Inventory Asset and 4.83 on Freight In. Two are entered -- DEL-1, which is deleted,
and KEEP-1, which a 20.83 bill payment answers. What this file proves over each real transport
is what a person gets, not that a call returned:

**The preview names the stock it would cancel and writes nothing.** `cancelled_stock_movements`
is the one receipt this bill brought in, and the whole database is byte-identical afterwards.

**The Delete grant is not the Post grant.** `ledger.post` is denied on the same membership that
holds `transaction.bill.delete`, so `bill update` is refused `E_PERMISSION` while the delete
goes through -- which is the separation `What it must never do` requires.

**A settled bill refuses and names what holds it.** The bill the payment answers refuses with
`E_HAS_APPLICATIONS` naming that payment, writes nothing, and still reads as paid in full.

**Deleting takes the money and the stock back out and keeps the history.** The general ledger,
read back through `report general-ledger` over the same transport, falls by exactly DEL-1's own
three postings and by nothing else; the inventory movement it made is gone; the revision and
the number are still readable through `include_deleted`, and the bill is gone from `bill query`.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.test_bill_item_lines import (  # noqa: F401  (books is a fixture)
    books, _inventory_asset, _inventory_part,
)
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated transport witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('bill delete',))

STOCK = 1600        # 2 x 8.00 of an inventory part, onto Inventory Asset
FREIGHT = 483       # 4.83 of delivery, onto Freight In
BILL = STOCK + FREIGHT   # 20.83 owed to the vendor


def _stocked(books, item, number, date):
    return books['run']('bill post', dict(
        vendor=books['vendor'], date=date, number=number, memo='Stocked ' + number,
        expenses=[{'account': books['freight'], 'amount': '4.83', 'memo': 'Delivery'}],
        items=[{'item': item, 'quantity': '2', 'unit_cost': '8.00'}]), reason='Enter ' + number)


@pytest.mark.timeout(600)
def test_bill_deletion_crosses_cli_http_and_source_bound_mcp_with_exact_books(books, tmp_path):
    pytest.importorskip('mcp')
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    doomed = _stocked(books, item, 'DEL-1', '2017-03-03')
    settled = _stocked(books, item, 'KEEP-1', '2017-03-04')
    paid = books['run']('bill pay', dict(
        date='2017-03-05', funding_account=books['bank'], method=books['methods']['Check'],
        bills=[{'bill': settled['id'], 'amount': '20.83'}]), reason='Pay KEEP-1')
    cheque = paid['payments'][0]['id']
    # Settling a bill moves its version, so the refusal below is asked with the current one --
    # otherwise the version guard answers first and E_HAS_APPLICATIONS is never reached.
    held_version = books['run']('bill show', {'bill': settled['id']})['version']

    # What the books say before anything is deleted, and what they have to say after.
    BEFORE = {books['payable']: -BILL * 2 + BILL, books['freight']: FREIGHT * 2,
              asset: STOCK * 2, books['bank']: -BILL}
    AFTER = {books['freight']: FREIGHT, asset: STOCK, books['bank']: -BILL}

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(Path(books['client'].data_root), tmp_path / 'surfaces')
            driven = {}
            for surface in ('cli', 'http', 'mcp'):
                claimed = set()

                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                async def witnessed(name, raw, **ctx):
                    """Drive a command this file is the ledger's witness for, and record it."""
                    claimed.add(name)
                    return await call(name, raw, **ctx)

                async def ledger():
                    """Signed minor units per account, read through the real report command."""
                    page = await call('report general-ledger', {
                        'date_from': '2017-01-01', 'date_to': '2017-12-31', 'limit': 200})
                    net = {}
                    for line in page['rows']:
                        if line['kind'] != 'posting':
                            continue
                        net[line['account_id']] = net.get(line['account_id'], 0) + (
                            line['debit']['minor_units'] - line['credit']['minor_units'])
                    return {account: units for account, units in net.items() if units}

                state = await call('permission show', {})
                await call('permission activate', dict(
                    expected_generation=state['generation'],
                    expected_catalog_sha256=state['catalog_sha256']))
                rows = (await call('membership list', {'company': books['company']}))['items']
                member = next(x for x in rows if x['scope_type'] == 'company'
                              and x['scope_id'] == books['company'])
                await call('membership grant', dict(
                    user=member['user_id'], company=books['company'],
                    expected_version=member['version'],
                    grants=['transaction.bill.delete'], denies=['ledger.post']))
                path = Path((await call('company show', {}))['path']) / 'company.db'

                assert await ledger() == BEFORE, surface
                before = database(path)
                raw = {'bill': doomed['id'], 'expected_version': 1,
                       'operation_key': surface + '-bill-delete'}

                preview = await witnessed('bill delete', raw, dry_run=True)
                assert preview['dry_run'] and preview['status'] == 'deleted', surface
                assert preview['version'] == 2 and preview['from_status'] == 'posted', surface
                assert preview['cancelled_stock_movements'] == 1, surface
                assert preview['number'] == doomed['number'], surface
                assert preview['purchase_order_id'] is None, surface

                stale = await witnessed('bill delete', {**raw, 'expected_version': 999}, rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface

                denied = await call('bill update', {
                    'bill': doomed['id'], 'expected_version': 1, 'memo': 'Denied'}, rejected=True)
                assert denied['code'] == 'E_PERMISSION', surface

                # A bill a payment answers refuses by name, and it still reads as paid.
                held = await witnessed('bill delete', {
                    'bill': settled['id'], 'expected_version': held_version,
                    'operation_key': surface + '-held'}, rejected=True)
                assert held['code'] == 'E_HAS_APPLICATIONS', surface
                assert held['details']['bill_id'] == settled['id'], surface
                assert held['details']['settlement_transaction_ids'] == [cheque], surface
                assert held['details']['application_ids'], surface
                assert 'bill payment unapply' in held['details']['next'], surface
                still = await call('bill show', {'bill': settled['id']})
                assert still['settlement_current']['open_minor_units'] == 0, surface
                assert still['settlement_current']['status'] == 'paid', surface

                # Nothing above wrote a byte.
                assert database(path) == before, surface
                assert await ledger() == BEFORE, surface

                deleted = await witnessed('bill delete', raw)
                assert deleted['status'] == 'deleted' and deleted['version'] == 2, surface
                assert deleted['from_status'] == 'posted', surface
                assert deleted['cancelled_stock_movements'] == 1, surface
                assert deleted['number'] == doomed['number'], surface
                after = database(path)
                assert after != before, surface
                assert await ledger() == AFTER, surface

                replay = await witnessed('bill delete', raw)
                assert replay['idempotent_replay'] and not replay['changed'], surface
                assert database(path) == after, surface
                losing = await witnessed('bill delete', {**raw, 'operation_key': 'losing-bill'},
                                    rejected=True)
                assert losing['code'] == 'E_VERSION_CONFLICT', surface

                gone = await call('bill show', {'bill': doomed['id']}, rejected=True)
                assert gone['code'] == 'E_RECORD_NOT_FOUND', surface
                shown = await call('bill show', {'bill': doomed['id'], 'include_deleted': True})
                assert shown['status'] == 'deleted' and shown['number'] == doomed['number'], surface
                # Who deleted it, through which interface, and why -- retained on the record.
                assert shown['deletion']['from_status'] == 'posted', surface
                assert shown['deletion']['reason'] == 'Registry parity', surface
                assert shown['deletion']['created_via'] == surface, surface
                history = await call('bill history', {'bill': doomed['id'], 'include_deleted': True})
                assert history['status'] == 'deleted', surface
                assert history['items'][0]['id'] == doomed['revision']['id'], surface
                listed = {row['id'] for row in (await call('bill query', {'limit': 50}))['items']}
                assert doomed['id'] not in listed and settled['id'] in listed, surface

                with sqlite3.connect(path) as db:
                    assert db.execute(
                        'SELECT sum(quantity_microunits),sum(value_minor_units)'
                        ' FROM inventory_movements WHERE item_id=?',
                        (item,)).fetchone() == (2000000, STOCK), surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                assert database(path) == after, surface
                driven[surface] = claimed
            # This file claims exactly these commands on these surfaces, and drove them there.
            assert set(driven) == {'cli', 'http', 'mcp'}
            assert all(names == COMMANDS for names in driven.values())
        finally:
            await matrix.close()

    asyncio.run(witness())
