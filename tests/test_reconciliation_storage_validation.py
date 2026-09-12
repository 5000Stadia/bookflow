"""Private storage constraints against ordinary source graphs, checked row by row.

Production materializes a document's statement effects inside the command that posts it. What
this file checks is the constraints themselves, against aggregates it builds by hand -- so the
documents it posts have to arrive unmaterialized and leave it the sole writer of their rows.
`owned_storage` is what arranges that, and every module that builds an aggregate the same way
imports it.
"""
import copy
import json
import sqlite3

import pytest
from bookflow.company import schema, reconciliation_adapters as adapters
from bookflow.company.reconciliation_storage_validation import validate, InvalidStorage, canonical, digest, population_fingerprint
from bookflow.core.ids import new_id
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_reconciliation_adapters import account, journal, pair, run


@pytest.fixture(autouse=True)
def owned_storage(monkeypatch):
    """Leave this file the writer of the reconciliation rows for the documents it posts.

    Every posting command materializes its own statement effects. These tests assert what the
    storage refuses, by inserting hand-built aggregates, so a production row for the same
    document would collide on the very uniqueness the assertion is about. Disabling the drain
    for the documents this file posts is what keeps the two writers off each other; documents
    posted before the test (the seeded demo) keep their rows and are simply other documents.
    """
    from bookflow.company import reconciliation_materialization as materialization
    monkeypatch.setattr(materialization, 'drain_in_command', lambda *a, **k: 0)


def blank():
    return {n.removeprefix('reconciliation_'):[] for n in schema.metadata.tables if n.startswith('reconciliation_')}


def references(session):
    import sqlalchemy as sa
    names={fk.column.table.name for n,t in schema.metadata.tables.items() if n.startswith('reconciliation_') for fk in t.foreign_keys if not fk.column.table.name.startswith('reconciliation_')}
    names.add('company_info')
    return {n:[dict(v) for v in session.company.conn.execute(sa.select(schema.metadata.tables[n])).mappings()] for n in names}


def captured(g):
    """A hand-built expectation, deliberately independent of the production materializer.

    `reconciliation_materialization` writes these same rows from the same adapters. Building
    them a second way here is the point: an expectation produced by the code under test proves
    only that it agrees with itself, and what the mutations below check is that `validate`
    catches a row the production writer could never produce.
    """
    out=blank(); history,current=adapters.enumerate_graph(g); keys={}; versions={}
    for v in history:
        k=keys.setdefault(v.ref,new_id());identity=new_id();versions[v.ref,v.version_id]=identity
        if not any(r['id']==k for r in out['keys']):
            out['keys'].append(dict(id=k,producer=v.ref.producer,transaction_id=v.ref.transaction_id,role=v.ref.role,commercial_line_id=None if v.ref.producer=='deposit' else v.ref.component_id,deposit_key_id=v.ref.component_id if v.ref.producer=='deposit' else None))
        rev=g.by_id('transaction_revisions')[v.revision_id]
        out['effect_versions'].append(dict(id=identity,key_id=k,transaction_id=v.ref.transaction_id,producer=v.ref.producer,source_version=v.version_id,revision_id=v.revision_id,business_batch_id=v.business_batch_id,transition_batch_id=v.transition_batch_id,source_audit_event_id=v.audit_event_id,account_id=v.account_id,account_type=v.account_type,currency=v.currency,effective_date=v.effective_date,signed_debit=v.signed_debit,active=int(v.active),format_version=1,
            movement_snapshot=canonical(v.movement_key.model_dump(mode='json')),display_snapshot=canonical(dict(format=1,number=v.number,memo=v.memo,payees=list(v.payees),issuer=json.loads(rev['issuer_snapshot']),custom_fields=json.loads(rev['custom_fields_snapshot']))),provenance_snapshot=canonical(dict(format=1,document_line_ids=list(v.document_line_ids),rows=list(v.provenance))),commercial_link_id=None if v.ref.producer=='deposit' else identity,deposit_link_id=identity if v.ref.producer=='deposit' else None))
        if v.ref.producer=='deposit':out['deposit_versions'].append(dict(id=identity,transaction_id=v.ref.transaction_id,producer='deposit',bank_key_id=v.ref.component_id,bank_version_id=v.version_id))
        else:out['commercial_versions'].append(dict(id=identity,transaction_id=v.ref.transaction_id,producer=v.ref.producer,line_id=v.ref.component_id))
        out['effect_legs'].extend(dict(version_id=identity,transaction_id=v.ref.transaction_id,posting_line_id=p) for p in v.posting_line_ids)
        out['effect_sources'].extend(dict(version_id=identity,transaction_id=v.ref.transaction_id,source_id=p) for p in v.source_ids)
    out['effect_heads']=[dict(key_id=keys[v.ref],version_id=versions[v.ref,v.version_id]) for v in current]
    return out


