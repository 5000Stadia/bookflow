"""Transaction detail by account, and the holes in a bank account's check numbers.

Every figure asserted here is written out in the module, so a reader can add it up without
running anything: three checks and a journal entry on one bank account, one check on a
second, a five-line journal entry that has no single other side, and one check voided after
the fact.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    root = tmp_path / 'ledger-detail'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Detail organization')
    company = client.company.new(legal_name='Detail', home_currency='USD',
                                 organization='Detail organization', timezone='UTC',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    savings = client.account.create(name='Payroll Bank', type='bank', company=company)['id']
    card = client.account.create(name='Company Card', type='credit_card', company=company)['id']
    expenses = [row['id'] for row in accounts if row['type'] == 'expense'][:3]
    vendor = client.vendor.create(name='Northside Supply', company=company)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, bank=bank, savings=savings, card=card,
                vendor=vendor, first=expenses[0], second=expenses[1], third=expenses[2],
                run=run)


def check(books, *, number, amount, account=None, date='2026-02-01', lines=None, memo=None):
    return books['run']('check post', dict(
        account=account or books['bank'], date=date, number=number, amount=amount,
        pay_to=dict(name_type='vendor', name_id=books['vendor']),
        **({'memo': memo} if memo else {}),
        expenses=lines or [dict(account=books['first'], amount=amount, memo='materials')]))


def labelled(page):
    """Each row as the three things a reader follows down the page."""
    return [(row['kind'], row['display_account_label'], row['balance']['amount']) for row in page['rows']]


def paged(books, name, request, limit):
    """Every row the report produces, read one small page at a time."""
    rows, cursor, totals = [], None, None
    while True:
        page = books['run'](name, {**request, 'limit': limit, **({'cursor': cursor} if cursor else {})})
        assert page['count'] == len(page['rows'])
        totals = page['totals'] if totals is None else totals
        assert page['totals'] == totals, 'totals cover the whole filter, not the page'
        rows.extend(page['rows'])
        cursor = page['next_cursor']
        if cursor is None:
            return rows, totals


# ------------------------------------------------------------------ transaction detail


def test_sections_carry_opening_activity_and_closing_for_each_account(books):
    """One 250.00 check in January and one 100.00 in February, read over February alone."""
    check(books, number='1001', amount='250.00', date='2026-01-20')
    check(books, number='1002', amount='100.00', date='2026-02-10')
    report = books['run']('report transaction-detail', {
        'date_from': '2026-02-01', 'date_to': '2026-02-28', 'accounts': [books['bank']], 'limit': 50})
    assert labelled(report) == [
        ('opening', '1000 · Checking', '-250.00'),
        ('posting', '1000 · Checking', '-350.00'),
        ('closing', '1000 · Checking', '-350.00'),
    ]
    closing = report['rows'][-1]
    assert (closing['debit']['amount'], closing['credit']['amount']) == ('0.00', '100.00')
    assert report['totals']['opening']['minor_units'] == -25000
    assert report['totals']['period_credits']['minor_units'] == 10000
    assert report['totals']['closing']['minor_units'] == -35000
    assert report['metadata']['period'] == {'date_from': '2026-02-01', 'date_to': '2026-02-28'}


def test_posting_rows_name_the_document_the_party_and_the_split_account(books):
    check(books, number='1001', amount='40.00', memo='February materials')
    line = next(row for row in books['run']('report transaction-detail', {
        'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': [books['bank']],
        'limit': 50})['rows'] if row['kind'] == 'posting')
    assert line['date'] == '2026-02-01'
    assert line['transaction_type'] == 'journal_entry'
    # The detail report names the document, so it prints the document's own reference; the
    # 1001 written on the cheque belongs to the bank account and is read in the register and
    # in `report missing-checks`, which are the two places a chequebook is walked.
    assert line['transaction_number'] and line['transaction_number'] != '1001'
    assert line['party_name'] == 'Northside Supply'
    assert line['memo'] == 'February materials'
    assert line['split_account_id'] == books['first']
    assert books['run']('account show', {'account': books['first']})['full_name'] in line['split_account_label']
    assert line['batch_kind'] == 'original'
    assert line['posting_line_id'] and line['batch_id'] and line['revision_id']


def test_split_is_the_other_side_of_two_lines_and_named_for_many(books):
    """A two-line entry names its one other account; a four-line entry cannot."""
    simple = check(books, number='1001', amount='40.00')
    split = check(books, number='1002', amount='60.00', lines=[
        dict(account=books['first'], amount='20.00'),
        dict(account=books['second'], amount='20.00'),
        dict(account=books['third'], amount='20.00')])
    rows = books['run']('report transaction-detail', {
        'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': [books['bank']],
        'limit': 50})['rows']
    splits = {row['transaction_id']: (row['split_account_id'], row['split_account_label'])
              for row in rows if row['kind'] == 'posting'}
    assert splits[simple['id']][0] == books['first']
    assert books['run']('account show', {'account': books['first']})['full_name'] in splits[simple['id']][1]
    assert splits[split['id']] == (None, '-SPLIT-')


def test_running_balance_survives_every_page_boundary(books):
    """Four checks read three rows at a time: the balance column is one unbroken run."""
    for index, amount in enumerate(('10.00', '20.00', '30.00', '40.00')):
        check(books, number=f'20{index:02}', amount=amount, date=f'2026-03-0{index + 1}')
    request = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': [books['bank']]}
    whole = books['run']('report transaction-detail', {**request, 'limit': 200})
    walked, totals = paged(books, 'report transaction-detail', request, 3)
    assert [row['balance'] for row in walked] == [row['balance'] for row in whole['rows']]
    assert walked == whole['rows']
    assert totals == whole['totals']
    assert [row['balance']['minor_units'] for row in walked] == [0, -1000, -3000, -6000, -10000, -10000]


def test_accounts_filter_selects_a_set_and_an_empty_list_selects_everything(books):
    check(books, number='1001', amount='40.00', lines=[
        dict(account=books['first'], amount='25.00'), dict(account=books['second'], amount='15.00')])
    request = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'limit': 200}
    every = books['run']('report transaction-detail', request)
    empty = books['run']('report transaction-detail', {**request, 'accounts': []})
    # An absent list and an empty one are the same request, down to the continuation
    # fingerprint; only the moment the report was generated separates the two answers.
    assert (empty['rows'], empty['totals'], empty['next_cursor']) == (every['rows'], every['totals'], every['next_cursor'])
    assert {row['account_id'] for row in every['rows']} == {books['bank'], books['first'], books['second']}
    two = books['run']('report transaction-detail',
                       {**request, 'accounts': [books['bank'], books['second']]})
    assert {row['account_id'] for row in two['rows']} == {books['bank'], books['second']}
    # The filter narrows the totals with the rows: only what those two accounts did.
    assert two['totals']['period_debits']['minor_units'] == 1500
    assert two['totals']['period_credits']['minor_units'] == 4000
    named = books['run']('report transaction-detail', {**request, 'accounts': ['Checking']})
    assert [row['account_id'] for row in named['rows']] == [books['bank']] * 3
    with pytest.raises(BookflowError, match='E_RECORD_NOT_FOUND'):
        books['run']('report transaction-detail', {**request, 'accounts': ['No Such Account']})


def test_a_void_appears_as_the_reversal_it_is(books):
    written = check(books, number='1001', amount='40.00')
    books['run']('check void', {'check': written['id'], 'expected_version': written['version']},
                 reason='Lost in the post')
    rows = [row for row in books['run']('report transaction-detail', {
        'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': [books['bank']],
        'limit': 50})['rows'] if row['kind'] == 'posting']
    assert [row['batch_kind'] for row in rows] == ['original', 'reversal']
    assert [(row['debit']['amount'], row['credit']['amount']) for row in rows] == [
        ('0.00', '40.00'), ('40.00', '0.00')]
    assert rows[-1]['balance']['minor_units'] == 0


# ------------------------------------------------------------------ missing checks


def test_gaps_name_the_checks_on_either_side_of_them(books):
    check(books, number='1001', amount='10.00', date='2026-02-01')
    check(books, number='1002', amount='20.00', date='2026-02-02')
    check(books, number='1005', amount='30.00', date='2026-02-05')
    report = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert report['totals']['gaps'] == 1 and report['totals']['missing_numbers'] == 2
    assert report['totals']['checks_examined'] == 3 == report['totals']['numbered_checks']
    row, = report['rows']
    assert (row['kind'], row['first_missing'], row['last_missing'], row['missing_count']) == ('gap', 1003, 1004, 2)
    assert row['account_id'] == books['bank'] and row['display_account_label'] == '1000 · Checking'
    assert (row['before']['number'], row['before']['date'], row['before']['amount']['amount']) == ('1002', '2026-02-02', '20.00')
    assert row['before']['party_name'] == 'Northside Supply' and row['before']['status'] == 'posted'
    assert (row['after']['number'], row['after']['amount']['amount']) == ('1005', '30.00')
    assert row['checks'] == [] and row['duplicate_number'] is None


def test_a_voided_check_still_occupies_its_number(books):
    check(books, number='1001', amount='10.00')
    written = check(books, number='1002', amount='20.00')
    check(books, number='1003', amount='30.00')
    books['run']('check void', {'check': written['id'], 'expected_version': written['version']},
                 reason='Cheque destroyed')
    report = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert report['rows'] == [] and report['totals']['gaps'] == 0
    assert report['totals']['checks_examined'] == 3
    # And it is still readable as the used number it is, on the row either side of a hole.
    check(books, number='1006', amount='40.00')
    row, = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})['rows']
    assert (row['first_missing'], row['last_missing']) == (1004, 1005)
    assert row['before']['number'] == '1003'


def test_each_bank_account_runs_its_own_sequence(books):
    check(books, number='1001', amount='10.00')
    check(books, number='1004', amount='10.00')
    check(books, number='5001', amount='10.00', account=books['savings'])
    check(books, number='5003', amount='10.00', account=books['savings'])
    rows = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})['rows']
    assert [(row['display_account_label'], row['first_missing'], row['last_missing']) for row in rows] == [
        ('1000 · Checking', 1002, 1003), ('Payroll Bank', 5002, 5002)]
    only = books['run']('report missing-checks',
                        {'as_of': '2026-12-31', 'account': books['savings'], 'limit': 50})
    assert [row['first_missing'] for row in only['rows']] == [5002]
    assert only['totals']['checks_examined'] == 2


def test_a_number_that_is_not_a_run_of_digits_is_counted_and_not_placed(books):
    check(books, number='1001', amount='10.00')
    check(books, number='EFT-0042', amount='10.00', date='2026-02-02')
    check(books, number='1003', amount='10.00', date='2026-02-03')
    report = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert report['totals']['checks_examined'] == 3
    assert report['totals']['numbered_checks'] == 2 and report['totals']['unnumbered_checks'] == 1
    row, = report['rows']
    assert (row['first_missing'], row['last_missing']) == (1002, 1002)


def test_a_leading_zero_is_the_same_place_in_the_sequence_and_the_second_cheque_is_refused(books):
    """The duplicate is refused where it is written, not reported after the fact.

    `01001` and `1001` are one place in one chequebook, so the second is a same-account
    collision. This is the chosen policy stated as a test: refuse it, rather than warn and
    let two cheques share a number the way the anchor product does.
    """
    held = check(books, number='1001', amount='10.00', date='2026-02-01')
    with pytest.raises(BookflowError) as raised:
        check(books, number='01001', amount='20.00', date='2026-02-02')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    assert raised.value.details['held_by'] == held['id']
    report = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert report['totals']['duplicate_numbers'] == 0 and report['totals']['gaps'] == 0
    assert report['totals']['checks_examined'] == 1 and report['rows'] == []


def test_a_correction_moves_a_check_into_the_other_account_sequence(books):
    check(books, number='1001', amount='10.00')
    moved = check(books, number='1002', amount='20.00')
    check(books, number='1003', amount='30.00')
    before = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert before['rows'] == []
    books['run']('check update', {'check': moved['id'], 'expected_version': moved['version'],
                                  'account': books['savings']})
    after = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    # 1002 left Checking's chequebook carrying its number, so Checking has a number it gave
    # up rather than a cheque that went missing -- and Savings, which had none, now starts at
    # 1002 with nothing before it and so has no hole either.
    assert [(row['display_account_label'], row['kind'], row['retired_number'],
             row['first_missing']) for row in after['rows']] == [
        ('1000 · Checking', 'retired', 1002, None)]
    assert after['totals']['checks_examined'] == 3
    assert after['totals']['gaps'] == 0 and after['totals']['retired_numbers'] == 1
    assert [use['transaction_id'] for use in after['rows'][0]['checks']] == [moved['id']]


def test_a_check_dated_after_the_as_of_date_is_not_examined(books):
    check(books, number='1001', amount='10.00', date='2026-02-01')
    check(books, number='1002', amount='20.00', date='2026-09-01')
    early = books['run']('report missing-checks', {'as_of': '2026-06-30', 'limit': 50})
    assert early['totals']['checks_examined'] == 1 and early['rows'] == []
    assert books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})[
        'totals']['checks_examined'] == 2


def test_rows_page_while_the_counts_stay_whole(books):
    for number in ('1001', '1003', '1005', '1007', '1009'):
        check(books, number=number, amount='10.00')
    whole = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    walked, totals = paged(books, 'report missing-checks', {'as_of': '2026-12-31'}, 2)
    assert walked == whole['rows'] and totals == whole['totals']
    assert totals['gaps'] == 4 and totals['missing_numbers'] == 4
    assert [row['first_missing'] for row in walked] == [1002, 1004, 1006, 1008]


def test_a_card_charge_is_not_a_check_and_a_non_bank_filter_is_refused(books):
    check(books, number='1001', amount='10.00')
    books['run']('card-charge post', dict(
        account=books['card'], date='2026-02-02', amount='55.00',
        expenses=[dict(account=books['first'], amount='55.00')]))
    books['run']('journal post', dict(date='2026-02-03', number='1002', lines=[
        dict(account=books['first'], side='debit', amount='5.00'),
        dict(account=books['bank'], side='credit', amount='5.00')]))
    report = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 50})
    assert report['totals']['checks_examined'] == 1 and report['rows'] == []
    with pytest.raises(BookflowError, match='E_VALIDATION'):
        books['run']('report missing-checks', {'as_of': '2026-12-31', 'account': books['card']})
    with pytest.raises(BookflowError, match='E_RECORD_NOT_FOUND'):
        books['run']('report missing-checks', {'as_of': '2026-12-31', 'account': 'No Such Account'})


def test_a_continuation_restarts_when_the_books_move_under_it(books):
    for number in ('1001', '1003', '1005'):
        check(books, number=number, amount='10.00')
    first = books['run']('report missing-checks', {'as_of': '2026-12-31', 'limit': 1})
    check(books, number='1007', amount='10.00')
    with pytest.raises(BookflowError, match='E_QUERY_STALE'):
        books['run']('report missing-checks',
                     {'as_of': '2026-12-31', 'limit': 1, 'cursor': first['next_cursor']})


def test_transaction_detail_continuation_restarts_when_the_books_move_under_it(books):
    for index in range(3):
        check(books, number=f'100{index}', amount='10.00', date=f'2026-02-0{index + 1}')
    request = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': [books['bank']]}
    first = books['run']('report transaction-detail', {**request, 'limit': 2})
    check(books, number='1009', amount='10.00', date='2026-02-09')
    with pytest.raises(BookflowError, match='E_QUERY_STALE'):
        books['run']('report transaction-detail',
                     {**request, 'limit': 2, 'cursor': first['next_cursor']})


def test_renaming_a_selected_account_stales_the_page_it_does_not_lose_it(books):
    for index in range(3):
        check(books, number=f'100{index}', amount='10.00', date=f'2026-02-0{index + 1}')
    request = {'date_from': '2026-01-01', 'date_to': '2026-12-31', 'accounts': ['Checking'], 'limit': 2}
    first = books['run']('report transaction-detail', request)
    account = books['run']('account show', {'account': books['bank']})
    books['run']('account update', {'account': books['bank'], 'expected_version': account['version'],
                                    'name': 'Operating'})
    with pytest.raises(BookflowError, match='E_QUERY_STALE'):
        books['run']('report transaction-detail', {**request, 'cursor': first['next_cursor']})
