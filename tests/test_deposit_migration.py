"""co19 preserving upgrade, raw history, local objects and claim constraints."""
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import pytest
from sqlalchemy.schema import CreateTable,CreateIndex
from sqlalchemy.dialects.sqlite import dialect
from bookflow.company import schema
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import HEADS,migrate_to_head,FeatureRevision,feature_admission
from bookflow.core.errors import BookflowError
from tests.payment_raw_evidence import table,attachments

BASE='57314722e8b2cd7e4402feaf059a244794f0efca'
M=importlib.import_module('bookflow.storage.company_migrations.versions.0020_deposits')


def _later_rebuilds(revision):
    """Tables a later company migration rebuilt, and triggers it rewrote."""
    import pkgutil
    from bookflow.storage.company_migrations import versions
    rebuilt,rewritten=set(),set()
    for info in pkgutil.iter_modules(versions.__path__):
        module=importlib.import_module(versions.__name__+'.'+info.name)
        if getattr(module,'revision','')<=revision:continue
        rebuilt.update(getattr(module,'CHANGED',()))
        rewritten.update(getattr(module,'REPLACED',()))
    return rebuilt,rewritten


def _later_additions(revision):
    """Columns a company migration later than ``revision`` appends to an existing table."""
    import pkgutil
    from bookflow.storage.company_migrations import versions
    added={}
    for info in pkgutil.iter_modules(versions.__path__):
        module=importlib.import_module(versions.__name__+'.'+info.name)
        if getattr(module,'revision','')<=revision:continue
        for name,columns in getattr(module,'ADDITIONS',{}).items():
            added.setdefault(name,[]).extend(part.strip().split()[0] for part in columns)
    return {name:tuple(columns) for name,columns in added.items()}


@pytest.fixture(scope='module')
def co19(tmp_path_factory):
    parent=tmp_path_factory.mktemp('deposit-co19');source=parent/'source';source.mkdir()
    archive=subprocess.check_output(['git','archive',BASE,'src'],cwd=Path(__file__).parents[1])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(source,filter='data')
    root=parent/'root'
    code='import bookflow,sys,io;c=bookflow.connect(data_root=sys.argv[1]);c.init();c.demo.reset();co="Demo Plumbing Co";p=c.customer.create(name="Deposit migration bytes",company=co);c.attachment.add(record_type="customer",record_id=p["id"],original_filename="deposit-bytes.bin",input_stream=io.BytesIO(b"A\\x00B\\x80\\xff"),company=co)'
    result=subprocess.run([sys.executable,'-c',code,str(root)],cwd=source,env=dict(os.environ,PYTHONPATH=str(source/'src'),BOOKFLOW_DATA_ROOT=str(root)),capture_output=True,text=True)
    (parent/'seed.log').write_text(result.stdout+result.stderr)
    assert result.returncode==0,result.stderr
    return root


def test_frozen_ddl_metadata_and_shared_feature_admission(tmp_path):
    expected=[]
    for name in M.NEW_TABLES:
        t=schema.metadata.tables[name]
        expected.append(str(CreateTable(t).compile(dialect=dialect())).strip())
        expected.extend(str(CreateIndex(i).compile(dialect=dialect())) for i in sorted(t.indexes,key=lambda i:i.name))
    assert M.DDL==expected
    with open_database(tmp_path/'fresh.db',writable=True,create=True) as db:
        assert migrate_to_head(db,'company',None)==(None,HEADS['company'])
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        assert feature_admission(db,FeatureRevision('company',None),resolver=None) is None
        resolver=lambda: 'real'
        assert feature_admission(db,FeatureRevision('company','co0019'),resolver=resolver) is resolver
        with pytest.raises(BookflowError) as caught:feature_admission(db,FeatureRevision('company','co0019'),resolver=None)
        assert caught.value.code=='E_INTERNAL'
        # Read-only facade supplies an unknown revision; no schema corruption.
        class Unknown:
            class Raw:
                def execute(self,*a):return self
                def fetchone(self):return ('co9999',)
            raw=Raw()
        with pytest.raises(BookflowError) as caught:feature_admission(Unknown(),FeatureRevision('company',None),resolver=None)
        assert caught.value.code=='E_SCHEMA_UNKNOWN'


