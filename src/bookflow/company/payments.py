"""Atomic new-cash receipts and exact-party immutable invoice applications."""
from bookflow.company.party_merges import merged_sql
import json

import sqlalchemy as sa

from bookflow.company.aliases import alias
from bookflow.company import payment_prefetch as prefetch
from bookflow.company import schema as c, sales, journals, sales_defaults as defaults
from bookflow.company import document_effects as effects, journal_custom_fields as custom, list_service
from bookflow.company import payment_calculations as calc, payment_queries as query, payment_selection as selection
from bookflow.company import payment_operations as operations, early_discounts as early
from bookflow.company.payment_authority import authorize
from bookflow.company.payment_models import PaymentContext
from bookflow.company.payment_summaries import summarize
from bookflow.company.payment_outputs import PaymentProfileOutput, PaymentWriteOutput, PaymentOutput, PaymentRevisionOutput, PaymentShowOutput
from bookflow.company.sales_models import money, _invalid
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES as SETTLEABLE
from bookflow.core import audit, clock
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, Applied, Touched
from bookflow.hub.users import common

TABLE_KINDS = (
    ('transaction_revisions', 'transaction_revision', 'id'),
    ('document_line_identities', 'document_line_identity', 'id'),
    ('document_lines', 'document_line', 'id'),
    ('payment_profiles', 'payment_profile', 'revision_id'),
    ('payment_component_keys', 'payment_component_key', 'id'),
    ('payment_components', 'payment_component', 'id'),
    ('posting_batches', 'posting_batch', 'id'),
    ('posting_lines', 'posting_line', 'id'),
    ('posting_line_sources', 'posting_line_source', 'id'),
    ('settlement_line_keys', 'settlement_line_key', 'id'),
    ('applications', 'application', 'id'),
    ('application_allocations', 'application_allocation', 'id'),
)
WRITE_OFF_ACCOUNT_TYPES = ('expense', 'other_expense')
PREFERENCES = ('automatically_apply_payments', 'automatically_calculate_payments', 'use_undeposited_funds_for_payments')


def party_lineage(s, party):
    rows = list_service.hierarchy_ancestors(s.company, c.customers, party)
    return [defaults._ref(row).model_dump() for row in rows]


def used_reference_facts(value):
    """Master version counters are informational; bind the fields actually used."""
    if isinstance(value, dict):
        return {key: used_reference_facts(item) for key, item in value.items() if key != 'version'}
    if isinstance(value, list):
        return [used_reference_facts(item) for item in value]
    return value


def effect_header(header, revision):
    if header is None:
        return None
    return dict(id=header['id'], version=header['version'], revision_id=revision['id'],
        revision_number=revision['revision_number'], number=header['number'], date=revision['date'],
        amount=Money(revision['total_minor_units'], revision['currency']).to_dict(), status=header['status'])


def current_output(s, selector, *, complete_components=False, facts=None):
    """`facts` are `payment_facts` this caller already read for the same receipt, still current."""
    if facts is None:
        facts = query.payment_facts(s, selector)
    else:
        query.authorize(s, [facts['header']['id']])
    header, revision = facts['header'], facts['revision']
    capacities = {row['component_key_id']: row for row in facts['components']}
    applied_by_key = {}
    for row in facts['applications']:
        key = row['source_component_key_id']
        applied_by_key[key] = applied_by_key.get(key, 0) + row['amount_minor_units']
    rendered = []
    ordered = sorted(facts['keys'].items(), key=lambda pair: (pair[1]['party_id'], pair[0]))
    for key_id, key in ordered if complete_components else ordered[:50]:
        component = capacities.get(key_id)
        capacity = component['amount_minor_units'] if component else 0
        party = prefetch.customer(s, key['party_id'], False, lambda: defaults._row(s.company, 'customer', key['party_id'], active=False))
        applied = applied_by_key.get(key_id, 0)
        rendered.append(dict(component_key_id=key_id, component_id=component['id'] if component else None,
            party_id=key['party_id'], party_name=party['full_name'], ar_account_id=key['ar_account_id'], currency=key['currency'],
            received_minor_units=capacity, applied_minor_units=applied, available_minor_units=facts['available'][key_id]))
    return dict(payment_id=header['id'], version=header['version'], revision_id=revision['id'], status=header['status'],
        received_minor_units=revision['total_minor_units'],
        effective_received_minor_units=revision['total_minor_units'] if header['status'] == 'posted' else 0,
        applied_minor_units=sum(row['amount_minor_units'] for row in facts['applications']),
        available_minor_units=sum(facts['available'].values()), currency=revision['currency'],
        components=rendered, component_count=len(ordered),
        discount_minor_units=sum(row['amount_minor_units'] for row in discount_rows(s, header['id']).values()))


def show(s, inp):
    from bookflow.company.payment_deletions import deletion_info
    facts = query.payment_facts(s, inp.payment)
    header, revision, profile = facts['header'], facts['revision'], facts['profile']
    deletion = deletion_info(s, header, getattr(inp, 'include_deleted', False))
    if inp.revision is not None:
        rows = effects.rows(s, c.transaction_revisions, c.transaction_revisions.c.transaction_id == header['id'],
                            c.transaction_revisions.c.revision_number == inp.revision)
        if not rows:
            raise BookflowError('E_RECORD_NOT_FOUND')
        revision = rows[0]
        profile = effects.rows(s, c.payment_profiles, c.payment_profiles.c.revision_id == revision['id'])[0]
    from bookflow.company import payment_bounces
    bounce = payment_bounces.live_bounces(s, [header['id']]).get(header['id'])
    return PaymentShowOutput(**dict(header, **({'status': 'deleted'} if deletion else {})), deletion=deletion,
        revision=revision_output(revision, profile), current=current_output(s, header['id']),
        bounce=payment_bounces.on_receipt(bounce) if bounce else None)


