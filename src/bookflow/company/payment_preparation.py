"""Complete invoice discovery and shared origin-aware receipt calculations."""
import json
from collections import namedtuple
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


def _candidate_query(s, inp, *, page_ids=None, resolved=None, positive_due=True):
    from bookflow.company.payment_authority import readable_predicate
    context, funding = resolved if resolved is not None else (selection.context(s, inp), None)
    if resolved is None and context['payment_id']:
        funding = query.payment_facts(s, context['payment_id'])
    t = query.indexed_source(c.transactions, 'ix_co17_transactions_current',
        'current_revision_id', 'type', 'status', 'id', 'version', 'number')
    r = query.indexed_source(c.transaction_revisions, 'ix_co17_revisions_read',
        'id', 'date', 'currency', 'total_minor_units', 'memo')
    if page_ids is not None:
        t = c.transactions
    p = query.indexed_source(c.sales_profiles,
        'ix_co17_sales_party' if page_ids is None else 'ix_co17_sales_revision',
        'customer_id', 'control_account_id', 'revision_id')
    display = query.indexed_source(c.sales_profiles, 'ix_co17_sales_revision',
        'revision_id', 'due_date').alias('candidate_display')
    due_date = sa.select(display.c.due_date).where(display.c.revision_id == r.c.id).correlate(r).scalar_subquery()
    source = (query.cross_join(query.cross_join(p, r, r.c.id == p.c.revision_id), t, t.c.current_revision_id == r.c.id)
        if page_ids is None else query.cross_join(query.cross_join(t, r, r.c.id == t.c.current_revision_id), p, p.c.revision_id == r.c.id))
    a = query.indexed_source(c.applications, 'ix_co17_applications_invoice',
        'paid_transaction_id', 'kind', 'amount_minor_units', 'id')
    inverse = c.applications.alias('candidate_inverse')
    used = sa.func.coalesce(sa.select(sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paid_transaction_id == t.c.id, a.c.kind == 'apply',
        ~sa.exists(sa.select(sa.literal_column("1")).where(inverse.c.reverses_application_id == a.c.id))
    ).correlate(t).scalar_subquery(), 0)
    statement = sa.select(t.c.id.label('invoice_id'), t.c.version.label('expected_version'), t.c.number,
        p.c.customer_id, r.c.date, due_date.label('due_date'), r.c.currency, r.c.total_minor_units.label('gross_minor_units'),
        r.c.id.label('revision_id'),
        used.label('applied_minor_units'), (r.c.total_minor_units-used).label('due_minor_units')).select_from(source).where(t.c.type == 'invoice', t.c.status == 'posted',
        r.c.date <= context['date'], r.c.currency == context['currency'], p.c.control_account_id == context['ar_account_id'],
        readable_predicate(s, t.c.id))
    if page_ids is not None:
        statement = statement.where(t.c.id.in_(page_ids))
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
    if positive_due:
        statement = statement.where(r.c.total_minor_units > used)
    if funding and context['date'] < funding['revision']['date']:
        statement = statement.where(sa.false())
    return context, statement.order_by(r.c.date, t.c.id), capacities, funding


def _original_projection(statement):
    original = c.transaction_revisions.alias('candidate_original')
    return statement.add_columns(original.c.id.label('original_revision_id'),
        original.c.total_minor_units.label('original_gross_minor_units')).join(original,
        sa.and_(original.c.transaction_id == statement.selected_columns.invoice_id, original.c.revision_number == 1))


_Candidate = namedtuple('_Candidate', 'invoice_id expected_version customer_id date currency revision_id gross_minor_units due_minor_units available_source_minor_units')

def candidates(s, inp):
    context, statement, capacities, funding = _candidate_query(s, inp, positive_due=False)
    # Suggestions bind the complete candidate relation and exact current money.
    # Original display fields are projected only by invoice delivery; immutable
    # revision identity/header version bind commercial history on this baseline.
    cols = statement.selected_columns
    baseline = statement.with_only_columns(cols.invoice_id, cols.expected_version,
        cols.customer_id, cols.date, cols.currency, cols.revision_id,
        cols.gross_minor_units, cols.due_minor_units).order_by(None).limit(-1).subquery('candidate_money')
    # LIMIT -1 is a transient SQLite flattening barrier: evaluate each exact
    # correlated live sum once, then retain all positive-due candidates.
    baseline = sa.select(baseline).where(baseline.c.due_minor_units > 0).order_by(baseline.c.date, baseline.c.invoice_id)
    result = [_Candidate(*row, capacities[row[2]] if context['payment_id'] else None)
        for row in s.company.conn.execute(baseline).all()]
    return context, result


def invoices(s, inp):
    context, statement, capacities, funding = _candidate_query(s, inp)
    # Legal commercial/settlement changes always advance the invoice header.
    # Bind every candidate identity/version and relevant lineage, but materialize
    # the monetary display projection only for the requested delivery page.
    baseline = [list(row) for row in s.company.conn.execute(statement.with_only_columns(
        statement.selected_columns.invoice_id, statement.selected_columns.expected_version, statement.selected_columns.customer_id)).all()]
    balances = query.payer_balances(s, context['customer_id'])
    lineage = lineage_facts(s, [context['customer_id'], *(row[2] for row in baseline)])
    out = query.page(s, 'payment invoices', inp, baseline, facts=[context, baseline, balances, lineage])
    # The pinned baseline already determines delivery identities. Restrict the
    # monetary projection to those IDs rather than rescanning/sorting the family.
    ids = [row[0] for row in out['items']]
    if ids:
        _, delivery, _, _ = _candidate_query(s, inp, page_ids=ids, resolved=(context, funding))
        out['items'] = [dict(row, available_source_minor_units=capacities[row['customer_id']] if context['payment_id'] else None)
            for row in s.company.conn.execute(_original_projection(delivery)).mappings()]
    else:
        out['items'] = []
    return dict(out, **balances)


