"""Private five-producer statement adapters over one caller-owned snapshot.

No writer, certificate resolver, authentication admission or cross-request cache.
"""
from dataclasses import dataclass
import json
from types import MappingProxyType
import sqlalchemy as sa
from bookflow.company import schema as c, payment_authority
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan
from bookflow.hub import access
from bookflow.company.reconciliation_models import (
    StatementEffectRef, StatementEffectVersion, MovementKey, CompletePopulation,
    UnsupportedPopulation, InvalidPopulation, StaleReference, ChangedEffects,
)

TABLES = ('transactions', 'transaction_revisions', 'document_line_identities', 'document_lines',
          'posting_batches', 'posting_lines', 'posting_line_sources', 'sales_profiles',
          'sales_line_profiles', 'sales_tax_components', 'payment_profiles', 'payment_components',
          'work_billing_allocations', 'deposit_profiles', 'deposit_components', 'deposit_row_keys',
          'bank_effect_keys', 'bank_effect_versions', 'bank_effect_current')


class Unsupported(Exception):
    pass


class Corrupt(Exception):
    pass


def require(ok, reason):
    if not ok:
        raise Corrupt(reason)


def canonical(row):
    return json.dumps(dict(row), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


@dataclass
class Graph:
    rows: dict
    accounts: dict

    def owned(self, table, transaction):
        return [r for r in self.rows[table] if r.get('transaction_id', r.get('id')) == transaction]

    def by_id(self, table):
        return {r['id']: r for r in self.rows[table]}


def graph(s, identifiers, *, pending=None, headers=()):
    ids = sorted(set(identifiers))
    rows = {name: [] for name in TABLES}
    for name in TABLES:
        table = getattr(c, name)
        if name == 'bank_effect_current':
            rows[name] = [dict(r) for r in s.company.conn.execute(sa.select(table).join(c.bank_effect_keys,c.bank_effect_keys.c.id==table.c.key_id).where(c.bank_effect_keys.c.transaction_id.in_(ids))).mappings()]
            continue
        field = table.c.id if name == 'transactions' else table.c.transaction_id
        for offset in range(0, len(ids), 200):
            rows[name].extend(dict(r) for r in s.company.conn.execute(sa.select(table).where(field.in_(ids[offset:offset+200]))).mappings())
    if pending:
        for name in TABLES:
            incoming = [{col.name:r.get(col.name) for col in getattr(c,name).columns} for r in pending.get(name, ())]
            if name == 'bank_effect_current':
                incoming_keys={r['key_id'] for r in incoming}
                rows[name]=[r for r in rows[name] if r['key_id'] not in incoming_keys]
            rows[name].extend(incoming)
    replaced = {h['id']: dict(h) for h in headers}
    rows['transactions'] = [replaced.pop(r['id'], r) for r in rows['transactions']] + list(replaced.values())
    accounts = {r['id']: dict(r) for r in s.company.conn.execute(sa.select(c.accounts)).mappings()}
    return Graph(rows, accounts)


def authority(s, identifiers):
    """Whole historical closure, delegated to the existing permission owners."""
    access.require_resource(s, 'ledger.read', 'member')
    ids = set(identifiers)
    # Selections include every historical attempt and consumed operation through
    # the accepted owner, rather than only present applications.
    edges = []
    for row in s.company.conn.execute(sa.select(c.payment_operations.c.id)).mappings():
        edges.append(payment_authority.record_transactions(s.company, 'payment_operation', row['id']))
    for row in s.company.conn.execute(sa.select(c.payment_selections.c.id)).mappings():
        edges.append(payment_authority.record_transactions(s.company, 'payment_selection', row['id']))
    for row in s.company.conn.execute(sa.select(c.deposit_operations.c.id)).mappings():
        edges.append(payment_authority.record_transactions(s.company, 'deposit_operation', row['id']))
    edges.extend({r[0], r[1]} for r in s.company.conn.execute(sa.select(c.applications.c.paying_transaction_id, c.applications.c.paid_transaction_id)))
    edges.extend({r[0], r[1]} for r in s.company.conn.execute(sa.select(c.deposit_memberships.c.transaction_id, c.deposit_memberships.c.source_transaction_id)))
    while True:
        following = ids | set().union(*(edge for edge in edges if edge & ids))
        if following == ids:
            break
        ids = following
    existing = set()
    ordered = sorted(ids)
    for offset in range(0,len(ordered),200):
        existing.update(s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id.in_(ordered[offset:offset+200]))).scalars())
    if existing != ids:
        raise BookflowError('E_PERMISSION')
    try:
        payment_authority.authorize(s, sorted(ids))
    except BookflowError as exc:
        if exc.code in ('E_PERMISSION', 'E_RECORD_NOT_FOUND'):
            raise BookflowError('E_PERMISSION') from None
        raise
    return tuple(sorted(ids))


