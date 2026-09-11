"""One invoice correction, complete logical restatement and permanent recovery."""
import json

from bookflow.company import schema as c, sales, journals, billing, document_effects as effects
from bookflow.company import credits, journal_custom_fields as custom, payment_operations as operations
from bookflow.company import payment_queries as query, payment_dependencies as dependencies, payments
from bookflow.company import payment_restatement as restatement
from bookflow.company.payment_outputs import InvoiceCorrectionOutput
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.money import Money
from bookflow.core.registry import Plan, Applied, Touched


# One receivable settlement edge, two kinds of paying document. An application's source is a
# receipt or a credit memo, and a correction has to read, version-check and report whichever it
# is: resolving every paying document as a payment answered E_RECORD_NOT_FOUND for a credit,
# which made an applied credit unable to have its invoice corrected at all.
def _source_facts(s, identifier):
    header = effects.rows(s, c.transactions, c.transactions.c.id == identifier)
    if header and header[0]['type'] == 'credit_memo':
        return credits.facts(s, header[0], write=True), True
    return query.payment_facts(s, identifier, write=True), False


def _selected_source(s, selector):
    """Resolve a settlement_versions reference, which may name a receipt or a credit memo."""
    try:
        return query.payment_facts(s, selector, write=True)
    except BookflowError as exc:
        if exc.code != 'E_RECORD_NOT_FOUND':
            raise
    return credits.facts(s, selector, write=True)


def _source_current(s, identifier):
    facts, is_credit = _source_facts(s, identifier)
    return credits.settlement_current_output(s, facts) if is_credit else payments.current_output(s, identifier)


def prepare(s, ctx, inp):
    if inp.operation_key and operations.find(s, inp.operation_key):
        recovered = operations.recover(inp, ctx, s, 'invoice update')
        if recovered:
            return Plan(recovered.output, dict(recovered=True, input=inp, document_type='invoice', operation='update'))
        raise BookflowError('E_PAYMENT_OPERATION_KEY_REUSED')
    facts = query.invoice_facts(s, inp.invoice, write=True)
    apps = query.active_applications(s, invoice=facts['header']['id'])
    funding = {row['paying_transaction_id']: _source_facts(s, row['paying_transaction_id'])[0] for row in apps}
    if apps and not inp.operation_key:
        raise BookflowError('E_VALIDATION', message='This invoice has applied payments. Supply one operation_key for this correction and a reason; reuse the key for preview, save and retries.', details={'field': 'operation_key', 'reason': 'applied_invoice_correction'})
    if apps and inp.expected_version is None:
        raise BookflowError('E_VALIDATION', details={'field': 'expected_version'})
    if inp.operation_key and (not ctx.reason or not ctx.reason.strip() or len(ctx.reason) > 140):
        raise BookflowError('E_REASON_REQUIRED')
    commercial_input = inp.model_copy(update={'expected_facts_fingerprint': None}) if inp.operation_key else inp
    plan = sales.prepare(s, ctx, commercial_input, 'invoice', 'update', _settlement_internal=True)
    data = plan.data
    data['input'] = inp
    data['settlement_applications'] = apps
    if data['changed']:
        credits.require_claims_intact(s, facts['header']['id'], facts['revision'], data['pending'])
        if apps:
            if inp.expected_version is None:
                raise BookflowError('E_VALIDATION', details={'field': 'expected_version'})
            if inp.settlement_guard:
                dependencies.validate(s, inp.settlement_guard, 'invoice', facts['header']['id'])
            else:
                supplied = {}
                for ref in inp.settlement_versions:
                    payment = _selected_source(s, ref.payment)
                    identifier = payment['header']['id']
                    if identifier in supplied:
                        raise BookflowError('E_VALIDATION', message='Supply each payment version once.')
                    supplied[identifier] = ref.expected_version
                if set(supplied) != set(funding):
                    raise BookflowError('E_PREVIEW_STALE', message='Review invoice settlement, supply its settlement_guard or current settlement_versions for every applied payment, then preview again. The current guard is also included in this error.', details={'reason': 'settlement_dependencies',
                        'settlement_guard': dependencies.issue(s, 'invoice', facts['header']['id'])})
                for identifier, payment in funding.items():
                    dependencies.payment_version(s, payment['header'], supplied[identifier])
        restatement.prepare(plan, s, ctx)
    if not inp.operation_key:
        data['settlement_extension'] = bool(data.get('settlement_keys'))
        return plan
    at = data['header']['updated_at'] if data['changed'] else clock.now_iso()
    if not data['changed']:
        data.update(header=facts['header'], before=facts['header'], old_revision=facts['revision'],
            pending={table: [] for table, _, _ in sales.TABLE_KINDS}, custom_plan=None, sequence=None, event=new_id())
    header = data['header']
    changed_headers, payment_outputs = [], []
    for identifier in data.get('settlement_changed_payment_ids', []):
        old = funding[identifier]['header']
        after = dict(old, version=old['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        changed_headers.append((old, after))
        payment_outputs.append(dict(_source_current(s, identifier), version=after['version']))
    data['settlement_headers'] = changed_headers
    fp = query.digest([operations.request(inp, ctx, s, 'invoice update'), plan.preview.facts_fingerprint,
        [value['header'] for _, value in sorted(funding.items())], apps, data.get('settlement_recipe', [])])
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint != fp:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'settlement_dependencies', 'facts_fingerprint': fp})
    current = query.invoice_current(s, header['id'])
    current.update(version=header['version'], revision_id=header['current_revision_id'],
        gross_minor_units=plan.preview.revision.total_minor_units,
        due_minor_units=plan.preview.revision.total_minor_units-current['applied_minor_units'])
    current['status'] = 'paid' if current['due_minor_units'] == 0 else 'partial' if current['applied_minor_units'] else 'unpaid'
    allocations = [dict(kind=row['kind'], reverses_allocation_id=row['reverses_allocation_id'], allocation_id=row['id'], application_id=row['application_id'], invoice_id=row['target_transaction_id'],
        target_ordinal=row['target_ordinal'], logical_kind=row['logical_kind'], tax_item_id=row['tax_item_id'],
        amount=Money(row['amount_minor_units'], row['currency']).to_dict()) for row in data.get('settlement_allocations', [])]
    operation_id = new_id()
    output = InvoiceCorrectionOutput(operation_key=inp.operation_key, facts_fingerprint=fp,
        changed=data['changed'], new_effect=data['changed'], current=current,
        effect=dict(operation_id=operation_id, invoice_id=header['id'], allocations=allocations,
            audit_event_id=data['event'], before_header=payments.effect_header(facts['header'], facts['revision']),
            after_header=payments.effect_header(header, data['pending']['transaction_revisions'][0] if data['changed'] else facts['revision']),
            document_changes=[current, *payment_outputs] if data['changed'] else [], payment_changes=payment_outputs),
        effect_counts=dict(source_components=0, applications=0, allocations=len(allocations), document_changes=(1+len(payment_outputs)) if data['changed'] else 0))
    data['settlement_commercial_fingerprint'] = plan.preview.facts_fingerprint
    plan.preview.facts_fingerprint = fp
    plan.preview.settlement = output
    data.update(settlement_extension=True, settlement_operation_id=operation_id, settlement_fingerprint=fp, at=at)
    return plan


