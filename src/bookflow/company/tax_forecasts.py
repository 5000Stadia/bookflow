"""Complete hypothetical remaining-work tax, independent of posting span caps."""
from typing import Literal
from pydantic import Field
from bookflow.company.sales_models import StrictModel
from bookflow.company.tax_attribution import TaxAttribution


class ForecastReason(StrictModel):
    code: Literal['line_span_limit','conversion_span_limit','source_ineligible','posting_ineligible','no_charge']
    line_id: str | None = None
    recovery: str


class WorkTaxForecast(StrictModel):
    forecast_basis: Literal['all_remaining_together'] = 'all_remaining_together'
    can_bill_together: bool
    forecast_eligibility_reasons: list[ForecastReason] = Field(default_factory=list,max_length=204)
    forecast_line_ordinals: dict[str,int]
    forecast_tax_attribution: TaxAttribution = Field(json_schema_extra={'sample': {
        'schema_version':1,'origin':{'kind':'explicit','source_id':None},
        'calculation':{'policy':'invoice_combined_half_up','currency':'USD',
            'buckets':[],'lines':[],'liabilities':[],'accounts':[],
            'net_minor_units':0,'tax_minor_units':0,'gross_minor_units':0}}})
    forecast_fingerprint: str = Field(min_length=64,max_length=64,pattern='^[0-9a-f]{64}$',
        json_schema_extra={'sample':'0'*64})


def remaining(s,source,revision,*,pending=()):
    """Stream all free intervals; never build an over-limit selection descriptor."""
    import hashlib
    from bookflow.company import work,billing_queries as query
    from bookflow.company import tax_attribution as tax,tax_policy,work_preferences
    profile=work.facts(revision).profile;mode=tax_policy.effective(profile)
    identities=query.root_identities(s,source)
    pending_by_root={(r['root_document_id'],r['root_line_id']):r for r in pending}
    selected=[]
    values={};inputs=[];ordinals={};span_count=0;reasons=[];free_facts=[]
    for row in work.saved_lines(s,revision):
        facts=work.line_facts(row);identity=identities[row['line_id']]
        root=identity['root_document_id'],identity['root_line_id']
        length,net,count=query.free_amounts(s,root,facts,mode,pending_by_root.get(root))
        values[row['line_id']]=dict(length=length,net=net if facts.billable else 0,tax=0,count=count)
        free_facts.append((row['line_id'],length,net,count,facts.billable))
        if not facts.billable or length==0:continue
        selected.append((row,root,facts))
        ordinal=len(inputs)+1;ordinals[row['line_id']]=ordinal;span_count+=count
        inputs.append(dict(net_minor_units=net,taxes=[dict(rule=t.rule) for t in facts.taxes]))
        if count>200:reasons.append(ForecastReason(code='line_span_limit',line_id=row['line_id'],recovery='Bill this root’s exact recommended net amount, then inspect the remaining work again.'))
    if span_count>2000:reasons.append(ForecastReason(code='conversion_span_limit',recovery='Select fewer complete source lines; each bounded installment rounds its own tax.'))
    if sum(value['net'] for value in values.values())<=0:reasons.append(ForecastReason(code='no_charge',recovery='No positive charge remains; physical completion is tracked separately.'))
    if not source['active'] or (source['kind']=='estimate' and source['status']!='accepted') or source['status']=='cancelled' or query.current_owner(s,source)['id']!=source['id']:
        reasons.append(ForecastReason(code='source_ineligible',recovery='Use the current billing owner, reactivate the source, or record the required acceptance before billing.'))
    from bookflow.company.billing import posting_eligibility
    from bookflow.core.errors import BookflowError
    try:
        posting_eligibility(s,revision,selected)
    except BookflowError as exc:
        if exc.code not in {'E_VALIDATION','E_INACTIVE_REFERENCE','E_RECORD_NOT_FOUND','E_VALUE_RANGE'}:raise
        reasons.append(ForecastReason(code='posting_ineligible',recovery='Resolve current selling-item, customer, tax or account eligibility before billing; captured rates and this hypothetical tax remain unchanged.'))
    result=tax.calculate(inputs,profile,revision['currency'],list(ordinals.values()))
    by_ordinal={v:k for k,v in ordinals.items()}
    for line in result.calculation.lines:values[by_ordinal[line.tax_ordinal]]['tax']=line.tax_minor_units
    token=dict(source_revision=revision['id'],source_version=source['version'],free=free_facts,
        attribution=result.model_dump(mode='json'),ordinals=ordinals,reasons=[r.model_dump() for r in reasons],
        preferences=work_preferences.financial_projection(s,source['kind']))
    fingerprint=hashlib.sha256(work.json_text(token).encode()).hexdigest()
    return WorkTaxForecast(can_bill_together=not reasons,forecast_eligibility_reasons=reasons,
        forecast_line_ordinals=ordinals,forecast_tax_attribution=result,forecast_fingerprint=fingerprint),values
