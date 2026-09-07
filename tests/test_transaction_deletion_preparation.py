"""Private Delete with real dispatcher Sessions and independent full-row oracles."""
import pytest
from bookflow import BookflowError
from bookflow.core import registry
from bookflow.company.transaction_deletion import prepare_delete
from bookflow.company.transaction_deletion_validation import validate_delete
from bookflow.company.transaction_deletion_models import DeleteIntent
from bookflow.hub import access
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import journal_accounts, lines


def session_call(client, monkeypatch, function):
    command = registry.get('company show')
    original = command.plan
    result=[]
    def plan(inp, ctx, s):
        result.append(function(s,ctx.model_copy(update={'reason':'Remove duplicate'})))
        return original(inp,ctx,s)
    with monkeypatch.context() as m:
        m.setattr(command,'plan',plan)
        client.run('company show',{},company=COMPANY)
    return result[0]


def create(client,sale,journal_accounts,family):
    if family=='journal_entry':
        return client.journal.post(date='2026-01-12',lines=lines(journal_accounts),company=COMPANY)
    if family=='invoice':
        return client.run('invoice post',dict(date='2026-01-12',customer=sale['customer'],lines=[dict(item=sale['item'])]),company=COMPANY)
    method=client.run('payment-method create',dict(name='Deletion cash',kind='cash'),company=COMPANY)['id']
    data=dict(date='2026-01-12',customer=sale['customer'],deposit_to=journal_accounts[0],payment_method=method)
    if family=='payment':
        data.update(amount='12.34',operation_key='deletion-payment')
        return client.run('payment receive',data,company=COMPANY)
    data['lines']=[dict(item=sale['item'])]
    return client.run('sales-receipt post',data,company=COMPANY)


def raw(s):
    return tuple((db,tuple(database.raw.iterdump())) for db,database in (('hub',s.hub),('company',s.company)))


@pytest.mark.parametrize('family',['journal_entry','invoice','sales_receipt','payment'])
@pytest.mark.parametrize('voided',[False,True])
def test_all_families_exact_inverse_or_prior_void(client,sale,journal_accounts,monkeypatch,family,voided):
    record=create(client,sale,journal_accounts,family)
    noun={'journal_entry':'journal','sales_receipt':'sales-receipt'}.get(family,family)
    if voided:
        data={noun.replace('-','_'):record['id'],'expected_version':record['version']}
        if family=='payment': data['operation_key']='deletion-void'
        record=client.run(noun+' void',data,reason='Original cancellation',company=COMPANY)
    intent=DeleteIntent(family=family,transaction_id=record['id'],expected_version=record['version'])
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        before=raw(s)
        p=prepare_delete(s,ctx,intent)
        assert validate_delete(s,ctx,p)==p
        assert raw(s)==before
        assert p.tombstone.status=='deleted' and p.tombstone.after_version==record['version']+1
        if voided:
            assert p.inverse_rows==() and p.tombstone.retained_void_batch_id==p.facts.header.values()['void_posting_batch_id']
        else:
            old={r.values()['id']:r.values() for r in p.facts.rows if r.table=='posting_lines' and r.values()['batch_id']==p.facts.business_batch.values()['id']}
            new=[r.values() for r in p.inverse_rows if r.table=='posting_lines']
            assert len(old)==len(new)==2
            for v in new:
                a=old[v['reversed_line_id']]
                expected=dict(a,id=v['id'],created_at=p.provenance.at,created_by=s.actor.id,created_via=str(ctx.interface),
                    batch_id=p.tombstone.delete_posting_batch_id,debit_minor_units=a['credit_minor_units'],
                    credit_minor_units=a['debit_minor_units'],reversed_line_id=a['id'])
                assert v==expected
            assert sum(v['debit_minor_units'] for v in new)==1234
        return p
    session_call(client,monkeypatch,check)


@pytest.mark.parametrize('family',['journal_entry','invoice','sales_receipt','payment'])
def test_actual_default_gate_precedes_guessed_record_and_facts(client,monkeypatch,family):
    from bookflow.core.ids import new_id
    from bookflow.company import transaction_deletion_facts as f
    def forbidden(*args,**kwargs): raise AssertionError('company evidence read before activation')
    monkeypatch.setattr(f,'read',forbidden)
    def check(s,ctx):
        with pytest.raises(BookflowError) as e:
            prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=new_id(),expected_version=1))
        assert e.value.code=='E_PERMISSION' and e.value.details['reason']=='capability_not_activated'
    session_call(client,monkeypatch,check)


