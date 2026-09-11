"""Applying a credit memo to an invoice, and taking the application back.

**No postings at all.** A credit memo already moved the money: it debited the income the sale
recognised and credited the customer's receivable. Applying it to an invoice moves nothing --
the receivable is already down by the credit and the invoice is already up by its gross, and
what an application changes is which of the two a reader sees them against. So this module
writes settlement rows and never a posting line, and the trial balance is identical before and
after.

**The same edge a receipt uses.** An application is an ``applications`` row with an
``application_allocations`` row per invoice component, exactly as a cash receipt writes one,
with the credit source named in ``credit_source_key_id`` / ``credit_source_component_id``
instead of the receipt columns. That is deliberate and it is the whole reason this was not
given a table of its own: ``invoice_facts`` already subtracts this edge from an invoice's
gross, ``sales.prepare`` already refuses to void an invoice that has one, ``payment_restatement``
already restates it when the invoice is corrected, and ``payments._target_components`` already
subtracts its allocations from what a later cash payment may relieve. Twenty owners read that
edge; a second table would have had to be added to all of them.

**One application per credit component.** A credit's capacity is one component per credited
line, and an application names exactly one of them, drawing the components down in order. A
request that spans two lines therefore writes two application rows against the same invoice --
which is what keeps the invoice-side rule intact that one application has exactly one
allocation per target component, the rule ``payment_restatement`` validates against.

**Exact party.** A credit from one customer settles that customer's invoices, on the same
receivable account, in the same currency, dated on or after the credit. Moving credit between
a parent and a job needs a balanced transfer document, which this release does not have.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import credits, journals, sales, schema as c
from bookflow.company import document_effects as effects
from bookflow.company import payment_calculations as calc
from bookflow.company import payment_queries as query
from bookflow.company import payments
from bookflow.company.credit_settlement_models import CreditSettlementOutput
from bookflow.company.payment_cancellation import live_allocations
from bookflow.company.sales_models import money, _invalid
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched

TABLE_KINDS = (
    ('settlement_line_keys', 'settlement_line_key', 'id'),
    ('applications', 'application', 'id'),
    ('application_allocations', 'application_allocation', 'id'),
)


def _capacity(side, requested, available, currency, identifier):
    if requested > available:
        raise BookflowError('E_APPLICATION_CAPACITY', details=dict(
            side=side, record_id=identifier, requested=Money(requested, currency).to_dict(),
            available=Money(available, currency).to_dict()))


def _compatible(s, key, invoice):
    """Exact customer, exact receivable, exact currency -- the trigger's rule, said early."""
    profile, revision = invoice['profile'], invoice['revision']
    if (profile['customer_id'] != key['party_id']
            or profile['control_account_id'] != key['ar_account_id']
            or revision['currency'] != key['currency']):
        raise BookflowError('E_APPLICATION_INCOMPATIBLE', details={
            'invoice_id': invoice['header']['id'], 'credit_party_id': key['party_id'],
            'invoice_party_id': profile['customer_id'],
            'credit_ar_account_id': key['ar_account_id'],
            'invoice_ar_account_id': profile['control_account_id'],
            'next': 'Apply a credit to an invoice for the same customer, receivable account and '
                    'currency; a credit cannot be moved between a parent and a job.'})


def _settlement_change(invoice, applied, currency):
    gross, header = invoice['gross'], invoice['header']
    due = gross - applied
    return dict(invoice_id=header['id'], invoice_number=header['number'],
                version=header['version'], revision_id=invoice['revision']['id'],
                gross_minor_units=gross, applied_minor_units=applied, due_minor_units=due,
                currency=currency, gross=Money(gross, currency).to_dict(),
                applied=Money(applied, currency).to_dict(), due=Money(due, currency).to_dict(),
                status=('voided' if header['status'] == 'voided' else
                        'paid' if due == 0 else 'partial' if applied else 'unpaid'))


# ---------------------------------------------------------------- apply


def _draw(remaining, components, wanted):
    """Take `wanted` from this credit's components in order; never more than one holds."""
    taken = []
    for component in components:
        if wanted <= 0:
            break
        share = min(wanted, remaining.get(component['id'], 0))
        if share <= 0:
            continue
        taken.append((component, share))
        remaining[component['id']] -= share
        wanted -= share
    if wanted > 0:  # pragma: no cover - the key-level capacity check runs first
        raise BookflowError('E_INTERNAL', message='Credit capacity is not attributable.')
    return taken


