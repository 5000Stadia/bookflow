"""R108 increment 3, property (design/bank-feeds.md §11): random sequences of import, sort (match, add, ignore,
transfer, unmatch; one line or a --lines batch) and reverse (an import, or a --lines batch's undo) keep the books and
the inbox equal to a replay without the reversed batches, and the bank balance the inbox shows equals the books'.

Two bank accounts on the demo company: Checking by CSV and Payment Example Bank by OFX, with transfers between them,
entries posted for some lines (matched at import) and a few nine days off (proposed, then matched by an act). Each
round imports a window of an account's pool, sorts a random waiting line (or several of one import), returns a sorted
line to waiting, reverses a live import (`--through` when it is not its account's newest) or undoes a --lines batch.
Refusals change nothing and are not replayed. At the end the inbox and books are recorded; every import is reversed
(books back to the entries alone, inbox empty), and the acts whose batches were never reversed are replayed in their
order: inbox and books must be what they were. A --lines act here holds lines of one import and adds or ignores (a
match batch undone later would leave an entry free that an import in between saw taken).
"""
import random

import pytest

from bookflow import BookflowError
from tests.test_bank_feeds import csv_file, ofx, run, table
from tests.test_bank_feed_reversal import grant, journal

ROUNDS = 40
ACCOUNTS = ('Checking', 'Payment Example Bank')
OTHER = {'Checking': 'Payment Example Bank', 'Payment Example Bank': 'Checking'}


def pools(client, rng):
    """Each account's statement lines over 18 days, and the entries already in the books for some of them."""
    checking, peb, pool_entries = [], [], set()
    for n in range(18):
        day = f'2026-10-{2 + n:02d}'
        cents = f'{n:02d}'
        kind = n % 6
        if kind == 0:  # a transfer: out of Checking, into Payment Example Bank the next day
            amount = f'{rng.randint(100, 900)}.{cents}'
            checking.append((day, f'TRANSFER TO PEB {n}', f'-{amount}'))
            peb.append((f'2026-10-{3 + n:02d}', amount, f'T{n:03d}', f'TRANSFER FROM CHK {n}'))
            continue
        amount = f'{rng.randint(10, 99)}.{cents}'
        if kind in (1, 2):  # money in, entered already (matched at import); every other one nine days off
            checking.append((day, f'DEPOSIT {n}', amount))
            entry_day = day if kind == 1 else f'2026-10-{min(28, 2 + n + 9):02d}'
            pool_entries.add(journal(client, entry_day, amount)['id'])
        else:
            checking.append((day, f'CARD SHOP {n}', f'-{amount}'))
        bank = f'-{rng.randint(10, 99)}.{cents}'
        peb.append((day, bank, f'P{n:03d}', f'PAYEE {n}'))
        if kind == 3:
            pool_entries.add(journal(client, day, bank.lstrip('-'), debit='Office Supplies',
                                     credit='Payment Example Bank')['id'])
    return {'Checking': checking, 'Payment Example Bank': peb}, pool_entries


def content_for(account, pool, start, size):
    window = pool[start:start + size]
    return csv_file(window) if account == 'Checking' else ofx(window, acct='000012349999')


def account_ids(client):
    return {name: table(client, 'SELECT id FROM accounts WHERE name = ?', name)[0][0] for name in ACCOUNTS}


def live_lines(client):
    return [dict(zip(('id', 'account_id', 'line_key', 'state', 'created', 'entry_id', 'ignore_reason', 'import_id',
                      'amount', 'batch_id'), row)) for row in table(client, (
        'SELECT l.id, l.account_id, l.line_key, l.state, l.created, l.entry_id, l.ignore_reason, l.import_id, '
        'l.amount, i.batch_id FROM bank_lines l JOIN bank_feed_imports i ON i.id = l.import_id WHERE l.live = 1 '
        'ORDER BY l.account_id, l.line_key'))]


def live_imports(client, account):
    return [row[0] for row in table(client, (
        "SELECT i.id FROM bank_feed_imports i JOIN batches b ON b.id = i.batch_id JOIN accounts a ON a.id = i.account_id "
        "WHERE a.name = ? AND b.state = 'live' ORDER BY b.rowid"), account)]


