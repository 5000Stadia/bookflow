"""Actual ordinary allocation branches, reconciliation and dated sale recost."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.test_sales_deletion import service_item
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


@pytest.mark.parametrize('change',['created','removed'])
def test_ordinary_work_allocation_provenance_branches(books,change):
    run=books['run'];item=service_item(books,'Ordinary allocation provenance')
    source=run('work-order create',dict(customer=books['customer'],date='2017-01-01',title='Four visits',lines=[dict(item=item,quantity='4')]),reason='Create scope')
    first=run('work-order invoice',dict(work_order=source['id'],expected_version=source['version'],conversion_key='quarter',date='2017-01-02',percent='25'),reason='First allocation')
    source=run('work-order show',dict(work_order=source['id']))
    request=dict(work_order=source['id'],expected_version=source['version'],conversion_key='preview-quarter',date='2017-01-02',percent='25')
    preview=run('work-order invoice',request,dry_run=True,reason='Preview before ordinary change')
    if change=='created':
        changed=run('work-order invoice',dict(request,conversion_key='other-quarter'),reason='Create another allocation')
    else:
        changed=run('invoice update',dict(invoice=first['id'],expected_version=1,lines=[dict(item=item,quantity='1',unit_price='12')]),reason='Replace linked line with ordinary line')
    before=database(location(books))
    # A newly created allocation advances the source header as well. Use its fresh
    # version while retaining the genuinely stale economic fingerprint.
    request['expected_version']=run('work-order show',dict(work_order=source['id']))['version']
    with pytest.raises(BookflowError) as stale:
        run('work-order invoice',dict(request,expected_facts_fingerprint=preview['facts_fingerprint']),reason='Stale allocation preview')
    assert stale.value.code=='E_PREVIEW_STALE'
    changes=stale.value.details['consumption_changes']
    assert len(changes)==1 and changes[0]['transaction_id']==changed['id']
    assert changes[0]['changed_fields']==['billing_consumption','allocation_revision']
    with sqlite3.connect(location(books)) as db:
        assert db.execute('SELECT command FROM audit_events WHERE id=?',(changes[0]['audit_event_id'],)).fetchone()==('work-order invoice' if change=='created' else 'invoice update',)
    assert database(location(books))==before


def test_receipt_reconciliation_refuses_actual_sale_cancellation(books):
    from bookflow.core.ids import new_id
    run=books['run'];item=service_item(books,'Reconciled sale')
    post=run('sales-receipt post',dict(customer=books['customer'],date='2017-01-02',deposit_to=books['bank'],payment_method=books['methods']['Cash'],lines=[dict(item=item)]),reason='Sale for bank statement')
    opening=run('reconcile opening start',dict(operation_key=new_id(),account=books['bank'],opening_date='2017-01-01',entered_balance='0',evidence=dict(format=1,statement_reference=None,entered_text='Opening zero'),references=[]),reason='Opening')['draft']
    draft=run('reconcile start',dict(operation_key=new_id(),account=books['bank'],statement_date='2017-01-31',ending_balance='12',opening_draft_id=opening['id']),reason='Statement')['draft']
    rows=run('reconcile candidates',dict(draft=draft['id'],limit=200))['items']
    assert len(rows)==1
    marked=run('reconcile mark',dict(operation_key=new_id(),draft=draft['id'],expected_version=1,entries=[dict(movement=x['movement'],group_fingerprint=x['group_fingerprint'],action='mark') for x in rows]),reason='Clear sale')['draft']
    preview=run('reconcile preview',dict(draft=draft['id'],expected_version=marked['version']))
    run('reconcile finish',dict(operation_key=new_id(),draft=draft['id'],expected_version=marked['version'],expected_facts_fingerprint=preview['expected_facts_fingerprint'],dependency_guard=preview['dependency_guard']),reason='Certify bank statement')
    enable(books,'sales-receipt');before=database(location(books))
    for preview in (True,False):
        with pytest.raises(BookflowError) as error:run('sales-receipt delete',dict(sales_receipt=post['id'],expected_version=1),dry_run=preview,reason='Refuse reconciled deletion')
        assert error.value.code=='E_RECONCILIATION_DEPENDENCY' and database(location(books))==before


def test_sale_delete_recosts_later_sale_and_keeps_recost_visible(books):
    run=books['run'];item=_inventory_part(books);asset=_inventory_asset(books)
    def receive(date,cost):run('bill post',dict(vendor=books['vendor'],date=date,items=[dict(item=item,quantity='2',unit_cost=cost)]),reason='Receive stock')
    def sell(date):return run('invoice post',dict(customer=books['customer'],date=date,lines=[dict(item=item,quantity='1',unit_price='12')]),reason='Sell unit')
    receive('2017-01-01','8');first=sell('2017-01-02');receive('2017-01-03','12');later=sell('2017-01-04')
    assert _net(books)[books['cogs']]==1867 and _net(books)[asset]==2133
    enable(books,'invoice');run('invoice delete',dict(invoice=first['id'],expected_version=1),reason='Remove first sale')
    assert _net(books)[books['cogs']]==1000 and _net(books)[asset]==3000
    with sqlite3.connect(location(books)) as db:
        rows=db.execute("SELECT transaction_id,effective_date,value_minor_units FROM inventory_movements WHERE kind='recost'").fetchall()
        assert len(rows)==1 and rows[0][1:]==('2017-01-04',67)
        recost=rows[0][0]
    register=run('register query',dict(account=asset,date_from='2017-01-01',date_to='2017-01-31',limit=200))
    assert recost in {x['transaction_id'] for x in register['rows']}
    assert first['id'] not in {x['transaction_id'] for x in register['rows']}
    assert later['id'] in {x['transaction_id'] for x in register['rows']}
