"""Owner vocabulary reaches bank workflows without hiding generic list imports."""
import pytest
from bookflow.adapters.mcp.catalog import list_commands


@pytest.mark.parametrize('words,expected', [
    ('bank statement', 'bank-feed import'),
    ("here is September's checking statement", 'bank-feed import'),
    ('download from the bank', 'bank-feed import'),
    ('categorize', 'bank-line query'),
    ('sort the inbox', 'bank-line query'),
    ('wrong account', 'bank-feed reverse'),
    ('undo that bank import', 'bank-feed reverse'),
    ('always put Shell under Fuel', 'bank-rule create'),
    ('bank rule', 'bank-rule create'),
    ('is the card payment a transfer', 'bank-line transfer'),
    ('connect my bank', 'bank-feed-connection query'),
])
def test_owner_bank_words(words, expected):
    page = list_commands(prefix=words, limit=8)
    assert page['commands'][0]['name'] == expected


def test_ambiguous_undo_offers_both_import_families():
    names = [x['name'] for x in list_commands(prefix='undo that import', limit=8)['commands']]
    assert names[:3] == ['import reverse', 'bank-feed reverse', 'bank-feed move']


def test_reconcile_remains_discoverable():
    assert 'reconcile import' in [x['name'] for x in list_commands(prefix='reconcile', limit=100)['commands']]


def test_unmatched_inbox_guidance_sorts_in_place_without_changing_reconciliation(client):
    from bookflow.company import statement_files as files
    from tests.test_bank_feeds import csv_file, run

    body = csv_file([('2026-09-28', 'UNIDENTIFIED DEBIT', '-2221.23')])
    data = {'account': 'Checking', 'content': body}
    preview = run(client, 'bank-feed import', data, dry_run=True)
    imported = run(client, 'bank-feed import', data)
    stored = run(client, 'bank-line query', {'import_id': imported['import_id']})
    for line in (preview['lines'][0], imported['lines'][0], stored['lines'][0]):
        assert line['state'] == 'waiting'
        assert 'bank-line add' in line['reason']
        assert 'import again' not in line['reason']
    # The shared matcher keeps the existing reconciliation instruction.
    result, = files.match([files.Line('sample', '2026-09-28', -222123)], [])
    assert 'enter it, then import again' in result.reason


def test_move_preview_next_replays_account_confirmation_and_matching_choices(client, cli):
    import shlex
    from tests.test_bank_feeds import csv_file, run
    from tests.test_bank_feed_reversal import grant

    grant(client, 'transaction.check.delete')
    imported = run(client, 'bank-feed import', {'account': 'Payment Example Bank',
        'content': csv_file([('2026-09-14', 'MOVE FEE', '-14.23')])})
    run(client, 'bank-line add', {'line': imported['lines'][0]['id'],
        'payee': 'Central Supply', 'account': 'Office Supplies'})
    preview = run(client, 'bank-feed move', {'import_id': imported['import_id'], 'to': 'Checking',
        'account_confirmed': True, 'auto_match': False, 'match_days': 3, 'suggest_days': 7, 'limit': 1}, dry_run=True)
    next_args = shlex.split(preview['next'])
    assert '--account-confirmed' in next_args and '--no-auto-match' in next_args
    assert next_args[next_args.index('--match-days') + 1] == '3'
    assert next_args[next_args.index('--suggest-days') + 1] == '7'
    assert next_args[next_args.index('--limit') + 1] == '1'
    moved = cli.json(*next_args, '--company', 'Demo Plumbing Co', '--reason', 'Replay exact preview next')
    assert moved['reversal']['state'] == 'reversed' and moved['reversal']['remaining'] == 0
    assert moved['target']['counts']['new'] == 1
