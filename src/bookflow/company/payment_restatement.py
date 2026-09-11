"""Invoice-side logical allocation recipe independent of regenerated physical IDs."""
import json

from bookflow.company import schema as c, document_effects as effects
from bookflow.company import payment_queries as query, payment_calculations as calc, journals
from bookflow.company.payment_cancellation import live_allocations
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id


def semantic(value):
    """Compare monetary recognition facts, not physical tax-row provenance."""
    tax = value.get('tax')
    if tax is not None:
        tax = {key: tax[key] for key in ('tax_item_id', 'agency_id', 'liability_account_id',
            'rate_percent_millionths', 'taxable_minor_units', 'tax_minor_units')}
    return dict(account_id=value['account_id'], net_minor_units=value['net_minor_units'], tax=tax)


def prepare(plan, s, ctx):
    data = plan.data
    if data['document_type'] != 'invoice' or data['operation'] != 'update' or not data['changed']:
        return
    header, pending, old = data['header'], data['pending'], data['before']
    keys = effects.rows(s, c.settlement_line_keys, c.settlement_line_keys.c.transaction_id == header['id'])
    applications = query.active_applications(s, invoice=header['id'])
    if not keys and not applications:
        return
    created = lambda: dict(id=new_id(), created_at=header['updated_at'], created_by=s.actor.id, created_via=ctx.interface.value,
                          audit_event_id=data['event'])
    ordinals = {row['line_id']: row['ordinal'] for row in keys}
    maximum = max(ordinals.values(), default=0)
    new_keys = []
    lines = pending['document_lines']
    for line in sorted(lines, key=lambda row: row['position']):
        if line['line_id'] not in ordinals:
            maximum += 1
            ordinals[line['line_id']] = maximum
            new_keys.append(dict(**created(), transaction_id=header['id'], line_id=line['line_id'], ordinal=maximum))
    data['settlement_keys'] = new_keys
    if not applications:
        return
    revision, profile = pending['transaction_revisions'][0], pending['sales_profiles'][0]
    original = query.invoice_facts(s, old['id'], write=True)
    if (profile['customer_id'], profile['control_account_id'], revision['currency']) != (
        original['profile']['customer_id'], original['profile']['control_account_id'], original['revision']['currency']):
        raise BookflowError('E_HAS_APPLICATIONS', details={'invoice_id': old['id'], 'reason': 'settlement_owner'})
    if revision['total_minor_units'] < sum(row['amount_minor_units'] for row in applications):
        raise BookflowError('E_APPLIED_EXCEEDS_TOTAL')
    if revision['date'] > min(row['effective_date'] for row in applications):
        raise BookflowError('E_HAS_APPLICATIONS', details={'invoice_id': old['id'], 'field': 'date'})
    recognition = {row['id']: row for row in pending['posting_lines'] if row['reversed_line_id'] is None}
    sources = [row for row in pending['posting_line_sources'] if row['reversed_source_id'] is None]
    net = {row['document_line_id']: row['net_minor_units'] for row in pending['sales_line_profiles']}
    components = {}
    for line in lines:
        values = [(None, net[line['id']])] + [(tax, tax['tax_minor_units']) for tax in pending['sales_tax_components'] if tax['document_line_id'] == line['id']]
        for tax, units in values:
            if not units:
                continue
            tax_id = tax['id'] if tax else None
            matching = [row for row in sources if row['document_line_id'] == line['id'] and row['tax_component_id'] == tax_id]
            ar = [row for row in matching if recognition[row['posting_line_id']]['debit_minor_units'] > 0]
            credit = [row for row in matching if recognition[row['posting_line_id']]['credit_minor_units'] > 0]
            if len(ar) != 1 or len(credit) != 1:
                raise BookflowError('E_VALIDATION', message='Ambiguous revised invoice attribution.')
            key = calc.ComponentKey(ordinals[line['line_id']], int(tax is not None), tax['tax_item_id'] if tax else '')
            components[key] = dict(capacity=units, line=line, tax=tax, ar=ar[0], recognition=credit[0],
                semantic=semantic(dict(account_id=recognition[credit[0]['posting_line_id']]['account_id'], net_minor_units=net[line['id']], tax=tax)))
    available = {key: value['capacity'] for key, value in components.items()}
    allocations, changed_payments, recipes = [], set(), []
    live_by_application = {}
    for row in live_allocations(s, [app['id'] for app in applications]):
        live_by_application.setdefault(row['application_id'], []).append(row)
    for app in applications:  # query order is original effective date, binary ID
        previous = live_by_application.get(app['id'], [])
        if not previous or sum(row['amount_minor_units'] for row in previous) != app['amount_minor_units']:
            raise BookflowError('E_VALIDATION', message='Current application attribution is incomplete.')
        split = calc.allocate(app['amount_minor_units'], available)
        for key, units in split.items():
            available[key] -= units
        old_recipe = sorted((row['target_ordinal'], int(row['logical_kind'] == 'tax'), row['tax_item_id'] or '',
            row['amount_minor_units'], query.canonical(semantic(json.loads(row['facts_snapshot'])))) for row in previous)
        new_recipe = [(key.ordinal, key.kind, key.tax_item_id, units, query.canonical(components[key]['semantic'])) for key, units in sorted(split.items())]
        recipes.append([app['id'], new_recipe])
        if old_recipe == new_recipe:
            continue
        journals.open_dates(s, [row['effective_date'] for row in previous])
        changed_payments.add(app['paying_transaction_id'])
        for row in previous:
            allocations.append(dict(row, **created(), kind='reversal', reverses_allocation_id=row['id']))
        for key, units in split.items():
            part = components[key]
            tax = part['tax']
            allocations.append(dict(previous[0], **created(), kind='allocation', reverses_allocation_id=None,
                target_revision_id=revision['id'], target_document_line_id=part['line']['id'], target_line_id=part['line']['line_id'],
                target_ordinal=key.ordinal, logical_kind='tax' if key.kind else 'net', tax_item_id=tax['tax_item_id'] if tax else None,
                tax_component_id=tax['id'] if tax else None, target_ar_source_id=part['ar']['id'],
                target_recognition_source_id=part['recognition']['id'], recognition_role='tax_liability' if key.kind else 'sales_net',
                amount_minor_units=units, facts_snapshot=query.canonical(part['semantic'])))
    data.update(settlement_allocations=allocations, settlement_changed_payment_ids=sorted(changed_payments),
                settlement_recipe=recipes, settlement_applications=applications)


