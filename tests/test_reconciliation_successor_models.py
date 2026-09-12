"""Private successor arithmetic and transitions over ordinary producer worlds."""
import copy
import json
from datetime import date,timedelta
import pytest
from pydantic import ValidationError
from bookflow.core.ids import new_id
from bookflow.company import reconciliation_commands_models as m
from bookflow.company import reconciliation_preparation as p,reconciliation_drafts as d,reconciliation_queries as q,reconciliation_attempts as a,reconciliation_reports as r,reconciliation_amendments as am,reconciliation_operations as op
from bookflow.company.reconciliation_storage_validation import Header,Evidence,canonical,digest,population_fingerprint
from tests.test_reconciliation_storage_validation import captured,aggregate,references,blank,owned_storage  # noqa: F401  (autouse)
from tests.test_reconciliation_adapters import account,journal,pair,run
from tests.test_deposit_lifecycle import driver
from tests.test_service_sales_lifecycle import sale,COMPANY
from bookflow.company import reconciliation_adapters as adapters


def context(driver,ids,rows=None,captures=None):
    with driver.session() as session:
        authorized=adapters.authority(session,ids)
        g=adapters.graph(session,ids);refs=references(session)
    values=captured(g) if rows is None else rows
    return p.snapshot(values,source=g,captured_graphs=captures or {},referenced_rows=refs,authority_transactions=authorized)


def draft(bank, *, kind='statement',ending=1000,cutoff='2026-01-31',selections=(),state=None):
    return m.Draft(id=new_id(),account_id=bank,kind=kind,version=1,current_revision_id=new_id(),state='open',
        header=Header(format=1,opening_date=cutoff if kind=='opening' else None,statement_date=None if kind=='opening' else cutoff,entered_balance=ending,evidence=Evidence(format=1,statement_reference=None,entered_text='ordinary retained graph'),preferences=d.DEFAULT_PREFERENCES),
        base_chain_version=state['version'] if state else 0,base_opening_id=state['opening_id'] if state else None,base_head_id=state['head_certificate_id'] if state else None,selections=selections)


def select(s,keys=None,action='mark'):
    return tuple(m.Selection(key_id=k,version_id=v['id'],action=action) for k,v in sorted(s.current.items()) if v['active'] and (keys is None or k in keys))


def extend(old,graph):
    """Test-only source append preserving existing N identities, never SQL writes."""
    fresh=captured(graph);mapping={}
    for v in fresh['keys']:
        oldkey=next((k for k in old['keys'] if all(k[n]==v[n] for n in ('producer','transaction_id','role','commercial_line_id','deposit_key_id'))),None)
        if oldkey:mapping[v['id']]=oldkey['id']
    for v in fresh['effect_versions']:
        prev=next((k for k in old['effect_versions'] if k['key_id']==mapping.get(v['key_id'],v['key_id']) and k['source_version']==v['source_version']),None)
        if prev:mapping[v['id']]=prev['id']
    result=copy.deepcopy(old)
    for name in ('keys','effect_versions','commercial_versions','deposit_versions','effect_legs','effect_sources','effect_heads'):
        result[name]=[{k:mapping.get(v,v) if isinstance(v,str) else v for k,v in row.items()} for row in fresh[name]]
    return result


@pytest.fixture
def certificate_world(client,driver):
    bank=account(client,'ii certificate bank');equity=account(client,'ii equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as session:
        g=adapters.graph(session,[doc['id']]);refs=references(session);authority=adapters.authority(session,[doc['id']])
    rows=aggregate(captured(g),g,bank)
    captures={v['id']:g for name in ('openings','certificates') for v in rows[name]}
    return p.snapshot(rows,source=g,captured_graphs=captures,referenced_rows=refs,authority_transactions=authority),bank,equity,doc


def test_contract_inventory_strict_shapes_and_storage_payloads():
    from bookflow.company.reconciliation_schema import COMMANDS
    assert set(COMMANDS)<=m.INPUTS.keys() and len(m.INPUTS)==43
    for model in set(m.INPUTS.values()):assert model.model_json_schema()['additionalProperties'] is False
    with pytest.raises(ValidationError):m.Page(limit=True)
    with pytest.raises(ValidationError):m.Page(limit=201)
    with pytest.raises(ValidationError):m.OpeningStart(operation_key='o',account=new_id(),opening_date='20260101',entered_balance='0.00',evidence=Evidence(format=1,statement_reference=None,entered_text=None))
    with pytest.raises(ValidationError):m.SeedTarget(account_id=new_id(),kind='opening',opening_id=new_id(),date='2026-01-01')
    with pytest.raises(ValidationError):m.MemberTarget(draft_id=new_id(),account_id=new_id(),key_id=new_id(),saved_version_id=new_id(),current_version_id=new_id(),action='mark')
    with pytest.raises(ValidationError):m.Start(operation_key='x',account=new_id(),statement_date='2026-01-01',ending_balance='0.00')
    for cls in (m.TransactionEvidence,m.AttachmentEvidence):
        with pytest.raises(ValidationError):cls(kind='opening_attachment',transaction_id=new_id())
    with pytest.raises(ValidationError):m.Totals(positive_count=0,positive_sum=2**63,negative_count=0,negative_sum=0,selected_sum=0,beginning_balance=0,ending_balance=0,cleared_balance=0,difference=0,decimal_units={})


def test_opening_complete_partition_gl_card_and_unbounded_intermediates(client,driver):
    bank=account(client,'ii opening');equity=account(client,'ii opening equity','equity')
    first=journal(client,pair(bank,equity,'1000'));second=journal(client,pair(equity,bank,'100'))
    s=context(driver,[first['id'],second['id']]);values=sorted(s.current.values(),key=lambda v:v['signed_debit'])
    selections=tuple(m.Selection(key_id=v['key_id'],version_id=v['id'],action='covered' if v['signed_debit']>0 else 'outstanding') for v in values)
    opening=draft(bank,kind='opening',ending=100000,selections=selections)
    before=driver.dump();assert p.opening(s,opening).selected_sum==100000
    assert p.account_population(s,bank,'2026-01-31')[1]==90000
    with pytest.raises(p.ReconciliationError,match='OPENING_UNPROVEN'):p.opening(s,opening.model_copy(update={'selections':selections[:1]}))
    with pytest.raises(p.ReconciliationError,match='OPENING_UNPROVEN'):p.opening(s,opening.model_copy(update={'header':opening.header.model_copy(update={'entered_balance':90001})}))
    assert driver.dump()==before
    card=account(client,'ii card','credit_card');j=journal(client,pair(equity,card,'10'))
    cs=context(driver,[j['id']]);co=draft(card,kind='opening',ending=1000,selections=select(cs,action='covered'))
    assert p.opening(cs,co).selected_sum==1000
    # Intermediates exceed signed64, final exact values do not. Totals sum in
    # unbounded Python before persisted/report range checks.
    empty=p.totals(2**63-1,2**63-1,[]);assert empty.difference==0 and empty.decimal_units['ending_balance']==str(2**63-1)


def test_403_components_whole_movements_paging_mark_all_and_chunks(client,sale,driver):
    from tests.test_payment_receipts import method
    bank=account(client,'ii 403 receipt bank');ids=[];payment_method=method(client)
    for count in (199,199,5):
        doc=run(client,'sales-receipt post',dict(customer=sale['customer'],date='2026-01-10',deposit_to=bank,payment_method=payment_method,lines=[dict(item=sale['item'],unit_price='1') for _ in range(count)]));ids.append(doc['id'])
    s=context(driver,ids);assert len(s.current)==403
    dr=draft(bank,ending=40300)
    pages=[q.candidates(s,dr,m.CandidateFilter(),limit=1,offset=i,authority_transactions=s.authority_transactions) for i in range(3)]
    assert [v.items[0].component_count for v in pages]==[199,199,5] or sorted(v.items[0].component_count for v in pages)==[5,199,199]
    assert all(v.count==3 and v.component_count==403 and v.positive_sum==40300 for v in pages)
    full=q.mark_all(s,dr,m.MarkAll(operation_key='all',draft=dr.id,expected_version=1,filters=m.CandidateFilter(),query_fingerprint=pages[0].fingerprint,action='mark'),revision_id=new_id())
    assert len(full.selections)==403 and len(p.whole_selection(s,full))==403
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):p.whole_selection(s,full.model_copy(update={'selections':full.selections[:-1]}))
    entries=tuple(m.MemberItem(kind='member',payload=m.MemberTarget(draft_id=dr.id,account_id=bank,key_id=v.key_id,saved_version_id=v.version_id,action='mark')) for v in full.selections)
    attempt=a.begin(s,dr,m.AttemptBegin(operation_key='begin',draft=dr.id,expected_version=1,base_revision_id=dr.current_revision_id,attempt_generation='generation1',declared_count=403,intent_hash=digest(a.payload(entries))),identity=new_id())
    for index,offset in enumerate(range(0,403,200)):
        inp=m.AttemptUpload(operation_key='upload'+str(index),attempt=attempt.id,chunk_index=index,items=entries[offset:offset+200])
        attempt,receipt=a.upload(attempt,inp)
        same,again=a.upload(attempt,inp);assert same==attempt and again==receipt
    assert [v.receipt.count for v in attempt.chunks]==[200,200,3]
    sealed=a.seal(s,dr,attempt,expected_version=attempt.version)
    changed,applied=a.apply(s,dr,sealed,expected_version=sealed.version,expected_draft_version=1,revision_id=new_id())
    assert applied.state=='applied' and len(changed.selections)==403
    with pytest.raises(p.ReconciliationError):a.apply(s,changed,applied,expected_version=applied.version,expected_draft_version=changed.version,revision_id=new_id())
    assert all(not v for k,v in s.rows.items() if k.startswith('attempt'))


