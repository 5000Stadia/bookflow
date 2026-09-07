"""Actual private reads in dispatch-owned Session; no public route substitute."""
import json
import pytest
from bookflow.company import deposit_queries as q, deposit_print_data as printing, deposit_read_models as m
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from tests.test_deposit_draft_financial import run_private, financial, make_posted
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale,COMPANY


def test_show_selected_current_and_dated(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        b=OSBinding.from_session(s);before=tuple(s.company.raw.iterdump())
        result=q.show(s,m.ShowInput(deposit=posted.current.id,as_of='2026-06-02'),binding=b)
        assert result.totals.bank_total.minor_units==6000
        assert result.dated_state.bank_movement.minor_units==0
        assert result.dated_state.financial_state=='not_effective'
        assert result.selected.pin.revision_number==1 and result.current.version==1
        assert result.selected.custom_fields==()
        after=q.show(s,m.ShowInput(deposit=posted.current.id,as_of='2099-06-03'),binding=b)
        assert after.dated_state.bank_movement.minor_units==6000 and after.dated_state.cutoff_after_evaluation_date
        assert after.dated_state.financial_state=='effective'
        assert tuple(s.company.raw.iterdump())==before
    run_private(read)


def test_items_complete_identity_and_print(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        b=OSBinding.from_session(s);p=printing.print_data(s,m.PrintDataInput(deposit=posted.current.id),binding=b)
        assert len(p.rows)==1 and p.rows[0].captured.source.transaction_id==cash['source']
        for limit in (1,25,200):
            for kind in ('sources','additional','cash_allocations'):
                rows=[];cursor=None
                while True:
                    page=q.items(s,m.ItemsInput(deposit=posted.current.id,kind=kind,page=m.PageInput(limit=limit,cursor=cursor)),binding=b)
                    rows.extend(page.items);cursor=page.next_cursor
                    if cursor is None:break
                expected=p.rows if kind=='sources' else () if kind=='additional' else p.cash_allocations
                assert tuple(rows)==expected
                assert page.total_count==len(expected)
        assert p.document.totals.source_total.minor_units==6000
    run_private(read)


def test_query_filters_counts_ties_and_freshness(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        b=OSBinding.from_session(s)
        for status in ('posted','voided',None):
            for explicit in (False,True):
                inp=m.QueryInput(**({'status':status} if status else {}),include_deleted=explicit)
                if status is None and explicit:
                    with pytest.raises(BookflowError) as err:q.query(s,inp,binding=b)
                    assert err.value.code=='E_VALIDATION';continue
                result=q.query(s,inp,binding=b)
                assert result.total_count==(0 if status=='voided' else 1)
        assert q.query(s,m.QueryInput(number=posted.current.number,date_from='2026-06-03',date_to='2026-06-03'),binding=b).total_count==1
        assert q.query(s,m.QueryInput(number='absent'),binding=b).total_count==0
        for payload in ({'status':'deleted'},{'include_deleted':True}):
            with pytest.raises(BookflowError) as err:q.query(s,m.QueryInput(**payload),binding=b)
            assert err.value.code=='E_VALIDATION' and err.value.details['reason']=='feature_unavailable'
    run_private(read)


def test_register_complete_relation_and_relevant_continuation(client,sale,run_private):
    from tests.test_deposit_lifecycle import additional_document,replacement
    docs=[additional_document(client,sale)]
    docs.append(dict(docs[0],memo='second retained memo'))
    values=[financial(run_private,dict(operation_key='read-register-'+str(n),document=d)) for n,d in enumerate(docs)]
    ids=sorted(v.current.id for v in values)
    def read(s,ctx,direction='asc',cursor=None):
        return q.query(s,m.QueryInput(direction=direction,page=m.PageInput(limit=1,cursor=cursor)),binding=OSBinding.from_session(s))
    first=run_private(read)
    assert first.total_count==2 and first.totals.bank_total.minor_units==2000 and first.items[0].current.id==ids[0]
    second=run_private(lambda s,ctx:read(s,ctx,cursor=first.next_cursor))
    assert second.items[0].current.id==ids[1] and second.previous_cursor
    assert run_private(lambda s,ctx:read(s,ctx,'desc')).items[0].current.id==ids[1]
    client.account.create(name='Unrelated current master',type='expense',company=COMPANY)
    stable=run_private(read)
    assert (stable.next_cursor,stable.fingerprint)==(first.next_cursor,first.fingerprint)
    off=next((n,v) for n,v in enumerate(values) if v.current.id==ids[1]);n,target=off
    changed=replacement(target,docs[n]);changed['memo']='changed off-page'
    financial(run_private,dict(operation_key='read-register-change',deposit=target.current.id,expected_version=1,document=changed),'update')
    with pytest.raises(BookflowError) as err:run_private(lambda s,ctx:read(s,ctx,cursor=first.next_cursor))
    assert err.value.code=='E_QUERY_STALE'
    def filtered(s,ctx):
        b=OSBinding.from_session(s)
        assert q.query(s,m.QueryInput(q='CHANGED OFF-PAGE',date_from=docs[0]['date'],date_to=docs[0]['date'],deposit_to=docs[0]['deposit_to']),binding=b).total_count==1
        assert q.query(s,m.QueryInput(q='CHANGED OFF-PAGE',number=values[0 if n else 1].current.number),binding=b).total_count==0
    run_private(filtered)


def test_split_and_zero_presence_survive_stored_print(client,sale,run_private):
    from tests.test_deposit_sources import uf
    from tests.test_payment_receipts import method
    from tests.test_deposit_lifecycle import additional_document
    classes=[client.run('class create',dict(name='Read split '+str(n)),company=COMPANY)['id'] for n in (1,2)]
    source=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=uf(client),payment_method=method(client),date='2026-06-02',lines=[dict(item=sale['item'],quantity='1',unit_price=price,class_id=cls) for price,cls in [('20',classes[0]),('40',classes[1]),('0',classes[0])]]),company=COMPANY)
    doc=additional_document(client,sale)
    doc['sources']=[dict(source=source['id'],source_type='sales_receipt',expected_version=source['version'])]
    posted=financial(run_private,dict(operation_key='read-split',document=doc))
    def read(s,ctx):
        b=OSBinding.from_session(s)
        result=printing.print_data(s,m.PrintDataInput(deposit=posted.current.id),binding=b)
        row=next(x for x in result.rows if isinstance(x,m.SourceItem))
        assert sorted(x.capacity for x in row.captured.source.components)==[2000,4000]
        assert {x.dimensions.class_id for x in row.captured.source.components}==set(classes)
        assert len(row.captured.occurrences)==3 and sum(x.present for x in row.captured.occurrences)==2
        for limit in (1,25,200):
            collected=[];cursor=None
            while True:
                page=q.items(s,m.ItemsInput(deposit=posted.current.id,kind='cash_allocations',page=m.PageInput(limit=limit,cursor=cursor)),binding=b)
                collected.extend(page.items);cursor=page.next_cursor
                if cursor is None:break
            assert tuple(collected)==result.cash_allocations
        assert result.document.totals.bank_total.minor_units==7000
    run_private(read)
