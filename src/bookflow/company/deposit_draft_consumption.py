"""Financial-owner consumption rows; never opens a transaction or savepoint."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_drafts as drafts, payment_queries as q
from bookflow.company import deposit_draft_provider as provider, deposit_draft_validation as validation
from bookflow.core.registry import Touched
from bookflow.core.errors import BookflowError
from bookflow.core.exact import INT64_MAX


def build(s,ctx,data,binding):
    if data.get('draft') is None:return None
    pin=provider.DraftPin.model_validate_json(q.canonical(data['draft']))
    h,r,m=provider.read_pin(s,ctx,pin,binding)
    if h['version']==INT64_MAX:raise BookflowError('E_VALUE_RANGE')
    if h['edit_transaction_id'] is not None:
        validation.require(h['edit_transaction_id']==data['identity'] and h['baseline_version']==data['before']['version'],'consume_target')
    after=dict(h,state='consumed',version=h['version']+1,updated_at=data['at'],updated_by=s.actor.id,updated_via=ctx.interface.value,
        consumed_revision_id=r['id'],consumed_operation_id=data['operation_id'],audit_event_id=data['event'])
    row=dict(operation_id=data['operation_id'],draft_id=h['id'],revision_id=r['id'],manifest_hash=r['manifest_hash'],
        created_at=data['at'],created_by=s.actor.id,created_via=ctx.interface.value,audit_event_id=data['event'])
    return dict(before=h,after=after,row=row)


def touches(value):
    if value is None:return []
    b,a,r=value['before'],value['after'],value['row']
    return [Touched('deposit_draft',a['id'],'update',b['version'],a['version'],a,b,db='company'),
        Touched('deposit_draft_consumption',r['operation_id'],'create',None,1,r,db='company')]


def persist(s,value):
    if value is None:return
    b,a,r=value['before'],value['after'],value['row']
    s.company.conn.execute(c.deposit_draft_consumptions.insert().values(**r))
    count=s.company.conn.execute(c.deposit_drafts.update().where(c.deposit_drafts.c.id==b['id'],
        c.deposit_drafts.c.version==b['version'],c.deposit_drafts.c.state=='open').values(**a)).rowcount
    if count!=1:raise BookflowError('E_VERSION_CONFLICT')


def _validate_consumed(s,header,revision):
    """Validate terminal lifecycle independently of the pinned revision version."""
    found=list(s.company.conn.execute(sa.select(c.deposit_draft_consumptions).where(c.deposit_draft_consumptions.c.draft_id==header['id'])).mappings())
    validation.require(len(found)==1,'consumption')
    row=dict(found[0])
    validation.require((row['revision_id'],row['operation_id'],row['manifest_hash'])==
        (revision['id'],header['consumed_operation_id'],revision['manifest_hash']),'consumption_pin')
    validation.require(header['consumed_revision_id']==header['current_revision_id']==revision['id'] and header['version']==revision['version']+1,'consumption_version')
    operation=drafts._row(s,c.deposit_operations,row['operation_id'])
    validation.require(operation['audit_event_id']==header['audit_event_id']==row['audit_event_id'],'consumption_event')
    if header['edit_transaction_id'] is not None:validation.require(operation['transaction_id']==header['edit_transaction_id'],'consumption_target')
    validation.require(header['state']=='consumed' and revision['draft_id']==header['id'], 'consumption_owner')
    validation.require((row['created_at'],row['created_by'],row['created_via'])==
        (header['updated_at'],header['updated_by'],header['updated_via']), 'consumption_writer')
    try:
        saved=json.loads(operation['request_snapshot'])
        pin=provider.DraftPin.model_validate_json(q.canonical(saved['resolved_draft']))
        output=json.loads(operation['effect_snapshot'])
        effect=output['effect']
        if operation['command']=='deposit coordinate':effect=effect['deposit']
        from bookflow.company.deposit_lifecycle_models import DraftConsumptionReceipt
        receipt=DraftConsumptionReceipt.model_validate_json(q.canonical(effect['consumed_draft']))
    except (ValueError, KeyError, TypeError):
        raise BookflowError('E_VALIDATION',details={'reason':'consumption_receipt'}) from None
    validation.require((pin.id,pin.version,pin.revision_id,pin.revision_number,pin.manifest_hash,pin.snapshot)==
        (header['id'],revision['version'],revision['id'],revision['version'],revision['manifest_hash'],revision['snapshot']), 'consumption_pin_receipt')
    old=json.loads(pin.header_json)
    transitioned=dict(old,state='consumed',version=old['version']+1,consumed_revision_id=revision['id'],
        consumed_operation_id=operation['id'],audit_event_id=operation['audit_event_id'],
        updated_at=operation['created_at'],updated_by=operation['created_by'],updated_via=operation['created_via'])
    validation.require(old['state']=='open' and header==transitioned,'consumption_exact_transition')
    keys=list(s.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(
        c.deposit_draft_row_keys.c.draft_id==header['id']).order_by(c.deposit_draft_row_keys.c.ordinal)).mappings())
    validation.require(q.canonical([dict(k) for k in keys])==pin.keys_json,'consumption_exact_keys')
    validation.require((receipt.draft_id,receipt.version,receipt.revision_id,receipt.manifest_hash,receipt.snapshot)==
        (pin.id,pin.version,pin.revision_id,pin.manifest_hash,pin.snapshot), 'consumption_original_receipt')
    validation.require(effect['after']['id']==operation['transaction_id'] and
        effect['audit_event_id']==row['audit_event_id'], 'consumption_effect_target')
    from bookflow.company.deposit_draft_models import Manifest
    manifest=Manifest.model_validate_json(revision['snapshot'])
    validation.require(q.digest(manifest.model_dump(mode='json'))==revision['manifest_hash'],'consumption_manifest_hash')
    captured={r.ordinal:r.row_id for r in (*manifest.sources,*manifest.additional)}
    financial=effect['financial']['intent']
    actual={r['ordinal']:r['row_id'] for r in (*financial['sources'],*financial['additional'])}
    validation.require(len(actual)==len(captured)==len(receipt.rows), 'consumption_row_count')
    validation.require({r.ordinal:(r.draft_row_id,r.financial_row_id) for r in receipt.rows}==
        {n:(identity,actual.get(n)) for n,identity in captured.items()}, 'consumption_row_map')
    validation.require(len({r.financial_row_id for r in receipt.rows})==len(receipt.rows), 'consumption_row_bijection')


def validate_consumed(s,header,revision):
    try:
        _validate_consumed(s,header,revision)
    except (ValueError,TypeError,KeyError,AttributeError):
        raise BookflowError('E_VALIDATION',details={'reason':'consumption_receipt'}) from None


def validate_persisted(s,value):
    """Both directions, after all aggregate writes and after the final FK fence and before RELEASE."""
    if value is None:return
    header=drafts._row(s,c.deposit_drafts,value['after']['id'])
    validation.require(header==value['after'], 'consumption_persisted_header')
    revision=drafts._row(s,c.deposit_draft_revisions,header['current_revision_id'])
    validate_consumed(s,header,revision)
    rows=list(s.company.conn.execute(sa.select(c.deposit_draft_consumptions).where(
        c.deposit_draft_consumptions.c.operation_id==value['row']['operation_id'])).mappings())
    validation.require(len(rows)==1 and dict(rows[0])==value['row'], 'consumption_persisted_row')


def current(s,operation,*,ctx=None,binding=None,write=False):
    from bookflow.storage.migrate import feature_admission
    if feature_admission(s.company,validation.FEATURE,resolver=lambda _:True) is None:return None
    rows=list(s.company.conn.execute(sa.select(c.deposit_draft_consumptions).where(c.deposit_draft_consumptions.c.operation_id==operation)).mappings())
    if not rows:
        stored=drafts._row(s,c.deposit_operations,operation)
        validation.require('resolved_draft' not in json.loads(stored['request_snapshot']), 'missing_consumption')
        return None
    h,r,m,_=drafts.load(s,rows[0]['draft_id'],ctx=ctx,binding=binding,write=write)
    from bookflow.company.deposit_lifecycle_models import ConsumedDraftState
    return ConsumedDraftState(id=h['id'],version=h['version'],state=h['state'],revision_id=r['id'],manifest_hash=r['manifest_hash'],operation_id=operation)


def receipt(data,financial):
    if data.get('draft') is None:return None
    from bookflow.company.deposit_lifecycle_models import DraftConsumptionReceipt, DraftRowIdentity
    from bookflow.company.deposit_draft_models import Manifest
    pin=provider.DraftPin.model_validate_json(q.canonical(data['draft']))
    manifest=Manifest.model_validate_json(pin.snapshot)
    actual={r.ordinal:r.row_id for r in (*financial.intent.sources,*financial.intent.additional)}
    entries=tuple(DraftRowIdentity(draft_row_id=r.row_id,financial_row_id=actual[r.ordinal],ordinal=r.ordinal)
        for r in sorted((*manifest.sources,*manifest.additional),key=lambda r:r.ordinal))
    validation.require(len(actual)==len(entries),'consumption_bijection')
    return DraftConsumptionReceipt(draft_id=pin.id,version=pin.version,revision_id=pin.revision_id,
        manifest_hash=pin.manifest_hash,snapshot=pin.snapshot,rows=entries)


def projected(value):
    if value is None:return None
    from bookflow.company.deposit_lifecycle_models import ConsumedDraftState
    a=value['after'];r=value['row']
    return ConsumedDraftState(id=a['id'],version=a['version'],state='consumed',revision_id=r['revision_id'],manifest_hash=r['manifest_hash'],operation_id=r['operation_id'])
