"""Retain credit uses while replacing their immutable revision-local attribution."""
from collections import defaultdict

from bookflow.company import schema as c, credits, journals, document_effects as effects
from bookflow.company.payment_cancellation import live_allocations
from bookflow.company.payment_authority import authorize
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Touched


def dependencies(s, previous):
    apps, refunds = previous['applications'], previous['consumptions']
    identifiers = sorted({row['paid_transaction_id'] for row in apps}
                         | {row['transaction_id'] for row in refunds})
    authorize(s, identifiers, write=True)
    return dict(applications=apps, consumptions=refunds,
                allocations=live_allocations(s, [row['id'] for row in apps]),
                headers=effects.rows(s, c.transactions, c.transactions.c.id.in_(identifiers),
                                     order=c.transactions.c.id) if identifiers else [])


def compatible(s, ctx, previous, resolved):
    uses = previous['applications'] + previous['consumptions']
    if not uses:
        return
    if not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140:
        raise BookflowError('E_REASON_REQUIRED')
    key, profile = previous['key'], resolved['profile']
    if (profile.customer.id, profile.control_account.id, resolved['currency']) != (
            key['party_id'], key['ar_account_id'], key['currency']):
        raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
            'reason': 'credit_source_ownership', 'credit_memo_id': previous['header']['id'],
            'next': 'Keep the customer, receivable account and currency of the used credit.'})
    used = previous['applied'] + previous['refunded']
    if resolved['total'] < used:
        raise BookflowError('E_APPLIED_EXCEEDS_TOTAL', details={
            'reason': 'credit_combined_use', 'minimum_minor_units': used,
            'applied_minor_units': previous['applied'], 'refunded_minor_units': previous['refunded']})
    earliest = min(row['effective_date'] for row in uses)
    if resolved['date'] > earliest:
        raise BookflowError('E_HAS_APPLICATIONS', details={
            'reason': 'credit_date_after_use', 'field': 'date', 'earliest_use_date': earliest})
    journals.open_dates(s, [row['effective_date'] for row in uses])


def prepare(s, previous, pending, created, event):
    from bookflow.company.credit_settlement import _draw
    graph = dependencies(s, previous)
    components = pending['credit_components']  # commercial line order, independent of fresh IDs
    remaining = {row['id']: row['amount_minor_units'] for row in components}
    audited = lambda: dict(**created(), audit_event_id=event)
    app_map, refund_map = {}, {}
    uses = [(row['effective_date'], row['id'], 'application', row) for row in graph['applications']]
    uses += [(row['effective_date'], row['id'], 'refund', row) for row in graph['consumptions']]
    for _, _, kind, old in sorted(uses):
        shares = _draw(remaining, components, old['amount_minor_units'])
        if kind == 'refund':
            pending['customer_refund_consumptions'].append(dict(
                old, **audited(), kind='release', reverses_consumption_id=old['id']))
            replacements = [dict(old, **audited(), credit_source_component_id=component['id'],
                                 amount_minor_units=amount) for component, amount in shares]
            pending['customer_refund_consumptions'].extend(replacements)
            refund_map[old['id']] = [row['id'] for row in replacements]
            continue
        allocations = [row for row in graph['allocations'] if row['application_id'] == old['id']]
        if sum(row['amount_minor_units'] for row in allocations) != old['amount_minor_units']:
            raise BookflowError('E_INTERNAL', message='Incomplete credit application attribution.')
        for row in allocations:
            pending['application_allocations'].append(dict(
                row, **audited(), kind='reversal', reverses_allocation_id=row['id']))
        if len(shares) > 1:
            pending['applications'].append(dict(
                old, **audited(), kind='unapply', reverses_application_id=old['id']))
        target_remaining = {row['id']: row['amount_minor_units'] for row in allocations}
        app_map[old['id']] = []
        for component, amount in shares:
            app = old if len(shares) == 1 else dict(old, **audited(), amount_minor_units=amount)
            if app is not old:
                pending['applications'].append(app)
            app_map[old['id']].append(app['id'])
            wanted = amount
            for row in allocations:
                share = min(wanted, target_remaining[row['id']])
                if not share:
                    continue
                pending['application_allocations'].append(dict(
                    row, **audited(), application_id=app['id'], source_revision_id=component['revision_id'],
                    credit_source_component_id=component['id'], source_posting_source_id=component['posting_source_id'],
                    amount_minor_units=share))
                target_remaining[row['id']] -= share
                wanted -= share
    stamp = created()
    changed = [(old, dict(old, version=old['version'] + 1, updated_at=stamp['created_at'],
                         updated_by=stamp['created_by'], updated_via=stamp['created_via']))
               for old in graph['headers']]
    if any(after['version'] > 9223372036854775807 for _, after in changed):
        raise BookflowError('E_VALUE_RANGE')
    return dict(graph=graph, app_map=app_map, refund_map=refund_map, changed_headers=changed)


class Companion:
    def __init__(self, data):
        self.headers = data.get('changed_headers', [])
        self.touches = [Touched('transaction', after['id'], 'update', before['version'], after['version'],
                                after, before, db='company') for before, after in self.headers]

    def write(self, s):
        for _, after in self.headers:
            s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == after['id']).values(**after))


