"""R108 increment 2: an import reversed as one unit, or moved to the right account in one step (design/bank-feeds.md §6,
criteria 2 and 3).

Against the demo's Checking, Payment Example Bank and Business Credit Card. A reversal hides the import's lines and
releases their keys, undoes its matches, returns the account's record to what it was, and deletes what it created; a
refusal names every blocker and changes nothing. Entries an import creates arrive with increment 3 (`bank-line add`):
here they are recorded as the import's `created` effects directly, exactly as that act will record them, so each
blocker the family's own `delete` raises is reproduced now.
"""
import json
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.core.ids import new_id
from tests.test_bank_feeds import books, csv_file, ofx, run, sample, table
from tests.test_row8_journal import database_path
from tests.test_service_sales_lifecycle import sale  # noqa: F401 - fixture

DELETES = ['transaction.journal_entry.delete', 'transaction.payment.delete', 'transaction.invoice.delete']


def imp(client, account, content, **kw):
    return run(client, 'bank-feed import', dict(account=account, content=content, **kw))


def reverse(client, import_id, dry_run=False, **kw):
    return run(client, 'bank-feed reverse', dict(import_id=import_id, **kw), dry_run=dry_run)


def blocked(client, name, data):
    with pytest.raises(BookflowError) as raised:
        run(client, name, data)
    assert raised.value.code == 'E_BATCH_BLOCKED', raised.value
    return raised.value.details


def inbox(client):
    """Everything the inbox holds that a reversal must restore: live lines, their links, and account records."""
    return (table(client, 'SELECT account_id, line_key, date, amount, state, entry_id FROM bank_lines WHERE live = 1 '
                          'ORDER BY account_id, line_key'),
            table(client, 'SELECT account_id, bank_number, account_last4, kind, last_import_id FROM bank_feed_accounts '
                          'ORDER BY account_id'))


def journal(client, day, amount, debit='Checking', credit='Service Income'):
    return run(client, 'journal post', dict(date=day, memo='entry', lines=[
        dict(account=debit, side='debit', amount=amount), dict(account=credit, side='credit', amount=amount)]))


def created_by(client, batch_id, entry):
    """Record that the import created ``entry``, as increment 3's `bank-line add` will (one `created` effect)."""
    with sqlite3.connect(database_path(client)) as conn:
        seq = conn.execute('SELECT max(seq) FROM batch_effects WHERE batch_id = ?', (batch_id,)).fetchone()[0] + 1
        conn.execute("INSERT INTO batch_effects (batch_id, seq, record_type, record_id, action, version_after, "
                     "created_at) VALUES (?, ?, 'transaction', ?, 'created', ?, '2026-10-28T00:00:00.000Z')",
                     (batch_id, seq, entry['id'], entry['version']))


def grant(client, *grants):
    member = next(m for m in client.membership.list(company='Demo Plumbing Co')['items'] if m['username'] == 'k')
    client.membership.grant(user=member['user_id'], company='Demo Plumbing Co', role='owner',
                            expected_version=member['version'], grants=list(grants))


def state(client, batch_id):
    return table(client, 'SELECT state FROM batches WHERE id = ?', batch_id)[0][0]