def test_seal_semantic_targets_hash_tail_and_immutable_abort(certificate_world):
    s,bank,_,_=certificate_world;dr=draft(bank)
    seeds=tuple(m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=bank,kind='insert',date=(date(2027,1,1)+timedelta(days=i)).isoformat())) for i in range(403))
    def build(entries,declared=None,hash_=None):
        out=a.begin(s,dr,m.AttemptBegin(operation_key='begin',draft=dr.id,expected_version=1,base_revision_id=dr.current_revision_id,attempt_generation='g',declared_count=declared if declared is not None else len(entries),intent_hash=hash_ or digest(a.payload(entries))),identity=new_id())
        for i,start in enumerate(range(0,len(entries),200)):out,_=a.upload(out,m.AttemptUpload(operation_key='up',attempt=out.id,chunk_index=i,items=entries[start:start+200]))
        return out
    from tests.test_reconciliation_insertion_targets import owned
    replacements=tuple(draft(bank,kind='amendment',cutoff=v.payload.date,state=s.rows['accounts'][0]) for v in seeds)
    s=owned(s,replacements)
    certs=tuple(m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=bank,mode='insert',predecessor_id=s.rows['certificates'][0]['id'],replacement_draft_revision_id=v.current_revision_id)) for v in replacements)
    entries=seeds+certs;ctx=a.ManifestContext(s.source,s.authority_transactions,{bank:s.rows['accounts'][0]['version']})
    attempt=build(entries);sealed=a.seal(s,dr,attempt,expected_version=attempt.version,manifest_context=ctx);aborted=a.abort(sealed,expected_version=sealed.version)
    assert aborted.chunks==sealed.chunks and len(aborted.items)==806
    assert len(a.manifest_order(s,entries,ctx))==403
    for bad in (build(seeds[:-1],declared=403),build(seeds,hash_='0'*64),build((seeds[0],seeds[0]))):
        with pytest.raises(p.ReconciliationError,match='MANIFEST'):a.seal(s,dr,bad,expected_version=bad.version)
    with pytest.raises(p.ReconciliationError,match='ATTEMPT_STATE'):a.abort(aborted,expected_version=aborted.version)
    with pytest.raises(p.ReconciliationError,match='KEY_REUSED'):a.upload(attempt,m.AttemptUpload(operation_key='changed',attempt=attempt.id,chunk_index=0,items=(seeds[1],)))
    with pytest.raises(p.ReconciliationError,match='ATTEMPT_STATE'):a.upload(aborted,m.AttemptUpload(operation_key='extra',attempt=aborted.id,chunk_index=len(aborted.chunks),items=(seeds[0],)))
    # Identical numeric IDs in distinct typed target families are not duplicates.
    assert a.semantic_key(seeds[0])[0]=='seed'