def test_corrected_current_only_and_independent_balanced_mutations(client,sale,journal_accounts,monkeypatch):
    from bookflow.company.transaction_deletion_models import StoredRow
    record=create(client,sale,journal_accounts,'journal_entry')
    record=client.journal.update(journal=record['id'],expected_version=1,date='2026-02-01',lines=lines(journal_accounts,'15.00'),company=COMPANY)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        p=prepare_delete(s,ctx,DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=2))
        validate_delete(s,ctx,p)
        assert p.facts.business_batch.values()['effective_date']=='2026-02-01'
        legs=[r for r in p.inverse_rows if r.table=='posting_lines']
        assert sum(r.values()['debit_minor_units'] for r in legs)==1500
        # Preserve balance but substitute the other account on both legs.
        def edit(r,changes):
            values=r.values();values.update(changes)
            return StoredRow(table=r.table,cells=tuple(values.items()))
        changed=tuple(edit(r,{'account_id':journal_accounts[1-journal_accounts.index(r.values()['account_id'])]})
            if r.table=='posting_lines' else r for r in p.inverse_rows)
        variants=[p.model_copy(update={'inverse_rows':changed}),
            p.model_copy(update={'inverse_rows':p.inverse_rows[:-1]}),
            p.model_copy(update={'tombstone':p.tombstone.model_copy(update={'after_version':99})}),
            p.model_copy(update={'facts':p.facts.model_copy(update={'rows':p.facts.rows[:-1]})}),
            p.model_copy(update={'provenance':p.provenance.model_copy(update={'event_id':p.facts.business_batch.values()['audit_event_id']})})]
        before=raw(s)
        for bad in variants:
            with pytest.raises(BookflowError): validate_delete(s,ctx,bad)
        assert raw(s)==before
    session_call(client,monkeypatch,check)


def test_stored_balanced_corruption_rejected_against_owned_audit(client,sale,journal_accounts,monkeypatch):
    import sqlite3
    from tests.test_row8_journal import database_path
    record=create(client,sale,journal_accounts,'journal_entry')
    # Real disposable SQL, an intentional corruption with unchanged balance/FKs.
    with sqlite3.connect(database_path(client)) as db:
        triggers=db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='posting_lines'").fetchall()
        for name,_ in triggers: db.execute('DROP TRIGGER "'+name+'"')
        db.execute('UPDATE posting_lines SET account_id=CASE account_id WHEN ? THEN ? ELSE ? END WHERE transaction_id=?',
            (*journal_accounts,journal_accounts[0],record['id']))
        for _,ddl in triggers: db.execute(ddl)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        before=raw(s)
        with pytest.raises(BookflowError) as error:
            prepare_delete(s,ctx,DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=1))
        assert error.value.code=='E_INTERNAL' and raw(s)==before
    session_call(client,monkeypatch,check)


@pytest.mark.parametrize('voided',[False,True])
def test_closed_date_posted_rejected_voided_retains_event(client,sale,journal_accounts,monkeypatch,voided):
    record=create(client,sale,journal_accounts,'journal_entry')
    if voided: record=client.journal.void(journal=record['id'],expected_version=1,reason='Original void',company=COMPANY)
    client.run('company update',dict(closing_date='2026-02-01'),company=COMPANY)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        intent=DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=record['version'])
        before=raw(s)
        if voided:
            p=prepare_delete(s,ctx,intent);validate_delete(s,ctx,p)
            assert p.inverse_rows==() and p.facts.prior_void_batch.values()['effective_date']=='2026-01-12'
        else:
            with pytest.raises(BookflowError) as e: prepare_delete(s,ctx,intent)
            assert e.value.code=='E_PERIOD_CLOSED'
        assert raw(s)==before
    session_call(client,monkeypatch,check)


