"""Concurrent exact-version requests and relational inverse/tombstone invariants."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import importlib
import sqlite3
import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.sqlite import dialect
from bookflow.core.errors import BookflowError
from bookflow.company import schema as c
from bookflow.company.purchase_deletion_schema import guards
from tests.test_bill_item_lines import books, _inventory_part
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database


def test_competing_deletes_have_one_exact_inverse_and_immutable_receipt(books):
    item=_inventory_part(books)
    post=books['run']('check post',dict(account=books['bank'],date='2017-01-02',amount='11',
        items=[dict(item=item,quantity='0.5',unit_cost='12.34')],expenses=[dict(account=books['freight'],amount='4.83')]),reason='Receipt')
    path=location(books);enable(books,'check');barrier=Barrier(2)
    def attempt(key):
        barrier.wait()
        try:return books['run']('check delete',dict(check=post['id'],expected_version=1,operation_key=key),reason='Concurrent deletion')
        except BookflowError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(attempt,key) for key in ('race-a','race-b')]
        results=[f.result() for f in futures]
    assert sum(isinstance(r,dict) for r in results)==1,results
    assert [r for r in results if isinstance(r,str)][0] in ('E_DB_BUSY','E_VERSION_CONFLICT'),results
    after=database(path)
    loser=('race-a','race-b')[next(i for i,r in enumerate(results) if isinstance(r,str))]
    with pytest.raises(BookflowError) as stale:
        books['run']('check delete',dict(check=post['id'],expected_version=1,operation_key=loser),reason='Concurrent deletion')
    assert stale.value.code=='E_VERSION_CONFLICT' and database(path)==after
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        original=[dict(x) for x in db.execute('SELECT * FROM posting_lines WHERE reversed_line_id IS NULL')]
        inverses=[dict(x) for x in db.execute('SELECT * FROM posting_lines WHERE reversed_line_id IS NOT NULL')]
        assert len(original)==len(inverses)==3
        assert {x['reversed_line_id'] for x in inverses}=={x['id'] for x in original}
        by_id={x['id']:x for x in original}
        for inverse in inverses:
            source=by_id[inverse['reversed_line_id']]
            assert (inverse['debit_minor_units'],inverse['credit_minor_units'])==(source['credit_minor_units'],source['debit_minor_units'])
            for field in ('transaction_id','account_id','currency','class_id','name_type','name_id'):
                assert inverse[field]==source[field]
        sources=[dict(x) for x in db.execute('SELECT * FROM posting_line_sources')]
        ordinary={x['id']:x for x in sources if x['reversed_source_id'] is None}
        reversed_sources=[x for x in sources if x['reversed_source_id'] is not None]
        assert len(ordinary)==len(reversed_sources)==3
        for x in reversed_sources:
            old=ordinary[x['reversed_source_id']]
            for field in ('revision_id','document_line_id','amount_minor_units','currency'):
                assert x[field]==old[field]
        movement=db.execute("SELECT * FROM inventory_movements WHERE kind='receipt'").fetchone()
        inverse=db.execute("SELECT * FROM inventory_movements WHERE kind='reversal'").fetchone()
        assert inverse['reverses_movement_id']==movement['id']
        assert (inverse['quantity_microunits'],inverse['value_minor_units'])==(-500000,-617)
        row=db.execute('SELECT * FROM purchase_deletions').fetchone()
        assert row['from_version']==1 and row['result_version']==2 and row['family']=='check'
        for sql in ('UPDATE purchase_deletions SET reason=\'changed\'','DELETE FROM purchase_deletions',
                    'UPDATE transactions SET status=\'posted\''):
            with pytest.raises(sqlite3.IntegrityError,match='immutable'):db.execute(sql)
            db.rollback()
    assert database(path)==after


def test_owned_co49_ddl_matches_current_table_and_guard_declarations():
    migration=importlib.import_module('bookflow.storage.company_migrations.versions.0049_purchase_deletions')
    assert migration.DDL==(str(CreateTable(c.purchase_deletions).compile(dialect=dialect())),*guards())
