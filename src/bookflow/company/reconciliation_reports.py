"""Three distinct pure projections over validated historical/current facts."""
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_preparation import read_admission,account_population,statement_amount,totals,require,bounded

def project(s,certificate_id, *, authority_transactions):
    read_admission(s,authority_transactions)
    certificates=s.by('certificates');cert=certificates.get(certificate_id)
    require(cert is not None,'E_RECORD_NOT_FOUND')
    members=[v for v in s.rows['certificate_members'] if v['certificate_id']==certificate_id]
    selected=[s.versions[v['version_id']] for v in members if v['classification']=='selected']
    historical=totals(cert['beginning_balance'],cert['ending_balance'],selected)
    require(historical.difference==0 and historical.selected_sum==cert['selected_sum'],'E_RECONCILIATION_SOURCE_INVALID')
    account_population(s,cert['account_id'],cert['statement_date'])
    def mapped(keys):
        return [v for k,v in s.current.items() if k in keys and v['active'] and v['account_id']==cert['account_id'] and v['effective_date']<=cert['statement_date']]
    local=bounded(sum(statement_amount(v) for v in mapped({v['key_id'] for v in selected}))-cert['selected_sum'])
    covered={v['key_id'] for v in s.rows['opening_members'] if v['opening_id']==cert['opening_id'] and v['classification']=='covered'}
    cursor=cert;seen=set()
    while cursor is not None:
        require(cursor['id'] not in seen,'E_RECONCILIATION_SOURCE_INVALID');seen.add(cursor['id'])
        covered.update(v['key_id'] for v in s.rows['certificate_members'] if v['certificate_id']==cursor['id'] and v['classification']=='selected')
        cursor=certificates.get(cursor['previous_certificate_id']) if cursor['previous_certificate_id'] else None
    reconstruction=bounded(sum(statement_amount(v) for v in mapped(covered)))
    difference=bounded(cert['ending_balance']-reconstruction)
    return m.Report(certificate_id=certificate_id,account_id=cert['account_id'],currency=cert['currency'],convention=cert['convention'],cutoff=cert['statement_date'],
        as_certified=historical,local_replacement_impact=local,cumulative_reconstruction=reconstruction,cumulative_difference=difference,
        decimal_units=dict(local_replacement_impact=str(local),cumulative_reconstruction=str(reconstruction),cumulative_difference=str(difference)),
        captured_member_ids=tuple(v['version_id'] for v in sorted(members,key=lambda v:v['ordinal'])),
        current_outstanding_ids=tuple(v['id'] for k,v in sorted(s.current.items()) if v['active'] and v['account_id']==cert['account_id'] and k not in covered))
