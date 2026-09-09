"""Private complete deposit show/register/items/history; no public routes."""
from datetime import datetime,timezone
import json
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_read_facts as facts, deposit_read_pages as pages, deposit_read_authority as authority
from bookflow.company import deposit_dependency_history as history_owner
from bookflow.company.deposit_dependency_models import InspectionRoot
from bookflow.company import deposit_read_models as m
from bookflow.company.deposit_lifecycle_models import DocumentState
from bookflow.core.errors import BookflowError


def now():return datetime.now(timezone.utc).isoformat()
def money(n,currency):return m.SignedMoney(minor_units=n,currency=currency)
def checked(value,model):
    if type(value) is not model:raise BookflowError('E_VALIDATION')
    return model.model_validate_json(value.model_dump_json(exclude_unset=True))


def totals(effect):
    i=effect.intent;c=i.currency
    return m.Totals(posting_total=money(effect.posting_total,c),source_total=money(sum(x.source.cash_minor_units for x in i.sources),c),
        positive_additional_total=money(sum(max(0,x.units) for x in i.additional),c),negative_additional_total=money(sum(min(0,x.units) for x in i.additional),c),
        subtotal=money(effect.subtotal,c),cash_back=money(effect.cash_back,c),bank_total=money(effect.bank_total,c))


def counts(e):return m.Counts(sources=len(e.intent.sources),additional=len(e.intent.additional),cash_allocations=len(e.cells),components=sum(len(r.occurrences) for r in e.intent.sources)+len(e.intent.additional))


def selected(data,number=None):
    n=max(data.selected) if number is None else number
    if n not in data.selected:raise BookflowError('E_RECORD_NOT_FOUND')
    return data.selected[n]


def current(data):
    h=data.header;e=data.effects[h['current_revision_id']]
    return DocumentState(id=h['id'],version=h['version'],revision_id=h['current_revision_id'],number=h['number'],status=h['status'],
        revision_date=e.intent.date,currency=e.intent.currency,revision_posting_total=e.posting_total,revision_subtotal=e.subtotal,revision_bank_total=e.bank_total,
        revision_cash_back=e.cash_back,effective_bank_total=e.bank_total if h['status']=='posted' else 0,
        active_source_ids=tuple(sorted(r['source_transaction_id'] for r in data.graph['deposit_current_memberships'])))


def collections(data,pin):
    effect=data.effects[pin.revision_id];g=data.graph
    members=[x for x in g['deposit_memberships'] if x['revision_id']==pin.revision_id and x['kind']=='claim']
    sources=[];refs={(r.kind,r.id):r for r in data.references}
    for row in sorted(effect.intent.sources,key=lambda x:(x.ordinal,x.row_id)):
        graph,header,claim=data.sources[row.source.transaction_id]
        rev=next(r for r in graph['transaction_revisions'] if r['id']==row.source.revision_id)
        lines=[r for r in graph['posting_lines'] if r['id'] in {v.posting_line_id for v in row.source.components}]
        accounts=[m.CapturedAccount.model_validate_json(r['account_snapshot']) for r in lines]
        facts.v.require(accounts and all(a==accounts[0] for a in accounts))
        method=row.source.profile.payment_method
        navigation=refs.get(('payment_methods',method.id)) if method else None
        sources.append(m.SourceItem(captured=row,source_number=rev['number'],from_account=accounts[0],membership_ids=tuple(sorted(x['id'] for x in members if x['row_id']==row.row_id)),
            current=m.SourceCurrent(id=header['id'],status=header['status'],version=header['version'],revision_id=header['current_revision_id'],claim_id=claim['membership_id'] if claim else None,claimed_by=claim['transaction_id'] if claim else None,payment_method_type=navigation.current_type if navigation else None)))
    components={r['id']:r for r in g['deposit_components'] if r['revision_id']==pin.revision_id}
    physical={}
    for r in g['deposit_cash_cells']:
        if r['revision_id']!=pin.revision_id:continue
        component=components[r['component_id']];bucket='additional:'+r['bucket_row_id'] if r['bucket']=='additional' else r['bucket']
        physical[component['row_id'],component['component_ordinal'],bucket]=r
    order={r.row_id:r.ordinal for r in (*effect.intent.sources,*effect.intent.additional)}
    cells=[]
    for cell in sorted(effect.cells,key=lambda x:(order[x.row_id],x.row_id,x.component_ordinal,x.bucket)):
        r=physical[cell.row_id,cell.component_ordinal,cell.bucket]
        cells.append(m.CellItem(id=r['id'],captured=cell,component_id=r['component_id'],bucket_row_id=r['bucket_row_id']))
    return dict(sources=tuple(sources),additional=tuple(sorted(effect.intent.additional,key=lambda x:(x.ordinal,x.row_id))),cash_allocations=tuple(cells))


