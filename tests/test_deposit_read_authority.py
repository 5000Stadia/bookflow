"""Current authenticated membership, input/cursor and stored evidence boundaries."""
import copy,json
import pytest
from pydantic import ValidationError
from bookflow.company import deposit_queries as q, deposit_read_models as m, deposit_read_facts as facts, deposit_read_validation as validation
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from tests.test_deposit_draft_financial import run_private,make_posted
from tests.test_deposit_drafts import cash
from tests.test_service_sales_lifecycle import sale,COMPANY


@pytest.mark.parametrize('payload',[{'page':{'limit':True}},{'page':{'limit':201}},{'unknown':1},{'status':'deleted','include_deleted':False},{'date_from':'2026-06-05','date_to':'2026-06-04'}])
def test_strict_query_input(payload):
    with pytest.raises(ValidationError):m.QueryInput.model_validate(payload)


def test_readonly_and_cursor_binding(client,cash,run_private):
    posted,draft=make_posted(client,cash,run_private)
    from bookflow.company import deposit_read_pages as pages
    def read(s,ctx):
        b=OSBinding.from_session(s);before=tuple(s.company.raw.iterdump())
        fp=pages.fingerprint(s,b,'items',{'owned':posted.current.id})
        token=pages.encode(s,b,'items',fp,[1,1])
        assert set(pages.decode(s,b,'items',token))=={'v','purpose','fp','position'}
        for purpose,t in [('query',token),('items',token[:-2]+'XX')]:
            with pytest.raises(BookflowError) as err:pages.decode(s,b,purpose,t)
            assert err.value.code=='E_VALIDATION'
        s.company.raw.execute('SAVEPOINT owned_key_fault')
        try:
            s.company.raw.execute('DELETE FROM report_cursor_keys WHERE key_id=1')
            with pytest.raises(BookflowError) as err:pages.fingerprint(s,b,'items',{})
            assert err.value.code=='E_INTERNAL'
        finally:s.company.raw.execute('ROLLBACK TO owned_key_fault');s.company.raw.execute('RELEASE owned_key_fault')
        assert tuple(s.company.raw.iterdump())==before
    run_private(read)


@pytest.mark.parametrize('fault',['source_owner','omitted_component','balanced_sign','issuer','membership_projection','nested_field'])
def test_stored_read_corruption(client,cash,run_private,fault):
    posted,draft=make_posted(client,cash,run_private)
    def read(s,ctx):
        b=OSBinding.from_session(s);data=facts.load_complete(s,[posted.current.id],binding=b)[0]
        graph=copy.deepcopy(data.graph);rev=graph['transaction_revisions'][0]
        source_graphs={k:v[0] for k,v in data.sources.items()}
        from bookflow.company import deposit_dependency_history as h
        if fault=='source_owner':graph['deposit_components'][0]['source_transaction_id']='foreign'
        elif fault=='omitted_component':graph['deposit_components'].pop()
        elif fault=='balanced_sign':
            for row in graph['posting_lines']:row['debit_minor_units'],row['credit_minor_units']=row['credit_minor_units'],row['debit_minor_units']
        elif fault=='issuer':
            value=json.loads(rev['issuer_snapshot']);value['invented']='bad';rev['issuer_snapshot']=json.dumps(value)
        elif fault=='membership_projection':graph['deposit_current_memberships']=[]
        else:
            profile=graph['deposit_profiles'][0];value=json.loads(profile['facts_snapshot']);value['intent']['unknown']=1;profile['facts_snapshot']=json.dumps(value)
        with pytest.raises((BookflowError,ValidationError)):
            if fault=='membership_projection':validation.lifecycle(graph,data.header)
            else:validation.revision(graph,rev,source_graphs,h.History(s))
    run_private(read)


from tests.test_deposit_dependency_binding import bound_people,observe,_credential

@pytest.mark.parametrize('loss',['actor','principal','token'])
def test_actual_binding_readonly_and_revocation(root,client,cash,run_private,monkeypatch,bound_people,loss):
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as hub
    from bookflow.core import clock
    posted,_=make_posted(client,cash,run_private)
    people=bound_people
    # The actual role owner permits member reads without ledger.post.
    with writer(root) as db:
        db.conn.execute(hub.memberships.update().where(hub.memberships.c.user_id.in_([people['agent'],people['first']])).values(role='readonly'))
    credential=_credential(client,people['agent'],people['first'])
    def read(s):
        before=(tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))
        result=q.show(s,m.ShowInput(deposit=posted.current.id),binding=credential)
        assert result.totals.bank_total.minor_units==6000
        assert q.query(s,m.QueryInput(),binding=credential).total_count==1
        assert before==(tuple(s.company.raw.iterdump()),tuple(s.hub.raw.iterdump()))
        return result
    observe(people['bot'],monkeypatch,read,people['company'])
    with writer(root) as db:
        if loss=='token':db.conn.execute(hub.api_tokens.update().where(hub.api_tokens.c.id==credential.token_id).values(revoked_at=clock.now_iso()))
        else:db.conn.execute(hub.memberships.update().where(hub.memberships.c.user_id==people['agent' if loss=='actor' else 'first']).values(revoked_at=clock.now_iso()))
    # Dispatch the witness as the unchanged owner; the reader MUST validate the
    # supplied real credential, not borrow the dispatch Session actor's grants.
    def denied(s,ctx):
        for function,inp in ((q.show,m.ShowInput(deposit=posted.current.id)),(q.query,m.QueryInput())):
            with pytest.raises(BookflowError) as err:function(s,inp,binding=credential)
            assert err.value.code in ('E_RECORD_NOT_FOUND','E_COMPANY_NOT_FOUND','E_UNAUTHENTICATED')
            assert posted.current.id not in str(err.value.details)
    run_private(denied)


