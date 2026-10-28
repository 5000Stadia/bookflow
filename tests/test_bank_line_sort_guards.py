"""R108 increment 3, review fixes: an undo never links a line back blindly, a part-way undo freezes its lines and always
finishes, `entry_gone` shows a line whose entry is no longer what it was, a one-sided transfer is completed late rather
than posted twice, stacked undos unwind newest first, retries are idempotent, and refusals point at what clears them
(design/bank-feeds.md §2, §4, As built: increment 3). Each test reproduces a review probe.
"""
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_bank_feeds import csv_file, run, table
from tests.test_bank_feed_reversal import DELETES, blocked, grant, state
from tests.test_bank_line_sort import imp, inbox_line, line_of, refused
from tests.test_row8_journal import database_path


def jr(client, day, amount, account='Checking'):
    return run(client, 'journal post', dict(date=day, memo='entry', lines=[
        dict(account=account, side='debit', amount=amount), dict(account='Service Income', side='credit',
                                                                 amount=amount)]))


def rejr(client, entry, day, amount):
    version = table(client, 'SELECT version FROM transactions WHERE id = ?', entry)[0][0]
    return run(client, 'journal update', dict(journal=entry, expected_version=version, date=day, memo='edited', lines=[
        dict(account='Checking', side='debit', amount=amount), dict(account='Service Income', side='credit',
                                                                    amount=amount)]))


def standing(client):
    """The books' standing entries and their postings (a deleted entry keeps its rows, voided, with its history)."""
    return table(client, 'SELECT p.* FROM posting_lines p JOIN transactions t ON t.id = p.transaction_id '
                         'WHERE t.voided_at IS NULL ORDER BY p.rowid')


def links(client):
    return table(client, 'SELECT id, state, entry_id, version FROM bank_lines WHERE live = 1 ORDER BY id')


def undo_blocked(client, batch_id):
    found = blocked(client, 'bank-line undo', dict(batch=batch_id))
    return {b['code']: b for b in found['blockers']}


def query(client, **kw):
    return run(client, 'bank-line query', dict(account='Checking', limit=1000, **kw))['lines']


def test_an_undo_never_links_a_line_back_to_an_entry_held_gone_or_changed(client):
    grant(client, 'transaction.journal_entry.delete')
    # Held: E matched by L1 at import; unmatch --lines [L1]; L2 matched to E; the undo would double E. Refused, naming
    # L2 and the unmatch that clears it; nothing changes; once cleared, the undo links L1 back.
    e1 = jr(client, '2026-10-05', '20.00')
    d1 = imp(client, 'Checking', csv_file([('2026-10-05', 'D1', '20.00')]))
    d2 = imp(client, 'Checking', csv_file([('2026-10-07', 'D1 AGAIN', '20.00')]))
    l1, l2 = d1['lines'][0], d2['lines'][0]
    assert (l1['state'], l2['state']) == ('matched', 'waiting')
    u1 = run(client, 'bank-line unmatch', dict(lines=[l1['id']]))
    run(client, 'bank-line match', dict(line=l2['id'], entry=e1['id']))
    before = links(client)
    found = undo_blocked(client, u1['batch_id'])
    assert set(found) == {'entry_matched'} and found['entry_matched']['matched_line'] == l2['id']
    assert f"bank-line unmatch --line {l2['id']}" in found['entry_matched']['clears']
    assert links(client) == before and state(client, u1['batch_id']) == 'live'
    run(client, 'bank-line unmatch', dict(line=l2['id']))
    run(client, 'bank-line undo', dict(batch=u1['batch_id']))
    assert table(client, 'SELECT state, entry_id FROM bank_lines WHERE id = ?', l1['id']) == [('matched', e1['id'])]

    # Gone: the entry was voided since the unmatch batch; nothing can link the line back.
    e2 = jr(client, '2026-10-12', '31.00')
    l3 = imp(client, 'Checking', csv_file([('2026-10-12', 'D2', '31.00')]))['lines'][0]
    u2 = run(client, 'bank-line unmatch', dict(lines=[l3['id']]))
    run(client, 'journal void', dict(journal=e2['id'], expected_version=e2['version']))
    before = links(client)
    found = undo_blocked(client, u2['batch_id'])
    assert set(found) == {'entry_gone'} and 'voided' in found['entry_gone']['message']
    assert links(client) == before and state(client, u2['batch_id']) == 'live'

    # Changed in amount: refused until the entry is as it was, then the undo links back.
    e3 = jr(client, '2026-10-14', '42.00')
    l4 = imp(client, 'Checking', csv_file([('2026-10-14', 'D3', '42.00')]))['lines'][0]
    u3 = run(client, 'bank-line unmatch', dict(lines=[l4['id']]))
    rejr(client, e3['id'], '2026-10-14', '45.00')
    found = undo_blocked(client, u3['batch_id'])
    assert set(found) == {'entry_gone'} and '45.00' in found['entry_gone']['message']
    rejr(client, e3['id'], '2026-10-14', '42.00')
    run(client, 'bank-line undo', dict(batch=u3['batch_id']))
    assert table(client, 'SELECT state, entry_id FROM bank_lines WHERE id = ?', l4['id']) == [('matched', e3['id'])]

    # A re-match batch (matched to E4, then --lines to E5) would revert to E4, deleted since: refused.
    l5 = imp(client, 'Checking', csv_file([('2026-10-20', 'D4', '50.00')]))['lines'][0]
    e4, e5 = jr(client, '2026-10-20', '50.00'), jr(client, '2026-10-22', '50.00')
    run(client, 'bank-line match', dict(line=l5['id'], entry=e4['id']))
    r = run(client, 'bank-line match', dict(lines=[dict(line=l5['id'], entry=e5['id'])]))
    run(client, 'journal delete', dict(journal=e4['id'], expected_version=e4['version']))
    before = links(client)
    assert set(undo_blocked(client, r['batch_id'])) == {'entry_gone'} and links(client) == before