def immutable(values):
    return [v.model_dump(mode='json',exclude={'current'}) if isinstance(v,m.SourceItem) else v.model_dump(mode='json') for v in values]


def _show(s,data,inp,binding,*,with_guard=True,at=None):
    sel=selected(data,inp.revision_number);effect=data.effects[sel.pin.revision_id];at=at or now();dated=None
    groups=collections(data,sel.pin)
    fps={kind:pages.fingerprint(s,binding,'items',[sel.pin.model_dump(),kind,immutable(values)]) for kind,values in groups.items()}
    if inp.as_of:
        batches={r['id']:r for r in data.graph['posting_batches'] if r['effective_date']<=inp.as_of}
        net=sum(r['debit_minor_units']-r['credit_minor_units'] for r in data.graph['posting_lines'] if r['batch_id'] in batches and r['account_id']==data.effects[batches[r['batch_id']]['revision_id']].intent.bank.id)
        members=sum((1 if r['kind']=='claim' else -1)*r['amount_minor_units'] for r in data.graph['deposit_memberships'] if next(b for b in data.graph['posting_batches'] if b['id']==r['batch_id'])['effective_date']<=inp.as_of)
        reversed_ids={b['reverses_batch_id'] for b in batches.values() if b['kind']=='reversal'}
        active=[b for b in batches.values() if b['kind']!='reversal' and b['id'] not in reversed_ids]
        state='effective' if active else 'canceled' if batches else 'not_effective'
        dated=m.DatedState(as_of=inp.as_of,knowledge_observed_at=at,cutoff_after_evaluation_date=inp.as_of>at[:10],financial_state=state,bank_movement=money(net,effect.intent.currency),source_membership_total=money(members,effect.intent.currency))
    guard=None;status='unknown_history'
    if with_guard:
        recipe,readset=history_owner.capture(s,InspectionRoot(kind='deposit',id=data.header['id']),binding)
        if not readset.unknown:guard=history_owner.issue(s,recipe,readset,binding);status='complete'
    return m.DepositShow(company_id=s.company_row['id'],currency=effect.intent.currency,selected=sel,selected_is_current=sel.pin.revision_id==data.header['current_revision_id'],current=current(data),current_observed_at=at,
        totals=totals(effect),counts=counts(effect),fingerprints=fps,dated_state=dated,links=data.links,current_references=data.references,
        dependencies=m.DependencySummary(source_ids=tuple(sorted({r['source_transaction_id'] for r in data.graph['deposit_memberships']})),guard=guard,history=status))


def show(s,inp,*,binding):
    inp=checked(inp,m.ShowInput);authority.selected(s,inp.deposit,binding=binding)
    data=facts.load_complete(s,[inp.deposit],binding=binding)[0]
    return _show(s,data,inp,binding)


