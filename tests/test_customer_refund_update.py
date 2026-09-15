"""Correcting a customer refund, and the three states where it cannot be corrected.

Every figure asserted here is written out in full at the top of the module: a 100.00 invoice,
a 30.00 credit, and 12.00 of it refunded in cash and then corrected to 20.00. What the trial
balance and the A/R aging say at each step is checked against those figures through the real
report commands, never against the correction's own output.

The three things this file exists to prove:

**A correction moves the money and nothing else.** The superseded effect is reversed at its
own date and a replacement is posted at the corrected one, so the bank and the receivable end
at the corrected figures and income -- which a refund never touches -- does not move at all.

**A credit is still consumed once.** The old consumption is released and the corrected one
taken in the same write, so what the credit is worth is the corrected remainder and never the
sum of both, and a correction asking for more than that is refused with nothing written.

**What cannot be corrected says so.** A voided refund, a closed period, a stale version, a
missing reason, another customer's credit, and a refund a finished bank reconciliation is
holding: each refuses by name and writes nothing.
"""
import sqlite3

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.credit_support import (  # noqa: F401  (books is a fixture)
    balances, books, goodwill_credit, invoice, refund, ties_to_the_receivable, worth,
)

# The money. Everything below is arithmetic on these.
INVOICE = 10000         # 100.00, four units at 25.00
CREDIT = 3000           # 30.00, dated 2026-03-10
FIRST = 1200            # 12.00 refunded on 2026-03-20, the amount typed wrong
CORRECTED = 2000        # 20.00, what should have been paid


def snapshot(books_):
    with sqlite3.connect(books_['database']) as db:
        return list(db.iterdump())


def correct(books_, paid, reason='Wrong amount typed', **patch):
    return books_['run']('customer-refund update',
                         dict(refund=paid['id'], expected_version=paid['version'], **patch),
                         reason=reason)


def paid_twelve(books_):
    """A hundred-dollar invoice, a thirty-dollar credit, and twelve of it paid back in cash."""
    invoice(books_)
    credit = goodwill_credit(books_, '30.00')
    return credit, refund(books_, credit['id'], amount='12.00', memo='Cheque 4101',
                          check_number='4101')


def certify(books_, ending):
    """A finished bank reconciliation over everything that has cleared the checking account."""
    run = books_['run']
    opening = run('reconcile opening start', dict(
        operation_key=new_id(), account=books_['checking'], opening_date='2026-03-01',
        entered_balance='0', references=[],
        evidence=dict(format=1, statement_reference=None, entered_text='Opening zero')),
        reason='Adopt the opening balance')['draft']
    draft = run('reconcile start', dict(
        operation_key=new_id(), account=books_['checking'], statement_date='2026-03-31',
        ending_balance=ending, opening_draft_id=opening['id']), reason='Bank statement')['draft']
    rows = run('reconcile candidates', dict(draft=draft['id'], limit=200))['items']
    marked = run('reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=1,
        entries=[dict(movement=row['movement'], group_fingerprint=row['group_fingerprint'],
                      action='mark') for row in rows]), reason='Clear the refund')['draft']
    preview = run('reconcile preview', dict(draft=draft['id'], expected_version=marked['version']))
    return run('reconcile finish', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
        expected_facts_fingerprint=preview['expected_facts_fingerprint'],
        dependency_guard=preview['dependency_guard']), reason='Certify the statement')


# ---------------------------------------------------------------- the correction itself


def test_a_correction_reverses_its_own_effect_and_posts_the_corrected_one(books):
    credit, paid = paid_twelve(books)
    after_post, _ = balances(books)
    assert after_post[books['checking']] == -FIRST
    assert after_post[books['receivable']] == INVOICE - CREDIT + FIRST

    corrected = correct(books, paid, date='2026-03-22', memo='Twenty, not twelve',
                        sources=[dict(credit_memo=credit['id'], amount='20.00')])

    assert corrected['total_minor_units'] == CORRECTED
    assert corrected['version'] == paid['version'] + 1
    assert corrected['revision']['revision_number'] == 2
    assert corrected['revision']['supersedes_revision_id'] == paid['revision']['id']
    assert corrected['number'] == paid['number']
    assert sorted(corrected['changed_fields']) == ['date', 'memo', 'total_minor_units']
    # The revision's own effect is the replacement, posted where the correction says.
    assert [(batch['kind'], batch['effective_date']) for batch in corrected['revision']['batches']] == [
        ('replacement', '2026-03-22')]

    after, _ = balances(books)
    assert after[books['checking']] == -CORRECTED
    assert after[books['receivable']] == INVOICE - CREDIT + CORRECTED
    # Income is what the credit memo left it; a refund never reverses a sale a second time.
    assert after[books['income']] == after_post[books['income']] == -(INVOICE - CREDIT)
    ties_to_the_receivable(books)