def test_a_reversal_leaves_books_inbox_and_account_record_as_before_and_releases_the_keys(client):
    before_books, before_inbox = books(client), inbox(client)
    content = sample('checking-2026-09.ofx')
    done = imp(client, 'Checking', content)
    assert done['counts']['matched'] == 8 and inbox(client) != before_inbox
    plan = reverse(client, done['import_id'], dry_run=True)
    assert (plan['state'], plan['lines'], plan['matches'], plan['entries'], plan['blockers']) == ('live', 10, 8, 0, [])
    assert plan['steps'][-1]['steps'][:3] == ['hide 10 line(s) and release their keys', 'undo 8 match(es)',
                                             'restore the account record']
    assert state(client, done['batch_id']) == 'live', 'a dry run changes nothing'
    out = reverse(client, done['import_id'], fingerprint=plan['fingerprint'])
    assert out['state'] == 'reversed' and out['imports'] == [done['import_id']] and len(out['reversal_batches']) == 1
    assert books(client) == before_books and inbox(client) == (before_inbox[0], [(
        table(client, "SELECT id FROM accounts WHERE name = 'Checking'")[0][0], None, None, None, None)])
    # The import, its lines and events stay, marked: nothing is erased.
    assert state(client, done['batch_id']) == 'reversed'
    assert table(client, 'SELECT count(*), sum(live) FROM bank_lines') == [(10, 0)]
    assert table(client, "SELECT count(*) FROM bank_line_events WHERE kind = 'reversed'") == [(10,)]
    effects = dict(table(client, 'SELECT record_type, count(*) FROM batch_effects WHERE batch_id = ? GROUP BY 1',
                         out['reversal_batches'][0]))
    assert effects == {'bank_feed_import': 1, 'bank_feed_account': 1, 'transaction': 8}
    assert run(client, 'bank-feed query', dict(account='Checking'))['imports'] == []
    assert run(client, 'bank-feed query', dict(account='Checking', include_reversed=True))['imports'][0][
        'batch_state'] == 'reversed'
    shown = run(client, 'batch show', dict(batch=done['batch_id']))['batch']
    assert shown['state'] == 'reversed' and shown['reversible'] is False
    # The keys and the entries are free: the same file comes in again, matched the same, and teaches the number again.
    again = imp(client, 'Checking', content)
    assert (again['counts']['new'], again['counts']['matched']) == (10, 8)
    assert again['account_check']['status'] == 'first'
    with pytest.raises(BookflowError) as twice:
        reverse(client, done['import_id'])
    assert twice.value.code == 'E_VALIDATION'


