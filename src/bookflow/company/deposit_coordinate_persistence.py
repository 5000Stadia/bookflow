"""Private deposit/source aggregate writer. Caller owns the outer transaction.

No child applier, business staging, source-claim bypass or commit is used here.
"""
from dataclasses import dataclass
import json
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects as rows, payment_queries as q
from bookflow.company import deposit_coordination as coordination, deposit_persistence as deposit
from bookflow.company import deposit_operations as operations, deposit_dependency_history as history
from bookflow.company import deposit_dependencies as dependencies, journal_custom_fields as custom
from bookflow.company import payments, sales, deposits, bank_effects
from bookflow.company.deposit_lifecycle import Prepared
from bookflow.company.deposit_lifecycle_models import LifecycleEffect, HeaderChange, MembershipChange
from bookflow.company.deposit_coordinate_models import (
    PreparedCoordinate, SourceRows, SourceEvidence, CoordinateHeader, CoordinateIdentity,
    CoordinateTransactionsRow, CoordinateEffect, CoordinateOutput, CoordinateCustomChange)
from bookflow.company.deposit_models import Effect
from bookflow.core import audit
from bookflow.core.registry import Touched
from bookflow.core.errors import BookflowError

DEPOSIT_KINDS = (
    ('transaction_revisions','transaction_revision','id'), ('deposit_profiles','deposit_profile','revision_id'),
    ('document_line_identities','document_line_identity','id'), ('deposit_row_keys','deposit_row_key','id'),
    ('document_lines','document_line','id'), ('deposit_component_keys','deposit_component_key','id'),
    ('deposit_components','deposit_component','id'), ('posting_batches','posting_batch','id'),
    ('posting_lines','posting_line','id'), ('posting_line_sources','posting_line_source','id'),
    ('deposit_cash_cells','deposit_cash_cell','id'), ('deposit_memberships','deposit_membership','id'),
    ('bank_effect_keys','bank_effect_key','id'), ('bank_effect_versions','bank_effect_version','id'))


@dataclass(frozen=True)
class CoordinateRows:
    prepared: PreparedCoordinate
    deposit_plan: Prepared
    deposit_bundle: dict
    source_rows: SourceRows
    headers: tuple[CoordinateHeader, ...]
    output: CoordinateOutput
    operation_json: str
    items_json: str
    audit_event: audit.PreparedEvent


def typed_source(values):
    # Only the nullable posting-source extension may be absent from a producer.
    material = {}
    for table, entries in values.items():
        material[table] = [dict(row, **{key:row.get(key) for key in ('tax_component_id','payment_component_id','deposit_component_id')})
            if table=='posting_line_sources' else row for row in entries]
    return SourceRows.model_validate_json(q.canonical(material))


def source_before(s, identity):
    material = {}
    for table in SourceRows.model_fields:
        owner=getattr(c,table)
        if table=='applications':
            conditions=[sa.or_(owner.c.paying_transaction_id==identity,owner.c.paid_transaction_id==identity)]
        elif table=='application_allocations':
            conditions=[sa.or_(owner.c.source_transaction_id==identity,owner.c.target_transaction_id==identity)]
        else:
            conditions=[owner.c.transaction_id==identity]
        material[table]=rows.rows(s,owner,*conditions)
    return typed_source(material)


def target_ids(s, prepared, headers, before, inserted):
    resolved=prepared.resolution;data=json.loads(resolved.deposit_data_json)
    result=set(data['targets'])|{resolved.input.deposit}|{v.before.id for v in headers}
    result.update(dependencies.historical_sources(s,resolved.input.deposit))
    from bookflow.company.deposit_dependency_models import ReadSet
    result.update(ReadSet.model_validate_json(prepared.readset_json).transactions)
    for collection in (before,inserted):
        for value in collection.applications:
            result.update((value.paying_transaction_id,value.paid_transaction_id))
        for value in collection.application_allocations:
            result.update((value.source_transaction_id,value.target_transaction_id))
        for value in collection.work_billing_allocations:
            result.add(value.transaction_id)
    return tuple(sorted(result))


