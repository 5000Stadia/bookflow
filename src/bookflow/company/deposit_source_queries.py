"""Private current source candidates and company-keyed relevant-facts pages."""
import base64
import hmac
import json
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_sources, deposit_dependencies, deposit_dependency_history
from bookflow.company import payment_queries as q
from bookflow.company.deposit_draft_models import Candidate, SourcePage, SourceFilter
from bookflow.company.deposit_draft_validation import admit
from bookflow.company.ledger_reports import _cursor_key
from bookflow.core.errors import BookflowError


def page(s,binding,domain,contract,values,limit,cursor,*,facts=None):
    identity=deposit_dependency_history.execution_binding(s,binding)
    fingerprint=q.digest([s.company_row['id'],domain,contract,identity,facts if facts is not None else values])
    key=_cursor_key(s.company); transcript=('bookflow.deposit.'+domain+'.v1\0').encode()
    offset=0
    if cursor:
        try:
            payload,signature=cursor.split('.')
            raw=base64.urlsafe_b64decode(payload+'='*(-len(payload)%4))
            mac=base64.urlsafe_b64decode(signature+'='*(-len(signature)%4))
            if not hmac.compare_digest(mac,hmac.digest(key,transcript+raw,'sha256')):raise ValueError()
            saved=json.loads(raw)
            if set(saved)!={'v','fp','offset'} or type(saved['v']) is not int or saved['v']!=1 or type(saved['offset']) is not int or saved['offset']<0:raise ValueError()
            if saved['fp']!=fingerprint:raise BookflowError('E_QUERY_STALE')
            if saved['offset']>len(values):raise ValueError()
            offset=saved['offset']
        except (ValueError,KeyError,TypeError):raise BookflowError('E_VALIDATION',details={'field':'cursor'}) from None
    after=offset+limit;next_cursor=None
    if after<len(values):
        raw=q.canonical(dict(v=1,fp=fingerprint,offset=after)).encode()
        encode=lambda b:base64.urlsafe_b64encode(b).decode().rstrip('=')
        next_cursor=encode(raw)+'.'+encode(hmac.digest(key,transcript+raw,'sha256'))
    return values[offset:after],next_cursor,fingerprint


def candidates(s,inp:SourceFilter,*,binding=None,ctx=None):
    binding=admit(s,ctx,binding)
    if inp.for_deposit:
        row=s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.id==inp.for_deposit,c.transactions.c.type=='deposit')).mappings().one_or_none()
        if row is None:raise BookflowError('E_RECORD_NOT_FOUND')
        deposit_dependency_history._authorize_binding_graph(s,binding,deposit_dependencies.historical_sources(s,inp.for_deposit))
    values=[];facts=[]
    # Coarse producer classification is not a completeness predicate. The owning
    # adapter proves UF partition; every candidate is authorized before its decode.
    headers=s.company.conn.execute(sa.select(c.transactions).where(c.transactions.c.type.in_(('payment','sales_receipt'))).order_by(c.transactions.c.id)).mappings()
    for header in headers:
        try:admit(s,ctx,binding,sources=(header['id'],))
        except BookflowError as error:
            if error.code=='E_PERMISSION':continue
            raise
        try:source=deposit_sources.load(s,header['id'])
        except BookflowError as error:
            if error.code=='E_DEPOSIT_SOURCE_INELIGIBLE':continue
            raise
        profile=source.profile
        customer=profile.payer if source.source_type=='payment' else profile.customer
        current=s.company.conn.execute(sa.select(c.customers).where(c.customers.c.id==customer.id)).mappings().one()
        method=profile.payment_method
        actual_method=s.company.conn.execute(sa.select(c.payment_methods).where(c.payment_methods.c.id==method.id)).mappings().one() if method else None
        kind=actual_method['kind'] if actual_method else None
        claim=deposit_dependencies.active_claim(s,header['id'])
        reasons=[]
        if source.receipt_date>inp.date:reasons.append('date')
        if not current['active'] or (actual_method and not actual_method['active']):reasons.append('inactive')
        if claim and claim['transaction_id']!=inp.for_deposit:reasons.append('claimed')
        if inp.payment_method_type is not None and kind!=inp.payment_method_type:continue
        if inp.date_from and source.receipt_date<inp.date_from:continue
        if inp.date_to and source.receipt_date>inp.date_to:continue
        name=current.get('full_name') or current['name']
        captured=customer.label
        if inp.q and inp.q.casefold() not in ' '.join(str(v or '') for v in (header['number'],name,captured,source.source_reference,source.source_memo,method.label if method else '')).casefold():continue
        if reasons and (not inp.include_ineligible or 'claimed' in reasons):continue
        facts.append([dict(header),source.model_dump(mode='json'),dict(current),dict(actual_method) if actual_method else None,dict(claim) if claim else None])
        values.append(Candidate(source=source,number=header['number'],recorded_at=header['created_at'],captured_name=captured,current_name=name,
            payment_method_type=kind,payment_method_label=method.label if method else None,membership_id=claim['membership_id'] if claim else None,
            eligible=not reasons,reason=','.join(reasons) or None))
    def order(v):
        first=v.source.receipt_date if inp.sort=='date' else v.current_name.casefold() if inp.sort=='name' else (v.payment_method_label or '').casefold()
        return first,v.source.transaction_id
    values.sort(key=order,reverse=inp.direction=='desc')
    return binding,values,facts


def query(s,inp,*,binding=None,ctx=None):
    filtered=SourceFilter.model_validate(inp.model_dump(exclude={'limit','cursor'}))
    binding,values,facts=candidates(s,filtered,binding=binding,ctx=ctx)
    selected,cursor,fp=page(s,binding,'sources',filtered.model_dump(mode='json'),values,inp.limit,inp.cursor,facts=facts)
    for value in selected:admit(s,ctx,binding,sources=(value.source.transaction_id,))
    return SourcePage(items=tuple(selected),total_count=len(values),subtotal=sum(v.source.cash_minor_units for v in values),next_cursor=cursor,facts_fingerprint=fp)
