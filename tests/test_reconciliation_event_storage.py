"""Real source transitions; independent contextual arithmetic, no report activation."""
import copy
import json
import sqlite3
import pytest
from bookflow.core.ids import new_id
from bookflow.company import reconciliation_adapters as adapters
from bookflow.company.reconciliation_storage_validation import validate, InvalidStorage, canonical, digest
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_service_sales_lifecycle import sale
from tests.test_reconciliation_adapters import account, journal, pair, run
from tests.test_reconciliation_storage_validation import captured, references, insert


def event_rows(rows,g):
    """Test-only event/operation envelopes over the actual immutable source audits."""
    rows=copy.deepcopy(rows);events={};previous={}
    for value in rows['effect_versions']:
        audit=value['source_audit_event_id']
        if audit not in events:
            operation,event=new_id(),new_id();events[audit]=(operation,event)
            rev=g.by_id('transaction_revisions')[value['revision_id']]
            created=dict(created_at=rev['created_at'],created_by=rev['created_by'],created_via=rev['created_via'],audit_event_id=audit)
            changed=[v for v in rows['effect_versions'] if v['source_audit_event_id']==audit]
            targets=[dict(kind='transactions',id=i) for i in sorted({v['transaction_id'] for v in changed})]
            targets += [dict(kind='accounts',id=i) for i in sorted({v['account_id'] for v in rows['effect_versions']})]
            manifests={k:dict(count=0,hash=digest([])) for k in ('request','effects','targets','generated')};manifests['targets']=dict(count=len(targets),hash=digest(targets))
            document=dict(source_audit_event_id=audit)
            rows['operations'].append(dict(id=operation,operation_key='event-'+audit,command='reconcile amendment apply',request_schema_version=1,original_request_snapshot=canonical(dict(format=1,document=document,canonical_intent=document,collections=manifests)),canonical_intent_hash=digest(document),effect_schema_version=1,original_effect_snapshot=canonical(dict(format=1,document=document,collections=manifests)),**created))
            rows['events'].append(dict(id=event,operation_id=operation,audit_event_id=audit,actor_id=rev['created_by'],principal_id=None,interface=rev['created_via'],recorded_at=rev['created_at'],reason='Private source-event storage witness',kind='amend',schema_version=1))
            for i,target in enumerate(targets):
                rows['operation_items'].append(dict(operation_id=operation,kind='targets',ordinal=i,facts_snapshot=canonical(target)))
                field='transaction_id' if target['kind']=='transactions' else 'account_id'
                rows['operation_'+target['kind']].append(dict(operation_id=operation,**{field:target['id']}))
        key=value['key_id'];event=events[audit][1]
        rows['event_effects'].append(dict(event_id=event,key_id=key,old_version_id=previous.get(key),new_version_id=value['id'],source_audit_event_id=audit))
        previous[key]=value['id']
    return rows


@pytest.fixture
def history(client,driver):
    a=account(client,'Event A');b=account(client,'Event B');equity=account(client,'Event equity','equity')
    inputs=[]
    body=dict(date='2026-01-10',lines=pair(a,equity,'10'));inputs.append(('journal post',body))
    doc=run(client,*inputs[-1]);transaction=doc['id']
    lines=[dict(account=b if v['side']=='debit' else equity,side=v['side'],amount='10',line_id=v['line_id']) for v in doc['revision']['lines']]
    changes=[dict(lines=lines),dict(date='2026-02-10'),dict(memo='Metadata only'),dict(lines=pair(a,equity,'10'))]
    for version,change in enumerate(changes,1):
        body=dict(journal=transaction,expected_version=version,**change);inputs.append(('journal update',body));doc=run(client,*inputs[-1])
    inputs.append(('journal void',dict(journal=transaction,expected_version=5)));run(client,*inputs[-1])
    with driver.session() as s:g=adapters.graph(s,[transaction]);refs=references(s)
    rows=event_rows(captured(g),g)
    return rows,g,refs,a,b,inputs