def insert(raw,name,values):
    for row in values:
        raw.execute('INSERT INTO reconciliation_'+name+' ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))


def test_real_source_full_history_exact_legs_subtypes_and_deferred_missing_subtype(client,driver):
    bank=account(client,'N bank');equity=account(client,'N equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    run(client,'journal update',dict(journal=doc['id'],expected_version=1,memo='N replacement'))
    run(client,'journal void',dict(journal=doc['id'],expected_version=2))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    rows=captured(g)
    assert [v['signed_debit'] for v in rows['effect_versions']]==[1000,1000,0]
    assert len(rows['effect_legs'])==2 and len(rows['effect_sources'])==2
    validate(rows,source=g,referenced_rows=refs)
    for mutation,rule in [('omit_leg','physical_coverage'),('amount','source_facts'),('movement','movement_facts'),('source','unknown_source_version'),('head','source_heads'),('subtype','foreign_owner')]:
        bad=copy.deepcopy(rows)
        if mutation=='omit_leg':bad['effect_legs'].pop()
        elif mutation=='amount':bad['effect_versions'][0]['signed_debit']+=1
        elif mutation=='movement':
            value=json.loads(bad['effect_versions'][0]['movement_snapshot']);value['role']='net';bad['effect_versions'][0]['movement_snapshot']=canonical(value)
        elif mutation=='source':bad['effect_versions'][0]['source_version']='wrong'
        elif mutation=='head':bad['effect_heads'][0]['version_id']=bad['effect_versions'][0]['id']
        else:bad['commercial_versions'].pop()
        with pytest.raises(InvalidStorage,match=rule):validate(bad,source=g,referenced_rows=refs)
    with driver.session() as s:
        raw=s.company.raw
        insert(raw,'keys',rows['keys']);insert(raw,'effect_versions',rows['effect_versions'])
        # Reciprocal FK is deferred, so failure must be the actual COMMIT.
        with pytest.raises(sqlite3.IntegrityError):raw.execute('COMMIT')
        raw.execute('ROLLBACK');raw.execute('BEGIN IMMEDIATE')
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads'):insert(raw,name,rows[name])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources'):
            with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_'+name)
        # Existing history is untouched; N writes are private witness data only.


def test_persisted_g2_active_inactive_business_and_transition_anchors(client,sale,driver):
    body=additional_document(client,sale)
    post=driver.run('post',dict(operation_key='N-g2-post',document=body))
    driver.run('void',dict(operation_key='N-g2-void',deposit=post.current.id,expected_version=1),reason='N source witness')
    with driver.session() as s:g=adapters.graph(s,[post.current.id]);refs=references(s)
    rows=captured(g);assert len(rows['deposit_versions'])==2
    assert [v['signed_debit'] for v in rows['effect_versions']]==[1000,0]
    assert rows['effect_versions'][1]['transition_batch_id']!=rows['effect_versions'][1]['business_batch_id']
    validate(rows,source=g,referenced_rows=refs)
    with driver.session() as s:
        for name in ('keys','effect_versions','deposit_versions','effect_legs','effect_sources','effect_heads'):insert(s.company.raw,name,rows[name])
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]