def test_a_part_way_undo_freezes_its_lines_and_always_finishes(client):
    grant(client, *DELETES, 'transaction.check.delete')
    d = imp(client, 'Checking', csv_file([(f'2026-10-{1 + n % 28:02d}', f'SHOP {n}', f'-{n + 1}.00')
                                          for n in range(101)]))
    before = standing(client)
    payee = dict(payee='Central Supply', account='Vehicle Expense')
    every = query(client)
    assert len(every) == 101
    added = run(client, 'bank-line add', dict(lines=[dict(line=v['id']) for v in every], **payee))
    batch = added['batch_id']
    first = run(client, 'bank-line undo', dict(batch=batch))
    assert first['state'] == 'reversing' and first['reversed_now'] == 100 and first['next'] == f'bank-line undo {batch}'
    # Its lines' entries are gone, the lines still added: they show entry_gone, and are frozen until it ends.
    gone = query(client, entry_gone=True)
    assert len(gone) == 100 and all(v['state'] == 'added' and 'deleted' in v['entry_gone'] for v in gone)
    line = gone[0]
    for name, data in (('bank-line unmatch', dict(line=line['id'])),
                       ('bank-line ignore', dict(line=line['id'], ignore_reason='other')),
                       ('bank-line add', dict(line=line['id'], **payee)),
                       ('bank-line transfer', dict(line=line['id'], to='Payment Example Bank'))):
        err = refused(client, name, data)
        assert 'undo in progress' in err.message and f'bank-line undo {batch}' in err.message, (name, err.message)
    assert {b['code'] for b in blocked(client, 'bank-feed reverse', dict(import_id=d['import_id']))['blockers']} \
        == {'sort_under_way'}
    # A path that slipped past the freeze put a line back to waiting: the next slice takes it as already back.
    with sqlite3.connect(database_path(client)) as conn:
        conn.execute("UPDATE bank_lines SET state = 'waiting', entry_id = NULL, created = 0, link_how = NULL, "
                     'match_reason = NULL, payee_display = NULL, entry_date = NULL, version = version + 1 '
                     'WHERE id = ?', (line['id'],))
    done = run(client, 'bank-line undo', dict(batch=batch))
    assert done['state'] == 'reversed' and state(client, batch) == 'reversed'
    assert {v['state'] for v in query(client)} == {'waiting'} and standing(client) == before
    reverse = run(client, 'bank-feed reverse', dict(import_id=d['import_id']))
    assert reverse['state'] == 'reversed'


