"""Pay a vendor bill, and check the books by hand.

Every figure asserted here is written out in full at the top of the module, so a reader can
add it up without running anything: three bills of 284.60, 75.25 and 40.00, a part payment of
100.00 taken back and then voided, a grouped payment that settles all three, and one of them
put on a credit card instead. What the trial balance, the general ledger and the bill list say after
each step is checked against those figures through the real report commands, not against the
payment's own output.
"""
from copy import deepcopy

import pytest

import bookflow
from bookflow.core.errors import BookflowError

# The bills. Everything below is arithmetic on these.
PARTS = '184.60'
FREIGHT = '100.00'
FIRST = '284.60'           # 184.60 + 100.00
SECOND = '75.25'
THIRD = '40.00'
OWED = '399.85'            # 284.60 + 75.25 + 40.00
PART_PAYMENT = '100.00'
STILL_OPEN = '184.60'      # 284.60 - 100.00
NORTHSIDE_GROUP = '359.85'  # 284.60 + 75.25
# Freeing the 284.60 check off the first bill and pointing it at the second: the second is
# only open for 75.25, so that is all it can take, and 209.35 of the check answers nothing.
REPOINTED = '75.25'
STILL_FREE = '209.35'      # 284.60 - 75.25


@pytest.fixture
def books(tmp_path, monkeypatch):
    """A real migrated company of its own, so every reported total is only this test's."""
    data_root = tmp_path / 'payments'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(data_root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(data_root))
    client.init()
    client.organization.new(name='Payments organization')
    company = client.company.new(legal_name='Payments', home_currency='USD', timezone='UTC',
                                 organization='Payments organization', chart='general')['company_id']
    accounts = client.account.query(company=company, limit=200)['items']
    payable = next(row['id'] for row in accounts if row['type'] == 'accounts_payable')
    bank = next(row['id'] for row in accounts if row['type'] == 'bank')
    receivable = next(row['id'] for row in accounts if row['type'] == 'accounts_receivable')
    parts = client.account.create(company=company, name='Parts Bought', type='expense')['id']
    freight = client.account.create(company=company, name='Freight In', type='expense')['id']
    card = client.account.create(company=company, name='Visa Card', type='credit_card')['id']
    terms = {row['name']: row['id'] for row in client.term.query(company=company, limit=50)['items']}
    north = client.vendor.create(company=company, name='Northside Supply',
                                 terms_id=terms['Net 30'])['id']
    corner = client.vendor.create(company=company, name='Corner Hardware')['id']
    methods = {row['name']: row['id'] for row in
               client.run('payment-method query', dict(limit=50), company=company)['items']}

    def run(name, raw=None, **context):
        return client.run(name, raw or {}, company=company, **context)

    return dict(client=client, company=company, payable=payable, bank=bank, card=card,
                receivable=receivable, parts=parts, freight=freight, north=north, corner=corner,
                methods=methods, run=run)


def _bills(books):
    """The three open bills every case below starts from."""
    first = books['run']('bill post', dict(
        vendor=books['north'], date='2017-03-03', supplier_reference='INV-1',
        expenses=[{'account': books['parts'], 'amount': PARTS},
                  {'account': books['freight'], 'amount': FREIGHT}]), reason='Enter the first bill')
    second = books['run']('bill post', dict(
        vendor=books['north'], date='2017-03-05',
        expenses=[{'account': books['parts'], 'amount': SECOND}]), reason='Enter the second bill')
    third = books['run']('bill post', dict(
        vendor=books['corner'], date='2017-03-06',
        expenses=[{'account': books['parts'], 'amount': THIRD}]), reason='Enter the third bill')
    return first, second, third


def _pay(books, bills, **extra):
    values = dict(date='2017-03-10', bills=bills, funding_account=books['bank'],
                  method=books['methods']['Check'])
    values.update(extra)
    return books['run']('bill pay', values, reason='Pay the vendor')


def _balances(books, date='2017-03-31'):
    """Signed minor units per account from the trial balance, debits positive."""
    report = books['run']('report trial-balance', dict(date_to=date, limit=200))
    rows = {row['account_id']: row['debit']['minor_units'] - row['credit']['minor_units']
            for row in report['rows']}
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    return rows, report


def _open_total(books):
    """The sum of what is still open on every posted bill, from the bill list."""
    items = books['run']('bill query', dict(limit=50))['items']
    return sum(row['settlement_current']['open_minor_units']
               for row in items if row['status'] == 'posted')


def _settlement(books, bill):
    return books['run']('bill show', {'bill': bill})['settlement_current']


def _unpaid(books, date='2017-03-31'):
    """What `report unpaid-bills` says, per bill number, straight from the report command."""
    report = books['run']('report unpaid-bills', dict(as_of=date, limit=50))
    return ({row['number']: (row['amount']['amount'], row['applied']['amount'],
                             row['balance']['amount'], row['settlement_status'])
             for row in report['rows']},
            {key: report['totals'][key]['minor_units'] for key in ('amount', 'applied', 'balance')})


def _aging(books, date='2017-03-31'):
    report = books['run']('report ap-aging', dict(as_of=date, limit=50))
    return ({row['display_vendor_label']: row['total']['minor_units'] for row in report['rows']},
            report['totals']['total']['minor_units'])