def postings(client, transaction_id):
    return tuple(table(client, 'SELECT account_id, debit_minor_units, credit_minor_units FROM posting_lines '
                               'WHERE transaction_id = ? ORDER BY line_no', transaction_id))


def kind_of(client, transaction_id):
    marked = table(client, 'SELECT kind FROM money_out_documents WHERE transaction_id = ?', transaction_id)
    return marked[0][0] if marked else table(client, 'SELECT type FROM transactions WHERE id = ?', transaction_id)[0][0]


def signature(client, transaction_id, pool_entries):
    """An entry by what it is: the books' own entries by id; one made from a line by its kind and postings."""
    if transaction_id is None or transaction_id in pool_entries:
        return transaction_id
    return (kind_of(client, transaction_id), postings(client, transaction_id))


def books(client, pool_entries):
    """Every live entry by signature, and each account's balance as exact integers summed here, never in SQL."""
    entries = sorted(map(repr, (signature(client, t, pool_entries) for (t,) in table(
        client, "SELECT id FROM transactions WHERE status = 'posted' AND voided_at IS NULL"))))
    balances = {}
    for account_id, debit, credit in table(client, 'SELECT account_id, debit_minor_units, credit_minor_units '
                                                   'FROM posting_lines'):
        balances[account_id] = balances.get(account_id, 0) + (debit or 0) - (credit or 0)
    return entries, balances


def inbox(client, pool_entries):
    return [(v['account_id'], v['line_key'], v['state'], v['created'], v['ignore_reason'],
             repr(signature(client, v['entry_id'], pool_entries))) for v in live_lines(client)]


def bank_balances_agree(client, ids):
    """The bank balance the inbox shows (its linked lines, bank sign) equals the books' for the entries they link."""
    for name, account_id in ids.items():
        linked = [v for v in live_lines(client) if v['account_id'] == account_id and v['entry_id']]
        shown = sum(v['amount'] for v in linked)
        booked = 0
        for v in linked:
            for posted_to, debit, credit in postings(client, v['entry_id']):
                if posted_to == account_id:
                    booked += (debit or 0) - (credit or 0)
        assert shown == booked, (name, shown, booked)


def payee_and_account(amount):
    if amount < 0:
        return dict(payee='Central Supply', account='Office Supplies')
    return dict(payee='Riverside Apartments', payee_type='customer', account='Service Income')


class Replay:
    """The acts that took, in a form that names lines by account and key, so they run again on a fresh inbox. An act
    lives on the batches it was recorded on; an unmatch also on those of the act it undid (undoing a link another
    import's act made goes with that import)."""

    def __init__(self):
        self.acts = []  # (kind, batches it lives on, data)
        self.set_by = {}  # (account, key) -> the batches of the act that last set the line

    def add(self, kind, batches, data, lines=()):
        self.acts.append((kind, frozenset(batches), data))
        for line in lines:
            self.set_by[line] = frozenset(batches)


def line_id(client, ids, account, key):
    found = table(client, 'SELECT id FROM bank_lines WHERE account_id = ? AND line_key = ? AND live = 1',
                  ids[account], key)
    assert found, (account, key)
    return found[0][0]


def try_run(client, name, data):
    try:
        return run(client, name, data)
    except BookflowError as error:
        assert error.code in ('E_VALIDATION', 'E_BATCH_BLOCKED'), error
        return None