def test_entry_gone_shows_a_line_whose_entry_was_voided_deleted_or_changed(client):
    grant(client, *DELETES, 'transaction.check.delete')
    e1, e2, e3, e4 = (jr(client, f'2026-10-0{n}', f'{n}0.00') for n in (5, 6, 7, 8))
    d = imp(client, 'Checking', csv_file([(f'2026-10-0{n}', f'D{n}', f'{n}0.00') for n in (5, 6, 7, 8)]
                                         + [('2026-10-09', 'FUEL', '-45.67')]))
    l1, l2, l3, l4 = (line_of(d, n * 1000) for n in (5, 6, 7, 8))
    assert {v['state'] for v in (l1, l2, l3, l4)} == {'matched'} and query(client, entry_gone=True) == []
    fuel = line_of(d, -4567)
    check = run(client, 'bank-line add', dict(line=fuel['id'], payee='Central Supply',
                                              account='Vehicle Expense'))['entries'][0]['entry_id']
    rejr(client, e1['id'], '2026-10-05', '55.00')                         # amount
    run(client, 'journal void', dict(journal=e2['id'], expected_version=e2['version']))
    run(client, 'journal delete', dict(journal=e3['id'], expected_version=e3['version']))
    rejr(client, e4['id'], '2026-10-01', '80.00')                         # date
    run(client, 'check delete', dict(check=check, expected_version=1))
    gone = {v['id']: v['entry_gone'] for v in query(client, entry_gone=True)}
    assert set(gone) == {l1['id'], l2['id'], l3['id'], l4['id'], fuel['id']}
    assert '55.00' in gone[l1['id']] and 'voided' in gone[l2['id']] and 'deleted' in gone[l3['id']]
    assert 'dated 2026-10-01' in gone[l4['id']] and 'deleted' in gone[fuel['id']]
    assert all('bank-line unmatch' in v for v in gone.values())
    assert not set(gone) & {v['id'] for v in query(client, entry_gone=False)}
    assert inbox_line(client, l1['id'], 'Checking')['state'] == 'matched'  # still matched, now flagged
    for line_id in gone:
        assert run(client, 'bank-line unmatch', dict(line=line_id))['lines'][0]['state'] == 'waiting'
    assert query(client, entry_gone=True) == []
    # A voided entry is never proposed again.
    e5 = jr(client, '2026-10-01', '20.00')
    waiting = imp(client, 'Checking', csv_file([('2026-10-10', 'DEP', '20.00')]))['lines'][0]
    assert [p['entry_ids'] for p in inbox_line(client, waiting['id'], 'Checking')['proposals']] == [[e5['id']]]
    run(client, 'journal void', dict(journal=e5['id'], expected_version=e5['version']))
    assert inbox_line(client, waiting['id'], 'Checking').get('proposals') in (None, [], ())


def test_a_one_sided_transfer_is_completed_late_never_posted_twice(client):
    transfers = lambda: table(client, "SELECT count(*) FROM money_out_documents WHERE kind = 'transfer'")[0][0]
    demo = transfers()
    chk = imp(client, 'Checking', csv_file([('2026-10-06', 'TRANSFER OUT', '-700.00'),
                                           ('2026-10-06', 'TRANSFER A', '-300.00'),
                                           ('2026-10-09', 'TRANSFER B', '-300.00')]))
    out, a, b = line_of(chk, -70000), *[v for v in chk['lines'] if v['amount'] == -30000]
    sent = run(client, 'bank-line transfer', dict(line=out['id'], to='Payment Example Bank'))['entries'][0]['entry_id']
    t_a = run(client, 'bank-line transfer', dict(line=a['id'], to='Payment Example Bank'))['entries'][0]['entry_id']
    t_b = run(client, 'bank-line transfer', dict(line=b['id'], to='Payment Example Bank'))['entries'][0]['entry_id']
    assert transfers() == demo + 3
    peb = imp(client, 'Payment Example Bank', csv_file([('2026-10-16', 'TRANSFER IN', '700.00'),
                                                       ('2026-10-17', 'TRANSFER IN', '300.00')]))
    p700, p300 = line_of(peb, 70000), line_of(peb, 30000)
    assert (p700['state'], p300['state']) == ('waiting', 'waiting')  # outside the import matcher's window
    # `to` names the transfer that may be this line's other end rather than posting a second one.
    err = refused(client, 'bank-line transfer', dict(line=p700['id'], to='Checking'))
    assert [v['entry'] for v in err.details['candidates']] == [sent] and 'new' in err.message
    assert f'--entry {sent}' in err.message and transfers() == demo + 3
    # Without `to`, the one transfer that fits is completed: one entry, both lines linked to it.
    done = run(client, 'bank-line transfer', dict(line=p700['id']))
    assert done['entries'] == [] and done['lines'][0]['entry_id'] == sent and 'completes' in done['summary']
    assert transfers() == demo + 3 and done['lines'][0]['pair_line_id'] == out['id']
    # Two fit: listed, never chosen; named, completed.
    err = refused(client, 'bank-line transfer', dict(line=p300['id']))
    assert sorted(v['entry'] for v in err.details['candidates']) == sorted([t_a, t_b]) and transfers() == demo + 3
    assert run(client, 'bank-line transfer', dict(line=p300['id'], entry=t_b))['lines'][0]['entry_id'] == t_b
    assert transfers() == demo + 3