def _apply(books, payment, bills, **extra):
    return books['run']('bill payment apply', dict(payment=payment['id'], bills=bills, **extra),
                        reason='Point the check at the right bill')


def _unapply(books, payment, **extra):
    return books['run']('bill payment unapply', dict(payment=payment['id'], **extra),
                        reason='Applied to the wrong bill')


# ---------------------------------------------------------------- the money moves


def test_a_full_payment_takes_the_payable_and_the_bank_down_by_the_same_amount(books):
    first, _, _ = _bills(books)
    before, _ = _balances(books)
    assert before[books['payable']] == -39985 and books['bank'] not in before

    paid = _pay(books, [{'bill': first['id']}], check_number='1041')

    assert paid['group_count'] == 1 and paid['bill_count'] == 1
    assert paid['paid']['amount'] == FIRST
    payment = paid['payments'][0]
    assert payment['type'] == 'bill_payment' and payment['status'] == 'posted'
    assert payment['total']['amount'] == FIRST
    assert payment['funding_kind'] == 'bank_cash' and payment['check_number'] == '1041'
    assert payment['funding_account_id'] == books['bank']
    assert payment['settlement_current']['status'] == 'applied'

    after, report = _balances(books)
    # 399.85 owed, 284.60 paid, 115.25 left; the bank falls by exactly the 284.60.
    assert after[books['payable']] == -11525
    assert after[books['bank']] == -28460
    assert before[books['payable']] - after[books['payable']] == -28460
    assert report['totals']['debit']['amount'] == report['totals']['credit']['amount']

    settlement = _settlement(books, first['id'])
    assert settlement['gross']['amount'] == FIRST
    assert settlement['applied']['amount'] == FIRST
    assert settlement['open']['amount'] == '0.00'
    assert settlement['status'] == 'paid'
    assert _open_total(books) == 11525 == -after[books['payable']]


def test_a_partial_payment_leaves_the_remainder_open_and_accounts_payable_agrees(books):
    first, _, _ = _bills(books)

    paid = _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])

    assert paid['paid']['amount'] == PART_PAYMENT
    settlement = _settlement(books, first['id'])
    assert settlement['gross']['amount'] == FIRST
    assert settlement['applied']['amount'] == PART_PAYMENT
    assert settlement['open']['amount'] == STILL_OPEN
    assert settlement['status'] == 'partial'
    # applied plus open is always gross, whatever was paid.
    assert settlement['applied_minor_units'] + settlement['open_minor_units'] == settlement['gross_minor_units']

    after, _ = _balances(books)
    assert after[books['payable']] == -29985  # 399.85 - 100.00
    assert after[books['bank']] == -10000
    # 184.60 + 75.25 + 40.00 = 299.85, which is the payable, to the cent.
    assert _open_total(books) == 29985 == -after[books['payable']]
    assert books['run']('report trial-balance', dict(date_to='2017-03-31'))['totals'][
        'debit']['amount'] == books['run']('report trial-balance', dict(date_to='2017-03-31'))['totals']['credit']['amount']


def test_a_second_payment_closes_what_the_first_one_left(books):
    first, _, _ = _bills(books)
    _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])
    _pay(books, [{'bill': first['id']}], date='2017-03-11')

    settlement = _settlement(books, first['id'])
    assert settlement['applied']['amount'] == FIRST and settlement['open']['amount'] == '0.00'
    assert settlement['status'] == 'paid'
    after, _ = _balances(books)
    assert after[books['payable']] == -11525 and after[books['bank']] == -28460


def test_a_card_payment_raises_the_card_instead_of_lowering_the_bank(books):
    _, _, third = _bills(books)

    paid = _pay(books, [{'bill': third['id']}], funding_account=books['card'],
                method=books['methods']['Visa'], date='2017-03-12')

    payment = paid['payments'][0]
    assert payment['funding_kind'] == 'card_liability' and payment['check_number'] is None
    after, _ = _balances(books)
    # The card is credit-normal: paying by it increases what the card is owed.
    assert after[books['card']] == -4000
    assert after[books['payable']] == -35985  # 399.85 - 40.00
    assert books['bank'] not in after


# ---------------------------------------------------------------- one payee, one payment


def test_two_bills_for_one_vendor_make_one_payment_and_two_vendors_never_share_one(books):
    first, second, third = _bills(books)

    paid = _pay(books, [{'bill': first['id']}, {'bill': second['id']}, {'bill': third['id']}],
                date='2017-03-12')

    assert paid['bill_count'] == 3 and paid['group_count'] == 2
    assert paid['paid']['amount'] == OWED
    by_vendor = {payment['vendor_name']: payment for payment in paid['payments']}
    assert set(by_vendor) == {'Northside Supply', 'Corner Hardware'}
    northside = by_vendor['Northside Supply']
    assert northside['total']['amount'] == NORTHSIDE_GROUP
    assert sorted(edge['amount']['amount'] for edge in northside['applications']) == [FIRST, SECOND]
    assert by_vendor['Corner Hardware']['total']['amount'] == THIRD
    assert len(by_vendor['Corner Hardware']['applications']) == 1
    assert northside['number'] != by_vendor['Corner Hardware']['number']

    after, _ = _balances(books)
    assert books['payable'] not in after  # nothing left owed
    assert after[books['bank']] == -39985
    assert _open_total(books) == 0
    assert all(row['settlement_current']['status'] == 'paid'
               for row in books['run']('bill query', dict(limit=50))['items'])


