"""Review regressions over ordinary producer worlds and real owner Plans."""
import copy
from dataclasses import replace
import pytest
from bookflow import BookflowError
from bookflow.core import registry
from bookflow.core.context import Context,Interface
from bookflow.core.ids import new_id
from bookflow.company import reconciliation_preparation as p,reconciliation_proposals as fees,reconciliation_drafts as d,reconciliation_queries as q,reconciliation_reports as reports,reconciliation_attempts as attempts,reconciliation_operations as operations,reconciliation_amendments as am,reconciliation_commands_models as m,reconciliation_adapters as adapters
from bookflow.company.reconciliation_storage_validation import digest
from tests.test_reconciliation_successor_models import certificate_world,draft,select
from tests.test_deposit_lifecycle import driver
from tests.test_reconciliation_insertion_targets import attempt,owned


def fee_world(s,bank,equity):
    dr=draft(bank,ending=850,cutoff='2026-02-28',state=s.rows['accounts'][0])
    body=m.ProposalInput(format=1,date='2026-02-10',amount_minor_units=100,currency='USD',offset_account_id=equity,class_id=None,memo='Saved fee',number=None,reason='fee')
    inp=m.ProposalSet(operation_key='save',draft=dr.id,expected_version=dr.version,role='charge',input=body)
    dr,proposal=d.set_proposal(s,dr,inp,identity=new_id(),proposal_revision_id=new_id(),draft_revision_id=new_id())
    return dr,proposal


def test_preview_exact_saved_set_before_arithmetic_omission_extra_substitution(driver,certificate_world,monkeypatch):
    s,bank,equity,_=certificate_world;dr,proposal=fee_world(s,bank,equity)
    before=driver.dump()
    with driver.session() as session:
        ctx=Context.new(Interface.python,'proposal binding',reason='saved fee')
        original=registry.get('journal post').plan(d.journal_input(s,dr,proposal),ctx,session)
        good=fees.preview(session,ctx,s,dr,(proposal,),(original,))
        assert good.output.original_difference==-150 and good.output.final.difference==-50
        assert good.output.final.selected_sum==-100 and good.plans==(original,)
        extra=proposal.model_copy(update={'id':new_id(),'revision_id':new_id()})
        extra_plan=registry.get('journal post').plan(d.journal_input(s,dr,extra),ctx,session)
        substitution=proposal.model_copy(update={'revision_id':new_id()})
        # The missing/set check must precede even the base statement arithmetic.
        def arithmetic(*args,**kwargs):raise AssertionError('arithmetic before proposal binding')
        monkeypatch.setattr(fees,'statement',arithmetic)
        for proposals,plans in (((),()),((proposal,extra),(original,extra_plan)),((substitution,),(original,))):
            with pytest.raises(BookflowError) as rejected:fees.preview(session,ctx,s,dr,proposals,plans)
            assert rejected.value.code=='E_VALIDATION'
            assert rejected.value.details=={'reason':'E_RECONCILIATION_MANIFEST'}
    assert driver.dump()==before


def receipt(s,doc,bank):
    values=dict(operation_key='original',draft=s.rows['drafts'][0]['id'],expected_version=1,expected_facts_fingerprint='1'*64,dependency_guard='old')
    request=operations.Request(command='reconcile finish',company_id=new_id(),original_input=values,provided_fields=tuple(values),context={},context_provided_fields=(),canonical_input=values,resolved_captures={})
    targets=(m.OperationTarget(kind='transactions',id=doc['id']),m.OperationTarget(kind='accounts',id=bank))
    return operations.Receipt(operation_id=new_id(),operation_key='original',request=request,original_effect={'selected_sum':1000},targets=targets,items={'request':(),'effects':(),'generated':(),'targets':tuple(v.model_dump(mode='json') for v in targets)})


def readers(s,dr,cert,stage,record,movement,group_hash,authority):
    # Literal independently enumerated entry inventory, including every item
    # reader and the added progress inspection. No reflection-based tautology.
    return {
        'report':lambda:reports.project(s,cert['id'],authority_transactions=authority),
        'candidates':lambda:q.candidates(s,dr,m.CandidateFilter(),authority_transactions=authority),
        'components':lambda:q.component_items(s,movement,group_hash,authority_transactions=authority),
        'certificates':lambda:q.certificates(s,m.CertificateQuery(account=dr.account_id),authority_transactions=authority),
        'certificate_items':lambda:q.certificate_items(s,cert['id'],authority_transactions=authority),
        'draft_history':lambda:q.draft_history(s,dr.id,authority_transactions=authority),
        'draft_show':lambda:d.load(s,dr.id,authority_transactions=authority),
        'attempt_items':lambda:attempts.items(s,stage,authority_transactions=authority),
        'missing_ranges':lambda:attempts.missing_ranges(s,stage,authority_transactions=authority),
        'operation_items':lambda:operations.items(s,record,'targets',authority_transactions=authority),
        'operation_recovery':lambda:operations.recover(s,record,record.request,authority_transactions=authority,current_targets=(),current_state={}),
    }