def test_a_correction_releases_what_it_spent_and_takes_the_corrected_amount_once(books):
    credit, paid = paid_twelve(books)
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT - FIRST

    corrected = correct(books, paid, sources=[dict(credit_memo=credit['id'], amount='20.00')])

    current = worth(books, credit['id'])
    assert current['refunded_minor_units'] == CORRECTED
    assert current['available_minor_units'] == CREDIT - CORRECTED
    assert sorted((row['kind'], row['amount_minor_units']) for row in corrected['consumptions']) == [
        ('consume', FIRST), ('consume', CORRECTED), ('release', FIRST)]
    # And the released capacity really is spendable again: the rest applies to the invoice.
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT - CORRECTED


def test_a_header_only_correction_leaves_the_sources_and_the_money_exactly_as_they_were(books):
    credit, paid = paid_twelve(books)
    before, _ = balances(books)

    corrected = correct(books, paid, memo='Cheque reissued', reference='REF-9',
                        check_number='4102', reason='Rewrote the cheque')

    assert corrected['total_minor_units'] == FIRST
    assert corrected['memo'] == 'Cheque reissued' and corrected['reference'] == 'REF-9'
    assert corrected['check_number'] == '4102'
    assert corrected['date'] == paid['date'] and corrected['funding_account_id'] == paid['funding_account_id']
    captured = corrected['revision']['profile']['sources']
    assert [(row['credit_memo_id'], row['amount_minor_units']) for row in captured] == [
        (credit['id'], FIRST)]
    after, _ = balances(books)
    assert after == before
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT - FIRST


def test_clearing_a_field_nulls_it_while_the_rest_of_the_refund_stands(books):
    """`--clear` is the only way to say null, and it must not be read as "left out"."""
    credit, paid = paid_twelve(books)
    assert paid['memo'] and paid['check_number']

    cleared = correct(books, paid, memo=None, check_number=None, reference=None,
                      reason='Paid by transfer, not by cheque')

    assert cleared['memo'] is None and cleared['check_number'] is None
    assert cleared['reference'] is None
    assert cleared['total_minor_units'] == FIRST
    assert cleared['funding_account_id'] == paid['funding_account_id']
    assert cleared['payment_method_id'] == paid['payment_method_id']
    assert worth(books, credit['id'])['available_minor_units'] == CREDIT - FIRST


def test_a_superseded_revision_stays_readable_at_its_own_figures(books):
    credit, paid = paid_twelve(books)
    correct(books, paid, date='2026-03-22', memo='Twenty, not twelve',
            sources=[dict(credit_memo=credit['id'], amount='20.00')])

    old = books['run']('customer-refund show', dict(refund=paid['id'], revision_number=1))
    assert old['revision']['total_minor_units'] == FIRST
    assert old['revision']['date'] == '2026-03-20' and old['revision']['revision_number'] == 1
    assert old['revision']['profile'] == paid['revision']['profile']
    current = books['run']('customer-refund show', dict(refund=paid['id']))
    assert current['revision']['revision_number'] == 2
    assert current['total_minor_units'] == CORRECTED
    # The document is still one refund in the list, at its one number.
    page = books['run']('customer-refund query', {'limit': 50})
    assert page['count'] == 1 and page['items'][0]['total_minor_units'] == CORRECTED


def test_an_empty_patch_writes_nothing_and_says_so(books):
    credit, paid = paid_twelve(books)
    before = snapshot(books)

    unchanged = books['run']('customer-refund update', dict(refund=paid['id']))

    assert unchanged['changed'] is False and unchanged['version'] == paid['version']
    assert unchanged['revision']['revision_number'] == 1
    assert snapshot(books) == before
    # And so does a patch that supplies exactly what is already there.
    repeated = books['run']('customer-refund update', dict(
        refund=paid['id'], expected_version=paid['version'], memo=paid['memo'],
        date=paid['date'], sources=[dict(credit_memo=credit['id'], amount='12.00')]))
    assert repeated['changed'] is False and snapshot(books) == before


