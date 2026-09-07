"""Sealing uses genuine owned N revisions over ordinary posting worlds."""
import copy
import json
from dataclasses import replace
import pytest
from bookflow.core.ids import new_id
from bookflow.company import reconciliation_attempts as a,reconciliation_amendments as am,reconciliation_preparation as p,reconciliation_commands_models as m
from bookflow.company.reconciliation_storage_validation import canonical,digest,validate,InvalidStorage
from tests.test_reconciliation_successor_models import draft,two_statements,select,certificate_world
from tests.test_deposit_lifecycle import driver


def owned(s,drafts):
    rows=copy.deepcopy(s.rows)
    for dr in drafts:
        header=dict(rows['drafts'][0],**{k:getattr(dr,k) for k in ('id','account_id','kind','version','current_revision_id','state','terminal_operation_id')})
        revision=dict(rows['draft_revisions'][0],id=dr.current_revision_id,draft_id=dr.id,account_id=dr.account_id,revision_number=dr.version,previous_revision_id=None,header_snapshot=canonical(dr.header.model_dump(mode='json')),
            **{k:getattr(dr,k) for k in ('base_chain_version','base_opening_id','base_head_id','repair_of_opening_id','repair_of_certificate_id')})
        rows['drafts'].append(header);rows['draft_revisions'].append(revision)
        rows['draft_members'].extend(dict(revision_id=dr.current_revision_id,draft_id=dr.id,account_id=dr.account_id,key_id=v.key_id,version_id=v.version_id,action=v.action,ordinal=i) for i,v in enumerate(dr.selections))
    return p.snapshot(rows,source=s.source,captured_graphs=s.captured_graphs,referenced_rows=s.referenced_rows,authority_transactions=s.authority_transactions)


def attempt(s,dr,entries):
    result=a.begin(s,dr,m.AttemptBegin(operation_key='begin',draft=dr.id,expected_version=dr.version,base_revision_id=dr.current_revision_id,attempt_generation='g',declared_count=len(entries),intent_hash=digest(a.payload(entries))),identity=new_id())
    for i,start in enumerate(range(0,len(entries),200)):
        result,_=a.upload(result,m.AttemptUpload(operation_key='upload',attempt=result.id,chunk_index=i,items=entries[start:start+200]))
    return result


def seal(s,dr,entries,ctx):
    value=attempt(s,dr,entries)
    return a.seal(s,dr,value,expected_version=value.version,manifest_context=ctx)


@pytest.fixture
def insertion_world(client,driver):
    s,bank,_,_,_=two_statements(client,driver,'Insertion ownership')
    # The ordinary January postings are Jan10; retain a real Jan10 P, so
    # Jan15 and Jan20 both observe P. Recalculate its cutoff capture exactly.
    rows=copy.deepcopy(s.rows);first=rows['certificates'][0]
    first['statement_date']='2026-01-10'
    population=json.loads(first['captured_source_snapshot']);population['cutoff']='2026-01-10';first['captured_source_snapshot']=canonical(population)
    rows['active_certificates'][0]['statement_date']='2026-01-10'
    rev=next(v for v in rows['draft_revisions'] if v['id']==first['origin_draft_revision_id'])
    head=json.loads(rev['header_snapshot']);head['statement_date']='2026-01-10';rev['header_snapshot']=canonical(head)
    s=p.snapshot(rows,source=s.source,captured_graphs=s.captured_graphs,referenced_rows=s.referenced_rows,authority_transactions=s.authority_transactions)
    jan,feb=s.rows['certificates'];state=s.rows['accounts'][0]
    inserts=[draft(bank,kind='amendment',ending=6000,cutoff=day,state=state) for day in ('2026-01-15','2026-01-20')]
    keys={v['key_id'] for v in s.rows['certificate_members'] if v['certificate_id']==feb['id'] and v['classification']=='selected'}
    successor=draft(bank,kind='amendment',ending=8000,cutoff='2026-02-28',state=state,selections=select(s,keys)).model_copy(update={'repair_of_certificate_id':feb['id']})
    dr=draft(bank,kind='amendment',state=state)
    s=owned(s,(*inserts,successor,dr))
    seeds=tuple(m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=bank,kind='insert',date=v.header.statement_date)) for v in inserts)
    targets=tuple(m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=bank,predecessor_id=jan['id'],replacement_draft_revision_id=v.current_revision_id,mode='insert')) for v in inserts)
    replacement=m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=bank,certificate_id=feb['id'],predecessor_id=jan['id'],replacement_draft_revision_id=successor.current_revision_id,mode='replace'))
    ctx=a.ManifestContext(s.source,s.authority_transactions,{bank:state['version']})
    return s,dr,seeds+targets+(replacement,),ctx,(*inserts,successor)


