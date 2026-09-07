"""Complete private candidate computation and deterministic bounded slicing.

Offsets/fingerprints here are internal values. Public signed cursor, current
binding and release admission must wrap this core at activation.
"""
import json
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_preparation import (
    account_population,groups,group_fingerprint,statement_amount,claimed,require,bounded,fingerprint,
)
from bookflow.company.reconciliation_storage_validation import digest

def matches(row,f):
    return ((f.from_date is None or row.date>=f.from_date) and (f.to_date is None or row.date<=f.to_date)
        and (f.side is None or (row.amount>0)==(f.side=='positive'))
        and (f.producer is None or row.movement.producer==f.producer)
        and (f.number is None or f.number.casefold() in row.number.casefold())
        and (f.payee is None or any(f.payee.casefold() in p.casefold() for p in row.payees))
        and (f.memo is None or f.memo.casefold() in (row.memo or '').casefold())
        and (f.amount is None or row.amount==f.amount) and (not f.hide_after_date or row.eligible))

def candidates(s,draft,filters, *, limit=50,offset=0,expected_fingerprint=None):
    require(type(limit) is int and 1<=limit<=200 and type(offset) is int and offset>=0,'E_VALIDATION')
    cutoff=draft.header.statement_date or draft.header.opening_date
    values,_=account_population(s,draft.account_id,cutoff)
    values={v['id']:v for v in values}
    values.update((v.version_id,s.versions[v.version_id]) for v in draft.selections)
    selected={v.version_id for v in draft.selections};claims=claimed(s);result=[]
    for group in groups(values.values()).values():
        v=group[0];display=json.loads(v['display_snapshot']);amount=bounded(sum(statement_amount(v) for v in group))
        row=m.Movement(movement=m.MovementKey.model_validate_json(v['movement_snapshot']),group_fingerprint=group_fingerprint(group),component_count=len(group),amount=amount,amount_decimal=str(amount),
            date=v['effective_date'],number=display['number'],payees=tuple(display['payees']),memo=display['memo'],eligible=v['effective_date']<=cutoff,
            stale=any(s.current.get(v['key_id'],{}).get('id')!=v['id'] or not v['active'] for v in group),claimed=any(v['key_id'] in claims for v in group),selected=any(v['id'] in selected for v in group))
        if matches(row,filters):result.append(row)
    def key(row):
        value={'date':row.date,'number':row.number,'payee':row.payees,'amount':row.amount,'type':row.movement.producer}[filters.sort]
        return value,row.movement.model_dump_json()
    result.sort(key=key,reverse=filters.descending)
    token=digest(dict(snapshot=fingerprint(s,draft),filters=filters.model_dump(mode='json'),rows=[v.model_dump(mode='json') for v in result]))
    require(expected_fingerprint in (None,token),'E_QUERY_STALE')
    require(offset<=len(result),'E_QUERY_STALE')
    return m.CandidatePage(items=tuple(result[offset:offset+limit]),count=len(result),component_count=sum(v.component_count for v in result),
        positive_sum=bounded(sum(v.amount for v in result if v.amount>0 and not v.stale)),negative_sum=bounded(sum(v.amount for v in result if v.amount<0 and not v.stale)),fingerprint=token,next_offset=offset+limit if offset+limit<len(result) else None)

def component_items(s,movement,group_hash, *, limit=50,offset=0):
    require(type(limit) is int and 1<=limit<=200 and type(offset) is int and offset>=0,'E_VALIDATION')
    values=next((v for v in groups(s.versions.values()).values() if json.loads(v[0]['movement_snapshot'])==movement.model_dump(mode='json')),None)
    require(values is not None and group_fingerprint(values)==group_hash,'E_QUERY_STALE')
    require(offset<=len(values),'E_QUERY_STALE')
    return tuple(m.Selection(key_id=v['key_id'],version_id=v['id'],action='mark') for v in values[offset:offset+limit]),len(values)

