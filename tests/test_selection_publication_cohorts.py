"""Complete historical sets and occurrence ordering, independent of page contents."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace, ModuleType
import hashlib
import json
import subprocess
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import payment_authority as pa, schema as c
from bookflow.core import publication_payment as pp
from tests.test_recovery_audit_cohort_integration import graph, EXPECTED
from tests.test_payment_publication_relations import outcome

BASE = '379a965a53bfdbd920a5f4db53376bb5d73dd499'


def frozen(path):
    source = subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=Path(__file__).resolve().parents[1])
    module = ModuleType('frozen_selection_reference')
    exec(compile(source, BASE + '/' + path, 'exec'), module.__dict__)
    module.source_sha256 = hashlib.sha256(source).hexdigest()
    return module


@contextmanager
def facts(rows):
    """Isolated relational evidence, not a corrupted Bookflow company."""
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        for name, values in rows.items():
            columns = tuple(c.metadata.tables[name].c.keys())
            conn.exec_driver_sql('CREATE TABLE '+name+' ('+','.join(x+' TEXT' for x in columns)+')')
            if values:
                conn.exec_driver_sql('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in columns)+')',
                                    [tuple(row.get(x) for x in columns) for row in values])
        yield SimpleNamespace(company=SimpleNamespace(conn=conn))
    engine.dispose()


@pytest.mark.parametrize('active', ['present', 'removed', 'replaced'])
def test_full_history_403_clear_and_shared_sql(active, monkeypatch, tmp_path):
    rows, _, expected = graph(active=active, fanout=403)
    rows['payment_selection_items'].append(dict(id='clear', selection_id='S', invoice_id=None))
    # A separate empty root must not inherit S's work requirement.
    rows['payment_selections'].append(dict(id='plain', consumed_operation_id=None))
    rows['work_billing_allocations'] = [dict(id='w', transaction_id='fanout-0402')]
    with facts(rows) as s:
        binds = []
        def trace(conn,cursor,statement,parameters,context,many): binds.append(len(parameters))
        sa.event.listen(s.company.conn, 'before_cursor_execute', trace)
        reader = pa._PublicationSelectionCohort(s.company, ['S', 'plain'])
        sa.event.remove(s.company.conn, 'before_cursor_execute', trace)
        assert reader.resolved['S'] == pa.record_transactions(s.company, 'payment_selection', 'S') == expected
        assert reader.resolved['plain'] == set() and None not in reader.resolved['S']
        assert max(binds) <= 200 and 200 in binds
        assert reader.requirements('S', False) == (('ledger.read','member'),('customer-work','member'))
        assert reader.requirements('plain', False) == (('ledger.read','member'),)
        old = frozen('src/bookflow/core/publication_payment.py'); calls=[]
        monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
        roots=[('payment_selection','plain',False),('payment_selection','S',False),('payment_selection','S',True)]
        previous=outcome(lambda:old.check(s,roots));prior=calls[:];calls.clear()
        current=outcome(lambda:pp.check(s,iter(roots)))
        assert previous==current=={'status':'ok'} and calls==prior and len(calls)==5
        (tmp_path/'graphs.json').write_text(json.dumps(dict(expected=sorted(expected),actual=sorted(reader.resolved['S']),binds=binds,calls=calls,old=previous,new=current)))


@pytest.mark.parametrize('sole', sorted(EXPECTED))
@pytest.mark.parametrize('through_application', [False, True])
def test_each_complete_dependency_is_sole_work_path(sole, through_application, monkeypatch):
    rows,_,expected=graph(active='removed')
    target='historical-paid' if through_application else sole
    rows['applications']=[dict(id='a',paying_transaction_id=sole,paid_transaction_id=target,kind='unapply')] if through_application else []
    # Application-only target intentionally absent: selection does not adopt R1.
    rows['work_billing_allocations']=[dict(id='w',transaction_id=target)]
    with facts(rows) as s:
        reader=pa._PublicationSelectionCohort(s.company,['S'])
        assert reader.resolved['S']==pa.record_transactions(s.company,'payment_selection','S')==expected
        assert reader.requirements('S',False)==pa.requirements(s.company,expected)==(('ledger.read','member'),('customer-work','member'))
        old=frozen('src/bookflow/core/publication_payment.py');calls=[]
        def deny(s,r,role):
            calls.append((r,role))
            if r=='customer-work':raise BookflowError('E_PERMISSION')
        monkeypatch.setattr(pa,'require_resource',deny)
        assert outcome(lambda:old.check(s,[('payment_selection','S',False)]))==outcome(lambda:pp.check(s,[('payment_selection','S',False)]))
        assert calls==[('ledger.read','member'),('customer-work','member')]*2


@pytest.mark.parametrize('shape',['missing-root','missing-D','missing-operation','operation-json','context-json','content-type','recursion'])
@pytest.mark.parametrize('reverse',[False,True])
def test_content_errors_are_root_local(shape,reverse,monkeypatch):
    rows,_,_=graph();rows['payment_selections'].append(dict(id='A',consumed_operation_id=None))
    if shape=='missing-root': rows['payment_selections']=[r for r in rows['payment_selections'] if r['id']!='S']
    if shape=='missing-D': rows['transactions']=[]
    if shape=='missing-operation': rows['payment_operations']=[]
    if shape=='operation-json': rows['payment_operations'][0]['request_snapshot']='{"resolved_transaction_ids":[null]}'
    if shape=='context-json': rows['payment_selection_revisions'][0]['context_snapshot']='{}'
    if shape in ('content-type','recursion'):
        original=pa._PublicationSelectionCohort._resolve
        def malformed(self,identifier):
            if identifier=='S':raise (TypeError('private-content') if shape=='content-type' else RecursionError('private-content'))
            return original(self,identifier)
        monkeypatch.setattr(pa._PublicationSelectionCohort,'_resolve',malformed)
    calls=[]
    def deny(s,r,role):calls.append((r,role));raise BookflowError('E_PERMISSION',details={'reason':'first-gate'})
    monkeypatch.setattr(pa,'require_resource',deny)
    with facts(rows) as s:
        roots=[('payment_selection',i,False) for i in (['S','A'] if reverse else ['A','S'])]
        result=outcome(lambda:pp.check(s,roots))
        assert result['code']==('E_IO' if reverse and shape in ('content-type','recursion') else 'E_PERMISSION')
        assert result['details']['reason']==('invalid_authority_evidence' if reverse and shape in ('content-type','recursion') else 'unresolved_payment_evidence' if reverse else 'first-gate')
        assert len(calls)==(0 if reverse else 1) and 'private-content' not in str(result)


@pytest.mark.parametrize('position',[199,200,201])
@pytest.mark.parametrize('failure',['permission','content'])
def test_no_later_cohort_consumed(position,failure,monkeypatch):
    consumed=[];loads=[];gates=[]
    class Reader:
        def __init__(self,db,ids):loads.append(ids)
        def requirements(self,identifier,write):
            if failure=='content' and int(identifier)==position:raise TypeError('content')
            return [('ledger.read','member')]
    def values():
        for i in range(1,402):consumed.append(i);yield(str(i),False)
    def gate(s,r,role):
        gates.append(r)
        if failure=='permission' and len(gates)==position:raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(pa,'_PublicationSelectionCohort',Reader);monkeypatch.setattr(pa,'require_resource',gate)
    result=outcome(lambda:pa.authorize_publication_selections(SimpleNamespace(company=None),values()))
    assert result['code']==('E_IO' if failure=='content' else 'E_PERMISSION')
    assert len(loads)==(1 if position<=200 else 2) and len(consumed)==len(loads)*200
    assert len(gates)==position-(failure=='content')


def test_no_second_hop_or_reverse_authority():
    rows,_,expected=graph()
    rows['applications']=[dict(id='a',paying_transaction_id='funding-old',paid_transaction_id='h'),dict(id='b',paying_transaction_id='h',paid_transaction_id='extra'),dict(id='c',paying_transaction_id='reverse',paid_transaction_id='saved-old')]
    rows['work_billing_allocations']=[dict(id='w',transaction_id=x) for x in ('extra','reverse')]
    with facts(rows) as s:
        reader=pa._PublicationSelectionCohort(s.company,['S'])
        assert reader.resolved['S']==expected
        assert reader.requirements('S',False)==pa.requirements(s.company,expected)==(('ledger.read','member'),)


@pytest.mark.parametrize('later_send',[False,True])
def test_driver_failure_forbids_pending_send(monkeypatch,later_send):
    import asyncio
    from bookflow.adapters.http.publication import PublicationMiddleware,protect
    checks=[];sent=[]
    def fail(self,*args):checks.append('driver');raise BookflowError('E_DB_BUSY')
    monkeypatch.setattr(pa._PublicationSelectionCohort,'_read',fail)
    class Document:
        credential = SimpleNamespace(token_id='owned')
        def check(self,**kw): pp.check(SimpleNamespace(company=None),[('payment_selection','S',False)])
    async def app(scope,receive,send):
        if later_send:await send({'type':'http.response.start','status':200,'headers':[]})
        protect(Document())
        await send({'type':'http.response.body' if later_send else 'http.response.start','status':200,'headers':[],'body':b'protected'})
    async def send(message):sent.append(message)
    async def receive():return {'type':'http.request'}
    async def run():await PublicationMiddleware(app)({'type':'http','path':'/test'},receive,send)
    if later_send:
        with pytest.raises(ConnectionAbortedError):asyncio.run(run())
    else:asyncio.run(run())
    assert checks==['driver'] and not any(b'protected' in m.get('body',b'') for m in sent)



def test_internal_driver_failure_denies_io_without_pending_send(monkeypatch):
    import asyncio
    import sqlite3
    from bookflow.adapters.http.publication import PublicationMiddleware, protect
    from bookflow.core.dispatch import guard

    attempted, sent = [], []
    with facts({'payment_selections': [dict(id='S', consumed_operation_id=None)]}) as s:
        def fail_execute(statement, *args, **kwargs):
            attempted.append(statement.get_final_froms()[0].name)
            raise sa.exc.OperationalError(
                'private selection SQL', {}, sqlite3.OperationalError('private driver failure'))

        # Leave the actual cohort _read body in place: a fallback that swallows
        # execute/driver failures must not turn I/O loss into missing evidence.
        monkeypatch.setattr(s.company.conn, 'execute', fail_execute)

        class Document:
            credential = SimpleNamespace(token_id='owned')

            def check(self, **kwargs):
                guard(lambda: pp.check(s, [('payment_selection', 'S', False)]))

        async def app(scope, receive, send):
            protect(Document())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'protected selection'})

        async def send(message):
            sent.append(message)

        async def receive():
            return {'type': 'http.request'}

        asyncio.run(PublicationMiddleware(app)({'type': 'http', 'path': '/test'}, receive, send))

    assert [m['status'] for m in sent if m['type'] == 'http.response.start'] == [403]
    body = b''.join(m.get('body', b'') for m in sent)
    denial = json.loads(body)
    assert denial['code'] == 'E_IO'
    assert denial['details'] == {'stage': 'publication', 'outcome': 'unknown'}
    assert attempted == ['payment_selections']  # abort immediately, no fallback/retry
    assert b'protected selection' not in body and b'private' not in body


@pytest.mark.parametrize('count',[0,1,200,201])
def test_empty_duplicate_occurrences_keep_write_flags(count,monkeypatch):
    rows,_,_=graph();rows['payment_selections']=[dict(id='plain',consumed_operation_id=None)]
    calls=[];loads=[];init=pa._PublicationSelectionCohort.__init__
    def measured(self,db,ids):loads.append(ids);init(self,db,ids)
    monkeypatch.setattr(pa._PublicationSelectionCohort,'__init__',measured)
    monkeypatch.setattr(pa,'require_resource',lambda s,r,role:calls.append((r,role)))
    with facts(rows) as s:
        pa.authorize_publication_selections(s,(('plain',bool(i%2)) for i in range(count)))
    assert calls==[('ledger.post','standard') if i%2 else ('ledger.read','member') for i in range(count)]
    assert loads==[['plain']]*((count+199)//200)