def build(s,ctx,prepared):
    resolved=prepared.resolution;source=resolved.source;data=json.loads(resolved.deposit_data_json)
    plan=Prepared('void' if resolved.input.replacement.mode=='void' else 'update',
        resolved.input.model_dump_json(by_alias=True,exclude_unset=True),prepared.facts_fingerprint,
        prepared.dependency_guard,resolved.deposit_data_json,resolved.deposit_custom_plan,prepared.binding)
    bundle=deposit.build(s,ctx,plan,source_overlay=resolved.overlay)
    collected=(payments if source.action.kind.startswith('payment_') else sales).coordinate_rows_and_touches(source.plan)
    inserted=typed_source({table:list(values) for table,values,_ in collected})
    before=source_before(s,resolved.overlay.source_id)
    changes=[]
    if source.plan.preview.changed:
        changes.append((source.plan.data['before'],source.plan.data['header']))
        changes.extend(source.plan.data.get('changed_headers',()))
    if data['changed']:
        changes.append((data['before'],bundle['header']))
        changes.extend(bundle['source_headers'])
    coalesced=coordination.coalesce_headers(changes,source.provenance,s.actor.id,ctx.interface.value)
    headers=tuple(CoordinateHeader.model_validate_json(q.canonical(dict(before=b,after=a))) for b,a in coalesced)
    final_source=next((h.after for h in headers if h.after.id==resolved.overlay.source_id),
        CoordinateTransactionsRow.model_validate_json(q.canonical(json.loads(resolved.overlay.before_header_json))))
    targets=target_ids(s,prepared,headers,before,inserted)
    history._authorize_binding_graph(s,prepared.binding,targets,write=True)
    identity_map=[CoordinateIdentity(owner_kind=v.owner_kind,logical_key=v.logical_key,physical_id=v.physical_id)
        for v in coordination.source_identity_map(source.plan,payment=source.action.kind.startswith('payment_')).entries]
    for key,value in (('event',data['event']),('operation_id',data['operation_id'])):
        if not any(v.physical_id==value for v in identity_map):
            identity_map.append(CoordinateIdentity(owner_kind='aggregate',logical_key=key,physical_id=value))
    for table, values in bundle['pending'].items():
        for index,value in enumerate(values):
            if 'id' in value and table!='deposit_row_keys':identity_map.append(CoordinateIdentity(owner_kind='deposit/'+table,logical_key=str(index),physical_id=value['id']))
    if resolved.deposit_custom_plan:
        for value in resolved.deposit_custom_plan.owner_plan.mutations:
            if value.operation=='insert':identity_map.append(CoordinateIdentity(owner_kind='deposit/custom_field_values',logical_key=value.definition_id,physical_id=value.row_id))
    from bookflow.core.ids import new_id
    custom_plans=(source.plan.data.get('custom_plan'),resolved.deposit_custom_plan if data['changed'] else None)
    from bookflow.company import deposit_draft_consumption as consumption
    consumed=consumption.build(s,ctx,data,prepared.binding)
    entry_count=len(consumption.touches(consumed))+len(headers)+sum(len(values) for _,values,_ in collected)+sum(len(values) for values in bundle['pending'].values())+1
    entry_count+=sum(len(custom.touches(cp)) for cp in custom_plans if cp)
    entry_ids=tuple(new_id() for _ in range(entry_count))
    identity_map.extend(CoordinateIdentity(owner_kind='audit_entries',logical_key=str(index),physical_id=value) for index,value in enumerate(entry_ids))
    inverse=next((b for b in bundle['pending']['posting_batches'] if b['kind']=='reversal'),None)
    financial=bundle['financial'];h=bundle['header']
    effect=LifecycleEffect(consumed_draft=consumption.receipt(data,financial),action=plan.verb,before=operations.state(s,h['id']),after=deposit._state(h,financial),
        financial=financial,reversal=deposits.inverse(Effect.model_validate_json(q.canonical(data['previous'])),inverse['reverses_batch_id']) if inverse else None,
        batch_ids=tuple(b['id'] for b in bundle['pending']['posting_batches']),
        memberships=tuple(MembershipChange(source_id=v['source_transaction_id'],claim_id=v['id'],kind=v['kind'],
            reverses_membership_id=v['reverses_membership_id'],amount_minor_units=v['amount_minor_units'],currency=v['currency']) for v in bundle['pending']['deposit_memberships']),
        headers=tuple(HeaderChange(id=v.after.id,before_version=v.before.version,after_version=v.after.version) for v in headers),
        bank_effects=tuple(bank_effects.BankEffect.model_validate(v) for v in bundle['data'].get('bank_effects',())),audit_event_id=data['event'])
    evidence=SourceEvidence(action=source.action,before_header=CoordinateTransactionsRow.model_validate_json(resolved.overlay.before_header_json),
        after_header=final_source,before=before,inserted=inserted,
        custom_changes=tuple(CoordinateCustomChange.model_validate_json(q.canonical(dict(before=v.before,after=v.after))) for v in custom.touches(source.plan.data['custom_plan'])) if source.plan.data.get('custom_plan') else (),
        payment_effect=source.plan.preview if source.action.kind.startswith('payment_') else None,bank_changes=source.bank_changes)
    current_headers=[]
    assigned={v.after.id:v.after for v in headers}
    for identity in targets:
        current_headers.append(assigned.get(identity) or CoordinateTransactionsRow.model_validate_json(q.canonical(rows.rows(s,c.transactions,c.transactions.c.id==identity)[0])))
    output=CoordinateOutput(current_draft=consumption.projected(consumed),operation_key=resolved.input.operation_key,operation_id=data['operation_id'],changed=resolved.changed,new_effect=resolved.changed,
        facts_fingerprint=prepared.facts_fingerprint,dependency_guard=prepared.dependency_guard,
        effect=CoordinateEffect(source=evidence,deposit=effect,headers=headers,identities=tuple(identity_map),target_ids=targets),
        current=effect.after,current_headers=tuple(current_headers),current_source_rows=typed_source({table:[v.model_dump() for v in getattr(before,table)]+[v.model_dump() for v in getattr(inserted,table)] for table in SourceRows.model_fields}))
    from bookflow.company.deposit_operation_pages import collections
    items=[dict(operation_id=data['operation_id'],kind=kind,ordinal=index,facts_snapshot=q.canonical(value))
        for kind,values in collections(output).items() for index,value in enumerate(values)]
    request=operations.request(resolved.input,ctx,s,'coordinate',original=True)
    request.update(resolved_transaction_ids=list(targets),resolved_identity_map=[v.model_dump(mode='json') for v in identity_map],
        item_manifests={kind:dict(count=len(values),digest=q.digest(values)) for kind,values in collections(output).items()})
    if consumed is not None:request['resolved_draft']=data['draft']
    operation=dict(id=data['operation_id'],operation_key=resolved.input.operation_key,command='deposit coordinate',transaction_id=h['id'],
        request_hash=q.digest(operations.request(resolved.input,ctx,s,'coordinate')),request_snapshot=q.canonical(request),effect_snapshot=output.model_dump_json(),
        created_at=data['at'],created_by=s.actor.id,created_via=ctx.interface.value,audit_event_id=data['event'])
    touched=consumption.touches(consumed)+[Touched('transaction',v.after.id,'update',v.before.version,v.after.version,v.after.model_dump(),v.before.model_dump(),db='company') for v in headers]
    for _,_,values in collected:touched.extend(values)
    for table,kind,key in DEPOSIT_KINDS:
        touched.extend(Touched(kind,v[key],'create',None,1,rows.decoded(v),db='company') for v in bundle['pending'][table])
    for cp in (source.plan.data.get('custom_plan'),resolved.deposit_custom_plan if data['changed'] else None):
        if cp:touched.extend(custom.touches(cp))
    touched.append(Touched('deposit_operation',operation['id'],'create',None,1,rows.decoded(operation),db='company'))
    event=audit.prepare_event_to(s.company,ctx,'deposit coordinate','Coordinate source and deposit',touched,
        actor_id=s.actor.id,actor_kind=s.actor.kind,directive_code=getattr(s,'directive_code',None),event_id=data['event'],at=data['at'],entry_ids=entry_ids)
    event=audit.PreparedEvent(dict(event.event,undo_of_event_id=None),event.entries)
    return CoordinateRows(prepared,plan,bundle,inserted,headers,output,q.canonical(operation),q.canonical(items),event)


