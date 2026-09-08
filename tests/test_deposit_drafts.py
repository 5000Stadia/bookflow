"""Private G3 through authentic dispatch Sessions and ordinary receipt producers."""
import copy
import json
import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.core.context import Context,Interface
from bookflow.core.errors import BookflowError
from bookflow.company import schema as c, deposit_drafts as d, deposit_selection as child
from bookflow.company import deposit_draft_models as m, deposit_source_queries as candidates
from tests.test_deposit_lifecycle import driver, additional_document
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method

@pytest.fixture
def run(driver):
    def execute(verb,body,selection=False):
        owner=child if selection else d
        with driver.session() as s:
            return owner.run(s,Context.new(Interface.python,'G3 ordinary witness'),owner.INPUTS[verb].model_validate(body),verb)
    return execute

@pytest.fixture
def cash(client,sale):
    result=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=uf(client),payment_method=method(client),date='2026-06-02',memo='Captured memo',
        lines=[dict(item=sale['item'],quantity='1',unit_price='60')]),company=COMPANY)
    return dict(source=result['id'],source_type='sales_receipt',expected_version=result['version'])

def financial(s):
    return {n:list(s.company.raw.execute('SELECT * FROM '+n)) for n in ('transactions','transaction_revisions','posting_batches','posting_lines','deposit_memberships','deposit_current_memberships','deposit_operations','sequences','custom_field_values')}


def test_incomplete_roundtrip_child_isolation_and_atomic_accept(client,sale,cash,driver,run):
    bank=client.account.create(name='Draft bank',type='bank',company=COMPANY)['id']
    with driver.session() as s:before=financial(s)
    draft=run('create',dict(header=dict(date='2026-06-03',memo='Parent',deposit_to=bank,cash_back=dict(memo='Unresolved'))))
    assert 'cash_back.account:required' in draft.summary.issues and draft.header.cash_back.memo=='Unresolved'
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash],set_additional=[dict(memo='Incomplete extra')]))
    with driver.session() as s:
        page=d.items(s,m.DraftItems(draft=draft.id))
        assert [r['ordinal'] for r in page['items']]==[1,2]
        parent_raw=s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(draft.revision_id,)).fetchone()[0]
    sel=run('create',dict(draft=draft.id,expected_version=draft.version),True)
    sel=run('clear',dict(selection=sel.id,expected_version=sel.version),True)
    abandoned=run('abandon',dict(selection=sel.id,expected_version=sel.version),True)
    assert abandoned.state=='abandoned'
    with driver.session() as s:
        assert s.company.raw.execute('SELECT snapshot FROM deposit_draft_revisions WHERE id=?',(draft.revision_id,)).fetchone()[0]==parent_raw
        assert d.show(s,m.DraftShow(draft=draft.id)).version==draft.version
    sel=run('create',dict(draft=draft.id,expected_version=draft.version),True)
    sel=run('clear',dict(selection=sel.id,expected_version=sel.version),True)
    accepted=run('accept',dict(selection=sel.id,expected_version=sel.version,draft=draft.id,expected_draft_version=draft.version),True)
    assert accepted.draft.summary.source_count==0 and accepted.draft.summary.additional_count==1
    assert accepted.draft.header==draft.header
    assert accepted.selection.accepted_revision_id==accepted.draft.revision_id
    with driver.session() as s:
        assert financial(s)==before
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        assert d.show(s,m.DraftShow(draft=draft.id,revision_number=2)).summary.source_count==1


def test_all_memo_states_restore_and_noop(cash,driver,run):
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash]))
    for override,origin,memo in [({'memo_override':None},'entered',None),({'memo_override':''},'entered',''),({'memo_override':'Own'},'entered','Own'),({'memo_action':'restore_source'},'source','Captured memo')]:
        draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[dict(cash,**override)]))
        with driver.session() as s:
            row=d.items(s,m.DraftItems(draft=draft.id))['items'][0]
            assert (row['memo_origin'],row['memo'])==(origin,memo)
    before=driver.dump()
    assert run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[dict(cash,memo_action='restore_source')])).version==draft.version
    assert driver.dump()==before
    with pytest.raises(ValidationError):m.SourcePatch.model_validate(dict(cash,memo_override=None,memo_action='restore_source'))


