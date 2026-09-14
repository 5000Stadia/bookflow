"""Receiving quantity and liability witnesses through registered public commands."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.payment_raw_evidence import database


def path(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def test_receipt_lifecycle_keeps_metadata_physical_identity_and_zero_history(books):
    run = books['run']; item = _inventory_part(books); asset = _inventory_asset(books)
    raw = dict(vendor=books['vendor'], date='2017-01-05', items=[dict(item=item, quantity='6', unit_cost='10.00')])
    before = database(path(books))
    preview = run('item-receipt post', raw, dry_run=True, reason='Preview receipt')
    assert preview['total']['minor_units'] == 6000
    assert database(path(books)) == before
    receipt = run('item-receipt post', raw, reason='Receive goods', idempotency_key='receive-six')
    assert receipt['items'][0]['quantity_microunits'] == 6000000
    assert receipt['items'][0]['unbilled_quantity_microunits'] == 6000000
    assert _net(books) == {asset: 6000, books['payable']: -6000}
    saved = database(path(books))
    assert run('item-receipt post', raw, reason='Receive goods', idempotency_key='receive-six')['id'] == receipt['id']
    assert database(path(books)) == saved
    assert run('bill query', {})['items'] == []
    memo = run('item-receipt update', dict(receipt=receipt['id'], expected_version=1, memo='Dock counted six'), reason='Record dock note')
    assert memo['items'] == receipt['items']
    assert memo['financial_revision_id'] == receipt['financial_revision_id']
    assert memo['version'] == 2
    assert run('item-receipt show', dict(receipt=receipt['id'], revision_number=1))['memo'] is None
    assert run('item-receipt query', {})['count'] == 1
    assert len(run('item-receipt history', dict(receipt=receipt['id']))['items']) == 2
    free = run('item-receipt update', dict(receipt=receipt['id'], expected_version=2,
        items=[dict(line_id=memo['items'][0]['line_id'], item=item, quantity='6', unit_cost='0.00')]), reason='Supplier confirmed free goods')
    assert free['total']['minor_units'] == 0
    assert _net(books) == {}
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone() == (6000000, 0)
        assert db.execute('SELECT count(*) FROM ap_obligation_keys').fetchone() == (0,)
        assert db.execute('SELECT posting_line_id FROM inventory_movements WHERE id=?', (free['items'][0]['movement_id'],)).fetchone() == (None,)
    voided = run('item-receipt void', dict(receipt=receipt['id'], expected_version=3), reason='Return goods')
    assert voided['status'] == 'voided'
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone() == (0, 0)
    saved = database(path(books))
    assert run('item-receipt void', dict(receipt=receipt['id']), reason='Already returned')['changed'] is False
    assert database(path(books)) == saved


def test_order_quantity_claims_are_separate_from_legacy_whole_order_consumption(books):
    run = books['run']; item = _inventory_part(books)
    po = run('purchase-order post', dict(date='2017-01-01', vendor=books['vendor'],
        lines=[dict(item=item, quantity='10', rate='10.00')]), reason='Order ten')
    line = po['revision']['lines'][0]['line_id']
    raw = dict(date='2017-01-05', vendor=books['vendor'], purchase_order=po['id'], purchase_order_version=1,
        items=[dict(item=item, order_line_id=line, quantity='6', unit_cost='10.00')])
    a = run('item-receipt post', raw, reason='Receive six')
    after_a = run('purchase-order show', dict(purchase_order=po['id']))
    assert after_a['version'] == 2
    assert after_a['status'] == 'partly_received'
    assert after_a['receiving'] == [dict(order_line_id=line, item_id=item, ordered_quantity_microunits=10000000,
        received_quantity_microunits=6000000, remaining_quantity_microunits=4000000)]
    before = database(path(books))
    with pytest.raises(BookflowError) as exc:
        run('item-receipt post', raw, reason='Stale selection')
    assert exc.value.code == 'E_VERSION_CONFLICT'
    assert database(path(books)) == before
    b = run('item-receipt post', dict(raw, purchase_order_version=2,
        items=[dict(item=item, order_line_id=line, quantity='2', unit_cost='12.00')]), reason='Receive two')
    assert run('purchase-order show', dict(purchase_order=po['id']))['receiving'][0]['remaining_quantity_microunits'] == 2000000
    before = database(path(books))
    with pytest.raises(BookflowError) as exc:
        run('bill post', dict(purchase_order=po['id'], date='2017-01-15'), reason='Prevent whole order duplicate receipt')
    assert exc.value.code == 'E_WORK_DEPENDENCY'
    assert database(path(books)) == before
    run('item-receipt void', dict(receipt=b['id']), reason='Return second shipment')
    assert run('purchase-order show', dict(purchase_order=po['id']))['receiving'][0]['remaining_quantity_microunits'] == 4000000
    run('item-receipt void', dict(receipt=a['id']), reason='Return first shipment')
    assert run('purchase-order show', dict(purchase_order=po['id']))['receiving'][0]['remaining_quantity_microunits'] == 10000000


def test_linked_bill_price_changes_use_receipt_sale_and_bill_dates_without_more_quantity(books):
    run = books['run']; item = _inventory_part(books); asset = _inventory_asset(books)
    a = run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'],
        items=[dict(item=item, quantity='6', unit_cost='10.00')]), reason='Receive six')
    b = run('item-receipt post', dict(date='2017-01-08', vendor=books['vendor'],
        items=[dict(item=item, quantity='2', unit_cost='12.00')]), reason='Receive two')
    run('invoice post', dict(date='2017-01-10', customer=books['customer'],
        lines=[dict(item=item, quantity='3', unit_price='0.00')]), reason='Issue three')
    assert _net(books) == {asset: 5250, books['cogs']: 3150, books['payable']: -8400}
    raw = dict(date='2017-01-15', receipts=[
        dict(receipt_line=a['items'][0]['id'], expected_receipt_version=1, quantity='4', unit_cost='11.00'),
        dict(receipt_line=b['items'][0]['id'], expected_receipt_version=1, quantity='1', unit_cost='13.00')])
    before = database(path(books))
    preview = run('bill post', raw, reason='Preview matching bill', dry_run=True)
    assert preview['total_minor_units'] == 5700
    assert database(path(books)) == before
    bill = run('bill post', raw, reason='Match vendor bill', idempotency_key='matched-bill')
    assert bill['total_minor_units'] == 5700
    assert [line['quantity_microunits'] for line in bill['revision']['items']] == [4000000, 1000000]
    assert _net(books) == {asset: 5562, books['cogs']: 3338, books['payable']: -8900}
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone() == (5000000,5562)
        assert db.execute("SELECT count(*) FROM inventory_movements WHERE kind='receipt'").fetchone() == (2,)
        assert db.execute('SELECT sum(end_microunits-start_microunits),sum(original_minor_units),sum(billed_minor_units) FROM receipt_bill_claims').fetchone() == (5000000,5200,5700)
        for cutoff, expected in [('2017-01-05',(6400,0,-6400)),('2017-01-08',(8900,0,-8900)),('2017-01-10',(5562,3338,-8900)),('2017-01-15',(5562,3338,-8900))]:
            actual = tuple(db.execute('SELECT coalesce(sum(l.debit_minor_units-l.credit_minor_units),0) FROM posting_lines l JOIN posting_batches b ON b.id=l.batch_id WHERE l.account_id=? AND b.effective_date<=?',(account,cutoff)).fetchone()[0] for account in (asset,books['cogs'],books['payable']))
            assert actual == expected
        assert db.execute('SELECT sum(amount_minor_units) FROM ap_obligation_components WHERE transaction_id=?', (bill['id'],)).fetchone() == (5700,)
        assert db.execute('SELECT sum(original_minor_units) FROM receipt_bill_claims').fetchone() == (5200,)
        # Receipt liability: original 8400 + actual corrections 500 - transferred 5700.
        assert 8400 + db.execute('SELECT sum(m.value_minor_units) FROM inventory_movements m JOIN receipt_bill_adjustments a ON a.movement_id=m.id').fetchone()[0] - 5700 == 3200
        assert bill['settlement_current']['open_minor_units'] == 5700
    for cutoff, qty, value in [('2017-01-05', '6', 6400), ('2017-01-08', '8', 8900), ('2017-01-10', '5', 5562), ('2017-01-15', '5', 5562)]:
        status = run('report stock-status', dict(as_of=cutoff, limit=200))
        row = next(row for row in status['rows'] if row['item_id'] == item)
        assert row['quantity_on_hand'] == qty
        assert row['asset_value']['minor_units'] == value
        assert run('report inventory-valuation', dict(as_of=cutoff, limit=200))['totals']['asset_value']['minor_units'] == value
    saved = database(path(books))
    assert run('bill post', raw, reason='Match vendor bill', idempotency_key='matched-bill')['id'] == bill['id']
    assert database(path(books)) == saved
    assert run('item-receipt show',dict(receipt=a['id']))['items'][0]['unbilled_quantity_microunits'] == 2000000
    with pytest.raises(BookflowError) as exc:
        run('item-receipt void', dict(receipt=a['id']), reason='Refuse linked receipt return')
    assert exc.value.code == 'E_WORK_DEPENDENCY'
    assert database(path(books)) == saved
    run('bill void',dict(bill=bill['id']),reason='Undo matching bill')
    assert _net(books) == {asset:5250,books['cogs']:3150,books['payable']:-8400}
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone() == (5000000,5250)
        assert db.execute("SELECT count(*) FROM inventory_movements WHERE kind='receipt'").fetchone() == (2,)
        assert db.execute('SELECT count(*) FROM receipt_bill_releases').fetchone() == (2,)


def test_equal_price_open_bill_does_not_touch_closed_receipt_but_price_change_refuses(books):
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-05',vendor=books['vendor'],items=[dict(item=item,quantity='10',amount='100.01')]),reason='Receive ten')
    run('company update',dict(closing_date='2017-01-10'),reason='Close acquisition period')
    selection=dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='3')
    before=database(path(books))
    with pytest.raises(BookflowError) as exc:
        run('bill post',dict(date='2017-01-15',receipts=[dict(selection,amount='31.00')]),reason='Refuse closed price correction')
    assert exc.value.code=='E_PERIOD_CLOSED'
    assert database(path(books))==before
    bill=run('bill post',dict(date='2017-01-15',receipts=[selection]),reason='Transfer equal price in open period')
    assert bill['total_minor_units']==3000
    with sqlite3.connect(path(books)) as db:
        assert db.execute("SELECT count(*) FROM inventory_movements").fetchone()==(1,)
    run('bill void',dict(bill=bill['id']),reason='Release equal price claim')
    with sqlite3.connect(path(books)) as db:
        assert db.execute("SELECT count(*) FROM inventory_movements").fetchone()==(1,)


def test_persisted_middle_interval_release_reclaim_and_zero_bill(books):
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-05',vendor=books['vendor'],items=[dict(item=item,quantity='10',amount='100.01')]),reason='Receive original basis')
    bills=[]
    for qty,expected in [('3',3000),('2',2001),('3',3000),('2',2000)]:
        shown=run('item-receipt show',dict(receipt=receipt['id']))
        bill=run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity=qty)]),reason='Match interval')
        assert bill['total_minor_units']==expected
        bills.append(bill)
    before=database(path(books))
    shown=run('item-receipt show',dict(receipt=receipt['id']))
    with pytest.raises(BookflowError) as exc:
        run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity='1')]),reason='Refuse exhausted quantity')
    assert exc.value.code=='E_WORK_DEPENDENCY'
    assert database(path(books))==before
    run('bill void',dict(bill=bills[1]['id']),reason='Release middle interval')
    shown=run('item-receipt show',dict(receipt=receipt['id']))
    reclaimed=run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity='2')]),reason='Reclaim exact middle interval')
    assert reclaimed['total_minor_units']==2001
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT start_microunits,end_microunits,original_minor_units FROM receipt_bill_claims WHERE bill_id=?',(reclaimed['id'],)).fetchall()==[(3000000,5000000,2001)]
    run('bill void',dict(bill=reclaimed['id']),reason='Release middle again')
    run('bill void',dict(bill=bills[3]['id']),reason='Release last interval')
    shown=run('item-receipt show',dict(receipt=receipt['id']))
    disjoint=run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity='3')]),reason='Claim disjoint intervals')
    assert disjoint['total_minor_units']==3001
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT start_microunits,end_microunits,original_minor_units FROM receipt_bill_claims WHERE bill_id=? ORDER BY start_microunits',(disjoint['id'],)).fetchall()==[(3000000,5000000,2001),(8000000,9000000,1000)]
    free=run('item-receipt post',dict(date='2017-01-05',vendor=books['vendor'],items=[dict(item=item,quantity='1',unit_cost='0.00')]),reason='Free additional unit')
    zero=run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=free['items'][0]['id'],expected_receipt_version=1,quantity='1')]),reason='Confirm free vendor bill')
    assert zero['total_minor_units']==0
    corrected=run('bill update',dict(bill=zero['id'],memo='Confirmed free'),reason='Retain free mapping')
    assert corrected['total_minor_units']==0
    run('bill void',dict(bill=zero['id']),reason='Release free mapping')
    assert run('item-receipt show',dict(receipt=free['id']))['items'][0]['unbilled_quantity_microunits']==1000000


def test_same_writer_claim_race_and_late_failure_leave_exact_state(books, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bookflow.company import receipt_billing
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-05',vendor=books['vendor'],items=[dict(item=item,quantity='10',unit_cost='10.00')]),reason='Receive race stock')
    raw=dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='6')])
    barrier=Barrier(2)
    def compete(index):
        barrier.wait(timeout=10)
        try:
            return run('bill post',raw,reason='Competing bill',idempotency_key='competing-'+str(index))
        except BookflowError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(compete,range(2)))
    successful=[r for r in results if isinstance(r,dict)]
    refused=[r for r in results if isinstance(r,BookflowError)]
    assert len(successful)==len(refused)==1
    if refused[0].code == 'E_DB_BUSY':
        # The host may refuse the concurrent caller at its lock before it can
        # enter the company writer. Its exact retry must then see stale claims.
        unchanged = database(path(books))
        loser = next(i for i, result in enumerate(results) if isinstance(result, BookflowError))
        with pytest.raises(BookflowError) as stale:
            run('bill post', raw, reason='Competing bill', idempotency_key='competing-' + str(loser))
        assert stale.value.code == 'E_VERSION_CONFLICT'
        assert database(path(books)) == unchanged
    else:
        assert refused[0].code == 'E_VERSION_CONFLICT'
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT sum(end_microunits-start_microunits) FROM receipt_bill_claims').fetchone()==(6000000,)
        assert db.execute("SELECT count(*) FROM transactions WHERE type='bill'").fetchone()==(1,)
        assert db.execute("SELECT sum(quantity_microunits) FROM inventory_movements").fetchone()==(10000000,)
    current=run('item-receipt show',dict(receipt=receipt['id']))
    remaining=dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=current['version'],quantity='4',unit_cost='11.00')])
    before=database(path(books)); real_apply=receipt_billing.apply
    def fail_after_all_writes(*args,**kwargs):
        real_apply(*args,**kwargs)
        raise BookflowError('E_INTERNAL',message='Owned injected failure after cost, bill and claim writes')
    with monkeypatch.context() as patch:
        patch.setattr(receipt_billing,'apply',fail_after_all_writes)
        with pytest.raises(BookflowError) as exc:
            run('bill post',remaining,reason='Injected atomic rollback',idempotency_key='late-failure')
        assert exc.value.code=='E_INTERNAL'
    assert database(path(books))==before
    assert run('bill post',remaining,reason='Injected atomic rollback',idempotency_key='late-failure')['total_minor_units']==4400


def test_co46_populated_commercial_and_movement_history_is_preserved(tmp_path,monkeypatch):
    import importlib
    from bookflow.storage.migrate import HEADS,migrate_to_head
    from bookflow.storage.engine import open_database
    from tests.payment_raw_evidence import table
    current=HEADS['company']
    monkeypatch.setitem(HEADS,'company','co0046')
    old=books.__wrapped__(tmp_path,monkeypatch);run=old['run'];item=_inventory_part(old)
    bill=run('bill post',dict(date='2017-01-01',vendor=old['vendor'],items=[dict(item=item,quantity='2',unit_cost='10.00')]),reason='Populated old purchase')
    run('invoice post',dict(date='2017-01-02',customer=old['customer'],lines=[dict(item=item,quantity='1',unit_price='15.00')]),reason='Populated old sale')
    po=run('purchase-order post',dict(date='2017-01-01',vendor=old['vendor'],lines=[dict(item=item,quantity='3',rate='12.00')]),reason='Legacy order')
    run('bill post',dict(date='2017-01-03',purchase_order=po['id']),reason='Legacy whole-order bill')
    dbpath=path(old)
    before=database(dbpath)
    assert before['tables']['inventory_movements']['count']==3
    assert before['tables']['purchase_order_conversions']['count']==1
    monkeypatch.setitem(HEADS,'company',current)
    migration=importlib.import_module('bookflow.storage.company_migrations.versions.0047_receiving')
    with open_database(dbpath,writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'owned-backups')==('co0046',current)
        for name,snapshot in before['tables'].items():
            if name!='alembic_version':
                assert table(db.raw,name)==snapshot,name
        assert set(before['ddl'])<=set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        for guard in migration.GUARDS:
            assert db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(guard.split()[2],)).fetchone()==(guard,)
    assert run('bill show',dict(bill=bill['id']))['total_minor_units']==2000
    legacy=run('purchase-order show',dict(purchase_order=po['id']))
    assert legacy['consumed'] is True
    assert legacy['receiving'][0]['remaining_quantity_microunits']==0
    before=database(path(old))
    with pytest.raises(BookflowError) as exc:
        run('item-receipt post',dict(date='2017-01-05',vendor=old['vendor'],purchase_order=po['id'],purchase_order_version=legacy['version'],items=[dict(item=item,order_line_id=legacy['revision']['lines'][0]['line_id'],quantity='1',unit_cost='12.00')]),reason='Refuse reopened legacy capacity')
    assert exc.value.code=='E_WORK_DEPENDENCY'
    assert database(path(old))==before


def test_linked_bill_header_edit_preserves_closed_cost_dates_and_payment_never_receives(books):
    run=books['run'];item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-05',vendor=books['vendor'],items=[dict(item=item,quantity='2',unit_cost='10.00')]),reason='Receive goods')
    bill=run('bill post',dict(date='2017-01-15',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='2',unit_cost='11.00')]),reason='Price received goods')
    run('company update',dict(closing_date='2017-01-10'),reason='Close physical period')
    with sqlite3.connect(path(books)) as db:
        original_movements=db.execute('SELECT * FROM inventory_movements ORDER BY sequence').fetchall()
    edited=run('bill update',dict(bill=bill['id'],memo='Supplier invoice note'),reason='Only annotate open bill')
    assert edited['total_minor_units']==2200
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT * FROM inventory_movements ORDER BY sequence').fetchall()==original_movements
    paid=run('bill pay',dict(date='2017-01-20',bills=[dict(bill=bill['id'])],funding_account=books['bank'],method=books['methods']['Check']),reason='Pay actual bill')
    assert paid['paid']['minor_units']==2200
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT * FROM inventory_movements ORDER BY sequence').fetchall()==original_movements
    before=database(path(books))
    with pytest.raises(BookflowError) as exc:
        run('bill void',dict(bill=bill['id']),reason='Refuse paid bill void')
    assert exc.value.code=='E_HAS_APPLICATIONS'
    assert database(path(books))==before


def test_zero_receipt_reprices_original_zero_issue_and_returns_to_zero(books):
    run = books['run']; item = _inventory_part(books); asset = _inventory_asset(books)
    receipt = run('item-receipt post', dict(date='2017-01-05', vendor=books['vendor'],
        items=[dict(item=item, quantity='6', unit_cost='0.00')]), reason='Receive free-valued goods')
    run('invoice post', dict(date='2017-01-10', customer=books['customer'],
        lines=[dict(item=item, quantity='3', unit_price='0.00')]), reason='Give three goods')
    assert _net(books) == {}
    with sqlite3.connect(path(books)) as db:
        physical = db.execute("SELECT * FROM inventory_movements WHERE kind IN ('receipt','issue') ORDER BY id").fetchall()
        assert len(physical) == 2
    selection = dict(receipt_line=receipt['items'][0]['id'], expected_receipt_version=1, quantity='6', amount='24.00')
    bill = run('bill post', dict(date='2017-01-15', receipts=[selection]), reason='Confirm actual cost')
    assert bill['total_minor_units'] == 2400
    assert _net(books) == {asset: 1200, books['cogs']: 1200, books['payable']: -2400}
    shown = run('item-receipt show', dict(receipt=receipt['id']))
    assert shown['receipt_liability_current']['minor_units'] == 0
    free = run('bill update', dict(bill=bill['id'], expected_version=bill['version'],
        receipts=[dict(selection, expected_receipt_version=shown['version'], amount='0.00')]), reason='Vendor confirms free goods')
    assert free['total_minor_units'] == 0
    assert free['settlement_current']['open_minor_units'] == 0
    assert _net(books) == {}
    with sqlite3.connect(path(books)) as db:
        assert db.execute("SELECT * FROM inventory_movements WHERE kind IN ('receipt','issue') ORDER BY id").fetchall() == physical
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone() == (3000000, 0)
    run('bill void', dict(bill=bill['id']), reason='Release free confirmation')
    assert _net(books) == {}
    shown = run('item-receipt show', dict(receipt=receipt['id']))
    assert shown['items'][0]['unbilled_quantity_microunits'] == 6000000