def test_naming_a_number_for_a_selection_that_makes_two_payments_is_refused(books):
    first, _, third = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id']}, {'bill': third['id']}], number='CHK-1')
    assert excinfo.value.code == 'E_VALIDATION'
    assert 'one payee at a time' in str(excinfo.value.details)
    assert not books['run']('bill payment query', dict(limit=10))['items']


# ---------------------------------------------------------------- taking it back


def test_unapplying_reopens_the_bill_and_moves_no_money(books):
    first, _, _ = _bills(books)
    paid = _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])
    payment = paid['payments'][0]
    during, _ = _balances(books)

    taken = books['run']('bill payment unapply',
                         dict(payment=payment['id'], expected_version=payment['version']),
                         reason='Applied to the wrong bill')

    assert taken['settlement_current']['applied']['amount'] == '0.00'
    assert taken['settlement_current']['unapplied']['amount'] == PART_PAYMENT
    assert taken['settlement_current']['status'] == 'unapplied'
    assert [edge['active'] for edge in taken['applications']] == [False, False] or \
        not any(edge['active'] for edge in taken['applications'])

    assert _settlement(books, first['id'])['open']['amount'] == FIRST
    after, _ = _balances(books)
    # The cash already left when the payment posted, so nothing in the ledger moves.
    assert after == during
    assert after[books['payable']] == -29985 and after[books['bank']] == -10000
    # 399.85 open against a 299.85 payable: the difference is the 100.00 now standing
    # unapplied against the vendor.
    assert _open_total(books) == 39985
    assert _open_total(books) + after[books['payable']] == 10000


def test_voiding_restores_every_figure_the_payment_changed(books):
    first, _, _ = _bills(books)
    before, _ = _balances(books)
    before_open = _open_total(books)
    paid = _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])
    payment = paid['payments'][0]
    taken = books['run']('bill payment unapply', dict(payment=payment['id']),
                         reason='Applied to the wrong bill')

    voided = books['run']('bill payment void',
                          dict(payment=payment['id'], expected_version=taken['version']),
                          reason='The check was never sent')

    assert voided['status'] == 'voided' and voided['settlement_current']['status'] == 'voided'
    assert voided['void_reason'] == 'The check was never sent'
    after, _ = _balances(books)
    assert after == before
    assert after[books['payable']] == -39985 and books['bank'] not in after
    assert _open_total(books) == before_open == 39985


def test_voiding_a_payment_that_still_settles_a_bill_is_refused(books):
    first, _, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    with pytest.raises(BookflowError) as excinfo:
        books['run']('bill payment void', dict(payment=payment['id']), reason='Sent in error')
    assert excinfo.value.code == 'E_HAS_APPLICATIONS'
    assert _settlement(books, first['id'])['status'] == 'paid'


def test_unapplying_names_only_the_bill_it_means(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}, {'bill': second['id']}])['payments'][0]

    taken = books['run']('bill payment unapply',
                         dict(payment=payment['id'], bills=[second['id']]), reason='Wrong bill')

    assert taken['settlement_current']['applied']['amount'] == FIRST
    assert taken['settlement_current']['unapplied']['amount'] == SECOND
    assert taken['settlement_current']['status'] == 'partial'
    assert _settlement(books, first['id'])['status'] == 'paid'
    assert _settlement(books, second['id'])['open']['amount'] == SECOND
    with pytest.raises(BookflowError) as excinfo:
        books['run']('bill payment unapply', dict(payment=payment['id'], bills=[second['id']]),
                     reason='Again')
    assert excinfo.value.code == 'E_APPLICATION_INACTIVE'


def test_unapplying_nothing_is_no_change_rather_than_an_error(books):
    first, _, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    books['run']('bill payment unapply', dict(payment=payment['id']), reason='Wrong bill')
    again = books['run']('bill payment unapply', dict(payment=payment['id']), reason='Wrong bill')
    assert again['changed'] is False
    assert again['settlement_current']['applied']['amount'] == '0.00'


# ------------------------------------------------ pointing the same money at another bill


def test_a_freed_payment_answers_another_bill_and_moves_not_a_cent(books):
    """The whole cycle: applied to one bill, freed, pointed at another, same check."""
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}], check_number='1041')['payments'][0]
    during, _ = _balances(books)
    assert _settlement(books, first['id'])['status'] == 'paid'

    taken = _unapply(books, payment, expected_version=payment['version'])
    moved = _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])

    # The same document, not a new one: the check number and the number it was written on
    # survive, which is exactly what voiding and paying again would have destroyed.
    assert moved['id'] == payment['id'] and moved['number'] == payment['number']
    assert moved['check_number'] == '1041' and moved['status'] == 'posted'
    assert moved['total']['amount'] == FIRST

    # 75.25 of the 284.60 answers the second bill; the other 209.35 answers nothing yet.
    assert moved['settlement_current']['applied']['amount'] == REPOINTED
    assert moved['settlement_current']['unapplied']['amount'] == STILL_FREE
    assert moved['settlement_current']['status'] == 'partial'

    assert _settlement(books, first['id'])['open']['amount'] == FIRST
    assert _settlement(books, first['id'])['status'] == 'unpaid'
    settled = _settlement(books, second['id'])
    assert settled['applied']['amount'] == REPOINTED and settled['open']['amount'] == '0.00'
    assert settled['status'] == 'paid'
    assert settled['applied_minor_units'] + settled['open_minor_units'] == settled['gross_minor_units']

    after, report = _balances(books)
    # An application posts nothing: the cash left when the payment posted.
    assert after == during
    assert after[books['payable']] == -11525 and after[books['bank']] == -28460
    assert report['totals']['debit']['minor_units'] == report['totals']['credit']['minor_units']
    # 284.60 + 40.00 still open against a 115.25 payable; the 209.35 gap is the free check.
    assert _open_total(books) == 32460
    assert _open_total(books) + after[books['payable']] == 20935


