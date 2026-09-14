"""Free commercial goods retain quantities and exact owned history without zero GL."""
from pathlib import Path
import sqlite3
import pytest
from tests.test_bill_item_lines import books, _inventory_part, _net
from tests.payment_raw_evidence import database

@pytest.mark.parametrize('sale_noun', ['invoice','sales-receipt'])
def test_free_bill_and_free_sale_have_physical_effects_without_money(books, sale_noun):
    run=books['run']; item=_inventory_part(books)
    bill=run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='0.00')]),reason='Free goods')
    assert bill['total']['minor_units']==0
    raw=dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='0.00')])
    if sale_noun=='sales-receipt': raw.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
    sold=run(sale_noun+' post',raw,reason='Give away free stock')
    assert sold['total_minor_units']==0
    assert _net(books)=={}
    if sale_noun=='sales-receipt':
        assert run('deposit sources',dict(date='2017-02-01'))['items']==[]
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT kind,quantity_microunits,value_minor_units,posting_line_id FROM inventory_movements ORDER BY sequence').fetchall()==[('receipt',2000000,0,None),('issue',-1000000,0,None)]
        assert db.execute('SELECT count(*) FROM posting_lines').fetchone()==(0,)
        assert db.execute('SELECT count(*) FROM ap_obligation_keys').fetchone()==(0,)

@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_free_direct_purchase_corrections_and_numbering(books, noun):
    from tests.test_bill_item_lines import _inventory_asset
    run = books['run']; item = _inventory_part(books)
    funding = books['bank'] if noun == 'check' else books['client'].account.create(name='Free card', type='credit_card', company=books['company'])['id']
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    raw=dict(account=funding, pay_to=dict(name_type='vendor', name_id=books['vendor']), date='2017-01-01',amount={'minor_units':0,'currency':'USD'},items=[dict(item=item,quantity='0.5',unit_cost={'minor_units':0,'currency':'USD'})])
    from bookflow.core.errors import BookflowError
    before=database(path)
    preview=run(noun+' post',raw,reason='Preview free purchase',dry_run=True)
    assert preview['document']['amount']['minor_units']==0
    assert database(path)==before
    with pytest.raises(BookflowError) as error:
        run(noun+' post',dict(raw,amount='0.01'),reason='Refuse unmatched money')
    assert error.value.code=='E_UNBALANCED_ENTRY'
    assert database(path)==before
    posted=run(noun+' post',raw,reason='Free purchase',idempotency_key='free-purchase')
    saved=database(path)
    assert run(noun+' post',raw,reason='Free purchase',idempotency_key='free-purchase')['id']==posted['id']
    assert database(path)==saved
    assert posted['document']['amount']['minor_units']==0
    assert posted['document']['items'][0]['profile']['quantity_microunits']==500000
    assert _net(books)=={}
    selector='check' if noun=='check' else 'card_charge'
    shown=run(noun+' show',{selector:posted['id']})
    assert shown['document']['items']==posted['document']['items']
    assert [r['id'] for r in run(noun+' query',dict(account=funding))['items']]==[posted['id']]
    if noun=='check': assert shown['document']['check_number']==posted['document']['check_number']
    unchanged=run(noun+' update',{selector:posted['id']},reason='Retain free purchase')
    assert unchanged['changed'] is False
    assert unchanged['document']['check_number']==shown['document']['check_number']
    line_id=shown['document']['items'][0]['line_id']
    paid=run(noun+' update',{selector:posted['id'], 'amount':'6.17', 'items':[dict(line_id=line_id,item=item,quantity='0.5',unit_cost='12.34')]},reason='Correct known price')
    assert _net(books)=={_inventory_asset(books):617,funding:-617}
    free=run(noun+' update',{selector:posted['id'],'amount':'0.00','items':[dict(line_id=line_id,item=item,quantity='0.5',unit_cost='0.00')]},reason='Restore free price')
    assert _net(books)=={}
    assert free['document']['items'][0]['line_id']==line_id
    if noun=='check': assert free['document']['check_number']==posted['document']['check_number']
    run(noun+' void',{selector:posted['id']},reason='Return free goods')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone()==(0,0)
        assert db.execute('SELECT count(*) FROM ap_obligation_keys').fetchone()==(0,)
        assert db.execute('SELECT count(*) FROM posting_lines WHERE debit_minor_units=0 AND credit_minor_units=0').fetchone()==(0,)
        cursor=db.execute("SELECT * FROM inventory_movements WHERE kind='receipt' AND value_minor_units=0 ORDER BY sequence LIMIT 1")
        original=dict(zip([column[0] for column in cursor.description],cursor.fetchone()))
        from bookflow.core.ids import new_id
        bad=dict(original,id=new_id(),sequence=100000,kind='reversal',reverses_movement_id=original['id'],quantity_microunits=-499999)
        with pytest.raises(sqlite3.IntegrityError,match='inventory reversal is not exact'):
            db.execute('INSERT INTO inventory_movements ('+','.join(bad)+') VALUES ('+','.join('?' for _ in bad)+')',tuple(bad.values()))
        bad=dict(original,id=new_id(),sequence=100001,document_line_id=new_id())
        with pytest.raises(sqlite3.IntegrityError,match='inventory movement does not match its owner or value'):
            db.execute('INSERT INTO inventory_movements ('+','.join(bad)+') VALUES ('+','.join('?' for _ in bad)+')',tuple(bad.values()))