def test_saved_selection_metadata_date_move_void_and_report_history(client,driver,certificate_world):
    old,bank,equity,doc=certificate_world;cert=old.rows['certificates'][0];captured_bytes=r.project(old,cert['id'],authority_transactions=old.authority_transactions).model_dump_json()
    run(client,'journal update',dict(journal=doc['id'],expected_version=1,memo='metadata'))
    with driver.session() as session:g=adapters.graph(session,[doc['id']]);refs=references(session);authority=adapters.authority(session,[doc['id']])
    now=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    report=r.project(now,cert['id'],authority_transactions=now.authority_transactions);assert report.local_replacement_impact==0 and report.cumulative_difference==0
    assert report.as_certified==r.project(old,cert['id'],authority_transactions=old.authority_transactions).as_certified
    dr=draft(bank,selections=select(old),state=old.rows['accounts'][0]);before=dr.model_dump_json()
    with pytest.raises(p.ReconciliationError,match='SELECTION_STALE'):p.whole_selection(now,dr)
    oldgroup=next(iter(p.groups(old.current.values()).values()));newgroup=next(iter(p.groups(now.current.values()).values()))
    accepted=d.accept_current(now,dr,m.AcceptCurrent(operation_key='accept',draft=dr.id,expected_version=1,entries=(m.AcceptEntry(saved_group_fingerprint=p.group_fingerprint(oldgroup),current=(m.GroupRef(movement=m.MovementKey.model_validate_json(newgroup[0]['movement_snapshot']),group_fingerprint=p.group_fingerprint(newgroup)),)),)),revision_id=new_id())
    assert p.whole_selection(now,accepted) and dr.model_dump_json()==before
    run(client,'journal update',dict(journal=doc['id'],expected_version=2,date='2026-02-10'))
    with driver.session() as session:g=adapters.graph(session,[doc['id']]);refs=references(session)
    dated=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    report=r.project(dated,cert['id'],authority_transactions=dated.authority_transactions);assert (report.local_replacement_impact,report.cumulative_difference)==(-1000,1000)
    run(client,'journal void',dict(journal=doc['id'],expected_version=3))
    with driver.session() as session:g=adapters.graph(session,[doc['id']]);refs=references(session)
    void=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    assert r.project(void,cert['id'],authority_transactions=void.authority_transactions).cumulative_reconstruction==0
    assert r.project(old,cert['id'],authority_transactions=old.authority_transactions).model_dump_json()==captured_bytes
    # The real R0 inverse proof runs for every report population; corrupting an
    # inverse amount is detected, not hidden by the zero current total.
    bad=copy.deepcopy(void.source);inverse=next(v for v in bad.rows['posting_lines'] if v['reversed_line_id']);inverse['credit_minor_units']+=1
    with pytest.raises(Exception):p.snapshot(void.rows,source=bad,captured_graphs=void.captured_graphs,referenced_rows=refs,authority_transactions=authority)


def test_authority_before_content_and_recovery_original_intent(certificate_world):
    s,bank,_,doc=certificate_world;bad=copy.deepcopy(s.rows);bad['effect_versions'][0]['signed_debit']=999
    with pytest.raises(p.ReconciliationError,match='E_PERMISSION'):p.snapshot(bad,source=s.source,captured_graphs=s.captured_graphs,referenced_rows=s.referenced_rows,authority_transactions=())
    original=dict(operation_key='original',draft=new_id(),expected_version=1,expected_facts_fingerprint='1'*64,dependency_guard='old')
    req=op.Request(command='reconcile finish',company_id=new_id(),original_input=original,provided_fields=tuple(original),context={'reason':'retained'},context_provided_fields=('reason',),canonical_input=original,resolved_captures={})
    targets=(m.OperationTarget(kind='transactions',id=doc['id']),m.OperationTarget(kind='accounts',id=bank))
    receipt=op.Receipt(operation_id=new_id(),operation_key='original',request=req,original_effect={'certificate_id':new_id(),'selected_sum':1000},targets=targets,items={'request':(),'effects':(),'generated':(),'targets':tuple(v.model_dump(mode='json') for v in targets)})
    request,effect,hash_=op.envelopes(receipt);assert hash_==digest(req.intent()) and request.document['context']=={'reason':'retained'}
    refreshed=dict(original,dependency_guard='new',expected_facts_fingerprint='2'*64)
    retry=req.model_copy(update={'original_input':refreshed,'canonical_input':refreshed})
    replay=op.recover(s,receipt,retry,authority_transactions=s.authority_transactions,current_targets=(doc['id'],),current_state={'state':'later'})
    assert replay.original_effect==receipt.original_effect and replay.current=={'state':'later'} and replay.idempotent_replay and not replay.new_effect
    with pytest.raises(p.ReconciliationError,match='E_PERMISSION'):op.recover(s,receipt,retry,authority_transactions=(),current_targets=(),current_state={})
    changed=req.model_copy(update={'context':{'reason':'different'}})
    assert op.recover(s,receipt,changed,authority_transactions=s.authority_transactions,current_targets=(),current_state={}) is None