def test_the_reports_agree_with_the_bill_at_every_step_of_the_cycle(books):
    first, second, _ = _bills(books)
    rows, totals = _unpaid(books)
    assert rows[first['number']] == (FIRST, '0.00', FIRST, 'unpaid')
    assert totals == dict(amount=39985, applied=0, balance=39985)
    assert _aging(books) == ({'Northside Supply': 35985, 'Corner Hardware': 4000}, 39985)

    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    rows, totals = _unpaid(books)
    # A paid bill leaves the report entirely; what is left is the 75.25 and the 40.00.
    assert first['number'] not in rows
    assert rows[second['number']] == (SECOND, '0.00', SECOND, 'unpaid')
    assert totals == dict(amount=11525, applied=0, balance=11525)
    assert _aging(books) == ({'Northside Supply': 7525, 'Corner Hardware': 4000}, 11525)

    taken = _unapply(books, payment, expected_version=payment['version'])
    rows, totals = _unpaid(books)
    assert rows[first['number']] == (FIRST, '0.00', FIRST, 'unpaid')
    assert totals == dict(amount=39985, applied=0, balance=39985)
    # Aging groups by vendor, so the freed check still nets against Northside's own bills.
    assert _aging(books) == ({'Northside Supply': 7525, 'Corner Hardware': 4000}, 11525)

    _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])
    rows, totals = _unpaid(books)
    assert second['number'] not in rows
    assert rows[first['number']] == (FIRST, '0.00', FIRST, 'unpaid')
    assert totals == dict(amount=32460, applied=0, balance=32460)
    assert _aging(books) == ({'Northside Supply': 7525, 'Corner Hardware': 4000}, 11525)
    for bill in (first, second):
        settlement = _settlement(books, bill['id'])
        reported = rows.get(bill['number'], (None, None, '0.00', None))
        assert reported[2] == settlement['open']['amount']


