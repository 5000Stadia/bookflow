"""Independent receipt shipping, bill components and dated inventory witnesses."""
import sqlite3
from pathlib import Path
import pytest
from bookflow import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from tests.payment_raw_evidence import database


def path(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def test_shipping_receipt_partial_bill_product_reprice_and_void(books):
    run=books['run']; item=_inventory_part(books); asset=_inventory_asset(books)
    raw=dict(date='2017-01-05',vendor=books['vendor'],shipping='12.00',items=[dict(item=item,quantity='3',unit_cost='8.00')])
    before=database(path(books))
    preview=run('item-receipt post',raw,dry_run=True,reason='Preview shipping')
    assert preview['total']['minor_units']==3600
    assert database(path(books))==before
    receipt=run('item-receipt post',raw,idempotency_key='shipping',reason='Receive three')
    assert (receipt['product_total']['minor_units'],receipt['shipping']['minor_units'])==(2400,1200)
    assert [receipt['items'][0][k] for k in ('product_per_unit','shipping_per_unit','total_per_unit')]==['8','4','12']
    saved=database(path(books))
    assert run('item-receipt post',raw,idempotency_key='shipping',reason='Receive three')['id']==receipt['id']
    assert database(path(books))==saved
    sale=run('invoice post',dict(date='2017-01-10',customer=books['customer'],lines=[dict(item=item,quantity='1',unit_price='0.00')]),reason='Free sample')
    assert _net(books)=={asset:2400,books['cogs']:1200,books['payable']:-3600}
    selection=dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='2',unit_cost='8.00')
    bill=run('bill post',dict(date='2017-01-15',receipts=[selection]),reason='Bill product plus retained shipping')
    assert bill['total_minor_units']==2400
    assert bill['revision']['receipts'][0]['shipping']['minor_units']==800
    assert _net(books)=={asset:2400,books['cogs']:1200,books['payable']:-3600}
    current=run('item-receipt show',dict(receipt=receipt['id']))
    assert current['receipt_liability_current']['minor_units']==1200
    changed=run('bill update',dict(bill=bill['id'],receipts=[dict(selection,expected_receipt_version=current['version'],unit_cost='9.00')]),reason='Correct only product price')
    assert changed['total_minor_units']==2600
    assert _net(books)=={asset:2533,books['cogs']:1267,books['payable']:-3800}
    metadata=run('bill update',dict(bill=bill['id'],memo='Keep product basis'),reason='Annotate bill')
    assert metadata['revision']['receipts'][0]['unit_cost']['minor_units']==900
    assert metadata['total_minor_units']==2600
    saved=database(path(books))
    with pytest.raises(BookflowError) as error:
        run('item-receipt update',dict(receipt=receipt['id'],shipping='0.00'),reason='Cannot remove billed shipping')
    assert error.value.code=='E_WORK_DEPENDENCY' and database(path(books))==saved
    run('bill void',dict(bill=bill['id']),reason='Release bill')
    assert _net(books)=={asset:2400,books['cogs']:1200,books['payable']:-3600}
    run('invoice void',dict(invoice=sale['id']),reason='Return sample')
    run('item-receipt void',dict(receipt=receipt['id']),reason='Return shipment')
    assert _net(books)=={}


def test_shipping_component_pennies_survive_middle_release_and_product_zero(books):
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],shipping='0.01',items=[dict(item=item,quantity='3',amount='0.01')]),reason='Two independent pennies')
    bills=[]
    for product,shipping in [(0,0),(1,1),(0,0)]:
        shown=run('item-receipt show',dict(receipt=receipt['id']))
        bill=run('bill post',dict(date='2017-01-02',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity='1')]),reason='Claim exact penny interval')
        assert bill['total_minor_units']==product+shipping
        assert bill['revision']['receipts'][0]['shipping']['minor_units']==shipping
        bills.append(bill)
    run('bill void',dict(bill=bills[1]['id']),reason='Release middle pennies')
    shown=run('item-receipt show',dict(receipt=receipt['id']))
    bill=run('bill post',dict(date='2017-01-02',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=shown['version'],quantity='1',amount='0.00')]),reason='Free product retains shipping penny')
    assert bill['total_minor_units']==1
    with sqlite3.connect(path(books)) as db:
        assert db.execute('SELECT start_microunits,end_microunits,original_minor_units,billed_minor_units,shipping_minor_units FROM receipt_bill_claims WHERE bill_id=?',(bill['id'],)).fetchall()==[(1000000,2000000,2,1,1)]