def test_stacked_undos_unwind_newest_first(client):
    d = imp(client, 'Checking', csv_file([('2026-10-05', 'A', '-1.00'), ('2026-10-06', 'B', '-2.00')]))
    a, b = line_of(d, -100), line_of(d, -200)
    b1 = run(client, 'bank-line ignore', dict(lines=[a['id'], b['id']], ignore_reason='other'))['batch_id']
    b2 = run(client, 'bank-line unmatch', dict(lines=[a['id']]))['batch_id']
    found = undo_blocked(client, b1)
    assert set(found) == {'later_sort'} and found['later_sort']['clears'] == f'`bank-line undo {b2}` first'
    run(client, 'bank-line undo', dict(batch=b2))
    assert table(client, 'SELECT state FROM bank_lines WHERE id = ?', a['id']) == [('ignored',)]
    run(client, 'bank-line undo', dict(batch=b1))
    assert table(client, 'SELECT state FROM bank_lines ORDER BY ordinal') == [('waiting',), ('waiting',)]


def test_retries_are_idempotent_and_refusals_point_at_what_clears_them(client):
    grant(client, *DELETES, 'transaction.check.delete')
    d = imp(client, 'Checking', csv_file([('2026-10-06', 'SHELL', '-45.67'), ('2026-10-07', 'SHELL 2', '-12.00')]))
    fuel, other = line_of(d, -4567), line_of(d, -1200)
    data = dict(line=fuel['id'], payee='Central Supply', account='Vehicle Expense')
    first = run(client, 'bank-line add', data, idempotency_key='bank-line-add-1')
    count = table(client, 'SELECT count(*) FROM transactions')
    again = run(client, 'bank-line add', data, idempotency_key='bank-line-add-1')
    assert again['entries'] == first['entries'] and table(client, 'SELECT count(*) FROM transactions') == count
    # Without the key, the added line's refusal names the entry's delete, never the unmatch that would refuse.
    err = refused(client, 'bank-line add', data)
    assert 'check delete' in err.message and 'returns it to waiting first' not in err.message
    err = refused(client, 'bank-line unmatch', dict(line=fuel['id']))
    assert 'check delete' in err.message
    # In a --lines batch, the hint is that batch's undo.
    batch = run(client, 'bank-line add', dict(lines=[dict(line=other['id'])], payee='Central Supply',
                                              account='Vehicle Expense'))['batch_id']
    err = refused(client, 'bank-line ignore', dict(line=other['id'], ignore_reason='other'))
    assert f'bank-line undo {batch}' in err.message
    # The hint clears it: delete the entry, then unmatch releases the line.
    run(client, 'check delete', dict(check=first['entries'][0]['entry_id'], expected_version=1))
    assert run(client, 'bank-line unmatch', dict(line=fuel['id']))['lines'][0]['state'] == 'waiting'
    for name, extra in (('bank-line match', {}), ('bank-line ignore', dict(ignore_reason='other')),
                        ('bank-line unmatch', {})):
        data = dict(lines=[fuel['id']] if name != 'bank-line match' else [dict(line=fuel['id'])], **extra)
        try:
            one = run(client, name, data, idempotency_key=f'{name}-1')
        except BookflowError:
            continue
        assert run(client, name, data, idempotency_key=f'{name}-1')['batch_id'] == one['batch_id']


def test_a_lines_add_that_fails_first_leaves_no_batch(client, monkeypatch):
    d = imp(client, 'Checking', csv_file([('2026-10-06', 'A', '-5.00')]))
    batches_before = table(client, "SELECT count(*) FROM batches WHERE kind = 'bank-sort'")
    from bookflow.company import bank_sort
    original = bank_sort.batches.run_nested

    def fail(s, ctx, name, payload, **kw):
        if not kw.get('dry_run'):
            raise BookflowError('E_VALIDATION', message='refused by the test')
        return original(s, ctx, name, payload, **kw)
    monkeypatch.setattr(bank_sort.batches, 'run_nested', fail)
    refused(client, 'bank-line add', dict(lines=[dict(line=d['lines'][0]['id'])], payee='Central Supply',
                                          account='Vehicle Expense'))
    monkeypatch.undo()
    assert table(client, "SELECT count(*) FROM batches WHERE kind = 'bank-sort'") == batches_before
