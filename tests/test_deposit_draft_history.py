"""Authentic retained history and independent malformed-proof boundaries."""
import copy

import pytest

from bookflow.company import deposit_drafts as d, deposit_selection as child
from bookflow.company import deposit_draft_models as m
from bookflow.company.deposit_draft_history import DraftHistoryProof
from bookflow.core import audit
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_deposit_drafts import cash, driver, run, financial
from tests.test_deposit_lifecycle import additional_document, replacement
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_deposit_sources import uf


@pytest.fixture
def world(client, sale, cash, driver, run):
    paid = client.run('payment receive', dict(customer=sale['customer'], deposit_to=uf(client),
        payment_method='Payment witness cash', date='2026-06-02', amount='5',
        operation_key='History-payment', applications=dict(mode='inline',items=[])), company=COMPANY)
    payment = dict(source=paid['id'],source_type='payment',expected_version=paid['version'])
    document = additional_document(client,sale)
    document['sources'] = [cash,payment]
    posted = driver.run('post',dict(operation_key='History-deposit',document=document))
    edit = run('create',dict(from_deposit=posted.current.id,expected_version=1))
    driver.run('void',dict(deposit=posted.current.id,expected_version=1,operation_key='History-void'),reason='Retained proof')
    copied = run('create',dict(copy_from_voided=posted.current.id,expected_version=2))
    selection = run('create',dict(draft=copied.id,expected_version=copied.version),True)
    corrected = client.run('sales-receipt update',dict(sales_receipt=cash['source'],expected_version=3,memo='Later receipt memo'),company=COMPANY,reason='Historical copy must remain readable')
    return dict(cash=cash,payment=payment,posted=posted,edit=edit,copy=copied,selection=selection,corrected=corrected)


def proof(s, output, kind='draft'):
    h,r,value,_=d.load(s,output.id,kind=kind)
    return DraftHistoryProof(s,h,r,kind),h,r,value


def test_exact_header_only_versions_and_stale_copy_child_remain_readable(world,driver):
    with driver.session() as s:
        before=tuple(s.company.raw.iterdump())
        p,_,_,value=proof(s,world['edit'])
        endpoint=p.exact_header(world['payment']['source'],2)
        earlier=p.exact_header(world['payment']['source'],1)
        assert endpoint.header['version']==2 and endpoint.sequence>earlier.sequence
        assert endpoint.header['current_revision_id']==earlier.header['current_revision_id']
        # Semantic-coalescing is not used as the numeric endpoint.
        assert any(r.source.expected_header_version==2 for r in value.sources)
        for output,kind in ((world['copy'],'draft'),(world['selection'],'selection')):
            p,_,_,value=proof(s,output,kind)
            assert len(value.sources)==2
            for row in value.sources:
                assert row.captured_header_version==1 and row.source.expected_header_version==3
                assert p.source_endpoint(row).header['version']==1
            shown=child.show(s,m.SelectionShow(selection=output.id)) if kind=='selection' else d.show(s,m.DraftShow(draft=output.id))
            assert world['cash']['source'] in shown.stale_source_ids
        assert tuple(s.company.raw.iterdump())==before


@pytest.mark.parametrize('case',['missing_version','wrong_revision','foreign_owner','wrong_capture','wrong_observed','capture_without_copy'])
def test_source_pin_tampering_rejects_real_history(world,driver,case):
    with driver.session() as s:
        p,_,_,value=proof(s,world['copy'])
        row=copy.deepcopy(next(r for r in value.sources if r.source.source_type=='sales_receipt'))
        if case=='missing_version':row.captured_header_version=999
        elif case=='wrong_revision':row.source=row.source.model_copy(update={'revision_id':world['corrected']['revision']['id']})
        elif case=='foreign_owner':row.source=row.source.model_copy(update={'transaction_id':world['payment']['source']})
        elif case=='wrong_capture':row.captured_header_version=2  # Real version, same commercial revision, wrong original pin.
        elif case=='wrong_observed':row.source=row.source.model_copy(update={'expected_header_version':2})
        else:p,_,_,_=proof(s,world['edit'])
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:p.source_endpoint(row)
        assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'deposit_draft_history'}
        assert tuple(s.company.raw.iterdump())==before


