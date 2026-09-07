"""One-transaction private deposit persistence; no commit or child dispatch."""
import json
from collections import defaultdict
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects as effects, payment_queries as q
from bookflow.company import deposit_lifecycle as lifecycle, deposit_operations as operations
from bookflow.company import deposit_validation, bank_effects, journal_custom_fields as custom
from bookflow.company.deposit_models import Effect
from bookflow.company.deposit_lifecycle_models import LifecycleOutput, LifecycleEffect, DocumentState, HeaderChange, MembershipChange
from bookflow.core import audit
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched
from bookflow.hub.users import common


TABLES=('transaction_revisions','deposit_profiles','document_line_identities','deposit_row_keys',
    'document_lines','deposit_component_keys','deposit_components','posting_batches','posting_lines','posting_line_sources',
    'deposit_cash_cells','deposit_memberships','bank_effect_keys','bank_effect_versions')


def _state(h,financial):
    return DocumentState(id=h['id'],version=h['version'],revision_id=h['current_revision_id'],number=h['number'],status=h['status'],
        revision_date=financial.intent.date,currency=financial.intent.currency,revision_posting_total=financial.posting_total,revision_subtotal=financial.subtotal,
        revision_bank_total=financial.bank_total,revision_cash_back=financial.cash_back,effective_bank_total=financial.bank_total if h['status']=='posted' else 0,
        active_source_ids=tuple(sorted(r.source.transaction_id for r in financial.intent.sources)) if h['status']=='posted' else ())


def build(s,ctx,plan,*,source_overlay=None):
    if source_overlay is not None:
        from bookflow.company.deposit_coordinate_models import SourceResultOverlay
        if type(source_overlay) is not SourceResultOverlay:
            raise BookflowError('E_INTERNAL')
    data=json.loads(plan.data_json);financial=Effect.model_validate_json(q.canonical(data['financial']))
    old=data['before'];prior=data['prior'];identity=data['identity'];event=data['event'];at=data['at']
    pending={name:[] for name in TABLES}
    provenance=dict(created_at=at,created_by=s.actor.id,created_via=ctx.interface.value)
    created=lambda:dict(id=new_id(),**provenance)
    audited=dict(**provenance,audit_event_id=event)
    h=dict(old) if old else dict(id=identity,**common(s.actor.id,ctx.interface.value,at),type='deposit',status='posted',
        number=data['number'],current_revision_id=None,voided_at=None,voided_by=None,void_reason=None,void_posting_batch_id=None)
    changed=data['changed'];batch=None;inverse=None;revision=prior
    claims=[];headers=[];bank_current=[]
    if changed:
        if old:h.update(version=old['version']+1,updated_at=at,updated_by=s.actor.id,updated_via=ctx.interface.value)
        if old:
            batches=effects.rows(s,c.posting_batches,c.posting_batches.c.revision_id==prior['id'],c.posting_batches.c.kind!='reversal')
            if len(batches)!=1:raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
            inverse=effects.reverse(s,h,prior,batches[0],event,created,pending)
        if plan.verb=='void':
            h.update(status='voided',voided_at=at,voided_by=s.actor.id,void_reason=ctx.reason.strip(),void_posting_batch_id=inverse['id'])
        else:
            revision=dict(**created(),transaction_id=identity,revision_number=prior['revision_number']+1 if prior else 1,
                supersedes_revision_id=prior['id'] if prior else None,date=financial.intent.date,number=data['number'],name_type=None,name_id=None,
                memo=data['memo'],total_minor_units=financial.posting_total,currency=financial.intent.currency,issuer_snapshot=q.canonical(data['issuer']),
                custom_fields_snapshot=q.canonical(plan.custom_plan.snapshot),audit_event_id=event)
            pending['transaction_revisions'].append(revision)
            h.update(current_revision_id=revision['id'],number=data['number'])
            pending['deposit_profiles'].append(dict(revision_id=revision['id'],transaction_id=identity,type='deposit',bank_account_id=financial.intent.bank.id,
                posting_total=financial.posting_total,subtotal=financial.subtotal,bank_total=financial.bank_total,cash_back=financial.cash_back,
                facts_snapshot=financial.model_dump_json(),**audited))
            batch=dict(**created(),transaction_id=identity,revision_id=revision['id'],kind='replacement' if old else 'original',
                effective_date=financial.intent.date,reverses_batch_id=None,replaces_batch_id=batches[0]['id'] if old else None,audit_event_id=event)
            pending['posting_batches'].append(batch)
            _business(s,financial,data,revision,batch,pending,created,audited,source_overlay)
        for claim in data['claims']:
            release=dict(claim,**created(),audit_event_id=event,kind='release',reverses_membership_id=claim['id'])
            pending['deposit_memberships'].append(release)
        if plan.verb!='void':
            for row in financial.intent.sources:
                source=row.source
                claim=dict(**created(),audit_event_id=event,kind='claim',transaction_id=identity,revision_id=revision['id'],batch_id=batch['id'],row_id=row.row_id,
                    source_transaction_id=source.transaction_id,source_revision_id=source.revision_id,source_batch_id=source.business_batch_id,
                    amount_minor_units=source.cash_minor_units,currency=source.currency,source_date=source.receipt_date,
                    facts_snapshot=source.model_dump_json(),reverses_membership_id=None)
                pending['deposit_memberships'].append(claim);claims.append(claim)
        for before in data['source_headers'].values():
            after=dict(before,version=before['version']+1,updated_at=at,updated_by=s.actor.id,updated_via=ctx.interface.value)
            headers.append((before,after))
        _bank(s,financial,data,revision,batch or batches[0],pending,bank_current,created,audited,void=plan.verb=='void')
    return dict(header=h,pending=pending,source_headers=headers,claims=claims,bank_current=bank_current,
        data=data,financial=financial,revision=revision)


