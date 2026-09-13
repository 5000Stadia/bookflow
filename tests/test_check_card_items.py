"""Known-cost direct purchases: independent fractional stock and funding evidence."""
import pytest
import sqlite3
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _inventory_part, _inventory_asset, _net
from pathlib import Path
from tests.payment_raw_evidence import database


@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_fractional_mixed_purchase_refusal_retry_and_lifecycle(books, noun):
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    funding = books['bank'] if noun == 'check' else books['client'].account.create(
        name='Purchase card', type='credit_card', company=books['company'])['id']
    raw = dict(account=funding, pay_to=dict(name_type='vendor', name_id=books['vendor']),
        date='2017-03-03', amount='11.00', expenses=[dict(account=books['freight'], amount='4.83')],
        items=[dict(item=item, quantity='0.5', unit_cost='12.34', customer=books['customer'], billable=True)])
    # Registered preview/refusal must write neither financial state nor captured item facts.
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    before = database(path)
    with pytest.raises(BookflowError) as failed:
        books['run'](noun+' post', dict(raw, amount='10.99'), reason='Wrong total')
    assert failed.value.details['difference_minor_units'] == 1
    assert database(path) == before
    preview = books['run'](noun+' post', raw, dry_run=True, reason='Preview purchase')
    assert preview['document']['item_total']['minor_units'] == 617
    assert database(path) == before
    posted = books['run'](noun+' post', raw, reason='Receive paid goods', idempotency_key='fractional-purchase')
    assert _net(books) == {asset:617, books['freight']:483, funding:-1100}
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT kind,quantity_microunits,value_minor_units,offset_account_id '
                          'FROM inventory_movements WHERE transaction_id=?', (posted['id'],)).fetchall() == [
                              ('receipt', 500000, 617, funding)]
    assert books['run']('bill query', {})['items'] == []
    captured = posted['document']['items'][0]
    assert captured['profile']['quantity_microunits'] == 500000
    assert captured['profile']['billable'] is True
    after = database(path)
    replay = books['run'](noun+' post', raw, reason='Receive paid goods', idempotency_key='fractional-purchase')
    assert replay['id'] == posted['id']
    assert database(path) == after
    selector = 'check' if noun == 'check' else 'card_charge'
    shown = books['run'](noun+' show', {selector:posted['id']})
    assert shown['document']['items'] == posted['document']['items']
    # Header-only correction retains items and exact stock, even after master changes.
    books['client'].item.update(item=item, purchase_description='New master description', company=books['company'])
    corrected = books['run'](noun+' update', {selector:posted['id'], 'memo':'Receipt retained'}, reason='Correct memo')
    assert corrected['document']['items'] == posted['document']['items']
    assert _net(books) == {asset:617, books['freight']:483, funding:-1100}
    with pytest.raises(BookflowError):
        books['run']('journal void', {'journal':posted['id']}, reason='Cannot bypass stock owner')
    books['run'](noun+' void', {selector:posted['id']}, reason='Reverse paid purchase')
    assert _net(books) == {}


@pytest.mark.parametrize('noun', ['check', 'card-charge'])
def test_item_only_nonstock_purchase(books, noun):
    funding = books['bank'] if noun == 'check' else books['client'].account.create(
        name='Nonstock card', type='credit_card', company=books['company'])['id']
    posted = books['run'](noun+' post', dict(account=funding, date='2017-03-03', amount='6.17',
        items=[dict(item=books['labor'], quantity='0.5', unit_cost='12.34')]), reason='Buy service')
    assert posted['document']['expense_lines'] == 0
    assert posted['document']['expense_total']['minor_units'] == 0
    assert _net(books) == {books['subs']:617, funding:-617}


