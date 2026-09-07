"""Stored graph equations; never prepares or executes a proposed transaction."""
import json
from collections import Counter
from pydantic import ValidationError
from bookflow.company import deposit_validation as arithmetic, deposit_sources, deposit_dependency_history as history
from bookflow.company.deposit_models import Effect
from bookflow.company.deposit_read_models import Issuer, CustomValue
from bookflow.company.journal_custom_fields import SnapshotField
from bookflow.core.errors import BookflowError


def require(ok):
    if not ok:raise BookflowError('E_DEPOSIT_SOURCE_INVALID')


def exact_one(values):
    require(len(values)==1);return values[0]


def audit_rows(reader,graph):
    """The finite immutable owner registry supplies exact historical row proofs."""
    owners={table:(key,kind) for table,key,kind in history._IMMUTABLE}
    for table,values in graph.items():
        if table not in owners:continue
        key,kind=owners[table]
        for row in values:
            value=reader.take(kind,row[key])
            require(value is not None)
            # Compare every literal stored field to its owning immutable image,
            # including attribution fields excluded from semantic coalescing.
            entries=reader.entries[kind].get(row[key],())
            creation=exact_one([r for r in entries if r['version_before'] is None])
            raw=history._audit_image(creation['after'])
            if kind=='posting_line_source':
                # Exact document_effects/history wire convention: omitted null
                # extension slots, never an arbitrary nullable-field fallback.
                for extension in ('deposit_component_id','payment_component_id'):
                    if extension not in raw and row[extension] is None:raw={**raw,extension:None}
            expected={k:json.loads(value) if k.endswith('_snapshot') and value is not None else value for k,value in row.items()}
            require(all(k in raw and type(raw[k]) is type(value) and raw[k]==value for k,value in expected.items()))
    require(not reader.unknown)


