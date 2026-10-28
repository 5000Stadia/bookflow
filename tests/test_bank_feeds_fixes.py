"""R108 increment 1, review fixes: a real bank line is never silently lost or doubled; when the inbox is not sure it
keeps the line and says why; every import is one reversible unit (design/bank-feeds.md §3, "As built: increment 1").

Each test is a probe from the increment 1 review (notes/reviews/r108-inc1-review.md) that failed before its fix.
"""
import json

from bookflow.company import statement_files as files
from bookflow.core.ids import new_id
from tests.test_bank_feeds import csv_file, ofx, refused, run, table


def imp(client, account, content, dry_run=False, **kw):
    return run(client, 'bank-feed import', dict(account=account, content=content, **kw), dry_run=dry_run)


def deposit(client, day, amount, account='Checking'):
    return run(client, 'journal post', dict(date=day, memo='deposit', lines=[
        dict(account=account, side='debit', amount=amount), dict(account='Service Income', side='credit', amount=amount)]))


def test_an_edited_entry_is_never_matched_by_two_lines(client):
    """High 1: an entry already linked stays out of matching after an edit gives it a new revision."""
    entry = deposit(client, '2026-10-01', '77.77')
    first = imp(client, 'Checking', csv_file([('2026-10-01', 'CUSTOMER A', '77.77')]))
    assert first['lines'][0]['state'] == 'matched' and first['lines'][0]['entry_id'] == entry['id']
    run(client, 'journal update', dict(journal=entry['id'], expected_version=entry['version'], memo='edited'))
    second = imp(client, 'Checking', csv_file([('2026-10-02', 'CUSTOMER B', '77.77')]))
    assert second['lines'][0]['state'] == 'waiting' and second['lines'][0]['entry_id'] is None
    assert table(client, 'SELECT entry_id, count(*) FROM bank_lines WHERE entry_id IS NOT NULL GROUP BY entry_id') \
        == [(entry['id'], 1)]


def test_a_statement_holds_a_line_only_when_it_is_the_same_line_on_a_standing_draft(client):
    """High 2: a FITID a reconciliation holds for another amount imports, flagged; a discarded draft holds nothing."""
    from tests.test_reconcile_import import opening
    opening(client)
    first = ofx([('2026-09-02', '-9.99', 'R1', 'STREAMING')])
    draft = run(client, 'reconcile import', dict(account='Checking', content=first, start=True,
                                                 statement_date='2026-09-30', ending_balance='1000.00'))['draft']
    same = imp(client, 'Checking', first, dry_run=True)
    assert same['counts']['in_statement'] == 1 and same['counts']['new'] == 0
    reused = imp(client, 'Checking', ofx([('2026-09-20', '-15.00', 'R1', 'STREAMING PLUS')]))
    line, = reused['lines']
    assert (line['outcome'], line['warnings']) == ('new', ['fitid_reused']) and reused['counts']['fitid_reused'] == 1
    run(client, 'reconcile discard', dict(operation_key=new_id(), draft=draft['id'],
                                          expected_version=draft['version']))
    back = imp(client, 'Checking', first)
    assert back['counts']['new'] == 1 and back['counts']['in_statement'] == 0


def test_the_number_an_import_teaches_is_a_batch_effect_with_what_it_was(client):
    """Medium 3: the account record a first or confirmed import changes is in its batch, with `before`."""
    first = imp(client, 'Payment Example Bank', ofx([('2026-09-01', '-1.00', 'A1', 'X')]))
    created, = table(client, "SELECT action, version_after, before FROM batch_effects WHERE batch_id = ? AND "
                             "record_type = 'bank_feed_account'", first['batch_id'])
    assert created == ('created', 1, None)
    other = ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001')
    confirmed = imp(client, 'Payment Example Bank', other, confirm_account=True)
    (action, version, before), = table(client, "SELECT action, version_after, before FROM batch_effects WHERE "
                                               "batch_id = ? AND record_type = 'bank_feed_account'",
                                       confirmed['batch_id'])
    before = json.loads(before)
    assert (action, version) == ('updated', 2)
    assert (before['account_last4'], before['bank_number'], before['last_import_id'], before['version']) == \
        ('5678', '011000015', first['import_id'], 1)
    assert table(client, 'SELECT account_last4, last_import_id FROM bank_feed_accounts') == \
        [('0001', confirmed['import_id'])]


def test_a_monthly_charge_under_one_fitid_is_a_new_line_each_month(client):
    """Medium 4: the same FITID and amount a month apart is two charges, in one file and across files."""
    both = imp(client, 'Checking', ofx([('2026-10-01', '-9.99', 'SUB', 'STREAM'), ('2026-11-01', '-9.99', 'SUB', 'STREAM')]))
    assert both['counts']['new'] == 2 and both['counts']['repeated_in_file'] == 0
    assert [v['warnings'] for v in both['lines']] == [[], ['fitid_reused']]
    nextmonth = imp(client, 'Checking', ofx([('2026-12-01', '-9.99', 'SUB', 'STREAM')]))
    line, = nextmonth['lines']
    assert (line['outcome'], line['warnings']) == ('new', ['fitid_reused'])
    # Within the drift it is the same line, re-dated; the whole file again brings nothing.
    posted = imp(client, 'Checking', ofx([('2026-12-04', '-9.99', 'SUB', 'STREAM')]))
    assert posted['counts']['already_in'] == 1 and posted['import_id'] is None
    again = imp(client, 'Checking', ofx([('2026-10-01', '-9.99', 'SUB', 'STREAM'), ('2026-11-01', '-9.99', 'SUB', 'STREAM'),
                                         ('2026-12-01', '-9.99', 'SUB', 'STREAM')]))
    assert again['counts']['already_in'] == 3 and again['counts']['new'] == 0
    assert table(client, "SELECT count(*) FROM bank_lines WHERE fitid = 'SUB'") == [(3,)]