def _business(s,financial,data,revision,batch,pending,created,audited,source_overlay=None):
    identity=data['identity'];currency=financial.intent.currency
    oldkeys={r['id']:r for r in effects.rows(s,c.deposit_row_keys,c.deposit_row_keys.c.transaction_id==identity)}
    oldcomponents={(r['row_id'],r['ordinal']):r for r in effects.rows(s,c.deposit_component_keys,c.deposit_component_keys.c.transaction_id==identity)}
    rows=[(r.row_id,r.ordinal,'source',r) for r in financial.intent.sources]+[(r.row_id,r.ordinal,'additional',r) for r in financial.intent.additional]
    rows.append((data['header_row'],data['header_ordinal'],'header',None))
    envelopes={};components={}
    for rowid,ordinal,kind,row in sorted(rows,key=lambda r:r[1]):
        if rowid not in oldkeys:
            line=dict(created(),id=rowid);pending['document_line_identities'].append(dict(line,transaction_id=identity))
            key=dict(id=rowid,transaction_id=identity,line_id=line['id'],ordinal=ordinal,kind=kind,**audited)
            pending['deposit_row_keys'].append(key)
        else:key=oldkeys[rowid]
        envelope=dict(**created(),transaction_id=identity,revision_id=revision['id'],line_id=key['line_id'],position=ordinal,kind='deposit',
            account_id=None,side=None,amount_minor_units=None,currency=currency,account_snapshot=None,name_type=None,name_id=None,party_name=None,
            class_id=None,class_name=None,description=row.memo if row else data['memo'],original_minor_units=None,original_currency=None,rate_used=None,rate_source=None)
        pending['document_lines'].append(envelope);envelopes[rowid]=envelope['id']
        if kind=='source':
            order={o.key:o.ordinal for o in row.occurrences if o.present}
            # Persist even retained zero-capacity occurrences; no zero component.
            for occurrence in row.occurrences:
                index=(rowid,occurrence.ordinal)
                if index not in oldcomponents:
                    pending['deposit_component_keys'].append(dict(**created(),transaction_id=identity,row_id=rowid,ordinal=occurrence.ordinal,
                        kind=occurrence.key.kind,semantic_identity=occurrence.key.identity,tax_item_id=occurrence.key.tax_item,audit_event_id=data['event']))
            values=[(order[v.key],'funding',v.capacity,v) for v in row.source.components]
        else:
            component_order=0 if kind=='additional' else 1
            if (rowid,component_order) not in oldcomponents:
                pending['deposit_component_keys'].append(dict(**created(),transaction_id=identity,row_id=rowid,ordinal=component_order,
                    kind=kind,semantic_identity=rowid,tax_item_id='',audit_event_id=data['event']))
            values=[(0,'funding' if row.units>0 else 'offset',abs(row.units),None)] if row else (
                [(1,'cash_back',financial.cash_back,None)] if financial.cash_back else [])
        for component_order,role,capacity,source in values:
            component=dict(**created(),transaction_id=identity,revision_id=revision['id'],document_line_id=envelope['id'],row_id=rowid,
                component_ordinal=component_order,role=role,capacity=capacity,currency=currency,
                facts_snapshot=source.model_dump_json() if source else row.model_dump_json() if row else financial.intent.cash_back.model_dump_json(),
                source_transaction_id=row.source.transaction_id if source else None,source_revision_id=row.source.revision_id if source else None,
                source_document_line_id=source.document_line_id if source else None,source_posting_line_id=source.posting_line_id if source else None,
                source_attribution_id=source.posting_source_id if source else None,audit_event_id=data['event'])
            pending['deposit_components'].append(component);components[(rowid,component_order)]=component['id']
    for cell in financial.cells:
        bucket='additional' if cell.bucket.startswith('additional:') else cell.bucket
        target=cell.bucket.split(':',1)[1] if bucket=='additional' else data['header_row']
        pending['deposit_cash_cells'].append(dict(**created(),transaction_id=identity,revision_id=revision['id'],component_id=components[(cell.row_id,cell.component_ordinal)],
            bucket_row_id=target,bucket=bucket,amount_minor_units=cell.units,currency=currency,audit_event_id=data['event']))
    accounts={a.id:a for a in [financial.intent.bank]+[r.account for r in financial.intent.additional]+([financial.intent.cash_back.account] if financial.intent.cash_back else [])}
    for order,leg in enumerate(financial.legs,1):
        if leg.key.startswith('additional:') and '/' not in leg.key:
            rowid=leg.key.split(':',1)[1];component_order=0
        else:
            _,rowid,component_order=leg.key.split('/');component_order=int(component_order)
        a=accounts.get(leg.account_id)
        if a:
            account={k:getattr(a,k) for k in ('id','name','full_name','number','type','normal_balance')}
        else:
            # UF must retain the source's captured account facts, not today's labels.
            source=next(r.source for r in financial.intent.sources if r.row_id==rowid)
            component=next(v for v in source.components if v.key==next(o.key for o in next(r for r in financial.intent.sources if r.row_id==rowid).occurrences if o.ordinal==component_order))
            candidates=effects.rows(s,c.posting_lines,c.posting_lines.c.id==component.posting_line_id)
            if source_overlay is not None and source.transaction_id==source_overlay.source_id:
                if source_overlay.deposit_id!=identity or source_overlay.source.cash!=source:
                    raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
                candidates += [r for r in source_overlay.source.plan.data.get('pending',{}).get('posting_lines',()) if r['id']==component.posting_line_id]
            if len(candidates)!=1:
                raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
            raw=candidates[0]
            if (raw['transaction_id'],raw['batch_id'],raw['account_id'])!=(source.transaction_id,source.business_batch_id,leg.account_id):
                raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
            account=json.loads(raw['account_snapshot'])
        dimensions=leg.dimensions
        posted=dict(**created(),transaction_id=identity,batch_id=batch['id'],line_no=order,account_id=leg.account_id,
            debit_minor_units=max(0,leg.signed_debit),credit_minor_units=max(0,-leg.signed_debit),currency=currency,account_snapshot=q.canonical(account),
            name_type=dimensions.party_kind,name_id=dimensions.party_id,party_name=dimensions.party_name,class_id=dimensions.class_id,class_name=dimensions.class_name,
            description=data['memo'],original_minor_units=None,original_currency=None,rate_used=None,rate_source=None,reversed_line_id=None)
        pending['posting_lines'].append(posted)
        owner_row,owner_ordinal = (data['header_row'],1) if leg.key.startswith('cash_back/') else (leg.key.split('/')[0].split(':',1)[1],0) if leg.key.startswith('additional:') and '/' in leg.key else (rowid,component_order)
        pending['posting_line_sources'].append(dict(**created(),transaction_id=identity,posting_line_id=posted['id'],revision_id=revision['id'],
            document_line_id=envelopes[owner_row],deposit_component_id=components[(owner_row,owner_ordinal)],amount_minor_units=abs(leg.signed_debit),currency=currency,
            reversed_source_id=None,tax_component_id=None,payment_component_id=None))


