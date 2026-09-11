"""A check number belongs to one bank account's chequebook, not to the document series.

Every number written here is spelled out in the module so a reader can follow the chequebook
without running anything: two accounts both issuing 1001, an explicit 500 below the pointer,
the collision that is refused, the retry that is replayed, the void that keeps its number,
the two corrections, and what the register, the document and the report each say afterwards.
"""
import pytest

import bookflow
from bookflow.core.errors import BookflowError

AMOUNT = '120.00'


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every number counted here is only this test's."""
    root = tmp_path / 'cheques'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Chequebook organization')
    company = client.company.new(legal_name='Chequebook', home_currency='USD',
                                 organization='Chequebook organization', timezone='UTC',
                                 chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    first = next(row['id'] for row in accounts if row['type'] == 'bank')
    second = client.account.create(name='Payroll Bank', type='bank', company=company)['id']
    expense = next(row['id'] for row in accounts if row['type'] == 'expense')
    vendor = client.vendor.create(name='Northside Supply', company=company)['id']
    other = client.vendor.create(name='Corner Hardware', company=company)['id']
    # Keyed by kind, because a cheque is a cheque by its kind and not by what it is called.
    methods = {row['kind']: row['id'] for row in
               client.run('payment-method query', dict(limit=50), company=company)['items']}

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    return dict(client=client, company=company, first=first, second=second,
                expense=expense, vendor=vendor, other=other, methods=methods, run=run)


def _check(books, **extra):
    body = dict(account=books['first'],
                pay_to={'name_type': 'vendor', 'name_id': books['vendor']},
                date='2026-03-04', amount=AMOUNT, memo='Supplies',
                expenses=[{'account': books['expense'], 'amount': AMOUNT}])
    body.update(extra)
    return body


def _write(books, **extra):
    return books['run']('check post', _check(books, **extra), reason='Pay Northside Supply')


def _bill(books, vendor=None, amount=AMOUNT):
    return books['run']('bill post', dict(
        vendor=vendor or books['vendor'], date='2026-03-03',
        expenses=[{'account': books['expense'], 'amount': amount}]), reason='Enter a bill')


def _pay(books, *entered, **extra):
    """``bill pay`` with the cheque method, which is what makes it a cheque."""
    values = dict(date='2026-03-04', bills=[{'bill': bill['id']} for bill in entered],
                  funding_account=books['first'], method=books['methods']['check'])
    values.update(extra)
    return books['run']('bill pay', values, reason='Pay the vendor')


def _report(books, **filters):
    return books['run']('report missing-checks', {'as_of': '2026-12-31', **filters})


def _findings(report):
    return [(row['kind'], row['first_missing'], row['last_missing'], row['duplicate_number'],
             row['retired_number']) for row in report['rows']]


# ---------------------------------------------------------------- the two numbers


def test_the_cheque_number_and_the_document_reference_are_two_different_numbers(books):
    """1001 is on the paper; the journal it posts as keeps its own reference."""
    written = _write(books, number='1001')

    assert written['document']['check_number'] == '1001'
    assert written['number'] != '1001'
    # And the journal series is untouched by the cheque: a hand-typed entry takes the next
    # document reference, not the next cheque number.
    journal = books['run']('journal post', {
        'date': '2026-03-05', 'lines': [
            {'account': books['expense'], 'side': 'debit', 'amount': '5.00'},
            {'account': books['first'], 'side': 'credit', 'amount': '5.00'}]},
        reason='Bank charge')
    assert journal['number'] != '1002'
    assert int(journal['number']) == int(written['number']) + 1


def test_two_bank_accounts_both_issue_1001_and_neither_is_the_other(books):
    first = _write(books, number='1001')
    second = _write(books, account=books['second'], number='1001')

    assert first['document']['check_number'] == second['document']['check_number'] == '1001'
    assert first['id'] != second['id']
    # Each account's report sees exactly one 1001 and calls it no kind of finding at all.
    assert _findings(_report(books, account=books['first'])) == []
    assert _findings(_report(books, account=books['second'])) == []
    assert _report(books)['totals']['duplicate_numbers'] == 0

    # And 1001 on its own no longer names one cheque, so nothing picks one.
    with pytest.raises(BookflowError) as raised:
        books['run']('check show', {'check': '1001'})
    assert raised.value.code == 'E_VALIDATION'
    named = {row['transaction_id'] for row in raised.value.details['candidates']}
    assert named == {first['id'], second['id']}
    accounts = {row['account_id'] for row in raised.value.details['candidates']}
    assert accounts == {books['first'], books['second']}

    # Both are still reachable by the one thing that does identify them.
    assert books['run']('check show', {'check': first['id']})['document']['check_number'] == '1001'
    assert books['run']('check show', {'check': second['id']})['document']['check_number'] == '1001'


# ---------------------------------------------------------------- allocation


def test_an_unnumbered_cheque_takes_the_accounts_own_next_number_and_moves_it_on(books):
    books['client'].account.update(account=books['first'], next_check_number='4010',
                                   company=books['company'])

    assert _write(books)['document']['check_number'] == '4010'
    assert _write(books)['document']['check_number'] == '4011'
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '4012'
    # The other account's chequebook has not moved, because it is another chequebook.
    assert books['client'].account.show(
        account=books['second'], company=books['company'])['next_check_number'] is None


def test_allocation_skips_a_number_that_is_already_on_a_cheque(books):
    books['client'].account.update(account=books['first'], next_check_number='7000',
                                   company=books['company'])
    # Typing 7001 moves the pointer past it, which is what the anchor product does too.
    _write(books, number='7001')
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '7002'

    # Put the pointer back at the start of the book by hand, as a person would after
    # finding an older pad, and allocation walks past what is already written.
    books['client'].account.update(account=books['first'], next_check_number='7000',
                                   company=books['company'])
    assert _write(books)['document']['check_number'] == '7000'
    assert _write(books)['document']['check_number'] == '7002'


def test_an_explicit_number_below_the_pointer_is_valid_and_does_not_move_it_back(books):
    """Below the pointer is not the same thing as duplicate: it is an older cheque."""
    books['client'].account.update(account=books['first'], next_check_number='2000',
                                   company=books['company'])
    low = _write(books, number='500')

    assert low['document']['check_number'] == '500'
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '2000'
    assert _write(books)['document']['check_number'] == '2000'


def test_an_explicit_number_at_or_above_the_pointer_moves_it_forward(books):
    books['client'].account.update(account=books['first'], next_check_number='300',
                                   company=books['company'])
    _write(books, number='900')

    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '901'


def test_a_pointer_written_with_leading_zeros_keeps_its_width(books):
    books['client'].account.update(account=books['first'], next_check_number='000105',
                                   company=books['company'])

    assert _write(books)['document']['check_number'] == '000105'
    assert _write(books)['document']['check_number'] == '000106'
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '000107'


def test_a_pointer_that_is_not_a_run_of_digits_refuses_rather_than_inventing_a_number(books):
    books['client'].account.update(account=books['first'], next_check_number='EFT',
                                   company=books['company'])

    with pytest.raises(BookflowError) as raised:
        _write(books)
    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'number'
    # Typing the number on the cheque still works, and does not disturb what was set.
    assert _write(books, number='88')['document']['check_number'] == '88'
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == 'EFT'


def test_a_number_that_is_not_digits_is_a_real_cheque_with_no_place_in_the_sequence(books):
    _write(books, number='1001')
    _write(books, number='EFT-7')
    _write(books, number='1003')

    totals = _report(books, account=books['first'])['totals']
    assert totals['checks_examined'] == 3
    assert (totals['numbered_checks'], totals['unnumbered_checks']) == (2, 1)
    # 1002 is the only hole; EFT-7 sits between no two numbers and makes none.
    assert _findings(_report(books, account=books['first'])) == [('gap', 1002, 1002, None, None)]


# ---------------------------------------------------------------- the duplicate policy


def test_the_same_number_on_the_same_account_is_refused_and_says_which_cheque_has_it(books):
    """The chosen policy, stated: refuse the collision. The anchor product warns instead."""
    held = _write(books, number='1001')

    with pytest.raises(BookflowError) as raised:
        _write(books, number='1001')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    assert raised.value.details['held_by'] == held['id']
    assert raised.value.details['account_id'] == books['first']
    # Nothing was written: the refusal is not a warning that later lets the entry through.
    assert books['run']('check query', {'account': books['first'], 'limit': 10})['count'] == 1


def test_01001_and_1001_are_one_number_and_the_second_is_refused(books):
    _write(books, number='1001')

    with pytest.raises(BookflowError) as raised:
        _write(books, number='01001')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'


# ---------------------------------------------------------------- retry, void, correction


def test_a_retry_under_one_key_keeps_the_number_the_first_attempt_allocated(books):
    books['client'].account.update(account=books['first'], next_check_number='6000',
                                   company=books['company'])
    first = books['run']('check post', _check(books), reason='Pay', idempotency_key='one')
    again = books['run']('check post', _check(books), reason='Pay', idempotency_key='one')

    assert first['document']['check_number'] == '6000'
    assert again['id'] == first['id'] and again['idempotent_replay']
    assert again['document']['check_number'] == '6000'
    # One cheque was written, and the chequebook moved on by one.
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '6001'
    assert books['run']('check query', {'account': books['first'], 'limit': 10})['count'] == 1


def test_a_voided_cheque_keeps_its_number_and_is_neither_a_gap_nor_reusable(books):
    _write(books, number='1001')
    lost = _write(books, number='1002')
    _write(books, number='1003')
    books['run']('check void', {'check': lost['id']}, reason='Cheque was lost')

    assert _findings(_report(books, account=books['first'])) == []
    with pytest.raises(BookflowError) as raised:
        _write(books, number='1002')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    # And the voided cheque still reads back with the number it was written with.
    shown = books['run']('check show', {'check': lost['id']})
    assert shown['status'] == 'voided' and shown['document']['check_number'] == '1002'


def test_correcting_the_number_retires_the_old_one_rather_than_leaving_a_hole(books):
    written = _write(books, number='1001')
    _write(books, number='1002')
    corrected = books['run']('check update', {'check': written['id'], 'number': '1005'},
                             reason='Wrong cheque number typed')

    assert corrected['document']['check_number'] == '1005'
    findings = _findings(_report(books, account=books['first']))
    # 1001 is accounted for -- somebody wrote it down and took it back -- so it is retired
    # rather than named as a cheque that went missing. 1003 and 1004 really are holes.
    assert findings == [('retired', None, None, None, 1001),
                        ('gap', 1003, 1004, None, None)]
    assert _report(books, account=books['first'])['totals']['retired_numbers'] == 1
    retired = _report(books, account=books['first'])['rows'][0]
    assert [use['transaction_id'] for use in retired['checks']] == [written['id']]
    assert retired['checks'][0]['number'] == '1005'

    # The retired number is not returned to the allocator.
    books['client'].account.update(account=books['first'], next_check_number='1001',
                                   company=books['company'])
    assert _write(books)['document']['check_number'] == '1003'

    # But a person looking at the paper may still type it deliberately.
    assert _write(books, number='1001')['document']['check_number'] == '1001'


def test_an_earlier_revision_still_reads_back_with_the_number_it_was_written_with(books):
    written = _write(books, number='1001')
    books['run']('check update', {'check': written['id'], 'number': '1009'},
                 reason='Wrong cheque number typed')

    original = books['run']('check show', {'check': written['id'], 'revision_number': 1})
    current = books['run']('check show', {'check': written['id']})
    assert original['document']['check_number'] == '1001'
    assert current['document']['check_number'] == '1009'
    history = books['run']('check history', {'check': written['id'], 'limit': 10})
    assert [item['document']['check_number'] for item in history['items']] == ['1001', '1009']


def test_correcting_the_account_moves_the_cheque_to_the_other_chequebook_keeping_its_number(books):
    books['client'].account.update(account=books['second'], next_check_number='2002',
                                   company=books['company'])
    written = _write(books, number='1001')
    _write(books, account=books['second'], number='2001')
    moved = books['run']('check update', {'check': written['id'], 'account': books['second']},
                         reason='Written on the wrong chequebook')

    assert moved['document']['check_number'] == '1001'
    assert moved['document']['account_id'] == books['second']
    assert _report(books, account=books['first'])['totals']['checks_examined'] == 0
    second = _report(books, account=books['second'])
    assert second['totals']['checks_examined'] == 2
    # 1002 through 2000 are holes in the account it moved to; the account it left has the
    # number it gave up recorded as retired rather than as a missing cheque.
    assert ('gap', 1002, 2000, None, None) in _findings(second)
    assert _findings(_report(books, account=books['first'])) == [
        ('retired', None, None, None, 1001)]
    # Carrying a number into another chequebook moves no pointer: nobody issued or typed it
    # on this call, and jumping the receiving account's book would skip the numbers between.
    assert books['client'].account.show(
        account=books['second'], company=books['company'])['next_check_number'] == '2002'


def test_a_correction_onto_an_account_that_already_has_the_number_is_refused(books):
    written = _write(books, number='1001')
    held = _write(books, account=books['second'], number='1001')

    with pytest.raises(BookflowError) as raised:
        books['run']('check update', {'check': written['id'], 'account': books['second']},
                     reason='Written on the wrong chequebook')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    assert raised.value.details['held_by'] == held['id']
    assert books['run']('check show', {'check': written['id']})['document']['account_id'] == books['first']


def test_the_journal_editor_carries_the_cheque_identity_forward_untouched(books):
    """Editing the entry is not editing the paper: the number and the chequebook survive."""
    written = _write(books, number='1001')
    selected = written['revision']['lines'][0]['line_id']
    corrected = books['run']('register update', dict(
        journal=written['id'], expected_version=written['version'], selected_line_id=selected,
        account=books['first'], date='2026-03-04', memo='Supplies',
        payee={'name_type': 'vendor', 'name_id': books['vendor']},
        direction='decrease', amount='200.00',
        allocations=[{'line_id': written['revision']['lines'][1]['line_id'],
                      'account': books['expense'], 'amount': '200.00'}]),
        reason='Corrected the amount')

    assert corrected['version'] == 2
    assert books['run']('check show', {'check': written['id']})['document']['check_number'] == '1001'
    assert _report(books, account=books['first'])['totals']['checks_examined'] == 1


# ---------------------------------------------------------------- everything agreeing


def test_the_register_the_document_and_the_report_all_show_one_number(books):
    written = _write(books, number='1001')
    _write(books, number='1003')

    shown = books['run']('check show', {'check': written['id']})
    listed = books['run']('check query', {'account': books['first'], 'number': '1001', 'limit': 5})
    register = books['run']('register query', {'account': books['first'],
                                               'date_from': '2026-01-01', 'date_to': '2026-12-31'})
    postings = [row for row in register['rows'] if row['kind'] == 'posting']
    report = _report(books, account=books['first'])

    assert shown['document']['check_number'] == '1001'
    assert listed['count'] == 1 and listed['items'][0]['id'] == written['id']
    assert {row['check_number'] for row in postings} == {'1001', '1003'}
    # The register's own document reference is still there beside it, and is not the cheque's.
    assert all(row['transaction_number'] != row['check_number'] for row in postings)
    gap = report['rows'][0]
    assert (gap['kind'], gap['first_missing'], gap['last_missing']) == ('gap', 1002, 1002)
    assert gap['before']['number'] == '1001' and gap['after']['number'] == '1003'
    assert gap['before']['transaction_id'] == written['id']


def test_a_card_charge_and_a_transfer_never_consume_a_cheque_number(books):
    books['client'].account.update(account=books['first'], next_check_number='1001',
                                   company=books['company'])
    card = books['client'].account.create(name='Company Card', type='credit_card',
                                          company=books['company'])['id']
    books['run']('card-charge post', {
        'account': card, 'date': '2026-03-04', 'amount': AMOUNT,
        'expenses': [{'account': books['expense'], 'amount': AMOUNT}]}, reason='Fuel')
    books['run']('transfer post', {
        'from_account': books['first'], 'to_account': books['second'],
        'date': '2026-03-05', 'amount': '50.00'}, reason='Top up payroll')

    # Neither moved the chequebook, and neither is a cheque as far as the report is concerned.
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '1001'
    assert _report(books)['totals']['checks_examined'] == 0
    assert _write(books)['document']['check_number'] == '1001'
    # And the card charge still carries the document reference it always had.
    charges = books['run']('card-charge query', {'limit': 5})
    assert charges['items'][0]['number'] and charges['items'][0]['document']['check_number'] is None


# ---------------------------------------------------------------- what it cannot know


def _as_carried_over(books):
    """Mark what is stored the way co0040 leaves a company file it upgraded.

    The migration is exercised against a real pre-upgrade database in
    ``tests/test_check_number_migration.py``; what is needed here is the state it leaves
    behind, so that the report can be asked what it says about numbers it did not issue.
    """
    import sqlite3
    from pathlib import Path
    folder = books['client'].company.show(company=books['company'])['path']
    database = next(Path(folder).glob('*.db'))
    # Only the projection: the revision rows are immutable by trigger, which is what keeps a
    # cheque's history from being rewritten, and the report reads origin from the projection.
    with sqlite3.connect(database) as raw:
        raw.execute("UPDATE check_instruments SET origin='migrated'")


def test_a_hole_among_carried_over_numbers_is_disclosed_rather_than_asserted(books):
    _write(books, number='1001')
    _write(books, number='1003')
    _as_carried_over(books)

    report = _report(books, account=books['first'])
    row, = report['rows']
    assert (row['kind'], row['first_missing']) == ('gap', 1002)
    # 1002 came out of the shared document series, where it may have gone to a transfer, a
    # card charge or a hand-typed entry. The report says it cannot tell rather than sending
    # somebody to look for a cheque that was never written.
    assert row['legacy_uncertain'] is True
    assert report['totals']['legacy_uncertain_gaps'] == 1
    assert report['totals']['checks_numbered_before_the_upgrade'] == 2
    assert 'shared' in report['disclosure'] and 'legacy_uncertain' in report['disclosure']


def test_a_hole_above_what_was_carried_over_is_named_without_a_caveat(books):
    _write(books, number='1001')
    _as_carried_over(books)
    _write(books, number='1003')

    report = _report(books, account=books['first'])
    row, = report['rows']
    # 1002 sits above the highest number the upgrade carried over, so it is a number this
    # chequebook skipped while Bookflow was issuing them, and that is a real hole.
    assert (row['kind'], row['first_missing'], row['legacy_uncertain']) == ('gap', 1002, False)
    assert report['totals']['legacy_uncertain_gaps'] == 0
    # The disclosure is still carried, because the file does hold carried-over numbers.
    assert report['disclosure']


def test_a_file_with_nothing_carried_over_carries_no_disclosure(books):
    _write(books, number='1001')
    _write(books, number='1003')

    report = _report(books, account=books['first'])
    assert report['totals']['checks_numbered_before_the_upgrade'] == 0
    assert report['disclosure'] is None


# ---------------------------------------------------------------- the other form that prints one


def test_paying_a_bill_by_cheque_takes_the_next_number_out_of_the_same_chequebook(books):
    """One chequebook, two forms. Whichever wrote the last cheque, the next one follows it."""
    books['client'].account.update(account=books['first'], next_check_number='2001',
                                   company=books['company'])
    written = _write(books)
    paid = _pay(books, _bill(books))['payments'][0]
    after = _write(books)

    assert written['document']['check_number'] == '2001'
    assert paid['check_number'] == '2002'
    assert after['document']['check_number'] == '2003'
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '2004'


def test_a_bill_payment_that_is_not_a_cheque_takes_no_number_and_moves_no_chequebook(books):
    """A cheque is the method being a check and the money leaving a bank. Nothing else is."""
    books['client'].account.update(account=books['first'], next_check_number='3000',
                                   company=books['company'])
    card = books['client'].account.create(name='Company Card', type='credit_card',
                                          company=books['company'])['id']
    not_a_cheque = next(value for kind, value in books['methods'].items() if kind != 'check')
    by_transfer = _pay(books, _bill(books), method=not_a_cheque)['payments'][0]
    on_the_card = _pay(books, _bill(books), funding_account=card)['payments'][0]

    assert by_transfer['check_number'] is None and on_the_card['check_number'] is None
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '3000'
    assert _report(books)['totals']['checks_examined'] == 0


def test_one_number_is_refused_whichever_form_already_has_it(books):
    """The refusal is the same refusal: a number is on one piece of paper or on none."""
    held = _write(books, number='1001')
    with pytest.raises(BookflowError) as raised:
        _pay(books, _bill(books), check_number='01001')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    assert raised.value.details['held_by'] == held['id']

    paid = _pay(books, _bill(books), check_number='1002')['payments'][0]
    with pytest.raises(BookflowError) as again:
        _write(books, number='1002')
    assert again.value.code == 'E_DUPLICATE_NUMBER'
    assert again.value.details['held_by'] == paid['id']
    # And neither refusal wrote anything: one cheque each is all that exists.
    assert books['run']('check query', {'account': books['first'], 'limit': 10})['count'] == 1
    assert books['run']('bill payment query', {'limit': 10})['count'] == 1


def test_paying_two_payees_at_once_takes_two_consecutive_numbers(books):
    """Two cheques out of one book in one command: the second cannot repeat the first."""
    books['client'].account.update(account=books['first'], next_check_number='4000',
                                   company=books['company'])
    paid = _pay(books, _bill(books), _bill(books, vendor=books['other']))

    assert paid['group_count'] == 2
    assert sorted(payment['check_number'] for payment in paid['payments']) == ['4000', '4001']
    assert books['client'].account.show(
        account=books['first'], company=books['company'])['next_check_number'] == '4002'
    assert _findings(_report(books, account=books['first'])) == []


def test_naming_a_number_while_paying_two_payees_is_refused(books):
    """One number names one piece of paper, and this selection writes two."""
    with pytest.raises(BookflowError) as raised:
        _pay(books, _bill(books), _bill(books, vendor=books['other']), check_number='5000')
    assert raised.value.code == 'E_VALIDATION'
    assert raised.value.details['fields'][0]['field'] == 'check_number'
    assert not books['run']('bill payment query', {'limit': 10})['items']


def test_the_report_places_a_bill_payments_cheque_in_the_run(books):
    """The hole this used to print was 1002, because 1003 was a number it could not see."""
    written = _write(books, number='1001')
    paid = _pay(books, _bill(books), check_number='1003')['payments'][0]

    report = _report(books, account=books['first'])
    row, = report['rows']
    assert (row['kind'], row['first_missing'], row['last_missing']) == ('gap', 1002, 1002)
    assert row['before']['transaction_id'] == written['id']
    assert row['after']['transaction_id'] == paid['id'] and row['after']['number'] == '1003'
    assert report['totals']['checks_examined'] == 2
    assert report['totals']['numbered_checks'] == 2
    assert report['totals']['checks_off_a_bank_account'] == 0
    # Nothing is disclosed as unplaceable any more, because there is nothing left it cannot place.
    assert report['disclosure'] is None


def test_a_voided_bill_payment_keeps_its_number_and_is_neither_a_gap_nor_reusable(books):
    lost = _pay(books, _bill(books), check_number='1002')['payments'][0]
    _write(books, number='1001')
    _write(books, number='1003')
    books['run']('bill payment unapply', {'payment': lost['id']}, reason='Cheque was lost')
    books['run']('bill payment void', {'payment': lost['id']}, reason='Cheque was lost')

    assert _findings(_report(books, account=books['first'])) == []
    with pytest.raises(BookflowError) as raised:
        _write(books, number='1002')
    assert raised.value.code == 'E_DUPLICATE_NUMBER'
    assert books['run']('bill payment show', {'payment': lost['id']})['check_number'] == '1002'


def test_the_register_shows_the_cheque_number_a_bill_payment_was_written_with(books):
    written = _write(books, number='1001')
    paid = _pay(books, _bill(books), check_number='1002')['payments'][0]

    register = books['run']('register query', {'account': books['first'],
                                               'date_from': '2026-01-01', 'date_to': '2026-12-31'})
    numbers = {row['transaction_id']: row['check_number']
               for row in register['rows'] if row['kind'] == 'posting'}
    assert numbers[written['id']] == '1001' and numbers[paid['id']] == '1002'


def test_a_bill_payments_cheque_does_not_make_a_checks_number_ambiguous(books):
    """`check show` opens checks, so only two of those make a number ambiguous.

    The guard `money_out.resolve` keeps is for two bank accounts both writing 1001, which is
    still reachable because a cheque number belongs to one chequebook. A bill payment shares
    that chequebook but is not a check, so it is filtered out before the count is taken --
    otherwise it would refuse a number that names exactly one check.
    """
    written = _write(books, number='1001')
    _pay(books, _bill(books), funding_account=books['second'], check_number='1001')

    assert books['run']('check show', {'check': '1001'})['id'] == written['id']

    here = _write(books, number='2001')
    there = _write(books, account=books['second'], number='2001')
    with pytest.raises(BookflowError) as raised:
        books['run']('check show', {'check': '2001'})
    assert raised.value.code == 'E_VALIDATION'
    assert {row['transaction_id'] for row in raised.value.details['candidates']} == {
        here['id'], there['id']}
