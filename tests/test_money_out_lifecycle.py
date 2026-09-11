"""Find, read, correct and void a check, a card charge and a transfer.

Every figure asserted here is written out in full in the module, so a reader can add it up
without running anything: a 284.60 check corrected to 300.00 and voided, a 75.25 card charge
corrected to 90.00 across two lines, and a 1,500.00 transfer redirected to another bank at
1,200.00. What the trial balance says at each step is written out beside it.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

CHECK = '284.60'
FIRST = '184.60'
SECOND = '100.00'
CORRECTED = '300.00'
CORRECTED_FIRST = '200.00'
CHARGE = '75.25'
CHARGE_CORRECTED = '90.00'
CHARGE_FUEL = '60.00'
CHARGE_OIL = '30.00'
TRANSFER = '1500.00'
TRANSFER_CORRECTED = '1200.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    root = tmp_path / 'money-out-lifecycle'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Lifecycle organization')
    company = client.company.new(legal_name='Lifecycle', home_currency='USD',
                                 organization='Lifecycle organization', timezone='UTC',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    other_bank = client.account.create(name='Payroll Bank', type='bank', company=company)['id']
    savings = client.account.create(name='Savings', type='bank', company=company)['id']
    card = client.account.create(name='Company Card', type='credit_card', company=company)['id']
    expenses = [row['id'] for row in accounts if row['type'] == 'expense'][:3]
    income = next(row['id'] for row in accounts if row['type'] == 'income')
    vendor = client.vendor.create(name='Northside Supply', company=company)['id']
    other = client.vendor.create(name='Southside Tools', company=company)['id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, bank=bank, other_bank=other_bank,
                savings=savings, card=card, vendor=vendor, other_vendor=other, income=income,
                first=expenses[0], second=expenses[1], third=expenses[2], run=run)


def _signed(books, date_to='2026-12-31'):
    """Every account's signed trial-balance position, debits positive, and the totals."""
    report = books['run']('report trial-balance', {'date_to': date_to, 'limit': 200})
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    assert report['totals']['signed_net']['minor_units'] == 0
    return ({row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
             for row in report['rows']
             if row['debit']['minor_units'] or row['credit']['minor_units']},
            report['totals']['debit']['minor_units'])


def _check(books, number='1042', **extra):
    return dict(account=books['bank'],
                pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
                date='2026-03-04', number=number, amount=CHECK, memo='March supplies',
                expenses=[{'account': books['first'], 'amount': FIRST, 'memo': 'Parts'},
                          {'account': books['second'], 'amount': SECOND, 'memo': 'Fuel'}],
                **extra)


# ---------------------------------------------------------------- the three lifecycles


