"""Sales-owner cancellation: independent stock oracle and permanent exact receipts."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


def service_item(books, name):
    exempt=next(row['id'] for row in books['run']('sales-tax-code list',{})['items'] if not row['taxable'])
    return books['client'].item.create(company=books['company'],name=name,type='service',sales_enabled=True,
        description=name,sales_tax_code_id=exempt,income_account_id=books['income'],price='12')['id']


def sale(books, noun='invoice', *, free=False, zero_cost=False):
    run=books['run']; item=_inventory_part(books)
    run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='0' if zero_cost else '8')]),reason='Receive two units')
    raw=dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='0' if free else '12')])
    if noun=='sales-receipt':raw.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
    return item,run(noun+' post',raw,reason='Sell one unit')


@pytest.mark.parametrize('noun',['invoice','sales-receipt'])
@pytest.mark.parametrize('state',['posted','free','zero','voided'])
def test_exact_sales_cancellation_and_permanent_retry(books,noun,state):
    item,post=sale(books,noun,free=state in ('free','zero'),zero_cost=state=='zero')
    asset=_inventory_asset(books);run=books['run'];selector=noun.replace('-','_')
    if state=='voided':post=run(noun+' void',{selector:post['id'],'expected_version':1},reason='Prior cancellation')
    path=location(books);enable(books,noun)
    raw={selector:post['id'],'expected_version':post['version'],'operation_key':'permanent-sale-delete'}
    before=database(path)
    with pytest.raises(BookflowError) as denied:run(noun+' void',{selector:post['id'],'expected_version':post['version']},reason='Posting denied')
    assert denied.value.code=='E_PERMISSION'
    preview=run(noun+' delete',raw,reason='Delete duplicate sale',dry_run=True)
    assert preview['version']==post['version']+1 and preview['cancelled_stock_movements']==(0 if state=='voided' else 1)
    assert database(path)==before
    result=run(noun+' delete',raw,reason='Delete duplicate sale')
    assert result['status']=='deleted' and result['version']==post['version']+1
    assert _net(books)==({} if state=='zero' else {asset:1600,books['payable']:-1600})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?',(item,)).fetchone()==(2000000,0 if state=='zero' else 1600)
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",(post['id'],)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM sales_deletions').fetchone()==(1,)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    after=database(path)
    for table in ('transaction_revisions','document_lines','sales_profiles','sales_line_profiles','sales_tax_components'):
        assert after['tables'][table]==before['tables'][table]
    replay=run(noun+' delete',raw,reason='Delete duplicate sale')
    assert replay['idempotent_replay'] and not replay['changed'] and database(path)==after
    with pytest.raises(BookflowError) as mismatch:run(noun+' delete',raw,reason='Other intention')
    assert mismatch.value.code=='E_IDEMPOTENCY_MISMATCH' and database(path)==after


def test_late_sales_tombstone_failure_rolls_back_stock_and_accounting(books,monkeypatch):
    from bookflow.company import sales_deletions
    item,post=sale(books);path=location(books);enable(books,'invoice');before=database(path)
    original=sales_deletions.persist_tombstone
    def fail(s,row):
        original(s,row)
        assert s.company.raw.execute('SELECT sum(quantity_microunits) FROM inventory_movements WHERE item_id=?',(item,)).fetchone()==(2000000,)
        raise BookflowError('E_VALIDATION',message='Injected after sales tombstone')
    monkeypatch.setattr(sales_deletions,'persist_tombstone',fail)
    with pytest.raises(BookflowError,match='Injected'):
        books['run']('invoice delete',dict(invoice=post['id'],expected_version=1),reason='Rollback witness')
    assert database(path)==before


@pytest.mark.parametrize('noun',['invoice','sales-receipt'])
def test_taxed_stock_sale_exact_captured_inverse(books,noun):
    run=books['run'];item=_inventory_part(books);asset=_inventory_asset(books)
    with sqlite3.connect(location(books)) as db:
        liability=db.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    agency=books['client'].vendor.create(company=books['company'],name='Deletion tax agency',is_tax_agency=True)['id']
    tax=run('item create',dict(name='Captured seven and quarter',type='sales_tax_item',tax_percent='7.25',tax_agency_vendor_id=agency,liability_account_id=liability))['id']
    run('company update',dict(sales_tax_enabled=True),reason='Enable captured tax')
    run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='8')]),reason='Receive')
    raw=dict(customer=books['customer'],date='2017-01-02',sales_tax_item=tax,lines=[dict(item=item,quantity='1',unit_price='12',tax_code=next(row['id'] for row in run('sales-tax-code list',{})['items'] if row['taxable']))])
    if noun=='sales-receipt':raw.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
    post=run(noun+' post',raw,reason='Taxed sale')
    assert post['total_minor_units']==1287 and post['tax_minor_units']==87
    assert _net(books)[asset]==800 and _net(books)[books['cogs']]==800
    path=location(books);enable(books,noun);before=database(path)
    run(noun+' delete',{noun.replace('-','_'):post['id'],'expected_version':1},reason='Cancel captured tax sale')
    assert _net(books)=={asset:1600,books['payable']:-1600}
    after=database(path)
    assert after['tables']['sales_tax_components']==before['tables']['sales_tax_components']
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        originals={r['id']:dict(r) for r in db.execute('SELECT l.*,b.effective_date FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id WHERE l.transaction_id=? AND l.reversed_line_id IS NULL',(post['id'],))}
        inverses=[dict(r) for r in db.execute('SELECT l.*,b.effective_date FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id WHERE l.transaction_id=? AND l.reversed_line_id IS NOT NULL',(post['id'],))]
        assert len(originals)==len(inverses)==5
        assert {r['reversed_line_id'] for r in inverses}==originals.keys()
        for r in inverses:
            old=originals[r['reversed_line_id']]
            assert (r['debit_minor_units'],r['credit_minor_units'])==(old['credit_minor_units'],old['debit_minor_units'])
            for field in ('account_id','currency','effective_date'):assert r[field]==old[field]


def test_work_release_once_and_deleted_update_fence(books):
    run=books['run']
    service=service_item(books,'Billed work service')
    source=run('work-order create',dict(customer=books['customer'],date='2017-01-01',title='Two visits',lines=[dict(item=service,quantity='2')]),reason='Create work')
    post=run('work-order invoice',dict(work_order=source['id'],expected_version=source['version'],conversion_key='work-sale',date='2017-01-02'),reason='Bill work')
    assert run('work-order billing',dict(work_order=source['id']))['remaining_net_minor_units']==0
    path=location(books);enable(books,'invoice');before=database(path)
    raw=dict(invoice=post['id'],expected_version=1,operation_key='delete-work-sale')
    run('invoice delete',raw,reason='Release billed work')
    assert run('work-order billing',dict(work_order=source['id']))['remaining_net_minor_units']==2400
    after=database(path)
    assert after['tables']['work_billing_allocations']==before['tables']['work_billing_allocations']
    run('invoice delete',raw,reason='Release billed work')
    assert database(path)==after


def test_concurrent_deletes_and_revoked_permanent_retry(books):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    _,post=sale(books);path=location(books);client=books['client']
    # Retain scoped administrator only to revoke the grant after the race;
    # the parameterized core witness separately uses Standard with posting denied.
    state=client.permission.show()
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    member=next(x for x in client.membership.list(company=books['company'])['items'] if x['scope_type']=='company' and x['scope_id']==books['company'])
    client.membership.grant(user=member['user_id'],company=books['company'],role='admin',expected_version=member['version'],grants=['transaction.invoice.delete'],denies=['ledger.post'])
    barrier=Barrier(2)
    def attempt(key):
        barrier.wait()
        try:return books['run']('invoice delete',dict(invoice=post['id'],expected_version=1,operation_key=key),reason='Concurrent deletion')
        except BookflowError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(attempt,key) for key in ('race-a','race-b')]
        results=[f.result() for f in futures]
    assert sum(isinstance(result,dict) for result in results)==1,results
    loser=next(i for i,result in enumerate(results) if isinstance(result,str))
    # The loser may not get in at all, or may arrive after the winner committed and be told
    # the sale is already deleted. Both are correct, and neither writes anything.
    assert results[loser] in ('E_DB_BUSY','E_VERSION_CONFLICT','E_VALIDATION'),results
    after=database(path)
    with pytest.raises(BookflowError) as stale:
        books['run']('invoice delete',dict(invoice=post['id'],expected_version=1,operation_key=('race-a','race-b')[loser]),reason='Concurrent deletion')
    # The loser's key wrote no receipt, so this is a new operation against a record that is
    # already gone: it is told the delete happened, not that its version is stale.
    assert stale.value.code=='E_VALIDATION' and database(path)==after
    assert 'already deleted and cannot be deleted again' in stale.value.details['fields'][0]['problem']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM sales_deletions').fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind='reversal'",(post['id'],)).fetchone()==(1,)
    # Direct command admission on a now-revoked existing actor, without replacing the producer.
    member=next(x for x in client.membership.list(company=books['company'])['items'] if x['scope_type']=='company' and x['scope_id']==books['company'])
    client.membership.grant(user=member['user_id'],company=books['company'],expected_version=member['version'],grants=[],denies=['transaction.invoice.delete','ledger.post'])
    with pytest.raises(BookflowError) as denied:
        books['run']('invoice delete',dict(invoice=post['id'],expected_version=1,operation_key=('race-a','race-b')[1-loser]),reason='Concurrent deletion')
    assert denied.value.code=='E_PERMISSION' and database(path)==after


@pytest.mark.parametrize('verb',['void','delete'])
def test_work_cancellation_history_uses_release_branch_provenance(books,verb):
    run=books['run'];service=service_item(books,'Partial work provenance')
    source=run('work-order create',dict(customer=books['customer'],date='2017-01-01',title='Two visits',lines=[dict(item=service,quantity='2')]),reason='Create work')
    post=run('work-order invoice',dict(work_order=source['id'],expected_version=source['version'],conversion_key='first-half',date='2017-01-02',percent='50'),reason='Bill half')
    enable(books,'invoice',deny_post=False)
    source=run('work-order show',dict(work_order=source['id']))
    request=dict(work_order=source['id'],expected_version=source['version'],conversion_key='other-half',date='2017-01-02',percent='50')
    preview=run('work-order invoice',request,reason='Preview remainder',dry_run=True)
    run('invoice '+verb,dict(invoice=post['id'],expected_version=1),reason='Release prior half')
    before=database(location(books))
    with pytest.raises(BookflowError) as stale:
        run('work-order invoice',dict(request,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Old preview')
    assert stale.value.code=='E_PREVIEW_STALE'
    changes=stale.value.details['consumption_changes']
    assert len(changes)==1 and changes[0]['transaction_id']==post['id']
    assert changes[0]['changed_fields']==['billing_consumption','status']
    with sqlite3.connect(location(books)) as db:
        assert db.execute('SELECT command FROM audit_events WHERE id=?',(changes[0]['audit_event_id'],)).fetchone()==('invoice '+verb,)
    assert database(location(books))==before