def test_shipping_fractional_allocations_and_closed_metadata(books):
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],shipping='0.05',items=[
        dict(item=item,quantity='0.5',unit_cost='8.00'),
        dict(item=item,quantity='1.5',amount='0.00'),
        dict(item=item,quantity='1',unit_cost='8.00')]),reason='Receive fractional quantities')
    assert [x['shipping']['minor_units'] for x in receipt['items']]==[1,2,2]
    assert [x['product_amount']['minor_units'] for x in receipt['items']]==[400,0,800]
    assert receipt['total']['minor_units']==1205
    run('company update',dict(closing_date='2017-01-10'),reason='Close receipt date')
    edited=run('item-receipt update',dict(receipt=receipt['id'],shipping='0.05',memo='Same captured cost'),reason='Annotate closed receipt')
    assert edited['items']==receipt['items']
    assert edited['financial_revision_id']==receipt['financial_revision_id']
    selection=dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=edited['version'],quantity='0.5',unit_cost='8.00')
    before=database(path(books))
    with pytest.raises(BookflowError) as error:
        run('bill post',dict(date='2017-01-15',receipts=[dict(selection,unit_cost='9.00')]),reason='Refuse historical repricing')
    assert error.value.code=='E_PERIOD_CLOSED'
    assert database(path(books))==before
    bill=run('bill post',dict(date='2017-01-15',receipts=[selection]),reason='Equal product cost carries shipping')
    assert bill['total_minor_units']==401
    assert bill['revision']['receipts'][0]['shipping']['minor_units']==1


def test_shipping_zero_corrections_recost_original_free_issue(books):
    run=books['run']; item=_inventory_part(books); asset=_inventory_asset(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],items=[dict(item=item,quantity='3',unit_cost='0.00')]),reason='Free received products')
    sale=run('invoice post',dict(date='2017-01-05',customer=books['customer'],lines=[dict(item=item,quantity='1',unit_price='0.00')]),reason='Free sample')
    assert _net(books)=={}
    for shipping,expected in [('12.00',{asset:800,books['cogs']:400,books['payable']:-1200}),('0.00',{}),('12.00',{asset:800,books['cogs']:400,books['payable']:-1200})]:
        changed=run('item-receipt update',dict(receipt=receipt['id'],shipping=shipping),reason='Correct shipping')
        assert changed['product_total']['minor_units']==0
        assert _net(books)==expected
    history=run('item-receipt history',dict(receipt=receipt['id']))
    assert len(history['items'])==4
    original=run('item-receipt show',dict(receipt=receipt['id'],revision_number=1))
    assert original['shipping']['minor_units']==0


def test_shipping_multi_item_bill_identity_survives_edit_and_reclaim(books):
    run=books['run']; first=_inventory_part(books)
    second=run('item create',dict(name='Second received item',type='inventory_part',description='Second product',purchase_description='Second received product',income_account_id=books['income'],cogs_account_id=books['cogs'],price='10.00',cost='2.00'),reason='Second product')['id']
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],shipping='3.00',items=[dict(item=first,quantity='1',unit_cost='8.00'),dict(item=second,quantity='2',unit_cost='2.00')]),reason='Different items and components')
    def selections():
        version=run('item-receipt show',dict(receipt=receipt['id']))['version']
        return [dict(receipt_line=x['id'],expected_receipt_version=version,quantity=x['quantity']) for x in reversed(receipt['items'])]
    def check(bill):
        rows={x['item']['id']:x for x in bill['revision']['receipts']}
        assert {i:(x['product_amount']['minor_units'],x['shipping']['minor_units']) for i,x in rows.items()}=={first:(800,100),second:(400,200)}
        for line in bill['revision']['items']:
            assert rows[line['item_id']]['bill_line_id']==line['id']
    bill=run('bill post',dict(date='2017-01-02',receipts=selections()),reason='Bill reverse item order')
    check(bill)
    check(run('bill update',dict(bill=bill['id'],memo='Keep captured identities'),reason='Edit bill metadata'))
    check(run('bill show',dict(bill=bill['id'])))
    run('bill void',dict(bill=bill['id']),reason='Release exact claims')
    check(run('bill post',dict(date='2017-01-02',receipts=selections()),reason='Reclaim item intervals'))


