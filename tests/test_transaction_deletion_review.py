"""Independent review corrections; real Sessions, small existing-owner fixtures."""
import io
import sqlite3
import pytest
from bookflow import BookflowError
from bookflow.company.transaction_deletion import prepare_delete,require_ready
from bookflow.company.transaction_deletion_validation import validate_delete
from bookflow.company.transaction_deletion_models import DeleteIntent,BlockedDelete,StoredRow
from bookflow.company import transaction_deletion_facts as f
from bookflow.hub import access
from tests.test_transaction_deletion_preparation import session_call,write_session_call,create,raw
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_row8_journal import journal_accounts,database_path
from tests.conftest import make_actor,as_user

@pytest.fixture
def grant(monkeypatch):
    monkeypatch.setattr(access,'require_explicit_grant',lambda *args,**kwargs:None)

@pytest.fixture
def journal(client,sale,journal_accounts):
    return create(client,sale,journal_accounts,'journal_entry')

def intent(record,family='journal_entry'):
    return DeleteIntent(family=family,transaction_id=record['id'],expected_version=record['version'])

def changed(row,**patch):
    v=row.values();v.update(patch)
    return StoredRow(table=row.table,cells=tuple(v.items()))


def test_trim_reason_before_cap_projection_and_validation(client,journal,grant,monkeypatch):
    def check(s,ctx):
        padded=' \t'+'x'*140+' \n'
        ctx=ctx.model_copy(update={'reason':padded})
        p=prepare_delete(s,ctx,intent(journal))
        assert p.tombstone.delete_reason=='x'*140
        assert validate_delete(s,ctx,p)==p
        with pytest.raises(BookflowError):
            validate_delete(s,ctx,p.model_copy(update={'tombstone':p.tombstone.model_copy(update={'delete_reason':padded})}))
        for reason,code in [(' \n\t','E_REASON_REQUIRED'),('x'*141,'E_VALIDATION')]:
            with pytest.raises(BookflowError) as e:prepare_delete(s,ctx.model_copy(update={'reason':reason}),intent(journal))
            assert e.value.code==code
    session_call(client,monkeypatch,check)


@pytest.mark.parametrize('table',['sequences','notes','attachment_links','attachments'])
def test_scoped_preservation_detects_relevant_changes(client,journal,grant,monkeypatch,table):
    note=client.note.add(record_type='transaction',record_id=journal['id'],body='Original',company=COMPANY)['note']
    a=client.attachment.add(record_type='transaction',record_id=journal['id'],original_filename='one.txt',
        media_type='text/plain',input_stream=io.BytesIO(b'preserved bytes'),company=COMPANY)
    client.attachment.unlink(link=a['link']['id'],expected_version=1,company=COMPANY)
    p=session_call(client,monkeypatch,lambda s,ctx:prepare_delete(s,ctx,intent(journal)))
    assert {r.table for r in p.facts.rows}>={'sequences','notes','attachment_links','attachments'}
    assert next(r.values()['active'] for r in p.facts.rows if r.table=='attachment_links')==0
    # An unrelated type's counter and another record's annotation cannot stale this proof.
    with sqlite3.connect(database_path(client)) as db:
        db.execute("INSERT INTO sequences(name,next_number,prefix) VALUES('unrelated',7,'')")
    client.note.add(record_type='customer',record_id=client.customer.create(name='Unrelated note',company=COMPANY)['id'],body='Elsewhere',company=COMPANY)
    session_call(client,monkeypatch,lambda s,ctx:validate_delete(s,ctx,p))
    with sqlite3.connect(database_path(client)) as db:
        if table=='sequences':db.execute("UPDATE sequences SET next_number=next_number+1 WHERE name='journal_entry'")
        elif table=='notes':db.execute('UPDATE notes SET body=? WHERE id=?',('changed',note['id']))
        elif table=='attachment_links':db.execute('UPDATE attachment_links SET caption=? WHERE id=?',('changed',a['link']['id']))
        else:db.execute('UPDATE attachments SET original_filename=? WHERE id=?',('changed.txt',a['attachment']['id']))
    def stale(s,ctx):
        with pytest.raises(BookflowError):validate_delete(s,ctx,p)
    session_call(client,monkeypatch,stale)


