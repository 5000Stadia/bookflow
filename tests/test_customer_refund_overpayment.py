"""Refunding what a customer overpaid, with the books checked by hand.

One story, written out in full here so no figure below comes from the product:

    Ridge is invoiced 100.00 for four site visits at 25.00.
        Dr Accounts Receivable 10000, Cr Income 10000.
    Ridge sends a cheque for 150.00 and 100.00 of it is applied to that invoice.
        Dr Checking 15000, Cr Accounts Receivable 15000.
        The receipt now carries 15000 of capacity, 10000 of it spent on the invoice,
        so 5000 is standing unapplied and Ridge's receivable is 10000 - 15000 = -5000.
    The business sends the extra 50.00 back.
        Dr Accounts Receivable 5000, Cr Checking 5000.

    After the refund, by hand:
        Accounts Receivable   -5000 + 5000 =      0
        Checking             15000 - 5000 =  10000
        Income                                -10000
        Trial balance debits 10000, credits 10000.
        The receipt's unapplied capacity 5000 - 5000 = 0.

The three things this file exists to prove:

**The overage is consumed exactly once.** Available before, the refund, available after, and a
second refund of the same overage refused with the whole database unchanged.

**The refund clears the customer off the account.** Ridge's balance is exactly zero afterwards,
which is the defect the cheque-against-A/R workaround could never reach: that workaround posts
the same two legs and consumes nothing, so the overage goes on offering itself for ever.

**A correction releases and retakes exactly.** Correcting the refund down to 30.00 hands the
whole 5000 back and takes 3000 again, so the receipt is worth 2000 and never 5000 twice.
"""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from tests.credit_support import (  # noqa: F401  (books is a fixture)
    balances, books, goodwill_credit, invoice, settlement, ties_to_the_receivable,
)
from tests.payment_raw_evidence import table

# The money, written out once.
INVOICE = 10000        # 100.00 billed
CHEQUE = 15000         # 150.00 received
OVERAGE = CHEQUE - INVOICE   # 5000 standing unapplied
PART = 3000            # 30.00, the corrected refund


def overpaid(books, amount='150.00', date='2026-03-05'):
    """The invoice, and a cheque larger than it with the invoice's own amount applied."""
    sale = invoice(books, date='2026-03-02')
    payment = books['run']('payment receive', dict(
        customer=books['ridge'], date=date, amount=amount, operation_key='overpaid-cheque',
        deposit_to=books['checking'], payment_method=books['method'],
        applications={'mode': 'inline', 'items': [
            {'invoice': sale['id'], 'expected_version': sale['version'], 'amount': '100.00'}]}),
        reason='Receive the cheque')
    return sale, payment


def unapplied(books, payment_id):
    return books['run']('payment show', {'payment': payment_id})['current']['available_minor_units']


def refund_overage(books, payment_id, amount=None, date='2026-03-20', **extra):
    source = {'payment': payment_id}
    if amount is not None:
        source['amount'] = amount
    request = dict(date=date, funding_account=books['checking'], method=books['method'],
                   sources=[source])
    request.update(extra)
    return books['run']('customer-refund post', request, reason='Send the overpayment back')


def whole_database(books):
    """Every row of every table, exactly as stored, for a refusal to be measured against."""
    with sqlite3.connect(books['database']) as raw:
        names = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            " ORDER BY name")]
        return {name: table(raw, name) for name in names}


def application_id(books, payment_id):
    """The one live application on this receipt, read off its own settlement page."""
    page = books['run']('payment settlement',
                        {'payment': payment_id, 'kind': 'applications', 'limit': 50})
    live, = page['items']
    return live['application_id']


# ---------------------------------------------------------------- the gap this closes


