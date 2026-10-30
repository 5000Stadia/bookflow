"""R108 increment 2, the review's findings (notes/reviews/r108-inc2-review.md): each reproduced probe as a test.

Kabe's rule: every import is reversible as one unit while nothing later depends on it; a refusal names exactly what
blocks it, and a half-finished state is always visible and finishable."""
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_bank_feeds import books, csv_file, ofx, run, table
from tests.test_bank_feed_reversal import DELETES, blocked, created_by, grant, imp, inbox, journal, reverse, state
from tests.test_row8_journal import database_path
from tests.test_service_sales_lifecycle import sale  # noqa: F401 - fixture


def _with_entries(client, n, account='Checking'):
    grant(client, *DELETES)
    done = imp(client, account, csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    made = [journal(client, '2026-10-01', f'{10 + k}.00', debit='Office Supplies', credit=account) for k in range(n)]
    for entry in made:
        created_by(client, done['batch_id'], entry)
    return done, made


def _version(client, transaction_id):
    return table(client, 'SELECT version FROM transactions WHERE id = ?', transaction_id)[0][0]


# ------------------------------------------------------------------ High 1

def test_move_dry_run_on_a_reversing_or_blocked_import_previews_the_rest_and_says_it_is_part_way(client):
    done, made = _with_entries(client, 3)
    assert reverse(client, done['import_id'], limit=1)['state'] == 'reversing'
    out = run(client, 'bank-feed move', dict(import_id=done['import_id'], to='Payment Example Bank'), dry_run=True)
    assert out['reversal']['state'] == 'reversing' and out['reversal']['entries'] == 2
    assert 'part way reversed' in out['reversal']['summary'] and '1 of 3' in out['reversal']['summary']
    assert out['target']['counts']['new'] == 1 and out['next'].startswith(f"bank-feed move {done['import_id']}")
    # Blocked between slices: the dry run refuses naming the blocker, and says the reversal is part way.
    run(client, 'journal update', dict(journal=made[0]['id'], expected_version=made[0]['version'], memo='edited'))
    blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert state(client, done['batch_id']) == 'blocked'
    with pytest.raises(BookflowError) as raised:
        run(client, 'bank-feed move', dict(import_id=done['import_id'], to='Payment Example Bank'), dry_run=True)
    assert raised.value.code == 'E_BATCH_BLOCKED' and 'entry_edited' in raised.value.message
    assert 'part way reversed (blocked)' in raised.value.message
    assert raised.value.details['part_way']['entries_left'] == 2


# ------------------------------------------------------------------ High 2

# What an import can create: increment 3 adds through `register post` (a journal entry), `check post` and `transfer
# post`, and on a card the card's own documents; increment 5 a deposit and a bill marked paid (a bill payment).
FAMILIES_AN_IMPORT_MAKES = {'journal_entry', 'check', 'card_charge', 'card_credit', 'transfer', 'deposit',
                            'bill_payment'}


def test_every_family_an_import_can_create_has_an_inverse_its_own_command_accepts():
    from bookflow.company.bank_reversal import INVERSE, inverse_payload
    from bookflow.core import registry
    from bookflow.core.dispatch import validate_input
    registry.load_all()
    assert FAMILIES_AN_IMPORT_MAKES <= set(INVERSE)
    batch, entry = '01J00000000000000000000001', '01J00000000000000000000002'
    for family, steps in INVERSE.items():
        for k, (command, selector) in enumerate(steps):
            payload = inverse_payload(command, selector, batch, entry, 3, k)
            validate_input(registry.get(command), payload)  # the real input model: a missing field fails here
            # A retry sends the same operation key: the same operation, never a second one.
            assert payload == inverse_payload(command, selector, batch, entry, 3, k)


def test_an_imported_deposit_is_deleted_by_its_own_command_with_a_key_stable_per_batch_and_entry(client, sale):
    from tests.test_payment_receipts import method
    grant(client, *DELETES, 'transaction.deposit.delete')
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    loose = run(client, 'payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='7.00',
                                                payment_method=method(client), operation_key='r108-fix-loose'))
    run(client, 'deposit post', dict(operation_key='r108-fix-deposit', document=dict(
        mode='inline', deposit_to='Checking', date='2026-06-03', additional=[], sources=[dict(
            source_type='payment', source=loose['id'],
            expected_version=run(client, 'payment show', dict(payment=loose['id']))['version'])])))
    deposit = table(client, "SELECT id FROM transactions WHERE type = 'deposit' ORDER BY rowid DESC LIMIT 1")[0][0]
    created_by(client, done['batch_id'], dict(id=deposit, version=_version(client, deposit)))
    plan = reverse(client, done['import_id'], dry_run=True)
    assert plan['blockers'] == [] and plan['steps'][0]['steps'] == ['deposit delete']
    assert reverse(client, done['import_id'], fingerprint=plan['fingerprint'])['state'] == 'reversed'
    assert table(client, 'SELECT operation_key FROM deposit_deletions WHERE transaction_id = ?', deposit) == [
        (f"bank-reverse:{done['batch_id']}:{deposit}:0",)]


