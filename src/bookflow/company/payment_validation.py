"""Independent accounting and ownership checks before payment persistence."""
from collections import defaultdict
import sqlalchemy as sa

from bookflow.company import schema as c, journals, payment_queries as query
from bookflow.company import journal_custom_fields as custom
from bookflow.core.errors import BookflowError


def validate(plan, s, ctx):
    data, out = plan.data, plan.preview
    pending, header = data['pending'], data['header']

    def require(condition, problem):
        if not condition:
            raise BookflowError('E_INTERNAL', message='Invalid payment aggregate: ' + problem)

    journals.open_dates(s, [data['input'].date])
    for table, rows in pending.items():
        for row in rows:
            require(row['created_by'] == s.actor.id and row['created_via'] == ctx.interface.value,
                    'pending history has a different execution principal')
            if 'audit_event_id' in row:
                require(row['audit_event_id'] == data['event'], 'pending history has an unrelated audit event')
    applications = pending['applications']
    allocations = pending['application_allocations']
    require(len({row['paid_transaction_id'] for row in applications}) == len(applications), 'duplicate invoice')
    by_source, by_target, by_application = defaultdict(int), defaultdict(int), defaultdict(int)
    for row in allocations:
        require(type(row['amount_minor_units']) is int and row['amount_minor_units'] > 0, 'nonpositive allocation')
        require(row['audit_event_id'] == data['event'] and row['currency'] == data['context']['currency'], 'allocation provenance')
        by_application[row['application_id']] += row['amount_minor_units']
        require(row['recognition_role'] == ('sales_net' if row['logical_kind'] == 'net' else 'tax_liability'), 'tax recognition')
    for row in applications:
        require(type(row['amount_minor_units']) is int and row['amount_minor_units'] > 0, 'nonpositive application')
        require(row['paying_transaction_id'] == header['id'] and row['audit_event_id'] == data['event'], 'application owner')
        require(row['kind'] == 'apply' and row['reverses_application_id'] is None, 'unexpected inverse')
        require(by_application[row['id']] == row['amount_minor_units'], 'allocation sum differs from application')
        by_source[row['source_component_key_id']] += row['amount_minor_units']
        by_target[row['paid_transaction_id']] += row['amount_minor_units']
        facts = query.invoice_facts(s, row['paid_transaction_id'], write=True)
        require(row['amount_minor_units'] <= facts['due'], 'invoice overapplied')
        require(facts['revision']['date'] <= row['effective_date'], 'application precedes invoice')
        # Re-derive proportional cents from stored net/tax and live immutable
        # allocations. Do not call the planner's allocator or trust its recipe.
        from bookflow.company import sales, document_effects as effects
        ordinal_rows = effects.rows(s, c.settlement_line_keys,
            c.settlement_line_keys.c.transaction_id == row['paid_transaction_id']) + [
                key for key in pending['settlement_line_keys'] if key['transaction_id'] == row['paid_transaction_id']]
        ordinals = {key['line_id']: key['ordinal'] for key in ordinal_rows}
        capacity = {}
        for line in sales.saved_lines(s, facts['revision']):
            if line['net_minor_units']:
                capacity[(ordinals[line['line_id']], 'net', '')] = line['net_minor_units']
            for tax in effects.rows(s, c.sales_tax_components, c.sales_tax_components.c.document_line_id == line['id']):
                if tax['tax_minor_units']:
                    capacity[(ordinals[line['line_id']], 'tax', tax['tax_item_id'])] = tax['tax_minor_units']
        a, inverse = c.application_allocations, c.application_allocations.alias('inverse')
        live = s.company.conn.execute(sa.select(a).where(a.c.target_transaction_id == row['paid_transaction_id'],
            a.c.kind == 'allocation', ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_allocation_id == a.c.id)))).mappings()
        for old in live:
            key = (old['target_ordinal'], old['logical_kind'], old['tax_item_id'] or '')
            require(key in capacity, 'live allocation has no capacity')
            capacity[key] -= old['amount_minor_units']
        total = sum(capacity.values())
        require(total >= row['amount_minor_units'] and all(value >= 0 for value in capacity.values()), 'invalid remaining capacity')
        quotient, remainders = {}, []
        for key, available in capacity.items():
            q, r = divmod(row['amount_minor_units'] * available, total)
            quotient[key] = q
            remainders.append((-r, key))
        for _, key in sorted(remainders)[:row['amount_minor_units'] - sum(quotient.values())]:
            quotient[key] += 1
        expected_split = {key: value for key, value in quotient.items() if value}
        own_allocations = [item for item in allocations if item['application_id'] == row['id']]
        actual_split = {(item['target_ordinal'], item['logical_kind'], item['tax_item_id'] or ''): item['amount_minor_units'] for item in own_allocations}
        require(len(actual_split) == len(own_allocations) and actual_split == expected_split, 'incorrect logical largest-remainder allocation')
    require(set(by_application) == {row['id'] for row in applications}, 'orphan allocation')
    require({new['id'] for _, new in data['changed_headers']} == set(by_target), 'incorrect touched invoice set')
    for old, new in data['changed_headers']:
        require(new['version'] == old['version'] + 1, 'invoice concurrency increment')
        require(new['current_revision_id'] == old['current_revision_id'] and new['status'] == old['status'], 'settlement changed commercial revision')
        require(all(new[key] == value for key, value in old.items() if key not in ('version', 'updated_at', 'updated_by', 'updated_via')), 'settlement changed invoice facts')
    if data['operation'] == 'apply':
        require(all(not pending[name] for name in ('transaction_revisions', 'document_lines', 'document_line_identities',
            'payment_profiles', 'payment_components', 'payment_component_keys', 'posting_batches', 'posting_lines', 'posting_line_sources')), 'apply wrote financial facts')
        funding = query.payment_facts(s, header['id'], write=True)
        for key, units in by_source.items():
            require(units <= funding['available'].get(key, 0), 'old credit overapplied')
        return
    require(len(pending['transaction_revisions']) == len(pending['document_lines']) == len(pending['posting_batches']) == 1, 'receipt shape')
    revision = pending['transaction_revisions'][0]
    amount = revision['total_minor_units']
    require(type(amount) is int and amount > 0, 'receipt total')
    keys = {row['id']: row for row in pending['payment_component_keys']}
    capacities = {row['component_key_id']: row for row in pending['payment_components']}
    require(set(keys) == set(capacities), 'component key ownership')
    expected = defaultdict(int)
    for row in applications:
        facts = query.invoice_facts(s, row['paid_transaction_id'], write=True)
        expected[facts['profile']['customer_id']] += row['amount_minor_units']
        require(keys[row['source_component_key_id']]['party_id'] == facts['profile']['customer_id'], 'cross-party application')
    residual = amount - sum(expected.values())
    require(residual >= 0, 'cash exceeded')
    expected[data['context']['customer_id']] += residual
    expected = {party: units for party, units in expected.items() if units}
    actual = {keys[key]['party_id']: row['amount_minor_units'] for key, row in capacities.items()}
    require(actual == expected and len(actual) == len(keys), 'cash source ownership not independently derived')
    legs, sources = pending['posting_lines'], pending['posting_line_sources']
    require(sum(row['debit_minor_units'] for row in legs) == sum(row['credit_minor_units'] for row in legs) == amount, 'unbalanced cash receipt')
    require(len([row for row in legs if row['debit_minor_units'] > 0]) == 1, 'receipt needs one cash debit')
    profile = pending['payment_profiles'][0]
    for leg in legs:
        require((leg['debit_minor_units'] > 0) != (leg['credit_minor_units'] > 0), 'zero or two-sided leg')
        own = [source for source in sources if source['posting_line_id'] == leg['id']]
        require(sum(row['amount_minor_units'] for row in own) == leg['debit_minor_units'] + leg['credit_minor_units'], 'posting attribution sum')
        if leg['debit_minor_units']:
            require(leg['account_id'] == profile['deposit_account_id'], 'cash destination differs')
        else:
            require(len(own) == 1 and leg['account_id'] == profile['ar_account_id'], 'AR component leg differs')
            component = next(row for row in capacities.values() if row['id'] == own[0]['payment_component_id'])
            require(leg['name_type'] == 'customer' and leg['name_id'] == keys[component['component_key_id']]['party_id'], 'AR posting party differs')
    for component in capacities.values():
        require(type(component['amount_minor_units']) is int and component['amount_minor_units'] > 0, 'zero component')
        own = [row for row in sources if row['payment_component_id'] == component['id']]
        require(len(own) == 2 and all(row['amount_minor_units'] == component['amount_minor_units'] and row['tax_component_id'] is None for row in own), 'component must attribute both cash and AR exactly')
    custom.validate(s.company, data['custom_plan'], header['id'], data['custom_plan'].snapshot, record_type='payment')
