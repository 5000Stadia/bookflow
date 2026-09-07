"""Handwritten pure projection oracles; not supplier/authentication witnesses."""
import pytest
from pydantic import ValidationError

from bookflow.company.deposit_models import Account, Additional, CashBack, Dimensions, Intent
from bookflow.company.deposit_report_models import (
    DepositDetailFilter, DepositDetailInput, ReportBatch, ReportDeposit, ReportRevision,
)
from bookflow.company.deposit_report_print import selected_compositions
from bookflow.company.deposit_reports import aggregate
from bookflow.core.errors import BookflowError


def revision(identity='r1', *, deposit='d', bank='A', date='2026-06-03', positive=18000, negative=-300, cash=500):
    account = Account(id=bank, name=bank, full_name=bank, number=None, normal_balance='debit',
                      type='bank', system_role=None, active=True, currency='USD')
    dims = Dimensions(party_kind='customer', party_id='party', party_name='Party', class_id=None, class_name=None)
    income = account.model_copy(update={'id': 'income', 'type': 'income'})
    expense = account.model_copy(update={'id': 'expense', 'type': 'expense'})
    # Deliberately handwritten additional-only arithmetic, not the N1 source world.
    intent = Intent(deposit_id=deposit, date=date, currency='USD', bank=account, sources=(),
                    cash_back=CashBack(account=account.model_copy(update={'id': 'cash', 'type': 'other_current_asset'}), units=cash) if cash else None,
                    additional=(Additional(row_id='p', ordinal=1, account=income, units=positive, dimensions=dims),
                                Additional(row_id='n', ordinal=2, account=expense, units=negative, dimensions=dims)))
    from bookflow.company.deposits import prepare
    effect = prepare(intent)
    from bookflow.company.bank_effects import enumerate_deposit
    return ReportRevision(revision_id=identity, number='D1', effect=effect,
                          bank_effects=enumerate_deposit(effect, header_row_id='header'))


def world(*, status='posted', bank='B'):
    r1, r2 = revision(), revision('r2', bank=bank, date='2026-06-05', positive=20000)
    batches = (
        ReportBatch(id='z-original', revision_id='r1', kind='original', effective_date='2026-06-03'),
        ReportBatch(id='a-reversal', revision_id='r1', kind='reversal', effective_date='2026-06-05', reverses_batch_id='z-original'),
        ReportBatch(id='b-replacement', revision_id='r2', kind='replacement', effective_date='2026-06-05', replaces_batch_id='z-original'),
    )
    return (ReportDeposit(id='d', current_revision_id='r2', status=status, revisions=(r1, r2), batches=batches),)


def request(**kwargs):
    return DepositDetailFilter(date_from='2026-06-01', date_to='2026-06-30', **kwargs)


def test_current_effective_corrected_bank_filter_and_complete_print():
    facts = world()
    result = aggregate(facts, request(), currency='USD', destination_id=None)
    assert [r.composition.bank_total for r in result.rows] == [17200, -17200, 19200]
    assert [r.revision_id for r in result.rows] == ['r1', 'r1', 'r2']
    assert result.totals.composition.model_dump() == dict(source=0, positive_additional=20000,
        negative_additional=-300, posting_total=20000, subtotal=19700, bank_total=19200, cash_back=500)
    assert (result.totals.row_count, result.totals.deposit_count, result.totals.additional_count) == (3, 1, 6)
    assert [r.revision_id for r in selected_compositions(result, facts)] == ['r1', 'r1', 'r2']
    assert selected_compositions(result, facts)[1].effect.bank_total == 17200
    for bank, expected in [('A', [17200, -17200]), ('B', [19200])]:
        filtered = aggregate(facts, request(deposit_to=bank), currency='USD', destination_id=bank)
        assert [r.composition.bank_total for r in filtered.rows] == expected
        assert filtered.totals.movement.closing.minor_units == sum(expected)
        assert filtered.totals.movement.closing.population.destination_id == bank
    current = aggregate(facts, request(projection='current'), currency='USD', destination_id=None)
    assert len(current.rows) == 1 and current.rows[0].composition.bank_total == 19200
    assert current.rows[0].effective_current_bank_total == 19200
    assert current.totals.movement is None


def test_voided_business_total_distinct_from_effective_zero():
    d = world()[0]
    d = ReportDeposit(**{**d.model_dump(), 'status': 'voided', 'batches': d.batches + (
        ReportBatch(id='void', revision_id='r2', kind='reversal', effective_date='2026-06-06', reverses_batch_id='b-replacement'),)})
    current = aggregate((d,), request(projection='current'), currency='USD', destination_id=None)
    assert current.rows[0].composition.bank_total == 19200
    assert current.rows[0].effective_current_bank_total == 0
    effective = aggregate((d,), request(status='voided'), currency='USD', destination_id=None)
    assert effective.totals.composition.bank_total == 0
    for value in (effective.totals.movement.opening, effective.totals.movement.period, effective.totals.movement.closing):
        assert value.population.current_status_filter == 'voided'
        assert value.population.scope == 'selected_deposit_population'
        assert value.population.is_account_balance is False


def test_scoped_endpoints_binary_order_future_and_lower_date():
    inp = DepositDetailFilter(date_from='2026-06-05', date_to='2026-06-05')
    result = aggregate(world(), inp, currency='USD', destination_id=None)
    assert [r.batch.id for r in result.rows] == ['a-reversal', 'b-replacement']
    assert [getattr(result.totals.movement, k).minor_units for k in ('opening', 'period', 'closing')] == [17200, 2000, 19200]
    assert result.totals.movement.opening.population == result.totals.population
    early = aggregate(world(), DepositDetailFilter(date_from='0001-01-01', date_to='0001-01-01'), currency='USD', destination_id=None)
    assert early.rows == () and early.totals.movement.closing.minor_units == 0
    # All currently recorded future facts are included without consulting a clock.
    future = world()[0].model_copy(update={'batches': tuple(b.model_copy(update={'effective_date': '2099-01-01'}) for b in world()[0].batches)})
    result = aggregate((future,), DepositDetailFilter(date_from='2099-01-01', date_to='2099-01-01'), currency='USD', destination_id=None)
    assert result.totals.movement.closing.minor_units == 19200


