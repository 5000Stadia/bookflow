"""Complete admitted stored snapshots; no writes or financial preparation."""
from dataclasses import dataclass, replace
import json
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.company import schema as c, deposit_dependency_history as h, deposit_sources
from bookflow.company import deposit_read_authority as authority, deposit_read_validation as v
from bookflow.company import deposit_operation_pages as opages, deposit_drafts, deposit_draft_consumption
from bookflow.company.deposit_read_models import Selected, Pin, EvidenceLink, Navigation, HistoryEntry
from bookflow.core.errors import BookflowError

TABLES=('transaction_revisions','deposit_profiles','document_line_identities','deposit_row_keys',
 'document_lines','deposit_component_keys','deposit_components','posting_batches','posting_lines','posting_line_sources',
 'deposit_cash_cells','deposit_memberships','bank_effect_keys','bank_effect_versions','deposit_current_memberships','deposit_operations')

@dataclass(frozen=True)
class ValidatedDeposit:
    header: dict
    graph: dict
    selected: dict
    effects: dict
    evidence: authority.ReadEvidence
    links: tuple
    history: tuple
    references: tuple
    sources: dict


def invalid(error):
    if isinstance(error,BookflowError) and error.code not in ('E_VALIDATION','E_INTERNAL','E_DEPOSIT_SOURCE_INVALID','E_DEPOSIT_DATE_BEFORE_SOURCE'):
        raise error
    raise BookflowError('E_DEPOSIT_SOURCE_INVALID') from None