def test_work_historical_removed_and_current_roots_have_exact_release_events(client,sale,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    source=accepted(client,sale,lines=[dict(item=sale['item'],net_amount='10.01'),dict(item=sale['item'],net_amount='20.02')])
    original=bill(client,source)
    kept=original['revision']['lines'][1]['line_id']
    record=client.run('invoice update',dict(invoice=original['id'],expected_version=1,
        lines=[dict(line_id=kept,item=sale['item'])]),company=COMPANY)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        before=raw(s)
        p=prepare_delete(s,ctx,DeleteIntent(family='invoice',transaction_id=record['id'],expected_version=2));validate_delete(s,ctx,p)
        assert len(p.work_releases)==3 and sum(r.newly_released for r in p.work_releases)==1
        prior={r.event_id for r in p.work_releases if not r.newly_released}
        assert len(prior)==1 and p.provenance.event_id not in prior
        assert ('customer-work','standard') in p.facts.required_resources
        assert ('ledger.post','standard') not in p.facts.required_resources
        assert len(p.facts.work_allocation_ids)==3 and len(p.facts.current_work_allocation_ids)==1
        assert raw(s)==before
        for r in p.work_releases:
            bad=p.model_copy(update={'work_releases':tuple(x for x in p.work_releases if x!=r)})
            with pytest.raises(BookflowError): validate_delete(s,ctx,bad)
    session_call(client,monkeypatch,check)


def test_payment_application_block_then_complete_unapply_history(client,sale,journal_accounts,monkeypatch):
    invoice=create(client,sale,journal_accounts,'invoice')
    method=client.run('payment-method create',dict(name='Applied deletion cash',kind='cash'),company=COMPANY)['id']
    payment=client.run('payment receive',dict(customer=sale['customer'],date='2026-01-12',amount='12.34',payment_method=method,
        applications=dict(mode='inline',items=[dict(invoice=invoice['id'],amount='12.34',expected_version=invoice['version'])]),operation_key='delete-applied'),company=COMPANY)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def blocked(s,ctx):
        for family,record in [('invoice',invoice),('payment',payment)]:
            version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(record['id'],)).fetchone()[0]
            with pytest.raises(BookflowError) as e:
                prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=record['id'],expected_version=version))
            assert e.value.code=='E_HAS_APPLICATIONS'
    session_call(client,monkeypatch,blocked)
    application=payment['effect']['applications'][0]['application_id']
    client.run('payment unapply',dict(payment=payment['id'],applications=[dict(application_id=application,invoice_expected_version=client.run('invoice show',dict(invoice=invoice['id']),company=COMPANY)['version'])],expected_version=payment['version'],operation_key='delete-unapply'),reason='Release application',company=COMPANY)
    def check(s,ctx):
        version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(payment['id'],)).fetchone()[0]
        p=prepare_delete(s,ctx,DeleteIntent(family='payment',transaction_id=payment['id'],expected_version=version));validate_delete(s,ctx,p)
        assert set(p.facts.participants)=={invoice['id'],payment['id']}
        apps=[r.values() for r in p.facts.rows if r.table=='applications']
        assert len(apps)==2 and {r['kind'] for r in apps}=={'apply','unapply'}
        assert sum(r.table=='application_allocations' for r in p.facts.rows)>=2
    session_call(client,monkeypatch,check)


def test_captured_tax_and_inactive_account_exact_reversal(client,tax_sale,monkeypatch):
    import sqlite3
    from tests.test_tax_policy_sales import request
    from tests.test_row8_journal import database_path
    record=client.run('invoice post',request(tax_sale,nets=('10.00',)),company=COMPANY)
    assert record['total_minor_units']==1100 and record['tax_minor_units']==100
    with sqlite3.connect(database_path(client)) as db:
        db.execute('UPDATE accounts SET active=0 WHERE id=?',(tax_sale['income'],))
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        p=prepare_delete(s,ctx,DeleteIntent(family='invoice',transaction_id=record['id'],expected_version=1));validate_delete(s,ctx,p)
        result={}
        for r in p.inverse_rows:
            if r.table=='posting_lines':
                v=r.values();result[v['account_id']]=result.get(v['account_id'],0)+v['debit_minor_units']-v['credit_minor_units']
        assert result=={record['revision']['profile']['control_account']['id']:-1100,tax_sale['income']:1000,tax_sale['liability']:100}
        tax=[r.values() for r in p.facts.rows if r.table=='sales_tax_components']
        assert len(tax)==2 and sorted(r['tax_minor_units'] for r in tax)==[50,50]
        sources=[r.values() for r in p.inverse_rows if r.table=='posting_line_sources' and r.values()['tax_component_id']]
        assert {r['tax_component_id'] for r in sources}=={r['id'] for r in tax}
        assert all(r['amount_minor_units']==50 for r in sources)
    session_call(client,monkeypatch,check)