def test_a_check_is_found_read_corrected_and_voided_and_the_ledger_follows_it(books):
    """Post it, find it, read it, move it by exactly the difference, and take it back out."""
    before, before_total = _signed(books)
    assert (before, before_total) == ({}, 0)

    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')

    # 284.60 out of the bank; 184.60 and 100.00 into the two expense accounts.
    after_post, post_total = _signed(books)
    assert after_post == {books['bank']: -28460, books['first']: 18460, books['second']: 10000}
    assert post_total == 28460

    # Found by the filters a bookkeeper actually has: the bank it was drawn on and the payee.
    page = books['run']('check query', {'account': books['bank'], 'payee': books['vendor'],
                                        'limit': 10})
    assert page['count'] == 1 and page['has_more'] is False and page['next_cursor'] is None
    assert page['items'][0]['id'] == posted['id']
    assert page['items'][0]['number'] == '1042'
    assert page['items'][0]['document']['amount']['amount'] == CHECK
    assert page['items'][0]['document']['expense_lines'] == 2

    # Read back by its own number, with the figures its own footer showed when it was written.
    shown = books['run']('check show', {'check': '1042'})
    assert shown['id'] == posted['id'] and shown['status'] == 'posted'
    assert shown['document'] == {
        'kind': 'check', 'account_id': books['bank'], 'funding': 'bank', 'currency': 'USD',
        'amount': {'amount': CHECK, 'currency': 'USD', 'minor_units': 28460},
        'expense_total': {'amount': CHECK, 'currency': 'USD', 'minor_units': 28460},
        'expense_lines': 2}
    assert shown['revision']['lines'][0]['party_name'] == 'Northside Supply'
    assert [line['description'] for line in shown['revision']['lines']] == \
           ['March supplies', 'Parts', 'Fuel']

    # The parts line was understated by 15.40; correct the check to 300.00.
    lines = shown['revision']['lines']
    corrected = books['run']('check update', dict(
        check=posted['id'], expected_version=posted['version'], amount=CORRECTED,
        expenses=[{'line_id': lines[1]['line_id'], 'account': books['first'],
                   'amount': CORRECTED_FIRST, 'memo': 'Parts'},
                  {'line_id': lines[2]['line_id'], 'account': books['second'],
                   'amount': SECOND, 'memo': 'Fuel'}]),
        reason='Parts line was understated')
    assert corrected['version'] == 2
    assert corrected['document']['amount']['amount'] == CORRECTED

    after_update, update_total = _signed(books)
    assert after_update == {books['bank']: -30000, books['first']: 20000, books['second']: 10000}
    assert update_total == 30000
    # The ledger moved by exactly the difference and by nothing else: 200.00 - 184.60 = 15.40.
    assert {account: after_update[account] - after_post.get(account, 0) for account in after_update} == \
           {books['bank']: -1540, books['first']: 1540, books['second']: 0}

    voided = books['run']('check void', {'check': posted['id'],
                                         'expected_version': corrected['version']},
                          reason='Cheque was never cashed')
    assert voided['status'] == 'voided' and voided['version'] == 3
    # Both batches sit on the check's own date: voiding never moves an earlier period.
    assert [(batch['kind'], batch['effective_date'], batch['total']['amount'])
            for batch in voided['revision']['batches']] == \
           [('replacement', '2026-03-04', CORRECTED), ('reversal', '2026-03-04', CORRECTED)]

    after_void, void_total = _signed(books)
    assert (after_void, void_total) == (before, before_total)

    history = books['run']('check history', {'check': posted['id'], 'limit': 10})
    assert history['status'] == 'voided' and history['count'] == 2
    assert [(item['revision_number'], item['document']['amount']['amount'],
             [batch['kind'] for batch in item['batches']]) for item in history['items']] == \
           [(1, CHECK, ['original', 'reversal']), (2, CORRECTED, ['replacement', 'reversal'])]

    # The first revision is still readable in full, with the figures it was written for.
    original = books['run']('check show', {'check': posted['id'], 'revision_number': 1})
    assert original['document']['amount']['amount'] == CHECK
    assert original['revision']['lines'][1]['amount']['amount'] == FIRST


def test_a_card_charge_is_found_read_corrected_and_voided(books):
    before, _ = _signed(books)
    posted = books['run']('card-charge post', dict(
        account=books['card'], pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
        date='2026-03-05', amount=CHARGE, memo='Fuel on the company card',
        expenses=[{'account': books['second'], 'amount': CHARGE}]),
        reason='Record a card purchase')

    after_post, post_total = _signed(books)
    assert after_post == {books['card']: -7525, books['second']: 7525}
    assert post_total == 7525

    page = books['run']('card-charge query', {'account': books['card'], 'limit': 10})
    assert page['count'] == 1 and page['items'][0]['document']['funding'] == 'credit_card'
    shown = books['run']('card-charge show', {'card_charge': posted['id']})
    assert shown['document']['kind'] == 'card_charge'
    assert shown['document']['amount']['minor_units'] == 7525

    # The receipt was really 90.00 and covered oil as well as fuel.
    corrected = books['run']('card-charge update', dict(
        card_charge=posted['id'], expected_version=posted['version'], amount=CHARGE_CORRECTED,
        expenses=[{'line_id': shown['revision']['lines'][1]['line_id'],
                   'account': books['second'], 'amount': CHARGE_FUEL},
                  {'account': books['third'], 'amount': CHARGE_OIL, 'memo': 'Oil'}]),
        reason='Split the fuel receipt')
    assert corrected['version'] == 2 and corrected['document']['expense_lines'] == 2

    after_update, update_total = _signed(books)
    # 60.00 fuel + 30.00 oil = 90.00 owed on the card; fuel fell 15.25 and oil is new.
    assert after_update == {books['card']: -9000, books['second']: 6000, books['third']: 3000}
    assert update_total == 9000
    assert after_update[books['second']] - after_post[books['second']] == -1525
    assert after_update[books['card']] - after_post[books['card']] == -1475

    voided = books['run']('card-charge void', {'card_charge': posted['id'],
                                               'expected_version': corrected['version']},
                          reason='The vendor reversed the charge')
    assert voided['status'] == 'voided'
    assert [(batch['kind'], batch['effective_date']) for batch in voided['revision']['batches']] == \
           [('replacement', '2026-03-05'), ('reversal', '2026-03-05')]
    assert _signed(books)[0] == before

    history = books['run']('card-charge history', {'card_charge': posted['id'], 'limit': 10})
    assert [(item['revision_number'], item['document']['amount']['amount'], item['line_count'])
            for item in history['items']] == [(1, CHARGE, 2), (2, CHARGE_CORRECTED, 3)]