def _bank(s,financial,data,revision,batch,pending,current,created,audited,*,void):
    identity=data['identity']
    keys=effects.rows(s,c.bank_effect_keys,c.bank_effect_keys.c.transaction_id==identity)
    previous=[];versions={};keymap={}
    for key in keys:
        value=effects.rows(s,c.bank_effect_current,c.bank_effect_current.c.key_id==key['id'])[0]
        version=effects.rows(s,c.bank_effect_versions,c.bank_effect_versions.c.id==value['version_id'])[0]
        item=bank_effects.BankEffect(transaction_id=identity,role=key['role'],row_id=key['row_id'],account_id=version['account_id'],active=bool(version['active']),
            signed_debit=version['signed_debit'],statement_amount=version['statement_amount'],date=version['effective_date'],currency=version['currency'])
        previous.append(item);keymap[item.identity]=key;versions[key['id']]=version['version']
    values=bank_effects.enumerate_deposit(financial,header_row_id=data['header_row'],previous=previous,void=void)
    data['bank_effects']=[v.model_dump(mode='json') for v in values]
    for value in values:
        key=keymap.get(value.identity)
        if key is None:
            key=dict(**created(),transaction_id=identity,role=value.role,row_id=value.row_id,audit_event_id=data['event'])
            pending['bank_effect_keys'].append(key)
        if versions.get(key['id'],0) >= 9223372036854775807:
            raise BookflowError('E_VALUE_RANGE')
        version=dict(**created(),transaction_id=identity,revision_id=revision['id'],key_id=key['id'],version=versions.get(key['id'],0)+1,
            batch_id=batch['id'],number=data['number'],memo=data['memo'],account_id=value.account_id,effective_date=value.date,
            active=value.active,signed_debit=value.signed_debit,statement_amount=value.statement_amount,currency=value.currency,audit_event_id=data['event'])
        pending['bank_effect_versions'].append(version);current.append(dict(key_id=key['id'],version_id=version['id']))