def aggregate(rows,g,bank):
    """Hand-enumerated zero opening + one 10-unit statement, event and receipt."""
    rows=copy.deepcopy(rows);v=rows['effect_versions'][0];rev=g.by_id('transaction_revisions')[v['revision_id']]
    audit=rev['audit_event_id'];actor=rev['created_by'];op,event,draft,dr,opening,certificate,claim=[new_id() for _ in range(7)]
    created=dict(created_at=rev['created_at'],created_by=actor,created_via=rev['created_via'],audit_event_id=audit)
    prefs=dict(format=1,columns=['date','amount'],sort='date',descending=False,hide_after_date=True,view='as_certified')
    evidence=dict(format=1,statement_reference=None,entered_text='Independent statement')
    rows['drafts']=[dict(id=draft,account_id=bank,kind='statement',version=1,current_revision_id=dr,state='consumed',terminal_operation_id=op,**created)]
    rows['draft_revisions']=[dict(id=dr,draft_id=draft,account_id=bank,revision_number=1,previous_revision_id=None,header_snapshot=canonical(dict(format=1,opening_date=None,statement_date='2026-01-31',entered_balance=1000,evidence=evidence,preferences=prefs)),base_chain_version=0,base_opening_id=None,base_head_id=None,repair_of_opening_id=None,repair_of_certificate_id=None,**created)]
    pop=dict(format=1,account_id=bank,currency='USD',cutoff='2026-01-01',version_ids=[],signed_gl_total=0,source_fingerprint=digest([]))
    rows['openings']=[dict(id=opening,account_id=bank,generation=1,opening_date='2026-01-01',balance=0,currency='USD',predecessor_opening_id=None,origin_draft_revision_id=dr,evidence_snapshot=canonical(evidence),authorized_source_snapshot=canonical(pop),**created)]
    pop.update(cutoff='2026-01-31',version_ids=[v['id']],signed_gl_total=1000,source_fingerprint=population_fingerprint([v]))
    rows['certificates']=[dict(id=certificate,account_id=bank,generation=1,statement_date='2026-01-31',opening_id=opening,previous_certificate_id=None,supersedes_certificate_id=None,origin_draft_revision_id=dr,beginning_balance=0,ending_balance=1000,selected_sum=1000,original_difference=0,final_difference=0,positive_sum=1000,negative_sum=0,positive_count=1,negative_count=0,currency='USD',convention='bank',captured_source_snapshot=canonical(pop),issuer_snapshot=rev['issuer_snapshot'],**created)]
    rows['certificate_members']=[dict(certificate_id=certificate,account_id=bank,key_id=v['key_id'],version_id=v['id'],classification='selected',eligible_at_cutoff=1,ordinal=0)]
    opening_draft,opening_revision=new_id(),new_id()
    rows['drafts'].append(dict(rows['drafts'][0],id=opening_draft,kind='opening',current_revision_id=opening_revision))
    header=dict(format=1,opening_date='2026-01-01',statement_date=None,entered_balance=0,evidence=evidence,preferences=prefs)
    rows['draft_revisions'].append(dict(rows['draft_revisions'][0],id=opening_revision,draft_id=opening_draft,header_snapshot=canonical(header)))
    rows['openings'][0]['origin_draft_revision_id']=opening_revision
    rows['claims']=[dict(id=claim,account_id=bank,key_id=v['key_id'],version_id=v['id'],opening_id=None,certificate_id=certificate,event_id=event)]
    rows['current_members']=[dict(key_id=v['key_id'],claim_id=claim)]
    rows['accounts']=[dict(account_id=bank,currency='USD',convention='bank',version=1,opening_id=opening,head_certificate_id=certificate,last_event_id=event)]
    rows['active_certificates']=[dict(account_id=bank,statement_date='2026-01-31',certificate_id=certificate)]
    rows['events']=[dict(id=event,operation_id=op,audit_event_id=audit,actor_id=actor,principal_id=None,interface='python',recorded_at=rev['created_at'],reason='Private N witness',kind='finish',schema_version=1)]
    rows['event_accounts']=[dict(event_id=event,account_id=bank,before_chain_version=0,after_chain_version=1,before_opening_id=None,after_opening_id=opening,before_head_id=None,after_head_id=certificate)]
    targets=[dict(kind='accounts',id=bank),dict(kind='transactions',id=v['transaction_id']),dict(kind='drafts',id=draft),dict(kind='openings',id=opening),dict(kind='certificates',id=certificate),dict(kind='drafts',id=opening_draft)]
    manifests={k:dict(count=0,hash=digest([])) for k in ('request','effects','targets','generated')};manifests['targets']=dict(count=len(targets),hash=digest(targets))
    rows['operations']=[dict(id=op,operation_key='N-example',command='reconcile finish',request_schema_version=1,original_request_snapshot=canonical(dict(format=1,document=dict(draft=draft),canonical_intent=dict(draft=draft),collections=manifests)),canonical_intent_hash=digest(dict(draft=draft)),effect_schema_version=1,original_effect_snapshot=canonical(dict(format=1,document=dict(certificate=certificate),collections=manifests)),**created)]
    rows['operation_items']=[dict(operation_id=op,kind='targets',ordinal=i,facts_snapshot=canonical(t)) for i,t in enumerate(targets)]
    for t in targets:
        field=dict(accounts='account_id',transactions='transaction_id',drafts='draft_id',openings='opening_id',certificates='certificate_id')[t['kind']]
        rows['operation_'+t['kind']].append(dict(operation_id=op,**{field:t['id']}))
    previous={}
    for value in rows['effect_versions']:
        key=value['key_id']
        if value['source_audit_event_id']==audit:
            rows['event_effects'].append(dict(event_id=event,key_id=key,old_version_id=previous.get(key),new_version_id=value['id'],source_audit_event_id=audit))
        previous[key]=value['id']
    return rows


