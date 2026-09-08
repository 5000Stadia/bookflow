"""Real saved recovery lifecycle records retain exact nonfinancial intent."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.audit import decode_snapshot
from bookflow.hub.audit_projection_legacy import decode_company_snapshot
from tests.test_audit_projection_activity import world,storage


@pytest.fixture(scope='module')
def recovered(world):
    from tests.test_service_sales_lifecycle import sale,COMPANY
    from tests.test_payment_recovery import setup,declaration,seal_compare,confirm,call
    client=world['client'];sales=sale.__wrapped__(client)
    draft,first,_=setup(client,sales)
    entries=[dict(invoice_id=first['id'],observed_invoice_version=1,action='remove')]
    begun=declaration(draft,entries);identifier,comparison=seal_compare(client,begun,entries)
    confirm(client,identifier,begun,comparison)
    draft=client.run('payment selection show',dict(selection=draft['id']),company=COMPANY)
    next_begin=declaration(draft,[])
    second=call(client,'begin',next_begin)['original_receipt']['recovery_id']
    replacement=declaration(draft,[])
    replaced=call(client,'replace',dict(recovery_id=second,expected_recovery_version=1,replacement=replacement))
    third=replaced['original_receipt']['replacement_recovery_id']
    call(client,'abort',dict(recovery_id=third,expected_recovery_version=1,disposition='discard_entire_attempt'))
    company=client.company.show(company=COMPANY);path=Path(company['path'])/'company.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute("SELECT a.id,a.command,e.record_type,e.action,e.before,e.after FROM audit_events a JOIN audit_entries e ON a.id=e.event_id WHERE a.command LIKE 'payment recovery %' AND a.id IN (SELECT event_id FROM audit_entries WHERE record_type='payment_selection_recovery' AND record_id IN (?,?,?)) ORDER BY a.seq,e.id",(identifier,second,third)).fetchall()
    return world['root'],company['company_id'],path,rows


def test_actual_recovery_all_six_write_actions_decode(recovered):
    _,_,path,rows=recovered;before=storage(path);seen=set()
    for event,command,kind,action,old,new in rows:
        seen.add(command)
        for blob in (old,new):
            if blob is None:continue
            raw=decode_snapshot(blob)
            view=decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
            assert view.model_dump(mode='json')['tag']==kind
    assert seen=={'payment recovery '+verb for verb in ('begin','upload','seal','apply','abort','replace')}
    assert storage(path)==before


@pytest.mark.parametrize('verb',['upload','apply','abort'])
def test_recovery_history_after_terminal_state_has_fresh_proof(recovered,verb):
    from bookflow.core.host import Host
    from bookflow.core.config import os_login
    from bookflow.core.context import Context,client_version
    from bookflow.core.publication import OSBinding,PublicationPermit
    from bookflow.hub.audit_projection import HistorySelection
    from bookflow.adapters.http.execution import run_history
    root,cid,path,rows=recovered;before=storage(path)
    event=next(event for event,command,*_ in rows if command=='payment recovery '+verb)
    host=Host(root,version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        out=run_history(host,HistorySelection(mode='show',company=cid,event=event),Context.new('http','recovery-history'),cred)
        entries=out['events'][0]['entries']
        if verb!='upload':
            header=next(x['after'] for x in entries if x['identity']['kind']=='payment_selection_recovery')
            assert header['state']=={'upload':'uploading','apply':'applied','abort':'aborted'}[verb]
            for key in ('intent_hash','attempt_generation','begin_request_hash','seal_request_hash','terminal_request_hash'):
                assert key not in header
            assert header['begin_request_snapshot']['context']=={}
            assert 'intent_hash' not in header['begin_request_snapshot']['input']
        if verb=='upload':
            chunk=next(x['after'] for x in entries if x['identity']['kind']=='payment_selection_recovery_chunk')
            intent=chunk['request_snapshot']['input']['entries'][0]
            assert intent['action']=='remove' and 'amount_minor_units' not in intent
            assert chunk['receipt_snapshot']['action']=='upload' and 'request_hash' not in chunk['receipt_snapshot']
        else:
            pointer=next(x for x in entries if x['identity']['kind']=='payment_selection_recovery_active')
            assert pointer['after'] is None and pointer['before']['selection_id']==header['selection_id']
        PublicationPermit.from_retained(out.permit.retained()).check(host,cred)
        assert storage(path)==before and host._readers_attached==0
    finally:host.stop()


@pytest.mark.parametrize('fault',['unknown_request','foreign_receipt','invalid_item','wrong_producer','wrong_action'])
def test_recovery_malformed_capture_fails_closed(recovered,fault):
    import copy,json
    from bookflow.core.errors import BookflowError
    _,_,path,rows=recovered;before=storage(path)
    wanted='payment_selection_recovery_item' if fault in ('invalid_item','wrong_producer','wrong_action') else 'payment_selection_recovery_chunk'
    _,command,kind,action,_,blob=next(row for row in rows if row[2]==wanted)
    raw=copy.deepcopy(decode_snapshot(blob))
    if fault=='unknown_request':
        data=json.loads(raw['request_snapshot']);data['input']['invented']='unowned';raw['request_snapshot']=json.dumps(data)
    elif fault=='foreign_receipt':
        data=json.loads(raw['receipt_snapshot']);data['recovery_id']='foreign';raw['receipt_snapshot']=json.dumps(data)
    elif fault=='invalid_item':raw['amount_minor_units']=1 # remove cannot carry an amount
    elif fault=='wrong_producer':command='payment recovery abort'
    else:action='delete'
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}
    assert storage(path)==before


@pytest.mark.parametrize('fault',['phase_action','phase_hash'])
def test_recovery_phase_request_receipt_agreement(recovered,fault):
    import copy,json
    from bookflow.core.errors import BookflowError
    _,_,path,rows=recovered;before=storage(path)
    _,command,kind,action,_,blob=next(row for row in rows if row[1]=='payment recovery apply' and row[2]=='payment_selection_recovery')
    raw=copy.deepcopy(decode_snapshot(blob))
    decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    if fault=='phase_action':
        request=json.loads(raw['terminal_request_snapshot'])
        request['command']='payment recovery abort'
        request['input']={key:request['input'][key] for key in ('recovery_id','expected_recovery_version')}
        request['input']['disposition']='discard_entire_attempt'
        # This is a valid command for the same attempt, but its saved receipt is apply.
        from bookflow.company.payment_recovery_models import AbortInput
        AbortInput.model_validate_json(json.dumps(request['input']))
        from bookflow.company.payment_queries import canonical,digest
        raw['terminal_request_snapshot']=canonical(request)
        raw['terminal_request_hash']=digest(request)
        receipt=json.loads(raw['terminal_receipt_snapshot']);receipt['request_hash']=digest(request)
        raw['terminal_receipt_snapshot']=canonical(receipt)
    else:
        receipt=json.loads(raw['terminal_receipt_snapshot'])
        assert receipt['request_hash']!='0'*64
        receipt['request_hash']='0'*64
        raw['terminal_receipt_snapshot']=json.dumps(receipt)
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}
    assert storage(path)==before


@pytest.mark.parametrize('phase',['begin','seal','terminal','upload'])
def test_recovery_request_body_is_bound_to_digest(recovered,phase):
    import copy,json
    from bookflow.company.payment_queries import canonical
    from bookflow.core.errors import BookflowError
    _,_,path,rows=recovered;before=storage(path)
    wanted='payment_selection_recovery_chunk' if phase=='upload' else 'payment_selection_recovery'
    _,command,kind,action,_,blob=next(row for row in rows if row[2]==wanted and (phase=='upload' or row[1]=='payment recovery apply'))
    raw=copy.deepcopy(decode_snapshot(blob))
    decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    key='request_snapshot' if phase=='upload' else phase+'_request_snapshot'
    request=json.loads(raw[key])
    if phase=='upload':request['input']['entries'][0]['observed_invoice_version']+=1
    else:request['input']['expected_version' if phase=='begin' else 'expected_recovery_version']+=1
    # Request is still structurally valid; neither stored hash is edited.
    raw[key]=canonical(request)
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}
    assert storage(path)==before


@pytest.mark.parametrize('phase',['begin','seal'])
def test_nonterminal_recovery_receipt_agreement(recovered,phase):
    import copy,json
    from bookflow.core.errors import BookflowError
    _,_,path,rows=recovered;before=storage(path)
    _,command,kind,action,_,blob=next(row for row in rows if row[1]=='payment recovery apply' and row[2]=='payment_selection_recovery')
    raw=copy.deepcopy(decode_snapshot(blob));key=phase+'_receipt_snapshot'
    receipt=json.loads(raw[key]);receipt['request_hash']='0'*64;raw[key]=json.dumps(receipt)
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}
    assert storage(path)==before


@pytest.mark.parametrize('phase',['begin','seal','terminal'])
def test_recovery_receipt_declared_count_matches_attempt(recovered,phase):
    import copy,json
    from bookflow.core.errors import BookflowError
    _,_,path,rows=recovered;before=storage(path)
    _,command,kind,action,_,blob=next(row for row in rows if row[1]=='payment recovery apply' and row[2]=='payment_selection_recovery')
    raw=copy.deepcopy(decode_snapshot(blob));key=phase+'_receipt_snapshot'
    receipt=json.loads(raw[key])
    assert receipt['declared_entry_count']==raw['declared_entry_count']
    receipt['declared_entry_count']+=1
    raw[key]=json.dumps(receipt)
    with pytest.raises(BookflowError) as error:
        decode_company_snapshot(producer=command,record_type=kind,action=action,snapshot=raw)
    assert error.value.code=='E_VALIDATION' and error.value.details=={'reason':'audit_format'}
    assert storage(path)==before
