"""Independent complete source-row admission and deposit aggregate equations."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects as rows, payment_queries as q
from bookflow.company import deposit_validation, deposit_persistence_validation, deposit_coordination as coordination
from bookflow.company import payment_corrections, payment_cancellation, sales_validation, journal_custom_fields as custom
from bookflow.company.deposit_coordinate_models import SourceRows
from bookflow.core import audit
from bookflow.core.errors import BookflowError


def require(ok):
    if not ok:raise BookflowError('E_INTERNAL',message='Invalid coordinate row aggregate.')


def validate(s,ctx,bundle):
    from bookflow.company.deposit_coordinate_persistence import typed_source, source_before, target_ids, DEPOSIT_KINDS
    source=bundle.prepared.resolution.source;plan=source.plan;data=plan.data
    if source.action.kind.startswith('payment_'):
        (payment_corrections if source.action.kind=='payment_update' else payment_cancellation).validate(plan,s,ctx)
    else:sales_validation.validate(plan,s,ctx)
    resolved=bundle.prepared.resolution
    require(set(bundle.deposit_bundle)=={'header','pending','source_headers','claims','bank_current','data','financial','revision'})
    require(set(bundle.deposit_bundle['pending'])=={table for table,_,_ in DEPOSIT_KINDS})
    require(all(set(pointer)=={'key_id','version_id'} for pointer in bundle.deposit_bundle['bank_current']))
    require(bundle.deposit_plan.verb==('void' if resolved.input.replacement.mode=='void' else 'update'))
    require(bundle.deposit_plan.data_json==resolved.deposit_data_json and bundle.deposit_plan.custom_plan==resolved.deposit_custom_plan)
    require(bundle.deposit_plan.binding==bundle.prepared.binding)
    require(bundle.deposit_plan.input_json==resolved.input.model_dump_json(by_alias=True,exclude_unset=True))
    require(coordination.prepare_source_overlay(s,ctx,resolved.input,source,bundle.prepared.binding)==resolved.overlay)
    expected=dict(data['pending']) if source.plan.preview.changed else dict(data.get('pending',{}))
    if data.get('billing_allocations'):expected['work_billing_allocations']=data['billing_allocations']
    require(bundle.source_rows==typed_source(expected))
    require(source_before(s,resolved.overlay.source_id)==bundle.output.effect.source.before)
    deposit_persistence_validation.validate_rows(s,ctx,bundle.deposit_plan,bundle.deposit_bundle)
    from bookflow.company.deposit_draft_provider import validate_financial
    validate_financial(s,ctx,bundle.deposit_bundle['data'],bundle.deposit_bundle['financial'],bundle.prepared.binding,resolved.overlay,custom_plan=bundle.deposit_plan.custom_plan)
    validate_deposit_columns(s,ctx,bundle)
    validate_deposit_business_columns(s,ctx,bundle)
    d=bundle.deposit_bundle;financial=d['financial'];old=d['data']['before'];h=d['header']
    if d['data']['changed'] and bundle.deposit_plan.verb!='void':
        previous=d['data']['previous']
        from bookflow.company.deposit_models import Effect
        if d['data'].get('draft') is None:
            deposit_validation.validate_references(financial,s,previous=Effect.model_validate_json(q.canonical(previous)))
        # Draft references were independently checked by validate_financial above.
        from bookflow.company import deposit_sources
        for row in financial.intent.sources:
            if row.source.transaction_id==resolved.overlay.source_id:require(row==resolved.overlay.retained_row)
            else:require(row.source==deposit_sources.load(s,row.source.transaction_id))
    changes=[]
    if source.plan.preview.changed:
        changes=[(data['before'],data['header']),*data.get('changed_headers',())]
    if d['data']['changed']:changes.extend([(old,h),*d['source_headers']])
    expected_headers=coordination.coalesce_headers(changes,source.provenance,s.actor.id,ctx.interface.value)
    require([(v.before.model_dump(),v.after.model_dump()) for v in bundle.headers]==list(expected_headers))
    require(bundle.output.effect.headers==bundle.headers)
    for before,after in expected_headers:
        require(rows.rows(s,c.transactions,c.transactions.c.id==before['id'])==[before])
        require(after['version']==before['version']+1)
    require(target_ids(s,bundle.prepared,bundle.headers,bundle.output.effect.source.before,bundle.source_rows)==bundle.output.effect.target_ids)
    inserted=bundle.source_rows.model_dump(mode='python')
    all_rows=[(table,value) for table,values in inserted.items() for value in values]
    all_rows.extend((table,value) for table,values in d['pending'].items() for value in values)
    generated=[]
    for table,value in all_rows:
        columns={column.name:column for column in getattr(c,table).columns}
        require(set(value)==set(columns) or (table=='posting_line_sources' and set(value)<=set(columns) and set(columns)-set(value)<={'tax_component_id','payment_component_id','deposit_component_id'}))
        for key,column in columns.items():
            raw=value.get(key)
            if raw is None:require(column.nullable or column.default is not None or column.server_default is not None)
            else:
                require(type(raw) is column.type.python_type)
                if type(raw) is int:require(-9223372036854775808<=raw<=9223372036854775807)
        for key,wanted in (('created_at',source.provenance.at),('created_by',s.actor.id),('created_via',ctx.interface.value),('audit_event_id',source.provenance.event_id)):
            if key in value:require(value[key]==wanted)
        if 'id' in value:
            generated.append(value['id'])
            require(not rows.rows(s,getattr(c,table),getattr(c,table).c.id==value['id']))
    repeated={identity for identity in generated if generated.count(identity)>1}
    for identity in repeated:
        aliases=[(table,value) for table,value in all_rows if value.get('id')==identity]
        require(len(aliases)==2 and {table for table,_ in aliases}=={'document_line_identities','deposit_row_keys'})
        key=next(value for table,value in aliases if table=='deposit_row_keys')
        line=next(value for table,value in aliases if table=='document_line_identities')
        require(key['line_id']==line['id'] and key['transaction_id']==line['transaction_id']==h['id'])
    identities=bundle.output.effect.identities
    require(len({v.physical_id for v in identities})==len(identities))
    manifest_ids={v.physical_id for v in identities}
    require(set(generated)<=manifest_ids)
    # Exact final pointer/number/date/captured fields, not a placeholder header.
    if d['data']['changed']:
        if bundle.deposit_plan.verb=='void':
            require(h['current_revision_id']==old['current_revision_id'] and h['status']=='voided')
            require(h['void_posting_batch_id']==next(v['id'] for v in d['pending']['posting_batches'] if v['kind']=='reversal'))
        else:
            require(len(d['pending']['transaction_revisions'])==1)
            revision=d['pending']['transaction_revisions'][0]
            require(h['current_revision_id']==revision['id'] and h['status']=='posted')
            require((revision['number'],revision['memo'],json.loads(revision['issuer_snapshot']))==
                (d['data']['number'],d['data']['memo'],d['data']['issuer']))
    for cp in (data.get('custom_plan'),resolved.deposit_custom_plan if d['data']['changed'] else None):
        if cp:
            owner=cp.owner_plan
            require(owner.record_id in (h['id'],resolved.overlay.source_id))
    require(data.get('sequence') is None and d['data']['sequence'] is None)
    operation=json.loads(bundle.operation_json)
    require(operation['effect_snapshot']==bundle.output.model_dump_json())
    require(operation['audit_event_id']==source.provenance.event_id and operation['id']==bundle.output.operation_id)
    from bookflow.company.deposit_operation_pages import collections
    values=collections(bundle.output)
    expected_items=[dict(operation_id=operation['id'],kind=kind,ordinal=index,facts_snapshot=q.canonical(value))
        for kind,items in values.items() for index,value in enumerate(items)]
    require(json.loads(bundle.items_json)==expected_items)
    event=bundle.audit_event
    require(event.event['id']==source.provenance.event_id and event.event['at']==source.provenance.at)
    require((event.event['actor_id'],event.event['actor_kind'],event.event['interface'],event.event['on_behalf_of'],event.event['reason'],event.event['directive_id'])==
        (s.actor.id,s.actor.kind,ctx.interface.value,ctx.on_behalf_of,ctx.reason,ctx.directive_id))
    expected_touches={}
    def touch(kind,key,after,before=None,action='create',version_before=None,version_after=1):
        require((kind,key) not in expected_touches)
        expected_touches[kind,key]=(audit.encode_snapshot(before),audit.encode_snapshot(after),action,version_before,version_after)
    for change in bundle.headers:touch('transaction',change.after.id,change.after.model_dump(),change.before.model_dump(),'update',change.before.version,change.after.version)
    from bookflow.company import payments,sales
    owner=payments if source.action.kind.startswith('payment_') else sales
    owner_kinds=list(owner.TABLE_KINDS)+([] if owner is payments else [('work_billing_allocations','work_billing_allocation','id')])
    for table,kind,key in owner_kinds:
        for value in getattr(bundle.source_rows,table):
            raw=value.model_dump()
            # Existing source audit omits new nullable posting-source extension.
            original=next(v for v in expected.get(table,()) if v[key]==raw[key])
            touch(kind,raw[key],rows.decoded(original))
    for table,kind,key in DEPOSIT_KINDS:
        for value in d['pending'][table]:touch(kind,value[key],rows.decoded(value))
    for cp in (data.get('custom_plan'),resolved.deposit_custom_plan if d['data']['changed'] else None):
        if cp:
            for value in custom.touches(cp):touch(value.record_type,value.record_id,value.after,value.before,value.action,value.version_before,value.version_after)
    from bookflow.company import deposit_draft_consumption as consumption
    consumed=consumption.build(s,ctx,d['data'],bundle.prepared.binding)
    require(bundle.output.current_draft==consumption.projected(consumed))
    require(bundle.output.effect.deposit.consumed_draft==consumption.receipt(d['data'],d['financial']))
    if consumed is not None:
        require(json.loads(operation['request_snapshot'])['resolved_draft']==d['data']['draft'])
        b,a,r=consumed['before'],consumed['after'],consumed['row']
        touch('deposit_draft',a['id'],a,b,'update',b['version'],a['version'])
        touch('deposit_draft_consumption',r['operation_id'],r)
    touch('deposit_operation',operation['id'],rows.decoded(operation))
    require(len(event.entries)==len(expected_touches))
    require({(v['record_type'],v['record_id']):(v['before'],v['after'],v['action'],v['version_before'],v['version_after']) for v in event.entries}==expected_touches)
    require(len({v['id'] for v in event.entries})==len(event.entries))
    require({v.physical_id for v in identities if v.owner_kind=='audit_entries'}=={v['id'] for v in event.entries})
    require(not ({v['id'] for v in event.entries}&set(generated)))
    for value in event.entries:
        require(value['event_id']==event.event['id'])
        require(not rows.rows(s,c.audit_entries,c.audit_entries.c.id==value['id']))
    require(not rows.rows(s,c.audit_events,c.audit_events.c.id==event.event['id']))
    require(event.event['seq']==audit.next_seq(s.company,c.audit_events))
    expected_ids=[(v.owner_kind,v.logical_key,v.physical_id) for v in coordination.source_identity_map(plan,payment=source.action.kind.startswith('payment_')).entries]
    for key,value in (('event',d['data']['event']),('operation_id',d['data']['operation_id'])):
        if not any(item[2]==value for item in expected_ids):expected_ids.append(('aggregate',key,value))
    for table,values in d['pending'].items():
        if table!='deposit_row_keys':
            expected_ids.extend(('deposit/'+table,str(index),value['id']) for index,value in enumerate(values) if 'id' in value)
    cp=resolved.deposit_custom_plan
    if cp:expected_ids.extend(('deposit/custom_field_values',v.definition_id,v.row_id) for v in cp.owner_plan.mutations if v.operation=='insert')
    expected_ids.extend(('audit_entries',str(index),v['id']) for index,v in enumerate(event.entries))
    require([(v.owner_kind,v.logical_key,v.physical_id) for v in identities]==expected_ids)
    require(set(event.event)==set(c.audit_events.columns.keys()) and event.event['undo_of_event_id'] is None)
    for key in ('on_behalf_of','client_name','client_version','client_host','session_id','request_id','idempotency_key','reason','directive_id','source_ref'):
        require(event.event[key]==getattr(ctx,key))
    require(event.event['command']=='deposit coordinate' and event.event['summary']=='Coordinate source and deposit')
    require(event.event['directive_code']==getattr(s,'directive_code',None))
    for value in event.entries:require(set(value)==set(c.audit_entries.columns.keys()))
    from bookflow.company import deposit_operations as operations
    request=operations.request(resolved.input,ctx,s,'coordinate',original=True)
    request.update(resolved_transaction_ids=list(bundle.output.effect.target_ids),resolved_identity_map=[v.model_dump(mode='json') for v in identities],
        item_manifests={kind:dict(count=len(items),digest=q.digest(items)) for kind,items in collections(bundle.output).items()})
    if consumed is not None:request['resolved_draft']=d['data']['draft']
    require(operation==dict(id=d['data']['operation_id'],operation_key=resolved.input.operation_key,command='deposit coordinate',transaction_id=h['id'],
        request_hash=q.digest(operations.request(resolved.input,ctx,s,'coordinate')),request_snapshot=q.canonical(request),effect_snapshot=bundle.output.model_dump_json(),
        created_at=d['data']['at'],created_by=s.actor.id,created_via=ctx.interface.value,audit_event_id=d['data']['event']))



def logical_collections(output):
    """Owner-typed references only; original snapshots and literal strings stay exact."""
    from pydantic import BaseModel
    from bookflow.company.deposit_operation_pages import typed_collections
    mapping={v.physical_id:v.owner_kind+'/'+v.logical_key for v in output.effect.identities}
    fields={
        'SourceRow':('row_id',), 'Additional':('row_id',),
        'CashSource':('revision_id','business_batch_id'),
        'CashComponent':('document_line_id','posting_line_id','posting_source_id','physical_component_id'),
        'Cell':('row_id',), 'BankEffect':('row_id',),
        'MembershipChange':('claim_id','reverses_membership_id'),
        'PaymentComponentOutput':('component_key_id','component_id'),
        'PaymentApplicationOutput':('application_id','reverses_application_id','source_component_key_id'),
        'PaymentAllocationOutput':('allocation_id','application_id','reverses_allocation_id'),
        'SalesComponentItem':('revision_id','document_line_id','physical_component_id'),
        'CoordinateCustomValue':('id',),
    }
    for table,references in coordination.SOURCE_REFERENCES.items():
        fields['Coordinate'+''.join(part.title() for part in table.split('_'))+'Row']=references
    pending=output.effect.source.inserted
    at=next((row.created_at for table in type(pending).model_fields for row in getattr(pending,table) if hasattr(row,'created_at')),None)
    def visit(value,*,new=True):
        if isinstance(value,BaseModel):
            name=type(value).__name__;raw=value.model_dump(mode='json')
            for field in type(value).model_fields:
                child=getattr(value,field)
                if isinstance(child,(BaseModel,list,tuple)):
                    raw[field]=visit(child,new=new and field!='before')
            for field in fields.get(name,()):
                if isinstance(raw.get(field),str):raw[field]=mapping.get(raw[field],raw[field])
            if name=='Cell' and value.bucket.startswith('additional:'):
                raw['bucket']='additional:'+mapping.get(value.bucket[11:],value.bucket[11:])
            if new and name.startswith('Coordinate') and name.endswith('Row'):
                times=('updated_at','voided_at') if name=='CoordinateTransactionsRow' else ('created_at',)
                for key in times:
                    if at is not None and raw.get(key)==at:raw[key]='aggregate/at'
                if name=='CoordinateTransactionRevisionsRow':
                    snapshot=json.loads(value.custom_fields_snapshot)
                    for captured in snapshot.values():captured['value_id']=mapping.get(captured['value_id'],captured['value_id'])
                    raw['custom_fields_snapshot']=q.canonical(snapshot)
            return raw
        if isinstance(value,(list,tuple)):return [visit(item,new=new) for item in value]
        return value
    return {kind:[visit(value) for value in values] for kind,values in typed_collections(output).items()}


def validate_deposit_columns(s,ctx,bundle):
    """Independent full-column equations for the deposit owner's new images."""
    d=bundle.deposit_bundle;data=d['data'];h=d['header'];old=data['before'];f=d['financial'];pending=d['pending']
    require({k:v for k,v in data.items() if k!='bank_effects'}=={k:v for k,v in json.loads(bundle.prepared.resolution.deposit_data_json).items() if k!='bank_effects'})
    at=data['at'];event=data['event'];identity=old['id']
    provenance=dict(created_at=at,created_by=s.actor.id,created_via=ctx.interface.value)
    audited=dict(**provenance,audit_event_id=event)
    expected_header=dict(old)
    if data['changed']:
        expected_header.update(version=old['version']+1,updated_at=at,updated_by=s.actor.id,updated_via=ctx.interface.value)
        if bundle.deposit_plan.verb=='void':
            inverse=next(v for v in pending['posting_batches'] if v['kind']=='reversal')
            expected_header.update(status='voided',voided_at=at,voided_by=s.actor.id,void_reason=ctx.reason.strip(),void_posting_batch_id=inverse['id'])
        else:expected_header.update(number=data['number'],current_revision_id=d['revision']['id'])
    require(h==expected_header)
    if not data['changed']:return
    if bundle.deposit_plan.verb!='void':
        r=d['revision'];prior=data['prior']
        require(r==dict(id=r['id'],**audited,transaction_id=identity,revision_number=prior['revision_number']+1,
            supersedes_revision_id=prior['id'],date=f.intent.date,number=data['number'],name_type=None,name_id=None,memo=data['memo'],
            total_minor_units=f.posting_total,currency=f.intent.currency,issuer_snapshot=q.canonical(data['issuer']),
            custom_fields_snapshot=q.canonical(bundle.deposit_plan.custom_plan.snapshot)))
        require(pending['transaction_revisions']==[r])
        require(pending['deposit_profiles']==[dict(revision_id=r['id'],transaction_id=identity,type='deposit',bank_account_id=f.intent.bank.id,
            posting_total=f.posting_total,subtotal=f.subtotal,bank_total=f.bank_total,cash_back=f.cash_back,facts_snapshot=f.model_dump_json(),**audited)])
        expected_rows={v.row_id:(v.ordinal,'source',v.memo) for v in f.intent.sources}
        expected_rows.update({v.row_id:(v.ordinal,'additional',v.memo) for v in f.intent.additional})
        expected_rows[data['header_row']]=(data['header_ordinal'],'header',data['memo'])
        oldkeys={v['id']:v for v in rows.rows(s,c.deposit_row_keys,c.deposit_row_keys.c.transaction_id==identity)}
        newkeys={v['id']:v for v in pending['deposit_row_keys']}
        require(len(newkeys)==len(pending['deposit_row_keys']) and set(newkeys)==set(expected_rows)-set(oldkeys))
        for key,value in newkeys.items():
            ordinal,kind,_=expected_rows[key]
            require(value==dict(id=key,transaction_id=identity,line_id=key,ordinal=ordinal,kind=kind,**audited))
        require({v['id'] for v in pending['document_line_identities']}==set(newkeys))
        for value in pending['document_line_identities']:require(value==dict(id=value['id'],transaction_id=identity,**provenance))
        keys=oldkeys|newkeys
        require(len(pending['document_lines'])==len(expected_rows))
        for value in pending['document_lines']:
            key=next(k for k,v in keys.items() if v['line_id']==value['line_id'])
            ordinal,_,memo=expected_rows[key]
            require(value==dict(id=value['id'],**provenance,transaction_id=identity,revision_id=r['id'],line_id=keys[key]['line_id'],position=ordinal,
                kind='deposit',account_id=None,side=None,amount_minor_units=None,currency=f.intent.currency,account_snapshot=None,name_type=None,name_id=None,
                party_name=None,class_id=None,class_name=None,description=memo,original_minor_units=None,original_currency=None,rate_used=None,rate_source=None))
        business=next(v for v in pending['posting_batches'] if v['kind']=='replacement')
        oldbatch=rows.rows(s,c.posting_batches,c.posting_batches.c.revision_id==prior['id'],c.posting_batches.c.kind!='reversal')
        require(len(oldbatch)==1)
        require(business==dict(id=business['id'],**audited,transaction_id=identity,revision_id=r['id'],kind='replacement',effective_date=f.intent.date,
            reverses_batch_id=None,replaces_batch_id=oldbatch[0]['id']))
        for claim in d['claims']:
            source=next(v for v in f.intent.sources if v.source.transaction_id==claim['source_transaction_id']);cash=source.source
            require(claim==dict(id=claim['id'],**audited,kind='claim',transaction_id=identity,revision_id=r['id'],batch_id=business['id'],row_id=source.row_id,
                source_transaction_id=cash.transaction_id,source_revision_id=cash.revision_id,source_batch_id=cash.business_batch_id,
                amount_minor_units=cash.cash_minor_units,currency=cash.currency,source_date=cash.receipt_date,facts_snapshot=cash.model_dump_json(),reverses_membership_id=None))
    # Receipt fields describe these exact rows, including original and current.
    from bookflow.company import deposit_operations, deposit_persistence
    output=bundle.output;effect=output.effect.deposit
    require(effect.before==deposit_operations.state(s,identity) and effect.after==deposit_persistence._state(h,f))
    require(output.current==effect.after and effect.financial==f and effect.audit_event_id==event)
    require(effect.batch_ids==tuple(v['id'] for v in pending['posting_batches']))