def revision_output(revision, profile):
    """Pure rendering of one stored, already-authorized commercial revision."""
    return PaymentRevisionOutput(**dict(id=revision['id'], revision_number=revision['revision_number'],
        date=revision['date'], number=revision['number'], memo=revision['memo'], reference=profile['reference'],
        total=Money(revision['total_minor_units'], revision['currency']).to_dict(), audit_event_id=revision['audit_event_id'],
        profile=json.loads(profile['profile_snapshot']), custom_fields_snapshot=json.loads(revision['custom_fields_snapshot'])))


def _capacity(side, requested, available, currency, identifier):
    if requested > available:
        raise BookflowError('E_APPLICATION_CAPACITY', details=dict(side=side, record_id=identifier,
            requested=Money(requested, currency).to_dict(), available=Money(available, currency).to_dict()))


def _applications(s, inp, context_, *, amount=None):
    selected = None
    if inp.applications.mode == 'selection':
        selected = selection.resolve(s, inp.applications.selection)
        revision, captured, items = selection.saved(s, selected)
        from bookflow.company.payment_recovery import require_no_active_recovery
        require_no_active_recovery(s, selected['id'])
        selection._version(s, selected, inp.applications.expected_version)
        if selected['state'] != 'open':
            raise BookflowError('E_SELECTION_CONSUMED')
        if any(context_[field] != captured[field] for field in ('mode', 'customer_id', 'ar_account_id', 'payment_id', 'currency', 'date')):
            raise _invalid('applications', 'selection context differs from this financial intent')
        if amount is not None and revision['amount_minor_units'] != amount:
            raise _invalid('amount', 'cash must equal the resolved saved selection header')
        if revision['amount_minor_units'] is None:
            raise _invalid('applications', 'resolve the shared header amount before posting')
        try:
            selection.funding_calculation(s, captured, items)
        except BookflowError as exc:
            if exc.code == 'E_QUERY_STALE':
                raise BookflowError('E_PREVIEW_STALE', details=exc.details) from None
            raise
        entries = [(item['invoice_id'], item['expected_version'], item['amount_minor_units']) for item in items]
        selected = dict(header=selected, revision=revision, items=items)
    elif inp.applications.mode == 'suggested':
        # The same choice `payment suggest` shows, made in this write's own read of the invoices.
        from bookflow.company.payment_models import PaymentSuggestInput
        from bookflow.company.payment_preparation import choose
        ask = PaymentSuggestInput(mode='new_receipt', customer=inp.customer, ar_account=inp.ar_account,
                                  date=inp.date, amount=inp.amount, strategy=inp.applications.strategy)
        rendered = choose(s, ask)[3]
        if len(rendered) > 200:
            raise _invalid('applications', f'the suggestion covers {len(rendered)} invoices, more than the 200 one '
                           'receipt applies; receive the payment in parts or use a saved selection')
        entries = [(row['invoice_id'], row['expected_version'], row['amount_minor_units']) for row in rendered]
    else:
        entries = [(item.invoice, item.expected_version, money(item.amount, context_['currency'], 'applications.amount').minor_units)
                   for item in inp.applications.items]
    result, seen = [], set()
    prefetch.invoices(s, [entry[0] for entry in entries])
    for selector_, version, units in entries:
        if units is None or units <= 0:
            raise _invalid('applications.amount', 'resolve a positive selected amount or explicitly deselect the row')
        facts = query.invoice_facts(s, selector_, write=True)
        header = facts['header']
        if header['id'] in seen:
            raise _invalid('applications', 'select each invoice once')
        seen.add(header['id'])
        sales._version(s, header, version)
        selection.compatible(s, context_, facts)
        _capacity('target', units, facts['due'], context_['currency'], header['id'])
        result.append(dict(facts=facts, amount=units))
    return result, selected


def _target_components(s, facts, pending, created, event):
    """Stored invoice amounts plus durable ordinals, never reconstructed prices."""
    header, revision = facts['header'], facts['revision']
    known = prefetch.invoice(s, header['id'])
    if known is not None and known['revision']['id'] != revision['id']:
        known = None
    lines = known['lines'] if known else sales.saved_lines(s, revision)
    keys = known['keys'] if known else effects.rows(s, c.settlement_line_keys, c.settlement_line_keys.c.transaction_id == header['id'])
    ordinals = {row['line_id']: row['ordinal'] for row in keys}
    maximum = max(ordinals.values(), default=0)
    for line in sorted(lines, key=lambda line: line['line_id']) if not keys else lines:
        if line['line_id'] not in ordinals:
            maximum += 1
            ordinals[line['line_id']] = maximum
            pending['settlement_line_keys'].append(dict(**created(), transaction_id=header['id'], line_id=line['line_id'],
                                                       ordinal=maximum, audit_event_id=event))
    source, leg = c.posting_line_sources, c.posting_lines
    sources = known['sources'] if known else [dict(row) for row in s.company.conn.execute(sa.select(source,
        leg.c.account_id, leg.c.debit_minor_units, leg.c.credit_minor_units).join(leg, leg.c.id == source.c.posting_line_id).where(
        source.c.transaction_id == header['id'], source.c.revision_id == revision['id'], source.c.reversed_source_id.is_(None))).mappings()]
    taxes = known['taxes'] if known else effects.rows(s, c.sales_tax_components, c.sales_tax_components.c.revision_id == revision['id'])
    result = {}
    for line in lines:
        values = [(None, line['net_minor_units'])] + [(tax, tax['tax_minor_units'])
                  for tax in taxes if tax['document_line_id'] == line['id']]
        for tax, capacity in values:
            if not capacity:
                continue
            pair = sales.component_attribution(sources, document_line_id=line['id'], item_snapshot=line['item_snapshot'],
                tax=tax, control_account_id=facts['profile']['control_account_id'],
                capacity=capacity, currency=revision['currency'])
            if pair is None:
                raise _invalid('invoice', 'stored component has ambiguous accounting attribution')
            ar, rec = pair
            physical, tax_item = (tax['id'], tax['tax_item_id']) if tax is not None else (None, None)
            key = calc.ComponentKey(ordinals[line['line_id']], 1 if physical else 0, tax_item or '')
            result[key] = dict(capacity=capacity, line=line, tax_component_id=physical, tax_item_id=tax_item,
                ar=ar, recognition=rec, semantic=dict(account_id=rec['account_id'],
                    net_minor_units=line['net_minor_units'], tax=tax))
    allocations, inverse = c.application_allocations, alias(c.application_allocations, 'inverse')
    live = known['live'] if known else s.company.conn.execute(sa.select(allocations).where(allocations.c.target_transaction_id == header['id'],
        allocations.c.kind == 'allocation', ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_allocation_id == allocations.c.id)))).mappings()
    for row in live:
        key = calc.ComponentKey(row['target_ordinal'], int(row['logical_kind'] == 'tax'), row['tax_item_id'] or '')
        if key not in result:
            raise _invalid('invoice', 'live settlement has no current logical capacity')
        result[key]['capacity'] -= row['amount_minor_units']
    if any(value['capacity'] < 0 for value in result.values()):
        raise _invalid('invoice', 'stored settlement exceeds component capacity')
    return result