def test_parent_race_removed_readded_ordinals_and_terminal(cash,driver,run):
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash],set_additional=[{}]))
    sel=run('create',dict(draft=draft.id,expected_version=draft.version),True)
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,remove_sources=[cash['source']]))
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash]))
    with driver.session() as s:assert [(r['kind'],r['ordinal']) for r in d.items(s,m.DraftItems(draft=draft.id))['items']]==[('additional',2),('source',3)]
    before=driver.dump()
    with pytest.raises(BookflowError) as e:run('accept',dict(selection=sel.id,expected_version=sel.version,draft=draft.id,expected_draft_version=draft.version),True)
    assert e.value.code=='E_VERSION_CONFLICT' and driver.dump()==before
    draft=run('abandon',dict(draft=draft.id,expected_version=draft.version))
    with pytest.raises(BookflowError) as e:run('clear',dict(draft=draft.id,expected_version=draft.version))
    assert e.value.code=='E_DEPOSIT_DRAFT_STATE'


def test_candidates_append_does_not_stale_and_actual_source_change_does(client,cash,driver,run):
    draft=run('create',dict(header=dict(date='2026-06-03')))
    sel=run('create',dict(draft=draft.id,expected_version=draft.version),True)
    with driver.session() as s:first=candidates.query(s,m.SourceQuery(date='2026-06-03'))
    assert cash['source'] in [v.source.transaction_id for v in first.items]
    sel=run('update',dict(selection=sel.id,expected_version=sel.version,set_sources=[cash]),True)
    with driver.session() as s:assert candidates.query(s,m.SourceQuery(date='2026-06-03')).facts_fingerprint==first.facts_fingerprint
    client.run('sales-receipt update',dict(sales_receipt=cash['source'],expected_version=cash['expected_version'],memo='Changed after selection'),company=COMPANY,reason='G3 stale witness')
    with driver.session() as s:
        assert candidates.query(s,m.SourceQuery(date='2026-06-03')).facts_fingerprint!=first.facts_fingerprint
        assert child.show(s,m.SelectionShow(selection=sel.id)).stale_source_ids==(cash['source'],)
    with pytest.raises(BookflowError) as e:run('accept',dict(selection=sel.id,expected_version=sel.version,draft=draft.id,expected_draft_version=draft.version),True)
    assert e.value.code=='E_PREVIEW_STALE'


def test_deferred_head_fault_rolls_back_inside_writer_preserves_sentinel(driver,run,monkeypatch):
    original=d.final_foreign_keys
    def corrupt(s):
        s.company.raw.execute("UPDATE deposit_drafts SET current_revision_id='00000000000000000000000000',version=version+1")
        original(s)
    with driver.session() as s:
        s.company.raw.execute('CREATE TABLE own_sentinel(value TEXT)')
        s.company.raw.execute("INSERT INTO own_sentinel VALUES ('survives')")
        before=s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()
        with monkeypatch.context() as patch:
            patch.setattr(d,'final_foreign_keys',corrupt)
            with pytest.raises(BookflowError) as e:d.run(s,Context.new(Interface.python,'B1 witness'),m.DraftCreate(),'create')
        assert e.value.code=='E_VALIDATION'
        assert s.company.raw.execute('SELECT count(*) FROM deposit_drafts').fetchone()==(0,)
        assert s.company.raw.execute('SELECT count(*) FROM audit_events').fetchone()==before
        assert s.company.raw.execute('SELECT * FROM own_sentinel').fetchall()==[('survives',)]
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]


def test_copy_voided_new_identity_retains_unavailable_sources_and_original_bytes(client,sale,cash,driver,run):
    document=additional_document(client,sale)
    document['sources']=[dict(cash,memo_override=None)]
    posted=driver.run('post',dict(operation_key='G3-copy-original',document=document))
    driver.run('void',dict(operation_key='G3-copy-void',deposit=posted.current.id,expected_version=posted.current.version),reason='Copy voided witness')
    # Another ordinary deposit claims the released receipt. Copy must not drop it.
    reclaimed=driver.run('post',dict(operation_key='G3-copy-reclaimed',document=dict(document,sources=[dict(cash,expected_version=cash['expected_version']+2)])))
    with driver.session() as s:
        before=financial(s)
        original=s.company.raw.execute('SELECT * FROM transactions WHERE id=?',(posted.current.id,)).fetchone()
    copied=run('create',dict(copy_from_voided=posted.current.id,expected_version=posted.current.version+1))
    assert copied.edit_transaction_id is None and copied.baseline_version is None and copied.copy_transaction_id==posted.current.id
    assert copied.header.number is None and copied.summary.source_count==1 and copied.summary.additional_count==1
    assert copied.stale_source_ids==(cash['source'],)
    with driver.session() as s:
        items=d.items(s,m.DraftItems(draft=copied.id))['items']
        source=next(r for r in items if r['kind']=='source')
        assert source['memo'] is None and source['memo_origin']=='entered'
        assert source['source']['expected_header_version']==cash['expected_version']+3
        assert financial(s)==before
        assert s.company.raw.execute('SELECT * FROM transactions WHERE id=?',(posted.current.id,)).fetchone()==original
    with pytest.raises(BookflowError) as e:run('create',dict(from_deposit=posted.current.id,expected_version=posted.current.version+1))
    assert e.value.code=='E_DEPOSIT_DRAFT_STATE'