from tests.test_tax_policy_sales import tax_sale


def write_session_call(client, monkeypatch, function):
    """Fixture-only write command, through the real dispatch transaction owner."""
    command=registry.get('company show');result=[]
    def apply(plan,ctx,s):
        result.append(function(s,ctx))
        return registry.Applied(plan.preview,[],'Owned fixture source setup',audited=True)
    with monkeypatch.context() as m:
        m.setattr(command,'writes',frozenset({'company'}))
        m.setattr(command,'kind','write');m.setattr(command,'truth','company')
        m.setattr(command,'apply',apply)
        client.run('company show',{},company=COMPANY)
    return result[0]


@pytest.mark.parametrize('family',['payment','sales_receipt'])
def test_current_deposit_claim_blocks_without_post_and_unauthorized_disclosure(client,sale,journal_accounts,monkeypatch,root,family):
    from bookflow.company import deposit_lifecycle,deposit_persistence
    from tests.test_deposit_sources import uf
    from tests.conftest import make_actor,as_user
    method=client.run('payment-method create',dict(name='Claim deletion cash',kind='cash'),company=COMPANY)['id']
    data=dict(date='2026-01-12',customer=sale['customer'],payment_method=method,deposit_to=uf(client))
    if family=='payment':
        data.update(amount='12.34',operation_key='claim-source');name='payment receive'
    else:
        data.update(lines=[dict(item=sale['item'])]);name='sales-receipt post'
    record=client.run(name,data,company=COMPANY)
    def deposit(s,ctx):
        inp=deposit_lifecycle.INPUTS['post'].model_validate(dict(operation_key='delete-claim-deposit',document=dict(
            mode='inline',date='2026-01-13',deposit_to=journal_accounts[0],
            sources=[dict(source_type=family,source=record['id'],expected_version=1)],additional=[])))
        plan=deposit_lifecycle.prepare(s,ctx,inp,'post')
        return deposit_persistence.execute(s,ctx,plan)
    deposited=write_session_call(client,monkeypatch,deposit)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    calls=[];original=access.require_resource
    def trace(s,resource,*args,**kwargs):
        calls.append(resource);return original(s,resource,*args,**kwargs)
    monkeypatch.setattr(access,'require_resource',trace)
    def check(s,ctx):
        before=raw(s)
        version=s.company.raw.execute('SELECT version FROM transactions WHERE id=?',(record['id'],)).fetchone()[0]
        with pytest.raises(BookflowError) as e:
            prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=record['id'],expected_version=version))
        assert e.value.code=='E_DEPOSIT_DEPENDENCY' and e.value.details['deposit']==deposited.current.id
        assert 'next' not in e.value.details and raw(s)==before
    session_call(client,monkeypatch,check)
    assert 'ledger.post' not in calls
    company_id=client.run('company show',{},company=COMPANY)['id']
    make_actor(root,'delete-readonly',company_role=(company_id,'readonly'))
    def denied(s,ctx):
        with pytest.raises(BookflowError) as e:
            prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=record['id'],expected_version=1))
        assert e.value.code=='E_PERMISSION' and deposited.current.id not in str(e.value.details)
    session_call(as_user(root,'delete-readonly'),monkeypatch,denied)


def test_all_real_role_defaults_deny(client,root,monkeypatch):
    from tests.conftest import make_actor,as_user
    from bookflow.core.ids import new_id
    company_id=client.run('company show',{},company=COMPANY)['id']
    for role in ('readonly','standard','admin','owner','hub_admin'):
        login='delete-default-'+role
        make_actor(root,login,hub_admin=role=='hub_admin',company_role=(company_id,'readonly' if role=='hub_admin' else role))
        def check(s,ctx):
            for family in ('journal_entry','invoice','sales_receipt','payment'):
                with pytest.raises(BookflowError) as e:
                    prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=new_id(),expected_version=1))
                assert e.value.code=='E_PERMISSION' and e.value.details['reason']=='capability_not_activated'
        session_call(as_user(root,login),monkeypatch,check)


