"""Independent G3 manifest, relational ownership and current admission checks."""
import json
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.company import schema as c, deposit_sources, deposit_dependencies as dependencies
from bookflow.company import deposit_dependency_history as history, payment_queries as q
from bookflow.company.deposit_draft_models import Manifest, Summary
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from bookflow.storage.migrate import FeatureRevision, feature_admission

FEATURE = FeatureRevision('company','co0024')

def require(ok, reason='invalid_manifest'):
    if not ok: raise BookflowError('E_VALIDATION',details={'reason':reason})

def admit(s, ctx=None, binding=None, *, sources=(), draft=None, selection=None, write=False):
    if feature_admission(s.company,FEATURE,resolver=lambda _:True) is None:raise BookflowError('E_SCHEMA_BEHIND')
    binding=binding or OSBinding.from_session(s,ctx.on_behalf_of if ctx else None)
    actor,_,principal,_=history.execution_binding(s,binding)
    # Legacy require_resource still has a hub-admin escape. This private boundary
    # requires an actual current membership role for both authenticated identities;
    # it does not change the shared owner or supply granular grants.
    from dataclasses import replace
    from bookflow.core.session import Actor
    from bookflow.hub import schema as hub, access
    for identity in (actor,) if principal is None else (actor,principal):
        user=s.hub.conn.execute(sa.select(hub.users).where(hub.users.c.id==identity,hub.users.c.active.is_(True))).mappings().one_or_none()
        if user is None:raise BookflowError('E_UNAUTHENTICATED')
        view=replace(s,actor=Actor(**{k:user[k] for k in ('id','kind','username','display_name','hub_admin','timezone')}),memberships=[])
        access.load_memberships(view)
        applicable={('company',s.company_row['id']),('organization',s.company_row['organization_id'])}
        view.memberships=[row for row in view.memberships if (row['scope_type'],row['scope_id']) in applicable]
        scope,role=access.company_role(view,s.company_row['id'],s.company_row['organization_id'])
        if role is None:raise BookflowError('E_COMPANY_NOT_FOUND')
        if not access.role_satisfies(role,scope,'standard' if write else 'member',False):raise BookflowError('E_PERMISSION')
    if ctx and ctx.on_behalf_of!=binding.on_behalf_of:raise BookflowError('E_UNAUTHENTICATED')
    history._authorize_binding_graph(s,binding,(),write=write)
    targets=set(sources);drafts=set([draft] if draft else []);selections=set([selection] if selection else [])
    visited_drafts=set();visited_selections=set()
    # Full retained graph, including accepted/abandoned children and operation-only targets.
    while drafts-visited_drafts or selections-visited_selections:
        for identity in sorted(drafts-visited_drafts):
            visited_drafts.add(identity)
            header=s.company.conn.execute(sa.select(c.deposit_drafts).where(c.deposit_drafts.c.id==identity)).mappings().one_or_none()
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            if header['edit_transaction_id']:targets.add(header['edit_transaction_id'])
            if header['copy_transaction_id']:targets.add(header['copy_transaction_id'])
            targets.update(s.company.conn.execute(sa.select(c.deposit_draft_sources.c.source_transaction_id).where(c.deposit_draft_sources.c.draft_id==identity)).scalars())
            selections.update(s.company.conn.execute(sa.select(c.deposit_selections.c.id).where(c.deposit_selections.c.target_draft_id==identity)).scalars())
            if header['consumed_operation_id']:
                targets.update(s.company.conn.execute(sa.select(c.deposit_operation_targets.c.transaction_id).where(c.deposit_operation_targets.c.operation_id==header['consumed_operation_id'])).scalars())
        for identity in sorted(selections-visited_selections):
            visited_selections.add(identity)
            header=s.company.conn.execute(sa.select(c.deposit_selections).where(c.deposit_selections.c.id==identity)).mappings().one_or_none()
            if header is None:raise BookflowError('E_RECORD_NOT_FOUND')
            drafts.add(header['target_draft_id'])
            targets.update(s.company.conn.execute(sa.select(c.deposit_selection_sources.c.source_transaction_id).where(c.deposit_selection_sources.c.selection_id==identity)).scalars())
    # Walk complete deposit membership and permanent-operation participants.
    # A removed receipt still connects its historical deposit and sibling sources.
    visited=set()
    while targets-visited:
        frontier=sorted(targets-visited);visited.update(frontier)
        for offset in range(0,len(frontier),200):
            group=frontier[offset:offset+200];members=c.deposit_memberships
            for source,deposit in s.company.conn.execute(sa.select(members.c.source_transaction_id,members.c.transaction_id).where(
                    sa.or_(members.c.source_transaction_id.in_(group),members.c.transaction_id.in_(group)))):
                targets.update((source,deposit))
            op=c.deposit_operations;ot=c.deposit_operation_targets
            targets.update(s.company.conn.execute(sa.select(ot.c.transaction_id).join(op,op.c.id==ot.c.operation_id).where(op.c.transaction_id.in_(group))).scalars())
    history._authorize_binding_graph(s,binding,sorted(targets),write=write)
    return binding