def test_two_insertions_complete_seal_apply_and_derived_adjacency(insertion_world):
    s,dr,entries,ctx,drafts=insertion_world
    # Upload order is deliberately not output date order.
    entries=entries[:2]+(entries[3],entries[4],entries[2])
    before=canonical(s.rows);sealed=seal(s,dr,entries,ctx)
    assert sealed.state=='sealed' and len(sealed.items)==5
    ordered=a.manifest_order(s,entries,ctx)
    assert [v.replacement_draft_revision_id for v in ordered]==[v.current_revision_id for v in drafts]
    assert len({v.predecessor_id for v in ordered})==1
    closure=am.derive(s,tuple(v.payload for v in entries if v.kind=='seed'),before_source=s.source,authority_transactions=s.authority_transactions)
    assert closure.certificate_ids==(s.rows['certificates'][1]['id'],)
    results=am.validate_manifest(s,closure,m.Manifest(seeds=tuple(v.payload for v in entries if v.kind=='seed'),certificates=ordered,account_versions=ctx.account_versions),(),replacement_drafts={v.current_revision_id:v for v in drafts})
    assert [(v.beginning_balance,v.ending_balance,v.selected_sum) for v in results]==[(6000,6000,0),(6000,6000,0),(6000,8000,2000)]
    changed,applied=a.apply(s,dr,sealed,expected_version=sealed.version,expected_draft_version=dr.version,revision_id=new_id(),manifest_context=ctx)
    assert changed.version==2 and applied.state=='applied' and applied.items==entries
    assert canonical(s.rows)==before
    assert sorted(a.semantic_key(v,snapshot=s) for v in entries if v.kind=='certificate' and v.payload.mode=='insert')==[('insertion',dr.account_id,'2026-01-15'),('insertion',dr.account_id,'2026-01-20')]


def test_seal_requires_complete_suffix_original_guards_and_context(insertion_world):
    s,dr,entries,ctx,_=insertion_world
    badpred=entries[3].model_copy(update={'payload':entries[3].payload.model_copy(update={'predecessor_id':None})})
    extra=entries[-1].model_copy(update={'payload':entries[-1].payload.model_copy(update={'certificate_id':s.rows['certificates'][0]['id']})})
    for values,context in ((entries,None),(entries[:-1],ctx),(entries+(extra,),ctx),(entries[:3]+(badpred,)+entries[4:],ctx),(entries,replace(ctx,account_versions={}))):
        with pytest.raises(p.ReconciliationError):seal(s,dr,values,context)
    with pytest.raises(p.ReconciliationError,match='E_PERMISSION'):
        seal(s,dr,entries,replace(ctx,authority_transactions=()))


def test_existing_certificate_identity_ignores_mode_account_predecessor(insertion_world):
    s,dr,entries,ctx,_=insertion_world
    original=entries[-1]
    changed=m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=new_id(),certificate_id=original.payload.certificate_id,predecessor_id=None,mode='invalidate'))
    assert a.semantic_key(original)==a.semantic_key(changed)==('certificate',original.payload.certificate_id)
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):seal(s,dr,entries+(changed,),ctx)


def test_insertion_identity_requires_exact_owned_decoded_revision(insertion_world):
    s,dr,entries,ctx,_=insertion_world
    target=entries[2]
    for patch in ({'replacement_draft_revision_id':new_id()},{'account_id':new_id()}):
        item=target.model_copy(update={'payload':target.payload.model_copy(update=patch)})
        with pytest.raises(p.ReconciliationError,match='MANIFEST'):a.semantic_key(item,snapshot=s)
    for header in ('{}','{"bad":true}','not json'):
        rows=copy.deepcopy(s.rows);next(v for v in rows['draft_revisions'] if v['id']==target.payload.replacement_draft_revision_id)['header_snapshot']=header
        with pytest.raises(p.ReconciliationError,match='MANIFEST'):a.semantic_key(target,snapshot=replace(s,rows=rows))
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):a.semantic_key(target)