def validate(s, data):
    """Prove exact cancellation, conserved use/captures and combined component capacity."""
    from bookflow.company.credit_validation import _require as require
    pending = data['pending']
    previous = credits.facts(s, data['header']['id'], write=True)
    graph = dependencies(s, previous)
    require(graph == data['restatement']['graph'], 'current credit use graph')
    provenance = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind',
                  'reverses_application_id', 'reverses_allocation_id', 'reverses_consumption_id'}

    def inverse(table, old_rows, kind, link):
        rows = [row for row in pending[table] if row['kind'] == kind]
        originals = {row['id']: row for row in old_rows}
        require(len(rows) == len(originals) and {row[link] for row in rows} == set(originals),
                'exact use cancellation coverage')
        for row in rows:
            require({k: v for k, v in row.items() if k not in provenance}
                    == {k: v for k, v in originals[row[link]].items() if k not in provenance},
                    'exact use cancellation facts')

    inverse('application_allocations', graph['allocations'], 'reversal', 'reverses_allocation_id')
    inverse('customer_refund_consumptions', graph['consumptions'], 'release', 'reverses_consumption_id')
    mappings = data['restatement']
    split = [row for row in graph['applications'] if mappings['app_map'].get(row['id']) != [row['id']]]
    inverse('applications', split, 'unapply', 'reverses_application_id')
    apps = {row['id']: row for row in graph['applications'] + pending['applications'] if row['kind'] == 'apply'}
    allocs = [row for row in pending['application_allocations'] if row['kind'] == 'allocation']
    replacements = [row for row in pending['customer_refund_consumptions'] if row['kind'] == 'consume']
    components = {row['id']: row for row in pending['credit_components']}
    used = defaultdict(int)
    target_fields = [name for name in c.application_allocations.c.keys()
                     if name not in provenance | {'application_id', 'source_revision_id',
                         'source_component_id', 'credit_source_component_id', 'source_posting_source_id',
                         'amount_minor_units'}]

    def totals(rows):
        result = defaultdict(int)
        for row in rows:
            result[tuple(row[name] for name in target_fields)] += row['amount_minor_units']
        return dict(result)

    mapped_ids = []
    for old in graph['applications']:
        ids = mappings['app_map'][old['id']]
        mapped_ids.extend(ids)
        require(bool(ids) and all(identifier in apps for identifier in ids), 'replacement application ownership')
        require(sum(apps[identifier]['amount_minor_units'] for identifier in ids) == old['amount_minor_units'],
                'retained application amount')
        for identifier in ids:
            app = apps[identifier]
            require({k: v for k, v in app.items() if k not in provenance | {'amount_minor_units'}}
                    == {k: v for k, v in old.items() if k not in provenance | {'amount_minor_units'}},
                    'retained application dimensions and date')
            rows = [row for row in allocs if row['application_id'] == identifier]
            require(sum(row['amount_minor_units'] for row in rows) == app['amount_minor_units']
                    and len({row['credit_source_component_id'] for row in rows}) == 1,
                    'one source component per application')
            keys = [(row['target_ordinal'], row['logical_kind'], row['tax_item_id']) for row in rows]
            require(len(keys) == len(set(keys)), 'one allocation per target component')
        require(totals([row for row in allocs if row['application_id'] in ids])
                == totals([row for row in graph['allocations'] if row['application_id'] == old['id']]),
                'retained target attribution and captures')
    require(len(mapped_ids) == len(set(mapped_ids))
            and {row['application_id'] for row in allocs} == set(mapped_ids)
            and {row['id'] for row in pending['applications'] if row['kind'] == 'apply'}
                == set(mapped_ids) - {row['id'] for row in graph['applications']}, 'complete application restatement')
    mapped_refunds = []
    for old in graph['consumptions']:
        ids = mappings['refund_map'][old['id']]
        mapped_refunds.extend(ids)
        rows = [row for row in replacements if row['id'] in ids]
        require(sum(row['amount_minor_units'] for row in rows) == old['amount_minor_units'], 'retained refund amount')
        ignored = provenance | {'credit_source_component_id', 'amount_minor_units'}
        require(all({k: v for k, v in row.items() if k not in ignored}
                    == {k: v for k, v in old.items() if k not in ignored} for row in rows),
                'retained refund ownership and date')
    require(len(mapped_refunds) == len(set(mapped_refunds))
            and set(mapped_refunds) == {row['id'] for row in replacements}, 'complete refund restatement')
    for row in allocs + replacements:
        part = components.get(row['credit_source_component_id'])
        require(part is not None and row['amount_minor_units'] > 0 and row['currency'] == part['currency'],
                'current replacement capacity')
        require(part['key_id'] == previous['key']['id'], 'used credit ownership')
        if 'source_revision_id' in row:
            require(row['source_revision_id'] == part['revision_id'] and row['source_component_id'] is None
                    and row['source_posting_source_id'] == part['posting_source_id'], 'replacement credit attribution')
        used[part['id']] += row['amount_minor_units']
    require(all(units <= components[identifier]['amount_minor_units'] for identifier, units in used.items()),
            'combined credit use fits each component')
    expected_headers = [(old, dict(old, version=old['version'] + 1,
                         updated_at=data['header']['updated_at'], updated_by=data['header']['updated_by'],
                         updated_via=data['header']['updated_via'])) for old in graph['headers']]
    require(data['restatement']['changed_headers'] == expected_headers, 'related document versions')
