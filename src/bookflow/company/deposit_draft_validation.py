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
        if not access.role_satisfies(role,scope,'standard' if write else 'member',False):raise dependencies.ProvenDepositDenial()
    if ctx and ctx.on_behalf_of!=binding.on_behalf_of:raise BookflowError('E_UNAUTHENTICATED')
    history._authorize_binding_graph(s,binding,(),write=write)
    from bookflow.company.deposit_draft_evidence import collect
    graph=collect(s.company,sources=sources,draft=draft,selection=selection)
    history._authorize_binding_graph(s,binding,graph.transactions,write=write)
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
        # Never-positive zero keys need no ordinal yet. Extra present zero keys
        # remain legal, including equality-era snapshots and retained ordinals.
        # CashComponent.capacity is Positive (gt=0); zero funding has no component.
        positive={x.key for x in source.components}
        present={o.key for o in row.occurrences if o.present}
        require(positive<=present<=set(source.semantic_presence))
    from bookflow.company import custom_fields as cf
    for key,v in manifest.header.custom_fields.items():
        require(key==v.definition_id)
        require(v.expected_kind is None or (v.canonical_text is not None and v.expected_kind==v.kind),'custom_kind_expectation')
        if v.canonical_text is not None:
            try:cf.typed_value_from_canonical(v.kind,v.canonical_text)
            except BookflowError:require(False)
        require((v.choice_id is not None)==(v.choice_label is not None))
        require(v.kind=='choice' or v.choice_id is None)
    require(manifest.summary==summary(manifest.currency,manifest.header,manifest.sources,manifest.additional),'summary_mismatch')
    return manifest


def require_occurrence_retention(previous, current, *, strict_ordinals):
    """Same-row continuity: reads require presence; new writes also keep ordinals.

    Old accepted selection histories can renumber retained keys, so stored reads
    deliberately do not prove ordinal continuity or normalize those snapshots.
    Only the immediate predecessor is compared; first/cloned revisions have no
    prior draft row. Removed/re-added rows with new identities do not inherit.
    """
    prior={row.row_id:row for row in previous.sources}
    for row in current.sources:
        old=prior.get(row.row_id)
        if old is None:continue
        present={o.key:o.ordinal for o in row.occurrences if o.present}
        semantic=set(row.source.semantic_presence)
        for occurrence in old.occurrences:
            if occurrence.present and occurrence.key in semantic:
                require(occurrence.key in present,'occurrence_retention')
                if strict_ordinals:
                    require(present[occurrence.key]==occurrence.ordinal,'occurrence_retention')


def decode_revision(s, header, revision, kind):
    from bookflow.company import deposit_financial_derivation as d
    read=d.CompanyFacts(d.CompanyConnection(s.company.conn))
    start=d.begin_revision(read,header,revision,kind=kind)
    source_graphs=deposit_sources.graph_many(s,[row.source.transaction_id for row in start.manifest.sources])
    return d.finish_revision(read,start,source_graphs)


def stale_sources(s,manifest,edit=None):
    stale=[]
    claims=dependencies.active_claims(s,[row.source.transaction_id for row in manifest.sources])
    for row in manifest.sources:
        try:
            actual=deposit_sources.load(s,row.source.transaction_id)
            claim=claims.get(row.source.transaction_id)
            invalid=actual!=row.source or (claim is not None and claim['transaction_id']!=edit) or (manifest.header.date is not None and actual.receipt_date>manifest.header.date)
        except BookflowError as error:
            if error.code!='E_DEPOSIT_SOURCE_INELIGIBLE':raise
            invalid=True
        if invalid:stale.append(row.source.transaction_id)
    return tuple(stale)