def discount_rows(s, payment_id):
    """Every early-payment discount this receipt took, by the application it was taken on."""
    known = prefetch.receipt(s, payment_id)
    if known is not None:
        return {key: dict(row) for key, row in known['discounts'].items()}
    return {row['application_id']: row for row in effects.rows(
        s, c.payment_discounts, c.payment_discounts.c.transaction_id == payment_id)}


def discounts_by_invoice(s, invoice_ids):
    """Discount still standing on each invoice: taken on an edge nothing has unapplied."""
    identifiers = list(invoice_ids)
    totals = {identifier: 0 for identifier in identifiers}
    if not identifiers:
        return totals
    d, a = c.payment_discounts, c.applications
    inverse = alias(a, 'inverse')
    for row in s.company.conn.execute(sa.select(d.c.invoice_id, sa.func.sum(d.c.amount_minor_units).label('net'))
            .select_from(d.join(a, a.c.id == d.c.application_id))
            .where(d.c.invoice_id.in_(identifiers),
                   ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == a.c.id)))
            .group_by(d.c.invoice_id)).mappings():
        totals[row['invoice_id']] = row['net']
    return totals


def _discounts(s, inp, targets, currency, context_):
    """Each named discount, bound to the settlement edge of the same invoice.

    The invoice is settled by the cash applied to it plus the discount, and the two together
    cannot exceed what is due. An invoice the receipt gives no cash -- the anchor's short-paid
    customer who takes the discount on another invoice -- is settled by the discount alone, on an
    edge of its own; it has to be the payer's family's invoice, as any application does, and
    its expected version is required because nothing else in the request names it.
    """
    by_invoice = {row['facts']['header']['id']: row for row in targets}
    for index, entry in enumerate(inp.discounts):
        field = f'discounts.{index}'
        header = sales.resolve(s, entry.invoice, SETTLEABLE)
        target = by_invoice.get(header['id'])
        if entry.expected_version is not None:
            sales._version(s, header, entry.expected_version)
        if target is None:
            if entry.expected_version is None:
                raise _invalid(field + '.expected_version', f'{header["type"].replace("_", " ")} {header["number"]} '
                               'receives no cash in this receipt, so its discount needs the invoice expected_version')
            facts = query.invoice_facts(s, header['id'], write=True)
            selection.compatible(s, context_, facts)
            target = dict(facts=facts, amount=0)
            targets.append(target)
            by_invoice[header['id']] = target
        if target.get('discount'):
            raise _invalid(field + '.invoice', 'name each discounted invoice once')
        units = money(entry.amount, currency, field + '.amount').minor_units
        if units <= 0:
            raise _invalid(field + '.amount', 'a discount must be more than zero')
        facts = target['facts']
        _capacity('target', target['amount'] + units, facts['due'], currency, header['id'])
        target['discount'] = units
    standing = discounts_by_invoice(s, [target['facts']['header']['id'] for target in targets])
    for target in targets:
        facts = target['facts']
        target.setdefault('discount', 0)
        target['terms'] = early.invoice_terms(facts['profile']['profile_snapshot'])
        already = standing[facts['header']['id']]
        target['suggested'] = early.suggested(target['terms'], facts['gross'], inp.date,
                                              taken=already, due=facts['due'])
    return targets


def _method_names(s):
    """The active payment methods by name, for an error that has to say which ones there are."""
    table = c.payment_methods
    return [row.name for row in s.company.conn.execute(
        sa.select(table.c.name).where(table.c.active.is_(True)).order_by(table.c.name))]


def _profile(s, inp, context_, *, write_off=False):
    # A write-off is the one receipt that may name an inactive customer: the books are being
    # cleaned of a debt the customer will never pay, and reactivating the name just to do it
    # would leave a trace in the customer list that means nothing.
    payer = defaults._row(s.company, 'customer', context_['customer_id'], active=not write_off)
    from bookflow.company.parties import project_party_record
    projected = project_party_record(s.company, 'customer', payer, custom_values=())
    method = inp.payment_method or projected.get('effective_preferred_payment_method_id')
    if method is None and write_off:
        # No cash changes hands, so no method of payment is being used: the list's own "Other".
        method = s.company.conn.execute(sa.select(c.payment_methods.c.id).where(
            c.payment_methods.c.kind == 'other', c.payment_methods.c.active.is_(True))
            .order_by(c.payment_methods.c.name)).scalars().first()
    if method is None:
        names = _method_names(s)
        error = _invalid('payment_method', 'give payment_method, one of: ' + ', '.join(names)
                         + ' (`payment-method list` shows them), or set a preferred payment method on the customer')
        error.details['allowed'] = names
        raise error
    try:
        method = defaults._row(s.company, 'payment_method', method)
    except BookflowError as exc:
        if exc.code in {'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_AMBIGUOUS_REFERENCE', 'E_VALIDATION'}:
            exc.details.setdefault('field', 'payment_method')
            exc.details['allowed'] = _method_names(s)
            exc.details['hint'] = 'payment_method is one of allowed, by name or id; `payment-method list` shows them.'
        raise
    info = defaults._info(s.company)
    destination = inp.deposit_to
    if destination is None and info['use_undeposited_funds_for_payments']:
        destination = s.company.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.system_role == 'undeposited_funds')).scalar_one_or_none()
    account = defaults._account(s.company, destination, 'deposit_to', {'bank', 'other_current_asset'})
    raw_account = defaults._row(s.company, 'account', account.id)
    if account.type != 'bank' and raw_account['system_role'] != 'undeposited_funds':
        raise _invalid('deposit_to', 'select a bank or system Undeposited Funds account')
    return PaymentProfileOutput(payer=defaults._ref(payer),
        lineage=[defaults._ref(row) for row in list_service.hierarchy_ancestors(s.company, c.customers, payer)],
        billing_address={key: value for key, value in projected.items() if key.startswith('billing_address_') and (value is None or isinstance(value, str))},
        ar_account=defaults._account(s.company, context_['ar_account_id'], 'ar_account', {'accounts_receivable'}),
        deposit_account=account, payment_method=defaults._ref(method),
        preferences={field: bool(info[field]) for field in PREFERENCES})