def _execute(s,ctx,plan):
    """Re-resolve and validate inside caller's already-open writer transaction."""
    if not s.company.raw.in_transaction or s.dry_run:
        raise RuntimeError('Private deposit execution requires an owned writer transaction')
    inp=lifecycle.INPUTS[plan.verb].model_validate_json(plan.input_json)
    recovered=lifecycle.recover(s,ctx,inp,plan.verb,plan.binding)
    if recovered is not None:return recovered,None
    if plan.verb!='post' and getattr(inp,'dependency_guard',None) is None:
        raise BookflowError('E_PREVIEW_STALE',details={'reason':'complete deposit guard required'})
    # Retain the authenticated preview producer, including bearer liveness.
    # Re-deriving OS identity here would change a hosted agent's admission.
    fresh=lifecycle.prepare(s,ctx,inp,plan.verb,binding=plan.binding,expected_guard=plan.dependency_guard)
    if fresh.facts_fingerprint!=plan.facts_fingerprint:raise BookflowError('E_PREVIEW_STALE')
    bundle=build(s,ctx,fresh)
    from bookflow.company.deposit_persistence_validation import validate
    validate(s,ctx,fresh,bundle)
    data=bundle['data'];h=bundle['header'];pending=bundle['pending'];old=data['before']
    financial=bundle['financial']
    changes=[HeaderChange(id=h['id'],before_version=old['version'] if old else None,after_version=h['version'])] if data['changed'] else []
    changes.extend(HeaderChange(id=a['id'],before_version=b['version'],after_version=a['version']) for b,a in bundle['source_headers'])
    from bookflow.company import deposits
    reversed_batch=next((b for b in pending['posting_batches'] if b['kind']=='reversal'),None)
    reversal=deposits.inverse(Effect.model_validate_json(q.canonical(data['previous'])),reversed_batch['reverses_batch_id']) if reversed_batch else None
    from bookflow.company import deposit_draft_consumption as consumption
    consumed=consumption.build(s,ctx,data,fresh.binding)
    original=LifecycleEffect(consumed_draft=consumption.receipt(data,financial),action=fresh.verb,before=operations.state(s,old['id']) if old else None,after=_state(h,financial),financial=financial,reversal=reversal,
        batch_ids=tuple(r['id'] for r in pending['posting_batches']),headers=tuple(changes),
        memberships=tuple(MembershipChange(source_id=r['source_transaction_id'],claim_id=r['id'],kind=r['kind'],reverses_membership_id=r['reverses_membership_id'],amount_minor_units=r['amount_minor_units'],currency=r['currency']) for r in pending['deposit_memberships']),
        bank_effects=tuple(bank_effects.BankEffect.model_validate(v) for v in data.get('bank_effects',[])),audit_event_id=data['event'])
    output=LifecycleOutput(current_draft=consumption.projected(consumed),command='deposit '+fresh.verb,operation_key=inp.operation_key,operation_id=data['operation_id'],changed=data['changed'],new_effect=data['changed'],
        facts_fingerprint=fresh.facts_fingerprint,dependency_guard=fresh.dependency_guard,effect=original,current=_state(h,financial))
    touched=consumption.touches(consumed)
    if data['changed']:
        touched.append(Touched('transaction',h['id'],'update' if old else 'create',old['version'] if old else None,h['version'],h,old,db='company'))
    touched.extend(Touched('transaction',a['id'],'update',b['version'],a['version'],a,b,db='company') for b,a in bundle['source_headers'])
    for name in TABLES:
        touched.extend(Touched(name.rstrip('s'),r.get('id',r.get('revision_id')),'create',None,1,effects.decoded(r),db='company') for r in pending[name])
    targets=sorted(set(data['targets'])|{h['id']}|set(data['source_headers']))
    saved_request=operations.request(inp,ctx,s,fresh.verb,original=True)
    saved_request.update(resolved_transaction_ids=targets,resolved_identity_map=dict(document=h['id'],
        bank=financial.intent.bank.id,header_row=data['header_row'],
        sources=[dict(source=r.source.transaction_id,row=r.row_id) for r in financial.intent.sources],
        additional=[dict(row=r.row_id,account=r.account.id,party=r.dimensions.party_id) for r in financial.intent.additional],
        prospective_ids={token:identity for identity,token in data['mapping'].items()}))
    if consumed is not None:saved_request['resolved_draft']=data['draft']
    operation=dict(id=data['operation_id'],operation_key=inp.operation_key,command='deposit '+fresh.verb,transaction_id=h['id'],request_hash=q.digest(operations.request(inp,ctx,s,fresh.verb)),
        request_snapshot=q.canonical(saved_request),effect_snapshot=output.model_dump_json(),
        created_at=data['at'],created_by=s.actor.id,created_via=ctx.interface.value,audit_event_id=data['event'])
    touched.append(Touched('deposit_operation',operation['id'],'create',None,1,effects.decoded(operation),db='company'))
    if fresh.custom_plan is not None and data['changed']:touched.extend(custom.touches(fresh.custom_plan))
    audit.write_event_to(s.company,ctx,'deposit '+fresh.verb,'Deposit '+fresh.verb,touched,actor_id=s.actor.id,actor_kind=s.actor.kind,
        directive_code=getattr(s,'directive_code',None),event_id=data['event'])
    if data['changed']:
        if old:s.company.conn.execute(c.transactions.update().where(c.transactions.c.id==h['id']).values(**h))
        else:s.company.conn.execute(c.transactions.insert().values(**h))
        for before,after in bundle['source_headers']:
            s.company.conn.execute(c.transactions.update().where(c.transactions.c.id==after['id']).values(**after))
        for name in TABLES:
            for row in pending[name]:s.company.conn.execute(getattr(c,name).insert().values(**row))
        for claim in data['claims']:
            s.company.conn.execute(c.deposit_current_memberships.delete().where(c.deposit_current_memberships.c.membership_id==claim['id']))
        for claim in bundle['claims']:
            s.company.conn.execute(c.deposit_current_memberships.insert().values(source_transaction_id=claim['source_transaction_id'],transaction_id=h['id'],membership_id=claim['id']))
        from sqlalchemy.dialects.sqlite import insert
        for value in bundle['bank_current']:
            stmt=insert(c.bank_effect_current).values(**value)
            s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['key_id'],set_=value))
        if fresh.custom_plan is not None:custom.apply(s.company,fresh.custom_plan)
        if data['sequence']:
            stmt=insert(c.sequences).values(**data['sequence'])
            s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'],set_=data['sequence']))
    s.company.conn.execute(c.deposit_operations.insert().values(**operation))
    for target in targets:s.company.conn.execute(c.deposit_operation_targets.insert().values(operation_id=operation['id'],transaction_id=target))
    collections=dict(request_sources=[r.model_dump(mode='json') for r in financial.intent.sources],request_additional=[r.model_dump(mode='json') for r in financial.intent.additional],
        memberships=[r.model_dump(mode='json') for r in original.memberships],document_changes=[r.model_dump(mode='json') for r in original.headers],
        cash_allocations=[r.model_dump(mode='json') for r in financial.cells],bank_changes=[r.model_dump(mode='json') for r in original.bank_effects])
    for kind,values in collections.items():
        for ordinal,value in enumerate(values):
            s.company.conn.execute(c.deposit_operation_items.insert().values(operation_id=operation['id'],kind=kind,ordinal=ordinal,facts_snapshot=q.canonical(value)))
    consumption.persist(s,consumed)
    return output,consumed


def execute(s,ctx,plan):
    """One financial rollback boundary, including deferred consumption heads."""
    if not s.company.raw.in_transaction or s.dry_run:
        raise RuntimeError('Private deposit execution requires an owned writer transaction')
    s.company.raw.execute('SAVEPOINT bookflow_deposit_financial')
    try:
        output,consumed=_execute(s,ctx,plan)
        from bookflow.company.deposit_drafts import final_foreign_keys
        final_foreign_keys(s)
        from bookflow.company import deposit_draft_consumption as consumption
        consumption.validate_persisted(s,consumed)
        s.company.raw.execute('RELEASE bookflow_deposit_financial')
        return output
    except BaseException:
        s.company.raw.execute('ROLLBACK TO bookflow_deposit_financial')
        s.company.raw.execute('RELEASE bookflow_deposit_financial')
        raise