def test_nonempty_certificate_chain_claim_receipts_and_independent_mutations(client,driver):
    bank=account(client,'N certificate bank');equity=account(client,'N certificate equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    rows=aggregate(captured(g),g,bank)
    captures={v['id']:g for name in ('openings','certificates') for v in rows[name]}
    validate(rows,source=g,captured_graphs=captures,referenced_rows=refs)
    changes=[('balance',lambda x:x['certificates'][0].update(ending_balance=1001),'certificate_balance'),
        ('future',lambda x:x['certificate_members'][0].update(eligible_at_cutoff=0),'certificate_eligibility'),
        ('claim',lambda x:x['current_members'].clear(),'current_claims'),
        ('chain',lambda x:x['active_certificates'].clear(),'active_chain_projection'),
        ('receipt_tail',lambda x:x['operation_items'].pop(),'receipt_tail'),
        ('target',lambda x:x['operation_transactions'].clear(),'operation_target_coverage'),
        ('extra_snapshot',lambda x:x['draft_revisions'][0].update(header_snapshot=canonical(dict(json.loads(x['draft_revisions'][0]['header_snapshot']),ignored=True))),'snapshot_format')]
    for name,change,rule in changes:
        bad=copy.deepcopy(rows);change(bad)
        with pytest.raises(InvalidStorage,match=rule):validate(bad,source=g,captured_graphs=captures,referenced_rows=refs)
    # SQL independently admits the reciprocal graph and rejects unreleased removal.
    order=['keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','event_effects','draft_revisions','drafts','openings','certificates','certificate_members','event_accounts','accounts','active_certificates','claims','current_members','operation_items','operation_accounts','operation_transactions','operation_drafts','operation_openings','operation_certificates']
    with driver.session() as s:
        raw=s.company.raw
        # Operation target FKs resolve after draft/opening/certificate insertion.
        for name in order:insert(raw,name,rows[name])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_current_members')
        with pytest.raises(sqlite3.IntegrityError):raw.execute('UPDATE reconciliation_accounts SET version=version+1')
        with pytest.raises(sqlite3.IntegrityError):raw.execute('UPDATE reconciliation_certificates SET ending_balance=1001')


def staged(rows, g, bank, count=403):
    """A nonfinancial open draft with >200 immutable insertion seeds."""
    from datetime import date, timedelta
    rows=aggregate(rows,g,bank)
    for name in ('openings','certificates','certificate_members','claims','current_members','accounts','active_certificates','event_accounts'):
        rows[name]=[]
    removed=rows['drafts'][1]['id']
    rows['drafts']=rows['drafts'][:1];rows['draft_revisions']=rows['draft_revisions'][:1]
    rows['operation_drafts']=[v for v in rows['operation_drafts'] if v['draft_id']!=removed]
    rows['operation_items']=[v for v in rows['operation_items'] if json.loads(v['facts_snapshot'])['id']!=removed]
    rows['drafts'][0].update(state='open',terminal_operation_id=None)
    op=rows['operations'][0];draft=rows['drafts'][0];event=rows['events'][0]
    op['command']='reconcile attempt seal';event['kind']='bulk_stage'
    for name in ('operation_openings','operation_certificates'):rows[name]=[]
    items=[v for v in rows['operation_items'] if json.loads(v['facts_snapshot'])['kind'] not in ('openings','certificates')]
    for i,v in enumerate(items):v['ordinal']=i
    rows['operation_items']=items
    for field in ('original_request_snapshot','original_effect_snapshot'):
        snapshot=json.loads(op[field]);snapshot['collections']['targets']=dict(count=len(items),hash=digest([json.loads(v['facts_snapshot']) for v in items]));op[field]=canonical(snapshot)
    attempt=new_id();payloads=[]
    for i in range(count):
        payload=dict(account_id=bank,opening_id=None,certificate_id=None,kind='insert',date=(date(2027,1,1)+timedelta(days=i)).isoformat())
        payloads.append(dict(kind='seed',payload=payload))
        rows['attempt_seeds'].append(dict(attempt_id=attempt,ordinal=i,**payload))
        rows['attempt_items'].append(dict(attempt_id=attempt,ordinal=i,kind='seed',payload=canonical(payload),member_ordinal=None,seed_ordinal=i,certificate_ordinal=None,proposal_ordinal=None))
    rows['attempts']=[dict(id=attempt,draft_id=draft['id'],base_revision_id=draft['current_revision_id'],version=1,state='uploading',declared_count=count,intent_hash=digest(payloads),attempt_generation='explicit-generation',**{k:draft[k] for k in ('created_at','created_by','created_via','audit_event_id')})]
    rows['attempt_active']=[dict(draft_id=draft['id'],attempt_id=attempt)]
    for i,start in enumerate(range(0,count,200)):
        value=dict(format=1,items=payloads[start:start+200]);hash_=digest(value)
        rows['attempt_chunks'].append(dict(attempt_id=attempt,chunk_index=i,request_hash=hash_,original_chunk=canonical(value),receipt=canonical(dict(format=1,chunk_index=i,first_ordinal=start,count=len(value['items']),request_hash=hash_))))
    return rows


def test_actual_403_leaf_storage_chunks_barrier_and_deferred_subtype_mutations(client,driver):
    bank=account(client,'N attempt bank');equity=account(client,'N attempt equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    rows=staged(captured(g),g,bank)
    assert len(rows['attempt_items'])==403 and [json.loads(v['receipt'])['count'] for v in rows['attempt_chunks']]==[200,200,3]
    validate(rows,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['attempt_active']=[]
    with pytest.raises(InvalidStorage,match='attempt_barriers'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['attempt_chunks'].pop()
    with pytest.raises(InvalidStorage,match='chunk_item_coverage'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['attempt_seeds'][1]['date']=bad['attempt_seeds'][0]['date'];bad['attempt_items'][1]['payload']=bad['attempt_items'][0]['payload']
    with pytest.raises(InvalidStorage,match='attempt_duplicate_target'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['attempt_items'][0]['member_ordinal']=0
    with pytest.raises(InvalidStorage,match='foreign_owner|attempt_subtype_pointer'):validate(bad,source=g,referenced_rows=refs)
    sealed=copy.deepcopy(rows);sealed['attempts'][0].update(state='sealed',version=2)
    validate(sealed,source=g,referenced_rows=refs)
    bad=copy.deepcopy(sealed);bad['attempts'][0]['intent_hash']='0'*64
    with pytest.raises(InvalidStorage,match='attempt_hash'):validate(bad,source=g,referenced_rows=refs)
    with driver.session() as s:
        raw=s.company.raw
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','event_effects','draft_revisions','drafts','operation_items','operation_accounts','operation_transactions','operation_drafts','attempts','attempt_chunks','attempt_items'):insert(raw,name,rows[name])
        with pytest.raises(sqlite3.IntegrityError):raw.execute('COMMIT')
        # A failed deferred commit keeps the transaction open: supply missing
        # reciprocal leaves, then the real owning context COMMIT must succeed.
        insert(raw,'attempt_seeds',rows['attempt_seeds']);insert(raw,'attempt_active',rows['attempt_active'])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_attempt_active')
        with pytest.raises(sqlite3.IntegrityError):raw.execute("UPDATE reconciliation_attempts SET state='sealed',version=2")
        with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_attempt_chunks')


def test_receipt_whole_movement_and_no_claims_for_saved_draft_marks(client,sale,driver):
    bank=account(client,'N grouped receipt')
    doc=run(client,'sales-receipt post',dict(customer=sale['customer'],date='2026-01-10',deposit_to=bank,payment_method=__import__('tests.test_payment_receipts',fromlist=['method']).method(client),lines=[dict(item=sale['item'],unit_price='5'),dict(item=sale['item'],unit_price='5')]))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    base=captured(g)
    assert [v['signed_debit'] for v in base['effect_versions']]==[500,500]
    assert len({v['movement_snapshot'] for v in base['effect_versions']})==1
    rows=staged(base,g,bank,0);d=rows['drafts'][0]
    rows['draft_members']=[dict(revision_id=d['current_revision_id'],draft_id=d['id'],account_id=bank,key_id=v['key_id'],version_id=v['id'],action='mark',ordinal=i) for i,v in enumerate(base['effect_versions'])]
    validate(rows,source=g,referenced_rows=refs)
    assert rows['claims']==rows['current_members']==[]
    bad=copy.deepcopy(rows);bad['draft_members'].pop()
    with pytest.raises(InvalidStorage,match='incomplete_movement'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['draft_members'][0]['action']='invented'
    with pytest.raises(InvalidStorage,match='closed_discriminator'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['draft_members'][1]['action']='outstanding'
    with pytest.raises(InvalidStorage,match='split_movement'):validate(bad,source=g,referenced_rows=refs)


def test_proposal_exact_saved_fields_consumption_and_report_preset_versions(client,driver):
    bank=account(client,'N proposal bank');equity=account(client,'N proposal offset','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    rows=staged(captured(g),g,bank,0);d=rows['drafts'][0];op=rows['operations'][0]
    created={k:d[k] for k in ('created_at','created_by','created_via','audit_event_id')}
    proposal,revision,preset=[new_id() for _ in range(3)]
    body=dict(format=1,date='2026-01-10',amount_minor_units=1000,currency='USD',offset_account_id=equity,class_id=None,memo=None,number=None,reason=None)
    a=g.accounts[equity]
    rows['proposals']=[dict(id=proposal,draft_id=d['id'],role='earned_credit',current_revision_id=revision,version=1)]
    rows['proposal_revisions']=[dict(id=revision,proposal_id=proposal,draft_id=d['id'],revision_number=1,previous_revision_id=None,**{k:v for k,v in body.items() if k!='format'},input_snapshot=canonical(body),expected_account_facts=canonical(dict(format=1,account_id=equity,version=a['version'],type=a['type'],currency=a['currency'],active=bool(a['active']))),**created)]
    rows['draft_proposals']=[dict(draft_revision_id=d['current_revision_id'],draft_id=d['id'],proposal_id=proposal,proposal_revision_id=revision)]
    prefs=json.loads(draft_header(rows))['preferences']
    rows['report_presets']=[dict(id=preset,version=1,name='N retained report',account_id=bank,parameters_snapshot=canonical(prefs),**created)]
    rows['report_preset_revisions']=[dict(preset_id=preset,version=1,parameters_snapshot=canonical(prefs),audit_event_id=d['audit_event_id'])]
    validate(rows,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['proposal_revisions'][0]['amount_minor_units']=2000
    with pytest.raises(InvalidStorage,match='proposal_input'):validate(bad,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['report_presets'][0]['version']=2
    with pytest.raises(InvalidStorage,match='foreign_owner'):validate(bad,source=g,referenced_rows=refs)
    rows['proposal_consumptions']=[dict(proposal_id=proposal,proposal_revision_id=revision,operation_id=op['id'],journal_transaction_id=doc['id'],journal_revision_id=rows['effect_versions'][0]['revision_id'],journal_type='journal_entry')]
    validate(rows,source=g,referenced_rows=refs)
    bad=copy.deepcopy(rows);bad['proposals'][0]['role']='charge'
    with pytest.raises(InvalidStorage,match='generated_journal_amount'):validate(bad,source=g,referenced_rows=refs)
    with driver.session() as s:
        raw=s.company.raw
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','event_effects','draft_revisions','drafts','operation_items','operation_accounts','operation_transactions','operation_drafts','proposal_revisions','proposals','draft_proposals','proposal_consumptions','report_preset_revisions','report_presets'):insert(raw,name,rows[name])
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        with pytest.raises(sqlite3.IntegrityError):raw.execute('UPDATE reconciliation_proposals SET version=2')
        with pytest.raises(sqlite3.IntegrityError):raw.execute('UPDATE reconciliation_report_presets SET version=2')
        with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_proposal_consumptions')


def draft_header(rows):return rows['draft_revisions'][0]['header_snapshot']


def test_event_backed_release_retains_certificate_and_never_inverts_source(client,driver):
    bank=account(client,'N release bank');equity=account(client,'N release equity','equity')
    doc=journal(client,pair(bank,equity,'10'))
    with driver.session() as s:g=adapters.graph(s,[doc['id']]);refs=references(s)
    rows=aggregate(captured(g),g,bank);captures={v['id']:g for name in ('openings','certificates') for v in rows[name]}
    original=copy.deepcopy(rows);old_event=rows['events'][0];old_op=rows['operations'][0]
    next_audit=next(v['id'] for v in refs['audit_events'] if v['id']!=old_event['audit_event_id'])
    operation,event=new_id(),new_id()
    new_op=dict(old_op,id=operation,operation_key='N-explicit-release',command='reconcile undo',audit_event_id=next_audit)
    request=dict(account=bank,certificate=rows['certificates'][0]['id'],expected_chain_version=1)
    envelope=json.loads(new_op['original_request_snapshot']);envelope.update(document=request,canonical_intent=request)
    new_op.update(original_request_snapshot=canonical(envelope),canonical_intent_hash=digest(request))
    rows['operations'].append(new_op)
    rows['events'].append(dict(old_event,id=event,operation_id=operation,audit_event_id=next_audit,kind='undo'))
    for name in ('operation_items','operation_accounts','operation_transactions','operation_drafts','operation_openings','operation_certificates'):
        rows[name].extend(dict(v,operation_id=operation) for v in original[name])
    rows['event_accounts'].append(dict(original['event_accounts'][0],event_id=event,before_chain_version=1,after_chain_version=2,before_opening_id=rows['openings'][0]['id'],before_head_id=rows['certificates'][0]['id'],after_head_id=None))
    rows['releases']=[dict(id=new_id(),claim_id=rows['claims'][0]['id'],event_id=event)]
    rows['current_members']=[];rows['active_certificates']=[]
    rows['accounts'][0].update(version=2,last_event_id=event,head_certificate_id=None)
    validate(rows,source=g,captured_graphs=captures,referenced_rows=refs)
    assert rows['certificates']==original['certificates'] and rows['claims']==original['claims']
    with driver.session() as s:
        raw=s.company.raw
        order=['keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','event_effects','draft_revisions','drafts','openings','certificates','certificate_members','event_accounts','accounts','active_certificates','claims','current_members','operation_items','operation_accounts','operation_transactions','operation_drafts','operation_openings','operation_certificates']
        for name in order:insert(raw,name,original[name])
        for name in ('operations','events','operation_items','operation_accounts','operation_transactions','operation_drafts','operation_openings','operation_certificates','event_accounts'):
            insert(raw,name,rows[name][len(original[name]):])
        insert(raw,'releases',rows['releases'])
        raw.execute('DELETE FROM reconciliation_current_members')
        raw.execute('DELETE FROM reconciliation_active_certificates')
        raw.execute('UPDATE reconciliation_accounts SET version=2,last_event_id=?,head_certificate_id=NULL',(event,))
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert adapters.graph(s,[doc['id']]).rows==g.rows
        assert raw.execute('SELECT ending_balance FROM reconciliation_certificates').fetchall()==[(1000,)]
