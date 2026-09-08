"""Company-only stored financial derivation between existing admission stages.

No credential, Session, Context, permission result or cross-read cache is retained.
The connection is an internal data interface, not an authorization capability.
"""
from dataclasses import dataclass
from collections import Counter
import json
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.company import schema as c, payment_queries as q, deposit_sources
from bookflow.company import deposit_draft_validation as dv, deposit_read_validation as v
from bookflow.company import deposit_dependency_history as h
from bookflow.company.deposit_draft_models import Manifest
from bookflow.company.deposit_read_models import Selected, Pin
from bookflow.core.errors import BookflowError


@dataclass(frozen=True, slots=True)
class CompanyConnection:
    conn: sa.Connection

    def __post_init__(self):
        if not isinstance(self.conn, sa.Connection):
            raise TypeError('CompanyConnection requires a SQLAlchemy connection')


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    company: CompanyConnection

    def __post_init__(self):
        if type(self.company) is not CompanyConnection:
            raise TypeError('CompanyFacts requires CompanyConnection')


def checked(read):
    if type(read) is not CompanyFacts or type(read.company) is not CompanyConnection:
        raise TypeError('Financial derivation requires CompanyFacts')


def _row(read, table, identity):
    checked(read)
    value=read.company.conn.execute(sa.select(table).where(table.c.id==identity)).mappings().one_or_none()
    if value is None:raise BookflowError('E_RECORD_NOT_FOUND')
    return dict(value)


@dataclass(frozen=True, slots=True)
class DraftStart:
    header: dict
    revision: dict


@dataclass(frozen=True, slots=True)
class RevisionStart:
    header: dict
    revision: dict
    kind: str
    manifest: Manifest
    proof: object
    stored: list


@dataclass(frozen=True, slots=True)
class RootFinancial:
    selected: dict
    effects: dict
    revisions: tuple


def begin_draft(read: CompanyFacts, identity, revision_number=None, *, kind='draft') -> DraftStart:
    checked(read)
    table=getattr(c,'deposit_'+('drafts' if kind=='draft' else 'selections'))
    header=_row(read,table,identity);rt=getattr(c,'deposit_'+kind+'_revisions')
    revisions=list(read.company.conn.execute(sa.select(rt).where(rt.c[kind+'_id']==identity).order_by(rt.c.version)).mappings())
    prior=None;high_water=-1
    for index,revision in enumerate(revisions,1):
        dv.require(revision['version']==index and revision['previous_revision_id']==prior,'revision_chain')
        dv.require(revision['high_water']>=high_water,'ordinal_high_water')
        high_water=revision['high_water'];prior=revision['id']
    consumed=kind=='draft' and header['state']=='consumed'
    dv.require(bool(revisions) and revisions[-1]['id']==header['current_revision_id'] and revisions[-1]['version']+int(consumed)==header['version'],'current_revision')
    if kind=='draft':
        if not consumed:
            dv.require(read.company.conn.execute(sa.select(c.deposit_draft_consumptions.c.operation_id).where(c.deposit_draft_consumptions.c.draft_id==identity)).first() is None,'unexpected_consumption')
    if consumed:
        require_consumed(read,header,revisions[-1])
    revision=next((r for r in revisions if r['version']==revision_number),None) if revision_number else revisions[-1]
    if revision is None:raise BookflowError('E_RECORD_NOT_FOUND')
    if kind=='draft':
        maximum=read.company.conn.execute(sa.select(sa.func.max(c.deposit_draft_row_keys.c.ordinal)).where(c.deposit_draft_row_keys.c.draft_id==identity)).scalar_one() or 0
        dv.require(high_water>=maximum,'ordinal_high_water')
    return DraftStart(header,dict(revision))