@pytest.mark.parametrize('mode',['copy','negative'])
def test_balanced_wrong_sign_inverse_is_rejected(client,journal,grant,monkeypatch,mode):
    def check(s,ctx):
        p=prepare_delete(s,ctx,intent(journal));validate_delete(s,ctx,p)
        rows=[]
        for r in p.inverse_rows:
            if r.table=='posting_lines':
                v=r.values();d,c=v['debit_minor_units'],v['credit_minor_units']
                r=changed(r,debit_minor_units=c if mode=='copy' else -d,credit_minor_units=d if mode=='copy' else -c)
            rows.append(r)
        legs=[r.values() for r in rows if r.table=='posting_lines']
        assert sum(r['debit_minor_units'] for r in legs)==sum(r['credit_minor_units'] for r in legs)
        with pytest.raises(BookflowError):validate_delete(s,ctx,p.model_copy(update={'inverse_rows':tuple(rows)}))
    session_call(client,monkeypatch,check)


def test_work_prior_void_retains_original_release_and_rejects_new_flag(client,sale,grant,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    first=bill(client,accepted(client,sale))
    record=client.run('invoice void',dict(invoice=first['id'],expected_version=1),reason='Actual prior work release',company=COMPANY)
    client.run('company update',dict(closing_date='2026-12-31'),company=COMPANY)
    def check(s,ctx):
        before=raw(s);p=prepare_delete(s,ctx,intent(record,'invoice'))
        assert len(p.work_releases)==1 and not p.work_releases[0].newly_released
        assert p.work_releases[0].event_id==p.facts.prior_void_batch.values()['audit_event_id']!=p.provenance.event_id
        assert p.inverse_rows==() and validate_delete(s,ctx,p)==p
        bad=p.model_copy(update={'work_releases':(p.work_releases[0].model_copy(update={'newly_released':True}),)})
        with pytest.raises(BookflowError):validate_delete(s,ctx,bad)
        assert raw(s)==before
    session_call(client,monkeypatch,check)


def test_omitted_owned_evidence_with_retained_create_entry_rejected(client,journal,grant,monkeypatch):
    original=f.read
    omitted=[]
    def incomplete(s,table,field,ids):
        rows=original(s,table,field,ids)
        if table=='document_line_identities' and rows:
            omitted.append(rows[0]);return rows[1:]
        return rows
    monkeypatch.setattr(f,'read',incomplete)
    def check(s,ctx):
        with pytest.raises(BookflowError):prepare_delete(s,ctx,intent(journal))
        assert omitted
        assert s.company.raw.execute('SELECT count(*) FROM audit_entries WHERE record_type=? AND record_id=?',
            ('document_line_identity',omitted[0].values()['id'])).fetchone()==(1,)
    session_call(client,monkeypatch,check)


@pytest.mark.parametrize('fault',['snapshot_only','number_only'])
def test_header_checks_are_independently_required(client,journal,grant,monkeypatch,fault):
    from bookflow.core.audit import decode_snapshot,encode_snapshot
    with sqlite3.connect(database_path(client)) as db:
        if fault=='snapshot_only':
            db.execute("UPDATE transactions SET updated_via='cli' WHERE id=?",(journal['id'],))
        else:
            db.execute('UPDATE transactions SET number=? WHERE id=?',('different-number',journal['id']))
            entry=db.execute("SELECT id,after FROM audit_entries WHERE record_type='transaction' AND record_id=? AND version_after=1",(journal['id'],)).fetchone()
            value=decode_snapshot(entry[1]);value['number']='different-number'
            triggers=db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_entries'").fetchall()
            for name,_ in triggers:db.execute('DROP TRIGGER "'+name+'"')
            db.execute('UPDATE audit_entries SET after=? WHERE id=?',(encode_snapshot(value),entry[0]))
            for _,ddl in triggers:db.execute(ddl)
    def check(s,ctx):
        with pytest.raises(BookflowError):prepare_delete(s,ctx,intent(journal))
    session_call(client,monkeypatch,check)


@pytest.fixture
def delegated(client,root):
    from bookflow.storage.engine import open_database
    from bookflow.hub import schema as h
    from bookflow.core.session import now_iso
    company=client.company.show(company=COMPANY)['id']
    principal=make_actor(root,'delete-person',company_role=(company,'standard'))
    agent=make_actor(root,'delete-agent',kind='agent',owner_user_id=principal,company_role=(company,'standard'))
    with open_database(root/'hub.db',writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE')
        db.conn.execute(h.agent_authority.insert().values(agent_user_id=agent,epoch=1))
        db.conn.execute(h.agent_principals.insert().values(agent_user_id=agent,principal_user_id=principal,assigned_by=principal,assigned_at=now_iso()))
        db.raw.execute('COMMIT')
    return as_user(root,'delete-agent'),agent,principal


def test_actual_delegated_principal_rights_are_conjoined(client,journal,delegated,root,grant,monkeypatch):
    agent_client,agent,principal=delegated
    def allowed(s,ctx):
        ctx=ctx.model_copy(update={'on_behalf_of':principal})
        p=prepare_delete(s,ctx,intent(journal));assert validate_delete(s,ctx,p)==p
        assert (p.actor_id,p.principal_id)==(agent,principal)
    session_call(agent_client,monkeypatch,allowed)
    with sqlite3.connect(root/'hub.db') as db:
        db.execute("UPDATE memberships SET role='readonly' WHERE user_id=?",(principal,))
    def denied(s,ctx):
        with pytest.raises(BookflowError) as e:prepare_delete(s,ctx.model_copy(update={'on_behalf_of':principal}),intent(journal))
        assert e.value.code=='E_PERMISSION'
    session_call(agent_client,monkeypatch,denied)


def test_principal_ledger_read_denial_precedes_record_lookup(client,journal,delegated,grant,monkeypatch):
    agent_client,agent,principal=delegated
    original=access.require_resource;seen=[]
    def resource(s,capability,*args,**kwargs):
        if s.actor.id==principal and capability=='ledger.read':
            seen.append(principal);raise BookflowError('E_PERMISSION')
        return original(s,capability,*args,**kwargs)
    def unread(*args,**kwargs):raise AssertionError('record read before current principal ledger admission')
    monkeypatch.setattr(access,'require_resource',resource)
    monkeypatch.setattr(f,'read',unread)
    def check(s,ctx):
        with pytest.raises(BookflowError) as e:prepare_delete(s,ctx.model_copy(update={'on_behalf_of':principal}),intent(journal))
        assert e.value.code=='E_PERMISSION' and seen==[principal]
    session_call(agent_client,monkeypatch,check)


@pytest.fixture
def claimed(client,sale,journal_accounts,monkeypatch):
    from tests.test_payment_receipts import method
    from bookflow.company import deposit_lifecycle,deposit_persistence
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-01-12',amount='12.34',
        payment_method=method(client),operation_key='review-claim'),company=COMPANY)
    with monkeypatch.context() as patch:
        patch.setattr(access,'require_explicit_grant',lambda *args,**kwargs:None)
        unclaimed=session_call(client,patch,lambda s,ctx:prepare_delete(s,ctx,intent(payment,'payment')))
    def make(s,ctx):
        inp=deposit_lifecycle.INPUTS['post'].model_validate(dict(operation_key='review-deposit',document=dict(
            mode='inline',date='2026-01-13',deposit_to=journal_accounts[0],additional=[],
            sources=[dict(source_type='payment',source=payment['id'],expected_version=payment['version'])])))
        return deposit_persistence.execute(s,ctx,deposit_lifecycle.prepare(s,ctx,inp,'post'))
    result=write_session_call(client,monkeypatch,make)
    payment=client.run('payment show',dict(payment=payment['id']),company=COMPANY)
    return payment,result,unclaimed