def test_redirecting_a_transfer_moves_the_money_out_of_one_account_and_into_the_other(books):
    """The interesting correction: a new destination has to empty the old one at the same date."""
    before, _ = _signed(books)
    posted = books['run']('transfer post', dict(
        from_account=books['bank'], to_account=books['savings'], date='2026-03-06',
        amount=TRANSFER, memo='Move to savings'), reason='Fund savings')
    assert posted['document']['from_account']['effect'] == 'decrease'
    assert posted['document']['to_account']['effect'] == 'increase'

    after_post, post_total = _signed(books)
    assert after_post == {books['bank']: -150000, books['savings']: 150000}
    assert post_total == 150000

    page = books['run']('transfer query', {'account': books['savings'], 'limit': 10})
    assert page['count'] == 1 and page['items'][0]['document']['amount']['amount'] == TRANSFER
    assert books['run']('transfer query', {'from_account': books['savings'],
                                           'limit': 10})['count'] == 0

    shown = books['run']('transfer show', {'transfer': posted['id']})
    assert shown['document']['from_account']['account_id'] == books['bank']
    assert shown['document']['to_account']['name'] == 'Savings'

    # It should have gone to the payroll bank, and for 1,200.00.
    corrected = books['run']('transfer update', dict(
        transfer=posted['id'], expected_version=posted['version'],
        to_account=books['other_bank'], amount=TRANSFER_CORRECTED),
        reason='Wrong destination account')
    assert corrected['version'] == 2
    assert corrected['document']['to_account']['account_id'] == books['other_bank']

    after_update, update_total = _signed(books)
    # Savings is emptied at the transfer's own date, payroll takes 1,200.00, and the 300.00
    # difference goes back to the account it came out of.
    assert after_update == {books['bank']: -120000, books['other_bank']: 120000}
    assert update_total == 120000
    assert books['savings'] not in after_update
    assert after_update[books['bank']] - after_post[books['bank']] == 30000

    # One correction, both moves, on one date.
    assert {(batch['kind'], batch['effective_date'])
            for batch in corrected['revision']['batches']} == {('replacement', '2026-03-06')}
    dated = books['run']('report trial-balance', {'date_to': '2026-03-05', 'limit': 200})
    assert dated['totals']['debit']['minor_units'] == 0

    voided = books['run']('transfer void', {'transfer': posted['id'],
                                            'expected_version': corrected['version']},
                          reason='The transfer was never made')
    assert voided['status'] == 'voided'
    assert _signed(books)[0] == before

    history = books['run']('transfer history', {'transfer': posted['id'], 'limit': 10})
    assert [(item['revision_number'], item['document']['to_account']['name'],
             item['document']['amount']['amount']) for item in history['items']] == \
           [(1, 'Savings', TRANSFER), (2, 'Payroll Bank', TRANSFER_CORRECTED)]


# ---------------------------------------------------------------- what each noun will answer for