def prepare_apply(s, ctx, inp):
    source = credits.facts(s, inp.credit_memo, write=True)
    old_header, revision, key = source['header'], source['revision'], source['key']
    journals.version_meta(s, old_header, inp.expected_version)
    if old_header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'credit_memo_id': old_header['id'], 'status': old_header['status'],
            'next': 'A voided credit memo is worth nothing and applies to nothing.'})
    date = inp.date or revision['date']
    if date < revision['date']:
        raise _invalid('date', 'an application cannot be dated before the credit memo')
    currency = source['currency']
    journals.open_dates(s, [date])
    at, event = clock.now_iso(), new_id()
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    audited = lambda: dict(**created(), audit_event_id=event)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    remaining_key = source['available']
    if remaining_key <= 0:
        raise BookflowError('E_CREDIT_UNAVAILABLE', details={
            'credit_memo_id': old_header['id'],
            'available': Money(remaining_key, currency).to_dict(),
            'next': 'This credit has already been applied or refunded in full.'})
    targets, seen = [], set()
    for item in inp.applications:
        invoice = query.invoice_facts(s, item.invoice, write=True)
        header = invoice['header']
        if header['id'] in seen:
            raise _invalid('applications', 'select each invoice once')
        seen.add(header['id'])
        sales._version(s, header, item.expected_version)
        _compatible(s, key, invoice)
        if item.amount is None:
            units = min(invoice['due'], remaining_key)
            if units <= 0:
                raise _invalid('applications.amount', 'this invoice owes nothing this credit can settle')
        else:
            units = money(item.amount, currency, 'applications.amount').minor_units
            if units <= 0:
                raise _invalid('applications.amount', 'apply a positive amount or leave the invoice out')
        _capacity('target', units, invoice['due'], currency, header['id'])
        remaining_key -= units
        if remaining_key < 0:
            raise BookflowError('E_CREDIT_UNAVAILABLE', details={
                'credit_memo_id': old_header['id'], 'invoice_id': header['id'],
                'requested': Money(units, currency).to_dict(),
                'available': Money(units + remaining_key, currency).to_dict(),
                'next': 'Apply at most what this credit is still worth.'})
        targets.append(dict(facts=invoice, amount=units))
    header = dict(old_header, version=old_header['version'] + 1, updated_at=at,
                  updated_by=s.actor.id, updated_via=ctx.interface.value)
    remaining = dict(source['remaining'])
    applications, allocations, changes, changed_headers, recipe = [], [], [], [], []
    for target in targets:
        invoice, units = target['facts'], target['amount']
        old_invoice, invoice_revision = invoice['header'], invoice['revision']
        changed = dict(old_invoice, version=old_invoice['version'] + 1, updated_at=at,
                       updated_by=s.actor.id, updated_via=ctx.interface.value)
        capacities = payments._target_components(s, invoice, pending, created, event)
        for component, share in _draw(remaining, source['components'], units):
            app = dict(**audited(), kind='apply', paying_transaction_id=header['id'],
                       paid_transaction_id=old_invoice['id'], source_component_key_id=None,
                       credit_source_key_id=key['id'], amount_minor_units=share,
                       currency=currency, effective_date=date, reverses_application_id=None)
            pending['applications'].append(app)
            split = calc.allocate(share, {name: value['capacity'] for name, value in capacities.items()})
            for name, allocated in split.items():
                part = capacities[name]
                capacities[name]['capacity'] -= allocated
                allocation = dict(**audited(), application_id=app['id'], kind='allocation',
                    reverses_allocation_id=None, source_transaction_id=header['id'],
                    source_revision_id=revision['id'], source_component_id=None,
                    credit_source_component_id=component['id'],
                    source_posting_source_id=component['posting_source_id'],
                    target_transaction_id=old_invoice['id'], target_revision_id=invoice_revision['id'],
                    target_document_line_id=part['line']['id'], target_line_id=part['line']['line_id'],
                    target_ordinal=name.ordinal, logical_kind='tax' if name.kind else 'net',
                    tax_item_id=part['tax_item_id'], tax_component_id=part['tax_component_id'],
                    target_ar_source_id=part['ar']['id'],
                    target_recognition_source_id=part['recognition']['id'],
                    recognition_role='tax_liability' if name.kind else 'sales_net',
                    amount_minor_units=allocated, currency=currency, effective_date=date,
                    facts_snapshot=query.canonical(part['semantic']))
                pending['application_allocations'].append(allocation)
                allocations.append(dict(allocation_id=allocation['id'], kind='allocation',
                    reverses_allocation_id=None, application_id=app['id'], invoice_id=old_invoice['id'],
                    target_ordinal=name.ordinal, logical_kind=allocation['logical_kind'],
                    tax_item_id=part['tax_item_id'], amount=Money(allocated, currency).to_dict()))
            applications.append(dict(application_id=app['id'], kind='apply',
                reverses_application_id=None, invoice_id=old_invoice['id'],
                invoice_number=old_invoice['number'], invoice_version=changed['version'],
                credit_source_key_id=key['id'], credit_source_component_id=component['id'],
                party_id=key['party_id'], amount=Money(share, currency).to_dict(),
                effective_date=date))
            recipe.append([old_invoice['id'], component['id'], share,
                           sorted((name.ordinal, name.kind, name.tax_item_id, value)
                                  for name, value in split.items())])
        changed_headers.append((old_invoice, changed))
        changes.append(_settlement_change(dict(invoice, header=changed), invoice['applied'] + units, currency))
    return _plan(s, ctx, inp, 'apply', header, old_header, revision, key, pending, changed_headers,
                 event, at, dict(applications=applications, allocations=allocations,
                                 document_changes=changes), source, recipe, date)