def _version(g, header, revision, batch, role, component, legs, *, prior=None,
             version_id=None, event=None, transition=None, date=None, active=True):
    sources = [r for r in g.owned('posting_line_sources', header['id']) if r['posting_line_id'] in {l['id'] for l in legs}]
    if active:
        require(bool(legs), 'missing_effect_legs')
        account_ids = {l['account_id'] for l in legs}
        require(len(account_ids) == 1, 'mixed_account_effect')
        account_id = next(iter(account_ids))
        signed = sum(l['debit_minor_units'] - l['credit_minor_units'] for l in legs)
        require(signed != 0, 'netted_opposite_legs')
    else:
        require(prior is not None, 'inactive_without_prior')
        account_id, signed = prior.account_id, 0
    account = g.accounts[account_id]
    ref = StatementEffectRef(producer=header['type'], transaction_id=header['id'], component_id=component, role=role)
    grouped = role in ('cash', 'control', 'net')
    movement = MovementKey(producer=header['type'], transaction_id=header['id'], revision_id=revision['id'],
        account_id=account_id, role=role, component_id=None if grouped else component)
    physical = {r['document_line_id'] for r in sources}
    provenance = [canonical(revision), canonical(batch), *(canonical(r) for r in legs), *(canonical(r) for r in sources)]
    if transition:
        provenance.append(canonical(g.by_id('posting_batches')[transition]))
    for name in ('document_lines', 'sales_profiles', 'sales_line_profiles', 'payment_profiles', 'payment_components', 'work_billing_allocations', 'deposit_profiles'):
        provenance.extend(canonical(r) for r in g.owned(name, header['id']) if r.get('revision_id') == revision['id'])
    return StatementEffectVersion(ref=ref, version_id=version_id or (transition or batch['id']) + ':' + role + ':' + component,
        revision_id=revision['id'], business_batch_id=batch['id'], transition_batch_id=transition,
        audit_event_id=event or batch['audit_event_id'], movement_key=movement,
        account_id=account_id, account_type=account['type'], currency=revision['currency'],
        effective_date=date or batch['effective_date'], signed_debit=signed, active=active,
        number=revision['number'], memo=revision['memo'], payees=tuple(sorted({r['party_name'] for r in legs if r.get('party_name')})),
        posting_line_ids=tuple(sorted(r['id'] for r in legs)), source_ids=tuple(sorted(r['id'] for r in sources)),
        document_line_ids=tuple(sorted(physical)), provenance=tuple(provenance))


def _commercial(g, header):
    identity = header['id']
    revisions = {r['id']: r for r in g.owned('transaction_revisions', identity)}
    lines = g.by_id('document_lines')
    identities = g.by_id('document_line_identities')
    batches = sorted((r for r in g.owned('posting_batches', identity) if r['kind'] != 'reversal'), key=lambda r: revisions[r['revision_id']]['revision_number'])
    history, current = [], {}
    for batch in batches:
        revision = revisions[batch['revision_id']]
        found = {}
        for leg in g.owned('posting_lines', identity):
            if leg['batch_id'] != batch['id'] or g.accounts[leg['account_id']]['type'] not in ('bank', 'credit_card'):
                continue
            sources = [r for r in g.owned('posting_line_sources', identity) if r['posting_line_id'] == leg['id']]
            physical = {r['document_line_id'] for r in sources}
            require(len(physical) == 1, 'ambiguous_commercial_line')
            line = lines[next(iter(physical))]
            require(line['transaction_id'] == identity and line['revision_id'] == revision['id'], 'line_owner')
            require(identities[line['line_id']]['transaction_id'] == identity, 'stable_line_owner')
            producer = header['type']
            if producer == 'journal_entry':
                role = 'entered'
            elif producer == 'payment':
                profile = next(r for r in g.owned('payment_profiles', identity) if r['revision_id'] == revision['id'])
                if leg['account_id'] != profile['deposit_account_id'] or not leg['debit_minor_units']:
                    raise Unsupported('payment_non_cash_bank_leg')
                role = 'cash'
            else:
                profile = next(r for r in g.owned('sales_profiles', identity) if r['revision_id'] == revision['id'])
                saved = next(r for r in g.owned('sales_line_profiles', identity) if r['document_line_id'] == line['id'])
                income = json.loads(saved['item_snapshot'])['income_account']['id']
                if producer == 'sales_receipt' and leg['account_id'] == profile['control_account_id'] and leg['debit_minor_units']:
                    role = 'control'
                elif leg['account_id'] == income and leg['credit_minor_units'] and all(r.get('tax_component_id') is None for r in sources):
                    role = 'net'
                else:
                    raise Unsupported('unsupported_sales_bank_role')
            key = (role, line['line_id'])
            require(key not in found, 'duplicate_component')
            found[key] = _version(g, header, revision, batch, role, line['line_id'], [leg])
        for key, old in current.items():
            if key not in found:
                found[key] = _version(g, header, revision, batch, *key, [], prior=old, active=False)
        history.extend(found.values())
        current = found
    require(not batches or batches[-1]['revision_id'] == header['current_revision_id'], 'current_revision_anchor')
    if header['status'] == 'voided':
        inverse = g.by_id('posting_batches')[header['void_posting_batch_id']]
        require(inverse['reverses_batch_id'] == batches[-1]['id'], 'void_anchor')
        current = {key: _version(g, header, revisions[batches[-1]['revision_id']], batches[-1], *key, [],
            prior=old, active=False, transition=inverse['id'], event=inverse['audit_event_id']) for key, old in current.items()}
        history.extend(current.values())
    return tuple(history), tuple(current.values())