@pytest.mark.parametrize('sale_noun', ['invoice', 'sales-receipt'])
def test_original_zero_issue_is_recosted_and_bill_obligation_survives_zero(books, sale_noun):
    from tests.test_bill_item_lines import _inventory_asset
    run=books['run']; item=_inventory_part(books); asset=_inventory_asset(books)
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    bill=run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='0.00')]),reason='Receive free goods')
    sale=dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='0.00')])
    if sale_noun=='sales-receipt': sale.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
    sold=run(sale_noun+' post',sale,reason='Free sale')
    with sqlite3.connect(path) as db:
        original_issue=db.execute("SELECT id FROM inventory_movements WHERE kind='issue'").fetchone()[0]
        line_id=db.execute('SELECT line_id FROM document_lines WHERE transaction_id=?',(bill['id'],)).fetchone()[0]
    paid=run('bill update',dict(bill=bill['id'],items=[dict(line_id=line_id,item=item,quantity='2',unit_cost='10.00')]),reason='Correct supplier cost')
    # Two units received for20; one already issued now costs10, regardless of sale price0.
    assert _net(books)=={asset:1000,books['cogs']:1000,books['payable']:-2000}
    with sqlite3.connect(path) as db:
        obligation=db.execute('SELECT id FROM ap_obligation_keys').fetchone()[0]
        assert db.execute('SELECT value_minor_units FROM inventory_movements WHERE corrects_movement_id=?',(original_issue,)).fetchall()==[(-1000,)]
    run('bill update',dict(bill=bill['id'],items=[dict(line_id=line_id,item=item,quantity='2',unit_cost='0.00')]),reason='Restore free purchase')
    assert _net(books)=={}
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id FROM ap_obligation_keys').fetchall()==[(obligation,)]
        assert db.execute('SELECT sum(value_minor_units) FROM inventory_movements WHERE corrects_movement_id=?',(original_issue,)).fetchone()==(0,)
    selector='invoice' if sale_noun=='invoice' else 'sales_receipt'
    run(sale_noun+' void',{selector:sold['id']},reason='Undo free sale')
    run('bill void',dict(bill=bill['id']),reason='Return all free goods')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements').fetchone()==(0,0)