@pytest.mark.parametrize('case',['missing','duplicate','raw_version','raw_before','wrong_revision','numeric_gap'])
def test_malformed_exact_history_rejects_without_store_mutation(world,driver,case):
    with driver.session() as s:
        p,_,_,_=proof(s,world['copy']);identity=world['payment']['source']
        p.reader.entries=copy.deepcopy(p.reader.entries)
        entries=p.reader.entries['transaction'][identity]
        target=next(r for r in entries if r['version_after']==2);requested=2
        if case=='missing':entries.remove(next(r for r in entries if r['version_after']==1))
        elif case=='duplicate':entries.append(dict(target))
        elif case=='raw_version':
            image=audit.decode_snapshot(target['after']);image['version']=999;target['after']=audit.encode_snapshot(image)
        elif case=='raw_before':
            image=audit.decode_snapshot(target['before']);image['version']=999;target['before']=audit.encode_snapshot(image)
        elif case=='wrong_revision':
            image=audit.decode_snapshot(target['after']);image['current_revision_id']=world['corrected']['revision']['id'];target['after']=audit.encode_snapshot(image)
        else:
            requested=7;target['version_after']=7
            image=audit.decode_snapshot(target['after']);image['version']=7;target['after']=audit.encode_snapshot(image)
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:p.exact_header(identity,requested)
        assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'deposit_draft_history'}
        assert tuple(s.company.raw.iterdump())==before


@pytest.mark.parametrize('case',['baseline','source'])
def test_persisted_false_proof_rolls_back_independent_of_hash_and_fk(client,sale,world,driver,case):
    with driver.session() as s:
        origin,manifest,originals=d.from_deposit(s,world['posted'].current.id,2,copy_voided=True)
        ctx=Context.new(Interface.python,'Malformed historical proof')
        header=d._new_header(s,ctx,'draft',copied=origin)
        if case=='baseline':header['copy_version']=1  # Owned revision/positive version still satisfy SQL shape.
        else:
            sources=list(manifest.sources);row=copy.deepcopy(sources[0])
            row.source=row.source.model_copy(update={'expected_header_version':2})
            sources[0]=row;manifest=d.manifest(manifest.currency,manifest.header,sources,manifest.additional,manifest.high_water)
        packed=d.bundle(s,ctx,'draft',header,None,manifest,new_id(),originals=originals)
        s.company.raw.execute('CREATE TABLE own_history_sentinel(value TEXT)')
        s.company.raw.execute("INSERT INTO own_history_sentinel VALUES ('keep')")
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:d.persist(s,ctx,[packed],packed[1]['audit_event_id'],'deposit draft create')
        assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'deposit_draft_history'}
        assert tuple(s.company.raw.iterdump())==before
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        assert d.show(s,m.DraftShow(draft=world['copy'].id)).id==world['copy'].id


@pytest.mark.parametrize('field',['version','revision'])
def test_edit_baseline_wrong_version_revision_pair_and_current_change(client,sale,driver,run,field):
    document=additional_document(client,sale)
    posted=driver.run('post',dict(operation_key='Baseline-old',document=document))
    edit=run('create',dict(from_deposit=posted.current.id,expected_version=1))
    changed=replacement(posted,document);changed['memo']='Second revision'
    updated=driver.run('update',dict(operation_key='Baseline-new',deposit=posted.current.id,expected_version=1,document=changed),reason='Current edit baseline moves')
    assert updated.current.version==2
    with driver.session() as s:
        assert d.show(s,m.DraftShow(draft=edit.id)).baseline_version==1
        origin,manifest,originals=d.from_deposit(s,posted.current.id,2)
        ctx=Context.new(Interface.python,'Wrong version/revision pair')
        header=d._new_header(s,ctx,'draft',edit=origin)
        if field=='version':header['baseline_version']=1
        else:
            old=s.company.raw.execute('SELECT baseline_revision_id FROM deposit_drafts WHERE id=?',(edit.id,)).fetchone()[0]
            header['baseline_revision_id']=old
        packed=d.bundle(s,ctx,'draft',header,None,manifest,new_id(),originals=originals)
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as error:d.persist(s,ctx,[packed],packed[1]['audit_event_id'],'deposit draft create')
        assert error.value.details=={'reason':'deposit_draft_history'}
        assert tuple(s.company.raw.iterdump())==before