def test_applying_part_of_what_is_free_leaves_the_rest_free(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    part = _apply(books, payment, [{'bill': second['id'], 'amount': '25.00'}],
                  expected_version=taken['version'])

    assert part['settlement_current']['applied']['amount'] == '25.00'
    assert part['settlement_current']['unapplied']['amount'] == '259.60'  # 284.60 - 25.00
    settlement = _settlement(books, second['id'])
    assert settlement['open']['amount'] == '50.25' and settlement['status'] == 'partial'

    rest = _apply(books, payment, [{'bill': second['id']}], expected_version=part['version'])
    assert rest['settlement_current']['applied']['amount'] == REPOINTED
    assert rest['settlement_current']['unapplied']['amount'] == STILL_FREE
    assert _settlement(books, second['id'])['open']['amount'] == '0.00'


def test_one_entered_line_can_answer_two_bills_once_it_has_been_freed(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    split = _apply(books, payment, [{'bill': first['id'], 'amount': PART_PAYMENT},
                                    {'bill': second['id']}],
                   expected_version=taken['version'])

    assert split['settlement_current']['applied']['amount'] == '175.25'  # 100.00 + 75.25
    assert split['settlement_current']['unapplied']['amount'] == '109.35'
    assert _settlement(books, first['id'])['open']['amount'] == STILL_OPEN
    assert _settlement(books, second['id'])['open']['amount'] == '0.00'
    # One entered line, one component, two live edges: the line names neither bill now, and
    # what it is worth is still the whole 284.60 it was entered for.
    line = split['revision']['lines'][0]
    assert line['bill_number'] == '' and line['amount']['amount'] == FIRST
    assert line['applied']['amount'] == '175.25'
    live = [edge for edge in split['applications'] if edge['active']]
    assert sorted(edge['amount']['amount'] for edge in live) == [PART_PAYMENT, SECOND]
    assert len({edge['source_component_id'] for edge in live}) == 1


def test_a_line_names_the_bill_its_money_answers_now(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    assert payment['revision']['lines'][0]['bill_number'] == first['number']
    taken = _unapply(books, payment, expected_version=payment['version'])
    assert taken['revision']['lines'][0]['bill_number'] == ''
    moved = _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])
    line = moved['revision']['lines'][0]
    assert line['bill_number'] == second['number']
    # What it was entered for is still on the line itself.
    assert line['description'] == f'Bill {first["number"]}'


def test_applying_more_than_the_payment_has_free_is_refused(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': second['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': first['id'], 'amount': '100.00'}],
               expected_version=taken['version'])

    assert excinfo.value.code == 'E_APPLICATION_CAPACITY'
    assert excinfo.value.details['available_minor_units'] == 7525
    assert excinfo.value.details['requested_minor_units'] == 10000
    assert excinfo.value.details['payment_id'] == payment['id']
    assert _settlement(books, first['id'])['open']['amount'] == FIRST


def test_a_payment_with_nothing_free_has_nothing_to_apply(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id']}], expected_version=payment['version'])

    assert excinfo.value.code == 'E_APPLICATION_CAPACITY'
    assert excinfo.value.details['available_minor_units'] == 0
    assert _settlement(books, second['id'])['open']['amount'] == SECOND


def test_applying_more_than_is_open_on_the_bill_is_refused(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id'], 'amount': '80.00'}],
               expected_version=taken['version'])

    # The same typed refusal the paying path gives, and the same figures.
    assert excinfo.value.code == 'E_APPLICATION_CAPACITY'
    assert excinfo.value.details['available_minor_units'] == 7525
    assert excinfo.value.details['bill_number'] == second['number']
    assert _settlement(books, second['id'])['open']['amount'] == SECOND


def test_a_payment_cannot_answer_another_vendors_bill(books):
    first, _, third = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': third['id']}], expected_version=taken['version'])

    assert excinfo.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert excinfo.value.details['bill_number'] == third['number']
    assert _settlement(books, third['id'])['open']['amount'] == THIRD


def test_a_voided_payment_answers_nothing(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    books['run']('bill payment void', dict(payment=payment['id'],
                                           expected_version=taken['version']),
                 reason='The check was never sent')

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id']}])

    assert excinfo.value.code == 'E_APPLICATION_INACTIVE'
    assert _settlement(books, second['id'])['open']['amount'] == SECOND


def test_a_voided_bill_cannot_be_settled_by_a_freed_payment(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    books['run']('bill void', dict(bill=second['id']), reason='Entered twice')

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])

    assert excinfo.value.code == 'E_APPLICATION_INACTIVE'
    assert _settlement(books, first['id'])['open']['amount'] == FIRST


def test_a_stale_expected_version_refuses_the_whole_apply(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    with pytest.raises(BookflowError) as stale_payment:
        _apply(books, payment, [{'bill': second['id']}], expected_version=payment['version'])
    assert stale_payment.value.code == 'E_VERSION_CONFLICT'

    books['run']('bill update', dict(bill=second['id'], memo='Reworded'), reason='Fix it')
    with pytest.raises(BookflowError) as stale_bill:
        _apply(books, payment, [{'bill': second['id'], 'expected_version': second['version']}],
               expected_version=taken['version'])
    assert stale_bill.value.code == 'E_VERSION_CONFLICT'
    assert _settlement(books, second['id'])['open']['amount'] == SECOND
    assert _settlement(books, first['id'])['open']['amount'] == FIRST


def test_an_application_cannot_be_dated_before_the_money_left(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id']}], date='2017-03-09',
               expected_version=taken['version'])

    assert excinfo.value.code == 'E_VALIDATION'
    assert _settlement(books, second['id'])['open']['amount'] == SECOND


def test_applying_into_a_closed_period_is_refused(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    books['client'].company.update(company=books['company'], closing_date='2017-03-31')

    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])

    assert excinfo.value.code == 'E_PERIOD_CLOSED'
    assert _settlement(books, second['id'])['open']['amount'] == SECOND


def test_a_later_application_answers_a_bill_entered_after_the_check_was_written(books):
    first, _, _ = _bills(books)
    later = books['run']('bill post', dict(
        vendor=books['north'], date='2017-03-20',
        expenses=[{'account': books['parts'], 'amount': '50.00'}]), reason='A later bill')
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    # Dated at the check, the later bill is out of reach; dated at the bill, it is not.
    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': later['id']}], expected_version=taken['version'])
    assert excinfo.value.code == 'E_VALIDATION'

    moved = _apply(books, payment, [{'bill': later['id']}], date='2017-03-20',
                   expected_version=taken['version'])
    assert moved['settlement_current']['applied']['amount'] == '50.00'
    assert _settlement(books, later['id'])['open']['amount'] == '0.00'
    edge = next(row for row in moved['applications'] if row['active'])
    assert edge['effective_date'] == '2017-03-20'
    after, _ = _balances(books)
    assert after[books['bank']] == -28460 and after[books['payable']] == -16525


def test_the_same_bill_cannot_be_named_twice_in_one_apply(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    with pytest.raises(BookflowError) as excinfo:
        _apply(books, payment, [{'bill': second['id'], 'amount': '10.00'},
                                {'bill': second['id'], 'amount': '10.00'}],
               expected_version=taken['version'])
    assert excinfo.value.code == 'E_VALIDATION'


def test_applying_locks_the_bill_and_the_payment_again(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    moved = _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])

    with pytest.raises(BookflowError) as voiding:
        books['run']('bill payment void', dict(payment=payment['id'],
                                               expected_version=moved['version']),
                     reason='Sent in error')
    assert voiding.value.code == 'E_HAS_APPLICATIONS'
    with pytest.raises(BookflowError) as correcting:
        books['run']('bill update', dict(bill=second['id'], memo='Reworded'), reason='Fix it')
    assert correcting.value.code == 'E_HAS_APPLICATIONS'


def test_a_dry_run_applies_nothing(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])

    preview = books['run']('bill payment apply',
                           dict(payment=payment['id'], bills=[{'bill': second['id']}],
                                expected_version=taken['version']),
                           reason='Have a look', dry_run=True)

    assert preview['dry_run'] and preview['settlement_current']['applied']['amount'] == REPOINTED
    assert _settlement(books, second['id'])['open']['amount'] == SECOND
    assert books['run']('bill payment show', {'payment': payment['id']})[
        'settlement_current']['unapplied']['amount'] == FIRST


def test_the_same_idempotency_key_applies_once(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    values = dict(payment=payment['id'], bills=[{'bill': second['id']}],
                  expected_version=taken['version'])

    once = books['run']('bill payment apply', deepcopy(values), reason='Point it',
                        idempotency_key='apply-1')
    twice = books['run']('bill payment apply', deepcopy(values), reason='Point it',
                         idempotency_key='apply-1')

    assert twice['idempotent_replay'] and once['version'] == twice['version']
    assert _settlement(books, second['id'])['applied']['amount'] == REPOINTED
    live = [edge for edge in books['run']('bill payment show', {'payment': payment['id']})[
        'applications'] if edge['active']]
    assert len(live) == 1


def test_every_apply_is_attributed_in_the_company_audit(books):
    first, second, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    taken = _unapply(books, payment, expected_version=payment['version'])
    _apply(books, payment, [{'bill': second['id']}], expected_version=taken['version'])

    events = books['run']('audit list', dict(limit=20))['items']
    applied = next(event for event in events if event['command'] == 'bill payment apply')
    assert applied['summary'] == f"apply bill payment {payment['number']}"
    assert applied['reason'] == 'Point the check at the right bill'


# ---------------------------------------------------------------- the seam back into the bill


def test_a_paid_bill_refuses_correction_and_void_until_the_payment_is_unapplied(books):
    first, _, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])['payments'][0]

    for command, values in (('bill update', dict(bill=first['id'], memo='Reworded')),
                            ('bill void', dict(bill=first['id']))):
        with pytest.raises(BookflowError) as excinfo:
            books['run'](command, values, reason='Fix the bill')
        assert excinfo.value.code == 'E_HAS_APPLICATIONS'

    books['run']('bill payment unapply', dict(payment=payment['id']), reason='Wrong bill')
    corrected = books['run']('bill update', dict(bill=first['id'], memo='Reworded'),
                             reason='Fix the bill')
    assert corrected['memo'] == 'Reworded'
    assert corrected['settlement_current']['open']['amount'] == FIRST