def test_typed_blocker_has_no_effect_and_is_independently_reloaded(client,claimed,grant,monkeypatch):
    payment,deposit,unclaimed=claimed
    def check(s,ctx):
        before=raw(s);p=prepare_delete(s,ctx,intent(payment,'payment'))
        assert type(p) is BlockedDelete and 'inverse_rows' not in type(p).model_fields
        b=p.facts.blockers[0]
        actual=s.company.raw.execute('SELECT transaction_id,membership_id FROM deposit_current_memberships WHERE source_transaction_id=?',(payment['id'],)).fetchone()
        assert (b.kind,b.source_id,b.deposit_id,b.membership_id)==('deposit_claim',payment['id'],*actual)
        assert validate_delete(s,ctx,p)==p
        forged=unclaimed.model_copy(update={'intent':p.intent,'facts':p.facts,
            'tombstone':unclaimed.tombstone.model_copy(update={'before_version':payment['version'],
                'after_version':payment['version']+1})})
        with pytest.raises(BookflowError):validate_delete(s,ctx,forged)
        with pytest.raises(BookflowError) as e:require_ready(p)
        assert e.value.code=='E_DEPOSIT_DEPENDENCY' and e.value.details['deposit']==deposit.current.id
        for blockers in ((),(b.model_copy(update={'membership_id':payment['id']}),)):
            with pytest.raises(BookflowError):validate_delete(s,ctx,p.model_copy(update={'facts':p.facts.model_copy(update={'blockers':blockers})}))
        assert raw(s)==before
    session_call(client,monkeypatch,check)