def test_complete_history_above_page_limit(client,sale,monkeypatch):
    from tests.test_work_billing_lifecycle import accepted,bill
    source=accepted(client,sale,lines=[dict(item=sale['item'],net_amount='1.00') for _ in range(200)])
    first=bill(client,source)
    record=client.run('invoice update',dict(invoice=first['id'],expected_version=1,
        lines=[dict(line_id=v['line_id'],item=sale['item']) for v in first['revision']['lines'][1:]]),company=COMPANY)
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        p=prepare_delete(s,ctx,DeleteIntent(family='invoice',transaction_id=record['id'],expected_version=2));validate_delete(s,ctx,p)
        assert len(p.work_releases)==399 and sum(r.newly_released for r in p.work_releases)==199
        assert len({r.root_line_id for r in p.work_releases})==200
        assert len(p.facts.current_work_allocation_ids)==199
        assert sum(r.values()['credit_minor_units'] for r in p.inverse_rows if r.table=='posting_lines')==19900
    session_call(client,monkeypatch,check)


def test_foreign_captured_bytes_and_custom_slots_are_retained(client,journal_accounts,monkeypatch):
    import sqlite3
    from tests.test_foreign_journals import rate,post
    from tests.test_row8_journal import database_path
    from tests.test_row8_custom_field_integration import definition
    field=definition(client,'Deletion retained custom')
    rate(client)
    record=post(client,journal_accounts,custom_fields={field['id']:'Original value'})
    client.run('custom-field deactivate',{'custom_field':field['id']},company=COMPANY)
    rate(client,value='0.01',version=1)
    with sqlite3.connect(database_path(client)) as db:
        db.execute('UPDATE accounts SET active=0 WHERE id=?',(journal_accounts[0],))
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        before=raw(s)
        p=prepare_delete(s,ctx,DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=1));validate_delete(s,ctx,p)
        new=[r.values() for r in p.inverse_rows if r.table=='posting_lines']
        assert len(new)==2
        slots=[r.values() for r in p.facts.rows if r.table=='custom_field_values']
        assert len(slots)==1 and slots[0]['canonical_text']=='Original value'
        assert {(r['original_minor_units'],r['original_currency'],r['rate_used']) for r in new}=={(2345,'JPY','0.0068')}
        assert sum(r['debit_minor_units'] for r in new)==1595
        assert raw(s)==before
    session_call(client,monkeypatch,check)


def test_fresh_revalidation_version_binding_reason_and_prior_void_corruption(client,sale,journal_accounts,monkeypatch):
    import sqlite3
    from tests.test_row8_journal import database_path
    from bookflow.core.ids import new_id
    record=create(client,sale,journal_accounts,'journal_entry')
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    intent=DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=1)
    p=session_call(client,monkeypatch,lambda s,ctx:prepare_delete(s,ctx,intent))
    def invalid(s,ctx):
        with pytest.raises(BookflowError) as e: prepare_delete(s,ctx.model_copy(update={'reason':' '}),intent)
        assert e.value.code=='E_REASON_REQUIRED'
        with pytest.raises(BookflowError) as e: prepare_delete(s,ctx.model_copy(update={'on_behalf_of':new_id()}),intent)
        assert e.value.code=='E_UNAUTHENTICATED'
    session_call(client,monkeypatch,invalid)
    client.journal.update(journal=record['id'],expected_version=1,lines=lines(journal_accounts,'15.00'),company=COMPANY)
    def stale(s,ctx):
        with pytest.raises(BookflowError) as e:validate_delete(s,ctx,p)
        assert e.value.code=='E_VERSION_CONFLICT'
    session_call(client,monkeypatch,stale)
    client.journal.void(journal=record['id'],expected_version=2,reason='Authentic second revision void',company=COMPANY)
    with sqlite3.connect(database_path(client)) as db:
        wrong=db.execute("SELECT id FROM posting_batches WHERE transaction_id=? AND kind='reversal' ORDER BY rowid LIMIT 1",(record['id'],)).fetchone()[0]
        db.execute('UPDATE transactions SET void_posting_batch_id=? WHERE id=?',(wrong,record['id']))
    def corrupt(s,ctx):
        with pytest.raises(BookflowError) as e:prepare_delete(s,ctx,intent.model_copy(update={'expected_version':3}))
        assert e.value.code=='E_INTERNAL'
    session_call(client,monkeypatch,corrupt)