def _deposit(g, header):
    from bookflow.company.deposit_models import Effect
    from bookflow.company.deposit_validation import validate
    identity = header['id']
    keys = {r['id']: r for r in g.owned('bank_effect_keys', identity)}
    revisions = g.by_id('transaction_revisions'); batches = g.by_id('posting_batches')
    profiles = {r['revision_id']: r for r in g.owned('deposit_profiles', identity)}
    history, current = [], {}
    for row in sorted(g.owned('bank_effect_versions', identity), key=lambda r: (r['version'], r['id'])):
        key = keys[row['key_id']]; batch = batches[row['batch_id']]; revision = revisions[row['revision_id']]
        require(batch['kind'] in ('original', 'replacement') and batch['revision_id'] == revision['id'], 'deposit_business_anchor')
        financial = Effect.model_validate_json(profiles[revision['id']]['facts_snapshot']); validate(financial)
        legs = []
        if row['active']:
            posted = sorted((r for r in g.owned('posting_lines', identity) if r['batch_id'] == batch['id']), key=lambda r: r['line_no'])
            require(len(posted) == len(financial.legs), 'deposit_leg_count')
            for actual, planned in zip(posted, financial.legs):
                require((actual['account_id'], actual['debit_minor_units']-actual['credit_minor_units']) == (planned.account_id, planned.signed_debit), 'deposit_leg_mapping')
                if planned.key.startswith('main_bank/'):
                    role, component = 'main_bank', key['row_id']
                elif planned.key.startswith('cash_back/'):
                    role, component = 'cash_back', key['row_id']
                elif planned.key.startswith('additional:'):
                    role, component = 'additional', planned.key.split('/')[0].split(':')[1]
                else:
                    continue
                if (role, component) == (key['role'], key['row_id']):
                    legs.append(actual)
        transition = next((b['id'] for b in g.owned('posting_batches', identity) if b['kind']=='reversal' and b['audit_event_id']==row['audit_event_id']), None)
        value = _version(g, header, revision, batch, key['role'], key['id'], legs, prior=current.get(key['id']),
            active=bool(row['active']), version_id=row['id'], event=row['audit_event_id'], transition=transition, date=row['effective_date'])
        require((value.account_id,value.signed_debit,value.number,value.memo) == (row['account_id'],row['signed_debit'],row['number'],row['memo']), 'deposit_version_facts')
        require(value.statement_amount == row['statement_amount'], 'deposit_statement_units')
        history.append(value); current[key['id']] = value
    pointers={r['key_id']:r['version_id'] for r in g.rows['bank_effect_current'] if r['key_id'] in keys}
    require(set(pointers)==set(keys) and pointers=={key:v.version_id for key,v in current.items()}, 'deposit_current_pointer')
    return tuple(history), tuple(current.values())


# Closed trusted dispatch. Register and billing delegate to their actual journal/sale.
REGISTRY = MappingProxyType({'journal_entry': _commercial, 'payment': _commercial,
    'sales_receipt': _commercial, 'invoice': _commercial, 'deposit': _deposit})


def enumerate_graph(g):
    history, current = [], []
    for header in sorted(g.rows['transactions'], key=lambda r: r['id']):
        adapter = REGISTRY.get(header['type'])
        if adapter is None:
            raise Unsupported('unknown_producer')
        old, now = adapter(g, header)
        history.extend(old); current.extend(now)
    return tuple(history), tuple(current)


