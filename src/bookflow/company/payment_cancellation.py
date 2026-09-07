"""Explicit application cancellation and exact receipt reversal; no implicit transfer."""
from collections import defaultdict

import sqlalchemy as sa

from bookflow.company import schema as c, journals, sales, document_effects as effects
from bookflow.company import payments, payment_queries as query, payment_operations as operations
from bookflow.company.payment_outputs import PaymentWriteOutput, PaymentSourceOutput
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Plan


def live_allocations(s, application_id):
    table = c.application_allocations
    inverse = table.alias('inverse')
    identifiers = [application_id] if isinstance(application_id, str) else list(application_id)
    result = []
    for offset in range(0, len(identifiers), 200):
        result.extend(dict(row) for row in s.company.conn.execute(sa.select(table).where(
            table.c.application_id.in_(identifiers[offset:offset+200]), table.c.kind == 'allocation',
            ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_allocation_id == table.c.id))
        ).order_by(table.c.id)).mappings())
    return result


def prepare(s, ctx, inp, operation):
    from bookflow.company.payment_models import EffectProvenance
    plan = _prepare_effect(s, ctx, inp, operation, EffectProvenance(at=clock.now_iso(), event_id=new_id(), operation_id=new_id()))
    plan.preview = PaymentWriteOutput(**plan.preview.model_dump(), operation_key=inp.operation_key)
    return plan


def prepare_void_effect(s, ctx, inp, provenance):
    from bookflow.company.payment_models import PaymentVoidIntent, EffectProvenance
    if type(inp) is not PaymentVoidIntent or type(provenance) is not EffectProvenance:
        raise BookflowError('E_VALIDATION')
    return _prepare_effect(s, ctx, inp, 'void', provenance)


def prepare_all_active_void(s, ctx, inp, provenance):
    from bookflow.company.payment_models import PaymentVoidIntent, EffectProvenance
    if type(inp) is not PaymentVoidIntent or type(provenance) is not EffectProvenance:
        raise BookflowError('E_VALIDATION')
    return _prepare_effect(s, ctx, inp, 'all_active_void', provenance)