def validate(plan, s, ctx):
    """Re-derive complete effective splits without calling the planner allocator."""
    data = plan.data
    def require(ok):
        if not ok:
            raise BookflowError('E_INTERNAL', message='Invalid invoice settlement restatement.')
    if not data['changed'] or not data.get('settlement_applications'):
        require(not data.get('settlement_allocations'))
        return
    header, pending = data['header'], data['pending']
    apps = query.active_applications(s, invoice=header['id'])
    require({row['id'] for row in apps} == {row['id'] for row in data['settlement_applications']})
    keys = effects.rows(s, c.settlement_line_keys, c.settlement_line_keys.c.transaction_id == header['id']) + data.get('settlement_keys', [])
    ordinals = {row['line_id']: row['ordinal'] for row in keys}
    require(len(ordinals) == len(keys) and len({row['ordinal'] for row in keys}) == len(keys))
    amounts = {row['document_line_id']: row['net_minor_units'] for row in pending['sales_line_profiles']}
    remaining = {}
    for line in pending['document_lines']:
        remaining[(ordinals[line['line_id']], 0, '')] = amounts[line['id']]
        for tax in pending['sales_tax_components']:
            if tax['document_line_id'] == line['id']:
                remaining[(ordinals[line['line_id']], 1, tax['tax_item_id'])] = tax['tax_minor_units']
    changes = data.get('settlement_allocations', [])
    reversed_ids = {row['reverses_allocation_id'] for row in changes if row['kind'] == 'reversal'}
    require(len(reversed_ids) == sum(row['kind'] == 'reversal' for row in changes))
    live_by_application = {}
    for row in live_allocations(s, [app['id'] for app in apps]):
        live_by_application.setdefault(row['application_id'], []).append(row)
    for app in apps:
        live = live_by_application.get(app['id'], [])
        replacement = [row for row in changes if row['application_id'] == app['id'] and row['kind'] == 'allocation']
        inverse = [row for row in changes if row['application_id'] == app['id'] and row['kind'] == 'reversal']
        if replacement or inverse:
            require({row['id'] for row in live} == {row['reverses_allocation_id'] for row in inverse})
            ignored = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind', 'reverses_allocation_id'}
            for row in inverse:
                old = next(value for value in live if value['id'] == row['reverses_allocation_id'])
                require({k: v for k, v in row.items() if k not in ignored} == {k: v for k, v in old.items() if k not in ignored})
                journals.open_dates(s, [row['effective_date']])
        effective = [row for row in live if row['id'] not in reversed_ids] + replacement
        denominator = sum(remaining.values())
        require(denominator >= app['amount_minor_units'])
        expected = {key: app['amount_minor_units'] * value // denominator for key, value in remaining.items()}
        residues = sorted(remaining, key=lambda key: (-(app['amount_minor_units'] * remaining[key] % denominator), key))
        for key in residues[:app['amount_minor_units']-sum(expected.values())]:
            expected[key] += 1
        expected = {key: value for key, value in expected.items() if value}
        actual = {(row['target_ordinal'], int(row['logical_kind'] == 'tax'), row['tax_item_id'] or ''): row['amount_minor_units'] for row in effective}
        require(len(actual) == len(effective) and actual == expected)
        for key, value in expected.items():
            remaining[key] -= value
        for row in replacement:
            require(row['effective_date'] == app['effective_date'] and row['currency'] == app['currency'])
            require(row['created_by'] == s.actor.id and row['audit_event_id'] == data['event'])
            original = live[0]
            require(all(row[key] == original[key] for key in ('source_transaction_id', 'source_revision_id', 'source_component_id', 'credit_source_component_id', 'source_posting_source_id')))
    changed_payments = {row['source_transaction_id'] for row in changes}
    require(changed_payments == {old['id'] for old, new in data.get('settlement_headers', [])})
    for old, new in data.get('settlement_headers', []):
        require(new['version'] == old['version'] + 1 and new['current_revision_id'] == old['current_revision_id'])
