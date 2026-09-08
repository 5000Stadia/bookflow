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


def test_missing_operation_index_aborts_query_before_filter(root, client, sale, run_private, monkeypatch):
    """Real persisted fault from the diagnostic, not a permission-provider stub."""
    import sqlite3
    from pathlib import Path
    from bookflow.company import deposit_operation_pages, payment_authority
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_deposit_draft_financial import financial

    document = additional_document(client, sale, '10', cash='3')
    document['additional'] = [dict(document['additional'][0], memo=f'Cash row {n}') for n in range(3)]
    good, broken = [financial(run_private, dict(operation_key=f'denial-index-{n}', document=document)) for n in range(2)]
    def clean(s):
        result = q.query(s, m.QueryInput(page={'limit': 25}), binding=OSBinding.from_session(s))
        assert result.total_count == len(result.items) == 2
        assert result.totals.bank_total.minor_units == 5400
    observe(client, monkeypatch, clean)
    path = Path(client.company.show(company=COMPANY)['path']) / 'company.db'
    assert path.is_relative_to(root)
    db = sqlite3.connect(path)
    try:
        guards = db.execute("SELECT name,sql FROM sqlite_master WHERE name IN "
                            "('deposit_operation_targets_no_delete','deposit_operations_no_update')").fetchall()
        assert len(guards) == 2
        for name, _ in guards:
            db.execute(f'DROP TRIGGER "{name}"')
        assert db.execute('DELETE FROM deposit_operation_targets WHERE operation_id=?',
                          (broken.operation_id,)).rowcount == 1
        assert db.execute('UPDATE deposit_operations SET request_snapshot=? WHERE id=?',
                          ('{}', broken.operation_id)).rowcount == 1
        for _, sql in guards:
            db.execute(sql)
        db.commit()
    finally:
        db.close()

    def read(s):
        assert not s.company.writable
        binding = OSBinding.from_session(s)
        assert q.show(s, m.ShowInput(deposit=good.current.id), binding=binding).totals.bank_total.minor_units == 2700
        for inp in (m.QueryInput(), m.QueryInput(number=good.current.number), m.QueryInput(q='no match')):
            with pytest.raises(BookflowError) as error:
                q.query(s, inp, binding=binding)
            assert error.value.to_dict() == BookflowError('E_DEPOSIT_SOURCE_INVALID').to_dict()
        # Point and shared evidence owners retain their existing codes/details.
        with pytest.raises(BookflowError) as point:
            q.show(s, m.ShowInput(deposit=broken.current.id), binding=binding)
        assert point.value.to_dict() == BookflowError('E_RECORD_NOT_FOUND').to_dict()
        cohort = payment_authority._EventCohort(s.company, [])
        cohort._load('deposit_operation', [broken.operation_id])
        with pytest.raises(BookflowError) as shared:
            cohort._walk(('deposit_operation', broken.operation_id))
        assert shared.value.to_dict() == BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'}).to_dict()
        cursor = s.company.raw.execute('SELECT * FROM deposit_operations WHERE id=?', (broken.operation_id,))
        saved = dict(zip((v[0] for v in cursor.description), cursor.fetchone()))
        with pytest.raises(BookflowError) as owner:
            deposit_operation_pages.authorized_original(s, saved, binding)
        assert owner.value.code == 'E_INTERNAL'
    observe(client, monkeypatch, read)


def test_query_omits_only_positive_gate_denial(client, sale, run_private, monkeypatch):
    """Gate-level witness: legacy member roles cannot selectively deny these roots."""
    from bookflow.company import deposit_read_authority as authority, deposit_dependency_history as history
    from bookflow.company.deposit_dependencies import ProvenDepositDenial
    from bookflow.hub import access
    from tests.test_deposit_lifecycle import additional_document
    from tests.test_deposit_draft_financial import financial

    document = additional_document(client, sale)
    good, hidden = [financial(run_private, dict(operation_key=f'denial-gate-{n}', document=document)) for n in range(2)]
    def read(s):
        binding = OSBinding.from_session(s)
        # Exercise the real resource error producer and sanitizer. Forcing its
        # predicate is a unit gate test, not a configurable/public deny journey.
        with monkeypatch.context() as patch:
            patch.setattr(access, 'role_satisfies', lambda *args: False)
            with pytest.raises(ProvenDepositDenial) as denied:
                history._authorize_binding_graph(s, binding, [hidden.current.id])
        original = authority.admit
        def gate(s, ids, *, binding):
            if hidden.current.id in ids:
                raise denied.value
            return original(s, ids, binding=binding)
        with monkeypatch.context() as patch:
            patch.setattr(authority, 'admit', gate)
            result = q.query(s, m.QueryInput(), binding=binding)
            assert result.total_count == len(result.items) == 1
            assert result.items[0].current.id == good.current.id
            assert result.totals.bank_total.minor_units == result.effective_bank_total.minor_units == 1000
            assert result.next_cursor is None

        for failure in (BookflowError('E_PERMISSION'), BookflowError('E_PERMISSION', details={'reason': 'new_unknown_reason'}),
                        BookflowError('E_RECORD_NOT_FOUND'), BookflowError('E_COMPANY_NOT_FOUND'),
                        BookflowError('E_UNAUTHENTICATED'), BookflowError('E_SCHEMA_BEHIND'),
                        BookflowError('E_DB_BUSY'), OSError('owned I/O failure')):
            def unclassified(s, ids, *, binding):
                if hidden.current.id in ids:
                    raise failure
                return original(s, ids, binding=binding)
            with monkeypatch.context() as patch:
                patch.setattr(authority, 'admit', unclassified)
                with pytest.raises((BookflowError, OSError)) as caught:
                    q.query(s, m.QueryInput(q='no match'), binding=binding)
                if isinstance(failure, BookflowError) and failure.code in ('E_PERMISSION', 'E_RECORD_NOT_FOUND'):
                    assert caught.value.to_dict() == BookflowError('E_DEPOSIT_SOURCE_INVALID').to_dict()
                else:
                    assert caught.value is failure
    observe(client, monkeypatch, read)


