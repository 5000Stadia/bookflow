"""Complete invoice discovery and shared origin-aware receipt calculations."""
from bookflow.company import schema as c, document_effects as effects, sales_defaults as defaults
from bookflow.company import payment_selection as selection, payment_queries as query, payment_calculations as calc, sales
from bookflow.company.sales_models import money, _invalid
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money


def candidates(s, inp):
    context = selection.context(s, inp)
    funding = query.payment_facts(s, context['payment_id']) if context['payment_id'] else None
    result = []
    for header in effects.rows(s, c.transactions, c.transactions.c.type == 'invoice', c.transactions.c.status == 'posted'):
        try:
            facts = query.invoice_facts(s, header['id'])
            selection.compatible(s, context, facts)
        except BookflowError as exc:
            if exc.code in ('E_PERMISSION', 'E_APPLICATION_INCOMPATIBLE', 'E_VALIDATION'):
                continue
            raise
        if facts['due'] <= 0:
            continue
        profile, revision = facts['profile'], facts['revision']
        if getattr(inp, 'q', None) and inp.q.casefold() not in (header['number'] + ' ' + (revision['memo'] or '')).casefold():
            continue
        capacity = None
        if funding:
            capacity = sum(funding['available'][key['id']] for key in funding['keys'].values() if key['party_id'] == profile['customer_id'])
            if capacity <= 0:
                continue
        result.append(dict(invoice_id=header['id'], expected_version=header['version'], number=header['number'],
            customer_id=profile['customer_id'], date=revision['date'], due_date=profile['due_date'], currency=revision['currency'],
            gross_minor_units=facts['gross'], applied_minor_units=facts['applied'], due_minor_units=facts['due'],
            available_source_minor_units=capacity))
    result.sort(key=lambda row: (row['date'], row['invoice_id']))
    return context, result


def invoices(s, inp):
    context, rows = candidates(s, inp)
    balances = query.payer_balances(s, context['customer_id'])
    return dict(query.page(s, 'payment invoices', inp, rows, facts=[context, rows, balances]), **balances)


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
    return dict(query.page(s, 'payment suggest', inp, rendered, facts=[context, rows, strategy, rendered]),
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
    for item in items:
        facts = query.invoice_facts(s, item['invoice_id'])
        sales._version(s, facts['header'], item['expected_version'])
        selection.compatible(s, context, facts)
        if facts['due'] != item['due_minor_units']:
            raise BookflowError('E_QUERY_STALE', details={'reason': 'selection_baseline'})
    if 'amount' in inp.model_fields_set:
        amount = money(inp.amount, context['currency'], 'amount').minor_units if inp.amount is not None else None
        origin = 'entered' if amount is not None else 'selection_total' if context['automatically_calculate'] else 'unresolved'
    if inp.amount_mode != 'company':
        origin = inp.amount_mode
    try:
        result = calc.calculate(amount, origin, selection._rows(items), calculate_unresolved=context['automatically_calculate'])
    except ValueError as exc:
        raise _invalid('applications', str(exc)) from None
    originals = {row['invoice_id']: row for row in items}
    rendered = [dict(originals[row.invoice], amount_minor_units=row.amount, amount_origin=row.origin, currency=context['currency']) for row in result.rows]
    return dict(query.page(s, 'payment calculate', inp, rendered, facts=[context, manifest, rendered, result.amount, result.amount_origin]),
        amount=Money(result.amount, context['currency']).to_dict() if result.amount is not None else None,
        amount_origin=result.amount_origin, unapplied_minor_units=result.unapplied, problems=list(result.problems))


def payment_page(s, inp):
    customer = defaults._row(s.company, 'customer', inp.customer, active=False)['id'] if inp.customer else None
    component_customer = defaults._row(s.company, 'customer', inp.component_customer, active=False)['id'] if inp.component_customer else None
    method = defaults._row(s.company, 'payment_method', inp.payment_method, active=False)['id'] if inp.payment_method else None
    family = {customer}
    if customer and inp.include_descendants:
        family.update(row[0] for row in s.company.raw.execute('''WITH RECURSIVE family(id) AS (
            SELECT id FROM customers WHERE id=? UNION SELECT c.id FROM customers c JOIN family f ON c.parent_id=f.id)
            SELECT id FROM family''', (customer,)))
    result = []
    for header in effects.rows(s, c.transactions, c.transactions.c.type == 'payment'):
        try:
            facts = query.payment_facts(s, header['id'])
        except BookflowError as exc:
            if exc.code == 'E_PERMISSION':
                continue
            raise
        profile, revision = facts['profile'], facts['revision']
        if (customer and profile['payer_id'] not in family or method and profile['payment_method_id'] != method or
            component_customer and not any(key['party_id'] == component_customer for key in facts['keys'].values()) or
            inp.status and header['status'] != inp.status or inp.number and header['number'] != inp.number or
            inp.date_from and revision['date'] < inp.date_from or inp.date_to and revision['date'] > inp.date_to):
            continue
        if inp.q and inp.q.casefold() not in ' '.join([header['number'], revision['memo'] or '', profile['reference'] or '']).casefold():
            continue
        available = sum(facts['available'].values())
        if inp.has_available_credit is not None and bool(available > 0) != inp.has_available_credit:
            continue
        result.append(dict(id=header['id'], version=header['version'], number=header['number'], date=revision['date'],
            status=header['status'], customer_id=profile['payer_id'], payment_method_id=profile['payment_method_id'],
            currency=revision['currency'], received_minor_units=revision['total_minor_units'],
            applied_minor_units=sum(row['amount_minor_units'] for row in facts['applications']), unapplied_minor_units=available))
    sort = {'received': 'received_minor_units', 'unapplied': 'unapplied_minor_units'}.get(inp.sort, inp.sort)
    result.sort(key=lambda row: (row[sort], row['id']), reverse=inp.direction == 'desc')
    return query.page(s, 'payment query', inp, result)