def _prepare_effect(s, ctx, inp, operation, provenance):
    facts = query.payment_facts(s, inp.payment, write=True)
    old, revision = facts['header'], facts['revision']
    from bookflow.company.payment_dependencies import payment_version
    payment_version(s, old, inp.expected_version)
    if not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140:
        raise BookflowError('E_REASON_REQUIRED')
    at, event, operation_id = provenance.at, provenance.event_id, provenance.operation_id
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    pending = {table: [] for table, _, _ in payments.TABLE_KINDS}
    header = dict(old)
    touched, targets, originals, inverse_sources = {}, [], [], []
    applications, allocations, changes = [], [], []
    current = payments.current_output(s, old['id'], complete_components=True)
    changed = operation == 'unapply' or old['status'] != 'voided'
    if changed:
        header.update(version=old['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    if operation == 'void':
        if facts['applications']:
            raise BookflowError('E_HAS_APPLICATIONS', details={'payment_id': old['id'], 'action': 'unapply_first'})
        if changed:
            journals.open_dates(s, [revision['date']])
            batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'],
                                  c.posting_batches.c.kind != 'reversal')
            if len(batches) != 1:
                raise BookflowError('E_VALIDATION', message='Receipt has ambiguous current posting evidence.')
            inverse = effects.reverse(s, header, revision, batches[0], event, created, pending)
            header.update(status='voided', voided_at=at, voided_by=s.actor.id, void_reason=ctx.reason.strip(),
                          void_posting_batch_id=inverse['id'])
        current.update(status='voided', effective_received_minor_units=0, available_minor_units=0)
        for component in current['components']:
            component['available_minor_units'] = 0
    else:
        if old['status'] != 'posted':
            raise BookflowError('E_APPLICATION_INACTIVE')
        active = {row['id']: row for row in facts['applications']}
        seen, released = set(), defaultdict(int)
        from bookflow.company.payment_models import UnapplyReference
        refs = inp.applications if operation == 'unapply' else [
            UnapplyReference(application_id=app['id'], invoice_expected_version=query.invoice_facts(s, app['paid_transaction_id'], write=True)['header']['version'])
            for app in facts['applications']]
        for ref in refs:
            if ref.application_id in seen:
                raise BookflowError('E_VALIDATION', message='Select each original application once.')
            seen.add(ref.application_id)
            app = active.get(ref.application_id)
            if app is None:
                raise BookflowError('E_APPLICATION_INACTIVE')
            invoice = query.invoice_facts(s, app['paid_transaction_id'], write=True)
            sales._version(s, invoice['header'], ref.invoice_expected_version)
            journals.open_dates(s, [app['effective_date']])
            originals.append(app)
            target_id = app['paid_transaction_id']
            if target_id not in touched:
                before = invoice['header']
                touched[target_id] = (before, dict(before, version=before['version'] + 1,
                    updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value))
                targets.append(dict(facts=invoice, amount=0))
            next(row for row in targets if row['facts']['header']['id'] == target_id)['amount'] += app['amount_minor_units']
            inverse = dict(app, **created(), audit_event_id=event, kind='unapply', reverses_application_id=app['id'])
            pending['applications'].append(inverse)
            party = facts['keys'][app['source_component_key_id']]['party_id']
            applications.append(dict(kind='unapply', reverses_application_id=app['id'], application_id=inverse['id'], invoice_id=target_id,
                invoice_version=touched[target_id][1]['version'], source_component_key_id=app['source_component_key_id'],
                party_id=party, amount=Money(app['amount_minor_units'], app['currency']).to_dict(), effective_date=app['effective_date']))
            released[app['source_component_key_id']] += app['amount_minor_units']
            live = live_allocations(s, app['id'])
            inverse_sources.extend(live)
            if sum(row['amount_minor_units'] for row in live) != app['amount_minor_units']:
                raise BookflowError('E_VALIDATION', message='Application allocation evidence is incomplete.')
            for row in live:
                journals.open_dates(s, [row['effective_date']])
                undo = dict(row, **created(), audit_event_id=event, kind='reversal', reverses_allocation_id=row['id'])
                pending['application_allocations'].append(undo)
                allocations.append(dict(kind='reversal', reverses_allocation_id=row['id'], allocation_id=undo['id'], application_id=app['id'], invoice_id=target_id,
                    target_ordinal=row['target_ordinal'], logical_kind=row['logical_kind'], tax_item_id=row['tax_item_id'],
                    amount=Money(row['amount_minor_units'], row['currency']).to_dict()))
        for target in targets:
            invoice, units = target['facts'], target['amount']
            applied = invoice['applied'] - units
            changes.append(dict(invoice_id=invoice['header']['id'], version=touched[invoice['header']['id']][1]['version'],
                revision_id=invoice['revision']['id'], gross_minor_units=invoice['gross'], applied_minor_units=applied,
                due_minor_units=invoice['due'] + units, currency=invoice['revision']['currency'], status='partial' if applied else 'unpaid'))
        for component in current['components']:
            units = released[component['component_key_id']]
            component['applied_minor_units'] -= units
            component['available_minor_units'] += units
        current['applied_minor_units'] -= sum(released.values())
        current['available_minor_units'] += sum(released.values())
    cancellation_set = None
    if operation == 'all_active_void':
        from bookflow.company.payment_models import CancellationSet
        cancellation_set = CancellationSet(payment_id=old['id'], expected_version=old['version'],
            application_ids=tuple(sorted(row['id'] for row in facts['applications'])),
            allocation_ids=tuple(sorted(row['id'] for row in inverse_sources)),
            invoice_versions=tuple(sorted((before['id'], before['version']) for before, after in touched.values())))
        journals.open_dates(s, [revision['date']])
        batches = effects.rows(s, c.posting_batches, c.posting_batches.c.revision_id == revision['id'], c.posting_batches.c.kind != 'reversal')
        if len(batches) != 1:
            raise BookflowError('E_VALIDATION', message='Receipt has ambiguous current posting evidence.')
        inverse = effects.reverse(s, header, revision, batches[0], event, created, pending)
        header.update(status='voided', voided_at=at, voided_by=s.actor.id, void_reason=ctx.reason.strip(), void_posting_batch_id=inverse['id'])
        current.update(status='voided', effective_received_minor_units=0, available_minor_units=0)
        for component in current['components']:
            component['available_minor_units'] = 0
    if any(after['version'] > 9223372036854775807 for after in [header, *(new for before, new in touched.values())]):
        raise BookflowError('E_VALUE_RANGE')
    current['version'] = header['version']
    fp = query.digest([operations.request(inp, ctx, s, 'payment ' + operation), old,
        facts['applications'], originals, inverse_sources, [a for a, b in touched.values()],
        s.company_info_row['closing_date']])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fp:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts'})
    effect = dict(kind='void' if operation == 'all_active_void' else operation, financial_changed=changed, operation_id=operation_id, payment_id=old['id'],
        audit_event_id=event, before_header=payments.effect_header(old, revision), after_header=payments.effect_header(header, revision),
        source_components=current['components'], applications=applications, allocations=allocations, document_changes=changes)
    output = PaymentSourceOutput(id=old['id'], version=header['version'], changed=changed, new_effect=changed,
        facts_fingerprint=fp, effect=effect, current=current,
        effect_counts={key: len(effect[key]) for key in ('source_components', 'applications', 'allocations', 'document_changes')})
    return Plan(output, dict(input=inp, operation='void' if operation == 'all_active_void' else operation, header=header, before=old, pending=pending,
        changed_headers=list(touched.values()), event=event, operation_id=operation_id, selected=None,
        custom_plan=None, sequence=None, context={'currency': revision['currency']}, targets=targets,
        fingerprint=fp, originals=originals, at=at, **({'cancellation_set': cancellation_set} if cancellation_set is not None else {})))


def validate(plan, s, ctx):
    """Compare inverses independently against stored live evidence, never planner copies."""
    data = plan.data
    def require(ok):
        if not ok:
            raise BookflowError('E_INTERNAL', message='Invalid payment cancellation aggregate.')
    pending, header, before = data['pending'], data['header'], data['before']
    all_active = data.get('cancellation_set') is not None
    require(header['current_revision_id'] == before['current_revision_id'])
    for table, rows in pending.items():
        if table not in ('applications', 'application_allocations', 'posting_batches', 'posting_lines', 'posting_line_sources'):
            require(not rows)
        for row in rows:
            require(row['created_by'] == s.actor.id and row['created_via'] == ctx.interface.value)
            if 'audit_event_id' in row:
                require(row['audit_event_id'] == data['event'])
    provenance = {'id', 'created_at', 'created_by', 'created_via', 'audit_event_id', 'kind', 'reverses_application_id', 'reverses_allocation_id'}
    for table, link in (('applications', 'reverses_application_id'), ('application_allocations', 'reverses_allocation_id')):
        for row in pending[table]:
            original = effects.rows(s, getattr(c, table), getattr(c, table).c.id == row[link])
            require(len(original) == 1)
            require({k: v for k, v in row.items() if k not in provenance} ==
                    {k: v for k, v in original[0].items() if k not in provenance})
            journals.open_dates(s, [row['effective_date']])
    if data['operation'] == 'unapply' or all_active:
        if data['operation'] == 'unapply':
            require(all(not pending[name] for name in ('posting_batches', 'posting_lines', 'posting_line_sources')))
        expected = {row['id'] for app in data['originals'] for row in live_allocations(s, app['id'])}
        require(expected == {row['reverses_allocation_id'] for row in pending['application_allocations']})
        require(header['version'] == before['version'] + 1)
        mutable = {'version', 'updated_at', 'updated_by', 'updated_via'}
        if all_active:
            mutable |= {'status', 'voided_at', 'voided_by', 'void_reason', 'void_posting_batch_id'}
        require({k: v for k, v in header.items() if k not in mutable} ==
                {k: v for k, v in before.items() if k not in mutable})
        require({row['paid_transaction_id'] for row in pending['applications']} ==
                {old['id'] for old, new in data['changed_headers']})
        mutable = {'version', 'updated_at', 'updated_by', 'updated_via'}
        for old, new in data['changed_headers']:
            require(new['version'] == old['version'] + 1)
            require({k: v for k, v in new.items() if k not in mutable} ==
                    {k: v for k, v in old.items() if k not in mutable})
    if data['operation'] != 'unapply':
        if all_active:
            actual = query.payment_facts(s, header['id'], write=True)
            require(before == actual['header'])
            live = actual['applications']
            expected_apps = {row['id'] for row in live}
            expected_allocs = {row['id'] for row in live_allocations(s, expected_apps)}
            complete = data['cancellation_set']
            invoices = {row['paid_transaction_id']: query.invoice_facts(s, row['paid_transaction_id'], write=True)['header'] for row in live}
            require(complete.invoice_versions == tuple(sorted((key, value['version']) for key, value in invoices.items())))
            require({row['id']: row for row, after in data['changed_headers']} == invoices)
            require(len(data['changed_headers']) == len(invoices))
            require(set(complete.application_ids) == expected_apps == {row['reverses_application_id'] for row in pending['applications']})
            require(len(pending['applications']) == len(expected_apps))
            require(set(complete.allocation_ids) == expected_allocs == {row['reverses_allocation_id'] for row in pending['application_allocations']})
            require(len(pending['application_allocations']) == len(expected_allocs))
            require(complete.payment_id == header['id'] and complete.expected_version == before['version'])
            require(header['status'] == 'voided' and header['version'] == before['version'] + 1)
            require(all(0 < row['version'] <= 9223372036854775807 for row in [header, *(new for old, new in data['changed_headers'])]))
            require(header['void_posting_batch_id'] == pending['posting_batches'][0]['id'])
        else:
            require(not query.active_applications(s, payment=header['id']))
            require(not pending['applications'] and not pending['application_allocations'])
        if before['status'] == 'voided':
            require(header == before and all(not rows for rows in pending.values()))
        else:
            require(len(pending['posting_batches']) == 1)
            batch = pending['posting_batches'][0]
            journals.open_dates(s, [batch['effective_date']])
            old_legs = effects.rows(s, c.posting_lines, c.posting_lines.c.batch_id == batch['reverses_batch_id'])
            require({row['id'] for row in old_legs} == {row['reversed_line_id'] for row in pending['posting_lines']})
            for leg in pending['posting_lines']:
                old = next(row for row in old_legs if row['id'] == leg['reversed_line_id'])
                ignored = {'id', 'created_at', 'created_by', 'created_via', 'batch_id', 'debit_minor_units', 'credit_minor_units', 'reversed_line_id'}
                require({k: v for k, v in leg.items() if k not in ignored} == {k: v for k, v in old.items() if k not in ignored})
                require(leg['debit_minor_units'] == old['credit_minor_units'] and leg['credit_minor_units'] == old['debit_minor_units'])
            old_sources = effects.rows(s, c.posting_line_sources,
                c.posting_line_sources.c.posting_line_id.in_([row['id'] for row in old_legs]))
            require({row['id'] for row in old_sources} == {row['reversed_source_id'] for row in pending['posting_line_sources']})
            for source in pending['posting_line_sources']:
                old = next(row for row in old_sources if row['id'] == source['reversed_source_id'])
                ignored = {'id', 'created_at', 'created_by', 'created_via', 'posting_line_id', 'reversed_source_id'}
                require({k: v for k, v in source.items() if k not in ignored} ==
                        {k: v for k, v in old.items() if k not in ignored})
                new_leg = next(row for row in pending['posting_lines'] if row['id'] == source['posting_line_id'])
                require(new_leg['reversed_line_id'] == old['posting_line_id'])