def test_the_account_record_returns_to_what_it_was_before_the_import(client):
    first = imp(client, 'Payment Example Bank', ofx([('2026-09-01', '-1.00', 'A1', 'X')]))
    record = inbox(client)[1]
    confirmed = imp(client, 'Payment Example Bank', ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001'),
                    confirm_account=True)
    assert inbox(client)[1] != record
    reverse(client, confirmed['import_id'])
    assert inbox(client)[1] == record and record[0][2] == '5678' and record[0][4] == first['import_id']
    # The number it had taught is no longer the account's: that file refuses again without confirm_account.
    with pytest.raises(BookflowError) as mismatch:
        imp(client, 'Payment Example Bank', ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001'))
    assert mismatch.value.code == 'E_ACCOUNT_MISMATCH'


def test_newest_first_per_account_and_through_reverses_the_newer_ones_in_one_plan(client):
    before = inbox(client)
    older = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    newer = imp(client, 'Checking', csv_file([('2026-10-03', 'LUNCH', '-12.00')]))
    elsewhere = imp(client, 'Payment Example Bank', csv_file([('2026-10-04', 'FEE', '-3.00')]))
    details = blocked(client, 'bank-feed reverse', dict(import_id=older['import_id']))
    blocker, = details['blockers']
    assert (blocker['code'], blocker['record_id'], blocker['lines']) == ('later_import', newer['import_id'], 1)
    assert '--through' in blocker['clears'] and state(client, older['batch_id']) == 'live'
    plan = reverse(client, older['import_id'], dry_run=True, through=True)
    assert plan['imports'] == [newer['import_id'], older['import_id']] and plan['lines'] == 2 and not plan['blockers']
    out = reverse(client, older['import_id'], through=True, fingerprint=plan['fingerprint'])
    assert out['state'] == 'reversed' and len(out['reversal_batches']) == 2
    assert {state(client, x['batch_id']) for x in (older, newer)} == {'reversed'}
    assert state(client, elsewhere['batch_id']) == 'live', 'another account is another scope'
    reverse(client, elsewhere['import_id'])
    assert inbox(client)[0] == before[0]


def test_a_dry_runs_fingerprint_refuses_once_a_later_import_landed(client):
    first = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    plan = reverse(client, first['import_id'], dry_run=True)
    imp(client, 'Checking', csv_file([('2026-10-05', 'BOOKS', '-20.00')]))
    details = blocked(client, 'bank-feed reverse', dict(import_id=first['import_id'], fingerprint=plan['fingerprint']))
    assert details['blockers'][0]['code'] == 'later_import' and state(client, first['batch_id']) == 'live'


def test_move_to_the_right_account_is_one_step_and_sorts_the_lines_there(client):
    """Criterion 2's move: a CSV of Checking's deposits sent to Payment Example Bank, moved in one step."""
    entries = [journal(client, f'2026-10-{d:02d}', f'{100 + d}.00') for d in range(1, 7)]
    content = csv_file([(f'2026-10-{d:02d}', f'CUSTOMER {d}', f'{100 + d}.00') for d in range(1, 7)]
                       + [('2026-10-09', 'BANK FEE', '-5.00')])
    with pytest.raises(BookflowError) as looks:
        imp(client, 'Payment Example Bank', content)
    assert looks.value.code == 'E_ACCOUNT_MISMATCH' and looks.value.details['looks_like_name'] == 'Checking'
    before_books = books(client)
    wrong = imp(client, 'Payment Example Bank', content, account_confirmed=True)
    assert wrong['counts']['new'] == 7 and wrong['counts']['matched'] == 0
    preview = run(client, 'bank-feed move', dict(import_id=wrong['import_id'], to='Checking'), dry_run=True)
    assert preview['reversal']['lines'] == 7 and preview['target']['counts']['matched'] == 6
    assert preview['target']['import_id'] is None and state(client, wrong['batch_id']) == 'live'
    moved = run(client, 'bank-feed move', dict(import_id=wrong['import_id'], to='Checking',
                                              fingerprint=preview['reversal']['fingerprint']))
    new = moved['target']
    assert moved['reversal']['state'] == 'reversed' and new['import_id'] and new['counts']['matched'] == 6
    assert sorted(v['entry_id'] for v in new['lines'] if v['entry_id']) == sorted(e['id'] for e in entries)
    assert table(client, 'SELECT moved_from FROM bank_feed_imports WHERE id = ?', new['import_id']) == [
        (wrong['import_id'],)]
    assert table(client, 'SELECT count(*) FROM bank_lines WHERE import_id = ? AND live = 1', wrong['import_id']) == [
        (0,)]
    assert state(client, wrong['batch_id']) == 'reversed' and books(client) == before_books
    # The same lines, keyed the same, now in Checking.
    assert sorted(table(client, 'SELECT line_key, amount FROM bank_lines WHERE import_id = ?', wrong['import_id'])) \
        == sorted(table(client, 'SELECT line_key, amount FROM bank_lines WHERE import_id = ?', new['import_id']))


def test_moving_to_a_card_works_and_another_kind_or_currency_is_refused(client):
    csv_import = imp(client, 'Checking', csv_file([('2026-10-01', 'AIRLINE', '-310.00')]))
    moved = run(client, 'bank-feed move', dict(import_id=csv_import['import_id'], to='Business Credit Card'))
    assert moved['target']['lines'][0]['amount'] == -31000 and moved['target']['account_id'] == table(
        client, "SELECT id FROM accounts WHERE name = 'Business Credit Card'")[0][0]
    # An OFX bank statement carries its kind and number: into a card it is refused, and nothing changes.
    bank_file = imp(client, 'Payment Example Bank', ofx([('2026-10-02', '-9.00', 'Z1', 'X')]))
    before = inbox(client)
    with pytest.raises(BookflowError) as kind:
        run(client, 'bank-feed move', dict(import_id=bank_file['import_id'], to='Business Credit Card'))
    assert kind.value.code == 'E_ACCOUNT_MISMATCH' and 'card' in kind.value.message
    assert inbox(client) == before and state(client, bank_file['batch_id']) == 'live'
    run(client, 'account create', dict(name='Euro Bank', type='bank', currency='EUR'))
    with pytest.raises(BookflowError) as currency:
        run(client, 'bank-feed move', dict(import_id=bank_file['import_id'], to='Euro Bank'))
    assert currency.value.code == 'E_VALIDATION' and 'EUR' in currency.value.message
    assert inbox(client) == before and state(client, bank_file['batch_id']) == 'live'
    with pytest.raises(BookflowError) as same:
        run(client, 'bank-feed move', dict(import_id=bank_file['import_id'], to='Payment Example Bank'))
    assert same.value.code == 'E_VALIDATION'


def test_entries_it_created_are_deleted_with_history_and_each_blocker_is_named(client):
    """Criterion 3: no authority, edited, linked, reconciled, closed period; each refusal changes nothing."""
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    plain = journal(client, '2026-10-01', '4.50', debit='Office Supplies', credit='Checking')
    created_by(client, done['batch_id'], plain)
    before = (inbox(client), books(client))
    # No authority: the owner holds no family Delete grant until given one.
    details = blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert [b['code'] for b in details['blockers']] == ['no_authority']
    assert details['blockers'][0]['record_id'] == plain['id'] and details['blockers'][0]['clears']
    assert (inbox(client), books(client)) == before and state(client, done['batch_id']) == 'live'
    grant(client, *DELETES)
    # Edited since the import made it.
    edited = run(client, 'journal update', dict(journal=plain['id'], expected_version=plain['version'], memo='mine'))
    details = blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert [b['code'] for b in details['blockers']] == ['entry_edited']
    assert 'delete it yourself' in details['blockers'][0]['clears']
    # Linked: a created transfer-like entry another account's live import matched.
    later = imp(client, 'Payment Example Bank', csv_file([('2026-10-02', 'NOTHING', '-1.00')]))
    reverse(client, later['import_id'])
    move = journal(client, '2026-10-02', '250.00', debit='Payment Example Bank', credit='Checking')
    second = imp(client, 'Checking', csv_file([('2026-10-02', 'TRANSFER OUT', '-1.25')]))
    created_by(client, second['batch_id'], move)
    linked = imp(client, 'Payment Example Bank', csv_file([('2026-10-02', 'TRANSFER IN', '250.00')]))
    assert linked['lines'][0]['entry_id'] == move['id']
    codes = {b['code']: b for b in blocked(client, 'bank-feed reverse', dict(import_id=second['import_id']))['blockers']}
    assert codes['entry_linked']['import_id'] == linked['import_id']
    assert linked['import_id'] in codes['entry_linked']['clears']
    reverse(client, linked['import_id'])
    assert reverse(client, second['import_id'], dry_run=True)['blockers'] == []
    # A closed period refuses with the family delete's own code, by name.
    run(client, 'company update', dict(closing_date='2026-10-05'))
    codes = [b['code'] for b in blocked(client, 'bank-feed reverse', dict(import_id=second['import_id']))['blockers']]
    assert codes == ['period_closed']
    run(client, 'company update', dict(closing_date=None))
    # Cleared: the created entry is deleted with preserved history, the edited one blocks until its own delete.
    out = reverse(client, second['import_id'])
    assert out['state'] == 'reversed' and out['entries'] == 1
    assert table(client, 'SELECT count(*) FROM journal_deletions WHERE transaction_id = ?', move['id']) == [(1,)]
    run(client, 'journal delete', dict(journal=plain['id'], expected_version=edited['version']))
    out = reverse(client, done['import_id'])
    assert out['state'] == 'reversed' and out['entries'] == 0, 'an entry already deleted is nothing to take back'


def test_reconciled_deposited_and_applied_entries_block_by_the_family_deletes_own_refusal(client, sale):
    from tests.test_payment_receipts import method, posted
    from tests.test_reconcile_import import opening
    grant(client, *DELETES)
    cash = method(client)
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'R108-INC2')
    paid = run(client, 'payment receive', dict(
        customer=sale['customer'], date='2026-06-02', amount='60.00', payment_method=cash,
        operation_key='r108-applied', applications=dict(mode='inline', items=[
            dict(invoice=invoice['id'], expected_version=1, amount='60.00')])))
    loose = run(client, 'payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='7.00',
                                                payment_method=cash, operation_key='r108-deposited'))
    run(client, 'deposit post', dict(operation_key='r108-deposit', document=dict(
        mode='inline', deposit_to='Checking', date='2026-06-03', additional=[], sources=[dict(
            source_type='payment', source=loose['id'],
            expected_version=run(client, 'payment show', dict(payment=loose['id']))['version'])])))
    ticked = journal(client, '2026-06-04', '33.00')
    for entry in (invoice, loose, ticked):
        created_by(client, done['batch_id'], dict(entry, version=table(
            client, 'SELECT version FROM transactions WHERE id = ?', entry['id'])[0][0]))
    opening(client)
    draft = run(client, 'reconcile start', dict(operation_key=new_id(), account='Checking', statement_date='2026-06-30',
                                                ending_balance='33.00'))['draft']
    rows = [x for x in run(client, 'reconcile candidates', dict(draft=draft['id'], limit=200))['items']
            if ticked['id'] in json.dumps(x['movement'])]
    assert len(rows) == 1
    marked = run(client, 'reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=draft['version'],
        entries=[dict(movement=x['movement'], group_fingerprint=x['group_fingerprint'], action='mark') for x in rows]))
    marked = marked['draft']
    preview = run(client, 'reconcile preview', dict(draft=draft['id'], expected_version=marked['version']))
    run(client, 'reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                         expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                         dependency_guard=preview['dependency_guard']))
    before = (inbox(client), books(client))
    details = blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    codes = {b['record_id']: b['code'] for b in details['blockers']}
    assert codes == {invoice['id']: 'entry_applied', loose['id']: 'entry_deposited', ticked['id']: 'entry_reconciled'}
    assert details['total'] == 3 and all(b['clears'] for b in details['blockers'])
    assert (inbox(client), books(client)) == before and state(client, done['batch_id']) == 'live'
    assert paid['id']


def test_slices_freeze_the_account_stop_blocked_and_resume_after_a_kill(client, monkeypatch):
    from dataclasses import replace
    from bookflow.company import batches
    grant(client, *DELETES)
    done = imp(client, 'Checking', csv_file([('2026-10-01', 'COFFEE', '-4.50')]))
    made = [journal(client, '2026-10-01', f'{n}.00', debit='Office Supplies', credit='Checking') for n in (11, 12, 13, 14)]
    for entry in made:
        created_by(client, done['batch_id'], entry)
    first = reverse(client, done['import_id'], limit=1)
    assert (first['state'], first['reversed_now'], first['remaining']) == ('reversing', 1, 4)
    assert first['next'] == f"bank-feed reverse {done['import_id']}" and state(client, done['batch_id']) == 'reversing'
    # The account takes no new import while the reversal is under way; another account does.
    with pytest.raises(BookflowError) as frozen:
        imp(client, 'Checking', csv_file([('2026-10-02', 'LUNCH', '-9.00')]))
    assert frozen.value.code == 'E_VALIDATION' and done['import_id'] in frozen.value.message
    imp(client, 'Payment Example Bank', csv_file([('2026-10-02', 'TOLL', '-2.00')]))
    # A blocker appearing between slices stops it `blocked`, naming it; the lines stay in the inbox.
    newest = made[0]  # entries are taken back newest first, so the oldest is still there
    edited = run(client, 'journal update', dict(journal=newest['id'], expected_version=newest['version'], memo='x'))
    details = blocked(client, 'bank-feed reverse', dict(import_id=done['import_id']))
    assert details['blockers'][0]['code'] == 'entry_edited' and details['blockers'][0]['record_id'] == newest['id']
    assert state(client, done['batch_id']) == 'blocked'
    assert table(client, 'SELECT sum(live) FROM bank_lines WHERE import_id = ?', done['import_id']) == [(1,)]
    with pytest.raises(BookflowError):
        imp(client, 'Checking', csv_file([('2026-10-02', 'LUNCH', '-9.00')]))
    run(client, 'journal delete', dict(journal=newest['id'], expected_version=edited['version']))
    # Killed part way through a slice: what ran stays done, recorded; the same command goes on and repeats nothing.
    adapter = batches.ADAPTERS['bank-import']
    calls = []

    def dies(s, ctx, batch, reversal_id, item):
        if calls:
            raise KeyboardInterrupt('killed')
        calls.append(item['record_id'])
        adapter.runner(s, ctx, batch, reversal_id, item)
    monkeypatch.setitem(batches.ADAPTERS, 'bank-import', replace(adapter, runner=dies))
    with pytest.raises(KeyboardInterrupt):
        reverse(client, done['import_id'])
    monkeypatch.setitem(batches.ADAPTERS, 'bank-import', adapter)
    assert state(client, done['batch_id']) == 'reversing'
    out = reverse(client, done['import_id'])
    assert out['state'] == 'reversed' and out['reversed_now'] == 1
    assert table(client, 'SELECT count(*), count(DISTINCT transaction_id) FROM journal_deletions WHERE transaction_id '
                         'IN (?, ?, ?, ?)', *(e['id'] for e in made)) == [(4, 4)]
    assert table(client, 'SELECT sum(live) FROM bank_lines WHERE import_id = ?', done['import_id']) == [(0,)]
    imp(client, 'Checking', csv_file([('2026-10-02', 'LUNCH', '-9.00')]))
