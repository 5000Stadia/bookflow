"""One authentic403-source producer world. No >200 financial document fabrication."""
import json
from pathlib import Path
import pytest
from bookflow.company import deposit_drafts as drafts, deposit_selection as child, deposit_source_queries as query
from bookflow.company import deposit_draft_models as m
from tests.test_deposit_drafts import run,financial
from tests.test_deposit_lifecycle import driver
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_deposit_sources import uf
from tests.test_payment_receipts import method


@pytest.fixture(scope='module')
def world(_seeded_template,tmp_path_factory):
    import shutil,bookflow
    import os
    root=tmp_path_factory.mktemp('g3-403-world')/'root'
    retained=os.environ.get('BOOKFLOW_G3_REUSE_SOURCE_ROOT')
    if retained:
        original=Path(retained)
        completed=original.parent/'403-receipt.json'
        if completed.exists():
            receipt=json.loads(completed.read_text())
            assert len(receipt['source_ids'])==403 and receipt['uf_delta']==40300
            shutil.copytree(original,root)
            client=bookflow.connect(data_root=str(root))
            return dict(client=client,account=uf(client),sources=receipt['source_ids'],root=root,reused=True,
                accepted=m.AcceptOutput.model_validate_json(json.dumps(receipt['all_matching'])),collected=[r['source'] for r in receipt['source_ids']])
        receipt=json.loads((original.parent/'producer-receipt.json').read_text())
        assert len(receipt['sources'])==403 and receipt['uf_total']==40300
        shutil.copytree(original,root)
        client=bookflow.connect(data_root=str(root))
        return dict(client=client,account=uf(client),sources=receipt['sources'],root=root,reused=True)
    shutil.copytree(_seeded_template,root)
    client=bookflow.connect(data_root=str(root))
    facts=sale.__wrapped__(client)
    return dict(client=client,sale=facts,account=uf(client),method=method(client),sources=[],root=root,reused=False)

@pytest.fixture
def client(world):return world['client']

@pytest.mark.parametrize('batch',range(9))
def test_00_ordinary_source_batch(world,batch,driver):
    assert not world['reused'], 'Use -k not ordinary_source_batch with a retained closed producer world'
    client=world['client'];sale=world['sale'];sources=world['sources']
    account=world['account'];pay_method=world['method']
    assert len(sources)==batch*50
    for index in range(batch*50,min(403,(batch+1)*50)):
        if index%2:
            result=client.run('sales-receipt post',dict(customer=sale['customer'],deposit_to=account,payment_method=pay_method,date='2030-01-01',number=f'G3S{index:04d}',
                lines=[dict(item=sale['item'],quantity='1',unit_price='1')]),company=COMPANY)
            kind='sales_receipt'
        else:
            result=client.run('payment receive',dict(customer=sale['customer'],deposit_to=account,payment_method=pay_method,date='2030-01-01',number=f'G3P{index:04d}',amount='1',
                operation_key=f'g3-scale-{index}',applications=dict(mode='inline',items=[])),company=COMPANY)
            kind='payment'
        sources.append(dict(source_type=kind,source=result['id'],expected_version=result['version']))
    expected=min(403,(batch+1)*50)
    assert len(sources)==expected and len({r['source'] for r in sources})==expected
    with driver.session() as s:
        ids=[r['source'] for r in sources]
        total=s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=? AND transaction_id IN ('+','.join('?' for _ in ids)+')',(account,*ids)).fetchone()[0]
        assert total==expected*100
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships').fetchone()==(0,)
    (world['root'].parent/'producer-receipt.json').write_text(json.dumps(dict(sources=sources,uf_total=total,batch=batch,root=str(world['root'])),indent=2))


def test_10_open_complete_selection(world,driver,run):
    assert len({x['source'] for x in world['sources']})==403
    with driver.session() as s:
        world['before']=financial(s)
        ids=[r['source'] for r in world['sources']]
        delta=s.company.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE account_id=? AND transaction_id IN ('+','.join('?' for _ in ids)+')',(world['account'],*ids)).fetchone()[0]
        assert delta==40300
    world['draft']=run('create',dict(header=dict(date='2030-01-02',memo='Full403 draft')))
    world['selection']=run('create',dict(draft=world['draft'].id,expected_version=world['draft'].version),True)
    world.update(filter=m.SourceFilter(date='2030-01-02',date_from='2030-01-01',date_to='2030-01-01'),cursor=None,collected=[],sizes=[],fingerprint=None)

@pytest.mark.parametrize('batch',range(3))
def test_20_fetch_append_next_200_200_3(world,driver,run,batch):
    assert len(world['sizes'])==batch
    with driver.session() as s:page=query.query(s,m.SourceQuery(**world['filter'].model_dump(),limit=200,cursor=world['cursor']))
    if world['fingerprint'] is not None:assert page.facts_fingerprint==world['fingerprint']
    world['fingerprint']=page.facts_fingerprint
    assert page.total_count==403 and page.subtotal==40300
    entries=[dict(source=v.source.transaction_id,source_type=v.source.source_type,expected_version=v.source.expected_header_version) for v in page.items]
    world['sizes'].append(len(entries));world['collected'].extend(x['source'] for x in entries)
    selection=world['selection']
    world['selection']=run('update',dict(selection=selection.id,expected_version=selection.version,set_sources=entries),True)
    world['cursor']=page.next_cursor
    assert world['selection'].source_count==min(403,(batch+1)*200)
    if batch==2:assert world['cursor'] is None and world['sizes']==[200,200,3] and len(set(world['collected']))==403