def population(s, account_id, cutoff):
    """Authorize the entire required graph before currency or population results."""
    from datetime import date
    date.fromisoformat(cutoff)
    access.require_resource(s, 'ledger.read', 'member')
    account = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == account_id)).mappings().first()
    if account is None:
        raise BookflowError('E_RECORD_NOT_FOUND')
    ids = set(s.company.conn.execute(sa.select(c.posting_lines.c.transaction_id).where(c.posting_lines.c.account_id == account_id)).scalars())
    ids.update(s.company.conn.execute(sa.select(c.bank_effect_versions.c.transaction_id).where(c.bank_effect_versions.c.account_id == account_id)).scalars())
    authorized = authority(s, ids)
    home = s.company_info_row['home_currency']
    if account['currency'] != home:
        return UnsupportedPopulation(kind='account_currency_unsupported',account_id=account_id,account_currency=account['currency'],home_currency=home,reason='foreign_statement_units_not_activated')
    if account['type'] not in ('bank','credit_card'):
        return UnsupportedPopulation(kind='population_unsupported',account_id=account_id,account_currency=account['currency'],home_currency=home,reason='account_not_bank_or_card')
    try:
        g = graph(s, ids)
        history, current = enumerate_graph(g)
        from bookflow.company.reconciliation_proof import prove
        total, gl = prove(g, history, current, account_id, cutoff)
    except Unsupported as exc:
        return UnsupportedPopulation(kind='population_unsupported', account_id=account_id, account_currency=account['currency'],home_currency=home,reason=str(exc))
    except (Corrupt, KeyError, ValueError, TypeError, IndexError, StopIteration) as exc:
        return InvalidPopulation(reason=str(exc) if isinstance(exc,Corrupt) else 'invalid_owned_population')
    relevant = tuple(v for v in current if v.account_id == account_id)
    return CompletePopulation(account_id=account_id,currency=home,cutoff=cutoff,current=relevant,
        eligible=tuple(v for v in relevant if v.active and v.effective_date<=cutoff),
        history=tuple(v for v in history if v.account_id==account_id),authority_transactions=authorized,signed_total=total,dated_gl_total=gl)


def resolve(s, ref, version_id):
    """Retained reference lookup with fresh whole-source authority on every call."""
    authority(s, (ref.transaction_id,))
    try:
        g=graph(s,(ref.transaction_id,))
        history,current=enumerate_graph(g)
        found=next((v for v in history if v.ref==ref and v.version_id==version_id),None)
        if found is None:return StaleReference()
        account=g.accounts[found.account_id];home=s.company_info_row['home_currency']
        if account['currency']!=home:
            return UnsupportedPopulation(kind='account_currency_unsupported',account_id=found.account_id,account_currency=account['currency'],home_currency=home,reason='foreign_statement_units_not_activated')
        from bookflow.company.reconciliation_proof import prove
        prove(g,history,current,found.account_id,'9999-12-31')
    except (Unsupported, Corrupt, KeyError, ValueError, TypeError, IndexError, StopIteration):
        return InvalidPopulation(reason='invalid_owned_population')
    return next((v for v in history if v.ref == ref and v.version_id == version_id), StaleReference())


def prospective(s, ctx, plan):
    return prepare_prospective(s, ctx, plan).changes


@dataclass(frozen=True)
class PreparedProjection:
    changes: ChangedEffects | UnsupportedPopulation
    # Exact source Plan or materialized deposit bundle; never persisted here.
    aggregate: Plan | dict


