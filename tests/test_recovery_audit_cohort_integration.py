"""Independently enumerated recovery ownership; malformed rows are pure seams."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from bookflow.company import payment_authority as pa, schema as c
from bookflow.core import publication_payment as pp
from bookflow import BookflowError
from tests.test_audit_authority_batching import loaded, outcome

STATES=('uploading','sealed','applied','aborted','superseded')
ACTIONS=('set','remove','calculate')
SAVED={'saved-old','saved-removed'}
FUNDING={'funding-old','funding-new'}
ATTEMPTS={f'{state}-{action}' for state in STATES for action in ACTIONS}
CONSUMED={'operation-primary','operation-secondary','operation-no-effect'}
EXPECTED=SAVED|FUNDING|ATTEMPTS|CONSUMED
ROOTS=(('payment_selection','S'),('payment_selection_revision','rev-old'),('payment_selection_item','item-old'),
       *((kind,identifier) for state in STATES for kind,identifier in (
           ('payment_selection_recovery','R-'+state),('payment_selection_recovery_chunk','chunk-'+state),
           ('payment_selection_recovery_item','attempt-'+state+'-set'))),
       ('payment_selection_recovery_active','S'))


def graph(*, active='present', fanout=0):
    extra={f'fanout-{i:04}' for i in range(fanout)}
    rows={
        'payment_selections':[dict(id='S',consumed_operation_id='operation')],
        'payment_selection_revisions':[dict(id='rev-old',selection_id='S',context_snapshot='{"payment_id":"funding-old"}'),
            dict(id='rev-new',selection_id='S',context_snapshot='{"payment_id":"funding-new"}')],
        'payment_selection_items':[dict(id='item-old',selection_id='S',invoice_id='saved-old'),dict(id='item-removed',selection_id='S',invoice_id='saved-removed')],
        'payment_selection_recoveries':[dict(id='R-'+state,selection_id='S',state=state) for state in STATES],
        'payment_selection_recovery_chunks':[dict(id='chunk-'+state,selection_id='S',recovery_id='R-'+state) for state in STATES],
        'payment_selection_recovery_items':[dict(id='attempt-'+state+'-'+action,selection_id='S',recovery_id='R-'+state,invoice_id=state+'-'+action,action=action) for state in STATES for action in ACTIONS]+
            [dict(id='attempt-'+identifier,selection_id='S',recovery_id='R-aborted',invoice_id=identifier,action='remove') for identifier in sorted(extra)],
        'payment_selection_recovery_active':[] if active=='removed' else [dict(selection_id='S',recovery_id='R-uploading' if active=='present' else 'R-sealed')],
        'payment_operations':[dict(id='operation',request_snapshot=json.dumps({'resolved_transaction_ids':sorted(CONSUMED)}))],
        'payment_operation_items':[dict(id='operation-item',operation_id='operation')],
        'notes':[dict(id='note',record_type='payment_selection_recovery_active',record_id='S')],
        'attachment_links':[dict(id='link',attachment_id='file',record_type='payment_selection_recovery_item',record_id='attempt-uploading-set')],
        'transactions':[dict(id=x) for x in sorted(EXPECTED|extra)],
        'applications':[], 'work_billing_allocations':[],
    }
    roots=(*ROOTS,('note','note'),('attachment','file'))
    rows['audit_entries']=[dict(event_id=f'event-{i}',record_type=kind,record_id=identifier) for i,(kind,identifier) in enumerate(roots)]
    rows['audit_entries'] += [dict(event_id='mixed',record_type=k,record_id=i) for k,i in (*roots,('payment_operation_item','operation-item'),ROOTS[0])]
    return rows,roots,EXPECTED|extra


@pytest.mark.parametrize('active',['present','removed','replaced'])
def test_every_root_full_scalar_cohort_and_event_sets(monkeypatch,tmp_path,active):
    rows,roots,expected=graph(active=active);loaded(monkeypatch,rows)
    events=[f'event-{i}' for i in range(len(roots))]+['mixed'];reader=pa._EventCohort(None,events)
    receipt=[]
    for index,root in enumerate(roots):
        scalar=pa.record_transactions(None,*root);node=reader._walk(root);event=reader.resolved[f'event-{index}']
        assert scalar==node==event==expected
        assert reader.requirements(f'event-{index}')==pa.event_requirements(None,f'event-{index}')==(('ledger.read','member'),)
        receipt.append(dict(root=root,scalar=sorted(scalar),node=sorted(node),event=sorted(event)))
    assert reader.resolved['mixed']==expected and len(reader.entries['mixed'])==len(roots)+2
    assert len(receipt)==21 and expected==EXPECTED
    (tmp_path/'full-graphs.json').write_text(json.dumps(dict(active=active,expected=sorted(expected),roots=receipt,mixed=sorted(reader.resolved['mixed'])),indent=2))


@pytest.mark.parametrize('sole_work',sorted(EXPECTED))
def test_each_dependency_is_the_only_work_path(monkeypatch,sole_work):
    rows,roots,expected=graph(active='removed');rows['work_billing_allocations']=[dict(transaction_id=sole_work)]
    loaded(monkeypatch,rows);events=[f'event-{i}' for i in range(len(roots))];reader=pa._EventCohort(None,events)
    calls=[]
    def deny(s,resource,role):
        calls.append((resource,role))
        if resource=='customer-work':raise BookflowError('E_PERMISSION',details={'reason':'sole_work'})
    monkeypatch.setattr(pa,'require_resource',deny)
    for event in events:
        assert reader.resolved[event]==expected
        assert reader.requirements(event)==pa.event_requirements(None,event)==(('ledger.read','member'),('customer-work','member'))
        calls.clear();old=outcome(lambda:pa.authorize_event(SimpleNamespace(company=None),event));old_calls=calls[:];calls.clear()
        new=outcome(lambda:pa.authorize_events(SimpleNamespace(company=None),[event]))
        assert old==new==('error','E_PERMISSION',{'reason':'sole_work'})
        assert calls==old_calls==[('ledger.read','member'),('customer-work','member')]


@pytest.mark.parametrize('shape',['missing-selection','missing-operation','malformed-operation','malformed-context','missing-recovery'])
def test_errors_remain_root_local_with_original_order(monkeypatch,tmp_path,shape):
    rows,roots,expected=graph(active='removed')
    root=('payment_selection_recovery_active','S')
    if shape=='missing-selection':rows['payment_selections']=[]
    if shape=='missing-operation':rows['payment_operations']=[]
    if shape=='malformed-operation':rows['payment_operations'][0]['request_snapshot']='{"resolved_transaction_ids":[null]}'
    if shape=='malformed-context':rows['payment_selection_revisions'][0]['context_snapshot']='{}'
    if shape=='missing-recovery':root=('payment_selection_recovery','missing')
    rows['audit_entries']=[dict(event_id='bad',record_type=root[0],record_id=root[1]),dict(event_id='good',record_type='customer',record_id='ordinary')]
    loaded(monkeypatch,rows);reader=pa._EventCohort(None,['good','bad'])
    scalar=outcome(lambda:pa.event_requirements(None,'bad'));cohort=outcome(lambda:reader.requirements('bad'))
    assert scalar==cohort==('error','E_PERMISSION',{'reason':'unresolved_payment_evidence'})
    assert reader.resolved['good']==set() and reader.requirements('good')==()
    (tmp_path/'errors.json').write_text(json.dumps(dict(shape=shape,scalar=scalar,cohort=cohort)))


def test_full_403_fanout_through_real_bounded_sql(monkeypatch,tmp_path):
    rows,roots,expected=graph(active='removed',fanout=403)
    # Ordinary relational fact tables in an isolated in-memory connection. No
    # Bookflow company is edited, and no storage guard is disabled.
    columns={
        'audit_entries':('event_id','record_type','record_id'),
        'payment_selections':('id','consumed_operation_id'),
        'payment_selection_revisions':('id','selection_id','context_snapshot'),
        'payment_selection_items':('id','selection_id','invoice_id'),
        'payment_selection_recoveries':('id','selection_id','state'),
        'payment_selection_recovery_chunks':('id','selection_id','recovery_id'),
        'payment_selection_recovery_items':('id','selection_id','invoice_id','recovery_id','action'),
        'payment_selection_recovery_active':('selection_id','recovery_id'),
        'payment_operations':('id','request_snapshot'),
        'payment_operation_items':('id','operation_id'),
        'notes':('id','record_type','record_id'),
        'attachment_links':('id','attachment_id','record_type','record_id'),
        'transactions':('id',),'applications':('paying_transaction_id','paid_transaction_id'),
        'work_billing_allocations':('id','transaction_id'),
    }
    engine=sa.create_engine('sqlite://');binds=[]
    with engine.begin() as connection:
        for table in columns:
            fields=tuple(c.metadata.tables[table].c.keys())
            connection.exec_driver_sql('CREATE TABLE '+table+' ('+','.join(f+' TEXT' for f in fields)+')')
            if rows[table]:connection.exec_driver_sql('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in fields)+')',[tuple(r.get(f) for f in fields) for r in rows[table]])
        db=SimpleNamespace(conn=connection)
        original=pa._EventCohort._read;reads=[]
        def measured(self,table,field,ids,fields):
            ids=list(ids);result=original(self,table,field,ids,fields)
            reads.append(dict(table=table.name,field=field,input_count=len(set(ids)),rows=sum(map(len,result.values()))))
            return result
        monkeypatch.setattr(pa._EventCohort,'_read',measured)
        def trace(conn,cursor,statement,parameters,context,many):binds.append(len(parameters))
        sa.event.listen(connection,'before_cursor_execute',trace)
        reader=pa._EventCohort(db,['mixed'])
        sa.event.remove(connection,'before_cursor_execute',trace)
        assert reader.resolved['mixed']==expected and len(expected)==len(EXPECTED)+403
        for root in roots:assert reader._walk(root)==pa.record_transactions(db,*root)==expected
        assert max(binds)<=200 and any(n==200 for n in binds)
        assert reader.requirements('mixed')==pa.event_requirements(db,'mixed')==(('ledger.read','member'),)
        assert any(r['table']=='payment_selection_recovery_items' and r['rows']==418 for r in reads)
        (tmp_path/'fanout.json').write_text(json.dumps(dict(expected=sorted(expected),resolved=sorted(reader.resolved['mixed']),reads=reads,bindings=binds),indent=2))
    engine.dispose()


def test_query_epoch_is_an_other_boundary_between_equal_transactions(monkeypatch):
    from bookflow.company import payment_recovery
    calls=[]
    monkeypatch.setattr(pa,'authorize_publication_transactions',lambda s,ids:calls.append(('transactions',list(ids))))
    monkeypatch.setattr(payment_recovery,'query_epoch',lambda s:calls.append(('epoch',)) or 7)
    pp.check(None,[('transaction','a',False),('payment_selection_query_epoch','7',False),('transaction','a',True)])
    assert calls==[('transactions',[('a',False)]),('epoch',),('transactions',[('a',True)])]
    calls.clear()
    with pytest.raises(BookflowError) as caught:pp.check(None,[('transaction','a',False),('payment_selection_query_epoch','6',False),('transaction','a',True)])
    assert caught.value.code=='E_QUERY_STALE' and calls==[('transactions',[('a',False)]),('epoch',)]