def test_actual_role_and_activation_denial_provenance(root, client, monkeypatch):
    from tests.conftest import make_actor, as_user
    from bookflow.company import deposit_dependency_history as history, deposit_draft_validation as drafts
    from bookflow.company.deposit_dependencies import ProvenDepositDenial
    from bookflow.core import registry

    company = client.company.show(company=COMPANY)['company_id']
    make_actor(root, 'deposit-denial-reader', company_role=(company, 'readonly'))
    reader = as_user(root, 'deposit-denial-reader')
    def read(s):
        binding = OSBinding.from_session(s)
        # Real readonly role: read admission passes, standard write requirement fails.
        drafts.admit(s, binding=binding)
        for owner in (lambda: history._authorize_binding_graph(s, binding, (), write=True),
                      lambda: drafts.admit(s, binding=binding, write=True)):
            with pytest.raises(ProvenDepositDenial) as error:
                owner()
            ordinary = BookflowError('E_PERMISSION')
            assert error.value.to_dict() == ordinary.to_dict()
            assert vars(error.value) == vars(ordinary)
        # Isolated activation-set change exercises the real default-deny owner;
        # no capability is activated and this is not a public permission journey.
        with monkeypatch.context() as patch:
            patch.setattr(registry, 'EXPLICIT_GRANT_ONLY_CAPABILITIES',
                          registry.EXPLICIT_GRANT_ONLY_CAPABILITIES | {'ledger.read'})
            with pytest.raises(ProvenDepositDenial) as error:
                history._authorize_binding_graph(s, binding, ())
            assert error.value.to_dict() == BookflowError('E_PERMISSION').to_dict()
    observe(reader, monkeypatch, read)


@pytest.mark.parametrize('membership', ['absent', 'revoked'])
def test_actual_missing_membership_fails_whole_query(root, client, monkeypatch, membership):
    from tests.conftest import make_actor, as_user
    from tests.test_row7_credentials import writer
    from bookflow.hub import schema as hub
    from bookflow.core import clock
    company = client.company.show(company=COMPANY)['company_id']
    # Hub admin can enter company show, but private deposit authentication still
    # requires actual applicable membership, including before an empty query.
    actor = make_actor(root, 'deposit-missing-member', hub_admin=True,
                       company_role=(company, 'readonly') if membership == 'revoked' else None)
    if membership == 'revoked':
        with writer(root) as db:
            db.conn.execute(hub.memberships.update().where(hub.memberships.c.user_id == actor)
                            .values(revoked_at=clock.now_iso()))
    def read(s):
        with pytest.raises(BookflowError) as error:
            q.query(s, m.QueryInput(), binding=OSBinding.from_session(s))
        assert error.value.to_dict() == BookflowError('E_COMPANY_NOT_FOUND').to_dict()
    observe(as_user(root, 'deposit-missing-member'), monkeypatch, read)


@pytest.mark.parametrize('stage', ['authentication', 'group', 'final', 'draft'])
def test_positive_tag_survives_candidate_admission_entrances(client, cash, run_private, monkeypatch, stage):
    """Gate-outcome injection checks propagation, not real selective permissions."""
    from bookflow.company import deposit_read_authority as authority
    from bookflow.company.deposit_dependencies import ProvenDepositDenial
    posted, draft = make_posted(client, cash, run_private)
    def read(s):
        binding = OSBinding.from_session(s)
        marker = ProvenDepositDenial()
        original_graph = authority.h._authorize_binding_graph
        original_draft = authority.draft_admit
        def graph(s, binding, ids, events=(), *, write=False):
            if ((stage == 'authentication' and not ids) or (stage == 'group' and ids and not events)
                    or (stage == 'final' and events)):
                raise marker
            return original_graph(s, binding, ids, events, write=write)
        def draft_gate(s, **kwargs):
            if stage == 'draft' and kwargs.get('draft') == draft.id:
                raise marker
            return original_draft(s, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(authority.h, '_authorize_binding_graph', graph)
            patch.setattr(authority, 'draft_admit', draft_gate)
            with pytest.raises(ProvenDepositDenial) as error:
                authority.admit(s, [posted.current.id], binding=binding)
            assert error.value is marker
    observe(client, monkeypatch, read)


@pytest.mark.parametrize('write', [False, True])
def test_positive_resource_shape_is_closed(write):
    from bookflow.company.deposit_dependency_history import _proven_resource_denial
    capability, role = ('ledger.post', 'standard') if write else ('ledger.read', 'member')
    for resource in (capability, 'customer-work'):
        details = dict(capability=resource, required_role=role, role='readonly')
        assert _proven_resource_denial(BookflowError('E_PERMISSION', details=details), write=write)
        for change in ({'capability': 'unknown'}, {'required_role': 'owner'}, {'role': 'unknown'}, {'extra': True}):
            assert not _proven_resource_denial(BookflowError('E_PERMISSION', details=dict(details, **change)), write=write)
    for details in ({}, {'reason': 'unresolved_payment_evidence'}, {'reason': 'new_reason'},
                    {'reason': 'capability_not_activated', 'extra': True}):
        assert not _proven_resource_denial(BookflowError('E_PERMISSION', details=details), write=write)
    assert _proven_resource_denial(BookflowError('E_PERMISSION', details={'reason': 'capability_not_activated'}), write=write)
    assert not _proven_resource_denial(BookflowError('E_RECORD_NOT_FOUND', details={'reason': 'capability_not_activated'}), write=write)