def revision(graph,rev,source_graphs,reader):
    identity=rev['transaction_id'];rid=rev['id']
    part=lambda name:[r for r in graph[name] if r.get('revision_id')==rid]
    p=exact_one(part('deposit_profiles'))
    effect=Effect.model_validate_json(p['facts_snapshot']);arithmetic.validate(effect)
    require(effect.inverse_of is None and effect.intent.deposit_id==identity and effect.intent.date==rev['date'] and effect.intent.currency==rev['currency'])
    require((p['posting_total'],p['subtotal'],p['bank_total'],p['cash_back'],p['bank_account_id'])==
            (effect.posting_total,effect.subtotal,effect.bank_total,effect.cash_back,effect.intent.bank.id))
    require(p['type']=='deposit' and rev['total_minor_units']==effect.posting_total)
    issuer=Issuer.model_validate_json(rev['issuer_snapshot'])
    custom=json.loads(rev['custom_fields_snapshot']);require(type(custom) is dict)
    customs=[]
    for key,value in custom.items():
        field=SnapshotField.model_validate_json(history.canonical(value));require(field.definition_id==key)
        customs.append(CustomValue(captured=field,captured_print_visibility=None))
    keys={r['id']:r for r in graph['deposit_row_keys']}
    envelopes=part('document_lines');byline={r['line_id']:r for r in envelopes}
    require(len(byline)==len(envelopes))
    expected={r.row_id:(r.ordinal,'source',r.memo) for r in effect.intent.sources}
    expected.update({r.row_id:(r.ordinal,'additional',r.memo) for r in effect.intent.additional})
    headerkeys=[r for r in keys.values() if r['kind']=='header'];hk=exact_one(headerkeys)
    expected[hk['id']]=(hk['ordinal'],'header',rev['memo'])
    require(len(envelopes)==len(expected))
    for key,(ordinal,kind,memo) in expected.items():
        require(key in keys);k=keys[key];require((k['transaction_id'],k['kind'],k['ordinal'])==(identity,kind,ordinal))
        line=byline.get(k['line_id']);require(line is not None)
        require((line['transaction_id'],line['revision_id'],line['position'],line['kind'],line['currency'],line['description'])==(identity,rid,ordinal,'deposit',rev['currency'],memo))
        require(all(line[v] is None for v in ('account_id','side','amount_minor_units','account_snapshot','name_type','name_id','party_name','class_id','class_name','original_minor_units','original_currency','rate_used','rate_source')))
    components=part('deposit_components');comp={(r['row_id'],r['component_ordinal']):r for r in components};require(len(comp)==len(components))
    occurrence={(r['row_id'],r['ordinal']):r for r in graph['deposit_component_keys']}
    expected_components={}
    for row in effect.intent.sources:
        src=row.source
        # Numeric captured header endpoint is independently selected in raw history;
        # current source ineligibility has no bearing on historical display.
        reader.load('transaction')
        endpoint=exact_one([e for e in reader.entries['transaction'].get(src.transaction_id,()) if e['version_after']==src.expected_header_version])
        saved=history._audit_image(endpoint['after']);require(saved is not None and saved['status']=='posted' and saved['current_revision_id']==src.revision_id and saved['type']==src.source_type)
        cutoff=reader.cutoff
        try:reader.cutoff=endpoint['seq'];require(bool(reader.chain('transaction',src.transaction_id)))
        finally:reader.cutoff=cutoff
        g=dict(source_graphs[src.transaction_id]);g['header']=saved
        g['posting_batches']=[b for b in g['posting_batches'] if b['id']==src.business_batch_id]
        if src.source_type=='payment':
            g['payment_component_keys']=[k for k in g['payment_component_keys'] if graph['_event_sequences'].get(k['audit_event_id'],10**100)<=endpoint['seq']]
        actual=deposit_sources.project(g,uf_account=src.uf_account,home_currency=src.currency)
        require(actual==src)
        orders={o.key:o.ordinal for o in row.occurrences if o.present}
        for o in row.occurrences:
            k=occurrence.get((row.row_id,o.ordinal));require(k is not None)
            require((k['kind'],k['semantic_identity'],k['tax_item_id'])==(o.key.kind,o.key.identity,o.key.tax_item))
        for c in src.components:
            expected_components[row.row_id,orders[c.key]]=('funding',c.capacity,c.model_dump(mode='json'),(src.transaction_id,src.revision_id,c.document_line_id,c.posting_line_id,c.posting_source_id))
    for row in effect.intent.additional:
        expected_components[row.row_id,0]=('funding' if row.units>0 else 'offset',abs(row.units),row.model_dump(mode='json'),(None,)*5)
    if effect.intent.cash_back:
        expected_components[hk['id'],1]=('cash_back',effect.cash_back,effect.intent.cash_back.model_dump(mode='json'),(None,)*5)
    require(set(comp)==set(expected_components))
    for key,(role,capacity,snapshot,source) in expected_components.items():
        c=comp[key]
        require((c['transaction_id'],c['currency'],c['role'],c['capacity'])==(identity,rev['currency'],role,capacity))
        require(c['document_line_id']==byline[keys[key[0]]['line_id']]['id'])
        require(json.loads(c['facts_snapshot'])==snapshot)
        require(tuple(c[k] for k in ('source_transaction_id','source_revision_id','source_document_line_id','source_posting_line_id','source_attribution_id'))==source)
    cells=part('deposit_cash_cells');bycomponent={r['id']:r for r in components}
    actual={}
    for cell in cells:
        c=bycomponent.get(cell['component_id']);require(c is not None and c['role']=='funding')
        bucket='additional:'+cell['bucket_row_id'] if cell['bucket']=='additional' else cell['bucket']
        require(cell['bucket_row_id']==(bucket.split(':')[1] if ':' in bucket else hk['id']))
        actual[c['row_id'],c['component_ordinal'],bucket]=cell['amount_minor_units']
    require(len(actual)==len(cells) and actual=={(r.row_id,r.component_ordinal,r.bucket):r.units for r in effect.cells})
    batch=exact_one([b for b in part('posting_batches') if b['kind']!='reversal'])
    require(batch['effective_date']==rev['date'])
    lines=sorted([r for r in graph['posting_lines'] if r['batch_id']==batch['id']],key=lambda r:r['line_no'])
    require(len(lines)==len(effect.legs))
    for line,leg in zip(lines,effect.legs):
        d=leg.dimensions
        require((line['account_id'],line['debit_minor_units']-line['credit_minor_units'],line['currency'],line['name_type'],line['name_id'],line['party_name'],line['class_id'],line['class_name'])==
                (leg.account_id,leg.signed_debit,leg.currency,d.party_kind,d.party_id,d.party_name,d.class_id,d.class_name))
        a=exact_one([a for a in graph['posting_line_sources'] if a['posting_line_id']==line['id']]);c=bycomponent.get(a['deposit_component_id']);require(c is not None)
        if leg.key.startswith('cash_back/'):expected_key=(hk['id'],1)
        elif leg.key.startswith('additional:'):expected_key=(leg.key.split('/')[0].split(':')[1],0)
        else:_,rowid,ordinal=leg.key.split('/');expected_key=(rowid,int(ordinal))
        require(c['id']==comp[expected_key]['id'] and a['document_line_id']==c['document_line_id'] and a['amount_minor_units']==abs(leg.signed_debit) and a['currency']==rev['currency'])
    claims=[r for r in part('deposit_memberships') if r['kind']=='claim']
    require(len(claims)==len(effect.intent.sources))
    for row in effect.intent.sources:
        claim=exact_one([c for c in claims if c['row_id']==row.row_id]);src=row.source
        require((claim['source_transaction_id'],claim['source_revision_id'],claim['source_batch_id'],claim['amount_minor_units'],claim['currency'],claim['source_date'],claim['batch_id'])==
                (src.transaction_id,src.revision_id,src.business_batch_id,src.cash_minor_units,src.currency,src.receipt_date,batch['id']))
        require(json.loads(claim['facts_snapshot'])==src.model_dump(mode='json'))
    return effect,issuer,tuple(sorted(customs,key=lambda c:(c.captured.position,c.captured.definition_id)))