def begin_revision(read: CompanyFacts, header, revision, *, kind) -> RevisionStart:
    checked(read)
    try:manifest=Manifest.model_validate_json(revision['snapshot'])
    except ValidationError:raise BookflowError('E_VALIDATION',details={'reason':'invalid_manifest'}) from None
    dv.validate_manifest(manifest)
    dv.require(q.digest(manifest.model_dump(mode='json'))==revision['manifest_hash'],'manifest_hash')
    dv.require(manifest.high_water==revision['high_water'])
    parent=kind+'_id'
    dv.require(revision[parent]==header['id'])
    from bookflow.company.deposit_draft_history import DraftHistoryProof
    proof=DraftHistoryProof(read,header,revision,kind)
    st=getattr(c,'deposit_'+kind+'_sources')
    stored=list(read.company.conn.execute(sa.select(st).where(st.c.revision_id==revision['id']).order_by(st.c.ordinal)).mappings())
    dv.require(len(stored)==len(manifest.sources),'source_count')
    return RevisionStart(header,revision,kind,manifest,proof,stored)


def finish_revision(read: CompanyFacts, start: RevisionStart, source_graphs) -> Manifest:
    checked(read)
    header,revision,kind,manifest,proof,stored=(start.header,start.revision,start.kind,start.manifest,start.proof,start.stored)
    if proof.s is not read:raise TypeError('Draft history lost its company reader')
    parent=kind+'_id'
    for raw,row in zip(stored,manifest.sources):
        dv.require((raw[parent],raw['row_id'],raw['ordinal'],raw['source_transaction_id'],raw['source_type'],raw['expected_header_version'],raw['source_revision_id'],raw['memo'],raw['memo_origin'])==
            (header['id'],row.row_id,row.ordinal,row.source.transaction_id,row.source.source_type,row.source.expected_header_version,row.source.revision_id,row.memo,row.memo_origin))
        dv.require(json.loads(raw['snapshot'])==row.model_dump(mode='json'),'source_snapshot')
        # Independently reconstruct the captured business revision from real owned
        # rows. A later correction is history, not permission to rewrite the pin.
        g=source_graphs[row.source.transaction_id]
        endpoint=proof.source_endpoint(row)
        if row.source.source_type=='payment':
            keys=c.payment_component_keys
            allowed=set(read.company.conn.execute(sa.select(keys.c.id).join(c.audit_events,c.audit_events.c.id==keys.c.audit_event_id).where(keys.c.transaction_id==row.source.transaction_id,c.audit_events.c.seq<=endpoint.sequence)).scalars())
            g['payment_component_keys']=[key for key in g['payment_component_keys'] if key['id'] in allowed]
        g['header']=endpoint.header
        g['posting_batches']=[b for b in g['posting_batches'] if b['id']==row.source.business_batch_id]
        actual=deposit_sources.project(g,uf_account=row.source.uf_account,home_currency=manifest.currency)
        captured=row.source.model_copy(update={'expected_header_version':endpoint.header['version']})
        dv.require(actual==captured,'source_provenance')
    if kind=='draft':
        dv.require((revision['bank_account_id'],revision['cashback_account_id'])==(manifest.header.bank.id if manifest.header.bank else None,manifest.header.cash_back.account.id if manifest.header.cash_back and manifest.header.cash_back.account else None))
        at=c.deposit_draft_additional
        added=list(read.company.conn.execute(sa.select(at).where(at.c.revision_id==revision['id']).order_by(at.c.ordinal)).mappings())
        dv.require(len(added)==len(manifest.additional))
        for raw,row in zip(added,manifest.additional):
            dv.require(json.loads(raw['snapshot'])==row.model_dump(mode='json'))
            dv.require((raw['draft_id'],raw['row_id'],raw['ordinal'],raw['account_id'],raw['class_id'],raw['payment_method_id'],raw['amount_minor_units'],raw['currency'])==
                (header['id'],row.row_id,row.ordinal,row.account.id if row.account else None,row.class_ref.id if row.class_ref else None,row.payment_method.id if row.payment_method else None,row.units,manifest.currency))
            dv.require(raw['party_kind']==(row.received_from.kind if row.received_from else None))
            for k in ('customer','vendor','employee','other_name'):dv.require(raw[k+'_id']==(row.received_from.id if row.received_from and row.received_from.kind==k else None))
        keys={r['id']:r for r in read.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==header['id'])).mappings()}
        for group,name in ((manifest.sources,'source'),(manifest.additional,'additional')):
            for row in group:
                key=keys.get(row.row_id);dv.require(key is not None and (key['ordinal'],key['kind'])==(row.ordinal,name))
                dv.require(key['edit_transaction_id'] is None or key['edit_transaction_id'] in (header['edit_transaction_id'],header['copy_transaction_id']))
    return manifest


