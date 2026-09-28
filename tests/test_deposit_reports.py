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


@pytest.mark.parametrize('update', [dict(limit=True), dict(limit=0), dict(limit=201),
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

from tests.test_deposit_draft_financial import run_private, financial
from tests.test_service_sales_lifecycle import sale, COMPANY


def test_real_corrected_pages_offpage_freshness_and_void(client,sale,run_private):
    from tests.test_deposit_lifecycle import additional_document,replacement
    from bookflow.company import deposit_reports as report
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale,'10')
    first=financial(run_private,dict(operation_key='runtime-first',document=doc))
    second=financial(run_private,dict(operation_key='runtime-second',document=dict(doc,memo='second')))
    def read(s,ctx,**extra):
        return report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',limit=1,**extra),binding=OSBinding.from_session(s))
    page=run_private(read)
    assert page.totals.row_count==2 and page.totals.composition.bank_total==2000
    next_page=run_private(lambda s,ctx:read(s,ctx,cursor=page.next_cursor))
    assert {page.items[0].movement.transaction_id,next_page.items[0].movement.transaction_id}=={first.current.id,second.current.id}
    client.account.create(name='Unrelated report master',type='expense',company=COMPANY)
    stable=run_private(read)
    assert (stable.next_cursor,stable.metadata.snapshot_reference)==(page.next_cursor,page.metadata.snapshot_reference)
    bank=client.account.create(name='Report replacement bank',type='bank',company=COMPANY)['id']
    changed=replacement(second,dict(doc,memo='second'));changed['additional'][0]['amount']='12';changed['deposit_to']=bank
    updated=financial(run_private,dict(operation_key='runtime-correct',deposit=second.current.id,expected_version=1,document=changed),'update')
    with pytest.raises(BookflowError,match='E_QUERY_STALE'):
        run_private(lambda s,ctx:read(s,ctx,cursor=page.next_cursor))
    def amounts(s,ctx):
        b=OSBinding.from_session(s)
        old=report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',deposit_to=doc['deposit_to']),binding=b)
        new=report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',deposit_to=bank),binding=b)
        assert sorted(r.movement.composition.bank_total for r in old.items)==[-1000,1000,1000]
        assert old.totals.movement.closing.minor_units==1000 and new.totals.movement.closing.minor_units==1200
        assert new.items[0].movement.batch.replaces_batch_id==second.effect.batch_ids[0]
    run_private(amounts)
    financial(run_private,dict(operation_key='runtime-void',deposit=second.current.id,expected_version=updated.current.version),'void')
    current=run_private(lambda s,ctx:read(s,ctx,projection='current'))
    assert current.totals.composition.bank_total==2200 and current.totals.effective_current_bank_total==1000

from tests.test_deposit_dependency_binding import bound_people,observe,_credential


def test_real_readonly_actor_principal_report_and_current_revocation(root,client,sale,run_private,monkeypatch,bound_people):
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as hub
    from bookflow.core import clock
    from bookflow.company import deposit_reports as report,deposit_report_print as printing
    financial(run_private,dict(operation_key='report-authority',document=additional_document(client,sale)))
    people=bound_people
    with writer(root) as db:
        db.conn.execute(hub.memberships.update().where(hub.memberships.c.user_id.in_([people['agent'],people['first']])).values(role='readonly'))
    credential=_credential(client,people['agent'],people['first'])
    def read(s):
        before=(tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))
        inp=DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30')
        result=report.detail(s,inp,binding=credential)
        assert result.totals.composition.bank_total==1000
        assert printing.print_data(s,DepositDetailFilter(date_from=inp.date_from,date_to=inp.date_to),binding=credential).totals==result.totals
        assert before==(tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))
    observe(people['bot'],monkeypatch,read,people['company'])
    with writer(root) as db:
        db.conn.execute(hub.memberships.update().where(hub.memberships.c.user_id==people['first']).values(revoked_at=clock.now_iso()))
    with pytest.raises(BookflowError):
        observe(people['bot'],monkeypatch,read,people['company'])