@pytest.mark.parametrize('update', [dict(page_size=True), dict(page_size=0), dict(page_size=201),
    dict(include_uf_bridge=1), dict(status=None), dict(basis='cash'), dict(date_from='2026-07-01'),
    dict(date_to='2026-02-30'), dict(status='deleted', include_deleted=False)])
def test_strict_inputs(update):
    with pytest.raises(ValidationError):
        DepositDetailInput(**(dict(date_from='2026-06-01', date_to='2026-06-30') | update))


def test_deleted_omission_truth_and_pre_schema_gate():
    assert request().statuses() == ('posted', 'voided')
    assert request(status='posted', include_deleted=True).statuses() == ('posted',)
    assert request(status='voided', include_deleted=False).statuses() == ('voided',)
    for inp in (request(include_deleted=True), request(status='deleted'), request(status='deleted', include_deleted=True)):
        with pytest.raises(BookflowError, match='E_VALIDATION'):
            inp.statuses()


def test_conversion_bijections_and_cross_owner_rejected():
    facts = world()
    for broken in (
        facts + facts,
        (facts[0].model_copy(update={'current_revision_id': 'missing'}),),
        (facts[0].model_copy(update={'revisions': facts[0].revisions + facts[0].revisions}),),
        (facts[0].model_copy(update={'id': 'other'}),),
        (facts[0].model_copy(update={'batches': facts[0].batches[1:]}),),
    ):
        with pytest.raises(BookflowError, match='E_DEPOSIT_SOURCE_INVALID'):
            aggregate(broken, request(), currency='USD', destination_id=None)


def test_full_cashback_and_card_roles_do_not_become_main_bank():
    from bookflow.company.bank_effects import enumerate_deposit
    from bookflow.company.deposits import prepare
    original = revision(positive=1300, negative=-300, cash=1000)
    cash_bank = original.effect.intent.bank.model_copy(update={'id':'cash-bank'})
    intent = original.effect.intent.model_copy(update={'cash_back': CashBack(account=cash_bank, units=1000)})
    effect = prepare(intent)
    captured = original.model_copy(update={'effect': effect,
        'bank_effects': enumerate_deposit(effect, header_row_id='header')})
    doc = ReportDeposit(id='d', current_revision_id='r1', status='posted', revisions=(captured,),
                        batches=(ReportBatch(id='batch', revision_id='r1', kind='original', effective_date='2026-06-03'),))
    result = aggregate((doc,), request(), currency='USD', destination_id=None)
    assert result.rows[0].composition.bank_total == 0
    assert [(r.role,r.account_id,r.signed_debit,r.statement_amount) for r in result.rows[0].bank_roles] == [('cash_back','cash-bank',1000,1000)]
    assert result.totals.account_roles[0].signed_debit.minor_units == 1000
    assert result.totals.movement.closing.minor_units == 0
    # A card-funded positive additional row pays down neither main-bank history
    # nor its statement convention by accident: the cash credit increases debt.
    positive = intent.additional[0]
    card = positive.account.model_copy(update={'id':'card','type':'credit_card','normal_balance':'credit'})
    intent = intent.model_copy(update={'additional': (positive.model_copy(update={'account':card}), intent.additional[1])})
    effect = prepare(intent)
    captured = captured.model_copy(update={'effect':effect, 'bank_effects':enumerate_deposit(effect,header_row_id='header')})
    doc = doc.model_copy(update={'revisions':(captured,)})
    result = aggregate((doc,),request(),currency='USD',destination_id=None)
    assert [(r.account_id,r.signed_debit,r.statement_amount) for r in result.rows[0].bank_roles] == [('card',-1300,1300),('cash-bank',1000,1000)]
    assert result.totals.account_roles[0].statement_amount.minor_units == 1300


def test_report_exact_sum_cancels_before_range_check_and_rejects_output_overflow():
    from bookflow.core.exact import INT64_MAX
    r = revision(positive=INT64_MAX, negative=-1, cash=0)
    doc = ReportDeposit(id='d', current_revision_id='r1', status='posted', revisions=(r,), batches=(
        ReportBatch(id='a',revision_id='r1',kind='original',effective_date='2026-06-03'),
        ReportBatch(id='b',revision_id='r1',kind='replacement',effective_date='2026-06-03',replaces_batch_id='a'),
        ReportBatch(id='z',revision_id='r1',kind='reversal',effective_date='2026-06-03',reverses_batch_id='a'),
    ))
    # This is an arithmetic conversion fixture; real history ownership remains
    # the read supplier's independent invariant, not inferred from these models.
    result = aggregate((doc,),request(),currency='USD',destination_id=None)
    assert result.totals.composition.posting_total == INT64_MAX
    assert result.totals.composition.bank_total == INT64_MAX-1
    other_r = revision(deposit='other', positive=INT64_MAX,negative=-1,cash=0)
    other = ReportDeposit(id='other',current_revision_id='r1',status='posted',revisions=(other_r,),batches=())
    with pytest.raises(BookflowError, match='E_VALUE_RANGE'):
        aggregate((doc,other),request(projection='current'),currency='USD',destination_id=None)
