"""R108 increment 1: statement files into the bank inbox, never duplicating an overlap (design/bank-feeds.md §3).

Against the demo's Checking (the same books `reconcile import`'s samples are written for): each line is known by its
FITID or, without one, a hash with its occurrence in the file; a re-dated pending line, a reused FITID, identical
coffees across overlapping files, a payee the bank cleaned up and a card file each land as §3 says; an OFX file for
another account refuses; a CSV mostly in another account shows the overlap; a junk file refuses whole with every
problem; each import is one batch; the books never change; and `reconcile import` answers as before.
"""
from pathlib import Path
import sqlite3

import pytest

from bookflow import BookflowError
from tests.test_service_sales_lifecycle import COMPANY
from tests.test_row8_journal import database_path

SAMPLES = Path(__file__).parent / 'fixtures' / 'statements'


def run(client, name, data, **ctx):
    from bookflow.core import registry
    if registry.get(name).is_write:
        ctx.setdefault('reason', 'R108 bank feed import')
    return client.run(name, data, company=COMPANY, **ctx)


def sample(name):
    return (SAMPLES / name).read_text()


def ofx(lines, *, acct='000012345678', bank='011000015', card=False):
    """An OFX 1 file: (date, amount, fitid, name) per line."""
    body = ''.join(f'<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{d.replace("-", "")}120000\n<TRNAMT>{a}\n'
                   + (f'<FITID>{f}\n' if f else '') + f'<NAME>{n}\n</STMTTRN>\n' for d, a, f, n in lines)
    if card:
        head, tail = ('<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>\n<CURDEF>USD\n'
                      f'<CCACCTFROM><ACCTID>{acct}\n</CCACCTFROM>\n'), '</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>'
    else:
        head, tail = ('<BANKMSGSRSV1><STMTTRNRS><STMTRS>\n<CURDEF>USD\n'
                      f'<BANKACCTFROM><BANKID>{bank}\n<ACCTID>{acct}\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>\n'), \
            '</STMTRS></STMTTRNRS></BANKMSGSRSV1>'
    return f'OFXHEADER:100\nDATA:OFXSGML\n\n<OFX>\n{head}<BANKTRANLIST>\n{body}</BANKTRANLIST>\n{tail}\n</OFX>\n'


def csv_file(rows):
    return 'Date,Description,Amount\n' + ''.join(f'{d},{n},{a}\n' for d, n, a in rows)


def books(client):
    """What an import must never change: every posting and transaction row."""
    with sqlite3.connect(database_path(client)) as conn:
        return (conn.execute('SELECT count(*) FROM transactions').fetchone(),
                conn.execute('SELECT * FROM posting_lines ORDER BY rowid').fetchall())


def table(client, sql, *args):
    with sqlite3.connect(database_path(client)) as conn:
        return conn.execute(sql, args).fetchall()


def refused(client, data, code):
    with pytest.raises(BookflowError) as raised:
        run(client, 'bank-feed import', data)
    assert raised.value.code == code, raised.value
    return raised.value


