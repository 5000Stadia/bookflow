"""Deleting a journal entry over actual in-process Python, CLI, HTTP and MCP.

Every figure is the fixture's and every one is written out here: a hand-typed entry of 61.50
debiting Freight In and crediting Checking, a 25.00 cheque, a 12.00 card charge and a 500.00
transfer -- all four stored as `transactions.type = 'journal_entry'` and told apart only by a
row in `money_out_documents`.

That storage fact is why this family needs a transport witness more than the others do. Three
of those four documents are not plain journal entries, and a `journal delete` that accepted one
would write a second, conflicting tombstone on a document that already carries one. The guard
that holds it shut is asserted here on every surface, by name and with the whole database
unchanged afterwards -- because a guard that holds in-process and not over MCP is not a guard.

What each real transport has to produce:

**A cheque, a card charge and a transfer are each refused by name**, and the refusal says which
command owns the document: `check delete`, `card-charge delete`, `transfer void`. Nothing is
written, and `journal_deletions` stays empty.

**The Delete grant is not the Post grant.** `ledger.post` is denied on the same membership that
holds `transaction.journal_entry.delete`, so `journal void` is refused `E_PERMISSION` while the
delete goes through.

**The preview writes nothing** and the whole database is byte-identical after it.

**Deleting cancels the entry and keeps the history.** The general ledger, read back through
`report general-ledger` over the same transport, loses exactly the entry's two postings; the
entry is gone from `journal query` but readable through `include_deleted`, and the retained
deletion record names the interface it arrived over.
"""
import asyncio
from pathlib import Path
import sqlite3

import pytest

from tests.test_bill_item_lines import books  # noqa: F401  (books is a fixture)
from tests.mcp_matrix_support import Matrix
from tests.payment_raw_evidence import database


# The commands this test is the designated witness for, and the surfaces it actually drives.
# The coverage ledger imports both rather than restating them beside a path it cannot check.
COMMANDS = frozenset(('journal delete',))
SURFACES = ('python', 'cli', 'http', 'mcp')

ENTRY = 6150        # 61.50 accrued to Freight In against Checking
CHEQUE = 2500       # 25.00 paid to the carrier
CHARGE = 1200       # 12.00 of supplies on the card
MOVED = 50000       # 500.00 swept to savings


