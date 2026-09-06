"""Complete invoice discovery and shared origin-aware receipt calculations."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects as effects, sales_defaults as defaults
from bookflow.company import payment_selection as selection, payment_queries as query, payment_calculations as calc, sales
from bookflow.company.sales_models import money, _invalid
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money


def lineage_facts(s, parties):
    from bookflow.company.list_service import MAX_HIERARCHY_DEPTH
    parties = sorted(set(parties))
    rows, pending = {}, set(parties)
    while pending:
        keys = sorted(pending)
        found = []
        for offset in range(0, len(keys), 200):
            found.extend(s.company.conn.execute(sa.select(c.customers.c.id, c.customers.c.parent_id,
                c.customers.c.full_name, c.customers.c.version).where(c.customers.c.id.in_(keys[offset:offset+200]))).mappings())
        if {row['id'] for row in found} != pending:
            raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type':'customer'})
        rows.update({row['id']: row for row in found})
        pending = {row['parent_id'] for row in found if row['parent_id']} - rows.keys()
    result = []
    for party in parties:
        ancestors, seen, parent = [], {party}, rows[party]['parent_id']
        while parent is not None:
            if parent in seen:
                raise BookflowError('E_HIERARCHY_CYCLE', details={'record_id':party,'parent_id':parent})
            seen.add(parent)
            ancestors.append(dict(id=parent,label=rows[parent]['full_name']))
            parent = rows[parent]['parent_id']
            if len(ancestors) >= MAX_HIERARCHY_DEPTH:
                raise BookflowError('E_HIERARCHY_DEPTH', details={'record_id':party,'maximum':MAX_HIERARCHY_DEPTH})
        result.append([party, ancestors])
    return result


def _candidate_query(s, inp):
    from bookflow.company.payment_authority import readable_predicate
    context = selection.context(s, inp)
    funding = query.payment_facts(s, context['payment_id']) if context['payment_id'] else None
    t, r, p, a = c.transactions, c.transaction_revisions, c.sales_profiles, c.applications
    inverse = a.alias('candidate_inverse')
    used = sa.func.coalesce(sa.select(sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paid_transaction_id == t.c.id, a.c.kind == 'apply',
        ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == a.c.id))
    ).correlate(t).scalar_subquery(), 0)
    statement = sa.select(t.c.id.label('invoice_id'), t.c.version.label('expected_version'), t.c.number,
        p.c.customer_id, r.c.date, p.c.due_date, r.c.currency, r.c.total_minor_units.label('gross_minor_units'),
        r.c.id.label('revision_id'),
        used.label('applied_minor_units'), (r.c.total_minor_units-used).label('due_minor_units')).select_from(t).join(
        r, r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id).where(t.c.type == 'invoice', t.c.status == 'posted',
        r.c.date <= context['date'], r.c.currency == context['currency'], p.c.control_account_id == context['ar_account_id'],
        r.c.total_minor_units > used, readable_predicate(s, t.c.id))
    capacities = {}
    if funding:
        for key in funding['keys'].values():
            if key['ar_account_id'] == context['ar_account_id'] and key['currency'] == context['currency']:
                capacities[key['party_id']] = capacities.get(key['party_id'], 0) + funding['available'][key['id']]
        statement = statement.where(p.c.customer_id.in_([party for party, amount in capacities.items() if amount > 0]))
    else:
        family = sa.select(c.customers.c.id).where(c.customers.c.id == context['customer_id']).cte('candidate_family', recursive=True)
        family = family.union(sa.select(c.customers.c.id).join(family, c.customers.c.parent_id == family.c.id))
        statement = statement.where(p.c.customer_id.in_(sa.select(family.c.id)))
    if getattr(inp, 'q', None):
        s.company.raw.create_function('payment_casefold', 1, lambda value: (value or '').casefold(), deterministic=True)
        text = t.c.number + ' ' + sa.func.coalesce(r.c.memo, '')
        statement = statement.where(sa.func.payment_casefold(text).contains(inp.q.casefold(), autoescape=True))
    if funding and context['date'] < funding['revision']['date']:
        statement = statement.where(sa.false())
    return context, statement.order_by(r.c.date, t.c.id), capacities


def _original_projection(statement):
    original = c.transaction_revisions.alias('candidate_original')
    return statement.add_columns(original.c.id.label('original_revision_id'),
        original.c.total_minor_units.label('original_gross_minor_units')).join(original,
        sa.and_(original.c.transaction_id == c.transactions.c.id, original.c.revision_number == 1))


def candidates(s, inp):
    context, statement, capacities = _candidate_query(s, inp)
    result = [dict(row, available_source_minor_units=capacities[row['customer_id']] if context['payment_id'] else None)
        for row in s.company.conn.execute(_original_projection(statement)).mappings()]
    return context, result


def invoices(s, inp):
    context, statement, capacities = _candidate_query(s, inp)
    # Legal commercial/settlement changes always advance the invoice header.
    # Bind every candidate identity/version and relevant lineage, but materialize
    # the monetary display projection only for the requested delivery page.
    baseline = [list(row) for row in s.company.conn.execute(statement.with_only_columns(
        c.transactions.c.id, c.transactions.c.version, c.sales_profiles.c.customer_id))]
    balances = query.payer_balances(s, context['customer_id'])
    lineage = lineage_facts(s, [context['customer_id'], *(row[2] for row in baseline)])
    out = query.page(s, 'payment invoices', inp, baseline, facts=[context, baseline, balances, lineage])
    # The pinned baseline already determines delivery identities. Restrict the
    # monetary projection to those IDs rather than rescanning/sorting the family.
    ids = [row[0] for row in out['items']]
    out['items'] = [dict(row, available_source_minor_units=capacities[row['customer_id']] if context['payment_id'] else None)
        for row in s.company.conn.execute(_original_projection(statement).where(c.transactions.c.id.in_(ids))).mappings()] if ids else []
    return dict(out, **balances)


def suggest(s, inp):
    context, rows = candidates(s, inp)
    amount = money(inp.amount, context['currency'], 'amount').minor_units
    if amount <= 0:
        raise _invalid('amount', 'enter a positive cash amount')
    strategy = inp.strategy
    if strategy == 'company':
        strategy = 'exact_then_oldest' if defaults._info(s.company)['automatically_apply_payments'] else 'none'
    exact = next((row for row in rows if row['due_minor_units'] == amount and
        (row['available_source_minor_units'] is None or row['available_source_minor_units'] >= amount)), None)
    chosen = [exact] if exact else rows
    rendered, remaining, capacity = [], amount, {}
    if strategy != 'none':
        for row in chosen:
            party = row['customer_id']
            if row['available_source_minor_units'] is not None:
                capacity.setdefault(party, row['available_source_minor_units'])
            units = min(row['due_minor_units'], remaining, capacity.get(party, remaining))
            if units:
                rendered.append(dict(invoice_id=row['invoice_id'], expected_version=row['expected_version'],
                    ordinal=len(rendered) + 1, due_minor_units=row['due_minor_units'], amount_minor_units=units,
                    amount_origin='calculated', currency=context['currency']))
                remaining -= units
                if party in capacity:
                    capacity[party] -= units
            if not remaining:
                break
    lineage = lineage_facts(s, [context['customer_id'], *(row['customer_id'] for row in rows)])
    return dict(query.page(s, 'payment suggest', inp, rendered, facts=[context, rows, strategy, rendered, lineage]),
        amount=Money(amount, context['currency']).to_dict(), amount_origin='entered', unapplied_minor_units=remaining, problems=[])


def calculate(s, inp):
    context = selection.context(s, inp)
    amount, origin = None, 'selection_total' if context['automatically_calculate'] else 'unresolved'
    manifest = None
    if inp.applications.mode == 'selection':
        header = selection.resolve(s, inp.applications.selection)
        revision, saved_context, items = selection.saved(s, header, inp.applications.expected_version)
        if any(context[field] != saved_context[field] for field in ('mode', 'customer_id', 'ar_account_id', 'payment_id', 'currency', 'date')):
            raise _invalid('applications', 'selection context differs from calculation')
        context = saved_context
        amount, origin = revision['amount_minor_units'], revision['amount_origin']
        manifest = revision['manifest_hash']
    else:
        items = []
        for ordinal, row in enumerate(inp.applications.items, 1):
            facts = query.invoice_facts(s, row.invoice)
            sales._version(s, facts['header'], row.expected_version)
            selection.compatible(s, context, facts)
            row_origin = row.amount_origin or ('entered' if row.amount is not None else 'unresolved')
            value = money(row.amount, context['currency'], 'applications.amount').minor_units if row.amount is not None else None
            if row_origin == 'calculated' and value is None:
                value = 0
            items.append(dict(invoice_id=facts['header']['id'], ordinal=ordinal, expected_version=row.expected_version,
                due_minor_units=facts['due'], amount_minor_units=value, amount_origin=row_origin))
    parties = {context['customer_id']}
    for item in items:
        facts = query.invoice_facts(s, item['invoice_id'])
        parties.add(facts['profile']['customer_id'])
        sales._version(s, facts['header'], item['expected_version'])
        selection.compatible(s, context, facts)
        if facts['due'] != item['due_minor_units']:
            raise BookflowError('E_QUERY_STALE', details={'reason': 'selection_baseline'})
    if 'amount' in inp.model_fields_set:
        amount = money(inp.amount, context['currency'], 'amount').minor_units if inp.amount is not None else None
        origin = 'entered' if amount is not None else 'selection_total' if context['automatically_calculate'] else 'unresolved'
    if inp.amount_mode != 'company' and not (origin == 'entered' and amount is not None and inp.amount_mode == 'selection_total'):
        origin = inp.amount_mode
    try:
        result = calc.calculate(amount, origin, selection._rows(items),
            calculate_unresolved=context['automatically_calculate'] or inp.amount_mode == 'selection_total',
            **selection.funding_calculation(s, context, items))
    except ValueError as exc:
        raise _invalid('applications', str(exc)) from None
    originals = {row['invoice_id']: row for row in items}
    rendered = [dict(originals[row.invoice], amount_minor_units=row.amount, amount_origin=row.origin, currency=context['currency']) for row in result.rows]
    return dict(query.page(s, 'payment calculate', inp, rendered, facts=[context, manifest, rendered, result.amount, result.amount_origin, lineage_facts(s, parties)]),
        amount=Money(result.amount, context['currency']).to_dict() if result.amount is not None else None,
        amount_origin=result.amount_origin, unapplied_minor_units=result.unapplied, problems=list(result.problems))


def payment_page(s, inp):
    """SQL aggregates and filtering precede bounded delivery; no per-row history walk."""
    import sqlalchemy as sa
    from bookflow.company.payment_authority import require_resource
    customer = defaults._row(s.company, 'customer', inp.customer, active=False)['id'] if inp.customer else None
    component_customer = defaults._row(s.company, 'customer', inp.component_customer, active=False)['id'] if inp.component_customer else None
    method = defaults._row(s.company, 'payment_method', inp.payment_method, active=False)['id'] if inp.payment_method else None
    family = {customer}
    if customer and inp.include_descendants:
        family.update(row[0] for row in s.company.raw.execute("""WITH RECURSIVE family(id) AS (
            SELECT id FROM customers WHERE id=? UNION SELECT c.id FROM customers c JOIN family f ON c.parent_id=f.id)
            SELECT id FROM family""", (customer,)))
    t, r, p, a = c.transactions, c.transaction_revisions, c.payment_profiles, c.applications
    inverse = a.alias('inverse')
    used = sa.func.coalesce(sa.select(sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paying_transaction_id == t.c.id, a.c.kind == 'apply',
        ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == a.c.id))
    ).correlate(t).scalar_subquery(), 0)
    available = sa.case((t.c.status == 'posted', r.c.total_minor_units-used), else_=0)
    statement = sa.select(t.c.id, t.c.version, t.c.number, r.c.date, t.c.status, p.c.payer_id.label('customer_id'),
        p.c.payment_method_id, r.c.currency, r.c.total_minor_units.label('received_minor_units')).select_from(t).join(r,
        r.c.id == t.c.current_revision_id).join(p, p.c.revision_id == r.c.id).where(t.c.type == 'payment')
    try:
        require_resource(s, 'customer-work', 'member')
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        protected = sa.exists(sa.select(a.c.id).join(c.work_billing_allocations,
            c.work_billing_allocations.c.transaction_id == a.c.paid_transaction_id).where(a.c.paying_transaction_id == t.c.id))
        statement = statement.where(~protected)
    if customer:
        statement = statement.where(p.c.payer_id.in_(family))
    if component_customer:
        statement = statement.where(sa.exists(sa.select(c.payment_component_keys.c.id).where(
            c.payment_component_keys.c.transaction_id == t.c.id, c.payment_component_keys.c.party_id == component_customer)))
    for condition in ([p.c.payment_method_id == method] if method else []) + ([t.c.status == inp.status] if inp.status else []) + ([t.c.number == inp.number] if inp.number else []):
        statement = statement.where(condition)
    if inp.date_from:
        statement = statement.where(r.c.date >= inp.date_from)
    if inp.date_to:
        statement = statement.where(r.c.date <= inp.date_to)
    if inp.has_available_credit is not None:
        statement = statement.where(available > 0 if inp.has_available_credit else available <= 0)
    if inp.q:
        s.company.raw.create_function('payment_casefold', 1, lambda value: (value or '').casefold(), deterministic=True)
        captured = sa.func.coalesce(sa.func.json_extract(p.c.profile_snapshot, '$.payer.label'), '')
        text = t.c.number + ' ' + sa.func.coalesce(r.c.memo, '') + ' ' + sa.func.coalesce(p.c.reference, '') + ' ' + captured
        statement = statement.where(sa.func.payment_casefold(text).contains(inp.q.casefold(), autoescape=True))
    order = {'date': r.c.date, 'number': t.c.number, 'received': r.c.total_minor_units, 'unapplied': available}[inp.sort]
    statement = statement.order_by(order.desc() if inp.direction == 'desc' else order.asc(), t.c.id.desc() if inp.direction == 'desc' else t.c.id.asc())
    out = query.sql_page(s, 'payment query', inp, statement)
    # Project settlement only after SQL has selected this page. Computing every
    # receipt's display fields before date sorting defeats bounded delivery.
    ids = [row['id'] for row in out['items']]
    amounts = dict(s.company.conn.execute(sa.select(a.c.paying_transaction_id, sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paying_transaction_id.in_(ids), a.c.kind == 'apply',
        ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == a.c.id))
    ).group_by(a.c.paying_transaction_id)).all()) if ids else {}
    for row in out['items']:
        row['applied_minor_units'] = amounts.get(row['id'], 0)
        row['unapplied_minor_units'] = row['received_minor_units'] - row['applied_minor_units'] if row['status'] == 'posted' else 0
    return out