def test_all_custom_kinds_captured_defaults_required_clear_and_inactive(client,driver,run):
    definitions={}
    for kind,value in [('text','Captured'),('number','1.000000001'),('date','2026-06-03'),('bool',False),('choice','Alpha')]:
        args=dict(name='G3 '+kind,kind=kind,scopes=['deposit'],default=value)
        if kind=='choice':args['choices']=[dict(value='Alpha'),dict(value='Beta')]
        definitions[kind]=client.run('custom-field create',args,company=COMPANY)['id']
    required=client.run('custom-field create',dict(name='G3 required',kind='text',scopes=['deposit'],required=True),company=COMPANY)['id']
    draft=run('create',{})
    assert 'custom_fields.'+required+':required' in draft.summary.issues
    assert draft.header.custom_fields[definitions['number']].canonical_text=='1.000000001'
    assert draft.header.custom_fields[definitions['bool']].canonical_text=='false'
    assert all(draft.header.custom_fields[x].origin=='default' for x in definitions.values())
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,header=dict(custom_fields={required:'Entered',definitions['text']:None})))
    assert draft.header.custom_fields[definitions['text']].canonical_text is None
    assert 'custom_fields.'+required+':required' not in draft.summary.issues
    client.run('custom-field update',dict(custom_field=definitions['text'],expected_version=1,default='New default'),company=COMPANY)
    with driver.session() as s:assert d.show(s,m.DraftShow(draft=draft.id)).header==draft.header


@pytest.mark.parametrize('mutation',['summary','source_amount','source_batch','ordinal','row_owner','snapshot_count'])
def test_independent_manifest_and_relational_proof(cash,driver,run,mutation):
    from bookflow.company import deposit_draft_validation as validation
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash]))
    with driver.session() as s:
        h,r,value,_=d.load(s,draft.id)
        altered=value.model_copy(deep=True)
        if mutation=='summary':altered.summary.source_total+=1
        elif mutation=='source_amount':altered.sources[0].source=altered.sources[0].source.model_copy(update={'cash_minor_units':6001})
        elif mutation=='source_batch':altered.sources[0].source=altered.sources[0].source.model_copy(update={'business_batch_id':'00000000000000000000000000'})
        elif mutation=='ordinal':altered.sources[0].ordinal+=1
        elif mutation=='row_owner':altered.sources[0].row_id='00000000000000000000000000'
        else:altered.sources=()
        from bookflow.company.payment_queries import digest
        fake=dict(r,snapshot=altered.model_dump_json(),manifest_hash=digest(altered.model_dump(mode='json')))
        with pytest.raises(BookflowError):validation.decode_revision(s,h,fake,'draft')


def test_sql_typed_owner_shape_and_immutable_history(cash,driver,run):
    import sqlite3
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash],set_additional=[{}]))
    with driver.session() as s:
        with pytest.raises(Exception):s.company.raw.execute('DELETE FROM deposit_draft_sources')
        with pytest.raises(Exception):s.company.raw.execute("UPDATE deposit_draft_revisions SET manifest_hash='bad'")
        # Real additional subtype: a party discriminator without its typed FK is rejected.
        row=dict(s.company.conn.execute(sa.select(c.deposit_draft_additional)).mappings().one())
        row.update(row_id='00000000000000000000000000',party_kind='customer')
        with pytest.raises(Exception):s.company.conn.execute(c.deposit_draft_additional.insert().values(**row))
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]