@pytest.mark.parametrize('seed', [108, 2026])
def test_random_sorts_and_reversals_equal_a_replay_without_the_reversed_batches(client, seed):
    grant(client, 'transaction.journal_entry.delete', 'transaction.check.delete', 'transaction.deposit.delete')
    rng = random.Random(seed)
    ids = account_ids(client)
    name_of = {v: k for k, v in ids.items()}
    pool, pool_entries = pools(client, rng)
    start_books = books(client, pool_entries)
    replay = Replay()
    totals = dict(imports=0, matched=0, added=0, ignored=0, transfers=0, unmatched=0, batches=0, reversed=0,
                  undone=0, refused=0)
    for _ in range(ROUNDS):
        lines = live_lines(client)
        waiting = [v for v in lines if v['state'] == 'waiting']
        roll = rng.random()
        if not lines or roll < 0.25:
            account = rng.choice(ACCOUNTS)
            content = content_for(account, pool[account], rng.randrange(0, 12), rng.randint(4, 9))
            done = run(client, 'bank-feed import', dict(account=account, content=content, again=True))
            if done['import_id']:
                replay.add('import', {done['batch_id']}, dict(account=account, content=content),
                           [(account, v['line_key']) for v in done['lines'] if v['outcome'] == 'new'])
                totals['imports'] += 1
            continue
        if waiting and roll < 0.65:
            line = rng.choice(waiting)
            account = name_of[line['account_id']]
            shown = next(v for v in run(client, 'bank-line query', dict(account=account, state='waiting',
                                                                       limit=1000))['lines'] if v['id'] == line['id'])
            proposals = {p['kind']: p for p in shown['proposals']}
            match = proposals.get('match')
            pair = proposals.get('transfer')
            if match and match['unique'] and match['entry_ids'][0] in pool_entries:
                done = try_run(client, 'bank-line match', dict(line=line['id'], entry=match['entry_ids'][0]))
                if done:
                    replay.add('match', {line['batch_id']}, dict(account=account, key=line['line_key'],
                                                                 entry=match['entry_ids'][0]),
                               [(account, line['line_key'])])
                    totals['matched'] += 1
            elif pair and pair['unique']:
                other = next(v for v in lines if v['id'] == pair['line_ids'][0])
                done = try_run(client, 'bank-line transfer', dict(line=line['id']))
                if done:
                    replay.add('transfer', {line['batch_id']}, dict(
                        account=account, key=line['line_key'], pair_batch=other['batch_id'],
                        pair_key=other['line_key'], pair_account=name_of[other['account_id']]),
                        [(account, line['line_key'])])
                    replay.set_by[(name_of[other['account_id']], other['line_key'])] = frozenset(
                        {line['batch_id'], other['batch_id']})
                    totals['transfers'] += 1
            elif 'TRANSFER' in shown['payee'] and rng.random() < 0.6:
                done = try_run(client, 'bank-line transfer', dict(line=line['id'], to=OTHER[account]))
                if done:
                    replay.add('transfer', {line['batch_id']}, dict(account=account, key=line['line_key'],
                                                                    to=OTHER[account]), [(account, line['line_key'])])
                    totals['transfers'] += 1
            elif rng.random() < 0.7:
                done = try_run(client, 'bank-line add', dict(line=line['id'], **payee_and_account(line['amount'])))
                if done:
                    replay.add('add', {line['batch_id']}, dict(account=account, key=line['line_key'],
                                                               amount=line['amount']), [(account, line['line_key'])])
                    totals['added'] += 1
            else:
                done = try_run(client, 'bank-line ignore', dict(line=line['id'], ignore_reason='other'))
                if done:
                    replay.add('ignore', {line['batch_id']}, dict(account=account, key=line['line_key']),
                               [(account, line['line_key'])])
                    totals['ignored'] += 1
            totals['refused'] += done is None
            continue
        if waiting and roll < 0.73:
            # Several waiting lines of one import, as one batch: entered, or set aside.
            first = rng.choice(waiting)
            group = [v for v in waiting if v['import_id'] == first['import_id']][:3]
            account = name_of[first['account_id']]
            if rng.random() < 0.6:
                done = try_run(client, 'bank-line add', dict(lines=[
                    dict(line=v['id'], **payee_and_account(v['amount'])) for v in group]))
                kind = 'add_lines'
            else:
                done = try_run(client, 'bank-line ignore', dict(lines=[v['id'] for v in group], ignore_reason='other'))
                kind = 'ignore_lines'
            if done:
                replay.add(kind, {first['batch_id'], done['batch_id']}, dict(
                    account=account, keys=[v['line_key'] for v in group], amounts=[v['amount'] for v in group]),
                    [(account, v['line_key']) for v in group])
                totals['batches'] += 1
            totals['refused'] += done is None
            continue
        sorted_lines = [v for v in lines if v['state'] in ('matched', 'ignored', 'transfer', 'settled')
                        and not v['created']]
        if sorted_lines and roll < 0.8:
            line = rng.choice(sorted_lines)
            where = (name_of[line['account_id']], line['line_key'])
            if try_run(client, 'bank-line unmatch', dict(line=line['id'])):
                replay.add('unmatch', {line['batch_id'], *replay.set_by.get(where, ())},
                           dict(account=where[0], key=where[1]), [where])
                totals['unmatched'] += 1
            continue
        sorts = [b for (b,) in table(client, "SELECT id FROM batches WHERE kind = 'bank-sort' AND state = 'live'")]
        if sorts and roll < 0.86:
            if try_run(client, 'bank-line undo', dict(batch=rng.choice(sorts))):
                totals['undone'] += 1
            else:
                totals['refused'] += 1
            continue
        account = rng.choice(ACCOUNTS)
        live = live_imports(client, account)
        if live:
            chosen = rng.choice(live)
            if try_run(client, 'bank-feed reverse', dict(import_id=chosen, through=chosen != live[-1])):
                totals['reversed'] += len(live) - live.index(chosen)
            else:
                totals['refused'] += 1
        bank_balances_agree(client, ids)
    bank_balances_agree(client, ids)
    recorded_books, recorded_inbox = books(client, pool_entries), inbox(client, pool_entries)
    reversed_batches = {b for (b,) in table(client, "SELECT id FROM batches WHERE state = 'reversed'")}

    # Everything still live reversed: the books are the entries alone again and the inbox is empty.
    for _ in range(4):
        for account in ACCOUNTS:
            live = live_imports(client, account)
            if live:
                try_run(client, 'bank-feed reverse', dict(import_id=live[0], through=True))
    assert not any(live_imports(client, a) for a in ACCOUNTS) and live_lines(client) == []
    assert books(client, pool_entries)[0] == start_books[0]

    # The acts never reversed, again, in their order.
    kept = [(k, d) for k, batches, d in replay.acts if not batches & reversed_batches]
    for kind, d in kept:
        if kind == 'import':
            assert run(client, 'bank-feed import', dict(account=d['account'], content=d['content'], again=True))[
                'import_id']
            continue
        if kind in ('add_lines', 'ignore_lines'):
            lines = [line_id(client, ids, d['account'], key) for key in d['keys']]
            if kind == 'add_lines':
                run(client, 'bank-line add', dict(lines=[dict(line=i, **payee_and_account(a))
                                                         for i, a in zip(lines, d['amounts'])]))
            else:
                run(client, 'bank-line ignore', dict(lines=lines, ignore_reason='other'))
            continue
        one = line_id(client, ids, d['account'], d['key'])
        if kind == 'match':
            run(client, 'bank-line match', dict(line=one, entry=d['entry']))
        elif kind == 'add':
            run(client, 'bank-line add', dict(line=one, **payee_and_account(d['amount'])))
        elif kind == 'ignore':
            run(client, 'bank-line ignore', dict(line=one, ignore_reason='other'))
        elif kind == 'unmatch':
            run(client, 'bank-line unmatch', dict(line=one))
        elif 'to' in d:
            run(client, 'bank-line transfer', dict(line=one, to=d['to']))
        elif d['pair_batch'] in reversed_batches:
            # Its other end went with its import: the transfer the act posted stands one-sided.
            run(client, 'bank-line transfer', dict(line=one, to=d['pair_account']))
        else:
            run(client, 'bank-line transfer', dict(line=one, pair=line_id(client, ids, d['pair_account'],
                                                                          d['pair_key'])))
    assert inbox(client, pool_entries) == recorded_inbox
    assert books(client, pool_entries) == recorded_books
    bank_balances_agree(client, ids)
    print(f'seed {seed}: {totals}, kept {len(kept)} of {len(replay.acts)} acts, live lines {len(recorded_inbox)}')