def persist(plan, ctx, s):
    restatement.validate(plan, s, ctx)
    data = plan.data
    header, before = data['header'], data['before']
    touched = []
    if data['changed']:
        touched.append(Touched('transaction', header['id'], 'update', before['version'], header['version'], header, before, db='company'))
    extra_headers = data.get('settlement_headers', [])
    touched.extend(Touched('transaction', after['id'], 'update', old['version'], after['version'], after, old, db='company') for old, after in extra_headers)
    inserts = [(getattr(c, table), data['pending'][table], kind, key) for table, kind, key in sales.TABLE_KINDS]
    inserts += [(c.work_billing_allocations, data.get('billing_allocations', []), 'work_billing_allocation', 'id'),
        (c.settlement_line_keys, data.get('settlement_keys', []), 'settlement_line_key', 'id'),
        (c.application_allocations, data.get('settlement_allocations', []), 'application_allocation', 'id')]
    if data.get('settlement_operation_id'):
        complete = plan.preview.settlement.effect.model_dump(mode='json')
        for kind in ('allocations', 'document_changes', 'payment_changes'):
            setattr(plan.preview.settlement.effect, kind, getattr(plan.preview.settlement.effect, kind)[:50])
        inp = data['input']
        operation = dict(id=data['settlement_operation_id'], operation_key=inp.operation_key, command='invoice update',
            request_schema_version=1, request_hash=operations.request_hash(inp, ctx, s, 'invoice update'),
            request_snapshot=query.canonical(dict(original_request=operations.original_request(inp, ctx, s, 'invoice update'),
                resolved_transaction_ids=[header['id'], *{row['paying_transaction_id'] for row in data.get('settlement_applications', [])}], expanded_selection_hash=None)),
            effect_snapshot=query.canonical(plan.preview.model_dump(mode='json')),
            execution_snapshot=query.canonical(dict(actor_id=s.actor.id, interface=ctx.interface.value, on_behalf_of=ctx.on_behalf_of,
                reason=ctx.reason, directive_id=ctx.directive_id, directive_code=getattr(s, 'directive_code', None))),
            created_at=data['at'], created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=data['event'])
        inserts.append((c.payment_operations, [operation], 'payment_operation', 'id'))
        items = []
        for kind in ('allocations', 'document_changes'):
            for ordinal, row in enumerate(complete[kind], 1):
                items.append(dict(id=new_id(), operation_id=operation['id'], kind=kind, ordinal=ordinal, item_snapshot=query.canonical(row),
                    created_at=data['at'], created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=data['event']))
        inserts.append((c.payment_operation_items, items, 'payment_operation_item', 'id'))
    for table, rows, kind, key in inserts:
        touched.extend(Touched(kind, row[key], 'create', None, 1, effects.decoded(row), db='company') for row in rows)
    if data['custom_plan']:
        touched.extend(custom.touches(data['custom_plan']))
    audit.write_event_to(s.company, ctx, 'invoice update', 'Correct invoice ' + header['number'], touched,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=data['event'])
    if data['changed']:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == header['id']).values(**header))
    for old, after in extra_headers:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == after['id']).values(**after))
    for table, rows, _, _ in inserts:
        if rows:
            s.company.conn.execute(table.insert(), rows)
    if data['custom_plan']:
        custom.apply(s.company, data['custom_plan'])
    if data['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**data['sequence'])
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=data['sequence']))
    return Applied(plan.preview, touched, 'invoice update', audited=True)