def validate_deposit_business_columns(s,ctx,bundle):
    """Full pending posting/key/cell/version images, including captured labels."""
    d=bundle.deposit_bundle;data=d['data'];pending=d['pending'];f=d['financial'];identity=d['header']['id']
    if not data['changed']:return
    prov=dict(created_at=data['at'],created_by=s.actor.id,created_via=ctx.interface.value)
    audited=dict(**prov,audit_event_id=data['event'])
    prior=data['prior']
    originals=rows.rows(s,c.posting_batches,c.posting_batches.c.revision_id==prior['id'],c.posting_batches.c.kind!='reversal')
    require(len(originals)==1);original=originals[0]
    reverse=next(v for v in pending['posting_batches'] if v['kind']=='reversal')
    require(reverse==dict(id=reverse['id'],**audited,transaction_id=identity,revision_id=prior['id'],kind='reversal',
        effective_date=original['effective_date'],reverses_batch_id=original['id'],replaces_batch_id=None))
    business=original if bundle.deposit_plan.verb=='void' else next(v for v in pending['posting_batches'] if v['kind']=='replacement')
    for key in pending['bank_effect_keys']:
        require(key==dict(id=key['id'],transaction_id=identity,role=key['role'],row_id=key['row_id'],**audited))
    for value in pending['bank_effect_versions']:
        require(value['batch_id']==business['id'])
    if bundle.deposit_plan.verb=='void':return
    revision=d['revision'];currency=f.intent.currency
    keys={v['id']:v for v in rows.rows(s,c.deposit_row_keys,c.deposit_row_keys.c.transaction_id==identity)}
    keys.update({v['id']:v for v in pending['deposit_row_keys']})
    envelopes={v['line_id']:v for v in pending['document_lines']}
    components={(v['row_id'],v['component_ordinal']):v for v in pending['deposit_components']}
    expected_keys={}
    for source in f.intent.sources:
        for occurrence in source.occurrences:
            key=occurrence.key
            expected_keys[source.row_id,occurrence.ordinal]=(key.kind,key.identity,key.tax_item)
    expected_keys.update({(v.row_id,0):('additional',v.row_id,'') for v in f.intent.additional})
    expected_keys[data['header_row'],1]=('header',data['header_row'],'')
    oldkeys={(v['row_id'],v['ordinal']):v for v in rows.rows(s,c.deposit_component_keys,c.deposit_component_keys.c.transaction_id==identity)}
    newkeys={(v['row_id'],v['ordinal']):v for v in pending['deposit_component_keys']}
    require(len(newkeys)==len(pending['deposit_component_keys']) and set(newkeys)==set(expected_keys)-set(oldkeys))
    for key,value in newkeys.items():
        kind,semantic,tax=expected_keys[key]
        require(value==dict(id=value['id'],**audited,transaction_id=identity,row_id=key[0],ordinal=key[1],kind=kind,semantic_identity=semantic,tax_item_id=tax))
    for component in components.values():
        require(component['document_line_id']==envelopes[keys[component['row_id']]['line_id']]['id'])
        require(component['currency']==currency)
    for value in pending['deposit_cash_cells']:
        require(value['transaction_id']==identity and value['revision_id']==revision['id'] and value['currency']==currency)
        if value['bucket']!='additional':require(value['bucket_row_id']==data['header_row'])
    accounts={v.id:v for v in (f.intent.bank,*[v.account for v in f.intent.additional],*([f.intent.cash_back.account] if f.intent.cash_back else []))}
    posted=[v for v in pending['posting_lines'] if v['batch_id']==business['id']]
    require(len(posted)==len(f.legs))
    for index,(value,leg) in enumerate(zip(posted,f.legs),1):
        account=accounts.get(leg.account_id)
        if account is not None:snapshot={k:getattr(account,k) for k in ('id','name','full_name','number','type','normal_balance')}
        else:
            _,rowid,ordinal=leg.key.split('/')
            source=next(v for v in f.intent.sources if v.row_id==rowid)
            semantic=next(v.key for v in source.occurrences if v.ordinal==int(ordinal))
            component=next(v for v in source.source.components if v.key==semantic)
            found=[v.model_dump() for v in bundle.source_rows.posting_lines if v.id==component.posting_line_id]
            found+=rows.rows(s,c.posting_lines,c.posting_lines.c.id==component.posting_line_id)
            require(len(found)==1);snapshot=json.loads(found[0]['account_snapshot'])
        dims=leg.dimensions
        require(value==dict(id=value['id'],**prov,transaction_id=identity,batch_id=business['id'],line_no=index,account_id=leg.account_id,
            debit_minor_units=max(0,leg.signed_debit),credit_minor_units=max(0,-leg.signed_debit),currency=currency,account_snapshot=q.canonical(snapshot),
            name_type=dims.party_kind,name_id=dims.party_id,party_name=dims.party_name,class_id=dims.class_id,class_name=dims.class_name,
            description=data['memo'],original_minor_units=None,original_currency=None,rate_used=None,rate_source=None,reversed_line_id=None))
    for value in pending['posting_line_sources']:
        if value['reversed_source_id'] is not None:continue
        require(value['currency']==currency and value.get('tax_component_id') is None and value.get('payment_component_id') is None)