# ---------------------------------------------------------------- what it will not do


def test_paying_more_than_is_open_is_refused_rather_than_becoming_a_credit(books):
    first, _, _ = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id'], 'amount': '300.00'}])
    assert excinfo.value.code == 'E_APPLICATION_CAPACITY'
    assert excinfo.value.details['available_minor_units'] == 28460
    assert excinfo.value.details['requested_minor_units'] == 30000

    _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id'], 'amount': '200.00'}], date='2017-03-11')
    assert excinfo.value.details['available_minor_units'] == 18460
    assert _settlement(books, first['id'])['open']['amount'] == STILL_OPEN


def test_a_voided_bill_cannot_be_paid(books):
    first, _, _ = _bills(books)
    books['run']('bill void', dict(bill=first['id']), reason='Entered twice')
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id']}])
    assert excinfo.value.code == 'E_APPLICATION_INACTIVE'


def test_a_payment_cannot_be_dated_before_the_bill_it_settles(books):
    first, _, _ = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id']}], date='2017-03-02')
    assert excinfo.value.code == 'E_VALIDATION'
    assert 'cannot settle bill' in str(excinfo.value.details)


def test_a_zero_amount_is_refused_because_nothing_here_writes_a_discount(books):
    """Credits and discounts alone must never post a payment written for zero."""
    first, _, _ = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id'], 'amount': '0.00'}])
    # The money parser refuses a non-positive amount before the settlement rules see it.
    assert excinfo.value.code == 'E_VALUE_RANGE'
    assert not books['run']('bill payment query', dict(limit=10))['items']
    assert _settlement(books, first['id'])['open']['amount'] == FIRST


def test_a_check_number_belongs_to_a_check_drawn_on_a_bank(books):
    first, _, third = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id']}], method=books['methods']['Cash'], check_number='1041')
    assert excinfo.value.code == 'E_VALIDATION'
    assert 'not a check' in str(excinfo.value.details)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': third['id']}], funding_account=books['card'],
             method=books['methods']['Check'], check_number='1041')
    assert excinfo.value.code == 'E_VALIDATION'


def test_a_bill_is_not_paid_out_of_an_expense_or_a_receivable(books):
    first, _, _ = _bills(books)
    for account in (books['parts'], books['receivable'], books['payable']):
        with pytest.raises(BookflowError) as excinfo:
            _pay(books, [{'bill': first['id']}], funding_account=account)
        assert excinfo.value.code == 'E_VALIDATION'