def _foreign_keys(s):
    if s.company.raw.execute('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise BookflowError('E_INTERNAL',message='Invalid aggregate foreign-key state.')


def execute(s,ctx,prepared):
    if type(prepared) is not PreparedCoordinate or not s.company.raw.in_transaction or s.dry_run:
        raise RuntimeError('Coordinate execution requires a prepared intent and owned writer transaction')
    inp=prepared.resolution.input
    recovered=recover(s,ctx,inp,prepared.binding)
    if recovered is not None:return recovered
    if inp.dependency_guard is None:
        raise BookflowError('E_PREVIEW_STALE',details={'reason':'complete deposit guard required'})
    if s.company.raw.execute('PRAGMA foreign_keys').fetchone()[0]!=1:
        raise RuntimeError('Coordinate execution requires foreign-key enforcement')
    _foreign_keys(s)
    fresh=coordination.validate(s,ctx,prepared)
    if operations.find(s,inp.operation_key) is not None:
        raise BookflowError('E_DEPOSIT_OPERATION_KEY_REUSED')
    bundle=build(s,ctx,fresh)
    from bookflow.company.deposit_coordinate_validation import validate
    validate(s,ctx,bundle)
    from bookflow.company import deposit_draft_consumption as consumption
    consumed=consumption.build(s,ctx,bundle.deposit_bundle['data'],prepared.binding)
    s.company.raw.execute('SAVEPOINT bookflow_deposit_coordinate')
    try:
        audit.insert_prepared_event(s.company,bundle.audit_event)
        for change in bundle.headers:
            count=s.company.conn.execute(c.transactions.update().where(c.transactions.c.id==change.before.id,
                c.transactions.c.version==change.before.version).values(**change.after.model_dump())).rowcount
            if count!=1:raise BookflowError('E_VERSION_CONFLICT')
        owner=payments if fresh.resolution.source.action.kind.startswith('payment_') else sales
        # Closed owner order, not metadata order or caller-supplied table names.
        for table,_,_ in owner.coordinate_rows_and_touches(fresh.resolution.source.plan):
            for row in getattr(bundle.source_rows,table):
                s.company.conn.execute(getattr(c,table).insert().values(**row.model_dump()))
        dbundle=bundle.deposit_bundle
        if dbundle['data']['changed']:
            for table,_,_ in DEPOSIT_KINDS:
                for row in dbundle['pending'][table]:s.company.conn.execute(getattr(c,table).insert().values(**row))
            for claim in dbundle['data']['claims']:
                count=s.company.conn.execute(c.deposit_current_memberships.delete().where(c.deposit_current_memberships.c.membership_id==claim['id'])).rowcount
                if count!=1:raise BookflowError('E_VERSION_CONFLICT')
            for claim in dbundle['claims']:
                s.company.conn.execute(c.deposit_current_memberships.insert().values(source_transaction_id=claim['source_transaction_id'],transaction_id=claim['transaction_id'],membership_id=claim['id']))
            for pointer in dbundle['bank_current']:
                prior=rows.rows(s,c.bank_effect_current,c.bank_effect_current.c.key_id==pointer['key_id'])
                if prior:
                    count=s.company.conn.execute(c.bank_effect_current.update().where(c.bank_effect_current.c.key_id==pointer['key_id'],
                        c.bank_effect_current.c.version_id==prior[0]['version_id']).values(**pointer)).rowcount
                    if count!=1:raise BookflowError('E_VERSION_CONFLICT')
                else:s.company.conn.execute(c.bank_effect_current.insert().values(**pointer))
        for cp in (fresh.resolution.source.plan.data.get('custom_plan'),fresh.resolution.deposit_custom_plan if dbundle['data']['changed'] else None):
            if cp:custom.apply(s.company,cp)
        # Existing-document corrections never advance automatic sequences.
        if fresh.resolution.source.plan.data.get('sequence') is not None or dbundle['data']['sequence'] is not None:
            raise BookflowError('E_INTERNAL',message='Unexpected correction sequence allocation.')
        s.company.conn.execute(c.deposit_operations.insert().values(**json.loads(bundle.operation_json)))
        for target in bundle.output.effect.target_ids:
            s.company.conn.execute(c.deposit_operation_targets.insert().values(operation_id=bundle.output.operation_id,transaction_id=target))
        for item in json.loads(bundle.items_json):s.company.conn.execute(c.deposit_operation_items.insert().values(**item))
        from bookflow.company import deposit_draft_consumption as consumption
        consumption.persist(s,consumed)
        _foreign_keys(s)
        consumption.validate_persisted(s,consumed)
        s.company.raw.execute('RELEASE bookflow_deposit_coordinate')
    except BaseException:
        s.company.raw.execute('ROLLBACK TO bookflow_deposit_coordinate')
        s.company.raw.execute('RELEASE bookflow_deposit_coordinate')
        raise
    return bundle.output


def recover(s,ctx,inp,binding):
    history._authorize_binding_graph(s,binding,(),write=True)
    if ctx.on_behalf_of!=binding.on_behalf_of:raise BookflowError('E_UNAUTHENTICATED')
    saved=operations.find(s,inp.operation_key)
    if saved is None:return None
    from bookflow.company.deposit_operation_pages import authorized_output
    output=authorized_output(s,saved,binding,write=True)
    if saved['command']!='deposit coordinate' or saved['request_hash']!=q.digest(operations.request(inp,ctx,s,'coordinate')):
        return None
    return output.model_copy(update={'changed':False,'new_effect':False,'idempotent_replay':True})


def execute_applied(s,ctx,prepared):
    """Private adapter contract: audit is already written, outer commit is not.

    No registry uses this yet. The caller must not add a second audit event or
    mark the aggregate finalized before its own transaction has committed.
    """
    from bookflow.core.registry import Applied
    return Applied(output=execute(s,ctx,prepared),touched=[],summary='Coordinate deposit and source',audited=True)