def test_an_overpayment_is_refunded_as_one_action_and_the_customer_goes_to_zero(books):
    sale, payment = overpaid(books)

    # Before: the books say exactly what the module docstring computed by hand.
    before, _ = balances(books)
    assert before[books['receivable']] == INVOICE - CHEQUE == -OVERAGE
    assert before[books['checking']] == CHEQUE
    assert before[books['income']] == -INVOICE
    assert unapplied(books, payment['id']) == OVERAGE
    assert settlement(books, sale['id'])['due_minor_units'] == 0

    paid = refund_overage(books, payment['id'])

    assert paid['type'] == 'customer_refund' and paid['status'] == 'posted'
    assert paid['total']['minor_units'] == OVERAGE
    assert paid['customer_id'] == books['ridge']
    # The refund names the receipt it drew on, not a credit memo.
    source, = paid['revision']['profile']['sources']
    assert source['payment_id'] == payment['id'] and source['credit_memo_id'] is None
    assert source['amount_minor_units'] == OVERAGE
    assert source['available_minor_units'] == OVERAGE

    after, _ = balances(books)
    # A zero balance leaves the account off the trial balance entirely, so read it as zero.
    assert after.get(books['receivable'], 0) == 0           # the customer is square
    assert after[books['checking']] == CHEQUE - OVERAGE == INVOICE
    assert after[books['income']] == -INVOICE               # no revenue moved
    _, report = ties_to_the_receivable(books)
    assert report['totals']['total']['minor_units'] == 0
    assert books['run']('customer show', {'customer': books['ridge']}
                        )['current_balance']['minor_units'] == 0


def test_the_refunded_overage_stops_being_available_and_cannot_be_spent_again(books):
    sale, payment = overpaid(books)
    assert unapplied(books, payment['id']) == OVERAGE

    refund_overage(books, payment['id'])

    assert unapplied(books, payment['id']) == 0
    frozen = whole_database(books)

    with pytest.raises(BookflowError) as refused:
        refund_overage(books, payment['id'], date='2026-03-21')
    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    assert refused.value.details['payment_id'] == payment['id']
    assert refused.value.details['available']['minor_units'] == 0
    # Nothing at all was written: not a row, not an audit event, not a sequence.
    assert whole_database(books) == frozen


def test_every_surface_that_reports_unapplied_cash_stops_reporting_it(books):
    """Three separate readers compute this figure; a refund has to move all three."""
    sale, payment = overpaid(books)
    listed = lambda: next(row for row in books['run']('payment query', {'limit': 50})['items']
                          if row['id'] == payment['id'])
    settled = lambda: books['run']('payment settlement', {'payment': payment['id']})
    assert unapplied(books, payment['id']) == OVERAGE          # payment show
    assert listed()['unapplied_minor_units'] == OVERAGE        # payment query
    assert settled()['unapplied_minor_units'] == OVERAGE       # payment settlement
    assert [row['id'] for row in books['run'](
        'payment query', {'limit': 50, 'has_available_credit': True})['items']] == [payment['id']]

    refund_overage(books, payment['id'])

    assert unapplied(books, payment['id']) == 0
    assert listed()['unapplied_minor_units'] == 0
    assert settled()['unapplied_minor_units'] == 0
    assert settled()['all_committed_current']['components'][0]['available_minor_units'] == 0
    assert books['run']('payment query',
                        {'limit': 50, 'has_available_credit': True})['items'] == []


def test_the_refunded_overage_cannot_be_applied_to_the_next_invoice_either(books):
    sale, payment = overpaid(books)
    refund_overage(books, payment['id'])
    later = invoice(books, date='2026-03-25', due='2026-04-25')

    with pytest.raises(BookflowError) as refused:
        books['run']('payment apply', dict(
            payment=payment['id'], expected_version=books['run'](
                'payment show', {'payment': payment['id']})['version'],
            operation_key='spend-it-twice', date='2026-03-26',
            applications={'mode': 'inline', 'items': [
                {'invoice': later['id'], 'expected_version': later['version'],
                 'amount': '50.00'}]}), reason='Try to spend the refunded overage')
    assert refused.value.code == 'E_APPLICATION_CAPACITY'
    assert settlement(books, later['id'])['due_minor_units'] == INVOICE


def test_only_the_overage_may_be_refunded_never_the_part_that_paid_the_invoice(books):
    sale, payment = overpaid(books)
    with pytest.raises(BookflowError) as refused:
        refund_overage(books, payment['id'], amount='60.00')
    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    assert refused.value.details['requested']['minor_units'] == 6000
    assert refused.value.details['available']['minor_units'] == OVERAGE
    assert unapplied(books, payment['id']) == OVERAGE