def test_seal_rejects_duplicate_result_dates_insertion_replacement_and_replacements(insertion_world):
    s,dr,entries,ctx,drafts=insertion_world
    # Same account/date but genuinely different owned draft IDs.
    duplicate=draft(dr.account_id,kind='amendment',ending=6000,cutoff='2026-01-15',state=s.rows['accounts'][0])
    s=owned(s,(duplicate,))
    collision=entries[3].model_copy(update={'payload':entries[3].payload.model_copy(update={'replacement_draft_revision_id':duplicate.current_revision_id})})
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):seal(s,dr,entries[:3]+(collision,)+entries[4:],ctx)
    # Distinct semantic keys (insert vs existing certificate), same output date.
    successor=draft(dr.account_id,kind='amendment',ending=6000,cutoff='2026-01-20',state=s.rows['accounts'][0]).model_copy(update={'repair_of_certificate_id':entries[-1].payload.certificate_id})
    s=owned(s,(successor,))
    replacement=entries[-1].model_copy(update={'payload':entries[-1].payload.model_copy(update={'replacement_draft_revision_id':successor.current_revision_id})})
    assert a.semantic_key(replacement)!=a.semantic_key(entries[3],snapshot=s)
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):seal(s,dr,entries[:-1]+(replacement,),ctx)


def test_apply_revalidates_current_chain_revision_and_dates(insertion_world):
    s,dr,entries,ctx,_=insertion_world;sealed=seal(s,dr,entries,ctx)
    def apply(current):return a.apply(current,dr,sealed,expected_version=sealed.version,expected_draft_version=dr.version,revision_id=new_id(),manifest_context=ctx)
    rows=copy.deepcopy(s.rows);rows['accounts'][0]['version']+=1
    with pytest.raises(p.ReconciliationError,match='CHAIN_STALE'):apply(replace(s,rows=rows))
    rows=copy.deepcopy(s.rows);owner=next(v for v in rows['drafts'] if v['current_revision_id']==entries[2].payload.replacement_draft_revision_id);owner['current_revision_id']=new_id()
    with pytest.raises(p.ReconciliationError,match='CHAIN_STALE'):apply(replace(s,rows=rows))
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):
        a.apply(s,dr,sealed,expected_version=sealed.version,expected_draft_version=dr.version,revision_id=new_id())


def test_two_replacements_same_date_rejected_at_seal(insertion_world):
    s,dr,_,ctx,_=insertion_world
    state=s.rows['accounts'][0];certs=s.rows['certificates']
    drafts=tuple(draft(dr.account_id,kind='amendment',ending=8000,cutoff='2026-03-01',state=state).model_copy(update={'repair_of_certificate_id':v['id']}) for v in certs)
    s=owned(s,drafts)
    entries=(m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=dr.account_id,kind='statement',certificate_id=certs[0]['id'])),)+tuple(m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=dr.account_id,certificate_id=c['id'],predecessor_id=c['previous_certificate_id'],replacement_draft_revision_id=v.current_revision_id,mode='replace')) for c,v in zip(certs,drafts))
    assert len({a.semantic_key(v) for v in entries[1:]})==2
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):seal(s,dr,entries,ctx)


def test_different_accounts_same_insertion_date_full_seal(client,driver):
    from tests.test_reconciliation_successor_models import adapters,references
    left,bank_a,_,_,ids_a=two_statements(client,driver,'Insert A')
    right,bank_b,_,_,ids_b=two_statements(client,driver,'Insert B')
    right.rows['operations'][0]['operation_key']='insertion-world-B'
    rows={k:left.rows[k]+right.rows[k] for k in left.rows}
    with driver.session() as session:
        g=adapters.graph(session,ids_a+ids_b);refs=references(session);authority=adapters.authority(session,ids_a+ids_b)
    s=p.snapshot(rows,source=g,captured_graphs=dict(left.captured_graphs,**right.captured_graphs),referenced_rows=refs,authority_transactions=authority)
    drafts=[];entries=[]
    for account in s.rows['accounts']:
        dr=draft(account['account_id'],kind='amendment',ending=8000,cutoff='2026-03-01',state=account);drafts.append(dr)
        entries.extend((m.SeedItem(kind='seed',payload=m.SeedTarget(account_id=dr.account_id,kind='insert',date='2026-03-01')),m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=dr.account_id,predecessor_id=account['head_certificate_id'],replacement_draft_revision_id=dr.current_revision_id,mode='insert'))))
    s=owned(s,drafts);ctx=a.ManifestContext(s.source,authority,{v['account_id']:v['version'] for v in s.rows['accounts']})
    result=seal(s,drafts[0],tuple(entries),ctx)
    assert result.state=='sealed'
    assert {a.semantic_key(v,snapshot=s) for v in entries if v.kind=='certificate'}=={('insertion',bank_a,'2026-03-01'),('insertion',bank_b,'2026-03-01')}