def test_small_pages_order_empty_and_strict_limits(cash,driver,run):
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash],set_additional=[{},{}]))
    with driver.session() as s:
        seen=[];cursor=None
        while True:
            page=d.items(s,m.DraftItems(draft=draft.id,limit=1,cursor=cursor));seen.extend(r['ordinal'] for r in page['items']);cursor=page['next_cursor']
            assert page['total_count']==3
            if cursor is None:break
        assert seen==[1,2,3]
        for limit in (25,200):assert [r['ordinal'] for r in d.items(s,m.DraftItems(draft=draft.id,limit=limit))['items']]==seen
    cleared=run('clear',dict(draft=draft.id,expected_version=draft.version))
    with driver.session() as s:assert d.items(s,m.DraftItems(draft=draft.id))['items']==[]
    with pytest.raises(ValidationError):m.DraftItems(draft=draft.id,limit=True)
    with pytest.raises(ValidationError):m.DraftItems(draft=draft.id,limit=201)
    with pytest.raises(ValidationError):m.DraftUpdate.model_validate(dict(draft=draft.id,expected_version=cleared.version,set_additional=[{}]*201))


def test_copy_voided_source_keeps_old_amount_and_explicit_stale(client,sale,cash,driver,run):
    document=additional_document(client,sale);document['sources']=[cash]
    posted=driver.run('post',dict(operation_key='G3-copy-source-void-post',document=document))
    driver.run('void',dict(operation_key='G3-copy-source-void',deposit=posted.current.id,expected_version=1),reason='Copy keeps old source')
    client.run('sales-receipt void',dict(sales_receipt=cash['source'],expected_version=cash['expected_version']+2),company=COMPANY,reason='Customer receipt canceled')
    before=driver.dump()
    copied=run('create',dict(copy_from_voided=posted.current.id,expected_version=2))
    assert copied.summary.source_total==6000 and copied.stale_source_ids==(cash['source'],)
    with driver.session() as s:
        row=d.items(s,m.DraftItems(draft=copied.id))['items'][0]
        assert row['source']['cash_minor_units']==6000
        assert s.company.raw.execute('SELECT status FROM transactions WHERE id=?',(cash['source'],)).fetchone()==('voided',)
        assert s.company.raw.execute('SELECT count(*) FROM deposit_draft_consumptions').fetchone()==(0,)


def test_dry_run_writes_nothing_and_edit_clear_retains_target(client,sale,driver,run):
    document=additional_document(client,sale)
    post=driver.run('post',dict(operation_key='G3-edit-baseline',document=document))
    with driver.session() as s:
        before=tuple(s.company.raw.iterdump());s.dry_run=True
        preview=d.run(s,Context.new(Interface.python,'G3 dry-run'),m.DraftCreate(),'create')
        assert preview.summary.source_count==0 and tuple(s.company.raw.iterdump())==before
    draft=run('create',dict(from_deposit=post.current.id,expected_version=1))
    cleared=run('clear',dict(draft=draft.id,expected_version=draft.version))
    assert cleared.edit_transaction_id==post.current.id and cleared.baseline_version==1
    assert cleared.summary.additional_count==0 and cleared.header.bank is None


def test_balanced_false_source_plan_rejected_against_real_owned_posting(cash,driver,run):
    from bookflow.company import deposit_draft_validation as validation
    from bookflow.core.ids import new_id
    draft=run('create',{})
    draft=run('update',dict(draft=draft.id,expected_version=draft.version,set_sources=[cash]))
    with driver.session() as s:
        h,r,value,_=d.load(s,draft.id)
        wrong=value.sources[0].model_copy(deep=True)
        components=list(wrong.source.components)
        components[0]=components[0].model_copy(update={'capacity':components[0].capacity+1})
        wrong.source=wrong.source.model_copy(update={'components':tuple(components),'cash_minor_units':wrong.source.cash_minor_units+1})
        # Internally balanced/count/hash-consistent, but one cent never existed.
        proposed=d.manifest(value.currency,value.header,(wrong,),value.additional,value.high_water)
        ctx=Context.new(Interface.python,'Independent source witness');event=new_id()
        packed=d.bundle(s,ctx,'draft',d._next_header(s,ctx,h),h,proposed,event)
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:d.persist(s,ctx,[packed],event,'deposit draft update')
        assert error.value.code=='E_VALIDATION' and error.value.details['reason']=='source_provenance'
        assert tuple(s.company.raw.iterdump())==before