def load_complete(s,deposit_ids,*,binding,annotations=('note','attachment')):
    from bookflow.company import deposit_read_manifest as manifest
    authority.authenticate(s,binding)
    ids=tuple(sorted(set(deposit_ids)))
    evidence=authority.admit(s,ids,binding=binding)
    headers=authority.select(s,c.transactions,c.transactions.c.id,ids)
    if len(headers)!=len(ids) or any(r['type']!='deposit' for r in headers):raise BookflowError('E_RECORD_NOT_FOUND')
    manifest.conform()
    graphs={name:authority.select(s,getattr(c,name),getattr(c,name).c.transaction_id,ids) for name in TABLES}
    roots=set(evidence.roots)
    source_ids={r['source_transaction_id'] for r in graphs['deposit_memberships']}
    source_graphs=deposit_sources.graph_many(s,source_ids)
    source_headers={r['id']:r for r in authority.select(s,c.transactions,c.transactions.c.id,source_ids)}
    source_claims={r['source_transaction_id']:r for r in authority.select(s,c.deposit_current_memberships,c.deposit_current_memberships.c.source_transaction_id,source_ids)}
    reader=h.History(s)
    events=set(evidence.events)
    for rows in graphs.values():events.update(r['audit_event_id'] for r in rows if 'audit_event_id' in r)
    for graph in source_graphs.values():
        events.update(r['audit_event_id'] for name,rows in graph.items() if name!='header' for r in rows if 'audit_event_id' in r)
    event_rows={r['id']:r for r in authority.select(s,c.audit_events,c.audit_events.c.id,events)}
    h._authorize_binding_graph(s,binding,tuple(sorted(roots)),tuple(sorted(events)),write=False)
    outputs={};links={i:[] for i in ids}
    # Only this complete read's coherent snapshot owns these results. Repeated
    # operation/link assembly must not reconstruct an identical draft again.
    # Consumption.current retains its own independent admission/receipt check.
    loaded_drafts={}
    def load_draft(identity,kind='draft'):
        key=(kind,identity)
        if key not in loaded_drafts:
            loaded_drafts[key]=deposit_drafts.load(s,identity,kind=kind,binding=binding)
        return loaded_drafts[key]
    try:
        for draft_id in evidence.drafts:
            dh,dr,dm,_=load_draft(draft_id)
            linked=dh['edit_transaction_id'] or dh['copy_transaction_id']
            if linked in links:links[linked].append(EvidenceLink(kind='draft',id=draft_id,related_id=dr['id']))
        for op in graphs['deposit_operations']:
            output=opages.authorized_original(s,op,binding,write=False)
            collections=opages.collections(output)
            stored=authority.select(s,c.deposit_operation_items,c.deposit_operation_items.c.operation_id,[op['id']])
            v.require(set(r['kind'] for r in stored)<=set(collections))
            for kind,values in collections.items():
                found=sorted([r for r in stored if r['kind']==kind],key=lambda r:r['ordinal'])
                v.require([r['ordinal'] for r in found]==list(range(len(values))) and [json.loads(r['facts_snapshot']) for r in found]==values)
            current=deposit_draft_consumption.current(s,op['id'],binding=binding,write=False)
            saved_effect=output.effect.deposit if output.command=='deposit coordinate' else output.effect
            if saved_effect.consumed_draft:
                v.require(current is not None and current.id==saved_effect.consumed_draft.draft_id)
            else:v.require(current is None)
            outputs[op['id']]=output
            links[op['transaction_id']].append(EvidenceLink(kind='operation',id=op['id'],label=op['operation_key']))
            if current:
                dh,dr,dm,_=load_draft(current.id)
                links[op['transaction_id']].append(EvidenceLink(kind='draft',id=current.id,related_id=dr['id']))
                children=authority.select(s,c.deposit_selections,c.deposit_selections.c.target_draft_id,[current.id])
                for child in children:
                    load_draft(child['id'],kind='selection')
                    links[op['transaction_id']].append(EvidenceLink(kind='selection',id=child['id'],related_id=child['accepted_revision_id']))
        results=[]
        for header in headers:
            identity=header['id'];graph={name:[r for r in rows if r['transaction_id']==identity] for name,rows in graphs.items()}
            graph['_event_sequences']={k:r['seq'] for k,r in event_rows.items()}
            v.require(reader.take('transaction',identity) is not None)
            v.audit_rows(reader,{k:x for k,x in graph.items() if not k.startswith('_')})
            revs=sorted(graph['transaction_revisions'],key=lambda r:r['revision_number']);previous=None
            chosen={};effects={}
            for n,rev in enumerate(revs,1):
                v.require(rev['revision_number']==n and rev['supersedes_revision_id']==previous);previous=rev['id']
                effect,issuer,custom=v.revision(graph,rev,source_graphs,reader)
                v.require(issuer.id==s.company_info_row['id'] and issuer.home_currency==rev['currency'])
                effects[rev['id']]=effect
                chosen[n]=Selected(pin=Pin(deposit=identity,revision_id=rev['id'],revision_number=n),date=rev['date'],number=rev['number'],memo=rev['memo'],issuer=issuer,custom_fields=custom,deposit_to=effect.intent.bank,cash_back=effect.intent.cash_back)
                links[identity].append(EvidenceLink(kind='revision',id=rev['id']))
            v.require(revs and header['current_revision_id']==revs[-1]['id'] and header['number']==revs[-1]['number'])
            v.lifecycle(graph,header)
            _bank(s,graph,effects,header)
            for version in graph['bank_effect_versions']:links[identity].append(EvidenceLink(kind='bank_version',id=version['id'],related_id=version['key_id'],active=bool(version['active'])))
            if annotations:_associations(s,identity,links[identity],annotations)
            semantic=_history(graph,event_rows,outputs)
            navigation=_navigation(s,effects)
            result_sources={key:(source_graphs[key],source_headers[key],source_claims.get(key)) for key in source_ids}
            proof=replace(evidence,events=tuple(sorted(events)),selected_pins=tuple((identity,r['id']) for r in revs),
                consumed_links=tuple((op['id'],outputs[op['id']].effect.deposit.consumed_draft.draft_id if outputs[op['id']].command=='deposit coordinate' else outputs[op['id']].effect.consumed_draft.draft_id,identity) for op in graph['deposit_operations'] if (outputs[op['id']].effect.deposit if outputs[op['id']].command=='deposit coordinate' else outputs[op['id']].effect).consumed_draft),
                current_references=navigation,observable_fields=('selected','current','totals','counts','fingerprints','dated_state','links','current_references','dependencies','sources','additional','cash_allocations','history'))
            results.append(ValidatedDeposit(header,graph,chosen,effects,proof,tuple({(link.kind,link.id):link for link in links[identity]}.values()),semantic,navigation,result_sources))
        return tuple(results)
    except (ValidationError,h.MissingHistory,ValueError,KeyError,TypeError,IndexError,BookflowError) as error:invalid(error)


def _bank(s,g,effects,header):
    keys={r['id']:r for r in g['bank_effect_keys']};versions=g['bank_effect_versions']
    current=authority.select(s,c.bank_effect_current,c.bank_effect_current.c.key_id,keys)
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