def lifecycle(graph,header):
    batches={r['id']:r for r in graph['posting_batches']};lines={r['id']:r for r in graph['posting_lines']};attrs=graph['posting_line_sources']
    reversed_batches=set()
    for batch in batches.values():
        group=[r for r in lines.values() if r['batch_id']==batch['id']]
        require(group and sum(r['debit_minor_units']-r['credit_minor_units'] for r in group)==0)
        if batch['kind']!='reversal':continue
        orig=batches.get(batch['reverses_batch_id']);require(orig is not None and orig['id'] not in reversed_batches)
        reversed_batches.add(orig['id']);require(orig['revision_id']==batch['revision_id'] and orig['effective_date']==batch['effective_date'])
        old=[r for r in lines.values() if r['batch_id']==orig['id']]
        require(len(group)==len(old) and {r['reversed_line_id'] for r in group}=={r['id'] for r in old})
        for line in group:
            prior=lines[line['reversed_line_id']]
            require(all(line[k]==v for k,v in prior.items() if k not in ('id','batch_id','debit_minor_units','credit_minor_units','reversed_line_id','created_at','created_by','created_via')))
            require((line['debit_minor_units'],line['credit_minor_units'])==(prior['credit_minor_units'],prior['debit_minor_units']))
            a=exact_one([a for a in attrs if a['posting_line_id']==line['id']]);old_a=exact_one([r for r in attrs if r['posting_line_id']==prior['id']])
            require(a['reversed_source_id']==old_a['id'] and all(a.get(k)==v for k,v in old_a.items() if k not in ('id','posting_line_id','reversed_source_id','created_at','created_by','created_via')))
    active=[b for b in batches.values() if b['kind']!='reversal' and b['id'] not in reversed_batches]
    require(header['status'] in ('posted','voided'))
    require(len(active)==(1 if header['status']=='posted' else 0))
    if active:require(active[0]['revision_id']==header['current_revision_id'])
    memberships=graph['deposit_memberships'];claims={r['id']:r for r in memberships if r['kind']=='claim'};released=set()
    for r in memberships:
        if r['kind']=='claim':continue
        old=claims.get(r['reverses_membership_id']);require(old is not None and old['id'] not in released);released.add(old['id'])
        require(all(r[k]==v for k,v in old.items() if k not in ('id','kind','reverses_membership_id','created_at','created_by','created_via','audit_event_id')))
    current=graph['deposit_current_memberships']
    require({(r['source_transaction_id'],r['transaction_id'],r['membership_id']) for r in current}=={(r['source_transaction_id'],r['transaction_id'],r['id']) for r in claims.values() if r['id'] not in released})
    require(len(current)==len(claims)-len(released))
    if header['status']=='voided':require(not current)