def summary(currency, header, sources, additional):
    problems=[]
    if header.bank is None:problems.append('deposit_to:required')
    elif header.bank.type!='bank' or header.bank.system_role=='undeposited_funds' or header.bank.currency!=currency or not header.bank.active:problems.append('deposit_to:ineligible')
    if header.date is None:problems.append('date:required')
    for row in additional:
        for key,value in (('received_from',row.received_from),('from_account',row.account),('amount',row.units)):
            if value is None:problems.append(f'{row.row_id}:{key}:required')
        if row.units==0:problems.append(f'{row.row_id}:amount:zero')
        if row.account and (not row.account.active or row.account.currency!=currency or row.account.type in ('accounts_receivable','accounts_payable','inventory') or row.account.system_role is not None or (header.bank and row.account.id==header.bank.id)):
            problems.append(f'{row.row_id}:from_account:ineligible')
    for identity,value in header.custom_fields.items():
        if value.required and value.canonical_text is None:problems.append(f'custom_fields.{identity}:required')
    total=sum(r.source.cash_minor_units for r in sources);extra=sum(r.units for r in additional if r.units is not None)
    resolved=all(r.units is not None for r in additional)
    subtotal=total+extra if resolved else None
    posting=total+sum(max(0,r.units) for r in additional) if resolved else None
    cash=header.cash_back
    if cash:
        if cash.account is None:problems.append('cash_back.account:required')
        if cash.units is None:problems.append('cash_back.amount:required')
        elif cash.units<=0:problems.append('cash_back.amount:positive')
        if cash.account and (cash.account.type in ('income','other_income','cost_of_goods_sold','accounts_receivable','accounts_payable','inventory') or cash.account.system_role is not None or not cash.account.active or cash.account.currency!=currency or (header.bank and cash.account.id==header.bank.id)):
            problems.append('cash_back.account:ineligible')
    bank=None if subtotal is None or (cash and cash.units is None) else subtotal-(cash.units if cash else 0)
    if subtotal is not None and subtotal<=0:problems.append('subtotal:positive')
    if bank is not None and bank<0:problems.append('cash_back:exceeds_subtotal')
    for name,value in (('source_total',total),('known_additional_total',extra),('subtotal',subtotal),('bank_total',bank),('posting_total',posting)):
        if value is not None and abs(value)>9223372036854775807:raise BookflowError('E_VALUE_RANGE',details={'field':name})
    return Summary(source_count=len(sources),additional_count=len(additional),source_total=total,known_additional_total=extra,
        subtotal=subtotal,bank_total=bank,posting_total=posting,issues=tuple(problems))


def validate_manifest(manifest):
    require(type(manifest) is Manifest)
    try:Manifest.model_validate_json(manifest.model_dump_json())
    except ValidationError:require(False)
    ids=[r.row_id for r in (*manifest.sources,*manifest.additional)]
    order=[r.ordinal for r in (*manifest.sources,*manifest.additional)]
    require(len(ids)==len(set(ids)) and len(order)==len(set(order)))
    require(all(v<=manifest.high_water for v in order))
    require(list(manifest.sources)==sorted(manifest.sources,key=lambda r:r.ordinal))
    require(list(manifest.additional)==sorted(manifest.additional,key=lambda r:r.ordinal))
    require(len({r.source.transaction_id for r in manifest.sources})==len(manifest.sources))
    for row in manifest.sources:
        source=row.source
        require(source.currency==manifest.currency and sum(x.capacity for x in source.components)==source.cash_minor_units)
        require(len(set(source.semantic_presence))==len(source.semantic_presence))
        require(len({x.key for x in source.components})==len(source.components))
        require(all(x.key in source.semantic_presence for x in source.components))
        require(row.memo_origin!='source' or row.memo==source.source_memo)
        require(len({o.key for o in row.occurrences})==len(row.occurrences) and len({o.ordinal for o in row.occurrences})==len(row.occurrences))
        require({o.key for o in row.occurrences if o.present}==set(source.semantic_presence))
    from bookflow.company import custom_fields as cf
    for key,v in manifest.header.custom_fields.items():
        require(key==v.definition_id)
        if v.canonical_text is not None:
            try:cf.typed_value_from_canonical(v.kind,v.canonical_text)
            except BookflowError:require(False)
        require((v.choice_id is not None)==(v.choice_label is not None))
        require(v.kind=='choice' or v.choice_id is None)
    require(manifest.summary==summary(manifest.currency,manifest.header,manifest.sources,manifest.additional),'summary_mismatch')
    return manifest