def test_each_noun_answers_only_for_its_own_documents(books):
    check = books['run']('check post', _check(books), reason='Pay Northside Supply')
    charge = books['run']('card-charge post', dict(
        account=books['card'], date='2026-03-05', amount=CHARGE,
        expenses=[{'account': books['second'], 'amount': CHARGE}]), reason='Card purchase')
    transfer = books['run']('transfer post', dict(
        from_account=books['bank'], to_account=books['savings'], date='2026-03-06',
        amount=TRANSFER), reason='Fund savings')
    # A bank payment typed straight into the register, and a hand-written journal entry with
    # the same shape as the check. Neither was entered as a document and neither is one.
    split = books['run']('register post', dict(
        account=books['bank'], date='2026-03-07', amount=SECOND, direction='decrease',
        allocations=[{'account': books['first'], 'amount': SECOND}]), reason='By hand')
    entry = books['run']('journal post', dict(date='2026-03-08', lines=[
        {'account': books['bank'], 'side': 'credit', 'amount': SECOND},
        {'account': books['first'], 'side': 'debit', 'amount': SECOND}]), reason='By hand')

    assert [item['id'] for item in books['run']('check query', {'limit': 20})['items']] == [check['id']]
    assert [item['id'] for item in books['run']('card-charge query', {'limit': 20})['items']] == [charge['id']]
    assert [item['id'] for item in books['run']('transfer query', {'limit': 20})['items']] == [transfer['id']]
    # All five are still journal entries and the journal still lists every one of them.
    assert {item['id'] for item in books['run']('journal query', {'limit': 20})['items']} == \
           {check['id'], charge['id'], transfer['id'], split['id'], entry['id']}

    for noun, field, wrong in (('check', 'check', charge['id']),
                               ('card-charge', 'card_charge', check['id']),
                               ('transfer', 'transfer', check['id']),
                               ('check', 'check', split['id']),
                               ('check', 'check', entry['id'])):
        with pytest.raises(BookflowError) as raised:
            books['run'](noun + ' show', {field: wrong})
        assert raised.value.code == 'E_RECORD_NOT_FOUND'


def test_a_check_moves_to_another_bank_account_but_never_onto_a_card(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    moved = books['run']('check update', {'check': posted['id'],
                                          'expected_version': posted['version'],
                                          'account': books['other_bank']},
                         reason='Written on the payroll account')
    assert moved['document']['account_id'] == books['other_bank']
    balances, _ = _signed(books)
    assert balances == {books['other_bank']: -28460, books['first']: 18460, books['second']: 10000}
    # The lines that were not mentioned are exactly the lines that were captured.
    assert [line['amount']['amount'] for line in moved['revision']['lines']] == \
           [CHECK, FIRST, SECOND]

    with pytest.raises(BookflowError) as raised:
        books['run']('check update', {'check': posted['id'],
                                      'expected_version': moved['version'],
                                      'account': books['card']}, reason='Wrong account')
    assert raised.value.code == 'E_VALIDATION'
    assert 'bank account' in raised.value.details['fields'][0]['problem']


def test_a_transfer_cannot_be_corrected_onto_an_account_a_transfer_may_not_touch(books):
    posted = books['run']('transfer post', dict(
        from_account=books['bank'], to_account=books['savings'], date='2026-03-06',
        amount=TRANSFER), reason='Fund savings')
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer update', {'transfer': posted['id'],
                                         'expected_version': posted['version'],
                                         'to_account': books['income']}, reason='Wrong end')
    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'to_account'
    assert 'income account' in raised.value.details['fields'][0]['problem']

    with pytest.raises(BookflowError) as raised:
        books['run']('transfer update', {'transfer': posted['id'],
                                         'expected_version': posted['version'],
                                         'to_account': books['bank']}, reason='Same account')
    assert raised.value.code == 'E_VALIDATION'
    assert 'both ends' in raised.value.message


# ---------------------------------------------------------------- the guards on a correction


