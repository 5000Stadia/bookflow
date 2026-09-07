"""Selected history, immutable page pins and independent dated/GL oracles."""
import copy,json
import pytest
from bookflow.company import deposit_queries as q, deposit_read_models as m, deposit_print_data
from bookflow.company import deposit_drafts as drafts, deposit_draft_models as dm
from bookflow.core.publication import OSBinding
from bookflow.core.errors import BookflowError
from tests.test_deposit_draft_financial import run_private,financial,make_posted
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_deposit_lifecycle import additional_document,replacement


def test_history_original_operation_and_consumption(client,cash,run_private):
    posted,original=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,dm.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    noop=financial(run_private,dict(operation_key='read-noeffect',deposit=posted.current.id,expected_version=1,document=dict(mode='draft',draft=edit.id,expected_version=edit.version)),'update')
    assert not noop.changed
    financial(run_private,dict(operation_key='read-void',deposit=posted.current.id,expected_version=1),'void')
    copied=run_private(lambda s,ctx:drafts.run(s,ctx,dm.DraftCreate(copy_from_voided=posted.current.id,expected_version=2),'create'))
    def read(s,ctx):
        b=OSBinding.from_session(s);before=tuple(s.company.raw.iterdump())
        shown=q.show(s,m.ShowInput(deposit=posted.current.id),binding=b)
        assert shown.current.status=='voided' and shown.totals.bank_total.minor_units==6000
        assert shown.current.effective_bank_total==0
        values=[];cursor=None
        while True:
            result=q.history(s,m.HistoryInput(deposit=posted.current.id,page=m.PageInput(limit=1,cursor=cursor)),binding=b)
            values.extend(result.items);cursor=result.next_cursor
            if not cursor:break
        assert len(values)==result.total_count and len({v.id for v in values})==len(values)
        assert sum(v.kind=='draft_consumed' for v in values)==2
        assert sum(v.kind=='void' for v in values)==1
        assert sum(v.kind=='no_effect_operation' for v in values)==1
        assert {v.draft_id for v in values if v.kind=='draft_consumed'}=={original.id,edit.id}
        item=q.items(s,m.ItemsInput(deposit=posted.current.id,kind='sources'),binding=b).items[0]
        assert item.captured.source.cash_minor_units==6000 and item.current.claim_id is None
        assert drafts.show(s,dm.DraftShow(draft=copied.id),ctx=ctx).copy_transaction_id==posted.current.id
        assert tuple(s.company.raw.iterdump())==before
    run_private(read)


def test_items_pin_and_current_observation_after_correction(client,sale,run_private):
    doc=additional_document(client,sale)
    doc['additional'].append(dict(doc['additional'][0],amount='5.00',memo='second'))
    posted=financial(run_private,dict(operation_key='read-pin-post',document=doc))
    first=run_private(lambda s,ctx:q.items(s,m.ItemsInput(deposit=posted.current.id,kind='additional',page=m.PageInput(limit=1)),binding=OSBinding.from_session(s)))
    assert first.next_cursor and first.total_count==2
    updated_doc=replacement(posted,doc);updated_doc['memo']='fresh metadata'
    updated=financial(run_private,dict(operation_key='read-pin-update',deposit=posted.current.id,expected_version=1,document=updated_doc),'update')
    def read(s,ctx):
        b=OSBinding.from_session(s)
        second=q.items(s,m.ItemsInput(deposit=posted.current.id,kind='additional',page=m.PageInput(limit=1,cursor=first.next_cursor)),binding=b)
        assert second.selected==first.selected and second.fingerprint==first.fingerprint
        assert second.current.version==2 and first.current.version==1
        assert [r.units for r in first.items+second.items]==[1000,500]
        old=q.show(s,m.ShowInput(deposit=posted.current.id,revision_number=1),binding=b)
        assert not old.selected_is_current and old.selected.memo==doc['memo']
        assert old.current.revision_id==updated.current.revision_id
    run_private(read)


def test_signed_n1_and_zero_bank_not_void(client,sale,cash,run_private):
    from tests.test_payment_receipts import posted as invoice_post,method
    inv=invoice_post(client,sale['customer'],sale['item'],'100.00','read-N1')
    pay=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='100',payment_method=client.run('payment-method create',dict(name='Read N1 cash',kind='cash'),company=COMPANY)['id'],operation_key='read-N1-payment',applications=dict(mode='inline',items=[dict(invoice=inv['id'],expected_version=1,amount='100')])),company=COMPANY)
    doc=additional_document(client,sale,'20',cash='5')
    fee=client.account.create(name='Read fee',type='expense',company=COMPANY)['id']
    doc['additional'].append(dict(received_from=dict(kind='customer',id=sale['customer']),from_account=fee,amount='-3'))
    doc['sources']=[dict(source_type='payment',source=pay['id'],expected_version=pay['version']),cash]
    posted=financial(run_private,dict(operation_key='read-N1',document=doc))
    def read(s,ctx):
        result=q.show(s,m.ShowInput(deposit=posted.current.id,as_of='2026-06-03'),binding=OSBinding.from_session(s))
        t=result.totals
        assert [t.posting_total.minor_units,t.source_total.minor_units,t.positive_additional_total.minor_units,t.negative_additional_total.minor_units,t.subtotal.minor_units,t.cash_back.minor_units,t.bank_total.minor_units]==[18000,16000,2000,-300,17700,500,17200]
        assert result.dated_state.bank_movement.minor_units==17200
        assert result.dated_state.source_membership_total.minor_units==16000
        printed=deposit_print_data.print_data(s,m.PrintDataInput(deposit=posted.current.id),binding=OSBinding.from_session(s))
        assert len(printed.rows)==4 and printed.document.totals==t
    run_private(read)
    zero_doc=dict(mode='inline',deposit_to=doc['deposit_to'],date='2026-06-03',cash_back=dict(account=doc['cash_back']['account'],amount='10'),additional=[doc['additional'][0]|{'amount':'10'}])
    zero=financial(run_private,dict(operation_key='read-zero',document=zero_doc))
    result=run_private(lambda s,ctx:q.show(s,m.ShowInput(deposit=zero.current.id,as_of='2026-06-03'),binding=OSBinding.from_session(s)))
    assert result.current.status=='posted' and result.dated_state.financial_state=='effective' and result.dated_state.bank_movement.minor_units==0