def test_populated_co45_upgrade_preserves_commercial_rows_links_and_owned_guards(tmp_path, monkeypatch):
    import importlib
    from bookflow.storage.migrate import HEADS, migrate_to_head
    from bookflow.storage.engine import open_database
    from tests.payment_raw_evidence import table
    head=HEADS['company']
    monkeypatch.setitem(HEADS, 'company', 'co0045')
    old=books.__wrapped__(tmp_path, monkeypatch)
    item=_inventory_part(old)
    bill=old['run']('bill post',dict(vendor=old['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='10.00')]),reason='Old populated receipt')
    old['run']('invoice post',dict(customer=old['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='15.00')]),reason='Old populated sale')
    path=Path(old['client'].company.show(company=old['company'])['path'])/'company.db'
    before=database(path)
    assert before['tables']['inventory_movements']['count']==2
    assert before['tables']['purchase_item_lines']['count']==1
    assert before['tables']['sales_line_profiles']['count']==1
    monkeypatch.setitem(HEADS, 'company', head)
    migration=importlib.import_module('bookflow.storage.company_migrations.versions.0046_zero_value_items')
    with open_database(path,writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'owned-backups')==('co0045',head)
        for name, snapshot in before['tables'].items():
            if name!='alembic_version': assert table(db.raw,name)==snapshot, name
        replaced=set(migration.CHANGED)|set(migration.REPLACED)|set(migration.TRIGGERS)
        after=set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        assert {row for row in before['ddl'] if row[1] not in replaced} <= after
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        for guard in migration.GUARDS:
            assert db.raw.execute('SELECT sql FROM sqlite_schema WHERE name=?',(guard.split()[2],)).fetchone()==(guard,)
        cursor=db.raw.execute('SELECT * FROM inventory_movements LIMIT 1')
        movement=dict(zip([column[0] for column in cursor.description],cursor.fetchone()))
        from bookflow.core.ids import new_id
        bad=dict(movement,id=new_id(),sequence=100,posting_line_id=None)
        with pytest.raises(sqlite3.IntegrityError):
            db.raw.execute('INSERT INTO inventory_movements ('+','.join(bad)+') VALUES ('+','.join('?' for _ in bad)+')',tuple(bad.values()))
    # Backward read of the original captured bill remains unchanged.
    assert old['run']('bill show',dict(bill=bill['id']))['total']['minor_units']==2000

@pytest.mark.parametrize('noun',['bill','check','card-charge'])
def test_mixed_free_paid_items_keep_order_and_zero_dates_are_closed(books,noun):
    from bookflow.core.errors import BookflowError
    run=books['run']; item=_inventory_part(books)
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    funding=books['bank'] if noun!='card-charge' else books['client'].account.create(name='Mixed card',type='credit_card',company=books['company'])['id']
    lines=[dict(item=item,quantity='0.5',unit_cost='0.00'),dict(item=item,quantity='0.5',unit_cost='12.34')]
    raw=dict(date='2017-01-01',items=lines,expenses=[dict(account=books['freight'],amount='4.83')])
    raw.update(dict(vendor=books['vendor']) if noun=='bill' else dict(account=funding,amount='11.00'))
    posted=run(noun+' post',raw,reason='Mixed free and paid goods')
    selector={'bill':'bill','check':'check','card-charge':'card_charge'}[noun]
    shown=run(noun+' show',{selector:posted['id']})
    if noun!='bill':
        assert [i['amount']['minor_units'] for i in shown['document']['items']]==[0,617]
        assert shown['document']['items']==posted['document']['items']
        assert shown['document']['amount']['minor_units']==1100
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT quantity_microunits,value_minor_units FROM inventory_movements WHERE kind='receipt' ORDER BY sequence").fetchall()==[(500000,0),(500000,617)]
    run('company update',dict(closing_date='2017-01-31'),reason='Close physical date')
    free=dict(raw,items=[lines[0]],expenses=[])
    if noun!='bill': free['amount']='0.00'
    before=database(path)
    with pytest.raises(BookflowError) as error: run(noun+' post',free,reason='Closed free purchase')
    assert error.value.code=='E_PERIOD_CLOSED'
    assert database(path)==before
    with pytest.raises(BookflowError) as error: run(noun+' void',{selector:posted['id']},reason='Closed return')
    assert error.value.code=='E_PERIOD_CLOSED'
    assert database(path)==before

@pytest.mark.parametrize('noun',['invoice','sales-receipt'])
def test_free_sale_positive_cogs_price_corrections_and_cash_absence(books,noun):
    from tests.test_bill_item_lines import _inventory_asset
    run=books['run']; item=_inventory_part(books); asset=_inventory_asset(books)
    run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='10.00')]),reason='Known positive cost')
    raw=dict(customer=books['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='0.00')])
    if noun=='sales-receipt': raw.update(deposit_to=books['bank'],payment_method=books['methods']['Cash'])
    sold=run(noun+' post',raw,reason='Give away paid stock')
    base={asset:1000,books['cogs']:1000,books['payable']:-2000}
    assert _net(books)==base
    selector='invoice' if noun=='invoice' else 'sales_receipt'
    line=sold['revision']['lines'][0]['line_id']
    changed=run(noun+' update',{selector:sold['id'],'lines':[dict(line_id=line,item=item,quantity='1',unit_price='5.00')]},reason='Correct sale price')
    assert changed['total_minor_units']==500
    restored=run(noun+' update',{selector:sold['id'],'lines':[dict(line_id=line,item=item,quantity='1',unit_price='0.00')]},reason='Restore free price')
    assert restored['total_minor_units']==0
    assert _net(books)==base
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE transaction_id=?',(sold['id'],)).fetchone()==(-1000000,-1000)
        assert db.execute('SELECT count(*) FROM posting_lines WHERE account_id=? AND batch_id IN (SELECT id FROM posting_batches WHERE revision_id=? AND kind!=?)',(books['bank'],restored['current_revision_id'],'reversal')).fetchone()==(0,)
    run('company update',dict(closing_date='2017-01-31'),reason='Close free sale dates')
    before=database(path)
    from bookflow.core.errors import BookflowError
    with pytest.raises(BookflowError) as error: run(noun+' void',{selector:sold['id']},reason='Closed free sale')
    assert error.value.code=='E_PERIOD_CLOSED'
    assert database(path)==before


def test_paid_bill_cannot_be_corrected_to_zero_and_ordinary_journal_stays_positive(books):
    from bookflow.core.errors import BookflowError
    run=books['run']; item=_inventory_part(books)
    posted=run('bill post',dict(vendor=books['vendor'],date='2017-01-01',items=[dict(item=item,quantity='1',unit_cost='10.00')]),reason='Supplier obligation')
    run('bill pay',dict(funding_account=books['bank'],method=books['methods']['Check'],date='2017-01-02',bills=[dict(bill=posted['id'])]),reason='Settle supplier')
    path=Path(books['client'].company.show(company=books['company'])['path'])/'company.db'
    before=database(path)
    line=posted['revision']['items'][0]['line_id']
    with pytest.raises(BookflowError): run('bill update',dict(bill=posted['id'],items=[dict(line_id=line,item=item,quantity='1',unit_cost='0.00')]),reason='Cannot erase settlement')
    assert database(path)==before
    with pytest.raises(BookflowError): run('journal post',dict(date='2017-01-01',lines=[dict(account=books['bank'],side='debit',amount='0.00'),dict(account=books['income'],side='credit',amount='0.00')]),reason='Zero is not journal money')
    assert database(path)==before