def prepare_prospective(s, ctx, plan):
    """Consume the exact present owning aggregate, not its bounded preview/cash.

    Deposit Prepared is materialized by its actual private bundle builder. Its
    prospective physical IDs belong to that bundle; callers must retain it when
    integrating a future coordinator. This function neither writes nor applies.
    """
    from bookflow.core.registry import Plan
    from bookflow.company.deposit_lifecycle import Prepared
    aggregate = plan
    if type(plan) is Prepared:
        from bookflow.company import deposit_persistence, deposit_persistence_validation
        bundle = deposit_persistence.build(s, ctx, plan)
        deposit_persistence_validation.validate(s, ctx, plan, bundle)
        aggregate = bundle
        data = dict(header=bundle['header'], pending=dict(bundle['pending'],bank_effect_current=bundle['bank_current']), before=bundle['data']['before'],
                    changed=bundle['data']['changed'], source_headers=bundle['source_headers'])
        extra = bundle['data']['targets']
    elif type(plan) is Plan:
        data = plan.data
        if data.get('recovered') or data.get('changed') is False:
            # No mutation replay and no invented financial version.
            if not data.get('header'):
                from bookflow.company import sales, journals
                inp = data.get('input')
                selector = next((getattr(inp,k,None) for k in ('journal','payment','invoice','sales_receipt') if getattr(inp,k,None)), None)
                if selector is None and data.get('recovered'):
                    raise Unsupported('recovery_is_not_a_prospective_aggregate')
                h = journals.resolve(s,selector) if getattr(inp,'journal',None) else sales.resolve(s,selector,data.get('document_type','payment'))
                data = dict(data,header=h,pending={})
        if 'pending' not in data or 'header' not in data:
            raise Unsupported('full_inner_source_plan_required')
        # Use the present independent owner validators, with no copied algorithm.
        kind = data['header']['type']
        if data.get('changed') is not False:
            if kind == 'journal_entry':
                from bookflow.company import journals
                journals.validate_pending_aggregate(s,data['header'],data['pending'],data.get('custom_plan'),custom_input=data['input'],creating=data.get('before') is None)
            elif kind in ('sales_receipt','invoice'):
                from bookflow.company import sales_validation
                sales_validation.validate(plan,s,ctx)
                if data.get('billing_conversion'):
                    from bookflow.company.billing_validation import validate
                    validate(plan,s,ctx)
                if data.get('settlement_extension'):
                    from bookflow.company.payment_restatement import validate
                    validate(plan,s,ctx)
            elif kind == 'payment':
                operation=data['operation']
                if operation in ('void','unapply'):
                    from bookflow.company.payment_cancellation import validate
                elif operation=='update':
                    from bookflow.company.payment_corrections import validate
                else:
                    from bookflow.company.payment_validation import validate
                validate(plan,s,ctx)
            else:
                raise Unsupported('unknown_prospective_owner')
        extra = set()
    else:
        raise TypeError('exact owning Plan or deposit Prepared required')
    header=data['header']; headers=[header]
    pending = dict(data['pending'])
    if data.get('billing_allocations'):
        pending['work_billing_allocations'] = list(pending.get('work_billing_allocations', ())) + list(data['billing_allocations'])
    for field in ('changed_headers','settlement_headers','source_headers'):
        headers.extend(after for before,after in data.get(field,()))
    persisted = {h['id'] for h in headers if s.company.conn.execute(sa.select(c.transactions.c.id).where(c.transactions.c.id==h['id'])).first()}
    # Pending work allocations/settlement participants belong to the full owner.
    participants=set(extra)|persisted
    for rows in data['pending'].values():
        for row in rows:
            participants.update(row[k] for k in ('paying_transaction_id','paid_transaction_id','source_transaction_id') if row.get(k))
    participants.discard(header['id']) if data.get('before') is None else None
    authorized=authority(s,participants)
    if data.get('billing_allocations') or data.get('billing_source'):
        access.require_resource(s,'customer-work','member')
    ids=persisted|{h['id'] for h in headers}
    before_graph=graph(s,persisted)
    after_graph=graph(s,ids,pending=pending,headers=headers)
    # Changes spanning a foreign account are an unsupported aggregate, never a
    # partially proved change set or a home-unit foreign statement balance.
    affected_accounts={row['account_id'] for view in (before_graph,after_graph)
        for table in ('posting_lines','bank_effect_versions') for row in view.rows[table]}
    home=s.company_info_row['home_currency']
    for identifier in sorted(affected_accounts):
        account=after_graph.accounts[identifier]
        if account['type'] in ('bank','credit_card') and account['currency']!=home:
            return PreparedProjection(UnsupportedPopulation(kind='account_currency_unsupported',account_id=identifier,
                account_currency=account['currency'],home_currency=home,reason='foreign_statement_units_not_activated'),aggregate)
    before_history,before=enumerate_graph(before_graph)
    after_history,after=enumerate_graph(after_graph)
    from bookflow.company.reconciliation_proof import prove
    for view,history,now in ((before_graph,before_history,before),(after_graph,after_history,after)):
        for account in {v.account_id for v in history}:
            prove(view,history,now,account,'9999-12-31')
    old={v.ref:v for v in before};new={v.ref:v for v in after}
    changed={ref for ref in old.keys()|new.keys() if old.get(ref)!=new.get(ref)}
    return PreparedProjection(ChangedEffects(before=tuple(v for v in before if v.ref in changed),after=tuple(v for v in after if v.ref in changed),
        authority_transactions=authorized), aggregate)