def repeated_reference(s, customer_id, reference):
    """Warnings for this customer's live receipts that carry the same check reference.

    Not a refusal: two checks can share a number. The person or agent sees the receipt
    already on file and decides. Voided and deleted receipts are not live and never match.
    """
    key = (reference or '').strip().casefold()
    if not key:
        return []
    t, p, r = c.transactions, c.payment_profiles, c.transaction_revisions
    rows = s.company.conn.execute(sa.select(t.c.id, t.c.number, r.c.date, r.c.total_minor_units, r.c.currency,
                                            p.c.reference)
        .join(p, p.c.revision_id == t.c.current_revision_id).join(r, r.c.id == t.c.current_revision_id)
        .where(t.c.type == 'payment', t.c.status == 'posted', p.c.payer_id == customer_id,
               p.c.reference.is_not(None))
        .order_by(r.c.date, t.c.number)).mappings().all()
    rows = [row for row in rows if row['reference'].strip().casefold() == key]
    return [f"reference: this customer already has receipt {row['number']} (payment {row['id']}) dated {row['date']} "
            f"for {Money(row['total_minor_units'], row['currency'])} with reference {row['reference']!r}; "
            f"make sure this is not the same check recorded twice" for row in rows]


def _write_off_intent(inp, ctx):
    """A receipt for 0.00 is a write-off, and says so: nothing else makes a zero receipt meaningful."""
    from bookflow.core.errors import require_reason
    if not inp.discounts:
        raise _invalid('amount', 'cash received must be positive; to write off a bad debt without cash '
                       'receive 0.00 and list the invoices written off in `discounts`, with `discount_account` '
                       'an expense account such as Bad Debt')
    if inp.applications.mode != 'inline' or inp.applications.items:
        raise _invalid('applications', 'a write-off receives no cash, so it applies none; name each invoice '
                       'written off in `discounts` with its expected_version')
    if inp.discount_account is None:
        raise _invalid('discount_account', 'a write-off names the expense account the balance is written off '
                       'to, such as Bad Debt')
    require_reason(ctx.reason)


def _write_off_account(account, targets, inp):
    """The balance written off goes to an expense: anything else is a discount, not a bad debt."""
    if account.type not in WRITE_OFF_ACCOUNT_TYPES:
        raise _invalid('discount_account', f'"{account.full_name}" is a {account.type.replace("_", " ")} account; '
                       'a write-off with no cash is taken to an expense account such as Bad Debt')


def prepare(s, ctx, inp, operation):
    from bookflow.hub.access import one_authorization
    with one_authorization(s), prefetch.reading(s):
        return _prepare(s, ctx, inp, operation)


