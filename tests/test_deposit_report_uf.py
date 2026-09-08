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

from tests.test_deposit_draft_financial import run_private, make_posted, financial
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale, COMPANY


def test_real_never_deposited_then_consumed_bridge_and_print(client,cash,run_private):
    from bookflow.company import deposit_report_uf as uf, deposit_reports as report, deposit_report_models as m, deposit_report_print as printing
    from bookflow.core.publication import OSBinding
    period=m.DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30')
    def before(s,ctx):
        raw=tuple(s.company.raw.iterdump())
        value=uf.uf_bridge(s,period,binding=OSBinding.from_session(s))
        assert value.state=='complete'
        assert value.data.closing.model_dump()==dict(receipts=6000,deposited=0,source_backed=6000,ledger=6000,unexplained=0)
        assert tuple(s.company.raw.iterdump())==raw
    run_private(before)
    posted,draft=make_posted(client,cash,run_private)
    def after(s,ctx):
        raw=tuple(s.company.raw.iterdump());binding=OSBinding.from_session(s)
        inp=m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',include_uf_bridge=True,limit=1)
        result=report.detail(s,inp,binding=binding)
        assert result.totals.composition.bank_total==6000
        assert result.uf.data.closing.model_dump()==dict(receipts=6000,deposited=6000,source_backed=0,ledger=0,unexplained=0)
        assert result.items[0].movement.transaction_id==posted.current.id
        assert {l.id for l in result.items[0].links if l.kind=='draft'}=={draft.id}
        p=printing.print_data(s,m.DepositDetailFilter(**inp.model_dump(exclude={'limit','cursor'},exclude_unset=True)),binding=binding)
        assert p.rows==result.items and p.totals==result.totals and p.uf==result.uf
        assert p.compositions[0].rows[0].captured.source.transaction_id==cash['source']
        assert p.metadata.snapshot_reference==result.metadata.snapshot_reference
        assert tuple(s.company.raw.iterdump())==raw
    run_private(after)


def test_real_payment_cash_history_replacement_direct_bank_void_and_inactive(client,sale,run_private):
    from tests.test_deposit_sources import uf
    from tests.test_payment_receipts import method
    from bookflow.company.deposit_cash_history import load_cash_history
    from bookflow.company import deposit_report_uf as report
    from bookflow.core.publication import OSBinding
    account=uf(client);bank=client.account.create(name='Historical direct bank',type='bank',company=COMPANY)['id']
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-05',amount='10',deposit_to=account,payment_method=method(client),operation_key='history-cash'),company=COMPANY)
    for version,patch in [(1,dict(amount='12',date='2026-06-08')),(2,dict(deposit_to=bank,date='2026-06-09'))]:
        client.run('payment update',dict(payment=paid['id'],expected_version=version,operation_key='history-change-'+str(version),**patch),company=COMPANY,reason='Historical cash witness')
    client.run('payment void',dict(payment=paid['id'],expected_version=3,operation_key='history-void'),company=COMPANY,reason='Cancel direct remittance')
    client.run('customer deactivate',dict(customer=sale['customer']),company=COMPANY)
    def read(s,ctx):
        raw=tuple(s.company.raw.iterdump());b=OSBinding.from_session(s)
        history=load_cash_history(s,[paid['id']],binding=b,uf_account=account)[0]
        assert history.cash_account_ids==(account,account,bank) and len(history.revision_ids)==3
        assert sorted((e.effective_date,e.units) for e in history.uf_effects)==[('2026-06-05',-1000),('2026-06-05',1000),('2026-06-08',-1200),('2026-06-08',1200)]
        for day in ('2026-06-07','2026-06-08','2026-06-09'):
            result=report.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to=day),binding=b)
            assert result.state=='complete' and result.data.closing.source_backed==result.data.closing.ledger==0
        assert tuple(s.company.raw.iterdump())==raw
    run_private(read)


