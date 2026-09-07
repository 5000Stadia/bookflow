"""Real registered reads, full output/cursor parity and fresh hosted disclosure."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import anyio
import pytest
import sqlalchemy as sa
from bookflow import BookflowError
from bookflow.core import registry, publication_payment as pp
from bookflow.company import payment_authority as pa
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_recovery import setup, declaration, call
from tests.test_row3_host import hosted as host_fixture
from tests.test_selection_publication_cohorts import BASE, frozen
from tests.test_payment_publication_freshness import raw_snapshot


def old_planner():
    source=subprocess.check_output(['git','show',BASE+':src/bookflow/commands/payment_cmds.py'],cwd=Path(__file__).resolve().parents[1])
    tree=ast.parse(source)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='selection_query')
    function.decorator_list=[]
    module=ast.Module(body=[function],type_ignores=[])
    namespace=dict(registry.get('payment selection query').plan.__globals__)
    namespace['selection']=frozen('src/bookflow/company/payment_selection.py')
    exec(compile(module,BASE+'/payment_cmds.py','exec'),namespace)
    return namespace['selection_query'],hashlib.sha256(source).hexdigest()


@pytest.fixture(scope='module')
def draft_books(_seeded_template,tmp_path_factory):
    import shutil
    import bookflow
    root=tmp_path_factory.mktemp('selection-201')/'root'
    shutil.copytree(_seeded_template,root)
    client=bookflow.connect(data_root=str(root))
    customer=client.customer.create(name='Selection page witness',company=COMPANY)['id']
    identifiers=[]
    for i in range(201):
        row=client.run('payment selection create',dict(mode='new_receipt',customer=customer,date='2026-06-01',amount='1',label=f'Straße 東京 {i:03}'),company=COMPANY)
        identifiers.append(row['id'])
    return root,identifiers


@pytest.fixture
def drafts(root,draft_books):
    import shutil
    source,identifiers=draft_books
    shutil.copytree(source,root,dirs_exist_ok=True)
    return identifiers


@pytest.fixture
def hosted(root,drafts):yield from host_fixture.__wrapped__(root)


@pytest.mark.parametrize('state,limit',[(state,limit) for limit in (1,25,200) for state in (None,'open','consumed','recovering') if (state,limit)!=(None,1)])
def test_201_registered_pages_cursors_and_sql_slope(hosted,drafts,monkeypatch,tmp_path,state,limit):
    command=registry.get('payment selection query');current=command.plan
    original,source_hash=old_planner();old=frozen('src/bookflow/core/publication_payment.py');check=pp.check
    executions={'old':0,'new':0};fences=[]
    def observed(s,roots):
        fences.append((s.company,tuple(roots)));return check(s,roots)
    def run(which,args):
        planner=original if which=='old' else current
        def counted(*a,**kw):executions[which]+=1;return planner(*a,**kw)
        with monkeypatch.context() as patch:
            patch.setattr(command,'plan',counted);patch.setattr(pp,'check',old.check if which=='old' else observed)
            return hosted.ok('payment.selection.query',args,company=hosted.company_id)
    before=raw_snapshot(hosted.root,hosted.company_id);pages=[]
    for state in (state,):
        for limit in (limit,):
            args={'limit':limit}
            if state is not None:args['state']=state
            seen=[]
            while True:
                a=run('old',args);b=run('new',args)
                assert a==b and json.dumps(a,sort_keys=True,ensure_ascii=False)==json.dumps(b,sort_keys=True,ensure_ascii=False)
                pages.append(dict(input=dict(args),old=a,new=b));seen.extend(x['id'] for x in b['items'])
                if not b['next_cursor']:break
                args['cursor']=a['next_cursor'] # exact old token accepted by new on next iteration
            assert len(seen)==len(set(seen))==b['total_count']
            if state in (None,'open'):assert set(drafts)<=set(seen)
    assert executions['old']==executions['new']==len(pages)>0
    (tmp_path/'pages.json').write_text(json.dumps(dict(base=BASE,planner_hash=source_hash,executions=executions,pages=pages),ensure_ascii=False))
    if state!='open' or limit!=200:
        assert raw_snapshot(hosted.root,hosted.company_id)==before
        return
    counts={};helper=[];init=pa._PublicationSelectionCohort.__init__
    def measured(self,db,ids):helper.append((db,list(ids)));init(self,db,ids)
    monkeypatch.setattr(pa._PublicationSelectionCohort,'__init__',measured)
    # The seed includes consumed/recovery histories. Use a current ordinary open
    # page whose newest 201 roots are the known empty/no-operation drafts.
    for limit in (10,200):
        counts[limit]={}
        for which in ('old','new'):
            statements=[];connections=[]
            def attach(connection,record,proxy):connection.set_trace_callback(statements.append);connections.append(connection)
            sa.event.listen(sa.engine.Engine,'checkout',attach)
            try:
                helper.clear();out=run(which,{'state':'open','limit':limit})
            finally:
                sa.event.remove(sa.engine.Engine,'checkout',attach)
                for conn in connections:
                    try:conn.set_trace_callback(None)
                    except Exception:pass
            assert len(out['items'])==limit and {x['id'] for x in out['items']}<=set(drafts)
            counts[limit][which]={'statements':len(statements),'selects':sum(s.lstrip().upper().startswith(('SELECT','WITH')) for s in statements)}
            if which=='new':assert len(helper)==3 and len({id(db) for db,_ in helper})==3 and all(len(ids)==limit for _,ids in helper)
    assert counts[10]['new']==counts[200]['new']
    assert counts[200]['old']['statements']-counts[10]['old']['statements']==2280
    assert counts[200]['old']['selects']-counts[10]['old']['selects']==2280
    assert counts[10]['old']['statements']-counts[10]['new']['statements']==108
    assert counts[200]['old']['statements']-counts[200]['new']['statements']==2388
    assert raw_snapshot(hosted.root,hosted.company_id)==before
    invalid=hosted.call('payment.selection.query',{'cursor':'invalid'},company=hosted.company_id)
    assert invalid.status_code==422 and invalid.json()['code']=='E_VALIDATION'
    (tmp_path/'pages-and-slope.json').write_text(json.dumps(dict(base=BASE,planner_hash=source_hash,executions=executions,pages=pages,counts=counts,raw=before),ensure_ascii=False))


def test_actual_four_interfaces_and_retained_current_authority(client,sale,root,monkeypatch,tmp_path):
    from tests.mcp_matrix_support import Matrix
    draft,first,second=setup(client,sale)
    binary=tmp_path/'bookflow-selection'
    binary.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(Path(__file__).resolve().parents[1]/"src")!r})\nfrom bookflow.adapters.cli.app import main\nmain()\n');binary.chmod(0o700)
    monkeypatch.setenv('BOOKFLOW_MCP_TEST_BINARY',str(binary));monkeypatch.setattr('tests.conftest.BIN',binary)
    async def witness():
        matrix=Matrix();seen=[];check=pp.check
        def observed(s,roots):seen.append((s.company,tuple(roots)));return check(s,roots)
        monkeypatch.setattr(pp,'check',observed)
        try:
            await matrix.open(root,tmp_path/'surfaces')
            before={k:raw_snapshot(r,matrix.company) for k,r in matrix.roots.items()};outputs={}
            for surface in ('python','cli','http','mcp'):
                seen.clear();outputs[surface]=await matrix.call(surface,'payment selection query',{'limit':200})
                assert any(r['id']==draft['id'] for r in outputs[surface]['items'])
                if surface=='http':assert len(seen)==3 and len({id(s) for s,_ in seen})==3
            assert outputs['python']==outputs['cli']==outputs['http']==outputs['mcp']
            retained=await matrix.mcp.call_tool('bookflow_run',{'command':'payment selection query','input':{'limit':200},'company':matrix.company})
            assert not retained.is_error and retained.structured_content['items']
            reference=retained.meta['bookflow_transport']['operation_ref'];gate=pa.require_resource
            def denied(s,roots):
                def deny(session,resource,role):
                    if resource=='ledger.read':raise BookflowError('E_PERMISSION')
                    return gate(session,resource,role)
                with monkeypatch.context() as patch:patch.setattr(pa,'require_resource',deny);return check(s,roots)
            monkeypatch.setattr(pp,'check',denied)
            errors={surface:await matrix.call(surface,'payment selection query',{'limit':200},rejected=True) for surface in ('http','mcp')}
            assert errors['http']['code']==errors['mcp']['code']=='E_PERMISSION' and 'items' not in errors['http']
            assert errors['mcp']['details']['operation']=='mcp_result'
            cmd=registry.get('payment selection query')
            with monkeypatch.context() as patch:
                patch.setattr(cmd,'plan',lambda *a,**k:(_ for _ in ()).throw(AssertionError('retained replanned')))
                refused=await matrix.mcp.call_tool('bookflow_run',{'input_ref':reference,'action':'execute'})
                assert refused.is_error and refused.structured_content['code']=='E_PERMISSION'
                monkeypatch.setattr(pp,'check',observed)
                restored=await matrix.mcp.call_tool('bookflow_run',{'input_ref':reference,'action':'execute'})
                assert not restored.is_error and restored.structured_content==retained.structured_content
            for k,r in matrix.roots.items():assert raw_snapshot(r,matrix.company)==before[k]
            (tmp_path/'interfaces.json').write_text(json.dumps(dict(outputs=outputs,errors=errors,retained=retained.structured_content,refused=refused.structured_content,raw=before)))
        finally:
            if hasattr(matrix,'stack'):await matrix.close()
    anyio.run(witness)


def test_new_historical_attempt_refreshes_selection_and_query_epoch(client,sale,root,monkeypatch,tmp_path):
    from tests.test_work_billing_lifecycle import accepted,bill
    from tests.test_mcp_runtime import credential
    from bookflow.adapters.mcp.runtime import Runtime
    from bookflow.core.context import Context,Interface
    draft,first,second=setup(client,sale)
    work=bill(client,accepted(client,sale))
    edits=[dict(invoice_id=work['id'],observed_invoice_version=1,action='remove')]
    begun=call(client,'begin',declaration(draft,edits))
    generator=host_fixture.__wrapped__(root);host=next(generator)
    try:
        cred=credential(host,monkeypatch);runtime=Runtime.for_host(host.handle.host);documents=[]
        for name,args in [('payment selection show',{'selection':draft['id']}),('payment selection query',{'state':'open','limit':1})]:
            cmd=registry.get(name);intent=runtime.admit(cmd,Context.new(Interface.mcp,'selection-freshness'),cred,host.company_id,'option',False)
            runtime.prepare_json(intent,args,cred);document=runtime.execute_json(intent,cred)
            runtime.intents.delivery(intent);runtime.intents.finish(intent,receipt=json.dumps(document).encode(),publication=document.permit.retained())
            documents.append((intent,document))
        observed=[];init=pa._PublicationSelectionCohort.__init__
        def measured(self,db,ids):init(self,db,ids);observed.append((db,dict(self.resolved)))
        monkeypatch.setattr(pa._PublicationSelectionCohort,'__init__',measured)
        documents[0][1].check();prior=observed[-1]
        assert prior[1][draft['id']]=={first['id'],second['id']}
        host.ok('payment.recovery.upload',dict(recovery_id=begun['original_receipt']['recovery_id'],chunk_index=0,entries=edits),company=host.company_id,headers={'X-Bookflow-Reason':'Record complete attempted target'})
        host.ok('payment.recovery.abort',dict(recovery_id=begun['original_receipt']['recovery_id'],expected_recovery_version=2,disposition='discard_entire_attempt'),company=host.company_id,headers={'X-Bookflow-Reason':'Retain abandoned history'})
        before=raw_snapshot(root,host.company_id)
        documents[0][1].check();assert observed[-1][0] is not prior[0] and observed[-1][1][draft['id']]=={first['id'],second['id'],work['id']}
        with pytest.raises(BookflowError) as stale:runtime.lookup(documents[1][0].reference,cred)
        assert stale.value.code=='E_PERMISSION' # Permit translates changed dependency to authority_changed.
        assert stale.value.details['reason']=='authority_changed'
        # The owning checker itself retains the exact stale code.
        captured=[];check=pp.check
        def inspect_check(s,roots):
            if any(k=='payment_selection_query_epoch' for k,_,_ in roots):
                oldroots=documents[1][1].permit.projection['payment_roots']
                with pytest.raises(BookflowError) as e:check(s,oldroots)
                assert e.value.code=='E_QUERY_STALE';captured.append(e.value.to_dict())
            return check(s,roots)
        with monkeypatch.context() as patch:
            patch.setattr(pp,'check',inspect_check)
            host.ok('payment.selection.query',{'limit':1},company=host.company_id)
        assert len(captured)==3
        gate=pa.require_resource
        def deny(s,r,role):
            if r=='customer-work':raise BookflowError('E_PERMISSION')
            return gate(s,r,role)
        monkeypatch.setattr(pa,'require_resource',deny)
        with pytest.raises(BookflowError):runtime.lookup(documents[0][0].reference,cred)
        response=host.call('payment.selection.show',{'selection':draft['id']},company=host.company_id)
        assert response.status_code==403 and 'context' not in response.json()
        assert raw_snapshot(root,host.company_id)==before
        (tmp_path/'freshness.json').write_text(json.dumps(dict(expected_before=sorted(prior[1][draft['id']]),expected_after=sorted({first['id'],second['id'],work['id']}),stale=captured,denied=response.json(),raw=before)))
    finally:generator.close()


@pytest.mark.parametrize('which',['old','new'])
def test_complete_limit_one_source_traversal(hosted,drafts,draft_books,monkeypatch,tmp_path,which):
    # Each complete traversal is independently bounded by the unchanged timeout;
    # both use copies of the same once-built company/signing-key preimage.
    cmd=registry.get('payment selection query');planner=cmd.plan;check=pp.check
    if which=='old':
        planner,_=old_planner();check=frozen('src/bookflow/core/publication_payment.py').check
    calls=[]
    def counted(*a,**kw):calls.append(True);return planner(*a,**kw)
    monkeypatch.setattr(cmd,'plan',counted);monkeypatch.setattr(pp,'check',check)
    before=raw_snapshot(hosted.root,hosted.company_id);pages=[];args={'limit':1};seen=[]
    while True:
        out=hosted.ok('payment.selection.query',args,company=hosted.company_id)
        pages.append(out);seen.extend(r['id'] for r in out['items'])
        if not out['next_cursor']:break
        args={'limit':1,'cursor':out['next_cursor']}
    assert len(calls)==len(pages)>=201 and set(drafts)<=set(seen)
    assert len(seen)==len(set(seen))==pages[-1]['total_count']
    assert raw_snapshot(hosted.root,hosted.company_id)==before
    canonical=json.dumps(pages,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    (tmp_path/'complete-limit-one.json').write_text(canonical)
    reference=draft_books[0].parent/'limit-one-reference.json'
    if which=='old':reference.write_text(canonical)
    else:
        assert reference.exists(),'old planner coverage must execute first'
        assert canonical==reference.read_text()
