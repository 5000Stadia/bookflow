"""Tombstone presentation preserves complete ledger facts and page continuation."""
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


@pytest.mark.parametrize('noun', ['invoice', 'sales-receipt'])
def test_deleted_reads_and_small_register_pages_keep_full_ledger(books, noun):
    run=books['run'];selector=noun.replace('-','_')
    from tests.test_sales_deletion import service_item
    service=service_item(books,'Visible sales')
    funding=books['bank'] if noun=='sales-receipt' else next(a['id'] for a in books['client'].account.query(company=books['company'],limit=200)['items'] if a['type']=='accounts_receivable')
    def purchase(amount,date):
        raw=dict(customer=books['customer'],date=date,lines=[dict(item=service,quantity='1',unit_price=amount)])
        if noun=='sales-receipt':raw.update(deposit_to=funding,payment_method=books['methods']['Cash'])
        return run(noun+' post',raw,reason='Record sale')
    first=purchase('3','2017-01-01');hidden=purchase('11','2017-01-02')
    middle=purchase('5','2017-01-02');zero=purchase('0','2017-01-03');last=purchase('7','2017-01-04')
    args=dict(account=funding,date_from='2017-01-01',date_to='2017-01-31')
    assert {x['id'] for x in run(noun+' query',{})['items']}=={p['id'] for p in (first,hidden,middle,zero,last)}
    enable(books,noun)
    before=run('register query',dict(args,limit=1))
    for post in (hidden,zero):
        run(noun+' delete',{selector:post['id'],'expected_version':post['version']},reason='Remove duplicate')
    frozen=database(location(books))
    with pytest.raises(BookflowError) as stale:run('register query',dict(args,limit=1,cursor=before['next_cursor']))
    assert stale.value.code=='E_QUERY_STALE'
    assert {x['id'] for x in run(noun+' query',{})['items']}=={p['id'] for p in (first,middle,last)}
    assert {x['id'] for x in run(noun+' query',{'include_deleted':True})['items']}=={p['id'] for p in (first,hidden,middle,zero,last)}
    for post in (hidden,zero):
        for verb in ('show','history'):
            with pytest.raises(BookflowError) as gone:run(noun+' '+verb,{selector:post['id']})
            assert gone.value.code=='E_RECORD_NOT_FOUND'
            historical=run(noun+' '+verb,{selector:post['id'],'include_deleted':True})
            assert historical['status']=='deleted' and historical['deletion']['reason']=='Remove duplicate'
        shown=run(noun+' show',{selector:post['id'],'include_deleted':True})
        assert shown['total']==post['total']
        assert shown['revision']['id']==post['revision']['id']
    # Each report page remains the full accounting projection, including the
    # original and inverse. Only the register presentation omits their rows.
    ledger=run('report general-ledger',dict(args,limit=100))
    assert sum(r['transaction_id']==hidden['id'] for r in ledger['rows'])==2
    for limit in (1,2):
        rows=[];cursor=None;seen=set()
        while True:
            page=run('register query',dict(args,limit=limit,**({'cursor':cursor} if cursor else {})))
            assert page['ledger_totals']==ledger['totals']
            assert page['current_balance']['balance']['minor_units']==1500
            assert page['rows'] or not page['next_cursor']
            rows+=page['rows'];cursor=page['next_cursor']
            if not cursor:break
            assert cursor not in seen;seen.add(cursor)
        posting=[r for r in rows if r['transaction_id']]
        assert [r['transaction_id'] for r in posting]==[p['id'] for p in (first,middle,last)]
        assert [r['running_balance']['minor_units'] for r in posting]==[300,800,1500]
    assert database(location(books))==frozen



def test_deleted_work_destination_replay_and_normal_navigation(books):
    from tests.test_sales_deletion import service_item
    from bookflow.adapters.workbench import document_nav
    run=books['run'];item=service_item(books,'Retained source sale')
    source=run('work-order create',dict(customer=books['customer'],date='2017-01-01',title='Retained work',lines=[dict(item=item,quantity='1')]),reason='Create source')
    request=dict(work_order=source['id'],expected_version=source['version'],conversion_key='permanent-source-sale',date='2017-01-02')
    post=run('work-order invoice',request,reason='Original work sale')
    enable(books,'invoice',deny_post=False)
    run('invoice delete',dict(invoice=post['id'],expected_version=1),reason='Delete source sale')
    before=database(location(books))
    assert run('work-order invoice',request,reason='Original work sale')['status']=='deleted'
    destinations=run('work-order billing',dict(work_order=source['id']))['destinations']
    assert len(destinations)==1 and destinations[0]['status']=='deleted'
    record=run('invoice show',dict(invoice=post['id'],include_deleted=True))
    read=lambda name,raw,company:run(name,raw)
    assert document_nav.recent(read,books['company'],'invoice')['documents']==[]
    strip=document_nav.strip(read,books['company'],'invoice',record)
    assert not strip['steps'] and 'Deleted record' in strip['unavailable']
    assert database(location(books))==before
