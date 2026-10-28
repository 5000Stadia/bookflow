"""R108 increment 1, seeded: random overlapping statement files into two accounts (design/bank-feeds.md §3, §10.1).

A pool of transactions per account holds re-dated pending lines (the same FITID, posted days later), FITIDs the bank
reused for another amount, and identical small purchases on one day. Each round cuts a random date window from one
account's pool (Checking downloads OFX with FITIDs, the other bank CSV without), sometimes repeats a window exactly,
and imports it. After every round: no transaction is in the inbox twice, every import that brought a line is one
batch with its effects, the books are unchanged, and `reconcile import` answers as before.
"""
import random
import sqlite3
from datetime import date, timedelta

import pytest

from bookflow import BookflowError
from tests.test_bank_feeds import books, csv_file, database_path, ofx, run, sample

SEEDS = (1087, 2026)
ROUNDS = 14


def pool(rng, prefix, start):
    """(id, pending date, posted date, amount, fitid, payee) per transaction; ids are what a line must be once."""
    out, day = [], start
    for n in range(40):
        day += timedelta(days=rng.choice((0, 1, 1, 2)))
        kind = rng.random()
        if kind < 0.2:  # identical small purchases on one day
            for k in range(rng.choice((2, 3))):
                out.append((f'{prefix}{n}c{k}', day, day, -450, f'{prefix}F{n}c{k}', 'COFFEE SHOP'))
        elif kind < 0.35:  # a pending line the bank re-dates when it posts
            out.append((f'{prefix}{n}', day, day + timedelta(days=rng.choice((1, 2, 3))),
                        -rng.randrange(500, 9000), f'{prefix}F{n}', f'STORE {n}'))
        elif kind < 0.45 and out:  # the bank reuses an earlier FITID for another amount
            out.append((f'{prefix}{n}', day, day, -rng.randrange(100, 900) - 1, out[-1][4], f'REUSED {n}'))
        else:
            out.append((f'{prefix}{n}', day, day, rng.choice((-1, 1)) * rng.randrange(100, 50000), f'{prefix}F{n}',
                        f'PAYEE {n}'))
    return out


def window(rng, txns, as_of):
    """The lines a download on `as_of` shows: dated within a random window, pending ones still at their first date."""
    first = min(t[1] for t in txns)
    lo = first + timedelta(days=rng.randrange(0, 40))
    hi = lo + timedelta(days=rng.randrange(5, 40))
    shown = []
    for t in txns:
        posted = t[2] <= as_of
        when = t[2] if posted else t[1]
        if lo <= when <= hi and t[1] <= as_of:
            shown.append((t, when))
    return shown


def file_for(account, shown):
    if account == 'Checking':
        return ofx([(when.isoformat(), f'{t[3] / 100:.2f}', t[4], t[5]) for t, when in shown])
    # A CSV carries no ids and its pending lines are not shown (as most banks' downloads).
    return csv_file([(when.isoformat(), t[5], f'{t[3] / 100:.2f}') for t, when in shown if when == t[2]])


def live_lines(client):
    with sqlite3.connect(database_path(client)) as conn:
        return conn.execute('SELECT l.account_id, a.name, l.line_key, l.fitid, l.amount, l.payee, l.date '
                            'FROM bank_lines l JOIN accounts a ON a.id = l.account_id WHERE l.live = 1').fetchall()


@pytest.mark.timeout(900)
@pytest.mark.parametrize('seed', SEEDS)
def test_random_overlapping_files_never_duplicate_and_change_no_books(client, seed):
    rng = random.Random(seed)
    pools = {'Checking': pool(rng, 'K', date(2026, 5, 1)), 'Payment Example Bank': pool(rng, 'P', date(2026, 5, 1))}
    golden = run(client, 'reconcile import', dict(account='Checking', content=sample('checking-2026-09.ofx')),
                 dry_run=True)
    before = books(client)
    seen: dict[str, set] = {name: set() for name in pools}  # pool ids any successful import showed
    imports, refusals, last = 0, 0, {}
    for round_ in range(ROUNDS):
        account = rng.choice(sorted(pools))
        as_of = date(2026, 5, 1) + timedelta(days=rng.randrange(10, 90))
        shown = (last.get(account) if rng.random() < 0.2 and account in last
                 else window(rng, pools[account], as_of))
        if not shown or (account != 'Checking' and not any(when == t[2] for t, when in shown)):
            continue
        content = file_for(account, shown)
        try:
            out = run(client, 'bank-feed import', dict(account=account, content=content))
        except BookflowError as error:
            assert error.code == 'E_ALREADY_IMPORTED', error
            refusals += 1
            continue
        last[account] = shown
        imports += out['import_id'] is not None
        seen[account] |= {t[0] for t, when in shown if account == 'Checking' or when == t[2]}
        lines = live_lines(client)
        # No transaction twice: each account holds exactly one live line per pool transaction its files showed.
        for name in pools:
            mine = [v for v in lines if v[1] == name]
            assert len({v[2] for v in mine}) == len(mine), 'a key is live twice'
            assert len(mine) == len(seen[name]), (seed, round_, name, len(mine), len(seen[name]))
        # Every import that brought a line is one live batch, with one effect per import row, line and match, and the account record.
        with sqlite3.connect(database_path(client)) as conn:
            rows = conn.execute(
                "SELECT i.lines_new, b.kind, b.state, (SELECT count(*) FROM batch_effects e WHERE e.batch_id = b.id), "
                "(SELECT count(*) FROM bank_lines l WHERE l.import_id = i.id AND l.entry_id IS NOT NULL) "
                'FROM bank_feed_imports i JOIN batches b ON b.id = i.batch_id').fetchall()
        assert len(rows) == imports
        assert all(kind == 'bank-import' and state == 'live' and n > 0 and effects == 2 + n + links
                   for n, kind, state, effects, links in rows)
        assert books(client) == before, 'an import posts nothing'
    assert imports >= 4, (seed, imports)
    after = run(client, 'reconcile import', dict(account='Checking', content=sample('checking-2026-09.ofx')),
                dry_run=True)
    strip = lambda d: {**d, 'counts': {k: v for k, v in d['counts'].items() if k != 'previously_imported'},
                       'lines': [{k: v for k, v in x.items() if k != 'previously_imported'} for x in d['lines']]}
    assert strip(after) == strip(golden)
    print(f'seed {seed}: {imports} imports, {refusals} refused repeats, '
          f"{sum(len(v) for v in seen.values())} transactions, {len(live_lines(client))} live lines")