def test_real_uf_section_denial_io_and_no_account_are_distinct(client,sale,run_private,monkeypatch):
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_deposit_sources import uf
    from tests.test_payment_receipts import method
    from bookflow.company import deposit_reports as report,deposit_report_models as m,deposit_report_uf as u,deposit_read_authority as authority
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale)
    financial(run_private,dict(operation_key='entitled-report',document=doc))
    financial(run_private,dict(operation_key='entitled-report-two',document=doc))
    secret=client.run('payment receive',dict(customer=sale['customer'],amount='4',date='2026-06-02',deposit_to=uf(client),payment_method=method(client),operation_key='uf-private-source'),company=COMPANY)['id']
    original=authority.admit;seen=[]
    def denied(s,ids,*,binding):
        ids=tuple(ids);seen.append(ids)
        if secret in ids:raise BookflowError('E_PERMISSION')
        return original(s,ids,binding=binding)
    # Existing authority owner fault only; actual Session/base admission still run.
    with monkeypatch.context() as patch:
        patch.setattr(authority,'admit',denied)
        def read(s,ctx):
            b=OSBinding.from_session(s)
            plain=report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',limit=1),binding=b)
            assert plain.uf.reason=='not_requested' and all(secret not in ids for ids in seen)
            included=report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',include_uf_bridge=True,limit=1),binding=b)
            assert included.uf.model_dump()==dict(state='unavailable',reason='not_authorized')
            assert included.items==plain.items and included.totals==plain.totals
            assert secret not in included.model_dump_json()
            return included
        unavailable=run_private(read)
        client.run('payment update',dict(payment=secret,expected_version=1,operation_key='still-denied-update',amount='6'),company=COMPANY,reason='Change hidden section only')
        repeated=run_private(lambda s,ctx:report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',include_uf_bridge=True,limit=1),binding=OSBinding.from_session(s)))
        assert repeated.next_cursor==unavailable.next_cursor and repeated.metadata.snapshot_reference==unavailable.metadata.snapshot_reference
    with pytest.raises(BookflowError,match='E_QUERY_STALE'):
        run_private(lambda s,ctx:report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',include_uf_bridge=True,limit=1,cursor=unavailable.next_cursor),binding=OSBinding.from_session(s)))
    def missing(s,ctx):
        b=OSBinding.from_session(s);s.company.raw.execute('SAVEPOINT report_no_role')
        try:
            s.company.raw.execute("UPDATE accounts SET system_role=NULL WHERE system_role='undeposited_funds'")
            result=u.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=b)
            assert result.model_dump()==dict(state='unavailable',reason='no_uf_account')
        finally:
            s.company.raw.execute('ROLLBACK TO report_no_role');s.company.raw.execute('RELEASE report_no_role')
    run_private(missing)
    def fail(*args,**kwargs):raise OSError('actual reader fault')
    with monkeypatch.context() as patch:
        patch.setattr(authority,'select',fail)
        with pytest.raises(BookflowError,match='E_IO'):
            run_private(lambda s,ctx:u.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=OSBinding.from_session(s)))

from tests.test_deposit_lifecycle import driver