@pytest.mark.timeout(1200)
def test_journal_deletion_crosses_all_four_actual_transports(books, tmp_path):
    pytest.importorskip('mcp')
    run = books['run']
    card = books['client'].account.create(
        company=books['company'], name='Company Card', type='credit_card')['id']
    savings = books['client'].account.create(
        company=books['company'], name='Savings', type='bank')['id']
    doomed = run('journal post', dict(date='2017-04-02', memo='Duplicated freight accrual', lines=[
        dict(account=books['freight'], side='debit', amount='61.50'),
        dict(account=books['bank'], side='credit', amount='61.50')]), reason='Enter the entry')
    cheque = run('check post', dict(account=books['bank'], date='2017-03-04', amount='25.00',
        expenses=[dict(account=books['freight'], amount='25.00')]), reason='Pay the carrier')
    charge = run('card-charge post', dict(account=card, date='2017-03-06', amount='12.00',
        expenses=[dict(account=books['freight'], amount='12.00')]), reason='Buy supplies')
    moved = run('transfer post', dict(from_account=books['bank'], to_account=savings,
        date='2017-03-07', amount='500.00'), reason='Sweep to savings')

    # What the books say before anything is deleted, and what they have to say after.
    BEFORE = {books['freight']: ENTRY + CHEQUE + CHARGE,
              books['bank']: -(ENTRY + CHEQUE + MOVED), card: -CHARGE, savings: MOVED}
    AFTER = {books['freight']: CHEQUE + CHARGE,
             books['bank']: -(CHEQUE + MOVED), card: -CHARGE, savings: MOVED}

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
                    grants=['transaction.journal_entry.delete'], denies=['ledger.post']))
                path = Path((await call('company show', {}))['path']) / 'company.db'

                assert await ledger() == BEFORE, surface
                before = database(path)

                # The guard this family exists to hold: three documents stored as journal
                # entries that are not plain journal entries, each refused by the name of the
                # command that owns it, on every surface.
                for document, owner, description in (
                        (cheque, 'check delete', 'a check'),
                        (charge, 'card-charge delete', 'a credit card charge'),
                        (moved, 'transfer void', 'a transfer')):
                    alias = await witnessed('journal delete', dict(
                        journal=document['id'], expected_version=document['version'],
                        operation_key=surface + '-alias-' + owner), rejected=True)
                    assert alias['code'] == 'E_VALIDATION', (surface, owner)
                    assert alias['details']['next'] == owner, (surface, owner)
                    assert alias['details']['transaction_id'] == document['id'], (surface, owner)
                    assert ('This entry is ' + description + ', not a plain journal entry.'
                            ) in alias['message'], (surface, owner)
                assert database(path) == before, surface
                with sqlite3.connect(path) as db:
                    assert db.execute(
                        'SELECT count(*) FROM journal_deletions').fetchone() == (0,), surface

                raw = {'journal': doomed['id'], 'expected_version': doomed['version'],
                       'operation_key': surface + '-journal-delete'}
                preview = await witnessed('journal delete', raw, dry_run=True)
                assert preview['dry_run'] and preview['status'] == 'deleted', surface
                assert preview['version'] == doomed['version'] + 1, surface
                assert preview['from_status'] == 'posted', surface
                assert preview['number'] == doomed['number'], surface

                stale = await witnessed('journal delete', {**raw, 'expected_version': 999},
                                        rejected=True)
                assert stale['code'] == 'E_VERSION_CONFLICT', surface

                denied = await call('journal void', {
                    'journal': doomed['id'], 'expected_version': doomed['version']}, rejected=True)
                assert denied['code'] == 'E_PERMISSION', surface

                # Nothing above wrote a byte.
                assert database(path) == before, surface
                assert await ledger() == BEFORE, surface

                deleted = await witnessed('journal delete', raw)
                assert deleted['status'] == 'deleted', surface
                assert deleted['version'] == doomed['version'] + 1, surface
                assert deleted['from_status'] == 'posted', surface
                assert deleted['number'] == doomed['number'], surface
                assert deleted['cancellation_batch_id'] is not None, surface
                after = database(path)
                assert after != before, surface
                assert await ledger() == AFTER, surface

                replay = await witnessed('journal delete', raw)
                assert replay['idempotent_replay'] and not replay['changed'], surface
                assert database(path) == after, surface

                gone = await call('journal show', {'journal': doomed['id']}, rejected=True)
                assert gone['code'] == 'E_RECORD_NOT_FOUND', surface
                shown = await call('journal show', {'journal': doomed['id'],
                                                    'include_deleted': True})
                assert shown['status'] == 'deleted', surface
                assert shown['number'] == doomed['number'], surface
                # Who deleted it, through which interface, and why -- retained on the record.
                assert shown['deletion']['from_status'] == 'posted', surface
                assert shown['deletion']['reason'] == 'Registry parity', surface
                assert shown['deletion']['created_via'] == surface, surface
                history = await call('journal history', {'journal': doomed['id'],
                                                         'include_deleted': True})
                assert history['items'][0]['id'] == doomed['revision']['id'], surface
                listed = {row['id'] for row in (await call('journal query', {'limit': 50}))['items']}
                assert doomed['id'] not in listed, surface

                with sqlite3.connect(path) as db:
                    assert db.execute(
                        "SELECT count(*) FROM posting_batches WHERE transaction_id=?"
                        " AND kind='reversal'", (doomed['id'],)).fetchone() == (1,), surface
                    assert db.execute(
                        'SELECT count(*) FROM journal_deletions').fetchone() == (1,), surface
                    assert db.execute('PRAGMA foreign_key_check').fetchall() == [], surface
                assert database(path) == after, surface
                driven[surface] = claimed
            # This file claims exactly these commands on these surfaces, and drove them there.
            assert set(driven) == set(SURFACES)
            assert all(names == COMMANDS for names in driven.values())
        finally:
            await matrix.close()

    asyncio.run(witness())