def test_complete_real_creation_account_date_metadata_removal_void_and_contexts(history,driver,tmp_path):
    rows,g,refs,a,b,inputs=history
    validate(rows,source=g,referenced_rows=refs)
    values=rows['effect_versions'];links=rows['event_effects']
    # One original key: create, account move, date move, metadata, inactive,
    # then another inactive void. A new key is born at replacement, then voided.
    original=[v for v in values if v['key_id']==values[0]['key_id']]
    assert len(original)==6 and len(values)==len(links)==8
    assert [v['active'] for v in original]==[1,1,1,1,0,0]
    assert [v['signed_debit'] for v in original]==[1000,1000,1000,1000,0,0]
    assert len([v for v in links if v['old_version_id'] is None])==2
    assert all(v['new_version_id'] for v in links)
    assert all('cause' not in v and 'local_signed_impact' not in v for v in links)
    def amount(v,account,cutoff):
        return v['signed_debit'] if v is not None and v['active'] and v['account_id']==account and v['effective_date']<=cutoff else 0
    def delta(old,new,account,cutoff):return amount(new,account,cutoff)-amount(old,account,cutoff)
    create,move,dated,metadata,removed,void=original
    assert delta(None,create,a,'2026-01-31')==1000
    assert (delta(create,move,a,'2026-01-31'),delta(create,move,b,'2026-01-31'))==(-1000,1000)
    assert (delta(move,dated,b,'2026-01-31'),delta(move,dated,b,'2026-02-28'))==(-1000,0)
    assert delta(dated,metadata,b,'2026-02-28')==0 and dated['id']!=metadata['id']
    assert delta(metadata,removed,b,'2026-02-28')==-1000
    assert delta(removed,void,b,'2026-02-28')==0 and removed['id']!=void['id']
    # A certificate captured this key at1000. B captured no selected keys.
    # At the move snapshot A's certificate impact is−1000, B's is0, although
    # the EVENT has B +1000. No simultaneous active claims are manufactured.
    a_certificate_impact=amount(move,a,'2026-01-31')-1000
    b_certificate_impact=sum([])-0
    assert (a_certificate_impact,b_certificate_impact)==(-1000,0)
    assert rows['claims']==rows['current_members']==[]
    with driver.session() as s:
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','operation_items','operation_transactions','operation_accounts','event_effects'):insert(s.company.raw,name,rows[name])
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert adapters.graph(s,[values[0]['transaction_id']]).rows==g.rows
        cursor=s.company.raw.execute('SELECT * FROM reconciliation_effect_versions ORDER BY rowid')
        stored=[dict(zip([c[0] for c in cursor.description],v)) for v in cursor.fetchall()]
        assert stored==values
        persisted={v['id']:v for v in stored}
        create,move,dated,metadata,removed,void=[persisted[v['id']] for v in original]
        assert (delta(create,move,a,'2026-01-31'),delta(create,move,b,'2026-01-31'))==(-1000,1000)
        assert (delta(move,dated,b,'2026-01-31'),delta(move,dated,b,'2026-02-28'))==(-1000,0)
        assert delta(dated,metadata,b,'2026-02-28')==0

    (tmp_path/'event-oracles.json').write_text(json.dumps(dict(inputs=inputs,versions=values,links=links,event_account_move=[-1000,1000],date_cutoffs=[-1000,0],certificate_impacts=[-1000,0]),indent=2))


def test_predecessor_causality_and_complete_event_coverage_mutations(history):
    rows,g,refs,*_=history
    validate(rows,source=g,referenced_rows=refs)
    links=rows['event_effects'];oldkey=links[0]['key_id'];chain=[v for v in links if v['key_id']==oldkey]
    mutations=[]
    bad=copy.deepcopy(rows);bad['event_effects'].remove(next(v for v in bad['event_effects'] if v['new_version_id']==chain[2]['new_version_id']));mutations.append((bad,'event_transition_coverage'))
    bad=copy.deepcopy(rows);event=links[-1]['event_id'];bad['event_effects']=[v for v in bad['event_effects'] if v['event_id']!=event];mutations.append((bad,'event_transition_coverage'))
    bad=copy.deepcopy(rows);next(v for v in bad['event_effects'] if v['new_version_id']==chain[2]['new_version_id'])['old_version_id']=chain[0]['new_version_id'];mutations.append((bad,'event_predecessor'))
    bad=copy.deepcopy(rows);bad['event_effects'][1]['old_version_id']=None;mutations.append((bad,'event_predecessor'))
    bad=copy.deepcopy(rows);bad['event_effects'][0]['old_version_id']=links[0]['new_version_id'];mutations.append((bad,'event_predecessor'))
    bad=copy.deepcopy(rows);bad['event_effects'][1]['source_audit_event_id']=links[0]['source_audit_event_id'];mutations.append((bad,'event_causality'))
    bad=copy.deepcopy(rows);bad['event_effects'][1]['event_id']=links[0]['event_id'];bad['event_effects'].pop(0);mutations.append((bad,'event_causality'))
    bad=copy.deepcopy(rows);bad['event_effects'].append(copy.deepcopy(links[0]));mutations.append((bad,'duplicate_primary_key'))
    bad=copy.deepcopy(rows);bad['event_effects'][0]['new_version_id']=None;mutations.append((bad,'null_required'))
    bad=copy.deepcopy(rows);operation=bad['events'][0]['operation_id']
    bad['operation_transactions']=[v for v in bad['operation_transactions'] if v['operation_id']!=operation]
    bad['operation_items']=[v for v in bad['operation_items'] if not (v['operation_id']==operation and v['kind']=='targets' and json.loads(v['facts_snapshot'])['kind']=='transactions')]
    targets=[v for v in bad['operation_items'] if v['operation_id']==operation and v['kind']=='targets']
    for i,v in enumerate(targets):v['ordinal']=i
    op=next(v for v in bad['operations'] if v['id']==operation)
    for field in ('original_request_snapshot','original_effect_snapshot'):
        envelope=json.loads(op[field]);envelope['collections']['targets']=dict(count=len(targets),hash=digest([json.loads(v['facts_snapshot']) for v in targets]));op[field]=canonical(envelope)
    mutations.append((bad,'event_source_target'))
    bad=copy.deepcopy(rows);event=bad['events'].pop()['id'];bad['event_effects']=[v for v in bad['event_effects'] if v['event_id']!=event];mutations.append((bad,'operation_event_coverage'))
    for bad,rule in mutations:
        with pytest.raises(InvalidStorage,match=rule):validate(bad,source=g,referenced_rows=refs)
    validate(rows,source=g,referenced_rows=refs)


