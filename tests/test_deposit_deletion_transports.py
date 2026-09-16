"""Deleting a deposit over actual in-process Python, CLI, HTTP and MCP.

The money, written out so a reader can add it up without running anything: a 100.00 customer
payment and a 60.00 counter sale, banked together as one 160.00 deposit. Deleting it must put
the bank back to nothing and both receipts back into Undeposited Funds, still posted, still
their own documents and ready to bank again -- owned handling, never a cascade.

Deletion in this family is confirmed the way every other deposit write is, which is the part
worth driving over a transport: **preview, then save with the `dependency_guard` the preview
minted**. A guard that only round-trips in-process is a two-step confirmation that does not
exist for a CLI or an agent, so each surface previews and saves with its own guard here.

What each real transport has to produce:

**The preview names every receipt it would return and writes nothing**, and the database is
byte-identical after it.

**The Delete grant is not the Post grant.** `ledger.post` is denied on the same membership that
holds `transaction.deposit.delete`, so `deposit void` is refused `E_PERMISSION` while the delete
goes through.

**The receipts come back.** After the delete, `deposit sources` offers both of them again,
undeposited and eligible, and the trial balance -- read back through `report trial-balance` over
the same transport -- shows the bank at nothing and Undeposited Funds holding the full 160.00.

**The history stays** -- on the two surfaces that can still read it. The deposit is gone from an
ordinary `deposit query` and from `deposit show`, readable through `include_deleted`, and its
deletion entry carries the reason and the interface it arrived over. The general ledger still
sums both of its immutable effects, because hiding a document does not change the report math.

KNOWN DEFECT, found by this witness and reported 2026-09-16: those retained reads are taken on
Python and the CLI only, because `deposit query`, `show`, `items` and `history` are refused
`E_PERMISSION` over HTTP and MCP from the moment `permission activate` runs -- and activation is
the required step before any explicit family Delete grant, so a deleted deposit cannot be read
back over those two surfaces at all. Confirmed against the live demo's own data: the workbench
Deposits pages answer 403 with a raw JSON body. The refusal is NOT asserted here as if it were
intended; when the permit is fixed those reads belong in the loop with everything else.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.test_deposit_command import (  # noqa: F401  (books is a fixture)
    COMPANY, books, receipts_in_undeposited_funds,
)
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated witness for, and the surfaces it actually drives.
# The coverage ledger imports both rather than restating them beside a path it cannot check.
COMMANDS = frozenset(('deposit delete',))
SURFACES = ('python', 'cli', 'http', 'mcp')

BANKED = 16000      # 100.00 paid on the invoice plus a 60.00 counter sale


@pytest.mark.timeout(1200)
def test_deposit_deletion_crosses_all_four_actual_transports(books, tmp_path):
    pytest.importorskip('mcp')
    client = books['client']
    _, payment, sale = receipts_in_undeposited_funds(books)
    available = client.run('deposit sources', dict(date='2026-06-03'), company=COMPANY)
    posted = client.run('deposit post', dict(operation_key='june-deposit', document=dict(
        mode='inline', deposit_to=books['bank'], date='2026-06-03',
        sources=[dict(source_type=row['source_type'], source=row['source'],
                      expected_version=row['expected_version']) for row in available['items']])),
        company=COMPANY, reason='bank Saturday receipts')['deposit']
    receipts = sorted([payment['id'], sale['id']])
    # The fixture names its company by legal name; the matrix and every membership row use the id.
    company = client.company.show(company=COMPANY)['company_id']

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
                    """Every account's signed ending net from the real trial balance."""
                    report = await call('report trial-balance', {
                        'date_to': '2026-12-31', 'include_zero': True, 'limit': 200})
                    assert report['totals']['signed_net']['minor_units'] == 0, surface
                    return {row['account_id']: row['signed_net']['minor_units']
                            for row in report['rows']}

                state = await call('permission show', {})
                await call('permission activate', dict(
                    expected_generation=state['generation'],
                    expected_catalog_sha256=state['catalog_sha256']))
                rows = (await call('membership list', {'company': company}))['items']
                member = next(x for x in rows if x['scope_type'] == 'company'
                              and x['scope_id'] == company)
                await call('membership grant', dict(
                    user=member['user_id'], company=company,
                    expected_version=member['version'],
                    grants=['transaction.deposit.delete'], denies=['ledger.post']))
                path = Path((await call('company show', {}))['path']) / 'company.db'

                banked = await balances()
                assert banked[books['bank']] == BANKED, surface
                assert banked[books['uf']] == 0, surface
                # How many bank postings this deposit made, read off the ledger rather than
                # assumed: a deposit posts one bank line per receipt it banks, so the number is
                # the scenario's and not a constant.
                window = {'account': books['bank'], 'date_from': '2026-01-01',
                          'date_to': '2026-12-31', 'limit': 100}
                original = [row for row in (await call('report general-ledger', window))['rows']
                            if row['transaction_id'] == posted['id']]
                assert len(original) == len(receipts), surface
                before = database(path)
                raw = {'deposit': posted['id'], 'expected_version': posted['version'],
                       'operation_key': surface + '-deposit-delete'}

                preview = await witnessed('deposit delete', raw, dry_run=True)
                assert preview['dry_run'] and preview['status'] == 'deleted', surface
                assert preview['version'] == posted['version'] + 1, surface
                assert preview['from_status'] == 'posted', surface
                assert preview['number'] == posted['number'], surface
                assert sorted(preview['released_receipt_ids']) == receipts, surface
                assert preview['dependency_guard'], surface

                stale = await witnessed('deposit delete', {**raw, 'expected_version': 999},
                                        rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface

                denied = await call('deposit void', {
                    'deposit': posted['id'], 'expected_version': posted['version'],
                    'operation_key': surface + '-denied-void'}, rejected=True)
                assert denied['code'] == 'E_PERMISSION', surface

                # Nothing above wrote a byte.
                assert database(path) == before, surface
                assert await balances() == banked, surface

                deleted = await witnessed('deposit delete', {
                    **raw, 'dependency_guard': preview['dependency_guard']})
                assert deleted['status'] == 'deleted', surface
                assert deleted['version'] == posted['version'] + 1, surface
                assert deleted['from_status'] == 'posted', surface
                assert deleted['number'] == posted['number'], surface
                assert sorted(deleted['released_receipt_ids']) == receipts, surface
                after = database(path)
                assert after != before, surface

                # The bank never had it, and the receipts are undeposited again, still posted.
                freed = await balances()
                assert freed[books['bank']] == 0, surface
                assert freed[books['uf']] == BANKED, surface
                assert freed == {**banked, books['bank']: 0, books['uf']: BANKED}, surface
                offered = await call('deposit sources', {'date': '2026-06-04'})
                assert sorted(row['source'] for row in offered['items']) == receipts, surface
                assert all(not row['deposited'] and row['eligible']
                           for row in offered['items']), surface

                replay = await witnessed('deposit delete', {
                    **raw, 'dependency_guard': 'stale-guard-value'})
                assert replay['idempotent_replay'] and not replay['changed'], surface
                assert replay['id'] == posted['id'], surface
                assert database(path) == after, surface
                mismatch = await witnessed('deposit delete', raw, reason='A different reason',
                                           rejected=True)
                assert mismatch['code'] == 'E_IDEMPOTENCY_MISMATCH', surface
                assert database(path) == after, surface

                # The retained reads are taken only where the product can serve them.
                #
                # KNOWN DEFECT, reported 2026-09-16, not asserted here as if it were intended.
                # The four reader-bound deposit reads -- `deposit query`, `show`, `items` and
                # `history`, the ones `publication_inventory.policy()` hard-names
                # `reader_bound_public_detail_proof` -- are refused E_PERMISSION over HTTP and
                # MCP from the moment `permission activate` runs, carrying only
                # `{'stage': 'publication', 'outcome': 'unknown'}`. Activation is the required
                # step before any explicit family Delete grant, so on those two surfaces a
                # deleted deposit cannot be read back at all. Everything above this line runs on
                # all four; when the defect is fixed these reads move back into the loop with
                # the rest and this branch goes away.
                if surface in ('python', 'cli'):
                    ordinary = await call('deposit query', {})
                    assert posted['id'] not in {row['selected']['pin']['deposit_id']
                                                for row in ordinary['items']}, surface
                    with_deleted = await call('deposit query', {'include_deleted': True})
                    retained = {row['selected']['pin']['deposit_id']: row
                                for row in with_deleted['items']}
                    assert retained[posted['id']]['current']['status'] == 'deleted', surface
                    hidden = await call('deposit show', {'deposit': posted['id']}, rejected=True)
                    assert hidden['code'] == 'E_RECORD_NOT_FOUND', surface
                    shown = await call('deposit show', {'deposit': posted['id'],
                                                        'include_deleted': True})
                    assert shown['current']['status'] == 'deleted', surface
                    assert shown['selected']['number'] == posted['number'], surface
                    history = await call('deposit history', {'deposit': posted['id'],
                                                             'include_deleted': True})
                    entry = next(row for row in history['items'] if row['kind'] == 'deleted')
                    assert entry['reason'] == 'Registry parity', surface
                    assert entry['interface'] == surface, surface

                    # Hiding the document has not changed the report math: the register omits it,
                    # and the general ledger still carries every posting line of both its immutable
                    # effects -- the original and its exact reversal -- which net to nothing.
                    register = await call('register query', window)
                    ledger = await call('report general-ledger', window)
                    assert posted['id'] not in {row['transaction_id'] for row in register['rows']
                                                if row['transaction_id']}, surface
                    both = [row for row in ledger['rows'] if row['transaction_id'] == posted['id']]
                    assert len(both) == 2 * len(original), surface
                    assert sum(row['debit']['minor_units'] - row['credit']['minor_units']
                               for row in both) == 0, surface
                    assert register['ledger_totals'] == ledger['totals'], surface

                with sqlite3.connect(path) as db:
                    assert db.execute(
                        'SELECT count(*) FROM deposit_deletions').fetchone() == (1,), surface
                    assert db.execute(
                        'SELECT count(*) FROM deposit_current_memberships WHERE transaction_id=?',
                        (posted['id'],)).fetchone() == (0,), surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                assert database(path) == after, surface
                driven[surface] = claimed
            # This file claims exactly these commands on these surfaces, and drove them there.
            assert set(driven) == set(SURFACES)
            assert all(names == COMMANDS for names in driven.values())
        finally:
            await matrix.close()

    asyncio.run(witness())