def require_row_origins(read: CompanyFacts, header, keys):
    """Original keys prove pinned composition membership; only edit reuses IDs."""
    checked(read)
    origin = header['edit_transaction_id'] or header['copy_transaction_id']
    revision_id = header['baseline_revision_id'] or header['copy_revision_id']
    members = {}
    if origin is not None:
        from bookflow.company.deposit_models import Effect
        profile = read.company.conn.execute(sa.select(c.deposit_profiles).where(
            c.deposit_profiles.c.transaction_id == origin,
            c.deposit_profiles.c.revision_id == revision_id)).mappings().one_or_none()
        dv.require(profile is not None, 'draft_origin_profile')
        try:
            effect = Effect.model_validate_json(profile['facts_snapshot'])
        except (ValueError,TypeError):
            raise BookflowError('E_VALIDATION',details={'reason':'draft_origin_profile'}) from None
        for kind, group in (('source', effect.intent.sources), ('additional', effect.intent.additional)):
            for row in group:
                dv.require(row.row_id not in members, 'draft_origin_bijection')
                members[row.row_id] = (kind, row.ordinal)
    seen = set()
    for key in keys:
        pair = (key['edit_transaction_id'], key['original_row_id'])
        if pair == (None, None):
            continue
        dv.require(origin is not None and pair[0] == origin, 'draft_origin_owner')
        dv.require(members.get(pair[1]) == (key['kind'], key['ordinal']), 'draft_origin_member')
        dv.require(pair[1] not in seen, 'draft_origin_bijection')
        seen.add(pair[1])
        raw = read.company.conn.execute(sa.select(c.deposit_row_keys).where(
            c.deposit_row_keys.c.id == pair[1], c.deposit_row_keys.c.transaction_id == origin)).mappings().one_or_none()
        dv.require(raw is not None and (raw['kind'], raw['ordinal']) == members[pair[1]], 'draft_origin_key')