def two_statements(client,driver,prefix="F3"):
    bank=account(client,prefix+' bank');equity=account(client,prefix+' equity','equity')
    positive=journal(client,pair(bank,equity,'100'));negative=journal(client,pair(equity,bank,'40'));feb=journal(client,pair(bank,equity,'20'),date='2026-02-10')
    ids=[v['id'] for v in (positive,negative,feb)]
    with driver.session() as session:g=adapters.graph(session,ids);refs=references(session);authority=adapters.authority(session,ids)
    rows=aggregate(captured(g),g,bank);first=rows['certificates'][0];second=dict(first,id=new_id(),generation=2,statement_date='2026-02-28',previous_certificate_id=first['id'],beginning_balance=6000,ending_balance=8000,selected_sum=2000,positive_sum=2000,negative_sum=0,positive_count=1,negative_count=0)
    first.update(ending_balance=6000,selected_sum=6000,positive_sum=10000,negative_sum=-4000,positive_count=1,negative_count=1)
    rows['certificates'].append(second)
    revision=copy.deepcopy(rows['draft_revisions'][0]);header=json.loads(revision['header_snapshot']);header.update(statement_date='2026-02-28',entered_balance=8000)
    revision.update(id=new_id(),draft_id=new_id(),header_snapshot=canonical(header),base_opening_id=first['opening_id'],base_head_id=first['id'])
    rows['draft_revisions'].append(revision);rows['drafts'].append(dict(rows['drafts'][0],id=revision['draft_id'],current_revision_id=revision['id']))
    second['origin_draft_revision_id']=revision['id']
    first_header=json.loads(rows['draft_revisions'][0]['header_snapshot']);first_header['entered_balance']=6000;rows['draft_revisions'][0]['header_snapshot']=canonical(first_header)
    rows['certificate_members']=[];rows['claims']=[];rows['current_members']=[]
    _,current=adapters.enumerate_graph(g)
    for cert in rows['certificates']:
        pop=json.loads(cert['captured_source_snapshot']);pop.update(cutoff=cert['statement_date'],version_ids=[v['id'] for v in rows['effect_versions']],signed_gl_total=6000 if cert is first else 8000,source_fingerprint=population_fingerprint(rows['effect_versions']))
        cert['captured_source_snapshot']=canonical(pop)
        for i,v in enumerate(rows['effect_versions']):
            jan=v['effective_date']<='2026-01-31'
            classification=('selected' if jan else 'outstanding') if cert is first else ('prior_cleared' if jan else 'selected')
            rows['certificate_members'].append(dict(certificate_id=cert['id'],account_id=bank,key_id=v['key_id'],version_id=v['id'],classification=classification,eligible_at_cutoff=int(v['effective_date']<=cert['statement_date']),ordinal=i))
            if classification=='selected':
                claim=dict(id=new_id(),account_id=bank,key_id=v['key_id'],version_id=v['id'],opening_id=None,certificate_id=cert['id'],event_id=rows['events'][0]['id'])
                rows['claims'].append(claim);rows['current_members'].append(dict(key_id=v['key_id'],claim_id=claim['id']))
    rows['active_certificates'].append(dict(account_id=bank,statement_date=second['statement_date'],certificate_id=second['id']))
    rows['accounts'][0]['head_certificate_id']=second['id'];rows['event_accounts'][0]['after_head_id']=second['id']
    operation=rows['operations'][0];targets=[json.loads(v['facts_snapshot']) for v in rows['operation_items']]
    for kind,identity in [('certificates',second['id']),('drafts',revision['draft_id']),*(('transactions',i) for i in ids)]:
        if dict(kind=kind,id=identity) not in targets:targets.append(dict(kind=kind,id=identity))
    for kind,field in [('transactions','transaction_id'),('accounts','account_id'),('drafts','draft_id'),('openings','opening_id'),('certificates','certificate_id')]:
        rows['operation_'+kind]=[dict(operation_id=operation['id'],**{field:v['id']}) for v in targets if v['kind']==kind]
    rows['operation_items']=[dict(operation_id=operation['id'],kind='targets',ordinal=i,facts_snapshot=canonical(v)) for i,v in enumerate(targets)]
    for field in ('original_request_snapshot','original_effect_snapshot'):
        envelope=json.loads(operation[field]);envelope['collections']['targets']=dict(count=len(targets),hash=digest(targets));operation[field]=canonical(envelope)
    captures={v['id']:g for name in ('openings','certificates') for v in rows[name]}
    return p.snapshot(rows,source=g,captured_graphs=captures,referenced_rows=refs,authority_transactions=authority),bank,equity,negative,ids


def test_f3_distinct_reports_suffix_manifest_adjacency_and_undo(client,driver):
    old,bank,equity,negative,ids=two_statements(client,driver)
    jan,feb=old.rows['certificates'];assert [r.project(old,c['id'],authority_transactions=old.authority_transactions).cumulative_reconstruction for c in (jan,feb)]==[6000,8000]
    run(client,'journal update',dict(journal=negative['id'],expected_version=1,lines=[dict(line,line_id=old_line['line_id']) for line,old_line in zip(pair(equity,bank,'45'),negative['revision']['lines'])]))
    with driver.session() as session:g=adapters.graph(session,ids);refs=references(session);authority=adapters.authority(session,ids)
    now=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    reports=[r.project(now,c['id'],authority_transactions=now.authority_transactions) for c in (jan,feb)]
    assert [v.local_replacement_impact for v in reports]==[-500,0]
    assert [v.cumulative_reconstruction for v in reports]==[5500,7500]
    assert [v.cumulative_difference for v in reports]==[500,500]
    closure=am.derive(now,(),before_source=old.source,authority_transactions=authority)
    assert closure.certificate_ids==(jan['id'],feb['id']) and closure.opening_ids==()
    replacements={};targets=[]
    for cert,ending in ((jan,5500),(feb,7500)):
        chosen={v['key_id'] for v in old.rows['certificate_members'] if v['certificate_id']==cert['id'] and v['classification']=='selected'}
        dr=draft(bank,kind='amendment',ending=ending,cutoff=cert['statement_date'],selections=select(now,chosen),state=old.rows['accounts'][0]).model_copy(update={'repair_of_certificate_id':cert['id']})
        replacements[dr.current_revision_id]=dr
        targets.append(m.CertificateTarget(account_id=bank,certificate_id=cert['id'],predecessor_id=cert['previous_certificate_id'],replacement_draft_revision_id=dr.current_revision_id,mode='replace'))
    manifest=m.Manifest(seeds=(),certificates=tuple(targets),account_versions=closure.account_versions)
    assert [v.ending_balance for v in am.validate_manifest(now,closure,manifest,(),replacement_drafts=replacements)]==[5500,7500]
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):am.validate_manifest(now,closure,manifest.model_copy(update={'certificates':manifest.certificates[:1]}),(),replacement_drafts=replacements)
    bad=dict(replacements);last=targets[-1].replacement_draft_revision_id;bad[last]=bad[last].model_copy(update={'header':bad[last].header.model_copy(update={'entered_balance':8000})})
    with pytest.raises(p.ReconciliationError,match='DIFFERENCE'):am.validate_manifest(now,closure,manifest,(),replacement_drafts=bad)
    undo=am.undo_manifest(old,bank,feb['id'],1);assert undo.certificates[0].mode=='invalidate'
    with pytest.raises(p.ReconciliationError,match='DEPENDENCY'):am.undo_manifest(old,bank,jan['id'],1)
    assert len(old.rows['claims'])==3 and all(v['active'] for v in old.current.values())


