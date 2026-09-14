"""Exact purchased stock cancellation under Delete, independently of posting authority."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.payment_raw_evidence import database


def enable(books, noun, *, deny_post=True, grants=None):
    client = books['client']
    state = client.permission.show()
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    rows=client.membership.list(company=books['company'])['items']
    member=next(x for x in rows if x['scope_type']=='company' and x['scope_id']==books['company'])
    client.membership.grant(user=member['user_id'],company=books['company'],expected_version=member['version'],
        grants=grants or ['transaction.'+noun.replace('-','_')+'.delete'],denies=['ledger.post'] if deny_post else [])


def location(books):
    return Path(books['client'].company.show(company=books['company'])['path'])/'company.db'


@pytest.mark.parametrize('noun', ['check','card-charge'])
@pytest.mark.parametrize('state', ['posted','zero','voided'])
def test_exact_purchase_delete_without_post_authority(books,noun,state):
    item=_inventory_part(books)
    asset=_inventory_asset(books)
    funding=books['bank'] if noun=='check' else books['client'].account.create(company=books['company'],name='Delete card',type='credit_card')['id']
    free=state=='zero'
    posted=books['run'](noun+' post',dict(account=funding,date='2017-03-03',amount='0' if free else '11',
        items=[dict(item=item,quantity='0.5',unit_cost='0' if free else '12.34')],
        expenses=[] if free else [dict(account=books['freight'],amount='4.83')]),reason='Receive goods')
    selector=noun.replace('-','_')
    if state=='voided':
        posted=books['run'](noun+' void',{selector:posted['id'],'expected_version':posted['version']},reason='Already reversed')
    path=location(books)
    enable(books,noun)
    raw={selector:posted['id'],'expected_version':posted['version'],'operation_key':'permanent-delete'}
    before=database(path)
    for verb in ('update','void'):
        with pytest.raises(BookflowError) as denied:
            books['run'](noun+' '+verb,{selector:posted['id'],'expected_version':posted['version']},reason='Posting denied')
        assert denied.value.code=='E_PERMISSION'
    assert database(path)==before
    preview=books['run'](noun+' delete',raw,reason='Remove duplicate purchase',dry_run=True)
    assert preview['status']=='deleted' and preview['version']==posted['version']+1
    assert preview['cancelled_stock_movements']==(0 if state=='voided' else 1)
    assert database(path)==before
    deleted=books['run'](noun+' delete',raw,reason='Remove duplicate purchase',idempotency_key='generic-delete')
    assert deleted['status']=='deleted' and deleted['version']==posted['version']+1
    assert _net(books)=={}
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?',(item,)).fetchone()==(0,0)
        assert db.execute('SELECT count(*) FROM purchase_deletions').fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM posting_batches WHERE transaction_id=? AND kind=\'reversal\'',(posted['id'],)).fetchone()==(1,)
        assert db.execute('SELECT count(*) FROM posting_lines WHERE transaction_id=?',(posted['id'],)).fetchone()==((0 if free else 6),)
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        for name in ('transaction_revisions','document_lines','money_out_item_lines','money_out_revision_profiles','check_number_reservations'):
            if name in before['tables']:
                assert database(path)['tables'][name]==before['tables'][name]
    after=database(path)
    # No generic retry key: permanent operation witness survives ordinary cache lifetime.
    replay=books['run'](noun+' delete',raw,reason='Remove duplicate purchase')
    assert replay['idempotent_replay'] and not replay['changed'] and replay['id']==deleted['id']
    assert database(path)==after
    with pytest.raises(BookflowError) as mismatch:
        books['run'](noun+' delete',raw,reason='Different intent')
    assert mismatch.value.code=='E_IDEMPOTENCY_MISMATCH' and database(path)==after


def test_late_tombstone_failure_rolls_back_all_cancellation(books,monkeypatch):
    from bookflow.company import purchase_deletions
    item=_inventory_part(books)
    post=books['run']('check post',dict(account=books['bank'],date='2017-03-03',amount='16',
        items=[dict(item=item,quantity='2',unit_cost='8')]),reason='Original receipt')
    path=location(books);enable(books,'check')
    before=database(path)
    original=purchase_deletions.persist_tombstone
    def fail(s,row):
        assert s.company.raw.execute("SELECT count(*) FROM posting_batches WHERE kind='reversal'").fetchone()==(1,)
        assert s.company.raw.execute('SELECT sum(quantity_microunits) FROM inventory_movements').fetchone()==(0,)
        original(s,row)
        raise BookflowError('E_VALIDATION',message='Injected after final tombstone insertion')
    monkeypatch.setattr(purchase_deletions,'persist_tombstone',fail)
    with pytest.raises(BookflowError,match='Injected'):
        books['run']('check delete',dict(check=post['id'],expected_version=post['version']),reason='Atomic failure')
    assert database(path)==before


def test_first_keyed_delete_upgrades_populated_co48_preserving_all_prior_storage(tmp_path,monkeypatch):
    from bookflow.storage.migrate import HEADS
    from bookflow.company import info
    from tests.payment_raw_evidence import table
    # Actual registered historical-compatible commands, not a synthetic schema pin.
    with monkeypatch.context() as historical:
        historical.setitem(HEADS,'company','co0048')
        b=books.__wrapped__(tmp_path,historical)
        item=_inventory_part(b)
        post=b['run']('check post',dict(account=b['bank'],date='2017-03-03',amount='16',
            items=[dict(item=item,quantity='2',unit_cost='8')]),reason='Existing co48 purchase')
        b['run']('check update',dict(check=post['id'],memo='Captured prior revision'),reason='Existing correction')
        post=b['run']('check show',dict(check=post['id']))
        path=location(b);enable(b,'check')
    # This witness owns the historical co48->co49 transition and its first writer.
    monkeypatch.setitem(HEADS, 'company', 'co0049')
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(tmp_path/'items'))
    before=database(path)
    from bookflow.storage import migrate
    original=migrate.migrate_to_head
    witnessed=[]
    def observing(db,chain,*args,**kw):
        result=original(db,chain,*args,**kw)
        if chain=='company' and result==('co0048','co0049'):
            for name,value in before['tables'].items():
                if name!='alembic_version':assert table(db.raw,name)==value,name
            assert set(before['ddl']) <= set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
            witnessed.append(result)
        return result
    monkeypatch.setattr(migrate,'migrate_to_head',observing)
    result=b['run']('check delete',dict(check=post['id'],expected_version=post['version']),
        reason='First migrated keyed delete',idempotency_key='co48-first-delete')
    assert witnessed==[('co0048','co0049')]
    assert result['status']=='deleted' and result['cancelled_stock_movements']==1
    assert _net(b)=={}