@pytest.mark.parametrize('change',['field','edge','column','codec'])
def test_closed_manifest_rejects_undispositioned_change(monkeypatch,change):
    from bookflow.company import deposit_read_manifest as manifest,schema as c
    import sqlalchemy as sa
    manifest.conform()
    if change=='field':
        fields=copy.deepcopy(manifest.FIELDS);del fields['deposit_profiles']['bank_total'];monkeypatch.setattr(manifest,'FIELDS',fields)
    elif change=='edge':
        edges=copy.deepcopy(manifest.EDGES);edges['deposit_components']=[];monkeypatch.setattr(manifest,'EDGES',edges)
    elif change=='codec':monkeypatch.setattr(manifest,'CODECS',dict(manifest.CODECS,Effect='unknown'))
    else:
        metadata=sa.MetaData()
        for table in c.metadata.tables.values():table.to_metadata(metadata)
        metadata.tables['deposit_profiles'].append_column(sa.Column('undispositioned',sa.Text))
        monkeypatch.setattr(c,'metadata',metadata)
    with pytest.raises(BookflowError) as err:manifest.conform()
    assert err.value.code=='E_DEPOSIT_SOURCE_INVALID'


def test_all_captured_custom_kinds_print_without_current_refresh(client,cash,run_private):
    values={'text':'Saved text','number':'1.000000001','date':'2026-06-03','bool':False,'choice':'Alpha'}
    fields={kind:client.run('custom-field create',dict(name='Read captured '+kind,kind=kind,scopes=['deposit'],default=value,**({'choices':[dict(value='Alpha'),dict(value='Beta')]} if kind=='choice' else {})),company=COMPANY)['id'] for kind,value in values.items()}
    posted,_=make_posted(client,cash,run_private)
    client.run('custom-field update',dict(custom_field=fields['text'],expected_version=1,name='Current renamed field',default='new value'),company=COMPANY)
    def read(s,ctx):
        result=deposit_print_data.print_data(s,m.PrintDataInput(deposit=posted.current.id),binding=OSBinding.from_session(s))
        captured={v.captured.kind:v for v in result.document.selected.custom_fields}
        assert set(captured)==set(values)
        for kind,value in values.items():
            assert captured[kind].captured.value==value and type(captured[kind].captured.value) is type(value)
            assert captured[kind].captured.name=='Read captured '+kind
            assert captured[kind].captured_print_visibility is None
    run_private(read)


def test_coordinate_retains_original_cash_and_all_current_knowledge_dates(client,cash,run_private):
    from bookflow.company import deposit_coordination as coordinate,deposit_coordinate_persistence as cp
    from bookflow.company.deposit_coordinate_models import CoordinateInput
    posted,_=make_posted(client,cash,run_private)
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,dm.DraftCreate(from_deposit=posted.current.id,expected_version=1),'create'))
    edit=run_private(lambda s,ctx:drafts.run(s,ctx,dm.DraftUpdate(draft=edit.id,expected_version=edit.version,header=dm.HeaderPatch(date='2026-06-09')),'update'))
    inp=CoordinateInput.model_validate(dict(deposit=posted.current.id,expected_version=1,operation_key='read-coordinate-date',
        source_action=dict(kind='sales_receipt_update',input=dict(sales_receipt=cash['source'],expected_version=2,date='2026-06-08')),
        replacement=dict(mode='document',document=dict(mode='draft',draft=edit.id,expected_version=edit.version),draft_source_result='retain')))
    def action(s,ctx):
        b=OSBinding.from_session(s);p=coordinate.prepare(s,ctx,inp,binding=b)
        return cp.execute(s,ctx,coordinate.prepare(s,ctx,inp.model_copy(update={'dependency_guard':p.dependency_guard}),binding=b))
    result=run_private(action)
    def read(s,ctx):
        b=OSBinding.from_session(s)
        for cutoff,units,state in [('2026-06-07',0,'canceled'),('2026-06-08',0,'canceled'),('2026-06-09',6000,'effective')]:
            shown=q.show(s,m.ShowInput(deposit=posted.current.id,revision_number=1,as_of=cutoff),binding=b)
            assert shown.selected.date=='2026-06-03' and not shown.selected_is_current
            assert shown.current.revision_id==result.current.revision_id
            assert shown.dated_state.bank_movement.minor_units==units
            assert shown.dated_state.source_membership_total.minor_units==units
            assert shown.dated_state.financial_state==state
        original=q.items(s,m.ItemsInput(deposit=posted.current.id,revision_number=1,kind='sources'),binding=b).items[0]
        current=q.items(s,m.ItemsInput(deposit=posted.current.id,kind='sources'),binding=b).items[0]
        assert original.captured.source.receipt_date=='2026-06-02' and current.captured.source.receipt_date=='2026-06-08'
        assert any(v.kind=='coordinated_source_change' for v in q.history(s,m.HistoryInput(deposit=posted.current.id),binding=b).items)
    run_private(read)