def test_draft_noop_header_proposal_consumption_and_real_ordinary_plan(client,driver,certificate_world):
    from bookflow.core import registry
    from bookflow.core.context import Context,Interface
    s,bank,equity,_=certificate_world
    dr=draft(bank,ending=1000,cutoff='2026-02-28',state=s.rows['accounts'][0])
    noop=m.DraftUpdate(operation_key='same',draft=dr.id,expected_version=1,entered_balance='10.00')
    assert d.update(s,dr,noop,revision_id=new_id())==dr
    changed=d.update(s,dr,m.DraftUpdate(operation_key='date',draft=dr.id,expected_version=1,statement_date='2026-03-31'),revision_id=new_id())
    assert changed.version==2 and changed.selections==dr.selections
    for action in ('leave','resume'):assert d.lifecycle(s,dr,action,expected_version=1,revision_id=new_id(),operation_id=new_id())==dr
    canceled=d.lifecycle(s,dr,'cancel',expected_version=1,revision_id=new_id(),operation_id=new_id())
    with pytest.raises(p.ReconciliationError,match='DRAFT_STATE'):d.lifecycle(s,canceled,'resume',expected_version=2,revision_id=new_id(),operation_id=new_id())
    body=m.ProposalInput(format=1,date='2026-02-10',amount_minor_units=100,currency='USD',offset_account_id=equity,class_id=None,memo='Fee',number=None,reason='ordinary fee')
    request=m.ProposalSet(operation_key='fee',draft=dr.id,expected_version=1,role='charge',input=body)
    proposal=d.proposal(s,dr,request,identity=new_id(),revision_id=new_id())
    inp=d.journal_input(s,dr,proposal);assert [v.side for v in inp.lines]==['credit','debit']
    before=driver.dump()
    with driver.session() as session:
        ctx=Context.new(Interface.python,'private ii witness',reason='ordinary fee')
        plan=registry.get('journal post').plan(inp,ctx,session)
        projection=adapters.prepare_prospective(session,ctx,plan)
        assert sum(v.signed_debit for v in projection.changes.after if v.account_id==bank)==-100
    assert driver.dump()==before
    consumed=proposal.model_copy(update={'consumed_journal_id':new_id(),'consumed_revision_id':new_id()})
    with pytest.raises(p.ReconciliationError,match='DRAFT_STATE'):d.journal_input(s,dr,consumed)
    update=request.model_copy(update={'proposal_id':proposal.id,'expected_proposal_version':1})
    with pytest.raises(p.ReconciliationError,match='DRAFT_STATE'):d.proposal(s,dr,update,identity=new_id(),revision_id=new_id(),previous=consumed)
    with pytest.raises(p.ReconciliationError,match='DATE'):d.proposal(s,dr,request.model_copy(update={'input':body.model_copy(update={'date':'2026-03-01'})}),identity=new_id(),revision_id=new_id())
    # A pending attempt blocks ordinary edits even when their content is a no-op.
    blocked=copy.deepcopy(s);blocked.rows['attempt_active']=[dict(draft_id=dr.id,attempt_id=new_id())]
    with pytest.raises(p.ReconciliationError,match='ATTEMPT_STATE'):d.update(blocked,dr,noop,revision_id=new_id())


def test_cross_account_mandatory_and_explicit_seed_suffixes_no_double_claim(client,driver):
    left,bank_a,equity_a,moving,ids_a=two_statements(client,driver,'Move A')
    right,bank_b,_,_,ids_b=two_statements(client,driver,'Move B')
    right.rows['operations'][0]['operation_key']='N-example-B'
    rows={k:left.rows[k]+right.rows[k] for k in left.rows}
    ids=ids_a+ids_b
    with driver.session() as session:g=adapters.graph(session,ids);refs=references(session);authority=adapters.authority(session,ids)
    old=p.snapshot(rows,source=g,captured_graphs=dict(left.captured_graphs,**right.captured_graphs),referenced_rows=refs,authority_transactions=authority)
    seed=m.SeedTarget(account_id=bank_a,kind='statement',certificate_id=left.rows['certificates'][0]['id'])
    elective=am.derive(old,(seed,),before_source=old.source,authority_transactions=authority)
    assert set(elective.account_versions)=={bank_a} and len(elective.certificate_ids)==2
    otherseed=m.SeedTarget(account_id=bank_b,kind='statement',certificate_id=right.rows['certificates'][0]['id'])
    assert len(am.derive(old,(seed,otherseed),before_source=old.source,authority_transactions=authority).certificate_ids)==4
    lines=[dict(line,line_id=saved['line_id']) for line,saved in zip(pair(equity_a,bank_b,'40'),moving['revision']['lines'])]
    run(client,'journal update',dict(journal=moving['id'],expected_version=1,lines=lines))
    with driver.session() as session:g=adapters.graph(session,ids);refs=references(session)
    now=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    closure=am.derive(now,(),before_source=old.source,authority_transactions=authority)
    assert set(closure.account_versions)=={bank_a,bank_b} and len(closure.certificate_ids)==4
    moved=next(v for v in now.current.values() if v['transaction_id']==moving['id'] and v['account_id']==bank_b)
    assert p.claimed(now)[moved['key_id']]['account_id']==bank_a
    assert sum(v['key_id']==moved['key_id'] for v in now.rows['current_members'])==1
    dr=draft(bank_b,cutoff='2026-03-31',ending=8000,state=right.rows['accounts'][0])
    group=next(v for v in p.groups(now.current.values()).values() if moved in v)
    request=m.Mark(operation_key='bad-move',draft=dr.id,expected_version=1,entries=(m.MarkEntry(movement=m.MovementKey.model_validate_json(moved['movement_snapshot']),group_fingerprint=p.group_fingerprint(group),action='mark'),))
    with pytest.raises(p.ReconciliationError,match='MEMBERSHIP_CONFLICT'):d.mark(now,dr,request,revision_id=new_id())
    targets=[];replacements={}
    for old_account,endings in ((left,(10000,12000)),(right,(2000,4000))):
        state=old_account.rows['accounts'][0]
        for i,cert in enumerate(old_account.rows['certificates']):
            keys={v['key_id'] for v in old.rows['certificate_members'] if v['certificate_id']==cert['id'] and v['classification']=='selected'}
            if state['account_id']==bank_a:keys.discard(moved['key_id'])
            elif i==0:keys.add(moved['key_id'])
            replacement=draft(state['account_id'],kind='amendment',ending=endings[i],cutoff=cert['statement_date'],selections=select(now,keys),state=state).model_copy(update={'repair_of_certificate_id':cert['id']})
            replacements[replacement.current_revision_id]=replacement
            targets.append(m.CertificateTarget(account_id=state['account_id'],certificate_id=cert['id'],predecessor_id=cert['previous_certificate_id'],replacement_draft_revision_id=replacement.current_revision_id,mode='replace'))
    manifest=m.Manifest(seeds=(),certificates=tuple(targets),account_versions=closure.account_versions)
    result=am.validate_manifest(now,closure,manifest,(),replacement_drafts=replacements)
    assert sorted(v.ending_balance for v in result)==[2000,4000,10000,12000]
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):am.validate_manifest(now,closure,manifest.model_copy(update={'certificates':tuple(targets[:2])}),(),replacement_drafts=replacements)