def test_every_reader_requires_actual_snapshot_authority_before_content(driver,certificate_world):
    s,bank,_,doc=certificate_world
    dr=draft(bank,cutoff='2026-02-28',state=s.rows['accounts'][0]);s=owned(s,(dr,))
    group=next(iter(p.groups(s.current.values()).values()));movement=m.MovementKey.model_validate_json(group[0]['movement_snapshot']);group_hash=p.group_fingerprint(group)
    stage=attempt(s,dr,());record=receipt(s,doc,bank);cert=s.rows['certificates'][0]
    with driver.session() as session:
        authorized=adapters.authority(session,(doc['id'],))
        unrelated=adapters.authority(session,())
    assert authorized==s.authority_transactions and unrelated==()
    result=readers(s,dr,cert,stage,record,movement,group_hash,authorized)
    assert len(result)==11
    for call in result.values():assert call() is not None
    # Malformed captured content and impossible arithmetic must stay unseen
    # when a real authority result does not cover this retained world.
    poisoned=copy.deepcopy(s.rows)
    poisoned['certificates'][0]['beginning_balance']='hidden malformed content'
    poisoned['effect_versions'][0]['display_snapshot']='not JSON'
    hidden=replace(s,rows=poisoned)
    for name,call in readers(hidden,dr,cert,stage,record,movement,group_hash,unrelated).items():
        with pytest.raises(BookflowError) as denied:call()
        assert denied.value.code=='E_PERMISSION',name
        assert denied.value.details=={},name
    with pytest.raises(TypeError):attempts.items(s,stage)
    with pytest.raises(TypeError):operations.items(s,record,'targets')


def test_support_gate_precedes_query_filters_and_report_totals(certificate_world):
    s,bank,_,doc=certificate_world;dr=draft(bank,cutoff='2026-02-28',state=s.rows['accounts'][0]);s=owned(s,(dr,))
    # Pure unsupported account snapshot seam, never a database retype/corruption.
    source=copy.deepcopy(s.source);source.accounts[bank]['currency']='EUR';unsupported=replace(s,source=source)
    group=next(iter(p.groups(s.current.values()).values()));movement=m.MovementKey.model_validate_json(group[0]['movement_snapshot'])
    for name,call in readers(unsupported,dr,s.rows['certificates'][0],attempt(s,dr,()),receipt(s,doc,bank),movement,p.group_fingerprint(group),s.authority_transactions).items():
        with pytest.raises(BookflowError) as error:call()
        assert error.value.details=={'reason':'E_RECONCILIATION_UNSUPPORTED'},name
    # Even an empty result filter must not bypass admission.
    with pytest.raises(BookflowError) as error:q.certificates(unsupported,m.CertificateQuery(account=new_id()),authority_transactions=s.authority_transactions)
    assert error.value.details=={'reason':'E_RECONCILIATION_UNSUPPORTED'}


def test_adapter_errors_are_typed_and_operational_failures_unchanged(certificate_world,monkeypatch):
    s,bank,_,_=certificate_world
    for code in ('E_VERSION_CONFLICT','E_QUERY_STALE','E_PREVIEW_STALE','E_PERMISSION'):
        with pytest.raises(BookflowError) as error:p.require(False,code)
        assert error.value.to_dict()['code']==code and error.value.details=={}
    original=adapters.enumerate_graph
    for failure,reason in ((adapters.Unsupported('hidden unsupported detail'),'E_RECONCILIATION_UNSUPPORTED'),(adapters.Corrupt('hidden corrupt detail'),'E_RECONCILIATION_SOURCE_INVALID'),(ValueError('bad owned format'),'E_RECONCILIATION_SOURCE_INVALID')):
        def fail(*args):raise failure
        monkeypatch.setattr(adapters,'enumerate_graph',fail)
        for call in (lambda:p.account_population(s,bank,'2026-12-31'),lambda:am.derive(s,(),before_source=s.source,authority_transactions=s.authority_transactions)):
            with pytest.raises(BookflowError) as error:call()
            assert error.value.details=={'reason':reason}
            assert 'hidden' not in str(error.value.to_dict())
    operational=OSError('owned IO failed')
    def fail(*args):raise operational
    monkeypatch.setattr(adapters,'enumerate_graph',fail)
    with pytest.raises(OSError) as error:p.account_population(s,bank,'2026-12-31')
    assert error.value is operational
    monkeypatch.setattr(adapters,'enumerate_graph',original)


