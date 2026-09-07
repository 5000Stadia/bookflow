"""Ordered200occurrence loads, explicit graph errors, and send boundaries."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from bookflow.company import payment_authority as pa
from bookflow.core import publication_payment as pp
from bookflow import BookflowError
from tests.test_payment_publication_relations import graph, original_checker, outcome


@pytest.mark.parametrize('count',[0,1,200,201])
def test_occurrences_match_frozen_checker(graph,monkeypatch,tmp_path,count):
    s=graph(work=('p0',))
    occurrences=[('p0' if i%3==0 else 'other',bool(i%2)) for i in range(count)]
    calls=[];monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
    old=original_checker();roots=[('transaction',*item) for item in occurrences]
    previous=outcome(lambda:old.check(s,roots));previous_calls=calls[:];calls.clear()
    loads=[];load=pa._publication_transaction_facts
    def counted(session,ids):loads.append(list(ids));return load(session,ids)
    monkeypatch.setattr(pa,'_publication_transaction_facts',counted)
    current=outcome(lambda:pp.check(s,iter(roots)))
    expected=[]
    for identifier,write in occurrences:
        role='standard' if write else 'member'
        expected.append(('ledger.post' if write else 'ledger.read',role))
        if identifier=='p0':expected.append(('customer-work',role))
    assert previous==current=={'status':'ok'} and calls==previous_calls==expected
    assert loads==[list(dict.fromkeys(i for i,_ in occurrences[n:n+200])) for n in range(0,count,200)]
    (tmp_path/'ordered-occurrences.json').write_text(json.dumps({'occurrences':occurrences,'old':previous,'new':current,'old_calls':previous_calls,'new_calls':calls,'loads':loads,'old_source':old.source_sha256},indent=2))


@pytest.mark.parametrize('position',[199,200,201])
@pytest.mark.parametrize('failure',['graph','permission'])
def test_failure_positions_stop_future_load(monkeypatch,position,failure):
    consumed=[];loads=[];gates=[]
    def stream():
        for i in range(1,402):consumed.append(i);yield (str(i),False)
    def load(s,ids):
        loads.append(ids)
        return {i:{'linked_work':False,'unresolved_target':failure=='graph' and int(i)==position} for i in ids}
    def gate(s,r,role):
        gates.append((r,role))
        if failure=='permission' and len(gates)==position:raise BookflowError('E_PERMISSION',details={'reason':'ordered_gate'})
    monkeypatch.setattr(pa,'_publication_transaction_facts',load);monkeypatch.setattr(pa,'require_resource',gate)
    result=outcome(lambda:pa.authorize_publication_transactions(None,stream()))
    assert result['code']=='E_PERMISSION'
    assert result['details']['reason']==('unresolved_payment_evidence' if failure=='graph' else 'ordered_gate')
    assert len(gates)==position-(failure=='graph')
    assert len(loads)==(1 if position<=200 else 2)
    assert len(consumed)==200*len(loads)


@pytest.mark.parametrize('reverse',[False,True])
def test_known_missing_is_deferred_after_earlier_permission(monkeypatch,reverse):
    monkeypatch.setattr(pa,'_publication_transaction_facts',lambda s,ids:{'present':{'linked_work':False,'unresolved_target':False}})
    gates=[]
    def deny(s,r,role):gates.append((r,role));raise BookflowError('E_PERMISSION',details={'reason':'earlier_gate'})
    monkeypatch.setattr(pa,'require_resource',deny)
    values=['present','missing'];values=values[::-1] if reverse else values
    result=outcome(lambda:pa.authorize_publication_transactions(None,[(v,False) for v in values]))
    assert result['details']['reason']==('unresolved_payment_evidence' if reverse else 'earlier_gate')
    assert len(gates)==(0 if reverse else 1)


def test_mixed_root_runs_keep_every_boundary(monkeypatch):
    calls=[]
    monkeypatch.setattr(pa,'authorize_publication_transactions',lambda s,ids:calls.append(('transactions',list(ids))))
    monkeypatch.setattr(pa,'authorize_events',lambda s,ids:calls.append(('audit',list(ids))))
    monkeypatch.setattr(pa,'authorize_publication_payer',lambda s,i,write=False:calls.append(('payer',i,write)))
    monkeypatch.setattr(pp,'work_access',lambda s:calls.append(('work_access',)) or False)
    def disclosure(db,kind,identifier):calls.append(('scalar',kind,identifier));return set()
    monkeypatch.setattr(pa,'disclosure_transactions',disclosure)
    monkeypatch.setattr(pa,'authorize',lambda s,ids,write=False:calls.append(('scalar_gate',write)))
    roots=[('transaction','a',False),('transaction','a',True),('audit_event','e',False),('transaction','a',False),
           ('payer','p',True),('transaction','a',False),('note','n',False),('transaction','a',True),('work_access',False,False),('customer','n',False),
           ('payment_history','h',False),('invoice_settlement','i',False)]
    pp.check(SimpleNamespace(company=None),iter(roots))
    assert calls==[('transactions',[('a',False),('a',True)]),('audit',['e']),('transactions',[('a',False)]),
        ('payer','p',True),('transactions',[('a',False)]),('scalar','note','n'),('transactions',[('a',True)]),('work_access',),('scalar','customer','n'),
        ('scalar','payment_history','h'),('scalar_gate',False),('scalar','invoice_settlement','i'),('scalar_gate',False)]


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('later_send',[False,True])
def test_operational_load_failure_forbids_pending_send(monkeypatch,reverse,later_send):
    from bookflow.adapters.http.publication import PublicationMiddleware,protect
    enabled=False;gates=[];terminal=[]
    def load(s,ids):
        if enabled:raise BookflowError('E_DB_BUSY')
        return {i:{'linked_work':False,'unresolved_target':False} for i in ids}
    def gate(s,r,role):
        gates.append((r,role))
        if enabled:raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(pa,'_publication_transaction_facts',load);monkeypatch.setattr(pa,'require_resource',gate)
    ids=['earlier-denied','load-error'];ids=ids[::-1] if reverse else ids
    def check(**kw):
        try:pp.check(None,[('transaction',i,False) for i in ids])
        except BookflowError as e:terminal.append(e.code);raise
    async def app(scope,receive,send):
        nonlocal enabled
        protect(SimpleNamespace(check=check,credential=SimpleNamespace(token_id='owned')))
        enabled=not later_send
        await send({'type':'http.response.start','status':200,'headers':[]})
        if later_send:
            await send({'type':'http.response.body','body':b'authorized','more_body':True})
            gates.clear();enabled=True
        await send({'type':'http.response.body','body':b'pending','more_body':False})
    sent=[]
    async def send(message):sent.append(message)
    async def receive():return {'type':'http.request','body':b''}
    invocation=PublicationMiddleware(app)({'type':'http','path':'/owned'},receive,send)
    if later_send:
        with pytest.raises(ConnectionAbortedError):asyncio.run(invocation)
        assert sent[-1]['body']==b'authorized' and sent[-1]['more_body']
    else:
        asyncio.run(invocation);assert sent[0]['status']==403 and json.loads(sent[-1]['body'])['code']=='E_DB_BUSY'
    assert terminal==['E_DB_BUSY'] and gates==[]
    assert all(x.get('body')!=b'pending' for x in sent)


@pytest.mark.parametrize('before,after',[(False,False),(False,True),(True,False),(True,True)])
def test_empty_page_work_access_is_current(monkeypatch,before,after):
    monkeypatch.setattr(pp,'work_access',lambda s:after)
    result=outcome(lambda:pp.check(None,[('work_access',before,False)]))
    assert result==({'status':'ok'} if before==after else {'status':'error','code':'E_PERMISSION',
        'message':'The acting user may not run this command here.','details':{'reason':'payment_projection_changed'}})