def test_backdated_opening_dependency_complete_replacement_and_invalidation(client,driver,certificate_world):
    old,bank,_,doc=certificate_world
    run(client,'journal update',dict(journal=doc['id'],expected_version=1,date='2025-12-31'))
    with driver.session() as session:g=adapters.graph(session,[doc['id']]);refs=references(session)
    now=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=old.authority_transactions)
    closure=am.derive(now,(),before_source=old.source,authority_transactions=old.authority_transactions)
    opening=old.rows['openings'][0];certificate=old.rows['certificates'][0]
    assert closure.opening_ids==(opening['id'],) and closure.certificate_ids==(certificate['id'],)
    new_opening=draft(bank,kind='opening',ending=0,cutoff='2026-01-01',selections=select(now,action='outstanding'),state=old.rows['accounts'][0]).model_copy(update={'repair_of_opening_id':opening['id']})
    new_statement=draft(bank,kind='amendment',ending=1000,selections=select(now),state=old.rows['accounts'][0]).model_copy(update={'repair_of_certificate_id':certificate['id']})
    replacements={v.current_revision_id:v for v in (new_opening,new_statement)}
    manifest=m.Manifest(seeds=(),certificates=(m.CertificateTarget(account_id=bank,certificate_id=certificate['id'],replacement_draft_revision_id=new_statement.current_revision_id,mode='replace'),),account_versions=closure.account_versions)
    action=am.OpeningAction(account_id=bank,opening_id=opening['id'],mode='replace',replacement_draft_revision_id=new_opening.current_revision_id)
    assert am.validate_manifest(now,closure,manifest,(action,),replacement_drafts=replacements)[0].difference==0
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):am.validate_manifest(now,closure,manifest,(),replacement_drafts=replacements)
    invalid=m.Manifest(seeds=(),certificates=(m.CertificateTarget(account_id=bank,certificate_id=certificate['id'],mode='invalidate'),),account_versions=closure.account_versions)
    assert am.validate_manifest(now,closure,invalid,(am.OpeningAction(account_id=bank,opening_id=opening['id'],mode='invalidate'),),replacement_drafts={})==()


def test_exact_fee_preview_force_and_no_money_until_owning_execution(client,driver,certificate_world):
    from bookflow.company import reconciliation_proposals as fees
    from bookflow.core import registry
    from bookflow.core.context import Context,Interface
    s,bank,equity,_=certificate_world;dr=draft(bank,ending=850,cutoff='2026-02-28',state=s.rows['accounts'][0])
    body=m.ProposalInput(format=1,date='2026-02-10',amount_minor_units=100,currency='USD',offset_account_id=equity,class_id=None,memo='Fee',number=None,reason='fee')
    proposal=m.Proposal(id=new_id(),draft_id=dr.id,version=1,revision_id=new_id(),role='charge',input=body)
    dr=dr.model_copy(update={'proposal_revision_ids':(proposal.revision_id,)})
    original=driver.dump()
    with driver.session() as session:
        ctx=Context.new(Interface.python,'ii preview',reason='fee')
        plan=registry.get('journal post').plan(d.journal_input(s,dr,proposal),ctx,session)
        prepared=fees.preview(session,ctx,s,dr,(proposal,),(plan,))
        assert prepared.plans[0] is plan and (prepared.output.original_difference,prepared.output.final.difference)==(-150,-50)
        force=fees.adjustment(dr,prepared.output.final,m.Adjustment(date='2026-02-10',offset_account_id=equity,reason='explicit correction'),identity=new_id(),revision_id=new_id(),currency='USD')
        assert force.input.amount_minor_units==-50
        adjustment_plan=registry.get('journal post').plan(d.journal_input(s,dr,force),ctx,session)
        final=fees.preview(session,ctx,s,dr.model_copy(update={'proposal_revision_ids':(proposal.revision_id,force.revision_id)}),(proposal,force),(plan,adjustment_plan))
        assert final.output.final.difference==0 and final.output.final.selected_sum==-150
        with pytest.raises(p.ReconciliationError,match='MANIFEST'):fees.preview(session,ctx,s,dr,(proposal,proposal),(plan,plan))
    assert driver.dump()==original


def test_query_output_history_pages_stale_fingerprint_and_proposal_revisions(certificate_world):
    s,bank,equity,_=certificate_world
    doc=q.certificates(s,m.CertificateQuery(account=bank,limit=1),authority_transactions=s.authority_transactions).items[0]
    items=q.certificate_items(s,doc.id,limit=1,authority_transactions=s.authority_transactions);assert items.count==1 and items.items[0].amount==1000
    assert items.items[0].display.number==q.certificate_items(s,doc.id,limit=200,authority_transactions=s.authority_transactions).items[0].display.number
    saved=d.load(s,s.rows['drafts'][0]['id'],authority_transactions=s.authority_transactions);history=q.draft_history(s,saved.id,limit=25,authority_transactions=s.authority_transactions)
    assert history.items[0].header==saved.header and history.items[0].id==saved.current_revision_id
    dr=draft(bank,ending=1000,cutoff='2026-02-28',state=s.rows['accounts'][0]);page=q.candidates(s,dr,m.CandidateFilter(),authority_transactions=s.authority_transactions)
    changed=d.update(s,dr,m.DraftUpdate(operation_key='header',draft=dr.id,expected_version=1,statement_date='2026-03-31'),revision_id=new_id())
    with pytest.raises(p.ReconciliationError,match='QUERY_STALE'):q.candidates(s,changed,m.CandidateFilter(),expected_fingerprint=page.fingerprint,authority_transactions=s.authority_transactions)
    body=m.ProposalInput(format=1,date='2026-02-10',amount_minor_units=100,currency='USD',offset_account_id=equity,class_id=None,memo=None,number=None,reason=None)
    request=m.ProposalSet(operation_key='new',draft=dr.id,expected_version=1,role='charge',input=body)
    first,proposal=d.set_proposal(s,dr,request,identity=new_id(),proposal_revision_id=new_id(),draft_revision_id=new_id())
    assert first.proposal_revision_ids==(proposal.revision_id,)
    update=request.model_copy(update={'operation_key':'modify','expected_version':2,'proposal_id':proposal.id,'expected_proposal_version':1,'input':body.model_copy(update={'amount_minor_units':200})})
    second,new_proposal=d.set_proposal(s,first,update,identity=new_id(),proposal_revision_id=new_id(),draft_revision_id=new_id(),previous=proposal)
    assert second.proposal_revision_ids==(new_proposal.revision_id,) and new_proposal.version==2 and proposal.input.amount_minor_units==100
    removed=d.remove_proposal(s,second,m.ProposalRemove(operation_key='remove',draft=dr.id,expected_version=3,proposal_id=proposal.id,expected_proposal_version=2),new_proposal,revision_id=new_id())
    assert removed.proposal_revision_ids==() and first.proposal_revision_ids==(proposal.revision_id,)