@pytest.mark.parametrize('family',['invoice','sales_receipt','payment'])
@pytest.mark.parametrize('voided',[False,True])
def test_corrected_commercial_and_cash_families_current_effect_only(client,sale,journal_accounts,monkeypatch,family,voided):
    record=create(client,sale,journal_accounts,family)
    noun=family.replace('_','-')
    data={family:record['id'],'expected_version':1,'date':'2026-02-01'}
    if family=='payment': data.update(amount='15.00',operation_key='corrected-delete-cash')
    else: data.update(lines=[dict(item=sale['item'],unit_price='15.00')])
    record=client.run(noun+' update',data,company=COMPANY,reason='Correct cash document')
    if voided:
        data={family:record['id'],'expected_version':record['version']}
        if family=='payment':data['operation_key']='corrected-delete-cash-void'
        record=client.run(noun+' void',data,company=COMPANY,reason='Void corrected cash')
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        p=prepare_delete(s,ctx,DeleteIntent(family=family,transaction_id=record['id'],expected_version=record['version']))
        validate_delete(s,ctx,p)
        assert p.facts.revision.values()['total_minor_units']==1500
        assert p.facts.business_batch.values()['effective_date']=='2026-02-01'
        assert len([r for r in p.facts.rows if r.table=='transaction_revisions' and r.values()['transaction_id']==record['id']])==2
        assert sum(r.values()['credit_minor_units'] for r in p.inverse_rows if r.table=='posting_lines')==(0 if voided else 1500)
        if voided: assert p.inverse_rows==() and p.facts.prior_void_batch.values()['effective_date']=='2026-02-01'
    session_call(client,monkeypatch,check)


def test_consistent_event_collision_and_strict_blob_roundtrip(client,sale,journal_accounts,monkeypatch):
    from bookflow.company.transaction_deletion_models import PreparedDelete,StoredRow
    record=create(client,sale,journal_accounts,'journal_entry')
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        p=prepare_delete(s,ctx,DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=1))
        assert PreparedDelete.model_validate_json(p.model_dump_json())==p
        event=p.facts.business_batch.values()['audit_event_id']
        def collision(row):
            values=row.values()
            if row.table=='posting_batches':values['audit_event_id']=event
            return StoredRow(table=row.table,cells=tuple(values.items()))
        bad=p.model_copy(update={'provenance':p.provenance.model_copy(update={'event_id':event}),
            'tombstone':p.tombstone.model_copy(update={'delete_audit_event_id':event}),
            'inverse_rows':tuple(collision(r) for r in p.inverse_rows)})
        with pytest.raises(BookflowError) as error:validate_delete(s,ctx,bad)
        assert error.value.code=='E_INTERNAL'
    session_call(client,monkeypatch,check)


def test_current_header_requires_full_owning_audit_provenance(client,sale,journal_accounts,monkeypatch):
    import sqlite3
    from tests.test_row8_journal import database_path
    record=create(client,sale,journal_accounts,'journal_entry')
    with sqlite3.connect(database_path(client)) as db:
        # Valid header/FKs and entirely unchanged balanced ledger, but no owner
        # event/revision authorizes this current number.
        db.execute('UPDATE transactions SET number=? WHERE id=?',('unowned-number',record['id']))
    monkeypatch.setattr(access,'require_explicit_grant',lambda *a,**kw:None)
    def check(s,ctx):
        before=raw(s)
        with pytest.raises(BookflowError) as e:
            prepare_delete(s,ctx,DeleteIntent(family='journal_entry',transaction_id=record['id'],expected_version=1))
        assert e.value.code=='E_INTERNAL' and raw(s)==before
    session_call(client,monkeypatch,check)