def decode_revision(s, header, revision, kind):
    try:manifest=Manifest.model_validate_json(revision['snapshot'])
    except ValidationError:raise BookflowError('E_VALIDATION',details={'reason':'invalid_manifest'}) from None
    validate_manifest(manifest)
    require(q.digest(manifest.model_dump(mode='json'))==revision['manifest_hash'],'manifest_hash')
    require(manifest.high_water==revision['high_water'])
    parent=kind+'_id'
    require(revision[parent]==header['id'])
    st=getattr(c,'deposit_'+kind+'_sources')
    stored=list(s.company.conn.execute(sa.select(st).where(st.c.revision_id==revision['id']).order_by(st.c.ordinal)).mappings())
    require(len(stored)==len(manifest.sources),'source_count')
    for raw,row in zip(stored,manifest.sources):
        require((raw[parent],raw['row_id'],raw['ordinal'],raw['source_transaction_id'],raw['source_type'],raw['expected_header_version'],raw['source_revision_id'],raw['memo'],raw['memo_origin'])==
            (header['id'],row.row_id,row.ordinal,row.source.transaction_id,row.source.source_type,row.source.expected_header_version,row.source.revision_id,row.memo,row.memo_origin))
        require(json.loads(raw['snapshot'])==row.model_dump(mode='json'),'source_snapshot')
        # Independently reconstruct the captured business revision from real owned
        # rows. A later correction is history, not permission to rewrite the pin.
        g=deposit_sources.graph(s,row.source.transaction_id)
        if row.source.source_type=='payment':
            version=row.captured_header_version or row.source.expected_header_version
            endpoint=s.company.conn.execute(sa.select(c.audit_events.c.seq).join(c.audit_entries,c.audit_entries.c.event_id==c.audit_events.c.id).where(
                c.audit_entries.c.record_type=='transaction',c.audit_entries.c.record_id==row.source.transaction_id,c.audit_entries.c.version_after==version)).scalars().all()
            require(len(endpoint)==1,'source_version_history')
            keys=c.payment_component_keys
            allowed=set(s.company.conn.execute(sa.select(keys.c.id).join(c.audit_events,c.audit_events.c.id==keys.c.audit_event_id).where(keys.c.transaction_id==row.source.transaction_id,c.audit_events.c.seq<=endpoint[0])).scalars())
            g['payment_component_keys']=[key for key in g['payment_component_keys'] if key['id'] in allowed]
        g['header']=dict(g['header'],current_revision_id=row.source.revision_id,version=row.source.expected_header_version,status='posted')
        g['posting_batches']=[b for b in g['posting_batches'] if b['id']==row.source.business_batch_id]
        actual=deposit_sources.project(g,uf_account=row.source.uf_account,home_currency=manifest.currency)
        require(actual==row.source,'source_provenance')
    if kind=='draft':
        require((revision['bank_account_id'],revision['cashback_account_id'])==(manifest.header.bank.id if manifest.header.bank else None,manifest.header.cash_back.account.id if manifest.header.cash_back and manifest.header.cash_back.account else None))
        at=c.deposit_draft_additional
        added=list(s.company.conn.execute(sa.select(at).where(at.c.revision_id==revision['id']).order_by(at.c.ordinal)).mappings())
        require(len(added)==len(manifest.additional))
        for raw,row in zip(added,manifest.additional):
            require(json.loads(raw['snapshot'])==row.model_dump(mode='json'))
            require((raw['draft_id'],raw['row_id'],raw['ordinal'],raw['account_id'],raw['class_id'],raw['payment_method_id'],raw['amount_minor_units'],raw['currency'])==
                (header['id'],row.row_id,row.ordinal,row.account.id if row.account else None,row.class_ref.id if row.class_ref else None,row.payment_method.id if row.payment_method else None,row.units,manifest.currency))
            require(raw['party_kind']==(row.received_from.kind if row.received_from else None))
            for k in ('customer','vendor','employee','other_name'):require(raw[k+'_id']==(row.received_from.id if row.received_from and row.received_from.kind==k else None))
        keys={r['id']:r for r in s.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==header['id'])).mappings()}
        for group,name in ((manifest.sources,'source'),(manifest.additional,'additional')):
            for row in group:
                key=keys.get(row.row_id);require(key is not None and (key['ordinal'],key['kind'])==(row.ordinal,name))
                require(key['edit_transaction_id'] is None or key['edit_transaction_id'] in (header['edit_transaction_id'],header['copy_transaction_id']))
    return manifest


def stale_sources(s,manifest,edit=None):
    stale=[]
    for row in manifest.sources:
        try:
            actual=deposit_sources.load(s,row.source.transaction_id)
            claim=dependencies.active_claim(s,row.source.transaction_id)
            invalid=actual!=row.source or (claim is not None and claim['transaction_id']!=edit) or (manifest.header.date is not None and actual.receipt_date>manifest.header.date)
        except BookflowError as error:
            if error.code!='E_DEPOSIT_SOURCE_INELIGIBLE':raise
            invalid=True
        if invalid:stale.append(row.source.transaction_id)
    return tuple(stale)