def test_current_payment_invoice_and_persisted_g2_anchors(client,sale,driver):
    from tests.test_payment_receipts import method
    from tests.test_deposit_lifecycle import additional_document
    bank=account(client,'ii payment bank')
    inv=run(client,'invoice post',dict(customer=sale['customer'],date='2026-01-10',lines=[dict(item=sale['item'],unit_price='5')]))
    paid=run(client,'payment receive',dict(customer=sale['customer'],date='2026-01-11',amount='10',deposit_to=bank,payment_method=method(client),operation_key='ii-payment',applications=dict(mode='inline',items=[dict(invoice=inv['id'],expected_version=1,amount='5')])))
    body=additional_document(client,sale)
    deposit=driver.run('post',dict(operation_key='ii-deposit',document=body))
    s=context(driver,[inv['id'],paid['id'],deposit.current.id])
    assert set(v['producer'] for v in s.rows['keys'])=={'payment','deposit'}
    assert {v['id'] for v in s.source.rows['transactions']}=={inv['id'],paid['id'],deposit.current.id}
    payment=q.candidates(s,draft(bank,ending=1000),m.CandidateFilter(),authority_transactions=s.authority_transactions);assert payment.count==1 and payment.positive_sum==1000
    deposit_bank=body['deposit_to'];page=q.candidates(s,draft(deposit_bank,ending=1000,cutoff='2026-06-30'),m.CandidateFilter(),authority_transactions=s.authority_transactions)
    assert page.count==1 and page.items[0].movement.producer=='deposit'
    version=next(v for v in s.current.values() if v['producer']=='deposit')
    assert version['source_version']==s.rows['deposit_versions'][0]['bank_version_id']
    assert p.totals(0,1000,[version]).difference==0


def test_opening_start_retains_ordinary_typed_attachment_identity(client,driver):
    import io
    bank=account(client,'ii evidence bank');equity=account(client,'ii evidence offset','equity')
    doc=journal(client,pair(bank,equity,'10'))
    attachment=client.attachment.add(record_type='transaction',record_id=doc['id'],original_filename='ii.bin',input_stream=io.BytesIO(b'private opening evidence'),company=COMPANY)
    s=context(driver,[doc['id']]);ref=m.AttachmentEvidence(kind='transaction_attachment',transaction_id=doc['id'],attachment_id=attachment['attachment']['id'],attachment_link_id=attachment['link']['id'])
    inp=m.OpeningStart(operation_key='opening',account=bank,opening_date='2026-01-31',entered_balance='10.00',evidence=Evidence(format=1,statement_reference='statement',entered_text=None),references=(ref,))
    opened=d.start(s,inp,identity=new_id(),revision_id=new_id());assert opened.evidence_references==(ref,)
    group=next(iter(p.groups(s.current.values()).values()))
    marked=d.mark(s,opened,m.Mark(operation_key='cover',draft=opened.id,expected_version=1,entries=(m.MarkEntry(movement=m.MovementKey.model_validate_json(group[0]['movement_snapshot']),group_fingerprint=p.group_fingerprint(group),action='covered'),)),revision_id=new_id())
    assert p.opening(s,marked).difference==0
    with pytest.raises(p.ReconciliationError,match='E_PERMISSION'):d.start(s,inp.model_copy(update={'references':(ref.model_copy(update={'transaction_id':new_id()}),)}),identity=new_id(),revision_id=new_id())


def test_attempt_payloads_match_exact_existing_subtype_columns():
    from bookflow.company import schema
    values=[m.MemberItem(kind='member',payload=m.MemberTarget(draft_id=new_id(),account_id=new_id(),key_id=new_id(),saved_version_id=new_id(),action='mark')),
        m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=new_id(),kind='insert',date='2026-01-01')),
        m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=new_id(),certificate_id=new_id(),mode='invalidate')),
        m.ProposalItem(kind='proposal',payload=m.ProposalTarget(proposal_id=new_id(),proposal_revision_id=new_id(),draft_id=new_id()))]
    for value in values:
        assert set(value.payload.model_dump())==set(schema.metadata.tables['reconciliation_attempt_'+value.kind+'s'].c.keys())-{'attempt_id','ordinal'}


def test_card_fee_force_mirror_and_current_period_owner(client,driver):
    from bookflow.company import reconciliation_proposals as fees
    from bookflow.core import registry
    from bookflow.core.context import Context,Interface
    from bookflow.company import schema
    from bookflow import BookflowError
    card=account(client,'ii fee card','credit_card');equity=account(client,'ii fee card equity','equity')
    doc=journal(client,pair(equity,card,'10'));s=context(driver,[doc['id']])
    opening=draft(card,kind='opening',ending=0,cutoff='2026-01-01');dr=draft(card,ending=0,cutoff='2026-01-31')
    body=m.ProposalInput(format=1,date='2026-01-15',amount_minor_units=100,currency='USD',offset_account_id=equity,class_id=None,memo=None,number=None,reason='card fee')
    proposal=m.Proposal(id=new_id(),draft_id=dr.id,version=1,revision_id=new_id(),role='charge',input=body)
    dr=dr.model_copy(update={'proposal_revision_ids':(proposal.revision_id,)})
    before=driver.dump()
    with driver.session() as session:
        ctx=Context.new(Interface.python,'ii card preview',reason='card fee')
        plan=registry.get('journal post').plan(d.journal_input(s,dr,proposal),ctx,session)
        prepared=fees.preview(session,ctx,s,dr,(proposal,),(plan,),opening_draft=opening)
        assert prepared.output.final.selected_sum==100 and prepared.output.final.difference==-100
        force=fees.adjustment(dr,prepared.output.final,m.Adjustment(date='2026-01-15',offset_account_id=equity,reason='explicit card correction'),identity=new_id(),revision_id=new_id(),currency='USD')
        force_input=d.journal_input(s,dr,force);assert force_input.lines[0].side=='debit'
        force_plan=registry.get('journal post').plan(force_input,ctx,session)
        assert fees.preview(session,ctx,s,dr.model_copy(update={'proposal_revision_ids':(proposal.revision_id,force.revision_id)}),(proposal,force),(plan,force_plan),opening_draft=opening).output.final.difference==0
        # An isolated transaction seam changes the real company period after the
        # ordinary Plan was prepared. Existing owning open_dates must reject.
        session.company.raw.execute('SAVEPOINT period_change')
        session.company.conn.execute(schema.company_info.update().values(closing_date='2026-01-31'))
        with pytest.raises(BookflowError) as error:fees.preview(session,ctx,s,dr,(proposal,),(plan,),opening_draft=opening)
        assert error.value.code=='E_PERIOD_CLOSED'
        session.company.raw.execute('ROLLBACK TO period_change');session.company.raw.execute('RELEASE period_change')
    assert driver.dump()==before