def test_report_cursor_domain_keyless_and_entitled_loaded_corruption(client,sale,run_private,monkeypatch):
    from tests.test_deposit_lifecycle import additional_document
    from bookflow.company import deposit_reports as report,deposit_read_pages as pages
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale)
    a=financial(run_private,dict(operation_key='cursor-a',document=doc));financial(run_private,dict(operation_key='cursor-b',document=doc))
    def read(s,ctx):
        b=OSBinding.from_session(s);inp=DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',limit=1)
        first=report.detail(s,inp,binding=b)
        for token in (first.next_cursor[:-3]+'xxx',pages.encode(s,b,'query','0'*64,[None,1])):
            with pytest.raises(BookflowError,match='E_VALIDATION'):
                report.detail(s,inp.model_copy(update={'cursor':token}),binding=b)
        before=tuple(s.company.raw.iterdump())
        import sqlite3
        from bookflow.company import deposit_read_authority as authority, schema as c
        # Real storage rejects mutation first; retain that guard. Exercise the
        # reader against damaged loaded rows without dropping an immutable DDL.
        with pytest.raises(sqlite3.IntegrityError,match='immutable ledger history'):
            s.company.raw.execute('UPDATE posting_lines SET debit_minor_units=credit_minor_units,credit_minor_units=debit_minor_units WHERE transaction_id=?',(a.current.id,))
        original=authority.select
        def damaged(session,table,column,identities):
            rows=original(session,table,column,identities)
            if table is c.posting_lines:
                rows=[dict(r,debit_minor_units=r['credit_minor_units'],credit_minor_units=r['debit_minor_units']) if r['transaction_id']==a.current.id else r for r in rows]
            return rows
        with monkeypatch.context() as patch:
            patch.setattr(authority,'select',damaged)
            with pytest.raises(BookflowError,match='E_DEPOSIT_SOURCE_INVALID'):
                report.detail(s,inp,binding=b)
        s.company.raw.execute('SAVEPOINT keyless_report')
        try:
            s.company.raw.execute('DELETE FROM report_cursor_keys WHERE key_id=1')
            with pytest.raises(BookflowError,match='E_INTERNAL'):
                report.detail(s,inp,binding=b)
        finally:s.company.raw.execute('ROLLBACK TO keyless_report');s.company.raw.execute('RELEASE keyless_report')
        assert tuple(s.company.raw.iterdump())==before
    run_private(read)


def test_real_offpage_memo_stales_without_any_total_change(client,sale,run_private):
    from tests.test_deposit_lifecycle import additional_document,replacement
    from bookflow.company import deposit_reports as report
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale)
    values=[financial(run_private,dict(operation_key='offpage-'+str(n),document=doc)) for n in (1,2)]
    def read(s,ctx,cursor=None):return report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',projection='current',limit=1,cursor=cursor),binding=OSBinding.from_session(s))
    first=run_private(read)
    target=next(v for v in values if v.current.id!=first.items[0].movement.transaction_id)
    changed=replacement(target,doc);changed['memo']='New offpage captured memo only'
    financial(run_private,dict(operation_key='offpage-memo',deposit=target.current.id,expected_version=1,document=changed),'update')
    current=run_private(read)
    assert current.totals==first.totals
    with pytest.raises(BookflowError,match='E_QUERY_STALE'):
        run_private(lambda s,ctx:read(s,ctx,first.next_cursor))


def test_base_ledger_read_denial_cannot_become_optional_unavailable(client,sale,run_private,monkeypatch):
    from bookflow.company import deposit_reports as report,deposit_report_uf as uf, payment_authority
    from bookflow.company.deposit_report_models import DepositReportPeriod
    from bookflow.core.publication import OSBinding
    original=payment_authority.require_resource;seen=[]
    def gate(s,resource,role):
        seen.append(resource)
        if resource=='ledger.read':raise BookflowError('E_PERMISSION')
        return original(s,resource,role)
    monkeypatch.setattr(payment_authority,'require_resource',gate)
    def read(s,ctx):
        b=OSBinding.from_session(s)
        for operation in (
            lambda:report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',include_uf_bridge=True),binding=b),
            lambda:uf.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=b)):
            with pytest.raises(BookflowError,match='E_PERMISSION'):operation()
        assert seen and set(seen)=={'ledger.read'}
    run_private(read)


@pytest.mark.parametrize('kind',['bank','credit_card'])
def test_real_full_cashback_additional_bank_card_role(client,sale,run_private,kind):
    from tests.test_deposit_lifecycle import additional_document
    from bookflow.company import deposit_reports as report
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale,'10',cash='10')
    source=client.account.create(name='Additional '+kind,type=kind,company=COMPANY)['id']
    doc['additional'][0]['from_account']=source
    financial(run_private,dict(operation_key='report-role',document=doc))
    def read(s,ctx):
        value=report.detail(s,DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30'),binding=OSBinding.from_session(s))
        assert value.totals.composition.bank_total==0 and value.totals.composition.cash_back==1000
        assert [(r.role,r.account_id,r.signed_debit,r.statement_amount) for r in value.items[0].movement.bank_roles]==[('additional',source,-1000,1000 if kind=='credit_card' else -1000)]
        assert value.totals.account_roles[0].signed_debit.minor_units==-1000
    run_private(read)