@pytest.mark.parametrize('mode',['date','full_cashback'])
def test_real_coordinate_dated_report_and_zero_main_bank(client,sale,driver,mode):
    from tests.test_deposit_sources import uf
    from tests.test_payment_receipts import method
    from tests.test_deposit_lifecycle import additional_document,replacement
    from tests.test_deposit_coordinate_persistence import prepare
    from bookflow.company import deposit_coordinate_persistence as persistence,deposit_report_uf as u,deposit_reports as report,deposit_report_models as m
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    from bookflow.core.context import Context,Interface
    from bookflow.core.publication import OSBinding
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-05',deposit_to=uf(client),payment_method=method(client),lines=[dict(item=sale['item'],quantity='1',unit_price='10')]),company=COMPANY)
    doc=additional_document(client,sale);doc.update(date='2026-06-06',additional=[],sources=[dict(source_type='sales_receipt',source=receipt['id'],expected_version=1)])
    posted=driver.run('post',dict(operation_key='report-date-post',document=doc))
    body=replacement(posted,doc);body['sources']=[dict(source_result=True,source=receipt['id'])]
    action=dict(sales_receipt=receipt['id'],expected_version=2)
    cashbank=None
    if mode=='date':action['date']='2026-06-08';body['date']='2026-06-09'
    else:
        cashbank=client.account.create(name='Cashback destination',type='bank',company=COMPANY)['id']
        body['cash_back']=dict(account=cashbank,amount='10')
    inp=CoordinateInput(deposit=posted.current.id,expected_version=1,operation_key='report-coordinate',source_action=dict(kind='sales_receipt_update',input=action),replacement=dict(mode='document',document=body))
    ctx=Context.new(Interface.python,'Report source witness',reason='Correct receipt date or cash disposition')
    with driver.session() as s:
        result=persistence.execute(s,ctx,prepare(s,ctx,inp));raw=tuple(s.company.raw.iterdump());b=OSBinding.from_session(s)
        if mode=='date':
            for day,expected in [('2026-06-07',0),('2026-06-08',1000),('2026-06-09',0)]:
                bridge=u.uf_bridge(s,m.DepositReportPeriod(date_from='2026-06-01',date_to=day),binding=b)
                assert bridge.data.closing.source_backed==bridge.data.closing.ledger==expected
        shown=report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30'),binding=b)
        assert shown.totals.row_count==3
        assert sorted(r.movement.composition.bank_total for r in shown.items)==([-1000,1000,1000] if mode=='date' else [-1000,0,1000])
        assert any(link.id==result.operation_id for link in shown.items[0].links if link.kind=='operation')
        if mode=='full_cashback':
            replacement_row=next(r for r in shown.items if r.movement.batch.kind=='replacement')
            assert [(r.role,r.account_id,r.signed_debit) for r in replacement_row.movement.bank_roles]==[('cash_back',cashbank,1000)]
        assert tuple(s.company.raw.iterdump())==raw


def test_real_uf_journal_changes_only_stale_requested_complete_bridge(client,sale,run_private):
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_deposit_sources import uf
    from bookflow.company import deposit_reports as report,deposit_report_models as m
    from bookflow.core.publication import OSBinding
    doc=additional_document(client,sale)
    for n in (1,2):financial(run_private,dict(operation_key='uf-cursor-'+str(n),document=doc))
    def read(s,ctx,include,cursor=None):
        return report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',limit=1,include_uf_bridge=include,cursor=cursor),binding=OSBinding.from_session(s))
    plain=run_private(lambda s,ctx:read(s,ctx,False));complete=run_private(lambda s,ctx:read(s,ctx,True))
    client.run('journal post',dict(date='2026-06-04',lines=[dict(account=uf(client),side='debit',amount='0.75'),dict(account=sale['income'],side='credit',amount='0.75')]),company=COMPANY)
    unchanged=run_private(lambda s,ctx:read(s,ctx,False,plain.next_cursor))
    assert unchanged.metadata.snapshot_reference==plain.metadata.snapshot_reference
    with pytest.raises(BookflowError,match='E_QUERY_STALE'):
        run_private(lambda s,ctx:read(s,ctx,True,complete.next_cursor))
    latest=run_private(lambda s,ctx:read(s,ctx,True))
    assert latest.uf.data.closing.unexplained-complete.uf.data.closing.unexplained==75