# ---------------------------------------------------------------- unapply


def prepare_unapply(s, ctx, inp):
    source = credits.facts(s, inp.credit_memo, write=True)
    old_header, revision, key = source['header'], source['revision'], source['key']
    journals.version_meta(s, old_header, inp.expected_version)
    if old_header['status'] != 'posted':
        raise BookflowError('E_APPLICATION_INACTIVE', details={
            'credit_memo_id': old_header['id'], 'status': old_header['status']})
    currency = source['currency']
    at, event = clock.now_iso(), new_id()
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    pending = {table: [] for table, _, _ in TABLE_KINDS}
    active = {row['id']: row for row in source['applications']}
    components = {row['id']: row for row in source['components']}
    header = dict(old_header, version=old_header['version'] + 1, updated_at=at,
                  updated_by=s.actor.id, updated_via=ctx.interface.value)
    applications, allocations, changes, changed_headers, recipe = [], [], [], [], []
    touched, released, seen = {}, {}, set()
    for reference in inp.applications:
        identifier = reference.application_id.upper()
        if identifier in seen:
            raise _invalid('applications', 'select each original application once')
        seen.add(identifier)
        app = active.get(identifier)
        if app is None:
            raise BookflowError('E_APPLICATION_INACTIVE', details={
                'application_id': identifier, 'credit_memo_id': old_header['id'],
                'next': 'Read the credit memo to see which applications are still live.'})
        invoice = query.invoice_facts(s, app['paid_transaction_id'], write=True)
        sales._version(s, invoice['header'], reference.invoice_expected_version)
        # The inverse carries the original date, never today's: an unapply undoes a
        # settlement where it happened, and that period has to be open to undo it.
        journals.open_dates(s, [app['effective_date']])
        target_id = app['paid_transaction_id']
        if target_id not in touched:
            before = invoice['header']
            touched[target_id] = dict(facts=invoice, amount=0, before=before,
                after=dict(before, version=before['version'] + 1, updated_at=at,
                           updated_by=s.actor.id, updated_via=ctx.interface.value))
        touched[target_id]['amount'] += app['amount_minor_units']
        inverse = dict(app, **created(), audit_event_id=event, kind='unapply',
                       reverses_application_id=app['id'])
        pending['applications'].append(inverse)
        released[app['id']] = app['amount_minor_units']
        live = live_allocations(s, app['id'])
        if not live or sum(row['amount_minor_units'] for row in live) != app['amount_minor_units']:
            raise BookflowError('E_VALIDATION', message='Application allocation evidence is incomplete.')
        applications.append(dict(application_id=inverse['id'], kind='unapply',
            reverses_application_id=app['id'], invoice_id=target_id,
            invoice_number=invoice['header']['number'], invoice_version=touched[target_id]['after']['version'],
            credit_source_key_id=app['credit_source_key_id'],
            credit_source_component_id=live[0]['credit_source_component_id'], party_id=key['party_id'],
            amount=Money(app['amount_minor_units'], app['currency']).to_dict(),
            effective_date=app['effective_date']))
        for row in live:
            journals.open_dates(s, [row['effective_date']])
            undo = dict(row, **created(), audit_event_id=event, kind='reversal',
                        reverses_allocation_id=row['id'])
            pending['application_allocations'].append(undo)
            allocations.append(dict(allocation_id=undo['id'], kind='reversal',
                reverses_allocation_id=row['id'], application_id=app['id'], invoice_id=target_id,
                target_ordinal=row['target_ordinal'], logical_kind=row['logical_kind'],
                tax_item_id=row['tax_item_id'],
                amount=Money(row['amount_minor_units'], row['currency']).to_dict()))
        recipe.append([app['id'], app['amount_minor_units'], sorted(row['id'] for row in live)])
    for target in touched.values():
        invoice, units = target['facts'], target['amount']
        changed_headers.append((target['before'], target['after']))
        changes.append(_settlement_change(dict(invoice, header=target['after']),
                                          invoice['applied'] - units, currency))
    if not components:  # pragma: no cover - a posted credit always has capacity
        raise BookflowError('E_INTERNAL')
    return _plan(s, ctx, inp, 'unapply', header, old_header, revision, key, pending, changed_headers,
                 event, at, dict(applications=applications, allocations=allocations,
                                 document_changes=changes), source, recipe, None)