def _prepare(s, ctx, inp, operation):
    command = 'payment ' + operation
    # Each party's row and ancestry is read once for this preparation, however many of its
    # steps name the party (a receipt applied to hundreds of jobs names each one three times).
    customers, lineages, inactive_ok = {}, {}, []
    merged_away = {row[0] for row in s.company.raw.execute(merged_sql('customer'))}
    def customer(party_id, active=True):
        # A write-off may name a customer who has since been made inactive (see `_profile`).
        active = active and not inactive_ok
        # A customer merged into the payer's family is paid through its survivor although the
        # merge made it inactive (party_merges.py); its old invoices stay payable.
        active = active and party_id not in merged_away
        if not active and (party_id, True) in customers:
            return customers[(party_id, True)]
        if (party_id, active) not in customers:
            customers[(party_id, active)] = defaults._row(s.company, 'customer', party_id, active=active)
        return customers[(party_id, active)]
    def lineage(party_id, row):
        if party_id not in lineages:
            lineages[party_id] = party_lineage(s, row)
        return lineages[party_id]
    if operations.find(s, inp.operation_key):
        recovered = operations.recover(inp, ctx, s, command)
        if recovered:
            return Plan(recovered.output, dict(recovered=True))
        raise BookflowError('E_PAYMENT_OPERATION_KEY_REUSED')
    # A retained deleted receipt is history: no new effect may reach it. Replaying
    # an earlier key still returns its own original result, above.
    if operation != 'receive':
        from bookflow.company.payment_deletions import require_not_deleted
        require_not_deleted(s, sales.resolve(s, inp.payment, 'payment')['id'])
    if operation in ('unapply', 'void'):
        from bookflow.company.payment_cancellation import prepare as cancellation
        return cancellation(s, ctx, inp, operation)
    if operation == 'update':
        from bookflow.company.payment_corrections import prepare as correction
        return correction(s, ctx, inp)
    at, event, operation_id = clock.now_iso(), new_id(), new_id()
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    audited = lambda: dict(**created(), audit_event_id=event)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    previous, funding, custom_plan, sequence = None, None, None, None
    if operation == 'receive':
        amount = money(inp.amount, defaults._info(s.company)['home_currency'], 'amount').minor_units
        if amount < 0:
            raise _invalid('amount', 'cash received cannot be negative')
        write_off = amount == 0
        if write_off:
            inactive_ok.append(True)
            # The anchor's Receive Payments for 0.00: no cash, and the open balance taken as a
            # discount to an expense account. Stated first so every later step can allow an
            # inactive customer and a missing method.
            _write_off_intent(inp, ctx)
        context_ = selection.context(s, PaymentContext(mode='new_receipt', customer=inp.customer,
            ar_account=inp.ar_account, date=inp.date), payer_active=not write_off)
        profile = _profile(s, inp, context_, write_off=write_off)
        repeated = repeated_reference(s, context_['customer_id'], inp.reference)
        targets, selected = _applications(s, inp, context_, amount=amount)
        _capacity('source', sum(row['amount'] for row in targets), amount, context_['currency'], context_['customer_id'])
        targets = _discounts(s, inp, targets, context_['currency'], context_)
        discounted = sum(row['discount'] for row in targets)
        discount_account = account_mutation = None
        if discounted:
            facts_, account_mutation = early.resolve_account(s, ctx, 'customer', inp.discount_account, at=at)
            discount_account = defaults.Account(**facts_)
        if write_off:
            _write_off_account(discount_account, targets, inp)
        # A discount is capacity beside the cash: the component of the invoice's customer carries
        # both, which is what lets every open-balance reader settle the invoice unchanged.
        components = calc.receipt_components(context_['customer_id'], amount + discounted,
            [(row['facts']['profile']['customer_id'], row['amount'] + row['discount']) for row in targets])
        party_discounts = {}
        for row in targets:
            if row['discount']:
                party = row['facts']['profile']['customer_id']
                party_discounts[party] = party_discounts.get(party, 0) + row['discount']
        header = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type='payment', status='posted',
            voided_at=None, voided_by=None, void_reason=None, void_posting_batch_id=None)
        number, sequence = effects.allocate(s, 'payment', inp.number)
        custom.validate_kinds(s.company, inp.custom_fields, inp.expected_custom_field_kinds, record_type='payment')
        custom_plan = custom.prepare(s.company, header['id'], inp.custom_fields, {}, creating=True, record_type='payment')
        info = defaults._info(s.company)
        issuer = {key: value for key, value in info.items() if key in ('id', 'legal_name', 'home_currency') or key.startswith(('address_', 'legal_address_'))}
        revision = dict(**created(), transaction_id=header['id'], revision_number=1, supersedes_revision_id=None,
            date=inp.date, number=number, name_type='customer', name_id=context_['customer_id'], memo=inp.memo,
            total_minor_units=amount, currency=context_['currency'], issuer_snapshot=query.canonical(issuer),
            custom_fields_snapshot=query.canonical(custom_plan.snapshot), audit_event_id=event)
        header.update(number=number, current_revision_id=revision['id'])
        pending['transaction_revisions'].append(revision)
        identity = dict(**created(), transaction_id=header['id'])
        pending['document_line_identities'].append(identity)
        line = dict(**created(), transaction_id=header['id'], revision_id=revision['id'], line_id=identity['id'],
            position=1, kind='payment', account_id=None, side=None, amount_minor_units=None,
            currency=context_['currency'], account_snapshot=None, name_type='customer', name_id=context_['customer_id'],
            party_name=profile.payer.label, class_id=None, class_name=None, description=inp.memo, **dict.fromkeys(journals.FACTS))
        pending['document_lines'].append(line)
        provenance = {key: value for key, value in audited().items() if key != 'id'}
        pending['payment_profiles'].append(dict(revision_id=revision['id'], transaction_id=header['id'], type='payment',
            payer_id=context_['customer_id'], ar_account_id=context_['ar_account_id'], deposit_account_id=profile.deposit_account.id,
            payment_method_id=profile.payment_method.id, reference=inp.reference, profile_snapshot=query.canonical(profile.model_dump()), **provenance))
        batch = dict(**audited(), transaction_id=header['id'], revision_id=revision['id'], kind='original', effective_date=inp.date,
                     reverses_batch_id=None, replaces_batch_id=None)
        pending['posting_batches'].append(batch)
        def leg(account, units, debit, party, line_no):
            value = dict(**created(), transaction_id=header['id'], batch_id=batch['id'], line_no=line_no,
                account_id=account.id, account_snapshot=query.canonical(account.model_dump()),
                debit_minor_units=units if debit else 0, credit_minor_units=0 if debit else units,
                currency=context_['currency'], name_type='customer', name_id=party['id'], party_name=party['full_name'],
                class_id=None, class_name=None, description=inp.memo, reversed_line_id=None, **dict.fromkeys(journals.FACTS))
            pending['posting_lines'].append(value)
            return value
        payer = customer(context_['customer_id'])
        # A write-off puts no cash in: there is no cash leg, and the lines count from the first
        # receivable credit.
        cash = leg(profile.deposit_account, amount, True, payer, 1) if amount else None
        first = 2 if amount else 1
        keys, component_rows, source_rows = {}, {}, {}
        # One discount debit per customer whose invoice took one, after the receivable credits.
        discount_legs = {party_id: leg(discount_account, units, True, customer(party_id),
                                       len(components) + first + position)
                         for position, (party_id, units) in enumerate(sorted(party_discounts.items()))}
        for position, (party_id, capacity) in enumerate(sorted(components.items()), first):
            party = customer(party_id)
            key = dict(**audited(), transaction_id=header['id'], line_id=identity['id'], party_id=party_id,
                       ar_account_id=context_['ar_account_id'], currency=context_['currency'])
            pending['payment_component_keys'].append(key)
            component = dict(**audited(), transaction_id=header['id'], revision_id=revision['id'], document_line_id=line['id'],
                component_key_id=key['id'], amount_minor_units=capacity, currency=context_['currency'],
                component_snapshot=query.canonical(dict(party=defaults._ref(party).model_dump(), ar_account=profile.ar_account.model_dump(),
                                                       lineage=lineage(party_id, party))))
            pending['payment_components'].append(component)
            ar = leg(profile.ar_account, capacity, False, party, position)
            discount = party_discounts.get(party_id, 0)
            # A customer whose invoices this receipt settles only by discount puts no cash in.
            shares = [(cash, capacity - discount)] if capacity > discount else []
            shares.append((ar, capacity))
            if discount:
                shares.append((discount_legs[party_id], discount))
            for posting, units in shares:
                source = dict(**created(), transaction_id=header['id'], posting_line_id=posting['id'], revision_id=revision['id'],
                    document_line_id=line['id'], amount_minor_units=units, currency=context_['currency'],
                    reversed_source_id=None, tax_component_id=None, payment_component_id=component['id'])
                pending['posting_line_sources'].append(source)
                if posting is ar:
                    source_rows[party_id] = source
            keys[party_id], component_rows[party_id] = key, component
        available = dict(components)
    else:
        funding = query.payment_facts(s, inp.payment, write=True)
        previous, revision = funding['header'], funding['revision']
        from bookflow.company.payment_dependencies import payment_version
        payment_version(s, previous, inp.expected_version)
        if previous['status'] != 'posted':
            raise BookflowError('E_APPLICATION_INACTIVE')
        header = dict(previous, version=previous['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        context_ = selection.context(s, PaymentContext(mode='existing_credit', payment=header['id'], date=inp.date))
        if inp.date < revision['date']:
            raise _invalid('date', 'application cannot precede receipt date')
        targets, selected = _applications(s, inp, context_)
        if not targets:
            raise _invalid('applications', 'select at least one invoice')
        for target in targets:
            target.update(discount=0, terms=None, suggested=None)
        discount_account = account_mutation = None
        keys = {key['party_id']: key for key in funding['keys'].values()}
        component_rows = {funding['keys'][row['component_key_id']]['party_id']: row for row in funding['components']}
        available = {funding['keys'][key]['party_id']: value for key, value in funding['available'].items()}
        components = {party: row['amount_minor_units'] for party, row in component_rows.items()}
        source_rows = {}
        for party, component in component_rows.items():
            source, leg_ = c.posting_line_sources, c.posting_lines
            matches = s.company.conn.execute(sa.select(source).join(leg_, leg_.c.id == source.c.posting_line_id).where(
                source.c.payment_component_id == component['id'], source.c.reversed_source_id.is_(None),
                leg_.c.account_id == context_['ar_account_id'], leg_.c.credit_minor_units > 0)).mappings().all()
            if len(matches) != 1:
                raise _invalid('payment', 'ambiguous payment source attribution')
            source_rows[party] = dict(matches[0])
    journals.open_dates(s, [inp.date])
    changed_headers, app_outputs, allocation_outputs, changes, recipes = [], [], [], [], []
    pending_discounts, warnings = [], []
    settled = []
    for target in targets:
        facts, units = target['facts'], target['amount'] + target['discount']
        invoice, invoice_revision = facts['header'], facts['revision']
        party = facts['profile']['customer_id']
        _capacity('source', units, available.get(party, 0), context_['currency'], header['id'])
        available[party] -= units
        capacities = _target_components(s, facts, pending, created, event)
        split = calc.allocate(units, {key: value['capacity'] for key, value in capacities.items()})
        app = dict(**audited(), kind='apply', paying_transaction_id=header['id'], paid_transaction_id=invoice['id'],
            source_component_key_id=keys[party]['id'], amount_minor_units=units, currency=context_['currency'],
            effective_date=inp.date, reverses_application_id=None)
        pending['applications'].append(app)
        changed = dict(invoice, version=invoice['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        changed_headers.append((invoice, changed))
        discount_view = {}
        if target['terms'] is not None and (target['terms'].percent_millionths is not None or target['discount']):
            discount_view = dict(suggested_discount=Money(target['suggested'], context_['currency']).to_dict(),
                                 discount_date=target['terms'].discount_date)
        if target['discount']:
            terms = target['terms']
            pending_discounts.append(dict(**audited(), transaction_id=header['id'], component_key_id=keys[party]['id'],
                application_id=app['id'], invoice_id=invoice['id'], discount_account_id=discount_account.id,
                amount_minor_units=target['discount'], currency=context_['currency'],
                suggested_minor_units=target['suggested'], discount_date=terms.discount_date,
                terms_percent_millionths=terms.percent_millionths))
            discount_view['discount'] = Money(target['discount'], context_['currency']).to_dict()
            late = early.late_warning(invoice['type'].replace('_', ' '), invoice['number'], terms, inp.date)
            if late:
                warnings.append(late)
        app_outputs.append(dict(application_id=app['id'], invoice_id=invoice['id'], invoice_version=changed['version'],
            source_component_key_id=keys[party]['id'], party_id=party, amount=Money(units, context_['currency']).to_dict(), effective_date=inp.date,
            **discount_view))
        settled.append(dict(document_id=invoice['id'], document_type=invoice['type'], number=invoice['number'],
            applied=target['amount'], discount=target['discount'], still_due=facts['due'] - units))
        changes.append(dict(invoice_id=invoice['id'], version=changed['version'], revision_id=invoice_revision['id'],
            gross_minor_units=facts['gross'], applied_minor_units=facts['applied'] + units, due_minor_units=facts['due'] - units,
            currency=context_['currency'], status='paid' if facts['due'] == units else 'partial'))
        for key, allocated in split.items():
            component = capacities[key]
            allocation = dict(**audited(), application_id=app['id'], kind='allocation', reverses_allocation_id=None,
                source_transaction_id=header['id'], source_revision_id=revision['id'], source_component_id=component_rows[party]['id'],
                source_posting_source_id=source_rows[party]['id'], target_transaction_id=invoice['id'],
                target_revision_id=invoice_revision['id'], target_document_line_id=component['line']['id'],
                target_line_id=component['line']['line_id'], target_ordinal=key.ordinal, logical_kind='tax' if key.kind else 'net',
                tax_item_id=component['tax_item_id'], tax_component_id=component['tax_component_id'],
                target_ar_source_id=component['ar']['id'], target_recognition_source_id=component['recognition']['id'],
                recognition_role='tax_liability' if key.kind else 'sales_net', amount_minor_units=allocated,
                currency=context_['currency'], effective_date=inp.date, facts_snapshot=query.canonical(component['semantic']))
            pending['application_allocations'].append(allocation)
            allocation_outputs.append(dict(allocation_id=allocation['id'], application_id=app['id'], invoice_id=invoice['id'],
                target_ordinal=key.ordinal, logical_kind=allocation['logical_kind'], tax_item_id=component['tax_item_id'],
                amount=Money(allocated, context_['currency']).to_dict()))
        recipes.append([invoice['id'], invoice['version'], invoice_revision['id'], units, target['discount'],
            used_reference_facts(lineage(party, customer(party))),
            [(key.ordinal, key.kind, key.tax_item_id, value['capacity'], value['semantic'], split.get(key, 0)) for key, value in sorted(capacities.items())]])
    component_outputs = []
    for party, key in sorted(keys.items()):
        capacity = components.get(party, 0)
        party_row = customer(party, False)
        component_outputs.append(dict(component_key_id=key['id'], component_id=component_rows[party]['id'] if party in component_rows else None,
            party_id=party, party_name=party_row['full_name'], ar_account_id=context_['ar_account_id'], currency=context_['currency'],
            received_minor_units=capacity, applied_minor_units=capacity - available.get(party, 0), available_minor_units=available.get(party, 0)))
    profile_dependencies = None
    if operation == 'receive':
        profile_dependencies = used_reference_facts(profile.model_dump(exclude={'preferences'}))
        if inp.deposit_to is None:
            profile_dependencies['use_undeposited_funds_for_payments'] = profile.preferences.use_undeposited_funds_for_payments
    financial_context = {key: context_[key] for key in ('mode', 'customer_id', 'ar_account_id', 'payment_id', 'date', 'currency')}
    fp = query.digest([operations.facts_request(inp, ctx, s, command), financial_context, recipes,
        early.account_identity(discount_account.model_dump(), account_mutation) if discount_account else None,
        [header['number'], revision['date'], revision['total_minor_units'], revision['memo']],
        profile_dependencies if operation == 'receive' else [previous['id'], previous['version'], funding['available']],
        selected['revision']['manifest_hash'] if selected else None, components,
        sales._custom_semantic(custom_plan.snapshot) if custom_plan else None,
        defaults._info(s.company)['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fp:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts', 'current_facts_fingerprint': fp})
    current = dict(payment_id=header['id'], version=header['version'], revision_id=revision['id'], status='posted',
        received_minor_units=revision['total_minor_units'], effective_received_minor_units=revision['total_minor_units'],
        applied_minor_units=sum(row['applied_minor_units'] for row in component_outputs),
        available_minor_units=sum(available.values()), currency=context_['currency'], components=component_outputs, component_count=len(component_outputs),
        discount_minor_units=sum(row['amount_minor_units'] for row in pending_discounts) if operation == 'receive'
            else sum(row['amount_minor_units'] for row in discount_rows(s, header['id']).values()))
    effect = dict(kind=operation, financial_changed=True, operation_id=operation_id, payment_id=header['id'],
        audit_event_id=event, before_header=effect_header(previous, revision), after_header=effect_header(header, revision),
        preferences=profile.preferences.model_dump() if operation == 'receive' else json.loads(funding['profile']['profile_snapshot'])['preferences'],
        source_components=component_outputs, applications=app_outputs, allocations=allocation_outputs, document_changes=changes)
    payer_label = defaults._row(s.company, 'customer', context_['customer_id'], active=False)['full_name']
    applied_cash = Money(sum(row['applied'] for row in settled), context_['currency'])
    opening = (f"Received {Money(revision['total_minor_units'], context_['currency'])} from {payer_label}."
               if operation == 'receive' else f"Applied {applied_cash} of {payer_label}'s credit.")
    if operation == 'receive' and not revision['total_minor_units']:
        opening = (f'No cash received from {payer_label}; the balance is written off to {discount_account.full_name}. '
                   'Nothing goes to the bank, and the sales tax on the invoices is unchanged.')
    elif operation == 'receive':
        opening += ' ' + _where_the_money_went(s, profile.deposit_account)
    summary = summarize(settled, context_['currency'], opening=opening, credit=current['available_minor_units'],
                        party_label=payer_label,
                        **({'discount_label': 'Written off'} if operation == 'receive' and not revision['total_minor_units'] else {}))
    output = PaymentWriteOutput(id=header['id'], version=header['version'], operation_key=inp.operation_key,
        facts_fingerprint=fp, effect=effect, current=current, summary=summary,
        warnings=(repeated + warnings) if operation == 'receive' else [],
        effect_counts={key: len(effect[key]) for key in ('source_components', 'applications', 'allocations', 'document_changes')})
    return Plan(output, dict(input=inp, operation=operation, header=header, before=previous, pending=pending,
        changed_headers=changed_headers, event=event, operation_id=operation_id, selected=selected,
        custom_plan=custom_plan, sequence=sequence, context=context_, targets=targets, fingerprint=fp,
        discounts=pending_discounts, account_mutation=account_mutation))


def apply(plan, ctx, s):
    if plan.data.get('recovered'):
        return Applied(plan.preview, [], 'recovered payment operation')
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    if fresh.data.get('recovered'):
        return Applied(fresh.preview, [], 'recovered payment operation')
    if fresh.data['operation'] in ('update','void') and fresh.preview.changed:
        from bookflow.company.deposit_dependencies import require_unclaimed
        require_unclaimed(s, fresh.data['header']['id'])
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts'})
    if fresh.data['operation'] in ('unapply', 'void'):
        from bookflow.company.payment_cancellation import validate
    elif fresh.data['operation'] == 'update':
        from bookflow.company.payment_corrections import validate
    else:
        from bookflow.company.payment_validation import validate
    validate(fresh, s, ctx)
    data = fresh.data
    header, before, pending = data['header'], data['before'], data['pending']
    complete_effect = fresh.preview.effect.model_dump(mode='json')
    for kind in ('source_components', 'applications', 'allocations', 'document_changes'):
        setattr(fresh.preview.effect, kind, getattr(fresh.preview.effect, kind)[:50])
    fresh.preview.current.components = fresh.preview.current.components[:50]
    touched = [] if header == before else [Touched('transaction', header['id'], 'update' if before else 'create',
        before['version'] if before else None, header['version'], header, before, db='company')]
    touched.extend(Touched('transaction', after['id'], 'update', old['version'], after['version'], after, old, db='company')
                   for old, after in data['changed_headers'])
    for table, kind, key in TABLE_KINDS:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company') for row in pending[table])
    if data['custom_plan']:
        touched.extend(custom.touches(data['custom_plan']))
    touched.extend(Touched('payment_discount', row['id'], 'create', None, 1, effects.decoded(row), db='company')
                   for row in data.get('discounts', ()))
    touched[:0] = early.created_account_touches(data.get('account_mutation'))
    operation = dict(id=data['operation_id'], operation_key=data['input'].operation_key, command='payment ' + data['operation'],
        request_schema_version=1, request_hash=operations.request_hash(data['input'], ctx, s, 'payment ' + data['operation']),
        request_snapshot=query.canonical(dict(original_request=operations.original_request(data['input'], ctx, s, 'payment ' + data['operation']),
            resolved_transaction_ids=[header['id'], *(after['id'] for _, after in data['changed_headers'])],
            expanded_selection_hash=data['selected']['revision']['manifest_hash'] if data['selected'] else None)),
        # The recorded effect omits advice about other receipts, so an exact replay of this
        # operation returns its effect alone rather than warning about itself.
        effect_snapshot=query.canonical(fresh.preview.model_copy(update={'warnings': []}).model_dump(mode='json')),
        execution_snapshot=query.canonical(dict(actor_id=s.actor.id, interface=ctx.interface.value,
            on_behalf_of=ctx.on_behalf_of, reason=ctx.reason, directive_id=ctx.directive_id, directive_code=getattr(s, 'directive_code', None))),
        created_at=data.get('at', header['updated_at']), created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=data['event'])
    touched.append(Touched('payment_operation', operation['id'], 'create', None, 1, effects.decoded(operation), db='company'))
    operation_items = []
    collections = {('effect_applications' if kind == 'applications' else kind): complete_effect[kind]
                   for kind in ('source_components', 'applications', 'allocations', 'document_changes')}
    collections['request_applications'] = [dict(invoice=row['facts']['header']['id'],
        expected_version=row['facts']['header']['version'], amount=Money(row['amount'], data['context']['currency']).to_dict())
        for row in data['targets'] if row['amount']]
    for kind, values in collections.items():
        for ordinal, value in enumerate(values, 1):
            item = dict(id=new_id(), operation_id=operation['id'], kind=kind, ordinal=ordinal,
                item_snapshot=query.canonical(value), created_at=operation['created_at'], created_by=s.actor.id,
                created_via=ctx.interface.value, audit_event_id=data['event'])
            operation_items.append(item)
            touched.append(Touched('payment_operation_item', item['id'], 'create', None, 1, effects.decoded(item), db='company'))
    selected_header = None
    if data['selected']:
        old = data['selected']['header']
        selected_header = dict(old, state='consumed', consumed_operation_id=operation['id'],
            updated_at=header['updated_at'], updated_by=s.actor.id, updated_via=ctx.interface.value)
        touched.append(Touched('payment_selection', old['id'], 'update', old['version'], old['version'], selected_header, old, db='company'))
    audit.write_event_to(s.company, ctx, operation['command'], f"{data['operation']} payment {header['number']}", touched,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if before and header != before:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == header['id']).values(**header))
    elif not before:
        s.company.conn.execute(c.transactions.insert().values(**header))
    for _, after in data['changed_headers']:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == after['id']).values(**after))
    # A discount account the chart did not have yet exists before the leg that posts to it.
    early.persist_created_account(s, data.get('account_mutation'))
    for table, _, _ in TABLE_KINDS:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    if data.get('discounts'):
        s.company.conn.execute(c.payment_discounts.insert(), data['discounts'])
    if data['custom_plan']:
        custom.apply(s.company, data['custom_plan'])
    s.company.conn.execute(c.payment_operations.insert().values(**operation))
    if operation_items:
        s.company.conn.execute(c.payment_operation_items.insert(), operation_items)
    if selected_header:
        s.company.conn.execute(c.payment_selections.update().where(c.payment_selections.c.id == selected_header['id']).values(**selected_header))
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(fresh.preview, touched, operation['command'], audited=True)


def coordinate_rows_and_touches(plan):
    """Closed keyless correction/cancellation rows; no operation/selection DML."""
    from bookflow.company.deposit_coordination import source_identity_map
    source_identity_map(plan, payment=True)
    data = plan.data
    if data['operation'] not in ('update', 'void', 'all_active_void') or data.get('selected') is not None:
        raise BookflowError('E_INTERNAL')
    return tuple((table, tuple(data['pending'][table]), tuple(
        Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company')
        for row in data['pending'][table])) for table, kind, key in TABLE_KINDS)


def _where_the_money_went(s, account):
    """The receipt's own sentence about where the money now sits, as QuickBooks' Deposit To says it."""
    role = s.company.conn.execute(sa.select(c.accounts.c.system_role).where(c.accounts.c.id == account.id)).scalar_one_or_none()
    if role == 'undeposited_funds':
        return ('It is held in Undeposited Funds until you record the bank deposit with `deposit post`; '
                'the bank balance does not include it yet.')
    return f'It was deposited to {account.full_name}.'
