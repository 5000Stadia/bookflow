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
    active = query.active_allocations(s, roots)
    if not active:
        return
    from bookflow.company.billing import dependency
    if before['title'] != after['title'] or any(before['facts'][key] != after['facts'][key] for key in work.AGREED_FIELDS):
        dependency('billed work agreement cannot be changed while a sale consumes its roots', source_id=header['id'])
    if header['kind'] == 'estimate' and after['status'] != 'accepted':
        dependency('billed estimate acceptance cannot be revoked', source_id=header['id'])
    occupied = {(row['root_document_id'], row['root_line_id']) for row in active}
    old = {entry['line_id']: entry['facts'] for entry in before['lines']}
    new = {entry['line_id']: entry['facts'] for entry in after['lines']}
    economic = lambda facts: {key: value for key, value in facts.items() if key not in ('completed_quantity_microunits', 'billable')}
    for key, facts in old.items():
        ident = identities[key]
        if (ident['root_document_id'], ident['root_line_id']) in occupied:
            if key not in new or economic(facts) != economic(new[key]):
                dependency('billed source line cannot be removed or changed', source_line_id=key)


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
    profile = json.loads(sales.profile_row(s, old_revision)['profile_snapshot'])
    replacement = resolved['profile'].model_dump()
    financial = {'control_account', 'due_date', 'discount_date', 'discount_available', 'payment_method', 'payment_reference', 'origins'}
    if any(profile.get(key) != replacement.get(key) for key in set(profile) | set(replacement) if key not in financial):
        dependency('retained linked sale lines freeze captured commercial header', source_id=old['id'])
    for line in retained:
        old_line = prior[line['line_id']]
        old_semantic = sales._line_semantic(dict(old_line, profile=SalesLineProfile.model_validate_json(old_line['item_snapshot'])))
        if sales._line_semantic(line) != old_semantic:
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
            data['billing_allocations'].append(dict(allocation, id=new_id(), revision_id=line['revision_id'],
                document_line_id=line['id'], created_at=header['updated_at'], created_by=header['updated_by'],
                created_via=header['updated_via']))