def suggest(s, inp):
    context, rows = candidates(s, inp)
    amount = money(inp.amount, context['currency'], 'amount').minor_units
    if amount <= 0:
        raise _invalid('amount', 'enter a positive cash amount')
    strategy = inp.strategy
    if strategy == 'company':
        strategy = 'exact_then_oldest' if defaults._info(s.company)['automatically_apply_payments'] else 'none'
    exact = next((row for row in rows if row.due_minor_units == amount and
        (row.available_source_minor_units is None or row.available_source_minor_units >= amount)), None)
    chosen = [exact] if exact else rows
    rendered, remaining, capacity = [], amount, {}
    if strategy != 'none':
        for row in chosen:
            party = row.customer_id
            if row.available_source_minor_units is not None:
                capacity.setdefault(party, row.available_source_minor_units)
            units = min(row.due_minor_units, remaining, capacity.get(party, remaining))
            if units:
                rendered.append(dict(invoice_id=row.invoice_id, expected_version=row.expected_version,
                    ordinal=len(rendered) + 1, due_minor_units=row.due_minor_units, amount_minor_units=units,
                    amount_origin='calculated', currency=context['currency']))
                remaining -= units
                if party in capacity:
                    capacity[party] -= units
            if not remaining:
                break
    lineage = lineage_facts(s, [context['customer_id'], *(row.customer_id for row in rows)])
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
    t = query.indexed_source(c.transactions, 'ix_co17_transactions_current',
        'current_revision_id', 'type', 'status', 'id', 'version', 'number')
    r = query.indexed_source(c.transaction_revisions, 'ix_co17_revisions_read',
        'id', 'date', 'currency', 'total_minor_units', 'memo')
    p = query.indexed_source(c.payment_profiles, 'ix_co17_payment_profile',
        'revision_id', 'payer_id', 'payment_method_id', 'reference', expression='payer_label')
    a = c.applications
    inverse = a.alias('inverse')
    used = sa.func.coalesce(sa.select(sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paying_transaction_id == t.c.id, a.c.kind == 'apply',
        ~sa.exists(sa.select(sa.literal_column("1")).where(inverse.c.reverses_application_id == a.c.id))
    ).correlate(t).scalar_subquery(), 0)
    available = sa.case((t.c.status == 'posted', r.c.total_minor_units-used), else_=0)
    statement = sa.select(t.c.id, t.c.version, t.c.number, r.c.date, t.c.status, p.c.payer_id.label('customer_id'),
        p.c.payment_method_id, r.c.currency, r.c.total_minor_units.label('received_minor_units')).select_from(query.cross_join(query.cross_join(p, r,
        r.c.id == p.c.revision_id), t, t.c.current_revision_id == r.c.id)).where(t.c.type == 'payment')
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
        captured = p.c.payer_label
        text = t.c.number + ' ' + sa.func.coalesce(r.c.memo, '') + ' ' + sa.func.coalesce(p.c.reference, '') + ' ' + captured
        statement = statement.where(sa.func.payment_casefold(text).contains(inp.q.casefold(), autoescape=True))
    order = {'date': r.c.date, 'number': t.c.number, 'received': r.c.total_minor_units, 'unapplied': available}[inp.sort]
    statement = statement.order_by(order.desc() if inp.direction == 'desc' else order.asc(), t.c.id.desc() if inp.direction == 'desc' else t.c.id.asc())
    # Carry only identity through the complete filtered window and sort. The
    # selected page receives its display fields afterward in the same snapshot.
    out = query.sql_page(s, 'payment query', inp, statement.with_only_columns(t.c.id),
        count_with_page=bool(inp.q) or inp.has_available_credit is not None)
    # Project settlement only after SQL has selected this page. Computing every
    # receipt's display fields before date sorting defeats bounded delivery.
    ids = [row['id'] for row in out['items']]
    if ids:
        # Use primary-key header lookup for bounded display, without repeating
        # expensive text/capacity predicates over the entire company.
        headers = c.transactions
        display = sa.select(headers.c.id, headers.c.version, headers.c.number, r.c.date,
            headers.c.status, p.c.payer_id.label('customer_id'), p.c.payment_method_id,
            r.c.currency, r.c.total_minor_units.label('received_minor_units')).select_from(
                query.cross_join(query.cross_join(headers, r, r.c.id == headers.c.current_revision_id),
                    p, p.c.revision_id == r.c.id)).where(headers.c.id.in_(ids))
        by_id = {row['id']: dict(row) for row in s.company.conn.execute(display).mappings()}
        out['items'] = [by_id[identifier] for identifier in ids]
    amounts = dict(s.company.conn.execute(sa.select(a.c.paying_transaction_id, sa.func.sum(a.c.amount_minor_units)).where(
        a.c.paying_transaction_id.in_(ids), a.c.kind == 'apply',
        ~sa.exists(sa.select(sa.literal_column("1")).where(inverse.c.reverses_application_id == a.c.id))
    ).group_by(a.c.paying_transaction_id)).all()) if ids else {}
    for row in out['items']:
        row['applied_minor_units'] = amounts.get(row['id'], 0)
        row['unapplied_minor_units'] = row['received_minor_units'] - row['applied_minor_units'] if row['status'] == 'posted' else 0
    return out