def items(s,inp,*,binding):
    inp=checked(inp,m.ItemsInput);authority.selected(s,inp.deposit,binding=binding)
    number=inp.revision_number
    if inp.page.cursor:
        token=pages.decode(s,binding,'items',inp.page.cursor);position=token['position']
        if type(position) is not list or len(position)!=2 or type(position[0]) is not int or (number is not None and number!=position[0]):raise BookflowError('E_VALIDATION',details={'field':'cursor'})
        number=position[0]
    data=facts.load_complete(s,[inp.deposit],binding=binding)[0];sel=selected(data,number);values=collections(data,sel.pin)[inp.kind]
    content=[sel.pin.model_dump(),inp.kind,immutable(values)]
    rows,fp,next_,_=pages.page(s,binding,'items',values,content,inp.page.limit,inp.page.cursor,pin=sel.pin.revision_number)
    return m.DepositItemPage(selected=sel.pin,kind=inp.kind,items=rows,total_count=len(values),totals=totals(data.effects[sel.pin.revision_id]),fingerprint=fp,next_cursor=next_,current=current(data),current_observed_at=now(),current_references=data.references)


def query(s,inp,*,binding):
    inp=checked(inp,m.QueryInput);authority.authenticate(s,binding)
    if inp.status=='deleted' or (inp.status is None and inp.include_deleted):raise BookflowError('E_VALIDATION',details={'reason':'feature_unavailable','feature':'transaction_deleted'})
    bank=None
    if inp.deposit_to:
        from bookflow.company.accounts import resolve_account
        bank=resolve_account(s.company,inp.deposit_to)['id']
    ids=list(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.type=='deposit').order_by(c.transactions.c.id)).scalars())
    allowed=[]
    for identity in ids:
        try:authority.admit(s,[identity],binding=binding)
        except BookflowError as e:
            if e.code in ('E_PERMISSION','E_RECORD_NOT_FOUND','E_COMPANY_NOT_FOUND'):continue
            raise
        allowed.append(identity)
    values=[]
    for data in facts.load_complete(s,allowed,binding=binding):
        sel=selected(data);effect=data.effects[sel.pin.revision_id]
        text=' '.join([sel.number,sel.memo or '']+[(r.source.profile.payer if r.source.source_type=='payment' else r.source.profile.customer).label for r in effect.intent.sources]+[r.dimensions.party_name or '' for r in effect.intent.additional]).casefold()
        if bank and sel.deposit_to.id!=bank:continue
        if inp.status and data.header['status']!=inp.status:continue
        if inp.date_from and sel.date<inp.date_from:continue
        if inp.date_to and sel.date>inp.date_to:continue
        if inp.number is not None and sel.number!=inp.number:continue
        if inp.q and inp.q.casefold() not in text:continue
        values.append(m.DepositRow(selected=sel,current=current(data),totals=totals(effect),counts=counts(effect)))
    values.sort(key=lambda r:({'date':r.selected.date,'number':r.selected.number,'bank_total':r.totals.bank_total.minor_units}[inp.sort],r.current.id),reverse=inp.direction=='desc')
    currency=s.company_info_row['home_currency'];sums={k:money(sum(getattr(r.totals,k).minor_units for r in values),currency) for k in m.Totals.model_fields}
    contract=inp.model_dump(mode='json',exclude={'page'},exclude_unset=True)
    rows,fp,next_,previous=pages.page(s,binding,'query',values,[contract,[r.model_dump(mode='json') for r in values]],inp.page.limit,inp.page.cursor)
    return m.DepositPage(items=rows,total_count=len(values),totals=m.Totals(**sums),effective_bank_total=money(sum(r.current.effective_bank_total for r in values),currency),fingerprint=fp,next_cursor=next_,previous_cursor=previous)


def history(s,inp,*,binding):
    inp=checked(inp,m.HistoryInput);authority.selected(s,inp.deposit,binding=binding)
    data=facts.load_complete(s,[inp.deposit],binding=binding)[0];values=data.history
    rows,fp,next_,_=pages.page(s,binding,'history',values,[inp.deposit,[x.model_dump(mode='json') for x in values]],inp.page.limit,inp.page.cursor)
    return m.DepositHistoryPage(deposit=inp.deposit,items=rows,total_count=len(values),fingerprint=fp,next_cursor=next_)