def test_item_quantity_edit_with_same_money_and_inventory_editor_guards(books):
    import sqlite3
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    def stock():
        with sqlite3.connect(path) as raw:
            return raw.execute('SELECT sum(quantity_microunits),sum(value_minor_units) FROM inventory_movements WHERE item_id=?', (item,)).fetchone()
    posted = books['run']('check post', dict(account=books['bank'], date='2017-03-03', amount='6.17',
        items=[dict(item=item, quantity='0.5', unit_cost='12.34')]), reason='Receive half a unit')
    assert stock() == (500000, 617)
    line_id = posted['document']['items'][0]['line_id']
    edited = books['run']('check update', dict(check=posted['id'], items=[dict(
        line_id=line_id, item=item, quantity='1', unit_cost='6.17')]), reason='Correct quantity at same value')
    assert edited['revision']['id'] != posted['revision']['id']
    assert edited['document']['items'][0]['line_id'] == line_id
    assert stock() == (1000000, 617)
    assert _net(books) == {asset:617, books['bank']:-617}
    original = books['run']('check show', dict(check=posted['id'], revision_number=1))
    assert original['document']['items'][0]['profile']['quantity_microunits'] == 500000
    before = database(path)
    with pytest.raises(BookflowError):
        books['run']('check post', dict(account=books['bank'], date='2017-03-03', amount='6.17',
            expenses=[dict(account=asset, amount='6.17')]), reason='Unattributed inventory must fail')
    assert database(path) == before
    with pytest.raises(BookflowError):
        books['run']('journal update', dict(journal=posted['id'], memo='Bypass'), reason='Forbidden editor')
    assert database(path) == before


def test_item_storage_additive_migration_preserves_prior_database(tmp_path):
    import importlib
    import sqlite3
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c
    from bookflow.company.check_item_schema import guards
    from bookflow.storage.engine import open_database
    from bookflow.storage.migrate import migrate_to_head, HEADS
    from tests.test_credit_memo_migration import _at
    from tests.payment_raw_evidence import table
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0045_check_items')
    target = c.money_out_item_lines
    assert migration.DDL == (str(CreateTable(target).compile(dialect=dialect())),) + tuple(
        str(CreateIndex(index).compile(dialect=dialect())) for index in target.indexes)
    assert migration.GUARDS == guards()
    path = tmp_path / 'old.db'
    _at(path, 'co0044')
    with sqlite3.connect(path) as raw:
        raw.execute('CREATE TABLE local_items_test(id INTEGER PRIMARY KEY, b BLOB)')
        raw.execute('INSERT INTO local_items_test VALUES(7, ?)', (b'\x00\xff',))
        raw.commit()
        ddl = set(raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        names = [r[1] for r in ddl if r[0]=='table' and r[1]!='alembic_version']
        before = {name:table(raw,name) for name in names}
    with open_database(path,writable=True) as db:
        assert migrate_to_head(db,'company',tmp_path/'backups') == ('co0044', HEADS['company'])
        assert {name:table(db.raw,name) for name in names} == before
        assert ddl <= set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall() == []
        assert db.raw.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert migrate_to_head(db,'company',tmp_path/'backups') == (HEADS['company'],HEADS['company'])


def test_direct_purchase_cost_edit_recosts_sale_and_refuses_unsafe_void(books):
    item = _inventory_part(books)
    asset = _inventory_asset(books)
    posted = books['run']('check post', dict(account=books['bank'], date='2017-01-01', amount='100.00',
        items=[dict(item=item, quantity='10', unit_cost='10.00')]), reason='Receive paid stock')
    books['run']('invoice post', dict(customer=books['customer'], date='2017-02-10',
        lines=[dict(item=item, quantity='4')]), reason='Sell four')
    row = posted['document']['items'][0]
    books['run']('check update', dict(check=posted['id'], amount='200.00',
        items=[dict(line_id=row['line_id'], item=item, quantity='10', unit_cost='20.00')]), reason='Correct acquisition cost')
    early = _net(books, date_to='2017-01-31')
    later = _net(books, date_to='2017-02-28')
    assert early[asset] == 20000 and early[books['bank']] == -20000
    assert later[asset] == 12000 and later[books['cogs']] == 8000
    path = Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'
    before = database(path)
    with pytest.raises(BookflowError) as failed:
        books['run']('check void', {'check':posted['id']}, reason='Cannot remove sold stock')
    assert failed.value.code == 'E_VALIDATION'
    assert database(path) == before
    books['run']('company update', {'closing_date':'2017-02-28'}, reason='Close posted dates')
    before = database(path)
    with pytest.raises(BookflowError) as failed:
        books['run']('check update', {'check':posted['id'], 'memo':'Closed'}, reason='Cannot edit closed purchase')
    assert failed.value.code == 'E_PERIOD_CLOSED'
    assert database(path) == before


def test_scoped_generated_item_contracts_match_models():
    from bookflow.documentation.generate import render_tree
    rendered = render_tree()
    for name in ('cli/check.md', 'cli/card-charge.md', 'cli/bill.md', 'schema/company/money_out_item_lines.md'):
        assert (Path(__file__).parents[1] / 'docs' / name).read_bytes() == rendered[name]