def test_actual_composite_foreign_keys_causal_guard_notnull_and_event_key_uniqueness(history,driver):
    rows,g,refs,*_=history
    by_event={}
    for v in rows['event_effects']:by_event.setdefault(v['event_id'],[]).append(v)
    siblings=next(v for v in by_event.values() if len(v)==2)
    x,y=siblings
    with driver.session() as s:
        raw=s.company.raw
        for name in ('keys','effect_versions','commercial_versions','effect_legs','effect_sources','effect_heads','operations','events','operation_items','operation_transactions','operation_accounts'):insert(raw,name,rows[name])
        wrong_new=dict(x,new_version_id=y['new_version_id'])
        wrong_old=dict(x,old_version_id=y['new_version_id'])
        for row in (wrong_new,wrong_old,dict(x,new_version_id=new_id())):
            with pytest.raises(sqlite3.IntegrityError,match='FOREIGN KEY'):insert(raw,'event_effects',[row])
        with pytest.raises(sqlite3.IntegrityError,match='NOT NULL'):insert(raw,'event_effects',[dict(x,old_version_id=None,new_version_id=None)])
        with pytest.raises(sqlite3.IntegrityError,match='invalid reconciliation storage transition'):
            insert(raw,'event_effects',[dict(x,source_audit_event_id=rows['event_effects'][0]['source_audit_event_id'])])
        insert(raw,'event_effects',rows['event_effects'])
        with pytest.raises(sqlite3.IntegrityError,match='UNIQUE'):insert(raw,'event_effects',[x])
        with pytest.raises(sqlite3.IntegrityError):raw.execute('DELETE FROM reconciliation_event_effects')
        assert raw.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_persisted_g2_metadata_and_inactive_transition_causality(client,sale,driver):
    body=additional_document(client,sale)
    post=driver.run('post',dict(operation_key='event-G2-post',document=body))
    update=replacement(post,body);update['memo']='G2 metadata transition'
    driver.run('update',dict(operation_key='event-G2-update',deposit=post.current.id,expected_version=1,document=update),reason='Correct captured memo')
    driver.run('void',dict(operation_key='event-G2-void',deposit=post.current.id,expected_version=2),reason='Void for event witness')
    with driver.session() as s:g=adapters.graph(s,[post.current.id]);refs=references(s)
    rows=event_rows(captured(g),g)
    validate(rows,source=g,referenced_rows=refs)
    values=rows['effect_versions'];assert len(values)==3
    assert [v['signed_debit'] for v in values]==[1000,1000,0]
    assert values[-1]['business_batch_id']==values[-2]['business_batch_id']
    assert values[-1]['transition_batch_id']!=values[-1]['business_batch_id']
    assert rows['event_effects'][-1]['old_version_id']==values[-2]['id']
    assert rows['event_effects'][-1]['source_audit_event_id']==values[-1]['source_audit_event_id']!=values[-2]['source_audit_event_id']
    assert [v['source_version'] for v in values]==[v['bank_version_id'] for v in rows['deposit_versions']]
    with driver.session() as s:
        for name in ('keys','effect_versions','deposit_versions','effect_legs','effect_sources','effect_heads','operations','events','operation_items','operation_transactions','operation_accounts','event_effects'):insert(s.company.raw,name,rows[name])
        assert s.company.raw.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert adapters.graph(s,[post.current.id]).rows==g.rows
