"""Effective-date settlement projections over all presently recorded evidence."""
import sqlalchemy as sa
import json

from bookflow.company import schema as c, payment_queries as query, payments
from bookflow.company.payment_dependencies import watermark
from bookflow.core.money import Money
from bookflow.core.errors import BookflowError
from bookflow.company import document_effects as effects
from bookflow.company.payment_cancellation import live_allocations
from bookflow.core import clock


def projection_metadata(as_of):
    return dict(projection='effective_date' if as_of is not None else 'current',
        projection_basis='all_current_knowledge_effective_date' if as_of is not None else 'all_committed_current',
        generated_at=clock.now_iso())


def dated_applications(s, *, invoice=None, payment=None, as_of=None):
    table = c.applications
    conditions = []
    if invoice:
        conditions.append(table.c.paid_transaction_id == invoice)
    if payment:
        conditions.append(table.c.paying_transaction_id == payment)
    if as_of:
        conditions.append(table.c.effective_date <= as_of)
    rows = [dict(row) for row in s.company.conn.execute(sa.select(table).where(*conditions).order_by(table.c.effective_date, table.c.id)).mappings()]
    reversed_ids = {row['reverses_application_id'] for row in rows if row['kind'] == 'unapply'}
    return [row for row in rows if row['kind'] == 'apply' and row['id'] not in reversed_ids]


def invoice(s, inp):
    facts = query.invoice_facts(s, inp.invoice)
    current = query.invoice_current(s, inp.invoice)
    out = dict(current)
    if inp.as_of is not None:
        p, b, a = c.posting_lines, c.posting_batches, c.accounts
        gross = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.sum(p.c.debit_minor_units-p.c.credit_minor_units), 0))
            .join(b, b.c.id == p.c.batch_id).join(a, a.c.id == p.c.account_id).where(
                p.c.transaction_id == facts['header']['id'], a.c.type == 'accounts_receivable', b.c.effective_date <= inp.as_of)).scalar_one()
        applied = sum(row['amount_minor_units'] for row in dated_applications(s, invoice=facts['header']['id'], as_of=inp.as_of))
        # Corrections cancel each superseded batch at that batch's own date.
        # All-current-knowledge existence therefore follows the final commercial
        # revision, even when a later void has canceled its monetary effect.
        # Neither an obsolete original nor a zero net amount establishes status.
        effective = s.company.conn.execute(sa.select(b.c.id).where(b.c.transaction_id == facts['header']['id'],
            b.c.revision_id == facts['revision']['id'], b.c.kind != 'reversal',
            b.c.effective_date <= inp.as_of).limit(1)).first() is not None
        status = ('not_effective' if not effective else 'voided' if facts['header']['status'] == 'voided'
                  else 'paid' if gross > 0 and gross == applied else 'partial' if applied else 'unpaid')
        out.update(gross_minor_units=gross, applied_minor_units=applied, due_minor_units=gross-applied,
                   status=status)
    apps = effects.rows(s, c.applications, c.applications.c.paid_transaction_id == facts['header']['id'],
        *([c.applications.c.effective_date <= inp.as_of] if inp.as_of else []), order=c.applications.c.effective_date)
    from bookflow.company.payment_authority import authorize
    all_payments = s.company.conn.execute(sa.select(c.applications.c.paying_transaction_id).where(
        c.applications.c.paid_transaction_id == facts['header']['id']).distinct()).scalars().all()
    authorize(s, [facts['header']['id'], *all_payments])
    apps.sort(key=lambda row: (row['effective_date'], row['id']))
    allocations = effects.rows(s, c.application_allocations, c.application_allocations.c.target_transaction_id == facts['header']['id'],
        *([c.application_allocations.c.effective_date <= inp.as_of] if inp.as_of else []))
    totals = {kind: sum(row['amount_minor_units'] * (1 if row['kind'] == 'allocation' else -1) for row in allocations if row['logical_kind'] == kind)
              for kind in ('net', 'tax')}
    mark = watermark(s)
    page = query.page(s, 'invoice settlement', inp, apps, facts=[mark, out, apps])
    return dict(out, **projection_metadata(inp.as_of), as_of=inp.as_of, audit_watermark=mark, all_committed_current=current,
        applications=page['items'], application_count=page['total_count'], next_cursor=page['next_cursor'],
        facts_fingerprint=page['facts_fingerprint'], net_applied_minor_units=totals['net'], tax_applied_minor_units=totals['tax'])


