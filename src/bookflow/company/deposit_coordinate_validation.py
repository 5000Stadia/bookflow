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
    require(coordination.prepare_source_overlay(s,ctx,resolved.input,source,bundle.prepared.binding)==resolved.overlay)
    expected=dict(data['pending'])
    if data.get('billing_allocations'):expected['work_billing_allocations']=data['billing_allocations']
    require(bundle.source_rows==typed_source(expected))
    require(source_before(s,resolved.overlay.source_id)==bundle.output.effect.source.before)
    deposit_persistence_validation.validate_rows(s,ctx,bundle.deposit_plan,bundle.deposit_bundle)
    d=bundle.deposit_bundle;financial=d['financial'];old=d['data']['before'];h=d['header']
    if d['data']['changed'] and bundle.deposit_plan.verb!='void':
        previous=d['data']['previous']
        from bookflow.company.deposit_models import Effect
        deposit_validation.validate_references(financial,s,previous=Effect.model_validate_json(q.canonical(previous)))
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
    require(len(set(generated))==len(generated))
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
    def touch(kind,key,after,before=None):
        require((kind,key) not in expected_touches)
        expected_touches[kind,key]=(audit.encode_snapshot(before),audit.encode_snapshot(after))
    for change in bundle.headers:touch('transaction',change.after.id,change.after.model_dump(),change.before.model_dump())
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
            for value in custom.touches(cp):touch(value.record_type,value.record_id,value.after,value.before)
    touch('deposit_operation',operation['id'],rows.decoded(operation))
    require(len(event.entries)==len(expected_touches))
    require({(v['record_type'],v['record_id']):(v['before'],v['after']) for v in event.entries}==expected_touches)
    require(len({v['id'] for v in event.entries})==len(event.entries))


def logical_collections(output):
    """Closed typed collection projection for prospective continuation only."""
    from bookflow.company.deposit_operation_pages import collections
    # Pending source physical IDs are domain-qualified; typed fields alone may
    # substitute them. Financial/text values are never traversed by value.
    mapping={v.physical_id:v.owner_kind+'/'+v.logical_key for v in output.effect.identities}
    from bookflow.company.deposit_coordinate_models import CoordinateTransactionsRow
    from pydantic import BaseModel
    identity_fields={'id','revision_id','current_revision_id','void_posting_batch_id','row_id','source_id','claim_id','reverses_membership_id',
        'document_line_id','line_id','posting_line_id','posting_source_id','physical_component_id','business_batch_id','batch_id','source_revision_id',
        'source_component_id','source_posting_source_id','target_revision_id','target_document_line_id','target_line_id','target_ar_source_id',
        'target_recognition_source_id','application_id','allocation_id','component_id','component_key_id','reverses_application_id','reverses_allocation_id',
        'audit_event_id','supersedes_revision_id','tax_component_id'}
    # Collections contain owned typed projections. Exclude all embedded captured
    # snapshots and arbitrary custom content from this mechanical reference map.
    def visit(value,key=None):
        if isinstance(value,dict):
            return {name:(item if name.endswith('_snapshot') else visit(item,name)) for name,item in value.items()}
        if isinstance(value,list):return [visit(item,key) for item in value]
        if key in identity_fields and isinstance(value,str):return mapping.get(value,value)
        if key in ('created_at','updated_at','voided_at') and value==output.effect.deposit.after.revision_date:
            return value
        return value
    return {kind:visit(values) for kind,values in collections(output).items()}