def test_seal_explicit_other_barrier_and_missing_suffix_403(certificate_world):
    s,bank,_,_=certificate_world;dr=draft(bank,cutoff='2026-02-28',state=s.rows['accounts'][0]);s=owned(s,(dr,))
    # A complete N-valid staged aggregate, not a fabricated barrier-only row.
    from tests.test_reconciliation_storage_validation import staged,captured
    rows=staged(captured(s.source),s.source,bank,0)
    durable=p.snapshot(rows,source=s.source,captured_graphs={},referenced_rows=s.referenced_rows,authority_transactions=s.authority_transactions)
    saved=d.load(durable,rows['drafts'][0]['id'],authority_transactions=s.authority_transactions)
    header=rows['attempts'][0]
    empty=attempts.Attempt(**{key:header[key] for key in attempts.Attempt.model_fields if key!='chunks'})
    other=empty.model_copy(update={'id':new_id()})
    with pytest.raises(BookflowError) as error:attempts.seal(durable,saved,other,expected_version=other.version)
    assert error.value.details=={'reason':'E_RECONCILIATION_ATTEMPT_STATE'}
    assert attempts.seal(durable,saved,empty,expected_version=empty.version).state=='sealed'
    from datetime import date,timedelta
    entries=tuple(m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=bank,kind='insert',date=(date(2027,1,1)+timedelta(days=i)).isoformat())) for i in range(403))
    staged=attempts.begin(s,dr,m.AttemptBegin(operation_key='begin',draft=dr.id,expected_version=1,base_revision_id=dr.current_revision_id,attempt_generation='403',declared_count=403,intent_hash=digest(attempts.payload(entries))),identity=new_id())
    observed=[]
    for index,start in enumerate((0,200,400)):
        progress=attempts.missing_ranges(s,staged,authority_transactions=s.authority_transactions)
        observed.append((progress.received_count,progress.missing[0].first_ordinal,progress.missing[0].count,progress.next_chunk_index))
        staged,_=attempts.upload(staged,m.AttemptUpload(operation_key='upload',attempt=staged.id,chunk_index=index,items=entries[start:start+200]))
    assert observed==[(0,0,403,0),(200,200,203,1),(400,400,3,2)]
    done=attempts.missing_ranges(s,staged,authority_transactions=s.authority_transactions)
    assert done.received_count==403 and done.missing==() and done.next_chunk_index==3
    assert attempts.items(s,staged,authority_transactions=s.authority_transactions,limit=200,offset=400).count==403
    assert len(attempts.items(s,staged,authority_transactions=s.authority_transactions,limit=200,offset=400).items)==3
    broken=staged.model_copy(update={'chunks':staged.chunks[1:]})
    with pytest.raises(BookflowError) as error:attempts.missing_ranges(s,broken,authority_transactions=s.authority_transactions)
    assert error.value.details=={'reason':'E_RECONCILIATION_SOURCE_INVALID'}


def test_proposal_removal_and_cancel_terminality_derived_from_saved_revisions(certificate_world):
    s,bank,equity,_=certificate_world;dr,proposal=fee_world(s,bank,equity)
    saved=dr.model_dump_json();original=proposal.model_dump_json()
    removed=d.remove_proposal(s,dr,m.ProposalRemove(operation_key='remove',draft=dr.id,expected_version=dr.version,proposal_id=proposal.id,expected_proposal_version=1),proposal,revision_id=new_id())
    assert removed.proposal_revision_ids==() and dr.proposal_revision_ids==(proposal.revision_id,)
    assert dr.model_dump_json()==saved and proposal.model_dump_json()==original
    canceled=d.lifecycle(s,dr,'cancel',expected_version=dr.version,revision_id=new_id(),operation_id=new_id())
    assert canceled.state=='canceled' and canceled.proposal_revision_ids==(proposal.revision_id,)
    inp=m.ProposalSet(operation_key='edit',draft=dr.id,expected_version=canceled.version,proposal_id=proposal.id,expected_proposal_version=1,role='charge',input=proposal.input)
    with pytest.raises(BookflowError) as error:d.proposal(s,canceled,inp,identity=new_id(),revision_id=new_id(),previous=proposal)
    assert error.value.details=={'reason':'E_RECONCILIATION_DRAFT_STATE'}


def test_real_graph_corruption_and_returned_unsupported_prospective_are_typed(driver,certificate_world,monkeypatch):
    s,bank,equity,_=certificate_world
    graph=copy.deepcopy(s.source)
    line=next(v for v in graph.rows['posting_lines'] if v['account_id']==bank)
    line['debit_minor_units']+=1
    broken=replace(s,source=graph)
    with pytest.raises(BookflowError) as corrupt:p.account_population(broken,bank,'2026-12-31')
    assert corrupt.value.code=='E_INTERNAL' and corrupt.value.details=={'reason':'E_RECONCILIATION_SOURCE_INVALID'}
    dr,proposal=fee_world(s,bank,equity)
    with driver.session() as session:
        ctx=Context.new(Interface.python,'known adapter outcome',reason='fee')
        plan=registry.get('journal post').plan(d.journal_input(s,dr,proposal),ctx,session)
        unsupported=adapters.UnsupportedPopulation(kind='population_unsupported',account_id=bank,account_currency='USD',home_currency='USD',reason='known unsupported producer')
        monkeypatch.setattr(adapters,'prepare_prospective',lambda *args:adapters.PreparedProjection(unsupported,plan))
        with pytest.raises(BookflowError) as error:fees.preview(session,ctx,s,dr,(proposal,),(plan,))
        assert error.value.details=={'reason':'E_RECONCILIATION_UNSUPPORTED'}
        assert 'known unsupported producer' not in str(error.value.to_dict())