def test_an_ofx_statement_comes_in_once_as_one_batch_and_posts_nothing(client):
    before = books(client)
    content = sample('checking-2026-09.ofx')
    preview = run(client, 'bank-feed import', dict(account='Checking', content=content), dry_run=True)
    assert preview['import_id'] is None and table(client, 'SELECT count(*) FROM bank_lines') == [(0,)]
    counts = preview['counts']
    # Ten transactions and one FITID the file repeats; eight match an entry surely, the late deposit is suggested and
    # the 12.00 fee waits to be entered.
    assert (counts['lines_in_file'], counts['new'], counts['repeated_in_file']) == (11, 10, 1)
    assert (counts['matched'], counts['settled'], counts['waiting']) == (8, 0, 2)
    assert preview['account_check']['status'] == 'first' and preview['account_check']['file_account_last4'] == '5678'
    # The first numbered file names the number it teaches the account, and learns it on the run.
    assert preview['account_check']['learns'] == 'account ...5678 at bank 011000015 (bank statement)'
    assert any('teaches it account ...5678' in w for w in preview['warnings'])
    done = run(client, 'bank-feed import', dict(account='Checking', content=content))
    assert done['counts'] == counts and done['import_id'] and done['batch_id']
    assert [v['line_key'] for v in done['lines']] == [v['line_key'] for v in preview['lines']]
    suggested = next(v for v in done['lines'] if v['amount'] == 20000)
    assert suggested['state'] == 'waiting' and suggested['suggestions']
    assert books(client) == before, 'an import posts nothing'
    # The batch: strict, scoped to the account, one effect per import row, line and match.
    batch, = table(client, 'SELECT kind, ordering, scope, state FROM batches WHERE id = ?', done['batch_id'])
    assert batch[:2] == ('bank-import', 'strict') and batch[3] == 'live' and batch[2].startswith('bank-account:')
    effects = dict(table(client, 'SELECT record_type, count(*) FROM batch_effects WHERE batch_id = ? '
                                 'GROUP BY record_type', done['batch_id']))
    assert effects == {'bank_feed_import': 1, 'bank_line': 10, 'transaction': 8, 'bank_feed_account': 1}
    shown = run(client, 'bank-feed show', dict(import_id=done['import_id'], state='waiting'))
    assert shown['by_state'] == {'matched': 8, 'waiting': 2} and len(shown['lines']) == 2
    assert run(client, 'bank-feed query', dict(account='Checking'))['imports'][0]['id'] == done['import_id']
    inbox = run(client, 'bank-line query', dict(account='Checking', state='matched'))
    assert inbox['counts'] == {'matched': 8, 'waiting': 2} and all(v['entry_id'] for v in inbox['lines'])
    assert run(client, 'bank-line query', dict(account='Checking', amount='-12.00'))['lines'][0]['payee'] == \
        'MONTHLY SERVICE FEE'
    # The same file again is refused, naming the import; with `again` it brings nothing new and writes nothing.
    error = refused(client, dict(account='Checking', content=content), 'E_ALREADY_IMPORTED')
    assert error.details['import'] == done['import_id']
    again = run(client, 'bank-feed import', dict(account='Checking', content=content, again=True))
    assert again['import_id'] is None and again['counts']['new'] == 0 and again['counts']['already_in'] == 10
    assert again['summary'] == '11 lines: 0 new, 10 already in, 1 repeated in the file'
    assert table(client, 'SELECT count(*) FROM bank_feed_imports') == [(1,)]
    # The guards: what the bank printed never changes and nothing is deleted.
    with sqlite3.connect(database_path(client)) as conn:
        for sql in ("UPDATE bank_lines SET amount = 1", 'DELETE FROM bank_lines', 'DELETE FROM bank_line_events',
                    "UPDATE bank_feed_imports SET reason = 'x'"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_re_dated_pending_lines_and_reused_fitids(client):
    first = ofx([('2026-09-01', '-40.00', 'P1', 'GAS STATION'), ('2026-09-02', '-9.99', 'R1', 'STREAMING')])
    run(client, 'bank-feed import', dict(account='Checking', content=first))
    # The pending gas purchase posts two days later under the same FITID; the bank reuses R1 for another amount.
    second = ofx([('2026-09-03', '-40.00', 'P1', 'GAS STATION'), ('2026-09-02', '-9.99', 'R1', 'STREAMING'),
                  ('2026-09-20', '-15.00', 'R1', 'STREAMING PLUS')])
    out = run(client, 'bank-feed import', dict(account='Checking', content=second))
    assert (out['counts']['new'], out['counts']['already_in'], out['counts']['fitid_reused']) == (1, 2, 1)
    reused = next(v for v in out['lines'] if v['outcome'] == 'new')
    assert reused['line_key'].startswith('fitid:R1!') and reused['warnings'] == ['fitid_reused']
    drift, = table(client, "SELECT e.details FROM bank_line_events e JOIN bank_lines l ON l.id = e.line_id "
                           "WHERE e.kind = 'seen_again' AND l.fitid = 'P1'")
    assert '"date_was": "2026-09-01"' in drift[0] and '"date_in_file": "2026-09-03"' in drift[0]
    # The same reuse read again is the same line.
    third = run(client, 'bank-feed import', dict(account='Checking', content=second + '\n'))
    assert third['counts']['new'] == 0 and third['import_id'] is None


def test_identical_coffees_across_overlapping_files_and_a_cleaned_up_payee(client):
    two = [('2026-08-03', 'COFFEE SHOP', '-4.50'), ('2026-08-03', 'COFFEE SHOP', '-4.50')]
    a = run(client, 'bank-feed import', dict(account='Checking', content=csv_file(two)))
    assert a['counts']['new'] == 2
    overlap = run(client, 'bank-feed import', dict(account='Checking', content=csv_file(
        [*two, ('2026-08-04', 'BAKERY', '-3.00')])))
    assert (overlap['counts']['new'], overlap['counts']['already_in']) == (1, 2)
    three = run(client, 'bank-feed import', dict(account='Checking', content=csv_file(
        [*two, ('2026-08-03', 'COFFEE SHOP', '-4.50')])))
    assert (three['counts']['new'], three['counts']['soft_duplicates']) == (1, 0), 'a third coffee is a new line'
    # The bank cleans its text up: the same purchase arrives with a new hash, flagged, never dropped.
    cleaned = run(client, 'bank-feed import', dict(account='Checking', content=csv_file(
        [('2026-08-05', 'SQ *BAKERY 0042', '-3.00')])))
    line, = cleaned['lines']
    assert line['outcome'] == 'new' and line['warnings'] == ['possible_duplicate'] and line['possible_duplicate_of']
    assert cleaned['counts']['soft_duplicates'] == 1


def test_a_card_file_keeps_the_banks_sign_and_is_checked_against_the_account(client):
    card = ofx([('2026-09-10', '-62.10', 'C1', 'OFFICE SUPPLIES'), ('2026-09-28', '500.00', 'C2', 'PAYMENT THANK YOU')],
               acct='4111111111111111', card=True)
    # A card statement sent to a bank account refuses, naming why.
    error = refused(client, dict(account='Checking', content=card), 'E_ACCOUNT_MISMATCH')
    assert 'card statement' in error.message and error.details['file']['kind'] == 'card'
    out = run(client, 'bank-feed import', dict(account='Business Credit Card', content=card))
    assert [(v['amount'], v['state']) for v in out['lines']] == [(-6210, 'waiting'), (50000, 'waiting')]
    assert out['account_check']['status'] == 'first'
    # Its account number is now known: the same number into the bank account names the card account.
    error = refused(client, dict(account='Checking', content=card.replace('C1', 'C9')), 'E_ACCOUNT_MISMATCH')
    assert error.details['received_by_name'] == 'Business Credit Card'


def test_an_ofx_file_for_another_account_number_refuses_and_a_person_may_confirm(client):
    run(client, 'bank-feed import', dict(account='Checking', content=ofx([('2026-09-01', '-1.00', 'A1', 'X')])))
    other = ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001')
    error = refused(client, dict(account='Checking', content=other), 'E_ACCOUNT_MISMATCH')
    assert error.details['known']['account_last4'] == '5678' and error.details['file']['account_last4'] == '0001'
    # Nothing was written by the refusal.
    assert table(client, 'SELECT count(*) FROM bank_feed_imports') == [(1,)]
    out = run(client, 'bank-feed import', dict(account='Checking', content=other, confirm_account=True))
    assert out['account_check']['status'] == 'confirmed' and out['counts']['new'] == 1
    assert table(client, 'SELECT account_last4 FROM bank_feed_accounts') == [('0001',)]


def test_a_csv_mostly_in_another_account_shows_the_overlap_before_importing(client):
    rows = [(f'2026-07-{d:02d}', f'VENDOR {d}', f'-{d}.00') for d in range(1, 15)]
    run(client, 'bank-feed import', dict(account='Payment Example Bank', content=csv_file(rows[:12])))
    # Most of its lines are in another account's inbox: it looks like that account's statement and is refused,
    # preview included, unless the caller says it is this account's.
    error = refused(client, dict(account='Checking', content=csv_file(rows)), 'E_ACCOUNT_MISMATCH')
    assert error.message.startswith("This looks like Payment Example Bank's statement: 12 of its 14 lines")
    assert error.details['looks_like_name'] == 'Payment Example Bank'
    preview = run(client, 'bank-feed import', dict(account='Checking', content=csv_file(rows),
                                                   account_confirmed=True), dry_run=True)
    overlap, = preview['overlap_elsewhere']
    assert (overlap['account'], overlap['lines'], overlap['percent']) == ('Payment Example Bank', 12, 85)
    assert any('Payment Example Bank' in w for w in preview['warnings'])
    assert preview['account_check']['status'] == 'no_id'


def test_a_file_with_junk_refuses_whole_listing_every_problem(client):
    content = csv_file([('2026-09-01', 'OK', '-1.00'), ('2026-13-01', 'BAD DATE', '-2.00'),
                        ('2026-09-03', 'BAD AMOUNT', 'TBD'), ('2026-09-04', 'TOO PRECISE', '-1.005')])
    error = refused(client, dict(account='Checking', content=content), 'E_VALIDATION')
    fields = [f['field'] for f in error.details['fields']]
    assert fields == ['row 3 date', 'row 4 amount', 'row 5 amount'] and error.details['total'] == 3
    assert table(client, 'SELECT count(*) FROM bank_lines') == [(0,)]
    junk = ofx([('2026-09-01', '1.00', 'J1', 'A'), ('2026-09-01', 'x', 'J2', 'B'), ('2026-02-30', '1', 'J3', 'C')])
    error = refused(client, dict(account='Checking', content=junk), 'E_VALIDATION')
    assert [f['field'] for f in error.details['fields']] == ['STMTTRN[1].TRNAMT', 'STMTTRN[2].DTPOSTED']


def test_reconcile_import_answers_as_before_beside_the_inbox(client):
    """The inbox shares the parser, matcher and mappings; `reconcile import` gives the same answers with lines in the
    inbox as without, except that it now knows those lines were imported before."""
    answers = {}
    for name in ('checking-2026-09.ofx', 'checking-2026-09.csv'):
        answers[name] = run(client, 'reconcile import', dict(account='Checking', content=sample(name)), dry_run=True)
    run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.ofx')))
    run(client, 'bank-feed import', dict(account='Checking', content=sample('checking-2026-09.csv'),
                                         save_mapping='bank csv'))
    for name, before in answers.items():
        after = run(client, 'reconcile import', dict(account='Checking', content=sample(name)), dry_run=True)
        strip = lambda d: {**d, 'counts': {k: v for k, v in d['counts'].items() if k != 'previously_imported'},
                           'lines': [{k: v for k, v in line.items() if k != 'previously_imported'}
                                     for line in d['lines']]}
        assert strip(after) == strip(before), name
        assert after['counts']['previously_imported'] == after['counts']['lines']
    # A mapping saved through the inbox serves `reconcile import` too.
    shared = run(client, 'reconcile import', dict(account='Checking', content=sample('checking-2026-09.csv'),
                                                  mapping_name='bank csv'), dry_run=True)
    assert shared['counts']['matched'] == answers['checking-2026-09.csv']['counts']['matched']


def test_a_standard_member_imports_but_only_bank_feed_connect_goes_past_a_mismatch(client, root):
    from tests.conftest import as_user, make_actor
    run(client, 'bank-feed import', dict(account='Checking', content=ofx([('2026-09-01', '-1.00', 'A1', 'X')])))
    make_actor(Path(root), 'clerk')
    client.run('membership grant', {'user': 'clerk', 'company': COMPANY, 'role': 'standard', 'expected_version': 0})
    clerk = as_user(Path(root), 'clerk')
    other = ofx([('2026-09-02', '-2.00', 'B1', 'Y')], acct='000099990001')
    with pytest.raises(BookflowError) as raised:
        clerk.run('bank-feed import', dict(account='Checking', content=other, confirm_account=True), company=COMPANY,
                  reason='wrong account?')
    assert raised.value.code == 'E_PERMISSION' and raised.value.details['capability'] == 'bank-feed.connect'
    done = clerk.run('bank-feed import', dict(account='Checking', content=ofx([('2026-09-03', '-3.00', 'C1', 'Z')])),
                     company=COMPANY, reason='next download')
    assert done['counts']['new'] == 1
    # Without ledger.read the inbox is not the clerk's to read.
    make_actor(Path(root), 'viewer')
    client.run('membership grant', {'user': 'viewer', 'company': COMPANY, 'role': 'standard', 'expected_version': 0,
                                    'denies': ['ledger.read']})
    viewer = as_user(Path(root), 'viewer')
    with pytest.raises(BookflowError) as raised:
        viewer.run('bank-line query', dict(account='Checking'), company=COMPANY)
    assert raised.value.code == 'E_PERMISSION'
