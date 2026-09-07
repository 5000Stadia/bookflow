"""Independent signed-stream UF equations, not SQL-supplier proof."""
import pytest
from pydantic import TypeAdapter, ValidationError
from bookflow.company.deposit_report_models import DepositReportPeriod, UFEvent, UFBridgeSection
from bookflow.company.deposit_report_uf import bridge
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def event(identity, amount, day='2026-06-01'):
    return UFEvent(identity=identity, transaction_id='t', batch_id='b', effective_date=day, units=amount)


def run(receipts=(), memberships=(), ledger=(), start='2026-06-01', end='2026-06-30'):
    return bridge(currency='USD', account_id='UF', period=DepositReportPeriod(date_from=start, date_to=end),
                  receipts=receipts, memberships=memberships, ledger=ledger)


def test_whole_sources_and_unrelated_journal_difference():
    r = (event('receipt600', 600), event('receipt400', 400))
    d = (event('whole600', 600, '2026-06-02'),)
    ledger = (event('cash600', 600), event('cash400', 400), event('deposit600', -600, '2026-06-02'),
              event('journal', 75, '2026-06-03'))
    value = run(r, d, ledger)
    assert value.closing.model_dump() == dict(receipts=1000, deposited=600, source_backed=400, ledger=475, unexplained=75)
    assert value.opening.model_dump() == dict(receipts=0, deposited=0, source_backed=0, ledger=0, unexplained=0)
    assert (value.receipt_effect_count, value.membership_effect_count, value.ledger_line_count) == (2, 1, 4)
    reversed_value = run(r, d, ledger + (event('reverse-journal', -75, '2026-06-04'),))
    assert reversed_value.closing.unexplained == 0
    assert value.scope == 'company_uf' and value.deposit_filters_applied is False


def test_claim_release_redeposit_and_signed_negative_not_clipped():
    r = (event('receipt', 700),)
    d = (event('claim', 700, '2026-06-02'), event('release', -700, '2026-06-03'), event('new-claim', 700, '2026-06-04'))
    ledger = (event('receipt-line', 700), event('claim-line', -700, '2026-06-02'),
              event('release-line', 700, '2026-06-03'), event('new-line', -700, '2026-06-04'))
    for day, expected in [('02', 0), ('03', 700), ('04', 0)]:
        value = run(r, d, ledger, start='2026-06-03', end='2026-06-'+day) if day != '02' else run(r,d,ledger,end='2026-06-02')
        assert value.closing.source_backed == value.closing.ledger == expected
        for key in type(value.closing).model_fields:
            assert getattr(value.opening, key) + getattr(value.change, key) == getattr(value.closing, key)
    assert run((), (event('claim', 5),), ()).closing.source_backed == -5


def test_corrected_dates_not_recorded_time_and_earliest_endpoint():
    # Original receipt June5 and deposit June6, each canceled at its original
    # accounting date; replacements June8/9. These are signed owner facts.
    r = (event('r',1000,'2026-06-05'),event('r-inverse',-1000,'2026-06-05'),event('r-new',1000,'2026-06-08'))
    d = (event('d',1000,'2026-06-06'),event('d-inverse',-1000,'2026-06-06'),event('d-new',1000,'2026-06-09'))
    ledger = r + tuple(e.model_copy(update={'identity':'line-'+e.identity,'units':-e.units}) for e in d)
    for day, expected in [('07',0),('08',1000),('09',0)]:
        value = run(r,d,ledger,end='2026-06-'+day)
        assert (value.closing.source_backed, value.closing.ledger, value.closing.unexplained) == (expected,expected,0)
    assert run(r,d,ledger,start='0001-01-01',end='0001-01-01').opening.receipts == 0


def test_exact_intermediate_cancellation_overflow_and_duplicate_identity():
    events = (event('a',INT64_MAX),event('b',INT64_MAX),event('c',-INT64_MAX))
    assert run(events,(),events).closing.receipts == INT64_MAX
    with pytest.raises(BookflowError, match='E_VALUE_RANGE'):
        run(events[:2],(),events[:2])
    with pytest.raises(BookflowError, match='E_DEPOSIT_SOURCE_INVALID'):
        run(events + events)


@pytest.mark.parametrize('reason', ['not_requested','not_authorized','no_uf_account'])
def test_unavailable_section_cannot_contain_amounts_or_ids(reason):
    adapter = TypeAdapter(UFBridgeSection)
    assert adapter.validate_python(dict(state='unavailable',reason=reason)).model_dump() == dict(state='unavailable',reason=reason)
    with pytest.raises(ValidationError):
        adapter.validate_python(dict(state='unavailable',reason=reason,account_id='secret',amount=0))
