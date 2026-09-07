"""Current composite authority and zero raw mutation on durable retry paths."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sqlite3
import pytest
from bookflow import BookflowError
from bookflow.company import payment_recovery as recovery,payment_authority
from bookflow.core.registry import Plan
from tests.conftest import make_actor,as_user
from tests.test_service_sales_lifecycle import sale,COMPANY
from tests.test_payment_recovery import setup,declaration,call,raw_books,seal_compare,confirm
from tests.test_payment_receipts import method
from tests.test_work_billing_lifecycle import accepted,bill


def test_live_expired_new_absent_keys_other_actor_and_writer_race_are_raw_readonly(client,sale,root,monkeypatch):
    draft,first,_=setup(client,sale)
    entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
    begin=declaration(draft,entries)
    original=call(client,'begin',begin,reason='Original preparation',idempotency_key='recovery-live-key')
    company=client.company.show(company=COMPANY)
    other_id=make_actor(root,'recovery-other',company_role=(company['id'],'owner'))
    other=as_user(root,'recovery-other')
    for who in (client,other):
        for key in (None,'recovery-live-key','recovery-new-key'):
            before=raw_books(root)
            result=call(who,'begin',begin,reason='Original preparation',**({'idempotency_key':key} if key else {}))
            assert result['original_receipt']==original['original_receipt'] and result['idempotent_replay']
            assert raw_books(root)==before
    path=Path(company['path'])/'company.db'
    with sqlite3.connect(path) as db:
        db.execute("UPDATE idempotency_keys SET created_at='2000-01-01T00:00:00+00:00' WHERE key='recovery-live-key'")
    before=raw_books(root)
    assert call(client,'begin',begin,reason='Original preparation',idempotency_key='recovery-live-key')['original_receipt']==original['original_receipt']
    assert raw_books(root)==before
    for changed in (None,'','Changed preparation'):
        with pytest.raises(BookflowError) as caught:
            call(client,'begin',begin,reason=changed,idempotency_key='recovery-live-key')
        assert caught.value.code=='E_RECOVERY_KEY_REUSED'
        assert raw_books(root)==before
    # Controlled stale-reader schedule: initial lookup and planner observed the
    # absence before the competing begin. Writer reprepare observes its receipt.
    # The real finalizer must rollback its principal upsert and avoid cache insertion.
    saved_prepare=recovery.prepare
    monkeypatch.setattr(recovery,'recover',lambda *args:None)
    monkeypatch.setattr(recovery,'prepare',lambda s,ctx,inp,verb:Plan(original,dict(input=inp,verb=verb)))
    before=raw_books(root)
    result=call(other,'begin',begin,reason='Original preparation',idempotency_key='writer-race-new-key')
    assert result['original_receipt']==original['original_receipt'] and raw_books(root)==before
    assert result['idempotent_replay']


def test_historical_added_work_target_protects_retries_pages_counts_and_audit(client,sale,root,monkeypatch):
    draft,_,_=setup(client,sale)
    protected=bill(client,accepted(client,sale))
    edits=[dict(invoice_id=protected['id'],observed_invoice_version=1,action='remove')]
    begin=declaration(draft,edits)
    begun=call(client,'begin',begin,idempotency_key='protected-begin')
    identifier=begun['original_receipt']['recovery_id']
    upload=dict(recovery_id=identifier,chunk_index=0,entries=edits)
    call(client,'upload',upload,idempotency_key='protected-upload')
    aborted=call(client,'abort',dict(recovery_id=identifier,expected_recovery_version=2,disposition='discard_entire_attempt'))
    event=aborted['original_receipt']['audit_event_id']
    import bookflow.hub.access as access
    ordinary=access.require_resource
    seen=[]
    def deny(s,capability,role):
        seen.append((capability,role))
        if capability=='customer-work':raise BookflowError('E_PERMISSION')
        return ordinary(s,capability,role)
    monkeypatch.setattr(access,'require_resource',deny)
    monkeypatch.setattr(payment_authority,'require_resource',deny)
    before=raw_books(root)
    for verb,inp in [('begin',begin),('upload',upload),('show',dict(recovery_id=identifier)),('items',dict(recovery_id=identifier))]:
        with pytest.raises(BookflowError) as caught:call(client,verb,inp)
        assert caught.value.code=='E_PERMISSION'
    for command,inp in [('payment selection show',dict(selection=draft['id'])),('payment selection items',dict(selection=draft['id'],revision=draft['version'])),
        ('audit show',dict(event=event)),('activity',dict(record_type='payment_selection',record_id=draft['id']))]:
        with pytest.raises(BookflowError) as caught:client.run(command,inp,company=COMPANY)
        assert caught.value.code=='E_PERMISSION'
    assert call(client,'query',dict(selection=draft['id']))['total_count']==0
    assert draft['id'] not in {r['id'] for r in client.run('payment selection query',{},company=COMPANY)['items']}
    assert raw_books(root)==before and ('customer-work','member') in seen


def test_begin_versus_fresh_consumption_has_one_atomic_winner(client,sale,root):
    draft,_,_=setup(client,sale)
    begin=declaration(draft,[])
    payment=dict(customer=sale['customer'],date='2026-06-01',amount='2.00',payment_method=method(client),operation_key='begin-versus-consume',
        applications=dict(mode='selection',selection=draft['id'],expected_version=draft['version']))
    import bookflow
    def attempt(command,inp):
        try:return bookflow.connect(data_root=str(root)).run(command,inp,company=COMPANY)
        except BookflowError as exc:return {'error':exc.code}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(attempt,'payment recovery begin',begin)
        second=pool.submit(attempt,'payment receive',payment)
        results=[first.result(),second.result()]
    assert sum('error' not in r for r in results)==1,results
    assert next(r['error'] for r in results if 'error' in r) in {'E_RECOVERY_PENDING','E_SELECTION_CONSUMED','E_DB_BUSY'}
    shown=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    if 'error' not in results[0]:
        assert shown['current_lifecycle']['state']=='recovery_uploading'
        assert client.run('payment query',dict(customer=sale['customer']),company=COMPANY)['total_count']==0
    else:
        assert shown['current_lifecycle']['state']=='consumed'
        assert client.run('payment query',dict(customer=sale['customer']),company=COMPANY)['total_count']==1


def test_every_durable_action_survives_expiry_and_actor_change_without_raw_mutation(client,sale,root):
    from uuid import uuid4
    draft,first,_=setup(client,sale)
    company=client.company.show(company=COMPANY)
    make_actor(root,'retry-peer',company_role=(company['id'],'owner'))
    peer=as_user(root,'retry-peer');path=Path(company['path'])/'company.db'
    edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
    receipts=[]
    def execute(verb,inp):
        key='live-'+str(uuid4())
        out=call(client,verb,inp,reason='Exact action reason',idempotency_key=key)
        receipts.append((verb,inp,key,out['original_receipt']))
        return out
    begin=declaration(draft,edits);identifier=execute('begin',begin)['original_receipt']['recovery_id']
    execute('upload',dict(recovery_id=identifier,chunk_index=0,entries=edits))
    execute('seal',dict(recovery_id=identifier,expected_recovery_version=2))
    comparison=call(client,'compare',dict(recovery_id=identifier,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash']))
    execute('apply',dict(recovery_id=identifier,expected_recovery_version=3,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint']))
    draft=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    begin=declaration(draft,[]);old=execute('begin',begin)['original_receipt']['recovery_id']
    replacement=declaration(draft,[])
    identifier=execute('replace',dict(recovery_id=old,expected_recovery_version=1,replacement=replacement))['original_receipt']['replacement_recovery_id']
    execute('abort',dict(recovery_id=identifier,expected_recovery_version=1,disposition='discard_entire_attempt'))
    # Every historical acknowledgement remains the exact original after terminal
    # transitions; a new actor/key must not refresh principals or cache headers.
    for verb,inp,key,receipt in receipts:
        for who in (client,peer):
            for transport in (None,key,'unused-'+str(uuid4())):
                before=raw_books(root)
                out=call(who,verb,inp,reason='Exact action reason',**({'idempotency_key':transport} if transport else {}))
                assert out['original_receipt']==receipt and out['idempotent_replay'] and raw_books(root)==before
        with sqlite3.connect(path) as db:
            db.execute("UPDATE idempotency_keys SET created_at='2000-01-01T00:00:00+00:00' WHERE key=?",(key,))
        before=raw_books(root)
        assert call(peer,verb,inp,reason='Exact action reason',idempotency_key=key)['original_receipt']==receipt
        assert raw_books(root)==before
        for reason in (None,'','Different reason'):
            with pytest.raises(BookflowError) as caught:call(peer,verb,inp,reason=reason,idempotency_key=key)
            assert caught.value.code=='E_RECOVERY_KEY_REUSED' and raw_books(root)==before


@pytest.mark.parametrize('opponent',['apply','abort','replace'])
def test_competing_terminal_actions_publish_at_most_one_whole_revision(client,sale,root,opponent):
    import bookflow
    draft,first,_=setup(client,sale)
    edits=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
    begin=declaration(draft,edits);identifier,comparison=seal_compare(client,begin,edits)
    apply=dict(recovery_id=identifier,expected_recovery_version=3,attempt_generation=begin['attempt_generation'],intent_hash=begin['intent_hash'],expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint'])
    second=apply if opponent=='apply' else dict(recovery_id=identifier,expected_recovery_version=3,**(dict(disposition='discard_entire_attempt') if opponent=='abort' else dict(replacement=declaration(draft,[]))))
    def attempt(verb,inp):
        try:return call(bookflow.connect(data_root=str(root)),verb,inp)
        except BookflowError as exc:return dict(error=exc.code)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(attempt,'apply',apply);b=pool.submit(attempt,opponent,second);results=[a.result(),b.result()]
    successes=[r for r in results if 'error' not in r]
    assert successes
    for result in results:
        if 'error' in result:assert result['error'] in {'E_RECOVERY_FINALIZED','E_VERSION_CONFLICT','E_DB_BUSY'}
    if opponent=='apply':
        assert len(successes)==2 and successes[0]['original_receipt']==successes[1]['original_receipt']
        assert sum(not r['idempotent_replay'] for r in successes)==1
    else:assert len(successes)==1
    current=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    assert client.run('payment query',dict(customer=sale['customer']),company=COMPANY)['total_count']==0
    terminal=call(client,'show',dict(recovery_id=identifier))
    if terminal['state']=='superseded':
        assert current['version']==draft['version'] and current['current_lifecycle']['state']=='recovery_uploading'
    else:
        assert current['version']==draft['version']+1 and current['current_lifecycle']['state']=='open'
        assert current['item_count']==(1 if terminal['state']=='applied' else 2)