def test_a_stale_expected_version_on_a_selected_bill_refuses_the_whole_payment(books):
    first, second, _ = _bills(books)
    books['run']('bill update', dict(bill=first['id'], memo='Reworded'), reason='Fix it')
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id'], 'expected_version': first['version']},
                     {'bill': second['id']}])
    assert excinfo.value.code == 'E_VERSION_CONFLICT'
    assert not books['run']('bill payment query', dict(limit=10))['items']
    assert _open_total(books) == 39985


def test_paying_into_a_closed_period_is_refused(books):
    first, _, _ = _bills(books)
    books['client'].company.update(company=books['company'], closing_date='2017-03-31')
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id']}])
    assert excinfo.value.code == 'E_PERIOD_CLOSED'


def test_the_same_bill_cannot_be_named_twice_in_one_payment(books):
    first, _, _ = _bills(books)
    with pytest.raises(BookflowError) as excinfo:
        _pay(books, [{'bill': first['id'], 'amount': '10.00'},
                     {'bill': first['id'], 'amount': '10.00'}])
    assert excinfo.value.code == 'E_VALIDATION'


# ---------------------------------------------------------------- reading it back


def test_the_ledger_shows_every_payable_movement_and_closes_at_what_is_owed(books):
    first, second, third = _bills(books)
    _pay(books, [{'bill': first['id'], 'amount': PART_PAYMENT}])
    _pay(books, [{'bill': second['id']}, {'bill': third['id']}], date='2017-03-12')

    ledger = books['run']('report general-ledger', dict(
        date_from='2017-03-01', date_to='2017-03-31', account=books['payable'], limit=200))
    postings = [row for row in ledger['rows'] if row['kind'] == 'posting']
    assert [row['transaction_type'] for row in postings] == [
        'bill', 'bill', 'bill', 'bill_payment', 'bill_payment', 'bill_payment']
    assert [row['credit']['amount'] for row in postings] == [
        FIRST, SECOND, THIRD, '0.00', '0.00', '0.00']
    assert [row['debit']['amount'] for row in postings] == [
        '0.00', '0.00', '0.00', PART_PAYMENT, SECOND, THIRD]
    closing = next(row for row in ledger['rows'] if row['kind'] == 'closing')
    assert closing['signed_balance']['amount'] == '-' + STILL_OPEN
    assert _open_total(books) == 18460


def test_show_and_query_read_back_what_was_paid(books):
    first, second, third = _bills(books)
    _pay(books, [{'bill': first['id']}, {'bill': second['id']}], check_number='1041')
    _pay(books, [{'bill': third['id']}], date='2017-03-12', method=books['methods']['Cash'])

    page = books['run']('bill payment query', dict(limit=10))
    assert [row['vendor_name'] for row in page['items']] == ['Northside Supply', 'Corner Hardware']
    assert [row['total']['amount'] for row in page['items']] == [NORTHSIDE_GROUP, THIRD]
    assert [row['check_number'] for row in page['items']] == ['1041', None]
    assert page['count'] == 2 and page['has_more'] is False

    assert [row['number'] for row in books['run'](
        'bill payment query', dict(bill=first['id']))['items']] == [page['items'][0]['number']]
    assert [row['number'] for row in books['run'](
        'bill payment query', dict(vendor=books['corner']))['items']] == [page['items'][1]['number']]
    assert len(books['run']('bill payment query', dict(check_number='1041'))['items']) == 1
    assert len(books['run']('bill payment query', dict(funding_account=books['bank']))['items']) == 2
    assert len(books['run']('bill payment query', dict(method=books['methods']['Cash']))['items']) == 1

    shown = books['run']('bill payment show', {'payment': page['items'][0]['id']})
    assert shown['total']['amount'] == NORTHSIDE_GROUP
    assert shown['payment_method_name'] == 'Check'
    assert [line['bill_number'] for line in shown['revision']['lines']] == [first['number'], second['number']]
    assert [line['amount']['amount'] for line in shown['revision']['lines']] == [FIRST, SECOND]
    assert [line['applied']['amount'] for line in shown['revision']['lines']] == [FIRST, SECOND]
    assert len(shown['revision']['batches']) == 1
    assert shown['revision']['batches'][0]['debit_total']['amount'] == NORTHSIDE_GROUP
    assert shown['revision']['profile']['funding_account']['id'] == books['bank']


def test_a_dry_run_reads_the_books_and_writes_nothing(books):
    first, _, _ = _bills(books)
    preview = books['run']('bill pay', dict(
        date='2017-03-10', bills=[{'bill': first['id']}], funding_account=books['bank'],
        method=books['methods']['Check']), reason='Pay the vendor', dry_run=True)
    assert preview['paid']['amount'] == FIRST
    assert preview['payments'][0]['total']['amount'] == FIRST
    assert not books['run']('bill payment query', dict(limit=10))['items']
    assert _settlement(books, first['id'])['open']['amount'] == FIRST


def test_the_same_idempotency_key_pays_once(books):
    first, _, _ = _bills(books)
    values = dict(date='2017-03-10', bills=[{'bill': first['id']}],
                  funding_account=books['bank'], method=books['methods']['Check'])
    once = books['run']('bill pay', deepcopy(values), reason='Pay', idempotency_key='pay-1')
    twice = books['run']('bill pay', deepcopy(values), reason='Pay', idempotency_key='pay-1')
    assert once['payments'][0]['id'] == twice['payments'][0]['id']
    assert len(books['run']('bill payment query', dict(limit=10))['items']) == 1
    assert _settlement(books, first['id'])['open']['amount'] == '0.00'