def test_a_pending_line_posted_under_a_new_fitid_is_kept_and_flagged(client):
    """Medium 5: the soft check covers FITID lines and reads payees by merchant name, not a shared word."""
    pending = imp(client, 'Checking', ofx([('2026-09-01', '-40.00', 'PEND1', 'GAS STATION')]))
    posted = imp(client, 'Checking', ofx([('2026-09-02', '-40.00', 'POST9', 'GAS STATION')]))
    line, = posted['lines']
    assert line['outcome'] == 'new' and line['warnings'] == ['possible_duplicate']
    assert line['possible_duplicate_of'] == pending['lines'][0]['id']
    imp(client, 'Checking', csv_file([('2026-09-10', 'AMZN MKTP US*2K3', '-23.45')]))
    alias = imp(client, 'Checking', csv_file([('2026-09-11', 'AMAZON.COM', '-23.45')]))
    assert alias['lines'][0]['warnings'] == ['possible_duplicate']
    # Two merchants plainly different are two lines; a payee it cannot read is flagged rather than doubled.
    imp(client, 'Checking', csv_file([('2026-09-15', 'SHELL OIL 1234', '-31.00'), ('2026-09-15', '#0042', '-17.00')]))
    later = imp(client, 'Checking', csv_file([('2026-09-16', 'STARBUCKS', '-31.00'), ('2026-09-16', 'BAKERY', '-17.00')]))
    assert {v['payee']: v['warnings'] for v in later['lines']} == {'BAKERY': ['possible_duplicate'], 'STARBUCKS': []}
    assert table(client, 'SELECT count(*) FROM bank_lines WHERE live = 1') == [(8,)], 'nothing dropped'


def test_a_csv_whose_lines_are_another_accounts_entries_is_refused_unless_confirmed(client):
    """Medium 6: a CSV carries no number, so its content says whose statement it is."""
    for day, amount in (('2026-06-03', '311.11'), ('2026-06-05', '322.22'), ('2026-06-09', '333.33')):
        deposit(client, day, amount, account='Payment Example Bank')
    content = csv_file([('2026-06-03', 'DEPOSIT', '311.11'), ('2026-06-05', 'DEPOSIT', '322.22'),
                        ('2026-06-10', 'DEPOSIT', '333.33')])
    error = refused(client, dict(account='Checking', content=content), 'E_ACCOUNT_MISMATCH')
    assert "looks like Payment Example Bank's statement: 3 of its 3 lines match entries" in error.message
    assert table(client, 'SELECT count(*) FROM bank_lines') == [(0,)]
    confirmed = imp(client, 'Checking', content, account_confirmed=True, dry_run=True)
    assert confirmed['counts']['new'] == 3
    # Into its own account it comes in and matches; then Checking refuses it as that account's inbox lines.
    right = imp(client, 'Payment Example Bank', content)
    assert right['counts']['matched'] == 3
    error = refused(client, dict(account='Checking', content=content), 'E_ACCOUNT_MISMATCH')
    assert "3 of its 3 lines are already in Payment Example Bank's bank inbox" in error.message


def test_a_file_with_nothing_new_writes_nothing_not_even_a_re_date(client):
    """Medium 7: as built, a file with nothing new writes nothing."""
    imp(client, 'Checking', ofx([('2026-09-02', '-2.00', 'B1', 'Y')]))
    before = [table(client, f'SELECT count(*) FROM {t}') for t in ('bank_line_events', 'audit_events', 'batches')]
    out = imp(client, 'Checking', ofx([('2026-09-05', '-2.00', 'B1', 'Y')]))
    assert out['import_id'] is None and out['counts']['already_in'] == 1
    assert [table(client, f'SELECT count(*) FROM {t}') for t in ('bank_line_events', 'audit_events', 'batches')] == \
        before
    # Brought with a new line, the re-date is noted inside that import's batch.
    out = imp(client, 'Checking', ofx([('2026-09-05', '-2.00', 'B1', 'Y'), ('2026-09-06', '-3.00', 'B2', 'Z')]))
    moved, = table(client, "SELECT import_id, batch_id FROM bank_line_events WHERE kind = 'seen_again'")
    assert moved == (out['import_id'], out['batch_id'])


def test_an_open_entry_outranks_one_a_certified_statement_already_cleared():
    """Low: the shared matcher never leaves the open entry behind for a reconciled one nearer in date."""
    line = files.Line(line_id='l', date='2026-09-10', amount=-5000, payee='RENT')
    cleared = files.Candidate(ref='cleared', date='2026-09-10', amount=-5000, payees=('RENT',), reconciled=True)
    open_ = files.Candidate(ref='open', date='2026-09-12', amount=-5000, payees=('RENT',))
    result, = files.match([line], [cleared, open_])
    assert (result.status, result.match.ref) == ('matched', 'open')


def test_the_preview_match_is_reused_by_the_import_when_nothing_changed(client, monkeypatch):
    """Performance: a dry run followed by the import matches once."""
    calls = []
    real = files.match
    monkeypatch.setattr(files, 'match', lambda *a, **k: calls.append(1) or real(*a, **k))
    deposit(client, '2026-08-01', '55.55')
    content = csv_file([('2026-08-01', 'CUSTOMER', '55.55'), ('2026-08-02', 'OTHER', '-1.23')])
    preview = imp(client, 'Checking', content, dry_run=True)
    done = imp(client, 'Checking', content)
    assert len(calls) == 1 and done['counts'] == preview['counts'] and done['counts']['matched'] == 1