def test_30_accept_complete_append(world,driver,run):
    selection=world['selection'];draft=world['draft']
    accepted=run('accept',dict(selection=selection.id,expected_version=selection.version,draft=draft.id,expected_draft_version=draft.version),True)
    assert accepted.draft.summary.source_count==403 and accepted.draft.summary.source_total==40300
    world['accepted']=accepted


def test_40_all_matching_complete_atomic_manifest(world,driver,run):
    other=run('create',dict(header=dict(date='2030-01-02')))
    selection=run('create',dict(draft=other.id,expected_version=other.version),True)
    selected=run('select-matching',dict(selection=selection.id,expected_version=selection.version,filter=world['filter'].model_dump(),facts_fingerprint=world['fingerprint']),True)
    assert selected.source_count==403 and selected.source_total==40300
    world.update(other=other,all_selection=selected)


def test_50_accept_all_matching(world,driver,run):
    selected=world['all_selection'];other=world['other']
    world['accepted_all']=run('accept',dict(selection=selected.id,expected_version=selected.version,draft=other.id,expected_draft_version=other.version),True)
    assert world['accepted_all'].draft.summary.source_count==403


def test_60_reopen_full_pages_raw_and_storage_receipt(world,driver):
    accepted=world['accepted'];draft=world['draft']
    with driver.session() as s:
        assert drafts.show(s,m.DraftShow(draft=draft.id)).summary.source_count==403
        assert financial(s)==world['before']
        assert s.company.raw.execute('SELECT count(*) FROM deposit_draft_sources WHERE revision_id=?',(accepted.draft.revision_id,)).fetchone()==(403,)
        assert s.company.raw.execute('SELECT count(*) FROM deposit_current_memberships').fetchone()==(0,)
        assert s.company.raw.execute('PRAGMA main.foreign_key_check').fetchall()==[]
        page=drafts.items(s,m.DraftItems(draft=draft.id,limit=200));seen=[r['source']['transaction_id'] for r in page['items']]
        while page['next_cursor']:
            page=drafts.items(s,m.DraftItems(draft=draft.id,limit=200,cursor=page['next_cursor']))
            seen.extend(r['source']['transaction_id'] for r in page['items'])
        assert seen==world['collected']
        metrics=dict(page_count=s.company.raw.execute('PRAGMA page_count').fetchone()[0],page_size=s.company.raw.execute('PRAGMA page_size').fetchone()[0],
            draft_source_rows=s.company.raw.execute('SELECT count(*) FROM deposit_draft_sources').fetchone()[0],selection_source_rows=s.company.raw.execute('SELECT count(*) FROM deposit_selection_sources').fetchone()[0])
    receipt=dict(root=str(world['root']),source_ids=world['sources'],append_sizes=world['sizes'],uf_delta=40300,draft=accepted.draft.model_dump(mode='json'),all_matching=world['accepted_all'].model_dump(mode='json'),storage=metrics,
        boundary='No financial provider, deposit memberships, consumed draft or403-member voided deposit created. These require stage2.')
    (world['root'].parent/'403-receipt.json').write_text(json.dumps(receipt,indent=2))


def test_70_removed_work_source_beyond_200_retains_whole_history_authority(world,tmp_path,monkeypatch):
    """Real linked-work source; test-only denial at the existing resource owner.

    This proves graph traversal, not live granular policy activation.
    """
    import shutil,bookflow
    from bookflow.company import deposit_sources,payment_authority
    from bookflow.core.context import Context,Interface
    from bookflow.core.errors import BookflowError
    from tests.test_work_billing_lifecycle import accepted,bill
    root=tmp_path/'work-history-root';shutil.copytree(world['root'],root)
    client=bookflow.connect(data_root=str(root));private=driver.__wrapped__(client,monkeypatch)
    receipt=next(r for r in world['sources'] if r['source_type']=='sales_receipt')
    with private.session() as s:
        source=deposit_sources.load(s,receipt['source'])
        facts=dict(customer=source.profile.customer.id,item=source.components[0].sale_line.item.id)
    work=accepted(client,facts,lines=[dict(item=facts['item'],quantity='1',unit_price='1')])
    paid=bill(client,work,key='G3-actual-work',verb='sales-receipt',deposit_to=world['account'],payment_method='Payment witness cash',amount_received='1')
    current=world['accepted'].draft
    with private.session() as s:
        updated=drafts.run(s,Context.new(Interface.python,'G3 historical work append'),m.DraftUpdate(draft=current.id,expected_version=current.version,
            remove_sources=[world['collected'][-1]],set_sources=[m.SourcePatch(source=paid['id'],source_type='sales_receipt',expected_version=paid['version'])]),'update')
    with private.session() as s:
        removed=drafts.run(s,Context.new(Interface.python,'G3 historical work remove'),m.DraftUpdate(draft=current.id,expected_version=updated.version,remove_sources=[paid['id']]),'update')
        before=tuple(s.company.raw.iterdump())
        original=payment_authority.require_resource;seen=[]
        def deny(session,resource,role):
            seen.append((resource,role))
            if resource=='customer-work':raise BookflowError('E_PERMISSION')
            return original(session,resource,role)
        with monkeypatch.context() as patch:
            patch.setattr(payment_authority,'require_resource',deny)
            for read in (lambda:drafts.show(s,m.DraftShow(draft=current.id)),lambda:drafts.items(s,m.DraftItems(draft=current.id,kind='additional',limit=1))):
                with pytest.raises(BookflowError) as error:read()
                assert error.value.code=='E_PERMISSION' and paid['id'] not in str(error.value.details)
            assert current.id not in {row['id'] for row in drafts.query(s,m.DraftQuery())['items']}
        assert ('customer-work','member') in seen
        assert tuple(s.company.raw.iterdump())==before
        assert removed.summary.source_count==402