def test_an_imported_bill_payment_is_unapplied_then_voided_and_the_bill_is_open_again(client):
    grant(client, *DELETES)
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    vendor = run(client, 'vendor create', dict(name='R108 fix vendor'))['id']
    bill = run(client, 'bill post', dict(vendor=vendor, date='2026-06-01',
                                         expenses=[dict(account='Office Supplies', amount='40.00')]))
    method = run(client, 'payment-method query', dict(limit=50))['items'][0]['id']
    payment = run(client, 'bill pay', dict(date='2026-06-05', bills=[dict(bill=bill['id'])],
                                           funding_account='Checking', method=method))['payments'][0]
    assert run(client, 'bill show', dict(bill=bill['id']))['settlement_current']['status'] == 'paid'
    created_by(client, done['batch_id'], dict(id=payment['id'], version=_version(client, payment['id'])))
    plan = reverse(client, done['import_id'], dry_run=True)
    assert plan['blockers'] == [] and plan['steps'][0]['steps'] == ['bill payment unapply', 'bill payment void']
    assert reverse(client, done['import_id'], fingerprint=plan['fingerprint'])['state'] == 'reversed'
    assert run(client, 'bill payment show', dict(payment=payment['id']))['status'] == 'voided'
    assert run(client, 'bill show', dict(bill=bill['id']))['settlement_current']['status'] != 'paid'


# ------------------------------------------------------------------ Medium 3

def test_an_entry_ticked_on_an_open_reconciliation_draft_blocks_naming_the_draft(client):
    from tests.test_reconcile_import import opening
    grant(client, *DELETES)
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    ticked = journal(client, '2026-06-04', '33.00')
    created_by(client, done['batch_id'], dict(ticked, version=_version(client, ticked['id'])))
    opening(client)
    draft = run(client, 'reconcile start', dict(operation_key=new_id(), account='Checking', statement_date='2026-06-30',
                                                ending_balance='33.00'))['draft']
    rows = [x for x in run(client, 'reconcile candidates', dict(draft=draft['id'], limit=200))['items']
            if ticked['id'] in str(x['movement'])]
    marked = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
        entries=[dict(movement=x['movement'], group_fingerprint=x['group_fingerprint'], action='mark') for x in rows]))
    before = (inbox(client), books(client))
    details = blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    [found] = details['blockers']
    assert found['code'] == 'entry_ticked' and found['record_id'] == ticked['id'] and found['draft'] == draft['id']
    assert draft['id'] in found['message'] and 'unmark' in found['clears'] and 'reconcile discard' in found['clears']
    assert (inbox(client), books(client)) == before and state(client, done['batch_id']) == 'live'
    run(client, 'reconcile discard', dict(operation_key=new_id(), draft=draft['id'],
                                          expected_version=marked['draft']['version']))
    assert reverse(client, done['import_id'])['state'] == 'reversed'


# ------------------------------------------------------------------ Medium 4

def test_a_move_that_stops_after_a_slice_keeps_its_flags_shows_its_state_and_says_it_is_part_way(client):
    grant(client, *DELETES)
    imp(client, 'Payment Example Bank', ofx([('2026-09-01', '-1.00', 'A1', 'X')]))
    src = imp(client, 'Checking', ofx([('2026-10-01', '-2.00', 'C1', 'Y')], acct='000099990001'))
    made = [journal(client, '2026-10-01', f'{n}.00', debit='Office Supplies', credit='Checking') for n in (11, 12)]
    for entry in made:
        created_by(client, src['batch_id'], entry)
    first = run(client, 'bank-feed move', dict(import_id=src['import_id'], to='Payment Example Bank',
                                               confirm_account=True, limit=1))
    assert first['reversal']['state'] == 'reversing' and first['target'] is None
    shown = run(client, 'bank-feed show', dict(import_id=src['import_id']))['part_way']
    assert (shown['state'], shown['entries_done'], shown['entries_left'], shown['lines']) == ('reversing', 1, 1, 1)
    assert 'Payment Example Bank' in shown['move_to_name'] and shown['next'] == first['next']
    assert 'part way moved to' in shown['summary']
    # An error while the move is part way says so and how to finish it.
    run(client, 'journal update', dict(journal=made[0]['id'], expected_version=made[0]['version'], memo='edited'))
    with pytest.raises(BookflowError) as raised:
        run(client, 'bank-feed move', dict(import_id=src['import_id'], to='Payment Example Bank'))
    assert raised.value.code == 'E_BATCH_BLOCKED' and 'part way moved to' in raised.value.message
    assert raised.value.details['part_way']['next'] == first['next'] and first['next'] in raised.value.message
    run(client, 'journal delete', dict(journal=made[0]['id'], expected_version=_version(client, made[0]['id'])))
    # `next` as given, without the flags, finishes the move with the ones it began with.
    out = run(client, 'bank-feed move', dict(import_id=src['import_id'], to='Payment Example Bank'))
    assert out['reversal']['state'] == 'reversed' and out['target']['import_id']
    assert run(client, 'bank-feed show', dict(import_id=src['import_id']))['part_way'] is None


