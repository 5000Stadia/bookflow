"""Source economics stay fixed while an active sale consumes their work roots."""
import json
from bookflow.company import schema as c, work, sales, billing_queries as query
from bookflow.company.sales_facts import SalesLineProfile
from bookflow.core.ids import new_id
from bookflow.hub.access import require_resource


def protect_work(s, header, before, after):
    if header is None:
        return
    identities = query.root_identities(s, header)
    roots = [(row['root_document_id'], row['root_line_id']) for row in identities.values()]
    from bookflow.company.billing_allocations import occupied_roots, has_active_for_document
    occupied = occupied_roots(s, roots)
    if not has_active_for_document(s,header['id']):
        return
    from bookflow.company.billing import dependency
    from bookflow.company import work_tax
    from copy import deepcopy
    before,after=deepcopy(before),deepcopy(after)
    for value in (before,after):value['facts']['profile']=work_tax.normalized(value)['facts']['profile']
    if before['title'] != after['title'] or any(before['facts'][key] != after['facts'][key] for key in work.AGREED_FIELDS):
        dependency('billed work agreement cannot be changed while a sale consumes its roots', source_id=header['id'])
    if header['kind'] == 'estimate' and after['status'] != 'accepted':
        dependency('billed estimate acceptance cannot be revoked', source_id=header['id'])
    old = {entry['line_id']: entry['facts'] for entry in before['lines']}
    new = {entry['line_id']: entry['facts'] for entry in after['lines']}
    economic = work_tax.economics
    for key, facts in old.items():
        ident = identities[key]
        if (ident['root_document_id'], ident['root_line_id']) in occupied:
            if key not in new or economic(facts) != economic(new[key]):
                dependency('billed source line cannot be removed or changed', source_line_id=key)


def retained_allocated_line(s, inp, previous, *, refresh=False):
    """Reconstruct a retained line from its immutable source proof, not defaults."""
    from bookflow.company import billing, billing_allocations as alloc, sales_defaults as defaults
    if refresh or inp.model_fields_set - {'line_id', 'item'}:
        billing.dependency('retained allocated lines accept only line_id and matching item; remove the line to release it',
                           source_line_id=previous['line_id'])
    item = defaults._row(s.company, 'item', inp.item, active=False)
    if item['id'] != previous['item_id']:
        billing.dependency('retained allocated item cannot change', source_line_id=previous['line_id'])
    rows = work.rows(s, c.work_billing_allocations,
        c.work_billing_allocations.c.document_line_id == previous['id'])
    if len(rows) != 1:
        raise sales.BookflowError('E_INTERNAL', message='Allocated sale line has no unique stored source proof')
    proof = alloc.read_proof(rows[0])
    facts = SalesLineProfile.model_validate_json(previous['item_snapshot'])
    if proof is None or proof != facts.allocation_proof:
        raise sales.BookflowError('E_INTERNAL', message='Allocated sale and stored source proofs disagree')
    warnings = [] if item['active'] else ['Retaining the captured inactive item on an existing allocated line.']
    return billing.resolved_line(alloc.captured_line(rows[0]), proof), warnings


def protect_sale(s, inp, old, old_revision, resolved):
    if not old:
        return
    allocations = query.revision_allocations(s, old_revision['id'])
    if not allocations:
        return
    require_resource(s, 'customer-work', 'standard')
    from bookflow.company.billing import dependency
    lines = sales.saved_lines(s, old_revision)
    linked = {row['document_line_id'] for row in allocations}
    prior = {row['line_id']: row for row in lines if row['id'] in linked}
    retained = [line for line in resolved['lines'] if line['line_id'] in prior]
    if not retained:
        return
    from bookflow.company.sales_facts import SalesProfile
    from bookflow.company.tax_attribution import semantic_profile
    profile = semantic_profile(SalesProfile.model_validate_json(sales.profile_row(s, old_revision)['profile_snapshot']))
    replacement = semantic_profile(resolved['profile'])
    financial = {'control_account', 'due_date', 'discount_date', 'discount_available', 'payment_method', 'payment_reference', 'origins'}
    if any(profile.get(key) != replacement.get(key) for key in set(profile) | set(replacement) if key not in financial):
        dependency('retained linked sale lines freeze captured commercial header', source_id=old['id'])
    for line in retained:
        old_line = prior[line['line_id']]
        old_semantic = sales._line_semantic(dict(old_line, profile=SalesLineProfile.model_validate_json(old_line['item_snapshot'])))
        new_semantic=sales._line_semantic(line)
        # Only cell cents and derived tax/gross may move on retained economics.
        def economics(value):
            from copy import deepcopy
            value=deepcopy(value)
            value.pop('tax_minor_units',None);value.pop('gross_minor_units',None)
            for cell in value.get('taxes',[]):cell.pop('tax_minor_units',None)
            return value
        if economics(new_semantic) != economics(old_semantic):
            dependency('remove the whole linked line to release it; its quoted facts cannot be rewritten',
                source_line_id=line['line_id'])


def carry_allocations(plan, s):
    data = plan.data
    if not data['changed'] or not data['before'] or data.get('billing_source'):
        return
    old_revision = data['old_revision']
    allocations = query.revision_allocations(s, old_revision['id'])
    if not allocations:
        return
    require_resource(s, 'customer-work', 'standard')
    data['billing_allocations'] = []
    if data['operation'] == 'void':
        return
    old_lines = {line['id']: line for line in sales.saved_lines(s, old_revision)}
    incoming = {line['line_id']: line for line in data['pending']['document_lines']}
    header = data['header']
    for allocation in allocations:
        line = incoming.get(old_lines[allocation['document_line_id']]['line_id'])
        if line:
            profile=next(row for row in data['pending']['sales_line_profiles'] if row['document_line_id']==line['id'])
            data['billing_allocations'].append(dict(allocation, id=new_id(), revision_id=line['revision_id'],
                tax_minor_units=profile['tax_minor_units'],gross_minor_units=profile['gross_minor_units'],
                document_line_id=line['id'], created_at=header['updated_at'], created_by=header['updated_by'],
                created_via=header['updated_via']))
