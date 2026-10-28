"""R108 increment 2, property (design/bank-feeds.md §11): random sequences of import, match and reverse keep the books
and the inbox equal to a replay without the reversed imports.

Two accounts on the demo company: Checking by CSV (no ids; identical lines across overlapping windows) and Payment
Example Bank by OFX with FITIDs, each with entries posted for about half its transactions, so imports auto-match.
Each round imports a random window of an account's pool, or reverses a random live import (the newest of its account,
or an older one with ``through``). At the end the inbox is recorded; every live import is reversed (books and inbox
back to the start), and the imports never reversed are imported again in their original order: the inbox must be
exactly what it was, line for line, match for match, with the account records the same.
"""
import random

import pytest

from tests.test_bank_feeds import books, csv_file, ofx, run, table
from tests.test_bank_feed_reversal import inbox, journal

ROUNDS = 14


def pools(client, rng):
    checking, other = [], []
    for n in range(24):
        day = f'2026-10-{1 + n:02d}'
        amount = f'{rng.randint(1000, 9999) // 100}.{n:02d}'
        checking.append((day, f'CHECKING ITEM {n}', amount))
        if n % 2 == 0:
            journal(client, day, amount)
        bank_amount = f'-{rng.randint(10, 99)}.{n:02d}'
        other.append((day, bank_amount, f'PEB{n:03d}', f'PAYEE {n}'))
        if n % 3 == 0:
            journal(client, day, bank_amount.lstrip('-'), debit='Office Supplies', credit='Payment Example Bank')
    return checking, other


def content_for(account, pool, start, size):
    window = pool[start:start + size]
    return csv_file(window) if account == 'Checking' else ofx(window, acct='000012349999')


def live_imports(client, account):
    return [row[0] for row in table(client, (
        "SELECT i.id FROM bank_feed_imports i JOIN batches b ON b.id = i.batch_id JOIN accounts a ON a.id = i.account_id "
        "WHERE a.name = ? AND b.state = 'live' ORDER BY b.rowid"), account)]


@pytest.mark.parametrize('seed', [108, 2026])
def test_random_imports_and_reversals_equal_a_replay_without_the_reversed_imports(client, seed):
    rng = random.Random(seed)
    start_books, start_inbox = books(client), inbox(client)
    checking, other = pools(client, rng)
    after_entries = books(client)
    pool = {'Checking': checking, 'Payment Example Bank': other}
    kept = []  # (import id, account, content) of imports still live, in import order
    totals = dict(imports=0, reversed=0, through=0, lines=0, matched=0)
    for _ in range(ROUNDS):
        live = [k for k in kept]
        if not live or rng.random() < 0.6:
            account = rng.choice(sorted(pool))
            content = content_for(account, pool[account], rng.randrange(0, 18), rng.randint(3, 8))
            done = run(client, 'bank-feed import', dict(account=account, content=content, again=True))
            if done['import_id']:
                kept.append((done['import_id'], account, content))
                totals['imports'] += 1
                totals['lines'] += done['counts']['new']
                totals['matched'] += done['counts']['matched']
            continue
        import_id, account, _ = rng.choice(live)
        newer = live_imports(client, account)
        through = newer[-1] != import_id
        run(client, 'bank-feed reverse', dict(import_id=import_id, through=through))
        gone = set(newer[newer.index(import_id):])
        totals['reversed'] += len(gone)
        totals['through'] += through
        kept = [k for k in kept if k[0] not in gone]
        assert books(client) == after_entries, 'a reversal never changes the books here'
    assert {k[0] for k in kept} == {i for a in pool for i in live_imports(client, a)}
    recorded = inbox(client)
    # Reverse everything still live: back to the start.
    for account in pool:
        survivors = live_imports(client, account)
        if survivors:
            run(client, 'bank-feed reverse', dict(import_id=survivors[0], through=True))
    assert inbox(client)[0] == start_inbox[0] and books(client) == after_entries
    # Replay only the imports never reversed, in their order. The account record's last import is the same import by
    # its place among them (the replay's import ids are new).
    replayed = [run(client, 'bank-feed import', dict(account=account, content=content, again=True))['import_id']
                for _, account, content in kept]

    def placed(state, ids):
        lines, accounts = state
        return lines, [(*row[:4], ids.index(row[4]) if row[4] else None) for row in accounts]
    assert placed(inbox(client), replayed) == placed(recorded, [k[0] for k in kept])
    assert books(client) == after_entries and start_books != after_entries
    print(f'seed {seed}: {totals}, kept {len(kept)}, live lines {len(recorded[0])}')
