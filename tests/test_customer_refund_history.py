"""Refund history reads retained revisions/effects without moving the books."""
import pytest

from bookflow import BookflowError
from tests.credit_support import books, goodwill_credit, refund  # noqa: F401
from tests.test_customer_refund_overpayment import overpaid, refund_overage, whole_database
from tests.test_customer_refund_update import correct


def retained(books, source='credit'):
    if source == 'credit':
        credit = goodwill_credit(books, '40.00')
        paid = refund(books, credit['id'], amount='12.00', memo='Original <refund>')
        patch = {'credit_memo': credit['id'], 'amount': '20.00'}
    else:
        _, payment = overpaid(books)
        paid = refund_overage(books, payment['id'], amount='12.00', memo='Original <refund>')
        patch = {'payment': payment['id'], 'amount': '20.00'}
    updated = correct(books, paid, date='2026-03-22', sources=[patch], memo='Corrected refund',
                      reason='Twenty, not twelve')
    return paid, updated


@pytest.mark.parametrize('source', ['credit', 'payment'])
def test_history_retains_revisions_attribution_and_correction_void_effects(books, source):
    paid, updated = retained(books, source)
    before = whole_database(books)
    history = books['run']('customer-refund history', {'refund': paid['id']})
    assert whole_database(books) == before
    assert history['current_revision_id'] == updated['revision']['id']
    assert history['status'] == 'posted' and history['version'] == 2
    old, current = history['items']
    assert [r['revision_number'] for r in history['items']] == [1, 2]
    assert old['profile'] == paid['revision']['profile']
    assert current['profile'] == updated['revision']['profile']
    assert (old['total_minor_units'], current['total_minor_units']) == (1200, 2000)
    assert [(b['kind'], b['effective_date']) for b in old['batches']] == [
        ('original', '2026-03-20'), ('reversal', '2026-03-20')]
    assert [(b['kind'], b['effective_date']) for b in current['batches']] == [('replacement', '2026-03-22')]
    assert [(c['kind'], c['amount_minor_units']) for c in old['consumptions']] == [('consume', 1200), ('release', 1200)]
    assert old['consumptions'][1]['reverses_consumption_id'] == old['consumptions'][0]['id']
    assert [e['command'] for e in old['events']] == ['customer-refund post', 'customer-refund update']
    assert old['events'][-1]['id'] == current['audit_event_id']
    assert old['events'][-1]['reason'] == 'Twenty, not twelve'
    assert all(e['actor_id'] and e['actor_name'] and e['interface'] == 'python' for e in old['events'])
    assert books['run']('customer-refund show', {'refund': paid['id']})['revision']['id'] == current['id']
    books['run']('customer-refund void', {'refund': paid['id'], 'expected_version': 2}, reason='Payment cancelled')
    before = whole_database(books)
    voided = books['run']('customer-refund history', {'refund': paid['id']})
    assert whole_database(books) == before
    assert voided['status'] == 'voided' and voided['void_reason'] == 'Payment cancelled'
    assert voided['count'] == 2 and voided['current_revision_id'] == current['id']
    current = voided['items'][1]
    assert [(c['kind'], c['amount_minor_units']) for c in current['consumptions']] == [('consume', 2000), ('release', 2000)]
    assert current['batches'][-1]['id'] == voided['void_posting_batch_id']
    assert current['batches'][-1]['kind'] == 'reversal'
    assert current['events'][-1]['command'] == 'customer-refund void'
    assert current['events'][-1]['reason'] == 'Payment cancelled'
    assert all(b['debit_minor_units'] == b['credit_minor_units'] for row in voided['items'] for b in row['batches'])


def test_history_pages_are_bound_to_refund_and_restart_after_write(books):
    paid, updated = retained(books)
    run = books['run']
    first = run('customer-refund history', {'refund': paid['id'], 'limit': 1})
    second = run('customer-refund history', {'refund': paid['id'], 'limit': 1, 'cursor': first['next_cursor']})
    assert first['has_more'] and not second['has_more'] and second['next_cursor'] is None
    assert [r['revision_number'] for r in first['items'] + second['items']] == [1, 2]
    with pytest.raises(BookflowError) as error:
        run('customer-refund history', {'refund': 'other-refund', 'limit': 1, 'cursor': first['next_cursor']})
    assert error.value.code == 'E_VALIDATION'
    correct(books, updated, memo='Third revision')
    with pytest.raises(BookflowError) as error:
        run('customer-refund history', {'refund': paid['id'], 'limit': 1, 'cursor': first['next_cursor']})
    assert error.value.code == 'E_QUERY_STALE'
    with pytest.raises(BookflowError) as error:
        run('customer-refund history', {'refund': 'missing'})
    assert error.value.code == 'E_RECORD_NOT_FOUND'
    assert run('customer-refund history', {'refund': paid['id']})['count'] == 3