def test_real_void_release_redeposit_and_noeffect_do_not_duplicate_cash(client,cash,run_private):
    from tests.test_deposit_lifecycle import additional_document
    from bookflow.company import deposit_report_uf as u,deposit_reports as report,deposit_report_models as m
    from bookflow.core.publication import OSBinding
    posted,draft=make_posted(client,cash,run_private)
    canceled=financial(run_private,dict(operation_key='report-release',deposit=posted.current.id,expected_version=1),'void')
    def bridge(s,ctx):return u.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=OSBinding.from_session(s))
    assert run_private(bridge).data.closing.model_dump()==dict(receipts=6000,deposited=0,source_backed=6000,ledger=6000,unexplained=0)
    count=run_private(lambda s,ctx:s.company.raw.execute('SELECT count(*) FROM posting_batches').fetchone()[0])
    noop=financial(run_private,dict(operation_key='report-void-noeffect',deposit=posted.current.id,expected_version=canceled.current.version),'void')
    assert not noop.changed
    assert run_private(lambda s,ctx:s.company.raw.execute('SELECT count(*) FROM posting_batches').fetchone()[0])==count
    version=client.run('sales-receipt show',dict(sales_receipt=cash['source']),company=COMPANY)['version']
    bank=client.account.create(name='Rebank released source',type='bank',company=COMPANY)['id']
    second=financial(run_private,dict(operation_key='report-redeposit',document=dict(mode='inline',date='2026-06-04',deposit_to=bank,sources=[dict(source=cash['source'],source_type='sales_receipt',expected_version=version)])))
    result=run_private(bridge)
    assert result.data.closing.model_dump()==dict(receipts=6000,deposited=6000,source_backed=0,ledger=0,unexplained=0)
    def current(s,ctx):
        value=report.detail(s,m.DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',projection='current'),binding=OSBinding.from_session(s))
        assert value.totals.composition.bank_total==12000 and value.totals.effective_current_bank_total==6000
        assert value.totals.row_count==2
    run_private(current)


def test_cash_supplier_rejects_nonfinancial_loaded_row_corruption(client,cash,run_private,monkeypatch):
    from bookflow.company import deposit_read_authority as authority, schema as c, deposit_report_uf as u
    from bookflow.core.publication import OSBinding
    original=authority.select
    def damaged(s,table,column,ids):
        rows=original(s,table,column,ids)
        if table is c.posting_lines:
            rows=[dict(r,description='not the retained audit image') if r['transaction_id']==cash['source'] else r for r in rows]
        return rows
    monkeypatch.setattr(authority,'select',damaged)
    with pytest.raises(BookflowError,match='E_DEPOSIT_SOURCE_INVALID'):
        run_private(lambda s,ctx:u.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=OSBinding.from_session(s)))


def test_real_apply_unapply_and_closed_period_leave_cash_unchanged(client,sale,run_private):
    from tests.test_payment_receipts import method,posted
    from tests.test_deposit_sources import uf
    from bookflow.company import deposit_report_uf as u
    from bookflow.core.publication import OSBinding
    invoice=posted(client,sale['customer'],sale['item'],'4','Report settlement target')
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='6',deposit_to=uf(client),payment_method=method(client),operation_key='report-unapplied'),company=COMPANY)
    def check(s,ctx):
        value=u.uf_bridge(s,DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30'),binding=OSBinding.from_session(s))
        assert value.data.closing.model_dump()==dict(receipts=600,deposited=0,source_backed=600,ledger=600,unexplained=0)
        return value.data
    before=run_private(check)
    applied=client.run('payment apply',dict(payment=paid['id'],expected_version=1,date='2026-06-03',operation_key='report-apply',applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='4')])),company=COMPANY)
    assert run_private(check)==before
    client.run('payment unapply',dict(payment=paid['id'],expected_version=2,operation_key='report-unapply',applications=[dict(application_id=applied['effect']['applications'][0]['application_id'],invoice_expected_version=2)]),company=COMPANY,reason='Remove settlement only')
    client.company.update(closing_date='2026-06-30',company=COMPANY)
    assert run_private(check)==before


def test_real_negative_unexplained_difference_survives_detail_and_print(client,cash,sale,run_private):
    from tests.test_deposit_sources import uf as uf_account
    from bookflow.company import deposit_report_uf as bridge,deposit_reports as report,deposit_report_print as printing,deposit_report_models as m
    from bookflow.core.publication import OSBinding
    # A balanced journal reduces actual UF without changing the receipt-backed cash.
    client.run('journal post',dict(date='2026-06-04',lines=[
        dict(account=uf_account(client),side='credit',amount='0.75'),
        dict(account=sale['income'],side='debit',amount='0.75')]),company=COMPANY)
    def check(s,ctx):
        raw=tuple(s.company.raw.iterdump());binding=OSBinding.from_session(s)
        period=m.DepositReportPeriod(date_from='2026-06-01',date_to='2026-06-30')
        standalone=bridge.uf_bridge(s,period,binding=binding)
        detail=report.detail(s,m.DepositDetailInput(**period.model_dump(),include_uf_bridge=True),binding=binding)
        printed=printing.print_data(s,m.DepositDetailFilter(**period.model_dump(),include_uf_bridge=True),binding=binding)
        expected=dict(receipts=6000,deposited=0,source_backed=6000,ledger=5925,unexplained=-75)
        for value in (standalone,detail.uf,printed.uf):
            assert value.state=='complete' and value.data.closing.model_dump()==expected
        assert tuple(s.company.raw.iterdump())==raw
    run_private(check)
