"""Real co49 storage and explicitly activated purchase policy upgrade independently."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books
from tests.test_sales_deletion import sale
from tests.test_purchase_deletion import location, enable
from tests.payment_raw_evidence import database, table


def test_populated_co49_first_keyed_sales_delete_preserves_storage(tmp_path,monkeypatch):
    from bookflow.storage import migrate
    with monkeypatch.context() as historical:
        historical.setitem(migrate.HEADS,'company','co0049')
        b=books.__wrapped__(tmp_path,historical)
        item,post=sale(b)
        b['run']('invoice update',dict(invoice=post['id'],memo='Historical correction'),reason='Keep history')
        post=b['run']('invoice show',dict(invoice=post['id']))
        path=location(b);enable(b,'invoice')
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(tmp_path/'items'))
    before=database(path);observed=[];original=migrate.migrate_to_head
    def observing(db,chain,*args,**kwargs):
        result=original(db,chain,*args,**kwargs)
        if chain=='company' and result==('co0049','co0054'):
            for name,rows in before['tables'].items():
                if name!='alembic_version':assert table(db.raw,name)==rows,name
            # co0054 rebuilds `deposit_operations` to admit `deposit delete` in its frozen
            # command CHECK, so that one table's definition is deliberately not the old one.
            # Its stored values are still compared above, which is the preservation claim;
            # what is exempted here is the text of the definition, nothing else.
            after=set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
            rebuilt={row for row in before['ddl'] if row[0]=='table' and row[1]=='deposit_operations'}
            assert len(rebuilt)==1
            assert (set(before['ddl'])-rebuilt) <= after
            assert not (rebuilt <= after)
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
            observed.append(result)
        return result
    monkeypatch.setattr(migrate,'migrate_to_head',observing)
    result=b['run']('invoice delete',dict(invoice=post['id'],expected_version=post['version'],operation_key='co49-first'),reason='First keyed sales deletion')
    assert observed==[('co0049','co0054')]
    assert result['status']=='deleted'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM sales_deletions','UPDATE sales_deletions SET reason=reason','UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError,match='immutable'):
                db.execute(sql,(post['id'],) if '?' in sql else ())
            db.rollback()


def test_purchase_policy_needs_explicit_sales_catalog_transition(books,monkeypatch):
    from pathlib import Path
    # The tip descriptor is the one an activation stores, whichever delta it is.
    from bookflow.hub import permission_deposit_deletion_catalog as current, permission_deletion_catalog as previous
    _,post=sale(books);client=books['client'];company=books['company']
    with monkeypatch.context() as historical:
        historical.setattr(current,'CATALOG',previous.CATALOG)
        historical.setattr(current,'MANIFEST',previous.MANIFEST)
        historical.setattr(current,'catalog_bundle',previous.catalog_bundle)
        enable(books,'invoice')
    hub=Path(client.data_root)/'hub.db';path=location(books)
    with sqlite3.connect(hub) as db:
        memberships=db.execute('SELECT * FROM memberships ORDER BY id').fetchall()
        assert db.execute('SELECT catalog_version FROM permission_state').fetchone()==(previous.CATALOG.version,)
    before=database(path)
    with pytest.raises(BookflowError) as error:
        books['run']('invoice delete',dict(invoice=post['id'],expected_version=1),reason='Not activated')
    assert error.value.code=='E_PERMISSION' and database(path)==before
    state=client.permission.show()
    preview=client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'],dry_run=True)
    assert preview['changed'] and preview['generation']==state['generation']+1
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    with sqlite3.connect(hub) as db:
        assert db.execute('SELECT * FROM memberships ORDER BY id').fetchall()==memberships
        assert db.execute('SELECT catalog_version FROM permission_state').fetchone()==(current.CATALOG.version,)
    assert books['run']('invoice delete',dict(invoice=post['id'],expected_version=1),reason='Explicitly activated')['status']=='deleted'


def test_co50_declaration_exact_and_other_families_remain_unavailable():
    import importlib
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from bookflow.company import schema as c, sales_deletion_schema
    from bookflow.hub import permission_sales_deletion_catalog as build
    migration=importlib.import_module('bookflow.storage.company_migrations.versions.0050_sales_deletions')
    assert migration.DDL==(str(CreateTable(c.sales_deletions).compile(dialect=dialect())),*sales_deletion_schema.guards())
    from bookflow.core.deletion_families import SALES_FAMILIES
    import re
    check = next(x for x in c.sales_deletions.constraints if x.name == 'ck_sales_delete_family')
    assert tuple(re.findall("'([^']+)'", str(check.sqltext))) == SALES_FAMILIES == ('invoice', 'sales_receipt')
    actions={row.key:row for row in build.CATALOG.company_actions}
    assert not actions['contract:delete:journal_entry'].available
    assert not actions['contract:delete:payment'].available


def test_catalog_selection_retains_literal_accepted_versions_and_legacy():
    from types import SimpleNamespace
    from bookflow.hub import permission_runtime as runtime
    from bookflow.hub import permission_activation_catalog as activation, permission_setup_catalog as setup
    from bookflow.hub import permission_deletion_catalog as purchase, permission_sales_deletion_catalog as sales
    from bookflow.hub import permission_payment_deletion_catalog as payment
    from bookflow.hub import permission_bill_deletion_catalog as bill
    from bookflow.hub import permission_credit_correction_catalog as correction
    from bookflow.hub import permission_credit_deletion_catalog as credit
    from bookflow.hub import permission_deposit_deletion_catalog as deposit
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE permission_state(id INTEGER,mode TEXT,catalog_version TEXT)')
        db.execute('INSERT INTO permission_state VALUES(1,?,?)',('legacy','sales-deletion-v1'))
        tx=SimpleNamespace(raw=db)
        assert runtime.catalog_for_root(tx)==runtime.catalog_bundle()
        for version,owner in (
            ('purchase-delete-activation-preparation-v1',activation),
            ('purchase-permission-setup-v1',setup),
            ('purchase-deletion-v1',purchase),
            ('sales-deletion-v1',sales),
            ('payment-deletion-v1',payment),
            ('bill-deletion-v1',bill),
            ('credit-correction-v1',correction),
            ('credit-memo-deletion-v1',credit),
            ('deposit-deletion-v1',deposit),
        ):
            db.execute("UPDATE permission_state SET mode='policy_v1',catalog_version=?",(version,))
            assert runtime.catalog_for_root(tx)==owner.catalog_bundle()
        db.execute("UPDATE permission_state SET catalog_version='unrecognized-future'")
        assert runtime.catalog_for_root(tx)==runtime.catalog_bundle()
        assert runtime.current_catalog() is deposit
