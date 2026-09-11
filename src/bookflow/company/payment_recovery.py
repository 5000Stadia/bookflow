"""Durable complete-intent recovery on one shared, nonfinancial draft identity."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, payment_selection as selection, payment_queries as q
from bookflow.company import payment_calculations as calc, payment_authority as authority
from bookflow.company import document_effects as effects
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
from bookflow.company.payment_recovery_models import RecoveryEntry
from bookflow.company.payment_recovery_outputs import RecoveryWriteOutput, RecoveryOutput, ComparisonOutput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Plan, Applied, Touched, MatchedRecovery
from bookflow.hub.users import common

R = c.payment_selection_recoveries
I = c.payment_selection_recovery_items
C = c.payment_selection_recovery_chunks
A = c.payment_selection_recovery_active


def fail(code, **details):
    raise BookflowError(code, details=details)


def find(s, recovery_id=None, recovery_key=None):
    rows = effects.rows(s, R, R.c.id == recovery_id if recovery_id else R.c.recovery_key == recovery_key)
    return rows[0] if rows else None


def resolve(s, recovery_id=None, recovery_key=None):
    row = find(s, recovery_id, recovery_key)
    if row is None:
        fail('E_RECORD_NOT_FOUND', record_type='payment_selection_recovery')
    authorize_selection(s, row['selection_id'])
    return row


def authorize_selection(s, identifier, write=False):
    authority.authorize(s, authority.record_transactions(s.company, 'payment_selection', identifier), write=write)


def active(s, identifier):
    pointers = effects.rows(s, A, A.c.selection_id == identifier)
    live = effects.rows(s, R, sa.and_(R.c.selection_id == identifier, R.c.state.in_(['uploading','sealed'])))
    if len(live) != len(pointers) or len(live) > 1 or (live and live[0]['id'] != pointers[0]['recovery_id']):
        fail('E_RECOVERY_PENDING', reason='inconsistent_recovery_barrier')
    return live[0] if live else None


def require_no_active_recovery(s, identifier):
    authorize_selection(s, identifier, write=True)
    row = active(s, identifier)
    if row:
        fail('E_RECOVERY_PENDING', selection_id=identifier, recovery_id=row['id'], remedy='payment recovery show')


def counts(s, row):
    received = s.company.conn.execute(sa.select(sa.func.count()).select_from(I).where(I.c.recovery_id == row['id'])).scalar_one()
    chunks = s.company.conn.execute(sa.select(sa.func.count()).select_from(C).where(C.c.recovery_id == row['id'])).scalar_one()
    return received, (row['declared_entry_count'] + 199)//200 - chunks


def lifecycle(s, identifier):
    authorize_selection(s, identifier)
    header=selection.resolve(s,identifier)
    return lifecycles(s,[header])[identifier]


def lifecycles(s,headers):
    """Batch the current projection for a preauthorized page, without a stored cache."""
    ids=[header['id'] for header in headers]
    if not ids:
        return {}
    pointers={row['selection_id']:row['recovery_id'] for row in s.company.conn.execute(sa.select(A).where(A.c.selection_id.in_(ids))).mappings()}
    live={}
    for row in s.company.conn.execute(sa.select(R).where(R.c.selection_id.in_(ids),R.c.state.in_(['uploading','sealed']))).mappings():
        if row['selection_id'] in live:
            fail('E_RECOVERY_PENDING',reason='inconsistent_recovery_barrier')
        live[row['selection_id']]=dict(row)
    if {key:row['id'] for key,row in live.items()}!=pointers:
        fail('E_RECOVERY_PENDING',reason='inconsistent_recovery_barrier')
    received={}
    if live:
        received=dict(s.company.conn.execute(sa.select(I.c.recovery_id,sa.func.count()).where(I.c.recovery_id.in_(pointers.values())).group_by(I.c.recovery_id)).all())
    opids=[header['consumed_operation_id'] for header in headers if header['consumed_operation_id']]
    operations={row['id']:dict(row) for row in s.company.conn.execute(sa.select(c.payment_operations).where(c.payment_operations.c.id.in_(opids))).mappings()} if opids else {}
    if operations:
        related=set()
        for row in operations.values():
            related.update(json.loads(row['request_snapshot'])['resolved_transaction_ids'])
        authority.authorize(s,related)
    results={}
    for header in headers:
        identifier=header['id'];row=live.get(identifier)
        result=dict(state='open',selection_id=identifier,selection_version=header['version'],selection_revision_id=header['current_revision_id'])
        if row:
            result.update(state='recovery_uploading' if row['state']=='uploading' else 'recovery_review',
                recovery_id=row['id'],attempt_generation=row['attempt_generation'],recovery_version=row['version'],
                received_entry_count=received.get(row['id'],0),declared_entry_count=row['declared_entry_count'])
        if header['state']=='consumed':
            if row or header['consumed_operation_id'] not in operations:
                fail('E_RECOVERY_PENDING',reason='inconsistent_consumed_selection')
            op=operations[header['consumed_operation_id']]
            effect=json.loads(op['effect_snapshot'])
            result.update(state='consumed',consumed_operation=dict(id=op['id'],operation_key=op['operation_key'],command=op['command'],payment_id=effect.get('id')))
        results[identifier]=result
    return results


def show_row(s, row):
    received, missing = counts(s,row)
    result = {k:row[k] for k in ('id','selection_id','recovery_key','state','version','attempt_generation','intent_hash',
        'local_baseline_revision_id','anchor_revision_id','anchor_selection_version','declared_entry_count','applied_revision_id')}
    result.update(header_intent=json.loads(row['header_intent']), received_entry_count=received, missing_chunk_count=missing,
        current=lifecycle(s,row['selection_id']))
    for action in ('begin','seal','terminal'):
        raw=row[action+'_receipt_snapshot']
        result[action+'_receipt'] = json.loads(raw) if raw else None
    return RecoveryOutput(**result)


def authorize_input(inp, ctx, s, write=True):
    identifiers = set()
    if getattr(inp,'selection',None):
        identifiers.add(inp.selection)
    if getattr(inp,'recovery_id',None) or getattr(inp,'recovery_key',None):
        row=find(s,getattr(inp,'recovery_id',None),getattr(inp,'recovery_key',None))
        if row:
            identifiers.add(row['selection_id'])
    if getattr(inp,'replacement',None):
        identifiers.add(inp.replacement.selection)
        old=find(s,recovery_key=inp.replacement.recovery_key)
        if old:
            identifiers.add(old['selection_id'])
    for identifier in identifiers:
        authorize_selection(s,identifier,write)
    authority.authorize(s,[entry.invoice_id for entry in getattr(inp,'entries',[])],write=write)


def request(inp,ctx,command):
    return dict(request_schema_version=1,command=command,input=inp.model_dump(mode='json',exclude_unset=True),
        context=dict(reason_present='reason' in ctx.model_fields_set,reason=ctx.reason))


def matched(inp,ctx,s,verb):
    """Check immutable action identity under current graph authority, before maintenance."""
    authorize_input(inp,ctx,s)
    row=find(s,recovery_key=inp.recovery_key) if verb=='begin' else find(s,recovery_id=inp.recovery_id)
    if not row:
        return None
    field='begin' if verb=='begin' else 'seal' if verb=='seal' else 'terminal'
    source=row
    if verb=='upload':
        found=effects.rows(s,C,sa.and_(C.c.recovery_id==row['id'],C.c.chunk_index==inp.chunk_index))
        if not found:
            return None
        source=found[0]
        hash_field,receipt_field='request_hash','receipt_snapshot'
    else:
        hash_field,receipt_field=field+'_request_hash',field+'_receipt_snapshot'
    if source[receipt_field] is None:
        return None
    receipt=json.loads(source[receipt_field])
    digest=q.digest(request(inp,ctx,'payment recovery '+verb))
    if receipt['action'] != verb:
        fail('E_RECOVERY_FINALIZED',recovery_id=row['id'])
    if source[hash_field] != digest:
        fail('E_RECOVERY_KEY_REUSED',recovery_id=row['id'])
    return RecoveryWriteOutput(original_receipt=receipt,current=lifecycle(s,row['selection_id']),changed=False,idempotent_replay=True)


def recover(inp,ctx,s,verb,cmd):
    from bookflow.core.dispatch import authorize
    ctx=authorize(cmd,ctx,s,read_only=True)
    result=matched(inp,ctx,s,verb)
    return MatchedRecovery(result) if result else None


def replay(inp,ctx,s,hit,verb):
    result=matched(inp,ctx,s,verb)
    if result is None:
        fail('E_RECOVERY_KEY_REUSED')
    return result.model_dump(mode='json')


def revision_by_id(s,header,identifier):
    rows=effects.rows(s,c.payment_selection_revisions,sa.and_(c.payment_selection_revisions.c.id==identifier,
        c.payment_selection_revisions.c.selection_id==header['id']))
    if not rows:
        fail('E_VALIDATION',reason='revision_must_belong_to_selection')
    return selection.saved(s,header,rows[0]['version'])


def manifest(row,entries):
    return dict(domain='bookflow.payment.recovery.intent',format=1,selection=row['selection_id'],
        local_baseline_revision=row['local_baseline_revision_id'],anchor_revision=row['anchor_revision_id'],
        attempt_generation=row['attempt_generation'],header_intent=json.loads(row['header_intent']),entries=entries)


def entries(s,row):
    return [entry_payload(item) for item in effects.rows(s,I,I.c.recovery_id==row['id'],)]


def entry_payload(item):
    value={key:item[key] for key in ('invoice_id','observed_invoice_version','action')}
    if item['action']=='set':
        value.update({key:item[key] for key in ('amount_minor_units','currency','amount_origin')})
        if item['retained_calculation_revision_id']:
            value['retained_calculation_revision_id']=item['retained_calculation_revision_id']
    elif item['action']=='calculate' and item['attempted_calculated_minor_units'] is not None:
        value['attempted_calculated_minor_units']=item['attempted_calculated_minor_units']
    return value


def sealed_entries(s,row):
    received,missing=counts(s,row)
    if received != row['declared_entry_count'] or missing:
        fail('E_RECOVERY_INCOMPLETE',recovery_id=row['id'],received_entry_count=received,declared_entry_count=row['declared_entry_count'])
    stored=s.company.conn.execute(sa.select(I).where(I.c.recovery_id==row['id']).order_by(I.c.entry_index)).mappings().all()
    if [item['entry_index'] for item in stored] != list(range(1,received+1)):
        fail('E_RECOVERY_INCOMPLETE',reason='ranges')
    chunks=s.company.conn.execute(sa.select(C.c.request_snapshot).where(C.c.recovery_id==row['id']).order_by(C.c.chunk_index)).scalars()
    values=[entry for raw in chunks for entry in json.loads(raw)['input']['entries']]
    if len(values)!=len(stored) or any(RecoveryEntry.model_validate(value).model_dump()!=RecoveryEntry.model_validate(entry_payload(item)).model_dump() for value,item in zip(values,stored)):
        fail('E_RECOVERY_INCOMPLETE',reason='stored_intent_mismatch')
    ids=[entry['invoice_id'] for entry in values]
    if ids!=sorted(set(ids)) or q.digest(manifest(row,values)) != row['intent_hash']:
        fail('E_RECOVERY_INCOMPLETE',reason='intent_hash_or_order')
    return values


def require_current(s,row,version=None,sealed=False):
    current=active(s,row['selection_id'])
    if current is None or current['id'] != row['id'] or row['state'] not in ('uploading','sealed'):
        fail('E_RECOVERY_FINALIZED',recovery_id=row['id'])
    if version is not None and row['version'] != version:
        fail('E_VERSION_CONFLICT',recovery_id=row['id'],current_version=row['version'])
    if sealed and row['state'] != 'sealed':
        fail('E_RECOVERY_INCOMPLETE',recovery_id=row['id'])
    header=selection.resolve(s,row['selection_id'])
    if header['state']!='open' or header['version']!=row['anchor_selection_version'] or header['current_revision_id']!=row['anchor_revision_id']:
        fail('E_VERSION_CONFLICT',selection_id=header['id'])
    return header


def begin_row(s,inp,ctx,at,event,identifier,allow_active=False):
    header=selection.resolve(s,inp.selection)
    authorize_selection(s,header['id'],True)
    if not allow_active:
        require_no_active_recovery(s,header['id'])
    if header['state']!='open':
        fail('E_SELECTION_CONSUMED')
    selection._version(s,header,inp.expected_version)
    revision,context,_=selection.saved(s,header)
    if inp.local_baseline_revision != revision['id']:
        revision_by_id(s,header,inp.local_baseline_revision)
    if inp.header_intent.currency and inp.header_intent.currency!=context['currency']:
        fail('E_VALIDATION',reason='currency')
    if find(s,recovery_key=inp.recovery_key):
        fail('E_RECOVERY_KEY_REUSED')
    row=dict(id=identifier,**common(s.actor.id,ctx.interface,at),selection_id=header['id'],recovery_key=inp.recovery_key,
        request_schema_version=1,attempt_generation=inp.attempt_generation,local_baseline_revision_id=inp.local_baseline_revision,
        anchor_revision_id=revision['id'],anchor_selection_version=header['version'],
        header_intent=q.canonical(inp.header_intent.model_dump(mode='json',exclude_unset=True)),declared_entry_count=inp.declared_entry_count,
        intent_hash=inp.intent_hash,state='uploading',applied_revision_id=None,audit_event_id=event)
    for action in ('begin','seal','terminal'):
        for field in ('request_snapshot','request_hash','receipt_snapshot'):
            row[action+'_'+field]=None
    return row


def comparison(s,row,inp):
    header=require_current(s,row,sealed=True)
    if inp.attempt_generation!=row['attempt_generation'] or inp.intent_hash!=row['intent_hash']:
        fail('E_QUERY_STALE')
    staged=sealed_entries(s,row)
    baseline,context,base_items=revision_by_id(s,header,row['anchor_revision_id'])
    local,_,local_items=(baseline,context,base_items) if row['local_baseline_revision_id']==baseline['id'] else revision_by_id(s,header,row['local_baseline_revision_id'])
    prior={item['invoice_id']:item for item in base_items}
    local_by_id={item['invoice_id']:item for item in local_items}
    final={key:dict(value) for key,value in prior.items()}
    ids=sorted(set(prior)|{entry['invoice_id'] for entry in staged})
    authority.authorize(s,ids)
    # Fetch complete relevant graph in bounded SQL batches, not per-invoice history walks.
    facts={}
    for offset in range(0,len(ids),400):
        batch=ids[offset:offset+400]
        headers=s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id.in_(batch))).mappings().all()
        revisions={r['id']:dict(r) for r in s.company.conn.execute(sa.select(c.transaction_revisions).where(
            c.transaction_revisions.c.id.in_([h['current_revision_id'] for h in headers]))).mappings()}
        profiles={r['revision_id']:dict(r) for r in s.company.conn.execute(sa.select(c.sales_profiles).where(
            c.sales_profiles.c.revision_id.in_(revisions))).mappings()}
        applied={}
        apps=c.applications; inv=apps.alias('recovery_inverse')
        for invoice,units in s.company.conn.execute(sa.select(apps.c.paid_transaction_id,sa.func.sum(apps.c.amount_minor_units)).where(
            apps.c.paid_transaction_id.in_(batch),apps.c.kind=='apply',~sa.exists(sa.select(inv.c.id).where(inv.c.reverses_application_id==apps.c.id))).group_by(apps.c.paid_transaction_id)):
            applied[invoice]=units
        for h in headers:
            r=revisions[h['current_revision_id']]
            facts[h['id']]=dict(version=h['version'],status=h['status'],revision_id=r['id'],date=r['date'],currency=r['currency'],
                due=(r['total_minor_units'] if h['status']=='posted' else 0)-applied.get(h['id'],0),profile=profiles.get(r['id']))
    family={r[0] for r in s.company.raw.execute('''WITH RECURSIVE family(id) AS (
        SELECT id FROM customers WHERE id=? UNION SELECT c.id FROM customers c JOIN family f ON c.parent_id=f.id)
        SELECT id FROM family''',(context['customer_id'],))}
    funding={};source=None
    if context['payment_id']:
        source=q.payment_facts(s,context['payment_id'])
        capacities={source['keys'][key]['party_id']:value for key,value in source['available'].items()}
        funding['source_capacities']=capacities
    maximum=s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.payment_selection_items.c.ordinal),0)).where(
        c.payment_selection_items.c.selection_id==header['id'])).scalar_one()
    calculate=set()
    for entry in staged:
        key=entry['invoice_id']
        if entry['action']=='remove':
            final.pop(key,None)
            continue
        if key not in final:
            maximum+=1
        ordinal=final[key]['ordinal'] if key in final else maximum
        if entry['action']=='calculate':
            calculate.add(key)
            value,origin=0,'calculated'
        else:
            value,origin=entry.get('amount_minor_units'),entry['amount_origin']
            if entry['currency']!=context['currency']:
                fail('E_VALIDATION',reason='currency')
            if origin=='calculated':
                ref=entry['retained_calculation_revision_id']
                saved_row=(local_by_id if ref==local['id'] else prior if ref==baseline['id'] else {}).get(key)
                if not saved_row or saved_row['amount_origin']!='calculated' or saved_row['amount_minor_units']!=value:
                    fail('E_VALIDATION',reason='unauthenticated_saved_calculation',invoice_id=key)
        f=facts.get(key)
        final[key]=dict(invoice_id=key,ordinal=ordinal,expected_version=f['version'] if f else entry['observed_invoice_version'],
            due_minor_units=max(0,f['due']) if f else 0,amount_minor_units=value,amount_origin=origin)
    problems=[]
    hard=[]
    def problem(key,code,blocking=False):
        value=dict(invoice_id=key,code=code,hard_blocker=blocking)
        problems.append(value)
        if blocking:
            hard.append(value)
    for key,item in final.items():
        f=facts.get(key);profile=f['profile'] if f else None
        if not f or not profile or f['status']!='posted' or f['date']>context['date'] or f['currency']!=context['currency'] or profile['control_account_id']!=context['ar_account_id'] or (not source and profile['customer_id'] not in family):
            problem(key,'ineligible_invoice',True)
        else:
            item.update(expected_version=f['version'],due_minor_units=max(0,f['due']))
    if source:
        if source['header']['status']!='posted' or source['revision']['date']>context['date']:
            problem(None,'ineligible_funding',True)
        funding['source_owners']={key:facts[key]['profile']['customer_id'] for key in final if facts.get(key) and facts[key]['profile']}
        if len(funding['source_owners'])!=len(final):
            funding={}
        else:
            for key,owner in funding['source_owners'].items():
                if owner not in capacities:
                    problem(key,'incompatible_source_owner',True)
            context=dict(context,funding_version=source['header']['version'],funding_date=source['revision']['date'],
                funding_capacities=capacities,funding_owners=funding['source_owners'])
    intent=json.loads(row['header_intent'])
    origin=baseline['amount_origin'] if intent['action']=='keep' else intent['amount_origin']
    amount=baseline['amount_minor_units'] if intent['action']=='keep' else intent.get('amount_minor_units')
    if origin=='unresolved':
        amount=None
    computational=[calc.DraftRow(key,item['ordinal'],item['due_minor_units'],item['amount_minor_units'],
        'calculated' if key in calculate else 'entered' if item['amount_minor_units'] is not None else 'unresolved') for key,item in final.items()]
    result=calc.calculate(amount,origin,computational,**funding)
    for calculated in result.rows:
        item=final[calculated.invoice]
        item['amount_minor_units']=calculated.amount
        if calculated.invoice in calculate:
            item['amount_origin']=calculated.origin
    if sum(item['amount_minor_units'] or 0 for item in final.values()) > 9223372036854775807:
        problem(None,'selected_amount:out_of_range',True)
    for code in result.problems:
        problem(None,code,code=='amount:out_of_range')
    values=sorted(final.values(),key=lambda item:(item['ordinal'],item['invoice_id']))
    changes=[]
    attempted={entry['invoice_id']:entry for entry in staged}
    for key in ids:
        f=facts.get(key)
        visible = {k:f[k] for k in ('version','status','revision_id','date','currency','due')} if f else None
        changes.append(dict(invoice_id=key,local_baseline=local_by_id.get(key),anchor=prior.get(key),attempted=attempted.get(key),
            current=visible,proposed=final.get(key),observed_history='unknown'))
    changes.extend(history_changes(s,ids,attempted,prior))
    header_comparison=dict(local_baseline=dict(amount_minor_units=local['amount_minor_units'],amount_origin=local['amount_origin']),
        anchor=dict(amount_minor_units=baseline['amount_minor_units'],amount_origin=baseline['amount_origin']),attempted=intent,
        proposed=dict(amount_minor_units=result.amount,amount_origin=origin),derived=origin=='selection_total')
    fingerprint=q.digest(dict(generation=row['attempt_generation'],intent_hash=row['intent_hash'],recovery_version=row['version'],
        anchor=row['anchor_revision_id'],local=row['local_baseline_revision_id'],facts=facts,
        lineage=lineage_facts(s,{context['customer_id']}|{f['profile']['customer_id'] for f in facts.values() if f['profile']}),
        funding=dict(version=source['header']['version'],date=source['revision']['date'],status=source['header']['status'],capacities=capacities) if source else None,
        header=header_comparison,items=values))
    subtotal=sum(item['amount_minor_units'] or 0 for item in values)
    preview=ComparisonOutput(recovery_id=row['id'],attempt_generation=row['attempt_generation'],intent_hash=row['intent_hash'],
        selection_id=header['id'],anchor_revision_id=baseline['id'],anchor_selection_version=header['version'],recovery_version=row['version'],
        facts_fingerprint=fingerprint,amount_minor_units=result.amount,amount_origin=origin,currency=context['currency'],
        selected_minor_units=subtotal if all(item['amount_minor_units'] is not None for item in values) else None,
        resolved_subtotal_minor_units=subtotal,unapplied_minor_units=result.unapplied,header_comparison=header_comparison,
        item_count=len(values),change_count=len(changes),problem_count=len(problems),hard_blocker_count=len(hard))
    return preview,dict(header=header,revision=baseline,context=context,items=values,prior=prior,changes=changes,problems=problems)


def validate_upload(s,row,inp):
    require_current(s,row)
    if row['state']!='uploading':
        fail('E_RECOVERY_FINALIZED')
    start=inp.chunk_index*200
    expected=min(200,row['declared_entry_count']-start)
    if expected<=0 or len(inp.entries)!=expected:
        fail('E_RECOVERY_INCOMPLETE',reason='chunk_length')
    identifiers=[entry.invoice_id for entry in inp.entries]
    if identifiers!=sorted(set(identifiers)):
        fail('E_VALIDATION',reason='chunk_order')
    present=set(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id.in_(identifiers),c.transactions.c.type.in_(SETTLEABLE_RECEIVABLE_TYPES))).scalars())
    if present != set(identifiers):
        fail('E_RECORD_NOT_FOUND',record_type='invoice')
    lower=s.company.conn.execute(sa.select(I.c.invoice_id).where(I.c.recovery_id==row['id'],I.c.entry_index<=start).order_by(I.c.entry_index.desc()).limit(1)).scalar()
    upper=s.company.conn.execute(sa.select(I.c.invoice_id).where(I.c.recovery_id==row['id'],I.c.entry_index>start+expected).order_by(I.c.entry_index).limit(1)).scalar()
    if (lower and lower>=identifiers[0]) or (upper and upper<=identifiers[-1]):
        fail('E_VALIDATION',reason='global_entry_order')
    if s.company.conn.execute(sa.select(I.c.id).where(I.c.recovery_id==row['id'],I.c.invoice_id.in_(identifiers)).limit(1)).first():
        fail('E_RECOVERY_KEY_REUSED',reason='overlapping_invoice')
    header=selection.resolve(s,row['selection_id'])
    baseline,context,base=revision_by_id(s,header,row['anchor_revision_id'])
    local,_,saved=(baseline,context,base) if row['local_baseline_revision_id']==baseline['id'] else revision_by_id(s,header,row['local_baseline_revision_id'])
    by_revision={baseline['id']:{r['invoice_id']:r for r in base},local['id']:{r['invoice_id']:r for r in saved}}
    for entry in inp.entries:
        if entry.currency and entry.currency!=context['currency']:
            fail('E_VALIDATION',reason='currency')
        if entry.amount_origin=='calculated':
            original=by_revision.get(entry.retained_calculation_revision_id,{}).get(entry.invoice_id)
            if not original or original['amount_origin']!='calculated' or original['amount_minor_units']!=entry.amount_minor_units:
                fail('E_VALIDATION',reason='unauthenticated_saved_calculation')


def prepare(s,ctx,inp,verb):
    authorize_input(inp,ctx,s)
    old=matched(inp,ctx,s,verb)
    if old:
        return Plan(old,dict(input=inp,verb=verb,replay=True))
    row=None;preview=None
    if verb=='begin':
        row=begin_row(s,inp,ctx,clock.now_iso(),None,None)
        header=selection.resolve(s,row['selection_id'])
    else:
        row=resolve(s,inp.recovery_id)
        header=require_current(s,row,getattr(inp,'expected_recovery_version',None))
        if verb=='upload':
            validate_upload(s,row,inp)
        elif verb=='seal':
            if row['state']!='uploading':
                fail('E_RECOVERY_FINALIZED')
            sealed_entries(s,row)
        elif verb=='apply':
            preview,data=comparison(s,row,inp)
            if inp.expected_selection_version!=header['version']:
                fail('E_VERSION_CONFLICT')
            if inp.expected_facts_fingerprint!=preview.facts_fingerprint:
                fail('E_PREVIEW_STALE')
            if preview.hard_blocker_count:
                fail('E_RECOVERY_INCOMPLETE',reason='hard_blockers',recovery_id=row['id'])
        elif verb=='replace':
            if inp.replacement.selection!=row['selection_id'] or inp.replacement.attempt_generation==row['attempt_generation']:
                fail('E_VALIDATION',reason='replacement_identity')
            begin_row(s,inp.replacement,ctx,clock.now_iso(),None,None,allow_active=True)
    received=counts(s,row)[0] if row['id'] else 0
    receipt=dict(action=verb,request_hash=q.digest(request(inp,ctx,'payment recovery '+verb)),recovery_id=row['id'],
        selection_id=header['id'],recovery_version=row['version']+(verb!='begin'),selection_version=header['version']+(verb in ('apply','abort')),
        received_entry_count=received+(len(inp.entries) if verb=='upload' else 0),declared_entry_count=row['declared_entry_count'])
    return Plan(RecoveryWriteOutput(original_receipt=receipt,current=lifecycle(s,header['id']),comparison=preview),dict(input=inp,verb=verb))


def publish(s,row,ctx,at,event,comparison_data=None):
    before=selection.resolve(s,row['selection_id'])
    previous,context,items=selection.saved(s,before)
    prior={item['invoice_id']:item for item in items}
    amount,origin=previous['amount_minor_units'],previous['amount_origin']
    if comparison_data:
        preview,data=comparison_data
        context,items=data['context'],data['items']
        amount,origin=preview.amount_minor_units,preview.amount_origin
    revision_id=new_id()
    header=dict(before,version=before['version']+1,current_revision_id=revision_id,updated_at=at,updated_by=s.actor.id,updated_via=ctx.interface)
    created=dict(created_at=at,created_by=s.actor.id,created_via=ctx.interface,audit_event_id=event)
    revision=dict(id=revision_id,selection_id=header['id'],version=header['version'],context_snapshot=q.canonical(context),
        amount_minor_units=amount,amount_origin=origin,currency=context['currency'],item_count=len(items),
        manifest_hash=q.digest([context,amount,origin,items]),**created)
    events=[]
    for identifier in prior.keys()-{item['invoice_id'] for item in items}:
        events.append(dict(invoice_id=identifier,kind='remove',ordinal=None,expected_version=None,due_minor_units=None,amount_minor_units=None,amount_origin=None))
    for item in items:
        if prior.get(item['invoice_id'])!=item:
            events.append(dict(kind='set',**item))
    events=[dict(id=new_id(),selection_id=header['id'],revision_id=revision_id,**created,**item) for item in events]
    mutations=[(c.payment_selections,header,before),(c.payment_selection_revisions,revision,None)]
    mutations.extend((c.payment_selection_items,item,None) for item in events)
    return mutations,revision_id,header['version']


def apply(plan,ctx,s):
    inp,verb=plan.data['input'],plan.data['verb']
    old=matched(inp,ctx,s,verb)
    if old:
        s.company.raw.execute('ROLLBACK')
        s.company_touched=[];s.hub_touched=[]
        return Applied(old,[],'payment recovery '+verb,finalized=True)
    prepare(s,ctx,inp,verb)
    at,event=clock.now_iso(),new_id()
    created=dict(created_at=at,created_by=s.actor.id,created_via=ctx.interface,audit_event_id=event)
    command='payment recovery '+verb
    snapshot=request(inp,ctx,command);digest=q.digest(snapshot)
    mutations=[]
    before=None if verb=='begin' else resolve(s,inp.recovery_id)
    row=begin_row(s,inp,ctx,at,event,new_id()) if verb=='begin' else dict(before,version=before['version']+1,updated_at=at,updated_by=s.actor.id,updated_via=ctx.interface)
    received=0 if verb=='begin' else counts(s,row)[0]
    header=selection.resolve(s,row['selection_id'])
    receipt=dict(action=verb,request_hash=digest,recovery_id=row['id'],selection_id=header['id'],recovery_version=row['version'],
        selection_version=header['version'],actor_id=s.actor.id,recorded_at=at,audit_event_id=event,
        received_entry_count=received,declared_entry_count=row['declared_entry_count'])
    field='begin' if verb=='begin' else 'seal' if verb=='seal' else 'terminal'
    pointer=None
    if verb=='begin':
        pointer=dict(selection_id=row['selection_id'],recovery_id=row['id'],**created)
    elif verb=='upload':
        receipt.update(chunk_index=inp.chunk_index,received_entry_count=received+len(inp.entries))
        chunk=dict(id=new_id(),selection_id=row['selection_id'],recovery_id=row['id'],chunk_index=inp.chunk_index,
            request_hash=digest,request_snapshot=q.canonical(snapshot),receipt_snapshot=q.canonical(receipt),**created)
        # The evidence records which receivable each attempted edit named, because the
        # composite foreign key is (id, type): writing 'invoice' over a statement charge
        # would be refused by the database, and pinning it would be a lie if it were not.
        kinds=dict(s.company.conn.execute(sa.select(c.transactions.c.id,c.transactions.c.type).where(
            c.transactions.c.id.in_([entry.invoice_id for entry in inp.entries]))).all())
        for offset,entry in enumerate(inp.entries):
            mutations.append((I,dict(id=new_id(),selection_id=row['selection_id'],recovery_id=row['id'],
                entry_index=inp.chunk_index*200+offset+1,invoice_type=kinds[entry.invoice_id],**entry.model_dump(),**created),None))
        mutations.append((C,chunk,None))
    elif verb=='seal':
        row['state']='sealed'
    elif verb in ('apply','abort'):
        comparison_data=comparison(s,before,inp) if verb=='apply' else None
        added,revision,version=publish(s,before,ctx,at,event,comparison_data)
        mutations.extend(added)
        receipt.update(revision_id=revision,selection_version=version)
        row.update(state='applied' if verb=='apply' else 'aborted',applied_revision_id=revision if verb=='apply' else None)
    elif verb=='replace':
        replacement=begin_row(s,inp.replacement,ctx,at,event,new_id(),allow_active=True)
        replacement_receipt=dict(receipt,action='begin',recovery_id=replacement['id'],recovery_version=1,
            received_entry_count=0,declared_entry_count=replacement['declared_entry_count'])
        replacement_snapshot=request(inp.replacement,ctx,'payment recovery begin')
        replacement_receipt['request_hash']=q.digest(replacement_snapshot)
        replacement.update(begin_request_snapshot=q.canonical(replacement_snapshot),begin_request_hash=q.digest(replacement_snapshot),
            begin_receipt_snapshot=q.canonical(replacement_receipt))
        mutations.append((R,replacement,None))
        pointer=dict(selection_id=row['selection_id'],recovery_id=replacement['id'],**created)
        row['state']='superseded'
        receipt['replacement_recovery_id']=replacement['id']
    if verb!='upload':
        row.update({field+'_request_snapshot':q.canonical(snapshot),field+'_request_hash':digest,field+'_receipt_snapshot':q.canonical(receipt)})
    # History rows and barrier transition share this writer transaction and audit event.
    mutations.append((R,row,before))
    if verb in ('apply','abort','replace'):
        old_pointer=effects.rows(s,A,A.c.selection_id==row['selection_id'])[0]
        mutations.append((A,None,old_pointer))
    if pointer:
        mutations.append((A,pointer,None))
    types={R.name:'payment_selection_recovery',I.name:'payment_selection_recovery_item',C.name:'payment_selection_recovery_chunk',A.name:'payment_selection_recovery_active',
        c.payment_selections.name:'payment_selection',c.payment_selection_revisions.name:'payment_selection_revision',c.payment_selection_items.name:'payment_selection_item'}
    touched=[]
    for table,after,prior in mutations:
        record=after or prior
        touched.append(Touched(types[table.name],record.get('id',record.get('selection_id')),
            'delete' if after is None else 'update' if prior else 'create',prior.get('version',1) if prior else None,
            after.get('version',1) if after else None,after,prior,db='company'))
    audit.write_event_to(s.company,ctx,command,command,touched,actor_id=s.actor.id,actor_kind=s.actor.kind,
        directive_code=getattr(s,'directive_code',None),event_id=event)
    for table,after,prior in mutations:
        pk=list(table.primary_key)[0]
        if after is None:
            s.company.conn.execute(table.delete().where(pk==prior[pk.name]))
        elif prior:
            s.company.conn.execute(table.update().where(pk==prior[pk.name]).values(**after))
        else:
            s.company.conn.execute(table.insert().values(**after))
    active(s,row['selection_id'])
    return Applied(RecoveryWriteOutput(original_receipt=receipt,current=lifecycle(s,row['selection_id'])),touched,command,audited=True)


def readable_selection(s,selection_id):
    """Historical secondary ownership predicate, applied before page counts."""
    work_allowed=True
    try:
        authority.require_resource(s,'customer-work','member')
    except BookflowError as exc:
        if exc.code!='E_PERMISSION':
            raise
        work_allowed=False
    # A consumed new-receipt selection has no funding ID in its context.
    # Its permanent operation owns P, including P's later historical links to
    # invoices that were never S items. Match that scalar ownership before count.
    h=c.payment_selections.alias('recovery_visibility_selection')
    op=c.payment_operations.alias('recovery_visibility_operation')
    safe=sa.case((sa.func.json_valid(op.c.request_snapshot),op.c.request_snapshot),else_='{}')
    array=sa.func.json_type(safe,'$.resolved_transaction_ids')=='array'
    values=sa.func.json_each(sa.case((array,sa.func.json_extract(safe,'$.resolved_transaction_ids')),else_='[]')).table_valued('value','type').alias('recovery_visibility_ids')
    missing=sa.exists(sa.select(values.c.value).where(sa.or_(values.c.type!='text',~sa.exists(sa.select(c.transactions.c.id).where(c.transactions.c.id==values.c.value)))))
    operation=sa.select(op.c.id).where(op.c.id==h.c.consumed_operation_id,array,~missing)
    if not work_allowed:
        operation=operation.where(~sa.exists(sa.select(values.c.value).where(authority.work_link_predicate(values.c.value))))
    consumed=~sa.exists(sa.select(h.c.id).where(h.c.id==selection_id,h.c.consumed_operation_id.is_not(None),~sa.exists(operation)))
    if not work_allowed:
        i=c.payment_selection_items;r=c.payment_selection_revisions
        payment=sa.func.json_extract(r.c.context_snapshot,'$.payment_id')
        return sa.and_(consumed,~sa.or_(sa.exists(sa.select(i.c.id).where(i.c.selection_id==selection_id,i.c.invoice_id.is_not(None),authority.work_link_predicate(i.c.invoice_id))),
            sa.exists(sa.select(I.c.id).where(I.c.selection_id==selection_id,authority.work_link_predicate(I.c.invoice_id))),
            sa.exists(sa.select(r.c.id).where(r.c.selection_id==selection_id,payment.is_not(None),authority.work_link_predicate(payment)))))
    return consumed


def query_epoch(s):
    """Compact publication fence for off-page count dependencies, as for cursors."""
    return s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.audit_events.c.seq),0))).scalar_one()


def history_changes(s,ids,attempted,prior):
    """Batch-owned Row21 decoding; each event is an independently paged change row."""
    from bookflow.company.sales import _history_snapshot
    result=[]
    a,e=c.audit_entries,c.audit_events
    for offset in range(0,len(ids),400):
        rows=s.company.conn.execute(sa.select(a,e.c.at,e.c.actor_id,e.c.on_behalf_of,e.c.interface).join(e,e.c.id==a.c.event_id).where(
            a.c.record_type=='transaction',a.c.record_id.in_(ids[offset:offset+400])).order_by(e.c.seq,a.c.id)).mappings()
        for row in rows:
            key=row['record_id']
            observed=attempted.get(key,{}).get('observed_invoice_version',prior.get(key,{}).get('expected_version'))
            if observed is None or row['version_after'] is None or row['version_after']<=observed:
                continue
            before,after=_history_snapshot(row['before']),_history_snapshot(row['after'])
            valid=all(value is not None and value.get('id')==key and value.get('type') in SETTLEABLE_RECEIVABLE_TYPES and type(value.get('version')) is int for value in (before,after))
            fields=None
            if valid:
                fields=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k) and k not in {
                    'version','updated_at','updated_by','updated_via','created_at','created_by','created_via'})
            result.append(dict(invoice_id=key,history_event_id=row['event_id'],history_entry_id=row['id'],
                version_before=row['version_before'],version_after=row['version_after'],actor_id=row['actor_id'],
                on_behalf_of=row['on_behalf_of'],interface=row['interface'],at=row['at'],changed_fields=fields,unknown_history=not valid))
    return result


def lineage_facts(s,identifiers):
    result={}
    identifiers=sorted(identifiers)
    for offset in range(0,len(identifiers),400):
        batch=identifiers[offset:offset+400]
        marks=','.join('?' for _ in batch)
        rows=s.company.raw.execute('WITH RECURSIVE lineage(id,parent_id,active) AS (SELECT id,parent_id,active FROM customers WHERE id IN ('+marks+') UNION SELECT c.id,c.parent_id,c.active FROM customers c JOIN lineage l ON c.id=l.parent_id) SELECT id,parent_id,active FROM lineage',batch)
        for identifier,parent,enabled in rows:
            result[identifier]=dict(parent_id=parent,active=enabled)
    return result