# ---------------------------------------------------------------- shared plan, validation, write


def _plan(s, ctx, inp, operation, header, before, revision, key, pending, changed_headers,
          event, at, effect, source, recipe, date):
    fingerprint = query.digest([
        # The expectation is not part of what is being described, or supplying it would
        # change the answer it is compared against and could never match.
        inp.model_dump(mode='json', exclude={'expected_facts_fingerprint'}),
        operation, before, source['available'],
        sorted(source['remaining'].items()), [row['id'] for row in source['applications']],
        [row['id'] for row in source['consumptions']], recipe,
        [[old['id'], old['version']] for old, _ in changed_headers], date,
        s.company_info_row['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'credit_facts',
                                                        'current_facts_fingerprint': fingerprint})
    moved = sum(row['amount_minor_units'] for row in pending['applications'])
    applied = source['applied'] + (moved if operation == 'apply' else -moved)
    current = _current(source, applied)
    preview = CreditSettlementOutput(
        id=header['id'], version=header['version'], number=header['number'],
        facts_fingerprint=fingerprint, current=current,
        effect=dict(kind=operation, credit_memo_id=header['id'], audit_event_id=event, **effect))
    return Plan(preview, dict(input=inp, operation=operation, header=header, before=before,
                              revision=revision, key=key, pending=pending,
                              changed_headers=changed_headers, event=event, at=at,
                              fingerprint=fingerprint, source=source, date=date))


def _current(source, applied):
    from bookflow.company.credit_models import CreditSourceOutput
    key, currency = source['key'], source['currency']
    capacity, refunded = source['capacity'], source['refunded']
    available = capacity - applied - refunded
    return CreditSourceOutput(
        credit_source_key_id=key['id'], party_id=key['party_id'], ar_account_id=key['ar_account_id'],
        currency=currency, capacity_minor_units=capacity, applied_minor_units=applied,
        refunded_minor_units=refunded, available_minor_units=available,
        capacity=Money(capacity, currency).to_dict(), applied=Money(applied, currency).to_dict(),
        refunded=Money(refunded, currency).to_dict(), available=Money(available, currency).to_dict())


def validate(plan, s, ctx):
    """Check the whole aggregate against stored evidence, never against the planner's copy."""
    data = plan.data

    def require(ok, why='Invalid credit settlement aggregate.'):
        if not ok:
            raise BookflowError('E_INTERNAL', message=why)

    header, before, pending, key = data['header'], data['before'], data['pending'], data['key']
    require(header['current_revision_id'] == before['current_revision_id'])
    require(header['version'] == before['version'] + 1)
    mutable = {'version', 'updated_at', 'updated_by', 'updated_via'}
    require({k: v for k, v in header.items() if k not in mutable}
            == {k: v for k, v in before.items() if k not in mutable})
    require(not pending['applications'] or all(
        row['created_by'] == s.actor.id and row['created_via'] == ctx.interface.value
        and row['audit_event_id'] == data['event'] for row in pending['applications']))
    for row in pending['application_allocations']:
        require(row['audit_event_id'] == data['event'] and row['created_by'] == s.actor.id)
    for old, after in data['changed_headers']:
        require(after['version'] == old['version'] + 1)
        require({k: v for k, v in after.items() if k not in mutable}
                == {k: v for k, v in old.items() if k not in mutable})
    if data['operation'] == 'apply':
        source = data['source']
        remaining = dict(source['remaining'])
        per_application = {}
        for row in pending['applications']:
            require(row['kind'] == 'apply' and row['reverses_application_id'] is None)
            require(row['source_component_key_id'] is None)
            require(row['credit_source_key_id'] == key['id'])
            require(row['paying_transaction_id'] == header['id'])
            require(row['effective_date'] == data['date'] >= data['revision']['date'])
            require(row['currency'] == source['currency'])
            per_application[row['id']] = row
            journals.open_dates(s, [row['effective_date']])
        for row in pending['application_allocations']:
            require(row['kind'] == 'allocation' and row['reverses_allocation_id'] is None)
            require(row['source_component_id'] is None)
            require(row['source_transaction_id'] == header['id'])
            require(row['source_revision_id'] == data['revision']['id'])
            component = next((value for value in source['components']
                              if value['id'] == row['credit_source_component_id']), None)
            require(component is not None and component['posting_source_id'] == row['source_posting_source_id'])
            remaining[component['id']] -= row['amount_minor_units']
        require(all(value >= 0 for value in remaining.values()),
                'A credit application exceeds the capacity of the line it draws on.')
        for identifier, application in per_application.items():
            parts = [row for row in pending['application_allocations'] if row['application_id'] == identifier]
            require(sum(row['amount_minor_units'] for row in parts) == application['amount_minor_units'])
            # One allocation per target component, which is the rule an invoice correction's
            # restatement validates against; two rows for one component would fail there.
            keys = [(row['target_ordinal'], row['logical_kind'], row['tax_item_id']) for row in parts]
            require(len(set(keys)) == len(keys))
            require(all(row['credit_source_component_id'] == parts[0]['credit_source_component_id']
                        for row in parts))
        spent = sum(row['amount_minor_units'] for row in pending['applications'])
        require(spent <= source['available'], 'A credit application exceeds what the credit is worth.')
        for change in plan.preview.effect.document_changes:
            require(change.due_minor_units >= 0)
        targets = {row['paid_transaction_id'] for row in pending['applications']}
        require(targets == {old['id'] for old, _ in data['changed_headers']})
    else:
        provenance = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind',
                      'reverses_application_id', 'reverses_allocation_id'}
        for table, link in (('applications', 'reverses_application_id'),
                            ('application_allocations', 'reverses_allocation_id')):
            for row in pending[table]:
                original = effects.rows(s, getattr(c, table), getattr(c, table).c.id == row[link])
                require(len(original) == 1)
                require({k: v for k, v in row.items() if k not in provenance}
                        == {k: v for k, v in original[0].items() if k not in provenance})
                journals.open_dates(s, [row['effective_date']])
        live = {row['id'] for row in credits.active_applications(s, header['id'])}
        undone = {row['reverses_application_id'] for row in pending['applications']}
        require(undone <= live)
        expected = {row['id'] for identifier in undone for row in live_allocations(s, identifier)}
        require(expected == {row['reverses_allocation_id'] for row in pending['application_allocations']})
        require(not pending['settlement_line_keys'])


