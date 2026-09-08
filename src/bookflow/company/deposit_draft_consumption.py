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
    from bookflow.company import deposit_financial_derivation as d
    read=d.CompanyFacts(d.CompanyConnection(s.company.conn))
    return d._validate_consumed(read,header,revision)


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
        from bookflow.company.deposit_financial_derivation import require_no_consumption
        require_no_consumption(stored)
        return None
    h,r,m,_=drafts.load(s,rows[0]['draft_id'],ctx=ctx,binding=binding,write=write)
    from bookflow.company.deposit_financial_derivation import consumed_state
    return consumed_state(operation,h,r)


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