def test_entitled_corruption_aborts_whole_query_before_filter(client,sale,run_private,monkeypatch):
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_deposit_draft_financial import financial
    doc=additional_document(client,sale)
    good=financial(run_private,dict(operation_key='read-good',document=doc))
    broken=financial(run_private,dict(operation_key='read-bad',document=doc))
    original=validation.revision;seen=[]
    def damaged(graph,revision,sources,reader):
        seen.append(revision['transaction_id'])
        if revision['transaction_id']==broken.current.id:
            graph=copy.deepcopy(graph);graph['deposit_components'].pop()
        return original(graph,revision,sources,reader)
    # Fault injection is solely at the raw in-memory graph boundary; the real
    # Session/admission/decoder/aggregate query and independent equations run.
    monkeypatch.setattr(validation,'revision',damaged)
    def read(s,ctx):
        b=OSBinding.from_session(s)
        assert q.show(s,m.ShowInput(deposit=good.current.id),binding=b).totals.bank_total.minor_units==1000
        for inp in (m.QueryInput(),m.QueryInput(number=good.current.number),m.QueryInput(q='no matching documents')):
            with pytest.raises(BookflowError) as err:q.query(s,inp,binding=b)
            assert err.value.code=='E_DEPOSIT_SOURCE_INVALID' and not err.value.details
        assert broken.current.id in seen
    run_private(read)


def test_io_failure_is_never_absence(client,cash,run_private,monkeypatch):
    posted,_=make_posted(client,cash,run_private)
    def fail(*args,**kwargs):raise OSError('owned injected I/O failure')
    monkeypatch.setattr(facts.deposit_sources,'graph_many',fail)
    def read(s,ctx):
        with pytest.raises(OSError,match='owned injected'):
            q.show(s,m.ShowInput(deposit=posted.current.id),binding=OSBinding.from_session(s))
    run_private(read)


@pytest.mark.parametrize('stage',['draft','operation','consumption'])
def test_inner_owner_denial_is_not_corrupt_books(client,cash,run_private,monkeypatch,stage):
    posted,_=make_posted(client,cash,run_private)
    owner,name={'draft':(facts.deposit_drafts,'load'),
                'operation':(facts.opages,'authorized_original'),
                'consumption':(facts.deposit_draft_consumption,'current')}[stage]
    calls=[]
    def denied(*args,**kwargs):
        # Inject an owning reader's denied outcome after real outer admission;
        # this tests error conversion, not a substitute permission evaluator.
        calls.append(stage)
        raise BookflowError('E_PERMISSION')
    monkeypatch.setattr(owner,name,denied)
    def read(s,ctx):
        before=tuple(s.company.raw.iterdump())
        with pytest.raises(BookflowError) as caught:
            q.show(s,m.ShowInput(deposit=posted.current.id),binding=OSBinding.from_session(s))
        assert caught.value.code=='E_PERMISSION' and not caught.value.details
        assert tuple(s.company.raw.iterdump())==before
    run_private(read)
    assert calls==[stage]


def test_discovered_groups_are_admitted_before_expansion(client,cash,run_private,monkeypatch):
    from bookflow.company import deposit_read_authority as authority,schema as c
    posted,_=make_posted(client,cash,run_private)
    admitted=set();expansions=[]
    original_admit=authority.h._authorize_binding_graph;original_select=authority.select
    def admit(s,binding,identities,*args,**kwargs):
        result=original_admit(s,binding,identities,*args,**kwargs)
        admitted.update(identities)
        return result
    def select(s,table,column,identities):
        values=tuple(identities)
        if table is c.deposit_memberships:
            assert set(values)<=admitted
            expansions.append(set(values))
        return original_select(s,table,column,values)
    monkeypatch.setattr(authority.h,'_authorize_binding_graph',admit)
    monkeypatch.setattr(authority,'select',select)
    run_private(lambda s,ctx:authority.admit(s,[posted.current.id],binding=OSBinding.from_session(s)))
    assert {posted.current.id} in expansions and {cash['source']} in expansions
