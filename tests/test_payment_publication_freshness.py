"""Fresh current checks, complete transport documents, and ordinary owned books."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import anyio
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.company import payment_authority as pa
from bookflow.core import registry, publication_payment as pp
from bookflow.core.context import Context, Interface
from bookflow.adapters.mcp.runtime import Runtime
from tests.test_mcp_runtime import credential
from tests.test_mcp_payment_publication import hosted, payment_graph, sale
from tests.test_mcp_registry_browsing import company_snapshot
from tests.mcp_matrix_support import Matrix
from tests.test_payment_publication_relations import original_checker, outcome
from tests.test_service_sales_lifecycle import COMPANY


def raw_snapshot(root,company):
    with sqlite3.connect((root/'hub.db').as_uri()+'?mode=ro',uri=True) as db:
        relative=db.execute('SELECT path FROM companies WHERE id=?',(company,)).fetchone()[0]
    path=root/relative/'company.db';result={'tables':{}}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        result['ddl']=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall()
        for (name,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall():
            quote=lambda value:'"'+value.replace('"','""')+'"'
            columns=db.execute('PRAGMA table_xinfo('+quote(name)+')').fetchall()
            expr=['rowid']+[f'{fn}({quote(c[1])})' if fn!='blob' else f'CAST({quote(c[1])} AS BLOB)' for c in columns for fn in ('typeof','quote','blob')]
            rows=db.execute('SELECT '+','.join(expr)+' FROM '+quote(name)+' ORDER BY rowid').fetchall()
            result['tables'][name]={'columns':columns,'rows':len(rows),'sha256':hashlib.sha256(repr(rows).encode()).hexdigest()}
    result['attachments']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/relative/'attachments').rglob('*') if p.is_file()}
    return result


def test_retained_query_sees_public_new_work_application(hosted,payment_graph,monkeypatch,tmp_path):
    paid=hosted.ok('payment.receive',dict(customer=hosted.ok('payment.show',{'payment':payment_graph['payment']},company=hosted.company_id)['revision']['profile']['payer']['id'],
        date='2026-06-03',amount='1.00',payment_method=hosted.ok('payment-method.query',{'limit':1},company=hosted.company_id)['items'][0]['id'],
        number='PUB-FRESH-PLAIN',operation_key='publication-fresh-plain',applications=dict(mode='inline',items=[])),company=hosted.company_id)
    cred=credential(hosted,monkeypatch);runtime=Runtime.for_host(hosted.handle.host);cmd=registry.get('payment query')
    intent=runtime.admit(cmd,Context.new(Interface.mcp,'fresh-query'),cred,hosted.company_id,'option',False)
    runtime.prepare_json(intent,{'number':'PUB-FRESH-PLAIN','limit':1},cred);document=runtime.execute_json(intent,cred)
    runtime.intents.delivery(intent);runtime.intents.finish(intent,receipt=json.dumps(document).encode(),publication=document.permit.retained())
    snapshots=[];load=pa._publication_transaction_facts
    def observe(s,ids):
        facts=load(s,ids);snapshots.append((s.company, {k:dict(v) for k,v in facts.items()}));return facts
    monkeypatch.setattr(pa,'_publication_transaction_facts',observe)
    document.check();assert not snapshots[-1][1][paid['id']]['linked_work'];first=snapshots[-1][0]
    before_change=raw_snapshot(hosted.root,hosted.company_id)
    changed=hosted.ok('payment.apply',dict(payment=paid['id'],expected_version=1,date='2026-06-03',operation_key='publication-fresh-apply',
        applications=dict(mode='inline',items=[dict(invoice=payment_graph['invoice'],expected_version=2,amount='0.01')])),company=hosted.company_id)
    assert changed['version']==2
    after_change=raw_snapshot(hosted.root,hosted.company_id)
    assert before_change['ddl']==after_change['ddl'] and before_change['attachments']==after_change['attachments']
    altered={t for t in before_change['tables'] if before_change['tables'][t]!=after_change['tables'][t]}
    assert altered=={'applications','application_allocations','audit_events','audit_entries','payment_operations','payment_operation_items','principals','transactions'}
    for table in ('applications','application_allocations','audit_events','payment_operations'):
        assert after_change['tables'][table]['rows']==before_change['tables'][table]['rows']+1
    document.check();assert snapshots[-1][1][paid['id']]['linked_work'] and snapshots[-1][0] is not first
    gate=pa.require_resource
    def deny(s,r,role):
        if r=='customer-work':raise BookflowError('E_PERMISSION')
        return gate(s,r,role)
    monkeypatch.setattr(pa,'require_resource',deny)
    with pytest.raises(BookflowError) as caught:runtime.lookup(intent.reference,cred)
    assert caught.value.code=='E_PERMISSION'
    monkeypatch.setattr(pa,'require_resource',gate)
    monkeypatch.setattr(cmd,'plan',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Retained result reexecuted')))
    assert json.loads(runtime.lookup(intent.reference,cred).receipt)==document
    assert raw_snapshot(hosted.root,hosted.company_id)==after_change
    (tmp_path/'fresh-graph.json').write_text(json.dumps({'altered_tables':sorted(altered),'before':before_change,'after':after_change,'retained':document},indent=2))


def test_actual_http_mcp_outputs_three_fences_and_current_denial(root,tmp_path,monkeypatch):
    binary=tmp_path/'bookflow-publication'
    binary.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(Path(__file__).resolve().parents[1]/"src")!r})\nfrom bookflow.adapters.cli.app import main\nmain()\n');binary.chmod(0o700)
    monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY',str(binary));monkeypatch.setattr('tests.conftest.BIN',binary)
    async def witness():
        matrix=Matrix();seen=[];original=pp.check
        def checked(s,roots):
            seen.append((s.company,tuple(roots)));return original(s,roots)
        monkeypatch.setattr(pp,'check',checked)
        try:
            await matrix.open(root,tmp_path/'surfaces')
            before={s:raw_snapshot(r,matrix.company) for s,r in matrix.roots.items()}
            payer=(await matrix.call('python','payment query',{'limit':1}))['items'][0]['customer_id']
            requests=[('payment query',{'limit':1}),('payment invoices',{'mode':'new_receipt','customer':payer,'date':'2026-06-02','q':'Absent search value'})]
            receipts={};fences=[]
            for surface in ('python','http','mcp'):
                receipts[surface]=[]
                for command,args in requests:
                    seen.clear();receipts[surface].append(await matrix.call(surface,command,args))
                    if surface=='http':
                        assert len(seen)==3 and len({id(s) for s,_ in seen})==3
                        if command=='payment invoices':
                            assert receipts[surface][-1]['items']==[]
                            assert all({'payer','work_access'}<={r[0] for r in roots} for _,roots in seen)
                        fences.append({'command':command,'checks':len(seen),'roots':[roots for _,roots in seen]})
            assert receipts['python']==receipts['http']==receipts['mcp']
            retained=await matrix.mcp.call_tool('bookflow_run',{'command':'payment query','input':{'limit':1},'company':matrix.company})
            assert not retained.is_error and retained.structured_content['items']
            reference=retained.meta['bookflow_transport']['operation_ref']
            gate=pa.require_resource
            def deny(s,r,role):
                if r=='ledger.read':raise BookflowError('E_PERMISSION')
                return gate(s,r,role)
            def rejected(s,roots):
                with monkeypatch.context() as patch:
                    patch.setattr(pa,'require_resource',deny);return original(s,roots)
            monkeypatch.setattr(pp,'check',rejected)
            denied={s:await matrix.call(s,'payment query',{'limit':1},rejected=True) for s in ('http','mcp')}
            assert denied['http']['code']==denied['mcp']['code']=='E_PERMISSION'
            assert denied['http']['message']==denied['mcp']['message']
            assert denied['mcp']['details']['stage']=='publication'
            assert denied['mcp']['details']['outcome']=='unknown'
            assert denied['mcp']['details']['operation']=='mcp_result'
            assert denied['mcp']['details']['operation_ref']
            assert denied['http']['code']=='E_PERMISSION' and denied['http']['details']=={'stage':'publication','outcome':'unknown'}
            assert 'items' not in denied['http']
            # Actual SDK retrieval invokes current authority, never the planner.
            monkeypatch.setattr(pp,'check',checked)
            def work_denied(s,roots):
                def reject_work(session,resource,role):
                    if resource=='customer-work':raise BookflowError('E_PERMISSION')
                    return gate(session,resource,role)
                with monkeypatch.context() as patch:
                    patch.setattr(pa,'require_resource',reject_work);return original(s,roots)
            monkeypatch.setattr(pp,'check',work_denied)
            query=registry.get('payment query')
            with monkeypatch.context() as patch:
                patch.setattr(query,'plan',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Retained MCP result replanned')))
                denied_retained=await matrix.mcp.call_tool('bookflow_run',{'input_ref':reference,'action':'execute'})
                assert denied_retained.is_error and denied_retained.structured_content['code']=='E_PERMISSION'
                monkeypatch.setattr(pp,'check',checked)
                recovered=await matrix.mcp.call_tool('bookflow_run',{'input_ref':reference,'action':'execute'})
                assert not recovered.is_error and recovered.structured_content==retained.structured_content
            for surface,r in matrix.roots.items():assert raw_snapshot(r,matrix.company)==before[surface]
            (tmp_path/'http-mcp-publication.json').write_text(json.dumps({'normal':receipts,'denied':denied,'http_fences':fences,'retained':retained.structured_content,'retained_denial':denied_retained.structured_content,'recovered':recovered.structured_content},indent=2))
        finally:
            if hasattr(matrix,'stack'):await matrix.close()
    anyio.run(witness)


@pytest.mark.parametrize('loss',['credential','membership'])
@pytest.mark.parametrize('fence',['start','body','retained'])
def test_current_outer_authority_at_each_send_and_retained(hosted,monkeypatch,tmp_path,loss,fence):
    """Real reader/credential/permit with the existing middleware send harness."""
    import asyncio
    from bookflow.adapters.http.publication import PublicationMiddleware, protect
    from tests.test_publication_review_behaviors import member, downgrade
    from tests.test_row3_host import Hosted
    actor,headers=member(hosted)
    issued=hosted.ok('token.issue',{'user':actor,'label':'Publication fence'})
    acting=Hosted(hosted.handle,hosted.root,'',hosted.company_id,issued,'',{})
    cred=credential(acting,monkeypatch);runtime=Runtime.for_host(hosted.handle.host)
    cmd=registry.get('payment query')
    intent=runtime.admit(cmd,Context.new(Interface.mcp,'outer-fence'),cred,hosted.company_id,'option',False)
    runtime.prepare_json(intent,{'limit':1},cred);document=runtime.execute_json(intent,cred)
    assert document['items']
    runtime.intents.delivery(intent);runtime.intents.finish(intent,receipt=json.dumps(document).encode(),publication=document.permit.retained())
    before=raw_snapshot(hosted.root,hosted.company_id)
    def change():
        if loss=='credential':hosted.ok('token.revoke',{'token':issued['token_id']})
        else:downgrade(hosted,actor,'readonly')  # Existing serialized valid authority fixture seam.
    terminal=[];sent=[]
    original=document.check
    def checked(**kw):
        try:return original(**kw)
        except BookflowError as exc:terminal.append(exc.to_dict());raise
    monkeypatch.setattr(document,'check',checked)
    if fence=='retained':
        change()
        with pytest.raises(BookflowError) as caught:runtime.lookup(intent.reference,cred)
        terminal.append(caught.value.to_dict())
    else:
        async def app(scope,receive,send):
            protect(document)
            if fence=='start':change()
            await send({'type':'http.response.start','status':200,'headers':[]})
            if fence=='body':change()
            await send({'type':'http.response.body','body':b'protected-payment-result','more_body':False})
        async def receive():return {'type':'http.request','body':b''}
        async def send(message):sent.append(message)
        invocation=PublicationMiddleware(app)({'type':'http','path':'/owned-publication-fence'},receive,send)
        if fence=='body':
            with pytest.raises(ConnectionAbortedError):asyncio.run(invocation)
            assert len(sent)==1 and sent[0]['status']==200
        else:
            asyncio.run(invocation);assert sent[0]['status']==(401 if loss=='credential' else 403)
        assert all(m.get('body')!=b'protected-payment-result' for m in sent)
    assert len(terminal)==1 and terminal[0]['code']==('E_UNAUTHENTICATED' if loss=='credential' else 'E_PERMISSION')
    assert raw_snapshot(hosted.root,hosted.company_id)==before
    (tmp_path/'outer-authority.json').write_text(json.dumps({'loss':loss,'fence':fence,'terminal':terminal,'sent':repr(sent),'raw':before},indent=2))