def _validate_consumed(read: CompanyFacts,header,revision):
    """Validate terminal lifecycle independently of the pinned revision version."""
    checked(read)
    from bookflow.company.deposit_draft_provider import DraftPin
    found=list(read.company.conn.execute(sa.select(c.deposit_draft_consumptions).where(c.deposit_draft_consumptions.c.draft_id==header['id'])).mappings())
    dv.require(len(found)==1,'consumption')
    row=dict(found[0])
    dv.require((row['revision_id'],row['operation_id'],row['manifest_hash'])==
        (revision['id'],header['consumed_operation_id'],revision['manifest_hash']),'consumption_pin')
    dv.require(header['consumed_revision_id']==header['current_revision_id']==revision['id'] and header['version']==revision['version']+1,'consumption_version')
    operation=_row(read,c.deposit_operations,row['operation_id'])
    dv.require(operation['audit_event_id']==header['audit_event_id']==row['audit_event_id'],'consumption_event')
    if header['edit_transaction_id'] is not None:dv.require(operation['transaction_id']==header['edit_transaction_id'],'consumption_target')
    dv.require(header['state']=='consumed' and revision['draft_id']==header['id'], 'consumption_owner')
    dv.require((row['created_at'],row['created_by'],row['created_via'])==
        (header['updated_at'],header['updated_by'],header['updated_via']), 'consumption_writer')
    try:
        saved=json.loads(operation['request_snapshot'])
        pin=DraftPin.model_validate_json(q.canonical(saved['resolved_draft']))
        output=json.loads(operation['effect_snapshot'])
        effect=output['effect']
        if operation['command']=='deposit coordinate':effect=effect['deposit']
        from bookflow.company.deposit_lifecycle_models import DraftConsumptionReceipt
        receipt=DraftConsumptionReceipt.model_validate_json(q.canonical(effect['consumed_draft']))
    except (ValueError, KeyError, TypeError):
        raise BookflowError('E_VALIDATION',details={'reason':'consumption_receipt'}) from None
    dv.require((pin.id,pin.version,pin.revision_id,pin.revision_number,pin.manifest_hash,pin.snapshot)==
        (header['id'],revision['version'],revision['id'],revision['version'],revision['manifest_hash'],revision['snapshot']), 'consumption_pin_receipt')
    old=json.loads(pin.header_json)
    transitioned=dict(old,state='consumed',version=old['version']+1,consumed_revision_id=revision['id'],
        consumed_operation_id=operation['id'],audit_event_id=operation['audit_event_id'],
        updated_at=operation['created_at'],updated_by=operation['created_by'],updated_via=operation['created_via'])
    dv.require(old['state']=='open' and header==transitioned,'consumption_exact_transition')
    keys=list(read.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(
        c.deposit_draft_row_keys.c.draft_id==header['id']).order_by(c.deposit_draft_row_keys.c.ordinal)).mappings())
    dv.require(q.canonical([dict(k) for k in keys])==pin.keys_json,'consumption_exact_keys')
    dv.require((receipt.draft_id,receipt.version,receipt.revision_id,receipt.manifest_hash,receipt.snapshot)==
        (pin.id,pin.version,pin.revision_id,pin.manifest_hash,pin.snapshot), 'consumption_original_receipt')
    dv.require(effect['after']['id']==operation['transaction_id'] and
        effect['audit_event_id']==row['audit_event_id'], 'consumption_effect_target')
    from bookflow.company.deposit_draft_models import Manifest
    manifest=Manifest.model_validate_json(revision['snapshot'])
    dv.require(q.digest(manifest.model_dump(mode='json'))==revision['manifest_hash'],'consumption_manifest_hash')
    captured={r.ordinal:r.row_id for r in (*manifest.sources,*manifest.additional)}
    financial=effect['financial']['intent']
    actual={r['ordinal']:r['row_id'] for r in (*financial['sources'],*financial['additional'])}
    dv.require(len(actual)==len(captured)==len(receipt.rows), 'consumption_row_count')
    dv.require({r.ordinal:(r.draft_row_id,r.financial_row_id) for r in receipt.rows}==
        {n:(identity,actual.get(n)) for n,identity in captured.items()}, 'consumption_row_map')
    dv.require(len({r.financial_row_id for r in receipt.rows})==len(receipt.rows), 'consumption_row_bijection')


def require_consumed(read: CompanyFacts,header,revision):
    checked(read)
    try:
        _validate_consumed(read,header,revision)
    except (ValueError,TypeError,KeyError,AttributeError):
        raise BookflowError('E_VALIDATION',details={'reason':'consumption_receipt'}) from None



def require_no_consumption(stored_operation):
    dv.require('resolved_draft' not in json.loads(stored_operation['request_snapshot']), 'missing_consumption')


def consumed_state(operation, header, revision):
    from bookflow.company.deposit_lifecycle_models import ConsumedDraftState
    return ConsumedDraftState(id=header['id'],version=header['version'],state=header['state'],revision_id=revision['id'],manifest_hash=revision['manifest_hash'],operation_id=operation)