# ---------------------------------------------------------------- the two legs, by hand


def test_the_refund_posts_one_receivable_debit_and_one_bank_credit_and_nothing_else(books):
    sale, payment = overpaid(books)
    paid = refund_overage(books, payment['id'])
    ledger = books['run']('report general-ledger', dict(
        date_from='2026-03-01', date_to='2026-03-31', limit=200))
    legs = sorted((row['account_id'], row['debit']['minor_units'], row['credit']['minor_units'])
                  for row in ledger['rows'] if row.get('transaction_id') == paid['id'])
    assert legs == sorted([(books['receivable'], OVERAGE, 0), (books['checking'], 0, OVERAGE)])
    assert all(row['transaction_type'] == 'customer_refund' for row in ledger['rows']
               if row.get('transaction_id') == paid['id'])


# ---------------------------------------------------------------- correction and void


def test_correcting_the_refund_releases_the_whole_overage_and_retakes_only_what_it_keeps(books):
    sale, payment = overpaid(books)
    paid = refund_overage(books, payment['id'])
    assert unapplied(books, payment['id']) == 0

    corrected = books['run']('customer-refund update', dict(
        refund=paid['id'], expected_version=paid['version'],
        sources=[{'payment': payment['id'], 'amount': '30.00'}]),
        reason='They only wanted 30.00 back')

    assert corrected['total']['minor_units'] == PART
    # Exactly one release of the whole 5000, and exactly one consumption of 3000.
    releases = [row for row in corrected['consumptions'] if row['kind'] == 'release']
    consumed = [row for row in corrected['consumptions'] if row['kind'] == 'consume']
    assert [row['amount_minor_units'] for row in releases] == [OVERAGE]
    assert sorted(row['amount_minor_units'] for row in consumed) == [PART, OVERAGE]
    assert all(row['payment_id'] == payment['id'] for row in corrected['consumptions'])
    # The receipt is worth 5000 - 3000, never 5000 twice and never 5000 - 5000 - 3000.
    assert unapplied(books, payment['id']) == OVERAGE - PART
    after, _ = balances(books)
    assert after[books['receivable']] == -(OVERAGE - PART)
    assert after[books['checking']] == CHEQUE - PART
    assert after[books['income']] == -INVOICE


def test_voiding_the_refund_gives_the_whole_overage_back(books):
    sale, payment = overpaid(books)
    before, _ = balances(books)
    paid = refund_overage(books, payment['id'])

    books['run']('customer-refund void', dict(refund=paid['id'], expected_version=paid['version']),
                 reason='Sent in error')

    assert unapplied(books, payment['id']) == OVERAGE
    after, _ = balances(books)
    assert after == before


# ---------------------------------------------------------------- refusals that name the blocker


def test_a_voided_payment_holds_nothing_to_refund_and_says_so(books):
    sale, payment = overpaid(books)
    books['run']('payment unapply', dict(
        payment=payment['id'], expected_version=payment['version'], operation_key='take-it-off',
        applications=[{'application_id': application_id(books, payment['id']),
                       'invoice_expected_version': books['run'](
                           'invoice show', {'invoice': sale['id']})['version']}]),
        reason='Unapply before voiding')
    books['run']('payment void', dict(payment=payment['id'], expected_version=books['run'](
        'payment show', {'payment': payment['id']})['version'], operation_key='void-it'),
        reason='Cheque bounced')
    frozen = whole_database(books)

    with pytest.raises(BookflowError) as refused:
        refund_overage(books, payment['id'])
    assert refused.value.code == 'E_APPLICATION_INACTIVE'
    assert refused.value.details['payment_id'] == payment['id']
    assert refused.value.details['status'] == 'voided'
    assert whole_database(books) == frozen