def test_one_idempotency_key_corrects_once_however_often_it_is_retried(books):
    credit, paid = paid_twelve(books)
    request = dict(refund=paid['id'], expected_version=paid['version'], memo='Corrected note')
    result = books['run']('customer-refund update', request, idempotency_key='refund-correction',
                          reason='Correct the note')
    after = snapshot(books)

    replay = books['run']('customer-refund update', request, idempotency_key='refund-correction',
                          reason='Correct the note')

    assert replay['revision']['id'] == result['revision']['id']
    assert snapshot(books) == after
    assert books['run']('customer-refund show', dict(refund=paid['id']))['version'] == result['version']


# ---------------------------------------------------------------- what refuses, and why


def test_correcting_past_what_the_credit_is_worth_is_refused_and_writes_nothing(books):
    credit, paid = paid_twelve(books)
    before = snapshot(books)

    with pytest.raises(BookflowError) as refused:
        correct(books, paid, sources=[dict(credit_memo=credit['id'], amount='40.00')])

    assert refused.value.code == 'E_CREDIT_UNAVAILABLE'
    # What it may take is what the books have plus exactly what it is handing back.
    assert refused.value.details['available']['minor_units'] == CREDIT
    assert snapshot(books) == before


def test_a_correction_needs_a_reason_and_a_current_version(books):
    credit, paid = paid_twelve(books)
    before = snapshot(books)
    with pytest.raises(BookflowError) as no_reason:
        books['run']('customer-refund update',
                     dict(refund=paid['id'], expected_version=paid['version'], memo='No reason'))
    assert no_reason.value.code == 'E_REASON_REQUIRED'
    assert snapshot(books) == before

    correct(books, paid, memo='Corrected note')

    with pytest.raises(BookflowError) as stale:
        correct(books, paid, memo='Stale correction')
    assert stale.value.code == 'E_VERSION_CONFLICT'


def test_a_voided_refund_is_written_again_rather_than_corrected(books):
    credit, paid = paid_twelve(books)
    books['run']('customer-refund void', dict(refund=paid['id'], expected_version=paid['version']),
                 reason='Wrong bank account')
    before = snapshot(books)

    with pytest.raises(BookflowError) as refused:
        books['run']('customer-refund update', dict(refund=paid['id'], memo='After the void'),
                     reason='Correct it anyway')

    assert refused.value.code == 'E_APPLICATION_INACTIVE'
    assert refused.value.details['status'] == 'voided'
    assert snapshot(books) == before


@pytest.mark.parametrize('closing', ['2026-03-21', '2026-03-31'])
def test_a_correction_needs_both_its_dates_open(books, closing):
    """The date being reversed and the date being posted to, whichever of them is closed."""
    credit, paid = paid_twelve(books)
    books['run']('company update', dict(closing_date=closing), reason='Close the period')
    before = snapshot(books)

    with pytest.raises(BookflowError) as refused:
        correct(books, paid, date='2026-03-25', memo='Too late')

    assert refused.value.code == 'E_PERIOD_CLOSED'
    assert snapshot(books) == before


def test_a_correction_cannot_pay_back_a_different_customer(books):
    credit, paid = paid_twelve(books)
    other = goodwill_credit(books, '30.00', customer=books['kerr'])
    before = snapshot(books)

    with pytest.raises(BookflowError) as refused:
        correct(books, paid, sources=[dict(credit_memo=other['id'], amount='12.00')])

    assert refused.value.code == 'E_APPLICATION_INCOMPATIBLE'
    assert refused.value.details['reason'] == 'customer_refund_ownership'
    assert snapshot(books) == before


def test_a_reconciled_refund_is_not_moved_under_the_statement_that_cleared_it(books):
    credit, paid = paid_twelve(books)
    certified = certify(books, '-12.00')
    assert certified['totals']['difference'] == 0
    before = snapshot(books)

    with pytest.raises(BookflowError) as refused:
        correct(books, paid, memo='After the statement')

    assert refused.value.code == 'E_RECONCILIATION_DEPENDENCY'
    assert refused.value.details['refund_id'] == paid['id']
    assert 'reconciliation' in refused.value.details['next']
    assert snapshot(books) == before