def test_N_existing_certificate_duplicate_ignores_predecessor_and_mode(insertion_world):
    from bookflow.company.reconciliation_storage_validation import _receipts
    s,dr,entries,ctx,_=insertion_world
    # Pure receipt mutation retains two owned existing certificate targets.
    # All full graph/shape/FK controls are separately exercised by Snapshot.
    rows=copy.deepcopy(s.rows)
    value=attempt(s,dr,entries);event=rows['events'][0]
    event['kind']='bulk_stage'
    operation=event['operation_id']
    rows['operation_drafts'].append(dict(operation_id=operation,draft_id=dr.id))
    targets=[json.loads(v['facts_snapshot']) for v in rows['operation_items'] if v['kind']=='targets']+[dict(kind='drafts',id=dr.id)]
    rows['operation_items']=[dict(operation_id=operation,kind='targets',ordinal=i,facts_snapshot=v) for i,v in enumerate(targets)]
    for field in ('original_request_snapshot','original_effect_snapshot'):
        env=json.loads(rows['operations'][0][field]);env['collections']['targets']=dict(count=len(targets),hash=digest(targets));rows['operations'][0][field]=env
    rows['attempts']=[dict(id=value.id,draft_id=dr.id,state='uploading',audit_event_id=event['audit_event_id'],declared_count=2)]
    first=entries[-1].payload.model_dump();second=dict(first,mode='invalidate',replacement_draft_revision_id=None,predecessor_id=None)
    rows['attempt_certificates']=[dict(attempt_id=value.id,ordinal=i,**v) for i,v in enumerate((first,second))]
    rows['attempt_items']=[dict(attempt_id=value.id,ordinal=i,kind='certificate',payload=v) for i,v in enumerate((first,second))]
    with pytest.raises(InvalidStorage,match='attempt_duplicate_target'):_receipts(rows)


def test_source_change_derives_mandatory_suffix_without_explicit_seed(client,driver,insertion_world):
    from tests.test_reconciliation_successor_models import adapters,references,extend,run,pair
    old,dr,entries,ctx,_=insertion_world
    negative=next(v for v in old.current.values() if v['signed_debit']<0)
    transaction=negative['transaction_id']
    # Ordinary public update with stable entered line IDs; no modified guard.
    shown=run(client,'journal show',dict(journal=transaction))
    equity=next(v['account_id'] for v in shown['revision']['lines'] if v['account_id']!=dr.account_id)
    lines=[dict(v,line_id=saved['line_id']) for v,saved in zip(pair(equity,dr.account_id,'45'),shown['revision']['lines'])]
    run(client,'journal update',dict(journal=transaction,expected_version=1,lines=lines))
    ids=list(old.authority_transactions)
    with driver.session() as session:
        g=adapters.graph(session,ids);refs=references(session);authority=adapters.authority(session,ids)
    current=p.snapshot(extend(old.rows,g),source=g,captured_graphs=old.captured_graphs,referenced_rows=refs,authority_transactions=authority)
    context=a.ManifestContext(old.source,authority,ctx.account_versions)
    with pytest.raises(p.ReconciliationError,match='MANIFEST'):seal(current,dr,entries,context)
    targets=tuple(m.CertificateItem(kind='certificate',payload=m.CertificateTarget(account_id=dr.account_id,certificate_id=v['id'],predecessor_id=v['previous_certificate_id'],mode='invalidate')) for v in old.rows['certificates'])
    sealed=seal(current,dr,targets,context)
    assert len(sealed.items)==2 and sealed.state=='sealed'
    assert sum(v['signed_debit'] for v in current.current.values())==7500
    assert sum(v['signed_debit'] for v in old.current.values())==8000
