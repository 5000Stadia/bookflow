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
        if chain=='company' and result==('co0049','co0050'):
            for name,rows in before['tables'].items():
                if name!='alembic_version':assert table(db.raw,name)==rows,name
            assert set(before['ddl']) <= set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
            observed.append(result)
        return result
    monkeypatch.setattr(migrate,'migrate_to_head',observing)
    result=b['run']('invoice delete',dict(invoice=post['id'],expected_version=post['version'],operation_key='co49-first'),reason='First keyed sales deletion')
    assert observed==[('co0049','co0050')]
    assert result['status']=='deleted'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        for sql in ('DELETE FROM sales_deletions','UPDATE sales_deletions SET reason=reason','UPDATE transactions SET version=version WHERE id=?'):
            with pytest.raises(sqlite3.IntegrityError,match='immutable'):
                db.execute(sql,(post['id'],) if '?' in sql else ())
            db.rollback()


def test_purchase_policy_needs_explicit_sales_catalog_transition(books,monkeypatch):
    from pathlib import Path
    from bookflow.hub import permission_sales_deletion_catalog as current, permission_deletion_catalog as previous
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
    actions={row.key:row for row in build.CATALOG.company_actions}
    assert not actions['contract:delete:journal_entry'].available
    assert not actions['contract:delete:payment'].available