@pytest.mark.timeout(180)
def test_all_raw_values_local_ddl_attachments_and_rollback(co19,tmp_path):
    root=tmp_path/'root';shutil.copytree(co19,root)
    path=next(root.glob('organizations/*/Demo Plumbing Co/company.db'))
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0019',)
        raw.execute('ALTER TABLE transactions ADD COLUMN local_text TEXT')
        raw.execute("ALTER TABLE document_lines ADD COLUMN local_bytes BLOB DEFAULT X'0080FF'")
        raw.execute('UPDATE transactions SET local_text=?',('before\0after',))
        # Use a fresh local table for arbitrary storage classes without modifying
        # historical financial rows or disabling their immutability guards.
        raw.execute('CREATE TABLE local_raw(id INTEGER PRIMARY KEY, t TEXT, b BLOB, f REAL)')
        raw.execute('INSERT INTO local_raw VALUES (97,?,?,?)',('before\0after',b'\0\x80\xff',0.1))
        raw.execute('CREATE VIEW local_deposit_view AS SELECT id,local_text FROM transactions')
        raw.execute('CREATE INDEX local_deposit_index ON transactions(substr(local_text,1,2)) WHERE local_text IS NOT NULL')
        raw.execute("CREATE TRIGGER local_deposit_guard BEFORE UPDATE OF local_text ON transactions BEGIN SELECT RAISE(ABORT,'local'); END")
        names=[r[0] for r in raw.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name <> 'alembic_version'")]
        before={name:table(raw,name) for name in names}
        ddl=raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        raw.execute('CREATE TABLE deposit_current_memberships (reserved TEXT)')
    files=attachments(root)
    with open_database(path,writable=True) as db:
        assert feature_admission(db,FeatureRevision('company','co0020'),resolver=None) is None
        with pytest.raises(BookflowError) as caught:migrate_to_head(db,'company',tmp_path/'backups')
        assert caught.value.code=='E_MIGRATION_FAILED'
        assert db.raw.execute('SELECT version_num FROM alembic_version').fetchone()==('co0019',)
        assert db.raw.execute("SELECT name FROM sqlite_schema WHERE name='deposit_profiles'").fetchall()==[]
        db.raw.execute('DROP TABLE deposit_current_memberships')
        assert migrate_to_head(db,'company',tmp_path/'backups')==('co0019',HEADS['company'])
        # Columns later revisions add to a rebuilt table are the one difference allowed,
        # and which those are is derived from the migrations themselves: a literal list
        # here is what made every new migration falsify this test.
        omit=dict(_later_additions(M.revision),posting_line_sources=('deposit_component_id',))
        after={name:table(db.raw,name,omit_columns=tuple(omit.get(name,()))) for name in names}
        assert before==after
        stored={(kind,name):(owner,sql) for kind,name,owner,sql in db.raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema')}
        rebuilt,rewritten=_later_rebuilds(M.revision)
        for kind,name,owner,sql in ddl:
            if kind=='table' and name in {'transactions','document_lines','posting_line_sources'}|rebuilt:continue
            if name in {'document_lines_type_insert'}|rewritten:continue
            assert stored[(kind,name)]==(owner,sql),(kind,name)
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.raw.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
    assert attachments(root)==files and files
    (tmp_path/'raw-evidence.json').write_text(json.dumps(dict(base=BASE,before=before,after=after,attachments=files),indent=2))
    backups=list((tmp_path/'backups').rglob('*'))
    assert any(p.is_file() for p in backups)


def test_n4_storage_claim_release_redeposit_and_owned_inverse(client,sale,monkeypatch):
    """Lower-level G1 storage witness, not a public lifecycle/replay claim."""
    from tests.test_deposit_sources import uf,observe,source_row
    from tests.test_deposit_g1 import account,nets
    from tests.test_payment_receipts import method
    from tests.test_row8_journal import database_path
    from bookflow.company import deposits,deposit_validation
    from bookflow.company.deposit_models import Intent
    from bookflow.core.ids import new_id
    receipt=client.run('sales-receipt post',dict(customer=sale['customer'],date='2026-06-02',deposit_to=uf(client),payment_method=method(client),
        lines=[dict(item=sale['item'],unit_price='7')]),company='Demo Plumbing Co')
    source=observe(client,monkeypatch,'sales-receipt',receipt['id'])
    first=deposits.prepare(Intent(deposit_id=new_id(),date='2026-06-03',currency='USD',bank=account(source.uf_account+'x' if len(source.uf_account)<26 else 'fixturebank'),sources=(source_row(source,1),),additional=()))
    reverse=deposits.inverse(first,'original_batch')
    deposit_validation.validate(reverse)
    assert nets(first)=={first.intent.bank.id:700,source.uf_account:-700}
    assert nets(reverse)=={first.intent.bank.id:-700,source.uf_account:700}
    with open_database(database_path(client),writable=True) as db:
        import sqlalchemy as sa
        c=schema
        old=dict(db.conn.execute(sa.select(c.transactions).where(c.transactions.c.id==receipt['id'])).mappings().one())
        rev=dict(db.conn.execute(sa.select(c.transaction_revisions).where(c.transaction_revisions.c.id==source.revision_id)).mappings().one())
        event=rev['audit_event_id']
        provenance={k:rev[k] for k in ('created_at','created_by','created_via')}
        audited=dict(provenance,audit_event_id=event)
        bank=db.raw.execute("SELECT id FROM accounts WHERE type='bank' LIMIT 1").fetchone()[0]
        old_source_rows=table(db.raw,'posting_lines')
        def scaffolding(identity,number):
            rid,bid,lineid,rowid=new_id(),new_id(),new_id(),new_id()
            db.conn.execute(c.transactions.insert().values(**dict(old,id=identity,type='deposit',number=number,current_revision_id=rid)))
            db.conn.execute(c.transaction_revisions.insert().values(**dict(rev,id=rid,transaction_id=identity,date='2026-06-03',number=number)))
            db.conn.execute(c.deposit_profiles.insert().values(revision_id=rid,transaction_id=identity,type='deposit',bank_account_id=bank,posting_total=700,subtotal=700,bank_total=700,cash_back=0,facts_snapshot=first.model_dump_json(),**audited))
            db.conn.execute(c.document_line_identities.insert().values(id=lineid,transaction_id=identity,**provenance))
            db.conn.execute(c.deposit_row_keys.insert().values(id=rowid,transaction_id=identity,line_id=lineid,ordinal=1,kind='source',**audited))
            db.conn.execute(c.posting_batches.insert().values(id=bid,transaction_id=identity,revision_id=rid,kind='original',effective_date='2026-06-03',reverses_batch_id=None,replaces_batch_id=None,**audited))
            envelope=dict(db.conn.execute(sa.select(c.document_lines).where(c.document_lines.c.id==source.components[0].document_line_id)).mappings().one())
            envelope.update(id=new_id(),transaction_id=identity,revision_id=rid,line_id=lineid,kind='deposit')
            db.conn.execute(c.document_lines.insert().values(**envelope))
            key=source.components[0].key
            db.conn.execute(c.deposit_component_keys.insert().values(id=new_id(),transaction_id=identity,row_id=rowid,ordinal=1,kind=key.kind,semantic_identity=key.identity,tax_item_id=key.tax_item,**audited))
            component_id=new_id()
            db.conn.execute(c.deposit_components.insert().values(id=component_id,transaction_id=identity,revision_id=rid,document_line_id=envelope['id'],row_id=rowid,component_ordinal=1,role='funding',capacity=700,currency='USD',facts_snapshot=source.components[0].model_dump_json(),
                source_transaction_id=source.transaction_id,source_revision_id=source.revision_id,source_document_line_id=source.components[0].document_line_id,source_posting_line_id=source.components[0].posting_line_id,source_attribution_id=source.components[0].posting_source_id,**audited))
            template=dict(db.conn.execute(sa.select(c.posting_lines).where(c.posting_lines.c.id==source.components[0].posting_line_id)).mappings().one())
            for order,acct,debit,credit in ((1,bank,700,0),(2,source.uf_account,0,700)):
                leg=dict(template,id=new_id(),transaction_id=identity,batch_id=bid,line_no=order,account_id=acct,debit_minor_units=debit,credit_minor_units=credit)
                account_row=dict(db.conn.execute(sa.select(c.accounts).where(c.accounts.c.id==acct)).mappings().one())
                from bookflow.company.sales_defaults import NORMAL_BALANCE
                leg['account_snapshot']=json.dumps(dict(**{k:account_row[k] for k in ('id','name','full_name','number','type')},normal_balance=NORMAL_BALANCE[account_row['type']]))
                db.conn.execute(c.posting_lines.insert().values(**leg))
                db.conn.execute(c.posting_line_sources.insert().values(id=new_id(),transaction_id=identity,posting_line_id=leg['id'],revision_id=rid,document_line_id=envelope['id'],amount_minor_units=700,currency='USD',reversed_source_id=None,tax_component_id=None,payment_component_id=None,deposit_component_id=component_id,**provenance))
            return rid,bid,rowid
        db.raw.execute('BEGIN IMMEDIATE')
        rid,bid,rowid=scaffolding(first.intent.deposit_id,'G1-STORAGE-ONE')
        # Exercise migrated SQL, including exact ownership, without relaxing guards.
        from sqlalchemy.exc import IntegrityError
        for row_order, row_kind, key_kind, good_ordinal, role in (
                (2,'additional','additional',0,'funding'),
                (3,'additional','additional',0,'offset'),
                (4,'header','header',1,'cash_back')):
            lineid, extra_row = new_id(), new_id()
            db.conn.execute(c.document_line_identities.insert().values(id=lineid,transaction_id=first.intent.deposit_id,**provenance))
            db.conn.execute(c.deposit_row_keys.insert().values(id=extra_row,transaction_id=first.intent.deposit_id,line_id=lineid,ordinal=row_order,kind=row_kind,**audited))
            envelope=dict(db.conn.execute(sa.select(c.document_lines).where(c.document_lines.c.revision_id==rid)).mappings().first())
            envelope.update(id=new_id(),line_id=lineid,position=row_order)
            db.conn.execute(c.document_lines.insert().values(**envelope))
            key=dict(id=new_id(),transaction_id=first.intent.deposit_id,row_id=extra_row,ordinal=good_ordinal,kind=key_kind,semantic_identity=extra_row,tax_item_id='',**audited)
            for invalid in (-1, 1 if key_kind=='additional' else 0):
                with pytest.raises(IntegrityError):
                    db.conn.execute(c.deposit_component_keys.insert().values(**dict(key,ordinal=invalid)))
            db.conn.execute(c.deposit_component_keys.insert().values(**key))
            component=dict(id=new_id(),transaction_id=first.intent.deposit_id,revision_id=rid,document_line_id=envelope['id'],row_id=extra_row,component_ordinal=good_ordinal,role=role,capacity=1,currency='USD',facts_snapshot='{}',**audited)
            for invalid in (-1, 1 if key_kind=='additional' else 0):
                with pytest.raises(IntegrityError):
                    db.conn.execute(c.deposit_components.insert().values(**dict(component,component_ordinal=invalid)))
            if key_kind=='additional':
                with pytest.raises(IntegrityError):
                    db.conn.execute(c.deposit_components.insert().values(**dict(component,role='cash_back')))
                # Valid zero cannot borrow the source row's positive key.
                with pytest.raises(IntegrityError):
                    db.conn.execute(c.deposit_components.insert().values(**dict(component,row_id=rowid)))
            db.conn.execute(c.deposit_components.insert().values(**component))
        source_key=dict(id=new_id(),transaction_id=first.intent.deposit_id,row_id=rowid,ordinal=0,kind=source.components[0].key.kind,semantic_identity='invalid-zero',tax_item_id='',**audited)
        with pytest.raises(IntegrityError):
            db.conn.execute(c.deposit_component_keys.insert().values(**source_key))
        assert db.raw.execute('SELECT k.kind,k.ordinal,c.role,c.component_ordinal FROM deposit_component_keys k JOIN deposit_components c ON c.transaction_id=k.transaction_id AND c.row_id=k.row_id AND c.component_ordinal=k.ordinal ORDER BY k.rowid').fetchall()==[
            (source.components[0].key.kind,1,'funding',1),('additional',0,'funding',0),('additional',0,'offset',0),('header',1,'cash_back',1)]
        claim=dict(id=new_id(),kind='claim',transaction_id=first.intent.deposit_id,revision_id=rid,batch_id=bid,row_id=rowid,
            source_transaction_id=source.transaction_id,source_revision_id=source.revision_id,source_batch_id=source.business_batch_id,amount_minor_units=700,currency='USD',source_date=source.receipt_date,facts_snapshot=source.model_dump_json(),reverses_membership_id=None,**audited)
        db.conn.execute(c.deposit_memberships.insert().values(**claim))
        current=dict(source_transaction_id=source.transaction_id,transaction_id=first.intent.deposit_id,membership_id=claim['id'])
        db.conn.execute(c.deposit_current_memberships.insert().values(**current))
        # A second claim cannot displace a live owner, and deletion needs its exact release.
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):db.conn.execute(c.deposit_current_memberships.insert().values(**current))
        with pytest.raises(IntegrityError):db.conn.execute(c.deposit_current_memberships.delete())
        with pytest.raises(IntegrityError):db.conn.execute(c.deposit_memberships.update().values(amount_minor_units=699))
        bad=dict(claim,id=new_id(),kind='release',reverses_membership_id=claim['id'],amount_minor_units=699)
        with pytest.raises(IntegrityError):db.conn.execute(c.deposit_memberships.insert().values(**bad))
        # Exact retained reversal and void header, with receipt cash untouched.
        inverse_id=new_id()
        db.conn.execute(c.posting_batches.insert().values(id=inverse_id,transaction_id=first.intent.deposit_id,revision_id=rid,kind='reversal',effective_date='2026-06-03',reverses_batch_id=bid,replaces_batch_id=None,**audited))
        original_legs=[dict(r) for r in db.conn.execute(sa.select(c.posting_lines).where(c.posting_lines.c.batch_id==bid)).mappings()]
        for leg in original_legs:
            flipped=dict(leg,id=new_id(),batch_id=inverse_id,debit_minor_units=leg['credit_minor_units'],credit_minor_units=leg['debit_minor_units'],reversed_line_id=leg['id'])
            db.conn.execute(c.posting_lines.insert().values(**flipped))
            old_attribution=dict(db.conn.execute(sa.select(c.posting_line_sources).where(c.posting_line_sources.c.posting_line_id==leg['id'])).mappings().one())
            db.conn.execute(c.posting_line_sources.insert().values(**dict(old_attribution,id=new_id(),posting_line_id=flipped['id'],reversed_source_id=old_attribution['id'])))
        db.conn.execute(c.transactions.update().where(c.transactions.c.id==first.intent.deposit_id).values(version=2,status='voided',voided_at=provenance['created_at'],voided_by=provenance['created_by'],void_reason='G1 retained cancellation witness',void_posting_batch_id=inverse_id))
        assert db.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id=? AND account_id=?',(first.intent.deposit_id,bank)).fetchone()==(0,)
        assert db.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id IN (?,?) AND account_id=?',(receipt['id'],first.intent.deposit_id,source.uf_account)).fetchone()==(700,)
        release=dict(claim,id=new_id(),kind='release',reverses_membership_id=claim['id'])
        db.conn.execute(c.deposit_memberships.insert().values(**release))
        db.conn.execute(c.deposit_current_memberships.delete().where(c.deposit_current_memberships.c.membership_id==claim['id']))
        second=new_id();r2,b2,row2=scaffolding(second,'G1-STORAGE-TWO')
        again=dict(claim,id=new_id(),transaction_id=second,revision_id=r2,batch_id=b2,row_id=row2)
        db.conn.execute(c.deposit_memberships.insert().values(**again))
        db.conn.execute(c.deposit_current_memberships.insert().values(source_transaction_id=source.transaction_id,transaction_id=second,membership_id=again['id']))
        assert db.raw.execute('SELECT kind,amount_minor_units FROM deposit_memberships ORDER BY rowid').fetchall()==[('claim',700),('release',700),('claim',700)]
        assert db.raw.execute('SELECT source_transaction_id,transaction_id FROM deposit_current_memberships').fetchall()==[(source.transaction_id,second)]
        assert db.raw.execute('SELECT status FROM transactions WHERE id=?',(receipt['id'],)).fetchone()==('posted',)
        assert table(db.raw,'posting_lines',through_rowid=old_source_rows['max_rowid'])==old_source_rows
        assert db.raw.execute('SELECT sum(debit_minor_units-credit_minor_units) FROM posting_lines WHERE transaction_id IN (?,?,?) AND account_id=?',(receipt['id'],first.intent.deposit_id,second,source.uf_account)).fetchone()==(0,)
        assert db.raw.execute('SELECT kind FROM posting_batches WHERE transaction_id=? ORDER BY rowid',(first.intent.deposit_id,)).fetchall()==[('original',),('reversal',)]
        assert db.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        db.raw.execute('ROLLBACK')

from tests.test_service_sales_lifecycle import sale