def derive_root(read: CompanyFacts, header, graph, source_graphs, event_rows, *, company_info_id, reader) -> RootFinancial:
    checked(read)
    if type(reader) is not h.History or reader.s is not read:
        raise TypeError('Financial history requires the same CompanyFacts')
    identity=header['id']
    v.require(reader.take('transaction',identity) is not None)
    v.audit_rows(reader,{k:x for k,x in graph.items() if not k.startswith('_')})
    revs=sorted(graph['transaction_revisions'],key=lambda r:r['revision_number']);previous=None
    chosen={};effects={}
    for n,rev in enumerate(revs,1):
        v.require(rev['revision_number']==n and rev['supersedes_revision_id']==previous);previous=rev['id']
        effect,issuer,custom=v.revision(graph,rev,source_graphs,reader)
        v.require(issuer.id==company_info_id and issuer.home_currency==rev['currency'])
        effects[rev['id']]=effect
        chosen[n]=Selected(pin=Pin(deposit=identity,revision_id=rev['id'],revision_number=n),date=rev['date'],number=rev['number'],memo=rev['memo'],issuer=issuer,custom_fields=custom,deposit_to=effect.intent.bank,cash_back=effect.intent.cash_back)
    v.require(revs and header['current_revision_id']==revs[-1]['id'] and header['number']==revs[-1]['number'])
    v.lifecycle(graph,header)
    _bank(read,graph,effects,header)
    return RootFinancial(chosen,effects,tuple(revs))


def _bank(read: CompanyFacts,g,effects,header):
    checked(read)
    # Only this exact helper is data-only; admission functions remain wrapper-owned.
    from bookflow.company.deposit_read_authority import select
    keys={r['id']:r for r in g['bank_effect_keys']};versions=g['bank_effect_versions']
    current=select(read,c.bank_effect_current,c.bank_effect_current.c.key_id,keys)
    v.require(len(current)==len(keys));bykey={r['key_id']:r['version_id'] for r in current}
    for key,k in keys.items():
        found=sorted([r for r in versions if r['key_id']==key],key=lambda r:r['version'])
        v.require(found and [r['version'] for r in found]==list(range(1,len(found)+1)) and bykey[key]==found[-1]['id'])
        for r in found:
            e=effects[r['revision_id']];role=k['role'];account=None;units=0
            if role=='main_bank':account=e.intent.bank;units=e.bank_total
            elif role=='cash_back':
                if e.intent.cash_back:account=e.intent.cash_back.account;units=e.cash_back
            else:
                row=next((a for a in e.intent.additional if a.row_id==k['row_id']),None)
                if row:account=row.account;units=-row.units
            batch=v.exact_one([b for b in g['posting_batches'] if b['id']==r['batch_id']])
            if r['active']:
                v.require(batch['kind']!='reversal' and account is not None and account.type in ('bank','credit_card') and units!=0)
                v.require((r['account_id'],r['signed_debit'],r['statement_amount'])==(account.id,units,-units if account.type=='credit_card' else units))
            else:v.require(r['signed_debit']==r['statement_amount']==0)
            selected=v.exact_one([x for x in g['transaction_revisions'] if x['id']==r['revision_id']])
            v.require((r['effective_date'],r['currency'],r['number'],r['memo'])==(selected['date'],selected['currency'],selected['number'],selected['memo']))
        if header['status']=='voided':v.require(not found[-1]['active'])
    # Every bank/card role of every stored business revision has its exact active version.
    for rid,e in effects.items():
        expected=[('main_bank',e.intent.bank,e.bank_total)]
        if e.intent.cash_back:expected.append(('cash_back',e.intent.cash_back.account,e.cash_back))
        expected.extend(('additional',a.account,-a.units) for a in e.intent.additional)
        wanted=Counter((role,a.id,n) for role,a,n in expected if a.type in ('bank','credit_card') and n)
        got=Counter((keys[r['key_id']]['role'],r['account_id'],r['signed_debit']) for r in versions if r['revision_id']==rid and r['active'])
        v.require(wanted==got)


def require_operation_items(collections, stored):
    v.require(set(r['kind'] for r in stored)<=set(collections))
    for kind,values in collections.items():
        found=sorted([r for r in stored if r['kind']==kind],key=lambda r:r['ordinal'])
        v.require([r['ordinal'] for r in found]==list(range(len(values))) and [json.loads(r['facts_snapshot']) for r in found]==values)


def require_consumption_match(output, current):
    saved_effect=output.effect.deposit if output.command=='deposit coordinate' else output.effect
    if saved_effect.consumed_draft:
        v.require(current is not None and current.id==saved_effect.consumed_draft.draft_id)
    else:v.require(current is None)
