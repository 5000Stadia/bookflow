"""Real co49 storage and explicitly activated purchase policy upgrade independently."""
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books
from tests.test_sales_deletion import sale
from tests.test_purchase_deletion import location, enable
from tests.payment_raw_evidence import database, table
from tests.test_bill_payment_migration import _rebuilt_since


def test_populated_co49_first_keyed_sales_delete_preserves_storage(tmp_path,monkeypatch):
    """An invoice written at co0049 by that release; today's keyed delete is its first writer."""
    from bookflow.storage import migrate
    from tests.historical_books import books_at
    from tests.payment_raw_evidence import preserved
    # The historical release's own sale: two units bought on a bill, one sold on an invoice.
    b=books_at(tmp_path,'co0049',
        "run=b['run'];item=_inventory_part(b)\n"
        "run('bill post',dict(vendor=b['vendor'],date='2017-01-01',items=[dict(item=item,quantity='2',unit_cost='8')]),reason='Receive two units')\n"
        "post=run('invoice post',dict(customer=b['customer'],date='2017-01-02',lines=[dict(item=item,quantity='1',unit_price='12')]),reason='Sell one unit')\n"
        "run('invoice update',dict(invoice=post['id'],memo='Historical correction'),reason='Keep history')\n"
        "result['post']=run('invoice show',dict(invoice=post['id']))\n")
    post=b['historical']['post']
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(tmp_path/'items'))
    path=next((tmp_path/'items').rglob('company.db'))
    enable(b,'invoice')
    before=database(path);observed=[];original=migrate.migrate_to_head
    def observing(db,chain,*args,**kwargs):
        result=original(db,chain,*args,**kwargs)
        # The destination is whatever the chain's head is today, not a literal: naming one
        # is a pin that the next migration silently falsifies, and this observer would then
        # simply never fire while the assertion below still read as a passing check.
        if chain=='company' and result==('co0049',migrate.HEADS['company']):
            # Retention through the chain: every stored value, read through the columns
            # that existed at co0049 (later revisions add their own).
            for name,rows in before['tables'].items():
                if name!='alembic_version':assert preserved(db.raw,name,rows)==rows,name
            # Every co49 object survives except the ones a later revision deliberately
            # rebuilds or alters. Which those are is derived, never listed here.
            rebuilt=_rebuilt_since('co0049')
            kept={row for row in before['ddl'] if row[1] not in rebuilt}
            assert kept <= set(db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema'))
            assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
            observed.append(result)
        return result
    monkeypatch.setattr(migrate,'migrate_to_head',observing)
    result=b['run']('invoice delete',dict(invoice=post['id'],expected_version=post['version'],operation_key='co49-first'),reason='First keyed sales deletion')
    assert observed==[('co0049',migrate.HEADS['company'])]
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
    from bookflow.hub import permission_deletion_catalog as previous
    from bookflow.hub.permission_runtime import current_catalog
    current = current_catalog()
    _,post=sale(books);client=books['client'];company=books['company']
    # An install upgraded from before activation existed, which then activated an older
    # catalog: a new install starts at the tip, so the older state is built from legacy.
    from pathlib import Path as _Path
    from tests.conftest import make_legacy
    make_legacy(_Path(client.data_root))
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
    from bookflow.hub import permission_job_time_catalog as jobtime
    from bookflow.hub import permission_journal_deletion_catalog as journal
    from bookflow.hub import permission_refund_history_catalog as refunds
    from bookflow.hub import permission_agent_catalog as agents
    from bookflow.hub import permission_everyday_reports_catalog as everyday
    from bookflow.hub import permission_catalog as c
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
            ('job-time-v1',jobtime),
            ('journal-deletion-v1',journal),
            ('customer-refund-history-v1',refunds),
            ('agent-administration-v1',agents),
            ('everyday-reports-v1',everyday),
        ):
            db.execute("UPDATE permission_state SET mode='policy_v1',catalog_version=?",(version,))
            assert runtime.catalog_for_root(tx)==owner.catalog_bundle()
        db.execute("UPDATE permission_state SET catalog_version='unrecognized-future'")
        assert runtime.catalog_for_root(tx)==runtime.catalog_bundle()
        # The tip is the newest accepted version (SCOPED_POLICY_VERSIONS lists newest first).
        assert runtime.current_catalog() is runtime.known_catalog(c.SCOPED_POLICY_VERSIONS[0])