def test_shipping_component_corruption_refuses_without_writes(books,monkeypatch):
    from bookflow.company import receipt_billing
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],shipping='0.01',items=[dict(item=item,quantity='3',amount='0.01')]),reason='Independent component witness')
    real=receipt_billing.component_span
    def corrupt(*args):
        product,shipping=real(*args)
        return product+1,shipping
    monkeypatch.setattr(receipt_billing,'component_span',corrupt)
    before=database(path(books))
    with pytest.raises(BookflowError) as error:
        run('bill post',dict(date='2017-01-02',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='1')]),reason='Reject corrupt original product allocation')
    assert error.value.code=='E_INTERNAL'
    assert database(path(books))==before


def test_co47_populated_receiving_contract_preserved_by_shipping_migration(books,tmp_path,monkeypatch):
    """Synthetic co47 fixture projected from valid zero-shipping lifecycle facts.

    This proves the populated storage migration, not execution by an old binary.
    Restore the exact historical triggers before taking the migration boundary.
    """
    from bookflow.storage.migrate import HEADS,migrate_to_head
    from bookflow.storage.engine import open_database
    from tests.payment_raw_evidence import preserved
    run=books['run']; item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],items=[dict(item=item,quantity='3',unit_cost='8.00')]),reason='Legacy compatible receipt')
    selection=dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='1',unit_cost='9.00')
    released=run('bill post',dict(date='2017-01-02',receipts=[selection]),reason='Legacy price correction')
    run('bill void',dict(bill=released['id']),reason='Legacy released claim')
    version=run('item-receipt show',dict(receipt=receipt['id']))['version']
    run('bill post',dict(date='2017-01-03',receipts=[dict(selection,expected_receipt_version=version)]),reason='Legacy active claim')
    oldpath=tmp_path/'populated-co47.db'
    current=HEADS['company']
    with monkeypatch.context() as historical:
        historical.setitem(HEADS,'company','co0047')
        with open_database(oldpath,writable=True,create=True) as old:
            migrate_to_head(old,'company',None)
    with sqlite3.connect(oldpath) as old, sqlite3.connect(path(books)) as source:
        triggers=old.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'").fetchall()
        for name,_ in triggers: old.execute('DROP TRIGGER "'+name+'"')
        tables=[x[0] for x in old.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'")]
        for name in tables:
            columns=[x[1] for x in old.execute('PRAGMA table_info("'+name+'")')]
            fields=','.join('"'+x+'"' for x in columns)
            rows=source.execute('SELECT rowid,'+fields+' FROM "'+name+'" ORDER BY rowid').fetchall()
            old.execute('DELETE FROM "'+name+'"')
            old.executemany('INSERT INTO "'+name+'"(rowid,'+fields+') VALUES('+','.join('?' for _ in range(len(columns)+1))+')',rows)
        for _,sql in triggers: old.execute(sql)
        assert old.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert old.execute('SELECT count(*) FROM receipt_bill_claims').fetchone()==(2,)
        assert old.execute('SELECT count(*) FROM receipt_bill_releases').fetchone()==(1,)
        assert old.execute('SELECT count(*) FROM receipt_bill_adjustments').fetchone()==(2,)
    before=database(oldpath)
    with open_database(oldpath,writable=True) as old:
        assert migrate_to_head(old,'company',tmp_path/'migration-backups')==('co0047',current)
        for name,snapshot in before['tables'].items():
            if name!='alembic_version': assert preserved(old.raw,name,snapshot)==snapshot,name
        afterddl=set(old.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        import importlib
        additions=importlib.import_module('bookflow.storage.company_migrations.versions.0048_receiving_shipping').COLUMNS
        for kind,name,_,sql in before['ddl']:
            if kind=='table' and name in additions:
                actual=old.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone()[0]
                assert actual.replace(', '+additions[name], '', 1)==sql,name
        for row in before['ddl']:
            if row[0]!='table' or row[1] not in {'item_receipt_lines','receipt_bill_claims'}:
                assert row in afterddl,row
        assert old.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        for name in ('item_receipt_lines','receipt_bill_claims'):
            assert old.raw.execute('SELECT DISTINCT shipping_minor_units FROM '+name).fetchall()==[(0,)]


def test_shipping_storage_checks_are_not_masked_by_ownership_triggers(books):
    run=books['run'];item=_inventory_part(books)
    receipt=run('item-receipt post',dict(date='2017-01-01',vendor=books['vendor'],shipping='2.00',items=[dict(item=item,quantity='2',unit_cost='8.00')]),reason='Valid shipping guard owners')
    run('bill post',dict(date='2017-01-02',receipts=[dict(receipt_line=receipt['items'][0]['id'],expected_receipt_version=1,quantity='1')]),reason='Valid claim guard owners')
    before=database(path(books))
    with sqlite3.connect(path(books)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for table,constraint,bad_values in [('receipt_bill_claims','ck_receipt_claim_shipping',[-1,0.5,901]),('item_receipt_lines','ck_receipt_shipping',[-1,0.5,1801])]:
            db.execute('BEGIN')
            db.execute('PRAGMA defer_foreign_keys=ON')
            columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
            values=list(db.execute('SELECT * FROM '+table+' LIMIT 1').fetchone())
            guard=table+'_immutable_delete'
            sql=db.execute('SELECT sql FROM sqlite_schema WHERE name=?',(guard,)).fetchone()[0]
            # Remove only the original row inside an always-rolled-back fixture
            # transaction, then restore its immutable guard before any probe.
            db.execute('DROP TRIGGER '+guard)
            db.execute('DELETE FROM '+table+' WHERE id=?',(values[columns.index('id')],))
            db.execute(sql)
            insert='INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
            db.execute('SAVEPOINT valid_owner')
            db.execute(insert,values)  # every FK/owner/interval guard accepts it
            assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
            db.execute('ROLLBACK TO valid_owner');db.execute('RELEASE valid_owner')
            for bad in bad_values:
                changed=values.copy();changed[columns.index('shipping_minor_units')]=bad
                with pytest.raises(sqlite3.IntegrityError,match='CHECK constraint failed: '+constraint):db.execute(insert,changed)
            if table=='receipt_bill_claims':
                changed=values.copy();changed[columns.index('billed_minor_units')]=99
                with pytest.raises(sqlite3.IntegrityError,match='CHECK constraint failed: '+constraint):db.execute(insert,changed)
            db.rollback()
    assert database(path(books))==before


def test_shipping_input_and_conserved_but_wrong_allocation_refuse(books,monkeypatch):
    from bookflow.company import receiving
    run=books['run'];item=_inventory_part(books)
    raw=dict(date='2017-01-01',vendor=books['vendor'],items=[dict(item=item,quantity='0.5',unit_cost='0.00'),dict(item=item,quantity='1.5',unit_cost='0.00'),dict(item=item,quantity='1',unit_cost='0.00')])
    before=database(path(books))
    for shipping in ['-0.01',None,{'minor_units':1,'currency':'EUR'}]:
        with pytest.raises(BookflowError) as error:run('item-receipt post',dict(raw,shipping=shipping),reason='Reject invalid shipping')
        assert error.value.code=='E_VALIDATION'
        assert database(path(books))==before
    # Five cents still conserved, but the captured .5/1.5/1 allocation must be
    # 1/2/2, not 0/0/5. Independent validation must reject the planner mutation.
    monkeypatch.setattr(receiving,'cumulative',lambda quantity,value,end: value if end==quantity else 0)
    with pytest.raises(BookflowError) as error:run('item-receipt post',dict(raw,shipping='0.05'),reason='Reject wrong conserved allocation')
    assert error.value.code=='E_INTERNAL'
    assert database(path(books))==before