def test_real_shared_input_validation_accepts_json_arrays_into_domain_tuples():
    from types import SimpleNamespace
    from bookflow.core import registry,dispatch
    inputs=dict(operation_key='upload',attempt=new_id(),chunk_index=0,items=[dict(kind='seed',payload=dict(account_id=new_id(),kind='insert',date='2026-01-01'))])
    # Use the actual parsed-input validator without adding a registry entry.
    parsed=dispatch.validate_input(SimpleNamespace(input_model=m.AttemptUpload),inputs)
    assert len(parsed.items)==1 and isinstance(parsed.items[0],m.SeedItem)
    with pytest.raises(Exception):dispatch.validate_input(SimpleNamespace(input_model=m.AttemptUpload),dict(inputs,chunk_index=True))
    # Four of this family's commands are registered and the rest are not. The names come from
    # the module that registers them, so the day a fifth lands this reads the new set rather
    # than failing on a number; what it holds is that the models alone never register anything.
    from bookflow.commands.reconcile_cmds import reconcile_finish  # noqa: F401  (forces the import)
    registry.load_all()
    registered={c.name for c in registry.all_commands() if c.name.startswith('reconcile ')}
    assert registered and registered<set(m.INPUTS), 'a registered command with no declared input model'
    from bookflow.company.deposit_dependencies import RECONCILIATION
    assert RECONCILIATION.revision is None


def test_retained_N_multiple_insert_same_gap_constraint_witness(client,driver):
    """Regression for the frozen original gap, retaining ordinary source proof."""
    from tests.test_reconciliation_storage_validation import staged
    from bookflow.company.reconciliation_storage_validation import validate,InvalidStorage
    bank=account(client,'ii insertion witness');equity=account(client,'ii insertion offset','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as session:g=adapters.graph(session,[doc['id']]);refs=references(session)
    rows=staged(captured(g),g,bank,0);attempt=rows['attempts'][0];items=[]
    for i,day in enumerate(('2026-01-15','2026-01-20')):
        source=rows['draft_revisions'][0];revision=dict(source,id=new_id(),draft_id=new_id())
        header=json.loads(revision['header_snapshot']);header['statement_date']=day;revision['header_snapshot']=canonical(header)
        rows['draft_revisions'].append(revision);rows['drafts'].append(dict(rows['drafts'][0],id=revision['draft_id'],current_revision_id=revision['id']))
        payload=dict(account_id=bank,certificate_id=None,predecessor_id=None,replacement_draft_revision_id=revision['id'],mode='insert')
        items.append(dict(kind='certificate',payload=payload))
        rows['attempt_certificates'].append(dict(attempt_id=attempt['id'],ordinal=i,**payload))
        rows['attempt_items'].append(dict(attempt_id=attempt['id'],ordinal=i,kind='certificate',payload=canonical(payload),member_ordinal=None,seed_ordinal=None,certificate_ordinal=i,proposal_ordinal=None))
    attempt.update(declared_count=2,intent_hash=digest(items))
    def chunk(values):
        body=dict(format=1,items=values);hash_=digest(body)
        return dict(attempt_id=attempt['id'],chunk_index=0,request_hash=hash_,original_chunk=canonical(body),receipt=canonical(dict(format=1,chunk_index=0,first_ordinal=0,count=len(values),request_hash=hash_)))
    rows['attempt_chunks']=[chunk(items)]
    one=copy.deepcopy(rows);one['attempt_items']=one['attempt_items'][:1];one['attempt_certificates']=one['attempt_certificates'][:1];one['attempt_chunks']=[chunk(items[:1])]
    validate(one,source=g,referenced_rows=refs)
    validate(rows,source=g,referenced_rows=refs)
    duplicate=copy.deepcopy(rows)
    header=json.loads(duplicate['draft_revisions'][-1]['header_snapshot']);header['statement_date']='2026-01-15';duplicate['draft_revisions'][-1]['header_snapshot']=canonical(header)
    with pytest.raises(InvalidStorage,match='attempt_duplicate_target'):validate(duplicate,source=g,referenced_rows=refs)
    left,right=(m.ITEM.validate_json(json.dumps(v)) for v in items)
    assert left.payload.replacement_draft_revision_id!=right.payload.replacement_draft_revision_id
    for field,value in (('replacement_draft_revision_id',new_id()),('account_id',equity)):
        bad=copy.deepcopy(rows);bad['attempt_certificates'][0][field]=value
        with pytest.raises(InvalidStorage,match='foreign_owner'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['draft_revisions'][-1]['header_snapshot']='{}'
    with pytest.raises(InvalidStorage,match='snapshot_format'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);header=json.loads(bad['draft_revisions'][-1]['header_snapshot']);header['statement_date']=None;bad['draft_revisions'][-1]['header_snapshot']=canonical(header)
    with pytest.raises(InvalidStorage,match='draft_statement_date'):validate(bad,source=g,referenced_rows=refs)
    state=p.snapshot(rows,source=g,captured_graphs={},referenced_rows=refs,authority_transactions=(doc['id'],))
    assert a.semantic_key(left,snapshot=state)!=a.semantic_key(right,snapshot=state)


def test_backdated_insertion_uses_retained_predecessor_guard_and_new_adjacency(client,driver):
    s,bank,_,_,_=two_statements(client,driver,'Insert')
    jan,feb=s.rows['certificates'];seed=m.SeedTarget(account_id=bank,kind='insert',date='2026-02-05')
    closure=am.derive(s,(seed,),before_source=s.source,authority_transactions=s.authority_transactions)
    assert closure.certificate_ids==(feb['id'],)
    inserted=draft(bank,kind='amendment',ending=6000,cutoff='2026-02-05',state=s.rows['accounts'][0])
    keys={v['key_id'] for v in s.rows['certificate_members'] if v['certificate_id']==feb['id'] and v['classification']=='selected'}
    successor=draft(bank,kind='amendment',ending=8000,cutoff='2026-02-28',state=s.rows['accounts'][0],selections=select(s,keys)).model_copy(update={'repair_of_certificate_id':feb['id']})
    manifest=m.Manifest(seeds=(seed,),account_versions=closure.account_versions,certificates=(
        m.CertificateTarget(account_id=bank,predecessor_id=jan['id'],replacement_draft_revision_id=inserted.current_revision_id,mode='insert'),
        m.CertificateTarget(account_id=bank,certificate_id=feb['id'],predecessor_id=jan['id'],replacement_draft_revision_id=successor.current_revision_id,mode='replace')))
    results=am.validate_manifest(s,closure,manifest,(),replacement_drafts={v.current_revision_id:v for v in (inserted,successor)})
    assert [(v.beginning_balance,v.ending_balance) for v in results]==[(6000,6000),(6000,8000)]