def mark_all(s,draft,inp, *, revision_id):
    from bookflow.company.reconciliation_drafts import editable,revised
    editable(s,draft,inp.expected_version);require(inp.draft==draft.id and draft.kind!='opening','E_RECONCILIATION_MANIFEST')
    first=candidates(s,draft,inp.filters,limit=200,expected_fingerprint=inp.query_fingerprint)
    result=list(first.items);offset=first.next_offset
    while offset is not None:
        page=candidates(s,draft,inp.filters,limit=200,offset=offset,expected_fingerprint=first.fingerprint)
        result.extend(page.items);offset=page.next_offset
    choices={v.key_id:v for v in draft.selections}
    allgroups={group_fingerprint(v):v for v in groups(s.current.values()).values()}
    for row in result:
        require(not row.stale and row.eligible,'E_RECONCILIATION_SELECTION_STALE')
        if inp.action=='mark':require(not row.claimed,'E_RECONCILIATION_MEMBERSHIP_CONFLICT')
        for v in allgroups[row.group_fingerprint]:
            require(v['key_id'] not in choices or choices[v['key_id']].version_id==v['id'],'E_RECONCILIATION_SELECTION_STALE')
            if inp.action=='unmark':choices.pop(v['key_id'],None)
            else:choices[v['key_id']]=m.Selection(key_id=v['key_id'],version_id=v['id'],action='mark')
    return revised(draft,revision_id,selections=tuple(choices[k] for k in sorted(choices)))


def _slice(values, *, limit,offset,expected_fingerprint):
    require(type(limit) is int and 1<=limit<=200 and type(offset) is int and 0<=offset<=len(values),'E_QUERY_STALE')
    token=digest([v.model_dump(mode='json') for v in values])
    require(expected_fingerprint in (None,token),'E_QUERY_STALE')
    return dict(items=tuple(values[offset:offset+limit]),count=len(values),next_offset=offset+limit if offset+limit<len(values) else None,fingerprint=token)


def certificate_items(s,identity, *, kind='coverage',limit=50,offset=0,expected_fingerprint=None):
    require(kind in ('selected','outstanding','coverage'),'E_VALIDATION')
    cert=s.by('certificates')[identity];values=[]
    for member in sorted(s.rows['certificate_members'],key=lambda v:v['ordinal']):
        if member['certificate_id']!=identity or (kind!='coverage' and member['classification']!=kind):continue
        v=s.versions[member['version_id']];amount=bounded(statement_amount(v))
        values.append(m.CapturedMember(key_id=v['key_id'],version_id=v['id'],transaction_id=v['transaction_id'],source_version=v['source_version'],movement=m.MovementKey.model_validate_json(v['movement_snapshot']),date=v['effective_date'],account_id=cert['account_id'],currency=cert['currency'],amount=amount,amount_decimal=str(amount),classification=member['classification'],eligible_at_cutoff=bool(member['eligible_at_cutoff']),display=json.loads(v['display_snapshot'])))
    return m.MemberPage(**_slice(values,limit=limit,offset=offset,expected_fingerprint=expected_fingerprint))


def certificates(s,filters, *, offset=0,expected_fingerprint=None):
    require(filters.cursor is None,'E_QUERY_STALE')
    active={v['certificate_id'] for v in s.rows['active_certificates']};values=[]
    for c in sorted(s.rows['certificates'],key=lambda v:(v['statement_date'],v['generation'],v['id'])):
        if filters.account and c['account_id']!=filters.account:continue
        if filters.from_date and c['statement_date']<filters.from_date:continue
        if filters.to_date and c['statement_date']>filters.to_date:continue
        if filters.state and ((c['id'] in active)!=(filters.state=='active')):continue
        values.append(m.Certificate.model_validate(dict(c,captured_source_snapshot=json.loads(c['captured_source_snapshot']),issuer_snapshot=json.loads(c['issuer_snapshot']))))
    return m.CertificatePage(**_slice(values,limit=filters.limit,offset=offset,expected_fingerprint=expected_fingerprint))


def draft_history(s,identity, *, limit=50,offset=0,expected_fingerprint=None):
    values=[]
    for r in sorted(s.rows['draft_revisions'],key=lambda v:v['revision_number']):
        if r['draft_id']!=identity:continue
        row=dict(r);row['header']=json.loads(row.pop('header_snapshot'))
        values.append(m.DraftRevision.model_validate(row))
    return m.DraftRevisionPage(**_slice(values,limit=limit,offset=offset,expected_fingerprint=expected_fingerprint))
