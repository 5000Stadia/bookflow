"""Whole-work tax preparation, immutable attribution and independent effect checks."""
from copy import deepcopy
from bookflow.company import schema as c, tax_attribution as tax, tax_policy
from bookflow.company.work_tax_facts import read_facts, read_line

TABLE_KEYS = {'work_tax_line_keys':'line_id', 'work_tax_attributions':'revision_id', 'work_tax_attribution_lines':'work_line_id'}


def normalized(value):
    if value is None:return None
    result=deepcopy(value)
    result['facts'].pop('schema_version',None)
    if 'schema_version' in value['facts']['profile']:
        result['facts']['profile']=tax.semantic_profile(read_facts(value['facts']).profile)
    for line in result['lines']:line['facts'].pop('schema_version',None)
    return result


def economics(value):
    # Old lineages retain their entire legacy basis, including component cents.
    excluded={'completed_quantity_microunits','billable'}
    if value['schema_version']==2:excluded|={'schema_version','tax_minor_units','gross_minor_units','taxes'}
    result={key:item for key,item in value.items() if key not in excluded}
    if value['schema_version']==2:result['tax_rules']=[cell['rule'] for cell in value['taxes']]
    return result


def prepare(s,document_id,value):
    """Mutate only the prospective value. Ordinal planning never writes."""
    f=read_facts(value['facts']);profile=f.profile
    captured=profile.model_dump(mode='json')
    captured.update(schema_version=2,sales_tax_calculation=tax_policy.effective(profile),tax_policy_origin=tax_policy.origin(profile).model_dump(mode='json'))
    value['facts']=dict(value['facts'],schema_version=2,profile=captured)
    profile=read_facts(value['facts']).profile
    lines=[read_line(entry['facts']) for entry in value['lines']]
    ordinals,keys=tax.prospective(s.company,document_id,[entry['line_id'] for entry in value['lines']],work=True)
    inputs=[dict(net_minor_units=line.net_minor_units,taxes=[dict(rule=t.rule,taxable_minor_units=t.taxable_minor_units,tax_minor_units=t.tax_minor_units) for t in line.taxes]) for line in lines]
    attributed=tax.calculate(inputs,profile,s.company_info_row['home_currency'],ordinals)
    tax.apply(inputs,attributed,ordinals)
    for entry,line,derived in zip(value['lines'],lines,inputs):
        facts=deepcopy(entry['facts'])
        if tax_policy.effective(profile)!=tax_policy.LEGACY:facts['schema_version']=2
        facts.update(tax_minor_units=derived['tax_minor_units'],gross_minor_units=derived['gross_minor_units'],
            taxes=[dict(rule=t['rule'].model_dump(mode='json'),taxable_minor_units=t['taxable_minor_units'],tax_minor_units=t['tax_minor_units']) for t in derived['taxes']])
        entry['facts']=read_line(facts).model_dump(mode='json')
    return attributed,ordinals,keys


def persist(s,header,revision,value,pending,provenance):
    attributed,ordinals,keys=prepare(s,header['id'],deepcopy(value))
    lines=[row for row in pending['work_lines'] if row['revision_id']==revision['id']]
    for entry,line,ordinal in zip(value['lines'],lines,ordinals,strict=True):
        if entry['line_id'] is None:keys[line['line_id']]=ordinal
        pending['work_tax_attribution_lines'].append(dict(document_id=header['id'],revision_id=revision['id'],
            work_line_id=line['id'],line_id=line['line_id'],tax_ordinal=ordinal,**provenance))
    pending['work_tax_line_keys'].extend(dict(document_id=header['id'],line_id=key,tax_ordinal=ordinal,**provenance) for key,ordinal in keys.items())
    pending['work_tax_attributions'].append(dict(document_id=header['id'],revision_id=revision['id'],facts_snapshot=attributed.model_dump_json(),**provenance))


def validate(s,rev,lines,pending,require):
    profile=read_facts(rev['facts_snapshot']).profile
    require(profile.schema_version==2,'new work revision has no captured tax policy')
    new_ids={row['id'] for row in pending['work_line_identities']}
    lines=sorted(lines,key=lambda row:row['position'])
    ordinals,keys=tax.prospective(s.company,rev['document_id'],[None if row['line_id'] in new_ids else row['line_id'] for row in lines],work=True)
    keys.update({row['line_id']:ordinal for row,ordinal in zip(lines,ordinals) if row['line_id'] in new_ids})
    actual_keys=[row for row in pending['work_tax_line_keys'] if row['document_id']==rev['document_id']]
    require(len(actual_keys)==len(keys) and {row['line_id']:row['tax_ordinal'] for row in actual_keys}==keys,'wrong immutable work tax keys')
    mapping=[row for row in pending['work_tax_attribution_lines'] if row['revision_id']==rev['id']]
    require(len(mapping)==len(lines) and {(r['work_line_id'],r['line_id'],r['tax_ordinal']) for r in mapping}=={(row['id'],row['line_id'],ordinal) for row,ordinal in zip(lines,ordinals)},'wrong work tax line ownership')
    require(all(row['document_id']==rev['document_id'] for row in mapping),'work tax owner differs')
    inputs=[]
    for row in lines:
        facts=read_line(row['facts_snapshot'])
        require(facts.schema_version==2 or tax_policy.effective(profile)==tax_policy.LEGACY,'combined work policy requires version2 line facts')
        taxable=profile.preferences.sales_tax_enabled and facts.profile.tax_code is not None and facts.profile.tax_code.taxable
        exempt=profile.customer_tax_code is not None and not profile.customer_tax_code.taxable
        rules=profile.tax_rules if taxable and not exempt else []
        require(rules is not None,'missing work tax rules')
        inputs.append(dict(net_minor_units=facts.net_minor_units,taxes=[dict(rule=rule) for rule in rules]))
    expected=tax.calculate(inputs,profile,rev['currency'],ordinals)
    snapshots=[row for row in pending['work_tax_attributions'] if row['revision_id']==rev['id']]
    require(len(snapshots)==1 and snapshots[0]['document_id']==rev['document_id'],'wrong work tax snapshot owner')
    require(tax.read_snapshot(snapshots[0]['facts_snapshot'])==expected,'work tax exact buckets/cells differ from authoritative facts')
    return {(row['id'],cell.rule.id):cell.tax_minor_units for row,ordinal in zip(lines,ordinals)
            for bucket in expected.calculation.buckets for cell in bucket.cells if cell.tax_ordinal==ordinal}
