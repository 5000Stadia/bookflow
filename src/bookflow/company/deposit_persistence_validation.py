"""Independent row/effect equations before any private lifecycle DML."""
from collections import defaultdict
import json
from bookflow.company import schema as c, document_effects as rows, deposit_validation as validation
from bookflow.company import journal_custom_fields as custom
from bookflow.company.deposit_models import Effect


def validate(s,ctx,plan,bundle):
    """Preserve ordinary financial/no-op/current-source precedence."""
    _validate_rows(s,ctx,plan,bundle,current_sources=True)
    from bookflow.company.deposit_draft_provider import validate_financial
    validate_financial(s,ctx,bundle['data'],bundle['financial'],plan.binding,custom_plan=plan.custom_plan)


def validate_rows(s,ctx,plan,bundle):
    """Row equations for a coordinator that independently admits its overlay."""
    _validate_rows(s,ctx,plan,bundle,current_sources=False)


def _validate_rows(s,ctx,plan,bundle,*,current_sources):
    require=validation.require
    data=bundle['data'];pending=bundle['pending'];h=bundle['header'];old=data['before'];financial=bundle['financial']
    validation.validate(financial)
    require(financial.intent.deposit_id==h['id']==data['identity'])
    if not data['changed']:
        require(h==old and not any(pending.values()) and not bundle['source_headers'] and not bundle['claims'] and not bundle['bank_current'])
        require(plan.custom_plan is None or not plan.custom_plan.changed)
        return
    previous=Effect.model_validate_json(json.dumps(data['previous'])) if data['previous'] else None
    if current_sources and plan.verb!='void':
        if data.get('draft') is None:
            validation.validate_current(financial,s,replacing_deposit=h['id'],previous=previous)
        else:
            # Independently decoded captured-reference proof follows below.
            validation.validate_current_sources(financial,s,replacing_deposit=h['id'])
    require(h['version']==(old['version']+1 if old else 1))
    before_by_id=data['source_headers']
    require(len(bundle['source_headers'])==len(before_by_id))
    for before,after in bundle['source_headers']:
        require(before==before_by_id[before['id']])
        require({k:v for k,v in after.items() if k not in ('version','updated_at','updated_by','updated_via')}==
                {k:v for k,v in before.items() if k not in ('version','updated_at','updated_by','updated_via')})
        require(after['version']==before['version']+1)
    releases=[r for r in pending['deposit_memberships'] if r['kind']=='release']
    require(len(releases)==len(data['claims']))
    oldclaims={r['id']:r for r in data['claims']}
    for release in releases:
        claim=oldclaims[release['reverses_membership_id']]
        require(all(release[k]==v for k,v in claim.items() if k not in ('id','kind','reverses_membership_id','created_at','created_by','created_via','audit_event_id')))
    claims=[r for r in pending['deposit_memberships'] if r['kind']=='claim']
    require(claims==bundle['claims'])
    expected={} if plan.verb=='void' else {r.source.transaction_id:r for r in financial.intent.sources}
    require(len(claims)==len(expected))
    # Aggregate equality is independent of individual claims; decoding the
    # complete effect once preserves the proof without quadratic work.
    require(Effect.model_validate_json(json.dumps(data['financial']))==financial)
    for claim in claims:
        source=expected[claim['source_transaction_id']]
        require(claim['row_id']==source.row_id and claim['amount_minor_units']==source.source.cash_minor_units)
        require(json.loads(claim['facts_snapshot'])==source.source.model_dump(mode='json'))
    batches={r['id']:r for r in pending['posting_batches']};sums=defaultdict(int)
    lines={r['id']:r for r in pending['posting_lines']}
    reversal_batches=[r for r in batches.values() if r['kind']=='reversal']
    require(len(reversal_batches)==(1 if old else 0))
    for inverse in reversal_batches:
        original_lines=rows.rows(s,c.posting_lines,c.posting_lines.c.batch_id==inverse['reverses_batch_id'])
        original_ids={r['id'] for r in original_lines}
        inverse_lines=[r for r in lines.values() if r['batch_id']==inverse['id']]
        require(len(inverse_lines)==len(original_lines) and {r['reversed_line_id'] for r in inverse_lines}==original_ids)
        original_sources=[r for line in original_lines for r in rows.rows(s,c.posting_line_sources,c.posting_line_sources.c.posting_line_id==line['id'])]
        inverse_ids={r['id'] for r in inverse_lines}
        inverse_sources=[r for r in pending['posting_line_sources'] if r['posting_line_id'] in inverse_ids]
        require(len(inverse_sources)==len(original_sources) and {r['reversed_source_id'] for r in inverse_sources}=={r['id'] for r in original_sources})
    require(len(lines)==len(pending['posting_lines']))
    for line in lines.values():
        require(line['transaction_id']==h['id'] and line['batch_id'] in batches)
        require((line['debit_minor_units']>0) != (line['credit_minor_units']>0))
        sums[line['batch_id']]+=line['debit_minor_units']-line['credit_minor_units']
        if line['reversed_line_id']:
            original=rows.rows(s,c.posting_lines,c.posting_lines.c.id==line['reversed_line_id'])[0]
            require(all(line[k]==v for k,v in original.items() if k not in ('id','batch_id','debit_minor_units','credit_minor_units','reversed_line_id','created_at','created_by','created_via')))
            require((line['debit_minor_units'],line['credit_minor_units'])==(original['credit_minor_units'],original['debit_minor_units']))
    require(set(sums)==set(batches) and not any(sums.values()))
    sources=pending['posting_line_sources'];attributed=defaultdict(int)
    components={r['id']:r for r in pending['deposit_components']}
    for source in sources:
        require(source['posting_line_id'] in lines and source['transaction_id']==h['id'])
        attributed[source['posting_line_id']]+=source['amount_minor_units']
        if source['reversed_source_id']:
            original=rows.rows(s,c.posting_line_sources,c.posting_line_sources.c.id==source['reversed_source_id'])[0]
            require(all(source.get(k)==v for k,v in original.items() if k not in ('id','posting_line_id','reversed_source_id','created_at','created_by','created_via')))
        else:
            component=components[source['deposit_component_id']]
            require(source['document_line_id']==component['document_line_id'] and source['revision_id']==component['revision_id'])
    require(set(attributed)==set(lines))
    require(all(attributed[key]==value['debit_minor_units']+value['credit_minor_units'] for key,value in lines.items()))
    if plan.verb=='void':
        require(h['status']=='voided' and len(batches)==1 and all(r['kind']=='reversal' for r in batches.values()))
        require(not pending['transaction_revisions'] and not pending['deposit_components'])
    else:
        business=[r for r in lines.values() if batches[r['batch_id']]['kind']!='reversal']
        require(len(business)==len(financial.legs))
        for actual,expected in zip(business,financial.legs):
            dims=expected.dimensions
            require((actual['account_id'],actual['debit_minor_units']-actual['credit_minor_units'],actual['currency'],actual['name_type'],actual['name_id'],actual['party_name'],actual['class_id'],actual['class_name'])==
                (expected.account_id,expected.signed_debit,expected.currency,dims.party_kind,dims.party_id,dims.party_name,dims.class_id,dims.class_name))
        emitted={(components[r['component_id']]['row_id'],components[r['component_id']]['component_ordinal'],
            'additional:'+r['bucket_row_id'] if r['bucket']=='additional' else r['bucket']):r['amount_minor_units'] for r in pending['deposit_cash_cells']}
        require(len(emitted)==len(pending['deposit_cash_cells']) and emitted=={(v.row_id,v.component_ordinal,v.bucket):v.units for v in financial.cells})
        revision=pending['transaction_revisions'][0]
        require(revision['total_minor_units']==financial.posting_total and revision['date']==financial.intent.date)
        require(json.loads(pending['deposit_profiles'][0]['facts_snapshot'])==financial.model_dump(mode='json'))
        if data.get('draft') is None:
            custom.validate(s.company,plan.custom_plan,h['id'],json.loads(revision['custom_fields_snapshot']),record_type='deposit')
        else:
            # The full draft validator below proves immutable captured metadata
            # and exact current slot mutations independently.
            require(json.loads(revision['custom_fields_snapshot'])==plan.custom_plan.snapshot)
    if plan.verb!='void':
        expected_components={}
        for row in financial.intent.sources:
            orders={o.key:o.ordinal for o in row.occurrences if o.present}
            for value in row.source.components:
                expected_components[(row.row_id,orders[value.key])]=dict(role='funding',capacity=value.capacity,
                    source_transaction_id=row.source.transaction_id,source_revision_id=row.source.revision_id,
                    source_document_line_id=value.document_line_id,source_posting_line_id=value.posting_line_id,
                    source_attribution_id=value.posting_source_id,facts_snapshot=value.model_dump(mode='json'))
        for row in financial.intent.additional:
            expected_components[(row.row_id,0)]=dict(role='funding' if row.units>0 else 'offset',capacity=abs(row.units),
                source_transaction_id=None,source_revision_id=None,source_document_line_id=None,source_posting_line_id=None,source_attribution_id=None,
                facts_snapshot=row.model_dump(mode='json'))
        if financial.intent.cash_back:
            expected_components[(data['header_row'],1)]=dict(role='cash_back',capacity=financial.cash_back,
                source_transaction_id=None,source_revision_id=None,source_document_line_id=None,source_posting_line_id=None,source_attribution_id=None,
                facts_snapshot=financial.intent.cash_back.model_dump(mode='json'))
        emitted={(r['row_id'],r['component_ordinal']):r for r in components.values()}
        require(len(emitted)==len(components) and set(emitted)==set(expected_components))
        for key,expected in expected_components.items():
            actual=emitted[key]
            require(actual['transaction_id']==h['id'] and actual['revision_id']==bundle['revision']['id'])
            require(all((json.loads(actual[k]) if k=='facts_snapshot' else actual[k])==v for k,v in expected.items()))
        # Check every business posting's exact component owner independently of
        # funding allocation sums. Cash-back and fee lines use owned envelopes.
        by_line={r['posting_line_id']:r for r in sources if r['reversed_source_id'] is None}
        for actual,leg in zip(business,financial.legs):
            if leg.key.startswith('cash_back/'):
                expected_key=(data['header_row'],1)
            elif leg.key.startswith('additional:'):
                expected_key=(leg.key.split('/')[0].split(':',1)[1],0)
            else:
                _,rowid,ordinal=leg.key.split('/');expected_key=(rowid,int(ordinal))
            require(by_line[actual['id']]['deposit_component_id']==emitted[expected_key]['id'])
    # Each appended current pointer names exactly the new immutable version.
    versions=pending['bank_effect_versions']
    require({(r['key_id'],r['id']) for r in versions}=={(r['key_id'],r['version_id']) for r in bundle['bank_current']})
    require(len(versions)==len(bundle['bank_current']))
    keys={r['id']:r for r in rows.rows(s,c.bank_effect_keys,c.bank_effect_keys.c.transaction_id==h['id'])}
    keys.update({r['id']:r for r in pending['bank_effect_keys']})
    expected_bank={}
    if plan.verb!='void':
        candidates=[('main_bank',data['header_row'],financial.intent.bank,financial.bank_total)]
        if financial.intent.cash_back:
            candidates.append(('cash_back',data['header_row'],financial.intent.cash_back.account,financial.cash_back))
        candidates.extend(('additional',r.row_id,r.account,-r.units) for r in financial.intent.additional)
        for role,row_id,account,signed in candidates:
            if account.type in ('bank','credit_card') and signed:
                expected_bank[(role,row_id)]=(account.id,signed,-signed if account.type=='credit_card' else signed,True)
    require(len(versions)==len(keys))
    for version in versions:
        key=keys[version['key_id']]
        expected=expected_bank.pop((key['role'],key['row_id']),None)
        if expected is None:
            current=rows.rows(s,c.bank_effect_current,c.bank_effect_current.c.key_id==key['id'])
            require(len(current)==1)
            prior=rows.rows(s,c.bank_effect_versions,c.bank_effect_versions.c.id==current[0]['version_id'])[0]
            expected=(prior['account_id'],0,0,False)
        require((version['account_id'],version['signed_debit'],version['statement_amount'],bool(version['active']))==expected)
        require(version['effective_date']==financial.intent.date and version['currency']==financial.intent.currency)
        require(version['number']==data['number'] and version['memo']==data['memo'])
        require(version['transaction_id']==h['id'] and version['revision_id']==bundle['revision']['id'])
        old_versions=rows.rows(s,c.bank_effect_versions,c.bank_effect_versions.c.key_id==version['key_id'])
        require(version['version']==max((r['version'] for r in old_versions),default=0)+1)
        require(version['active']==(version['signed_debit']!=0) and version['active']==(version['statement_amount']!=0))
        if plan.verb=='void':require(not version['active'])
    require(not expected_bank)
