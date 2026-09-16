"""Deleting a credit memo over actual in-process Python, CLI, HTTP and MCP.

Every figure is written out here so a reader can add it up without running anything. Three
credits are entered against one company, and only the first is deletable:

    RETURNED   one taxable unit of a four-unit 100.00 invoice sent back, 8% tax
               25.00 of income and 2.00 of tax taken back, so 27.00 off the receivable
    APPLIED    a 30.00 goodwill credit, applied to a 100.00 invoice
    REFUNDED   a 30.00 goodwill credit, paid back to the customer in cash

What each real transport has to produce:

**The preview names the invoice quantity it would give back and writes nothing.**
`released_source_claims` is the one return claim this credit holds and `source_invoice_ids`
names the invoice that issued it, and the whole database is byte-identical after the dry run.

**The Delete grant is not the Post grant.** `ledger.post` is denied on the same membership that
holds `transaction.credit_memo.delete`, so `credit-memo void` is refused `E_PERMISSION` while
the delete goes through.

**A credit something still stands on refuses by name, and never cascades.** The applied credit
refuses `E_HAS_APPLICATIONS` naming the invoice holding it and pointing at
`customer-credit unapply`; the refunded one refuses `E_HAS_REFUND` naming the refund drawn on
it and pointing at `customer-refund void`. Neither writes a byte, and neither invoice nor
refund is touched.

**Deleting takes the income, the tax and the receivable back out and keeps the history.** The
trial balance, read back through `report trial-balance` over the same transport, returns to
exactly what it was before the credit was written; the credit is gone from `credit-memo query`
but readable through `include_deleted`, and the retained record names the interface it arrived
over.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.credit_support import (  # noqa: F401  (books is a fixture)
    apply_credit, books, goodwill_credit, invoice, refund, returned_credit, taxed_invoice,
)
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated witness for, and the surfaces it actually drives.
# The coverage ledger imports both rather than restating them beside a path it cannot check.
COMMANDS = frozenset(('credit-memo delete',))
SURFACES = ('python', 'cli', 'http', 'mcp')

RETURN_NET = 2500       # 25.00 of income given back on the returned unit
RETURN_TAX = 200        # 8% of it
RETURN_GROSS = 2700     # what comes off the receivable


@pytest.mark.timeout(1200)
def test_credit_memo_deletion_crosses_all_four_actual_transports(books, tmp_path):
    pytest.importorskip('mcp')
    sale = taxed_invoice(books)
    doomed = returned_credit(books, sale['id'], sale['revision']['lines'][0]['line_id'])

    billed = invoice(books)
    applied_credit = goodwill_credit(books, '30.00')
    apply_credit(books, applied_credit, billed['id'], amount='30.00')

    refunded_credit = goodwill_credit(books, '30.00', date='2026-03-11')
    paid = refund(books, refunded_credit['id'], amount='30.00')

    version = books['run']('credit-memo show', {'credit_memo': doomed['id']})['version']

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(Path(books['client'].data_root), tmp_path / 'surfaces')
            driven = {}
            for surface in SURFACES:
                claimed = set()

                async def call(name, raw, **ctx):
                    return await matrix.call(surface, name, raw, **ctx)

                async def witnessed(name, raw, **ctx):
                    """Drive a command this file is the ledger's witness for, and record it."""
                    claimed.add(name)
                    return await call(name, raw, **ctx)

                async def balances():
                    """Signed minor units per account from the real trial balance, debits +."""
                    report = await call('report trial-balance',
                                        {'date_to': '2026-04-30', 'limit': 200})
                    assert report['totals']['debit'] == report['totals']['credit'], surface
                    return {row['account_id']: row['debit']['minor_units']
                            - row['credit']['minor_units'] for row in report['rows']}

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
                    grants=['transaction.credit_memo.delete'], denies=['ledger.post']))
                path = Path((await call('company show', {}))['path']) / 'company.db'

                opening = await balances()
                before = database(path)
                raw = {'credit_memo': doomed['id'], 'expected_version': version,
                       'operation_key': surface + '-credit-delete'}

                preview = await witnessed('credit-memo delete', raw, dry_run=True)
                assert preview['dry_run'] and preview['status'] == 'deleted', surface
                assert preview['version'] == version + 1, surface
                assert preview['from_status'] == 'posted', surface
                assert preview['number'] == doomed['number'], surface
                # A live return claim is owned handling, not a refusal: the interval goes back.
                assert preview['released_source_claims'] == 1, surface
                assert preview['source_invoice_ids'] == [sale['id']], surface

                stale = await witnessed('credit-memo delete', {**raw, 'expected_version': 999},
                                        rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface

                denied = await call('credit-memo void', {
                    'credit_memo': doomed['id'], 'expected_version': version}, rejected=True)
                assert denied['code'] == 'E_PERMISSION', surface

                # A credit an invoice still holds refuses by name and says what to do about it.
                held = await witnessed('credit-memo delete', {
                    'credit_memo': applied_credit['id'],
                    'expected_version': (await call('credit-memo show',
                                                    {'credit_memo': applied_credit['id']}))['version'],
                    'operation_key': surface + '-applied'}, rejected=True)
                assert held['code'] == 'E_HAS_APPLICATIONS', surface
                assert held['details']['credit_memo_id'] == applied_credit['id'], surface
                assert held['details']['invoice_ids'] == [billed['id']], surface
                assert len(held['details']['application_ids']) == 1, surface
                assert 'customer-credit unapply' in held['details']['next'], surface

                # A credit a refund was paid out of refuses on the same capacity edge.
                spent = await witnessed('credit-memo delete', {
                    'credit_memo': refunded_credit['id'],
                    'expected_version': (await call('credit-memo show',
                                                    {'credit_memo': refunded_credit['id']}))['version'],
                    'operation_key': surface + '-refunded'}, rejected=True)
                assert spent['code'] == 'E_HAS_REFUND', surface
                assert spent['details']['credit_memo_id'] == refunded_credit['id'], surface
                assert spent['details']['refund_ids'] == [paid['id']], surface
                assert len(spent['details']['consumption_ids']) == 1, surface
                assert 'customer-refund void' in spent['details']['next'], surface

                # Not one of those refusals wrote a byte.
                assert database(path) == before, surface
                assert await balances() == opening, surface

                deleted = await witnessed('credit-memo delete', raw)
                assert deleted['status'] == 'deleted' and deleted['version'] == version + 1, surface
                assert deleted['from_status'] == 'posted', surface
                assert deleted['number'] == doomed['number'], surface
                assert deleted['released_source_claims'] == 1, surface
                after = database(path)
                assert after != before, surface

                # The income, the tax and the receivable the credit took back are back.
                closing = await balances()
                assert closing[books['income']] == opening[books['income']] - RETURN_NET, surface
                assert closing[books['liability']] == opening[books['liability']] - RETURN_TAX, surface
                assert closing[books['receivable']] == opening[books['receivable']] + RETURN_GROSS, surface
                # And nothing else moved at all.
                assert {account: units for account, units in closing.items()
                        if units != opening.get(account, 0)} == {
                    books['income']: closing[books['income']],
                    books['liability']: closing[books['liability']],
                    books['receivable']: closing[books['receivable']]}, surface

                replay = await witnessed('credit-memo delete', raw)
                assert replay['idempotent_replay'] and not replay['changed'], surface
                assert database(path) == after, surface

                gone = await call('credit-memo show', {'credit_memo': doomed['id']}, rejected=True)
                assert gone['code'] == 'E_RECORD_NOT_FOUND', surface
                shown = await call('credit-memo show', {'credit_memo': doomed['id'],
                                                        'include_deleted': True})
                assert shown['status'] == 'deleted', surface
                assert shown['number'] == doomed['number'], surface
                # Who deleted it, through which interface, and why -- retained on the record.
                assert shown['deletion']['from_status'] == 'posted', surface
                assert shown['deletion']['reason'] == 'Registry parity', surface
                assert shown['deletion']['created_via'] == surface, surface
                history = await call('credit-memo history', {'credit_memo': doomed['id'],
                                                             'include_deleted': True})
                assert history['items'][0]['id'] == doomed['revision']['id'], surface
                listed = {row['id'] for row in
                          (await call('credit-memo query', {'limit': 50}))['items']}
                assert doomed['id'] not in listed, surface
                assert {applied_credit['id'], refunded_credit['id']} <= listed, surface

                with sqlite3.connect(path) as db:
                    assert db.execute(
                        "SELECT count(*) FROM posting_batches WHERE transaction_id=?"
                        " AND kind='reversal'", (doomed['id'],)).fetchone() == (1,), surface
                    assert db.execute(
                        'SELECT count(*) FROM credit_deletions').fetchone() == (1,), surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                assert database(path) == after, surface
                driven[surface] = claimed
            # This file claims exactly these commands on these surfaces, and drove them there.
            assert set(driven) == set(SURFACES)
            assert all(names == COMMANDS for names in driven.values())
        finally:
            await matrix.close()

    asyncio.run(witness())