def test_every_write_is_attributed_in_the_company_audit(books):
    first, _, _ = _bills(books)
    payment = _pay(books, [{'bill': first['id']}])['payments'][0]
    events = books['run']('audit list', dict(limit=20))['items']
    paid = next(event for event in events if event['command'] == 'bill pay')
    assert paid['summary'] == f"pay bill payment {payment['number']}"
    assert paid['reason'] == 'Pay the vendor'


# ---------------------------------------------------------------- every surface, same result

COMMANDS = frozenset(('bill pay', 'bill payment show', 'bill payment query',
                      'bill payment history', 'bill payment apply', 'bill payment unapply',
                      'bill payment void'))


@pytest.mark.timeout(300)
def test_the_same_bill_payment_through_python_cli_http_and_mcp(root, tmp_path):
    pytest.importorskip('mcp')
    import anyio

    from tests.mcp_matrix_support import Matrix, normalize
    from tests.test_mcp_registry_work import GHOST

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            for surface in matrix.documents:
                calls = {}

                async def call(name, raw, **ctx):
                    if not ctx.get('rejected'):
                        calls[name] = deepcopy(raw)
                    return await matrix.call(surface, name, raw, **ctx)

                expense = (await matrix.call(surface, 'account create',
                                             dict(name='Parity supplies', type='expense')))['id']
                checking = (await matrix.call(surface, 'account create',
                                              dict(name='Parity checking', type='bank')))['id']
                vendor = (await matrix.call(surface, 'vendor create',
                                            dict(name='Parity Supply Co')))['id']
                method = next(row['id'] for row in (await matrix.call(
                    surface, 'payment-method query', dict(limit=50)))['items']
                    if row['name'] == 'Check')
                bill = await matrix.call(surface, 'bill post', dict(
                    vendor=vendor, date='2026-03-04', number='PARITY-BILL-1',
                    expenses=[{'account': expense, 'amount': FIRST, 'memo': 'Fittings'}]))
                request = dict(date='2026-03-06', number='PARITY-PAY-1',
                               funding_account=checking, method=method, check_number='1041',
                               memo='Parity payment', bills=[{'bill': bill['id']}])

                assert (await call('bill pay', request, dry_run=True))['dry_run']
                paid = await call('bill pay', request, idempotency_key='pay-1')
                replay = await call('bill pay', request, idempotency_key='pay-1')
                payment = paid['payments'][0]
                assert replay['payments'][0]['id'] == payment['id'] and replay['idempotent_replay']
                assert paid['group_count'] == 1 and paid['paid']['amount'] == FIRST

                await call('bill payment show', {'payment': payment['id']})
                await call('bill payment query', {'vendor': vendor, 'limit': 10})
                walked = await call('bill payment history', {'payment': payment['id'], 'limit': 10})
                assert walked['count'] == 1 and walked['items'][0]['revision_number'] == 1
                assert [row['kind'] for row in walked['items'][0]['batches']] == ['original']
                assert [(row['kind'], row['active']) for row in walked['items'][0]['applications']] == [
                    ('apply', True)]
                taken = await call('bill payment unapply', {
                    'payment': payment['id'], 'expected_version': payment['version']})
                assert taken['settlement_current']['unapplied']['amount'] == FIRST
                back = await call('bill payment apply', {
                    'payment': payment['id'], 'expected_version': taken['version'],
                    'bills': [{'bill': bill['id']}]})
                assert back['settlement_current']['applied']['amount'] == FIRST
                freed = await call('bill payment unapply', {
                    'payment': payment['id'], 'expected_version': back['version']})
                await call('bill payment void', {'payment': payment['id'],
                                                 'expected_version': freed['version']})
                ended = await call('bill payment history', {'payment': payment['id'], 'limit': 10})
                assert ended['status'] == 'voided'
                assert [row['kind'] for row in ended['items'][0]['batches']] == ['original', 'reversal']
                assert not any(row['active'] for row in ended['items'][0]['applications'])

                refused = await call('bill pay', {
                    **request, 'number': 'PARITY-PAY-2',
                    'bills': [{'bill': bill['id'], 'amount': '9999.00'}]}, rejected=True)
                assert refused['code'] == 'E_APPLICATION_CAPACITY'
                assert refused['details']['available_minor_units'] == 28460
                assert set(calls) == COMMANDS
                for name, data in list(calls.items()):
                    assert (await call(name, data, company=GHOST,
                                       rejected=True))['code'] == 'E_COMPANY_NOT_FOUND'
            expected = normalize(matrix.documents['python'], matrix.roots['python'], set())
            for surface in ('cli', 'http', 'mcp'):
                actual = normalize(matrix.documents[surface], matrix.roots[surface], set())
                assert len(actual) == len(expected)
                for index, (left, right) in enumerate(zip(expected, actual)):
                    assert left == right, (surface, index, left, right)
        finally:
            await matrix.close()

    anyio.run(witness)