def prepare(s, ctx, inp, operation):
    return prepare_apply(s, ctx, inp) if operation == 'apply' else prepare_unapply(s, ctx, inp)


def apply(plan, ctx, s):
    # Rebuilt inside the writer transaction: the residue on this credit, the invoice's due and
    # the open period are only decisive here, and the preview read them before anyone held the
    # lock. A loser gets E_PREVIEW_STALE rather than a partial write.
    operation = plan.data['operation']
    fresh = prepare(s, ctx, plan.data['input'], operation)
    if fresh.data['fingerprint'] != plan.data['fingerprint']:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'credit_facts'})
    validate(fresh, s, ctx)
    data = fresh.data
    header, before, pending = data['header'], data['before'], data['pending']
    touched = [Touched('transaction', header['id'], 'update', before['version'], header['version'],
                       header, before, db='company')]
    touched.extend(Touched('transaction', after['id'], 'update', old['version'], after['version'],
                           after, old, db='company') for old, after in data['changed_headers'])
    for table, kind, identity in TABLE_KINDS:
        touched.extend(Touched(kind, row[identity], 'create', None, 1, effects.decoded(row), db='company')
                       for row in pending[table])
    command_name = 'customer-credit ' + operation
    summary = f"{operation} credit memo {header['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary, touched, actor_id=s.actor.id,
                         actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None),
                         event_id=data['event'])
    s.company.conn.execute(c.transactions.update().where(
        c.transactions.c.id == header['id']).values(**header))
    for _, after in data['changed_headers']:
        s.company.conn.execute(c.transactions.update().where(
            c.transactions.c.id == after['id']).values(**after))
    for table, _, _ in TABLE_KINDS:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    return Applied(fresh.preview, touched, summary, audited=True)