def payment(s, inp):
    facts = query.payment_facts(s, inp.payment)
    current = payments.current_output(s, inp.payment, complete_components=True)
    capacities = {key: 0 for key in facts['keys']}
    source, leg, batch, component = c.posting_line_sources, c.posting_lines, c.posting_batches, c.payment_components
    conditions = [source.c.transaction_id == facts['header']['id'], leg.c.account_id == facts['profile']['ar_account_id']]
    if inp.as_of:
        conditions.append(batch.c.effective_date <= inp.as_of)
    amount = sa.case((leg.c.credit_minor_units > 0, source.c.amount_minor_units), else_=-source.c.amount_minor_units)
    rows = s.company.conn.execute(sa.select(component.c.component_key_id, sa.func.sum(amount)).select_from(source)
        .join(leg, leg.c.id == source.c.posting_line_id).join(batch, batch.c.id == leg.c.batch_id)
        .join(component, component.c.id == source.c.payment_component_id).where(*conditions)
        .group_by(component.c.component_key_id))
    capacities.update(dict(rows.all()))
    apps = dated_applications(s, payment=facts['header']['id'], as_of=inp.as_of)
    applied = {key: 0 for key in capacities}
    app_outputs = []
    for app in apps:
        invoice_facts = query.invoice_facts(s, app['paid_transaction_id'])
        applied[app['source_component_key_id']] += app['amount_minor_units']
        app_outputs.append(dict(application_id=app['id'], invoice_id=app['paid_transaction_id'], invoice_version=invoice_facts['header']['version'],
            source_component_key_id=app['source_component_key_id'], party_id=facts['keys'][app['source_component_key_id']]['party_id'],
            amount=Money(app['amount_minor_units'], app['currency']).to_dict(), effective_date=app['effective_date']))
    components = [dict(row, received_minor_units=capacities[row['component_key_id']],
        applied_minor_units=applied[row['component_key_id']], available_minor_units=capacities[row['component_key_id']]-applied[row['component_key_id']]) for row in current['components']]
    rendered = components if inp.kind == 'components' else app_outputs
    mark = watermark(s)
    page = query.page(s, 'payment settlement', inp, rendered, facts=[mark, current, rendered])
    current['components'] = current['components'][:50]
    return dict(page, **projection_metadata(inp.as_of), committed=True, kind=inp.kind, as_of=inp.as_of,
        audit_watermark=mark, received_minor_units=sum(capacities.values()), applied_minor_units=sum(applied.values()),
        unapplied_minor_units=sum(capacities.values())-sum(applied.values()), all_committed_current=current)


def application(s, selector):
    rows = effects.rows(s, c.applications, c.applications.c.id == selector)
    if not rows:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'application'})
    row = rows[0]
    query.payment_facts(s, row['paying_transaction_id'])
    query.invoice_facts(s, row['paid_transaction_id'])
    return row


def application_show(s, inp):
    row = application(s, inp.application)
    original_id = row['reverses_application_id'] or row['id']
    inverses = effects.rows(s, c.applications, c.applications.c.reverses_application_id == original_id)
    allocations = live_allocations(s, original_id)
    return dict(record=row, original_application_id=original_id, active=not inverses,
        reverse_application_id=inverses[0]['id'] if inverses else None,
        current_payment=payments.current_output(s, row['paying_transaction_id']),
        current_invoice=query.invoice_current(s, row['paid_transaction_id']),
        current_allocations=allocations[:50], current_allocation_count=len(allocations))


def _entry(s, row, kind, **values):
    sequence = s.company.conn.execute(sa.select(c.audit_events.c.seq).where(c.audit_events.c.id == row['audit_event_id'])).scalar_one()
    return dict(id=row['id'], audit_event_id=row['audit_event_id'], audit_sequence=sequence, kind=kind, **values)


def application_history(s, inp):
    row = application(s, inp.application)
    original_id = row['reverses_application_id'] or row['id']
    applications = effects.rows(s, c.applications, sa.or_(c.applications.c.id == original_id, c.applications.c.reverses_application_id == original_id))
    allocations = effects.rows(s, c.application_allocations, c.application_allocations.c.application_id == original_id)
    items = [_entry(s, row, 'application', application=row) for row in applications]
    items.extend(_entry(s, row, 'allocation', allocation=row) for row in allocations)
    items.sort(key=lambda row: (row['audit_sequence'], row['id']))
    mark = watermark(s)
    return dict(query.page(s, 'application history', inp, items, facts=[mark, items]), audit_watermark=mark)


def payment_history(s, inp):
    from bookflow.company.payment_models import PaymentShowInput
    facts = query.payment_facts(s, inp.payment)
    identifier = facts['header']['id']
    items = []
    for row in effects.rows(s, c.transaction_revisions, c.transaction_revisions.c.transaction_id == identifier):
        revision = payments.show(s, PaymentShowInput(payment=identifier, revision=row['revision_number'])).revision.model_dump(mode='json')
        items.append(_entry(s, row, 'receipt_revision', revision=revision))
    resolved = sa.func.json_each(c.payment_operations.c.request_snapshot,
        '$.resolved_transaction_ids').table_valued('value')
    relevant = sa.exists(sa.select(resolved.c.value).where(resolved.c.value == identifier))
    for row in effects.rows(s, c.payment_operations, relevant):
        captured = json.loads(row['request_snapshot'])
        if identifier in captured['resolved_transaction_ids']:
            from bookflow.company.payment_authority import authorize
            authorize(s, captured['resolved_transaction_ids'])
            items.append(_entry(s, row, 'operation', operation_key=row['operation_key'], command=row['command']))
    for row in effects.rows(s, c.applications, c.applications.c.paying_transaction_id == identifier):
        items.append(_entry(s, row, 'application', application=row))
    for row in effects.rows(s, c.application_allocations, c.application_allocations.c.source_transaction_id == identifier):
        items.append(_entry(s, row, 'allocation', allocation=row))
    items.sort(key=lambda row: (row['audit_sequence'], row['id']))
    mark = watermark(s)
    return dict(query.page(s, 'payment history', inp, items, facts=[mark, items]), audit_watermark=mark)