def test_version_then_reason_before_claim_after_graph_authority(client,claimed,grant,monkeypatch):
    payment,deposit,unclaimed=claimed
    def check(s,ctx):
        for version,reason,code in [(payment['version']-1,' ','E_VERSION_CONFLICT'),(payment['version'],' ','E_REASON_REQUIRED'),
            (payment['version'],'x'*141,'E_VALIDATION')]:
            with pytest.raises(BookflowError) as e:
                require_ready(prepare_delete(s,ctx.model_copy(update={'reason':reason}),intent(payment,'payment').model_copy(update={'expected_version':version})))
            assert e.value.code==code and deposit.current.id not in str(e.value.details)
    session_call(client,monkeypatch,check)


def test_application_blocker_actionable_identity(client,sale,journal_accounts,grant,monkeypatch):
    invoice=create(client,sale,journal_accounts,'invoice')
    from tests.test_payment_receipts import method
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-01-12',amount='12.34',payment_method=method(client),
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],expected_version=1,amount='12.34')]),operation_key='review-applied'),company=COMPANY)
    def check(s,ctx):
        for family,record in [('invoice',invoice),('payment',payment)]:
            version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(record['id'],)).fetchone()[0]
            p=prepare_delete(s,ctx,intent(record,family).model_copy(update={'expected_version':version}))
            assert isinstance(p,BlockedDelete) and validate_delete(s,ctx,p)==p
            assert p.facts.blockers[0].application_ids==(payment['effect']['applications'][0]['application_id'],)
            with pytest.raises(BookflowError) as e:require_ready(p)
            assert e.value.code=='E_HAS_APPLICATIONS' and e.value.details[family+'_id']==record['id']
            assert e.value.details['action']=='unapply_first'
    session_call(client,monkeypatch,check)


def test_revision_note_and_absent_family_sequence_are_preserved(client,journal,grant,monkeypatch):
    revision=journal['revision']['id']
    note=client.note.add(record_type='transaction_revision',record_id=revision,body='Retained revision note',company=COMPANY)['note']
    with sqlite3.connect(database_path(client)) as db:db.execute("DELETE FROM sequences WHERE name='journal_entry'")
    p=session_call(client,monkeypatch,lambda s,ctx:prepare_delete(s,ctx,intent(journal)))
    assert not any(r.table=='sequences' for r in p.facts.rows)
    assert any(r.table=='notes' and r.values()['id']==note['id'] for r in p.facts.rows)
    session_call(client,monkeypatch,lambda s,ctx:validate_delete(s,ctx,p))
    with sqlite3.connect(database_path(client)) as db:db.execute("INSERT INTO sequences VALUES('journal_entry',9,'J')")
    def check(s,ctx):
        with pytest.raises(BookflowError):validate_delete(s,ctx,p)
    session_call(client,monkeypatch,check)