# ------------------------------------------------------------------ Medium 5

def test_with_a_dry_runs_fingerprint_the_first_call_probes_only_its_slice(client, monkeypatch):
    from bookflow.company import batches
    done, _ = _with_entries(client, 5)
    plan = reverse(client, done['import_id'], dry_run=True)
    probes, real = [], batches.run_nested

    def counting(s, ctx, name, payload, **kw):
        if kw.get('dry_run'):
            probes.append(name)
        return real(s, ctx, name, payload, **kw)
    monkeypatch.setattr(batches, 'run_nested', counting)
    out = reverse(client, done['import_id'], fingerprint=plan['fingerprint'], limit=2)
    assert (out['state'], out['reversed_now'], len(probes)) == ('reversing', 2, 2)


# ------------------------------------------------------------------ Medium 6

def test_an_entry_an_older_imports_line_matches_says_to_unmatch_never_to_reverse_the_older_one_first(client):
    grant(client, *DELETES)
    older = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    newer = imp(client, 'Checking', csv_file([('2026-10-05', 'LUNCH', '-9.00')]))
    entry = journal(client, '2026-10-01', '4.50', debit='Office Supplies', credit='Checking')
    created_by(client, newer['batch_id'], entry)
    line = table(client, 'SELECT id FROM bank_lines WHERE import_id = ?', older['import_id'])[0][0]
    with sqlite3.connect(database_path(client)) as conn:  # increment 3's manual match, made directly
        conn.execute("UPDATE bank_lines SET entry_id = ?, state = 'matched' WHERE id = ?", (entry['id'], line))
    details = blocked(client, 'bank-feed reverse', dict(import_id=newer['import_id']))
    [found] = details['blockers']
    assert found['code'] == 'entry_linked' and 'older import' in found['message']
    assert f"bank-feed reverse {older['import_id']}" not in found['clears']
    assert f'bank-line unmatch {line}' in found['clears']


# ------------------------------------------------------------------ Lows

def test_a_second_finish_is_refused_as_already_reversed(client, monkeypatch):
    from bookflow.company import batches
    done, _ = _with_entries(client, 1)
    real, seen = batches._finish, []

    def twice(s, ctx, batch_id, reversal_id, batch, then=None, state='reversed'):
        out = real(s, ctx, batch_id, reversal_id, batch, then, state=state)
        with pytest.raises(BookflowError) as again:
            real(s, ctx, batch_id, reversal_id, batch, None, state=state)
        seen.append(again.value)
        return out
    monkeypatch.setattr(batches, '_finish', twice)
    before = table(client, 'SELECT * FROM bank_feed_accounts ORDER BY account_id')
    assert reverse(client, done['import_id'])['state'] == 'reversed'
    assert seen[0].code == 'E_VALIDATION' and 'already reversed' in seen[0].message
    assert table(client, "SELECT count(*) FROM batch_effects WHERE record_type = 'bank_feed_import' AND action = "
                         "'reversed'") == [(1,)]
    assert len(table(client, 'SELECT * FROM bank_feed_accounts')) == len(before)


def test_a_real_journal_delete_prepares_once(client, monkeypatch):
    from bookflow.company import journal_deletions
    grant(client, *DELETES)
    entry = journal(client, '2026-10-01', '5.00')
    real, calls = journal_deletions.prepare, []
    monkeypatch.setattr(journal_deletions, 'prepare', lambda s, ctx, inp: calls.append(1) or real(s, ctx, inp))
    run(client, 'journal delete', dict(journal=entry['id'], expected_version=entry['version']))
    assert len(calls) == 1 and table(client, 'SELECT count(*) FROM journal_deletions WHERE transaction_id = ?',
                                     entry['id']) == [(1,)]
