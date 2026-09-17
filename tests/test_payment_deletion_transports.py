"""Actual CLI, HTTP and source-bound MCP deletion of a customer payment.

`payment delete` is the command that was registered, tested and reviewed and still could not be
run over any transport but in-process Python. This file is the standing witness that it can.

The money is written out in full: a 25.00 invoice, a 25.00 receipt applied to it (KEPT), and a
second 25.00 receipt applied to nothing (DOOMED), both banked. So before anything is deleted
Accounts Receivable is 25.00 - 25.00 - 25.00 = -25.00, income is -25.00 and the bank holds
50.00. What each real transport has to produce:

**The preview names the postings it would cancel and writes nothing.** A banked receipt is two
posting lines, and the whole database is byte-identical after the dry run.

**The Delete grant is not the Post grant.** `ledger.post` is denied on the same membership that
holds `transaction.payment.delete`, so `payment void` is refused `E_PERMISSION` while the delete
goes through.

**An applied receipt refuses and names the application holding it.** `E_HAS_APPLICATIONS`
carries the application id and points at `payment unapply`, nothing is written, and the invoice
it answers still reads as paid in full afterwards.

**Deleting takes the cash back out and keeps the history.** The general ledger, read back
through `report general-ledger` over the same transport, loses exactly the doomed receipt's two
postings; the revision and the number stay readable through `include_deleted`; the receipt is
gone from `payment query`.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.test_bill_item_lines import books  # noqa: F401  (books is a fixture)
from tests.test_sales_deletion import service_item
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated transport witness for; the coverage ledger imports
# this set rather than restating the names beside a path it cannot check.
COMMANDS = frozenset(('payment delete',))

SALE = 2500     # 25.00 invoiced, received twice over


def _receivable(books):
    return next(row['id'] for row in books['client'].account.query(
        company=books['company'], limit=200)['items'] if row['type'] == 'accounts_receivable')


@pytest.mark.timeout(600)
def test_payment_deletion_crosses_cli_http_and_source_bound_mcp_with_exact_books(books, tmp_path):
    pytest.importorskip('mcp')
    run = books['run']
    receivable = _receivable(books)
    item = service_item(books, 'Payment transport service')
    sale = run('invoice post', dict(customer=books['customer'], date='2017-01-01',
        lines=[dict(item=item, quantity='1', unit_price='25.00')]), reason='Bill the customer')
    kept = run('payment receive', dict(
        customer=books['customer'], date='2017-01-02', amount='25.00', operation_key='kept',
        payment_method=books['methods']['Cash'], deposit_to=books['bank'],
        applications=dict(mode='inline', items=[
            dict(invoice=sale['id'], expected_version=sale['version'], amount='25.00')])),
        reason='Customer paid the invoice')
    application = kept['effect']['applications'][0]['application_id']
    doomed = run('payment receive', dict(
        customer=books['customer'], date='2017-01-03', amount='25.00', operation_key='doomed',
        payment_method=books['methods']['Cash'], deposit_to=books['bank']),
        reason='The same money entered twice')
    # `payment receive` returns the effect, not the document; the number and the revision a
    # deletion has to keep readable come off the saved receipt itself.
    saved = run('payment show', {'payment': doomed['id']})
    NUMBER, REVISION = saved['number'], saved['revision']['id']

    BEFORE = {receivable: SALE - SALE - SALE, books['income']: -SALE, books['bank']: SALE * 2}
    AFTER = {books['income']: -SALE, books['bank']: SALE}

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
                    grants=['transaction.payment.delete'], denies=['ledger.post']))
                path = Path((await call('company show', {}))['path']) / 'company.db'

                assert await ledger() == BEFORE, surface
                before = database(path)
                raw = {'payment': doomed['id'], 'expected_version': 1,
                       'operation_key': surface + '-payment-delete'}

                preview = await witnessed('payment delete', raw, dry_run=True)
                assert preview['dry_run'] and preview['status'] == 'deleted', surface
                assert preview['version'] == 2 and preview['from_status'] == 'posted', surface
                assert preview['cancelled_posting_lines'] == 2, surface
                assert preview['number'] == NUMBER, surface

                stale = await witnessed('payment delete', {**raw, 'expected_version': 999},
                                   rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface

                denied = await call('payment void', {
                    'payment': doomed['id'], 'expected_version': 1,
                    'operation_key': surface + '-denied'}, rejected=True)
                assert denied['code'] == 'E_PERMISSION', surface

                # The applied receipt refuses by name and says what to do about it.
                held = await witnessed('payment delete', {
                    'payment': kept['id'], 'expected_version': kept['version'],
                    'operation_key': surface + '-held'}, rejected=True)
                assert held['code'] == 'E_HAS_APPLICATIONS', surface
                assert held['details']['payment_id'] == kept['id'], surface
                assert held['details']['application_ids'] == [application], surface
                assert 'payment unapply' in held['details']['next'], surface
                settled = await call('invoice settlement', {'invoice': sale['id']})
                assert settled['due_minor_units'] == 0, surface
                assert settled['applied_minor_units'] == SALE, surface

                # Nothing above wrote a byte.
                assert database(path) == before, surface
                assert await ledger() == BEFORE, surface

                deleted = await witnessed('payment delete', raw)
                assert deleted['status'] == 'deleted' and deleted['version'] == 2, surface
                assert deleted['from_status'] == 'posted', surface
                assert deleted['cancelled_posting_lines'] == 2, surface
                assert deleted['number'] == NUMBER, surface
                after = database(path)
                assert after != before, surface
                assert await ledger() == AFTER, surface

                replay = await witnessed('payment delete', raw)
                assert replay['idempotent_replay'] and not replay['changed'], surface
                assert database(path) == after, surface
                # A fresh permanent key over an already-deleted receipt refuses and writes
                # nothing, saying the delete already happened rather than that a version moved.
                # This family answered that way first and de565dd moved the other five to match,
                # because E_VERSION_CONFLICT is true and useless here: the version is stale
                # precisely BECAUSE the delete happened, so an agent re-reads it and retries for
                # ever. The wording below is the one all six now share.
                losing = await witnessed('payment delete', {**raw, 'operation_key': 'losing-payment'},
                                    rejected=True)
                assert losing['code'] == 'E_VALIDATION', surface
                assert losing['details']['fields'][0]['field'] == 'transaction', surface
                assert 'already deleted and cannot be deleted again' in \
                    losing['details']['fields'][0]['problem'], surface
                assert database(path) == after, surface

                gone = await call('payment show', {'payment': doomed['id']}, rejected=True)
                assert gone['code'] == 'E_RECORD_NOT_FOUND', surface
                shown = await call('payment show', {'payment': doomed['id'],
                                                    'include_deleted': True})
                assert shown['status'] == 'deleted' and shown['number'] == NUMBER, surface
                # Who deleted it, through which interface, and why -- retained on the record.
                assert shown['deletion']['from_status'] == 'posted', surface
                assert shown['deletion']['reason'] == 'Registry parity', surface
                assert shown['deletion']['created_via'] == surface, surface
                history = await call('payment history', {'payment': doomed['id'],
                                                         'include_deleted': True})
                assert history['total_count'] == 2, surface
                assert [row['revision']['id'] for row in history['items']
                        if row['kind'] == 'receipt_revision'] == [REVISION], surface
                assert [row['command'] for row in history['items']
                        if row['kind'] == 'operation'] == ['payment receive'], surface
                listed = {row['id'] for row in (await call('payment query', {'limit': 50}))['items']}
                assert doomed['id'] not in listed and kept['id'] in listed, surface

                with sqlite3.connect(path) as db:
                    assert db.execute(
                        "SELECT count(*) FROM posting_batches WHERE transaction_id=?"
                        " AND kind='reversal'", (doomed['id'],)).fetchone() == (1,), surface
                    assert db.execute(
                        'SELECT count(*) FROM payment_deletions').fetchone() == (1,), surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                assert database(path) == after, surface
                driven[surface] = claimed
            # This file claims exactly these commands on these surfaces, and drove them there.
            assert set(driven) == {'cli', 'http', 'mcp'}
            assert all(names == COMMANDS for names in driven.values())
        finally:
            await matrix.close()

    asyncio.run(witness())
