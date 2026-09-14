"""Owning stock, closed-date and reconciled claims refuse before any Delete effects."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


def purchase(b,item,cost='8'):
    return b['run']('check post',dict(account=b['bank'],date='2017-01-02',amount=str(int(cost)*2),
        items=[dict(item=item,quantity='2',unit_cost=cost)]),reason='Received two items')


def sale(b,item):
    return b['run']('invoice post',dict(customer=b['customer'],date='2017-02-01',
        lines=[dict(item=item,quantity='1')]),reason='Sold one item')


def certify(b):
    run=b['run'];bank=b['bank']
    opening=run('reconcile opening start',dict(operation_key=new_id(),account=bank,
        opening_date='2017-01-01',entered_balance='0',evidence=dict(format=1,statement_reference=None,entered_text='Opening zero'),references=[]),reason='Adopt statement')['draft']
    draft=run('reconcile start',dict(operation_key=new_id(),account=bank,statement_date='2017-01-31',ending_balance='-16',opening_draft_id=opening['id']),reason='Bank statement')['draft']
    rows=run('reconcile candidates',dict(draft=draft['id'],limit=200))['items']
    assert len(rows)==1
    marked=run('reconcile mark',dict(operation_key=new_id(),draft=draft['id'],expected_version=1,
        entries=[dict(movement=x['movement'],group_fingerprint=x['group_fingerprint'],action='mark') for x in rows]),reason='Clear check')['draft']
    preview=run('reconcile preview',dict(draft=draft['id'],expected_version=marked['version']))
    return run('reconcile finish',dict(operation_key=new_id(),draft=draft['id'],expected_version=marked['version'],
        expected_facts_fingerprint=preview['expected_facts_fingerprint'],dependency_guard=preview['dependency_guard']),reason='Certify statement')


@pytest.mark.parametrize('blocker',['negative-stock','closed-receipt','closed-prefix-after-sale','reconciled'])
def test_dependency_refusal_leaves_complete_storage_unchanged(books,blocker):
    item=_inventory_part(books);post=purchase(books,item)
    if blocker=='negative-stock':sale(books,item)
    if blocker=='closed-prefix-after-sale':
        purchase(books,item,'10');sale(books,item)
    if blocker.startswith('closed'):
        books['run']('company update',{'closing_date':'2017-02-01' if blocker=='closed-prefix-after-sale' else '2017-01-02'},reason='Close period')
    if blocker=='reconciled':
        done=certify(books)
        assert done['totals']['difference']==0
    path=location(books);enable(books,'check');before=database(path)
    with pytest.raises(BookflowError) as refused:
        books['run']('check delete',dict(check=post['id'],expected_version=post['version']),reason='Delete dependent receipt')
    assert refused.value.code==('E_RECONCILIATION_DEPENDENCY' if blocker=='reconciled' else 'E_PERIOD_CLOSED' if blocker.startswith('closed') else 'E_VALIDATION')
    assert database(path)==before


def test_delete_recosts_original_sale_date_and_fences_stale_and_deleted_edits(books):
    item=_inventory_part(books);asset=_inventory_asset(books)
    post=purchase(books,item);purchase(books,item,'10');sold=sale(books,item)
    assert _net(books)[asset]==2700 and _net(books)[books['cogs']]==900
    path=location(books);enable(books,'check',deny_post=False,grants=['transaction.check.delete','transaction.card_charge.delete'])
    stale=dict(check=post['id'],expected_version=999)
    before=database(path)
    with pytest.raises(BookflowError) as refusal:books['run']('check delete',stale,reason='Stale request')
    assert refusal.value.code=='E_VERSION_CONFLICT' and database(path)==before
    raw=dict(check=post['id'],expected_version=post['version'],operation_key='recost-delete')
    books['run']('check delete',raw,reason='Remove first purchase')
    assert _net(books,date_to='2017-01-31')=={asset:2000,books['bank']:-2000}
    assert _net(books)[asset]==1000 and _net(books)[books['cogs']]==1000
    # COGS rises 900 -> 1000; its inventory-side correction is -100, on the sale date.
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT effective_date,value_minor_units FROM inventory_movements WHERE kind='recost'").fetchall()==[('2017-02-01',-100)]
    before=database(path)
    for name,selector in (('check update','check'),('check void','check'),('journal update','journal'),('journal void','journal')):
        with pytest.raises(BookflowError):
            books['run'](name,{selector:post['id'],'expected_version':post['version']+1},reason='Do not resurrect')
        assert database(path)==before
    with pytest.raises(BookflowError) as wrong_family:
        books['run']('card-charge delete',dict(card_charge=post['id'],expected_version=post['version']+1),reason='Wrong family')
    assert wrong_family.value.code=='E_RECORD_NOT_FOUND'
    assert database(path)==before