from collections import Counter

def _history(g,events,outputs):
    rows=[]
    def add(kind,identity,event,**extra):
        e=events[event]
        value=HistoryEntry(id=kind+':'+identity,kind=kind,event_id=event,at=e['at'],actor_id=e['actor_id'],interface=e['interface'],on_behalf_of=e['on_behalf_of'],reason=e['reason'],**extra)
        rows.append((e['seq'],value.id,value))
    for r in g['transaction_revisions']:
        add('revision_created' if r['revision_number']==1 else 'replaced',r['id'],r['audit_event_id'],revision_id=r['id'],previous_revision_id=r['supersedes_revision_id'])
    for r in g['deposit_memberships']:
        add('membership_claimed' if r['kind']=='claim' else 'membership_released',r['id'],r['audit_event_id'],membership_id=r['id'],revision_id=r['revision_id'],source_ids=(r['source_transaction_id'],),batch_ids=(r['batch_id'],))
    for r in g['deposit_operations']:
        output=outputs[r['id']];effect=output.effect.deposit if output.command=='deposit coordinate' else output.effect
        kw=dict(operation_id=r['id'],operation_key=r['operation_key'],revision_id=effect.after.revision_id,batch_ids=effect.batch_ids,bank_version_ids=tuple(b['id'] for b in g['bank_effect_versions'] if b['audit_event_id']==r['audit_event_id']))
        if output.command=='deposit coordinate':add('coordinated_source_change',r['id'],r['audit_event_id'],source_ids=tuple(x for x in output.effect.target_ids if x!=r['transaction_id']),**kw)
        if not output.changed:add('no_effect_operation',r['id'],r['audit_event_id'],**kw)
        elif effect.action=='void':add('void',r['id'],r['audit_event_id'],**kw)
        if effect.consumed_draft:add('draft_consumed',r['id'],r['audit_event_id'],draft_id=effect.consumed_draft.draft_id,**kw)
    return tuple(r[2] for r in sorted(rows,key=lambda x:x[:2]))


def _navigation(s,effects):
    refs={}
    for e in effects.values():
        refs.setdefault('accounts',set()).add(e.intent.bank.id)
        if e.intent.cash_back:refs['accounts'].add(e.intent.cash_back.account.id)
        for row in e.intent.additional:
            refs['accounts'].add(row.account.id)
            table={'customer':'customers','vendor':'vendors','employee':'employees','other_name':'other_names'}[row.dimensions.party_kind]
            refs.setdefault(table,set()).add(row.dimensions.party_id)
            if row.payment_method:refs.setdefault('payment_methods',set()).add(row.payment_method.id)
            if row.dimensions.class_id:refs.setdefault('classes',set()).add(row.dimensions.class_id)
        for row in e.intent.sources:
            refs['accounts'].add(row.source.uf_account)
            profile=row.source.profile;party=profile.payer if row.source.source_type=='payment' else profile.customer
            refs.setdefault('customers',set()).add(party.id)
            if profile.payment_method:refs.setdefault('payment_methods',set()).add(profile.payment_method.id)
    result=[]
    for name,ids in sorted(refs.items()):
        table=getattr(c,name);found={r['id']:r for r in authority.select(s,table,table.c.id,ids)}
        for identity in sorted(ids):
            row=found.get(identity)
            result.append(Navigation(current_type=row.get("kind") if row else None,kind=name,id=identity,label=(row.get('full_name') or row['name']) if row else None,active=row['active'] if row else None,version=row['version'] if row else None,available=row is not None))
    return tuple(result)


def _associations(s,identity,links,kinds=('note','attachment')):
    if 'note' in kinds:
        for r in s.company.conn.execute(sa.select(c.notes).where(c.notes.c.record_type=='transaction',c.notes.c.record_id==identity)).mappings():
            links.append(EvidenceLink(kind='note',id=r['id']))
    if 'attachment' in kinds:
        for r in s.company.conn.execute(sa.select(c.attachment_links).where(c.attachment_links.c.record_type=='transaction',c.attachment_links.c.record_id==identity)).mappings():
            links.append(EvidenceLink(kind='attachment',id=r['id'],related_id=r['attachment_id'],active=r['active'],label=r['caption']))