def test_a_correction_whose_lines_do_not_add_up_is_refused_with_the_difference(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    with pytest.raises(BookflowError) as raised:
        books['run']('check update', {'check': posted['id'],
                                      'expected_version': posted['version'],
                                      'amount': CORRECTED}, reason='Only the amount')
    assert raised.value.code == 'E_UNBALANCED_ENTRY'
    assert raised.value.details['difference'] == {'amount': '15.40', 'currency': 'USD',
                                                  'minor_units': 1540}
    assert _signed(books)[1] == 28460


def test_a_stale_expected_version_says_who_changed_it_and_what(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    books['run']('check update', {'check': posted['id'], 'expected_version': posted['version'],
                                  'memo': 'March supplies, revised'}, reason='Memo')
    with pytest.raises(BookflowError) as raised:
        books['run']('check update', {'check': posted['id'],
                                      'expected_version': posted['version'],
                                      'memo': 'Something else'}, reason='Stale')
    assert raised.value.code == 'E_VERSION_CONFLICT'
    assert raised.value.details['current_version'] == 2
    assert raised.value.details['changed_fields'] == ['journal']
    assert 'expected_version 2' in raised.value.message

    with pytest.raises(BookflowError) as raised:
        books['run']('check void', {'check': posted['id'], 'expected_version': 1},
                     reason='Stale void')
    assert raised.value.code == 'E_VERSION_CONFLICT'


def test_a_voided_document_cannot_be_corrected_and_voiding_twice_changes_nothing(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    voided = books['run']('check void', {'check': posted['id'],
                                         'expected_version': posted['version']},
                          reason='Lost in the post')
    with pytest.raises(BookflowError) as raised:
        books['run']('check update', {'check': posted['id'],
                                      'expected_version': voided['version'],
                                      'memo': 'Too late'}, reason='Too late')
    assert raised.value.code == 'E_VALIDATION'
    assert 'voided' in raised.value.details['fields'][0]['problem']

    again = books['run']('check void', {'check': posted['id'],
                                        'expected_version': voided['version']},
                         reason='Lost in the post')
    assert again['changed'] is False and again['version'] == voided['version']
    assert _signed(books)[1] == 0


def test_voiding_requires_a_reason_and_a_closed_period_refuses_both_verbs(books):
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    with pytest.raises(BookflowError) as raised:
        books['run']('check void', {'check': posted['id']})
    assert raised.value.code == 'E_REASON_REQUIRED'

    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close March')
    for name, raw in (('check update', {'check': posted['id'], 'memo': 'After close'}),
                      ('check void', {'check': posted['id']})):
        with pytest.raises(BookflowError) as raised:
            books['run'](name, raw, reason='After the closing date')
        assert raised.value.code == 'E_PERIOD_CLOSED'
        assert raised.value.details['closing_date'] == '2026-03-31'


def test_correcting_a_check_in_the_register_keeps_it_a_check(books):
    """The register and the document are two doors into one entry, not two entries."""
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    lines = posted['revision']['lines']
    books['run']('register update', dict(
        journal=posted['id'], expected_version=posted['version'],
        selected_line_id=lines[0]['line_id'], account=books['bank'], date='2026-03-04',
        number='1042', memo='March supplies',
        payee={'name_type': 'vendor', 'name_id': books['vendor']},
        direction='decrease', amount=CORRECTED,
        allocations=[{'line_id': lines[1]['line_id'], 'account': books['first'],
                      'amount': CORRECTED_FIRST, 'memo': 'Parts'},
                     {'line_id': lines[2]['line_id'], 'account': books['second'],
                      'amount': SECOND, 'memo': 'Fuel'}]),
        reason='Corrected in the register')
    shown = books['run']('check show', {'check': posted['id']})
    assert shown['version'] == 2
    assert shown['document']['amount']['amount'] == CORRECTED
    assert [item['id'] for item in books['run']('check query', {'limit': 10})['items']] == \
           [posted['id']]


# ---------------------------------------------------------------- paging


def test_a_check_page_carries_its_cursor_and_restarts_when_the_books_move(books):
    for number in ('1001', '1002', '1003'):
        books['run']('check post', _check(books, number=number), reason='Pay Northside Supply')
    first = books['run']('check query', {'limit': 2})
    assert first['count'] == 2 and first['has_more'] is True and first['next_cursor']
    second = books['run']('check query', {'limit': 2, 'cursor': first['next_cursor']})
    assert second['count'] == 1 and second['has_more'] is False and second['next_cursor'] is None
    assert [item['number'] for item in first['items'] + second['items']] == ['1001', '1002', '1003']

    newest = books['run']('check query', {'limit': 3, 'direction': 'desc'})
    assert [item['number'] for item in newest['items']] == ['1003', '1002', '1001']

    # A cursor belongs to the contract that minted it.
    with pytest.raises(BookflowError) as raised:
        books['run']('check query', {'limit': 2, 'direction': 'desc',
                                     'cursor': first['next_cursor']})
    assert raised.value.code == 'E_VALIDATION'

    # And to the books as they stood when it was minted.
    books['run']('check post', _check(books, number='1004'), reason='Pay Northside Supply')
    with pytest.raises(BookflowError) as raised:
        books['run']('check query', {'limit': 2, 'cursor': first['next_cursor']})
    assert raised.value.code == 'E_QUERY_STALE'


def test_the_query_filters_each_narrow_the_page_they_say_they_do(books):
    books['run']('check post', _check(books, number='2001'), reason='Pay Northside Supply')
    books['run']('check post', dict(
        account=books['other_bank'], pay_to={'name_type': 'vendor', 'name_id': books['other_vendor']},
        date='2026-04-04', number='2002', amount=SECOND, memo='April tools',
        expenses=[{'account': books['first'], 'amount': SECOND}]), reason='Pay Southside Tools')
    query = lambda **raw: [item['number'] for item in
                           books['run']('check query', dict(limit=20, **raw))['items']]
    assert query() == ['2001', '2002']
    assert query(account=books['other_bank']) == ['2002']
    assert query(payee=books['vendor']) == ['2001']
    assert query(date_from='2026-04-01') == ['2002']
    assert query(date_to='2026-03-31') == ['2001']
    assert query(number='2001') == ['2001']
    assert query(query='April') == ['2002']
    assert query(status='voided') == []


# ---------------------------------------------------------------- what stopped being a document


def test_an_entry_rearranged_in_the_journal_editor_says_so_rather_than_reporting_wrong_figures(books):
    """A check whose lines were turned around is still good accounting. It is not a check."""
    posted = books['run']('check post', _check(books), reason='Pay Northside Supply')
    books['run']('journal update', dict(journal=posted['id'], expected_version=posted['version'],
                                        lines=[{'account': books['bank'], 'side': 'debit',
                                                'amount': SECOND},
                                               {'account': books['first'], 'side': 'credit',
                                                'amount': SECOND}]),
                 reason='Turned it into a deposit by hand')

    with pytest.raises(BookflowError) as raised:
        books['run']('check show', {'check': posted['id']})
    assert raised.value.code == 'E_VALIDATION'
    assert 'money no longer leaves' in raised.value.details['fields'][0]['problem']
    assert raised.value.details['open_journal'] == {'journal': posted['id'],
                                                    'command': 'journal show'}
    # Nothing is lost: the entry is still there and the journal still opens it.
    assert books['run']('journal show', {'journal': posted['id']})['version'] == 2
    assert books['run']('check query', {'limit': 10})['count'] == 0
    assert _signed(books)[1] == 10000


def test_a_transfer_that_grew_a_third_line_is_no_longer_a_transfer(books):
    posted = books['run']('transfer post', dict(
        from_account=books['bank'], to_account=books['savings'], date='2026-03-06',
        amount=TRANSFER), reason='Fund savings')
    books['run']('journal update', dict(
        journal=posted['id'], expected_version=posted['version'],
        lines=[{'account': books['bank'], 'side': 'credit', 'amount': TRANSFER},
               {'account': books['savings'], 'side': 'debit', 'amount': TRANSFER_CORRECTED},
               {'account': books['other_bank'], 'side': 'debit', 'amount': CORRECTED}]),
        reason='Split it by hand')
    with pytest.raises(BookflowError) as raised:
        books['run']('transfer show', {'transfer': posted['id']})
    assert raised.value.code == 'E_VALIDATION'
    assert 'exactly two lines and this has 3' in raised.value.details['fields'][0]['problem']
    assert books['run']('transfer query', {'limit': 10})['count'] == 0
    assert books['run']('journal show', {'journal': posted['id']})['revision']['line_count'] == 3


def test_the_stored_kinds_are_exactly_what_the_table_allows():
    """One spelling for each document, in the marker table and in the code that writes it."""
    from bookflow.company.money_out import KIND
    from bookflow.company.money_out_schema import KINDS

    assert sorted(KIND.values()) == sorted(KINDS)
    assert sorted(KIND) == ['card-charge', 'check', 'transfer']