def test_a_payment_a_refund_still_stands_on_cannot_be_voided(books):
    sale, payment = overpaid(books)
    paid = refund_overage(books, payment['id'])
    books['run']('payment unapply', dict(
        payment=payment['id'], expected_version=books['run'](
            'payment show', {'payment': payment['id']})['version'], operation_key='take-it-off',
        applications=[{'application_id': application_id(books, payment['id']),
                       'invoice_expected_version': books['run'](
                           'invoice show', {'invoice': sale['id']})['version']}]),
        reason='Unapply before voiding')
    frozen = whole_database(books)

    with pytest.raises(BookflowError) as refused:
        books['run']('payment void', dict(payment=payment['id'], expected_version=books['run'](
            'payment show', {'payment': payment['id']})['version'], operation_key='void-it'),
            reason='Cheque bounced')
    assert refused.value.code == 'E_HAS_REFUND'
    assert refused.value.details['payment_id'] == payment['id']
    assert refused.value.details['refund_ids'] == [paid['id']]
    assert whole_database(books) == frozen


def test_a_payment_cannot_be_corrected_below_what_has_already_been_refunded(books):
    sale, payment = overpaid(books)
    refund_overage(books, payment['id'])
    frozen = whole_database(books)

    current = books['run']('payment show', {'payment': payment['id']})
    with pytest.raises(BookflowError) as refused:
        books['run']('payment update', dict(
            payment=payment['id'], expected_version=current['version'],
            operation_key='shrink-it', amount='120.00',
            settlement_guard=current['settlement_guard']),
            reason='Try to shrink the cheque under the refund')
    # By hand: 120.00 of capacity against 100.00 applied plus 50.00 already handed back is
    # 30.00 short, so the smallest this cheque may now be corrected to is 150.00.
    assert refused.value.code == 'E_APPLIED_EXCEEDS_TOTAL'
    assert refused.value.details['minimum_minor_units'] == INVOICE + OVERAGE == CHEQUE
    assert whole_database(books) == frozen


def test_a_refund_of_an_overage_cannot_be_dated_into_a_closed_period(books):
    sale, payment = overpaid(books)
    books['run']('company update', {'closing_date': '2026-03-31'}, reason='Close the month')
    frozen = whole_database(books)

    with pytest.raises(BookflowError) as refused:
        refund_overage(books, payment['id'], date='2026-03-20')
    assert refused.value.code == 'E_PERIOD_CLOSED'
    assert whole_database(books) == frozen


def test_a_refund_cannot_be_dated_before_the_payment_it_pays_back(books):
    sale, payment = overpaid(books)
    with pytest.raises(BookflowError) as refused:
        refund_overage(books, payment['id'], date='2026-03-04')
    assert refused.value.code == 'E_VALIDATION'
    assert refused.value.details['fields'][0]['field'] == 'date'


def test_a_source_names_one_document_and_the_guard_catches_the_wrong_customer(books):
    sale, payment = overpaid(books)
    with pytest.raises(BookflowError) as both:
        books['run']('customer-refund post', dict(
            date='2026-03-20', funding_account=books['checking'], method=books['method'],
            sources=[{'payment': payment['id'], 'credit_memo': payment['id']}]),
            reason='Two sources on one row')
    assert both.value.code == 'E_VALIDATION'

    with pytest.raises(BookflowError) as wrong:
        refund_overage(books, payment['id'], customer=books['kerr'])
    assert wrong.value.code == 'E_VALIDATION'
    assert wrong.value.details['fields'][0]['field'] == 'customer'


# ---------------------------------------------------------------- both kinds in one refund


def test_a_credit_memo_and_an_overpayment_are_paid_back_by_one_refund(books):
    sale, payment = overpaid(books)
    credit = goodwill_credit(books, '20.00', date='2026-03-10')

    paid = books['run']('customer-refund post', dict(
        date='2026-03-20', funding_account=books['checking'], method=books['method'],
        sources=[{'credit_memo': credit['id']}, {'payment': payment['id']}]),
        reason='Clear the customer out')

    assert paid['total']['minor_units'] == OVERAGE + 2000
    kinds = {('credit' if row['credit_memo_id'] else 'payment') for row in paid['consumptions']}
    assert kinds == {'credit', 'payment'}
    assert unapplied(books, payment['id']) == 0
    assert books['run']('credit-memo show', {'credit_memo': credit['id']}
                        )['source_current']['available_minor_units'] == 0
    after, _ = balances(books)
    assert after.get(books['receivable'], 0) == 0
    assert after[books['checking']] == CHEQUE - OVERAGE - 2000
