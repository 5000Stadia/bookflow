"""Read-only prospective effect pages, bound to exact original intent and facts."""
import json
import base64
import hmac
from copy import deepcopy

from bookflow.company import payment_queries as query, payment_operations as operations, payments
from bookflow.company.payment_models import PaymentPreviewItemsInput
from bookflow.company.payment_outputs import PaymentEffectItemsOutput, ProspectivePageOutput
from bookflow.core.errors import BookflowError

KINDS = ('source_components', 'applications', 'allocations', 'document_changes')


def _logical_key(kind, row):
    if kind == 'source_components':
        return [row['party_id'], row['ar_account_id'], row['currency']]
    if kind == 'document_changes':
        return [row.get('invoice_id') or row.get('payment_id')]
    if kind == 'applications':
        return [row['invoice_id'], row.get('kind'), row.get('reverses_application_id'), row.get('source_component_key_id'), row['party_id']]
    return [row['invoice_id'], row.get('application_id'), row['target_ordinal'], row['logical_kind'], row.get('tax_item_id'),
            row.get('kind'), row.get('reverses_allocation_id')]


def _page(s, inp, rows):
    """Authenticate original intent and the last stable logical identity explicitly."""
    original = inp.request.model_dump(mode='json')
    # Expected financial fingerprints are optional input transport guards, not
    # a second business intent. The original envelope otherwise retains omission.
    intent = query.digest(original)
    binding = dict(v=1, company_id=s.company_row['id'], original_command=inp.request.command,
        canonical_intent_hash=intent, facts_fingerprint=inp.facts_fingerprint, kind=inp.kind, limit=inp.limit)
    domain = b'bookflow.payment.prospective.v1\0'
    key = query._cursor_key(s.company)
    keys = [_logical_key(inp.kind, row) for row in rows]
    if len({query.canonical(value) for value in keys}) != len(keys):
        raise BookflowError('E_INTERNAL', message='Prospective effect identities are ambiguous.')
    offset = 0
    if inp.cursor:
        try:
            body, signature = inp.cursor.split('.')
            raw = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
            mac = base64.b64decode(signature + '=' * (-len(signature) % 4), altchars=b'-_', validate=True)
            if not hmac.compare_digest(mac, hmac.digest(key, domain + raw, 'sha256')):
                raise ValueError()
            saved = json.loads(raw)
            if not isinstance(saved, dict) or set(saved) != set(binding) | {'last_logical_sort_key'} or any(saved[field] != value for field, value in binding.items()):
                raise ValueError()
            offset = keys.index(saved['last_logical_sort_key']) + 1
        except (ValueError, TypeError, KeyError):
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'prospective_continuation'}) from None
    items = rows[offset:offset + inp.limit]
    cursor = None
    if offset + len(items) < len(rows):
        raw = query.canonical(dict(binding, last_logical_sort_key=keys[offset + len(items)-1])).encode()
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
        cursor = encode(raw) + '.' + encode(hmac.digest(key, domain + raw, 'sha256'))
    return dict(items=items, total_count=len(rows), next_cursor=cursor, facts_fingerprint=inp.facts_fingerprint)


def request(inp, ctx, operation):
    return dict(command='payment ' + operation, input=inp.model_dump(mode='json', exclude_unset=True),
        context=({} if ctx.reason is None else {'reason': ctx.reason}) |
                ({} if ctx.directive_id is None else {'directive_id': ctx.directive_id}))


def logical_items(output, kind):
    rows = deepcopy(output.effect.model_dump(mode='json')[kind])
    for row in rows:
        if output.effect.kind == 'invoice_update' and kind == 'document_changes' and output.changed and 'invoice_id' in row:
            row['revision_id'] = None
        for field in ('application_id', 'allocation_id'):
            if field == 'application_id' and kind == 'allocations' and output.effect.kind not in ('receive', 'apply'):
                continue
            if field in row:
                row[field] = None
        if output.effect.kind == 'receive' or output.effect.kind == 'update' and output.changed:
            for field in ('component_id', 'component_key_id', 'source_component_key_id'):
                if field in row:
                    row[field] = None
    return rows


def preview_output(s, ctx, inp, plan):
    """Bound the initial preview and advertise every prospective collection."""
    output = plan.preview.model_copy(deep=True)
    original = request(inp, ctx, plan.data['operation'])
    pages = []
    for kind in KINDS:
        model = PaymentPreviewItemsInput.model_validate(dict(request=original, facts_fingerprint=output.facts_fingerprint, kind=kind))
        items = logical_items(output, kind)
        result = _page(s, model, items)
        setattr(output.effect, kind, [type(item).model_validate(value) for item, value in zip(getattr(output.effect, kind), result['items'])])
        pages.append(ProspectivePageOutput(request=model.request, facts_fingerprint=output.facts_fingerprint,
            kind=kind, total_count=len(items), next_cursor=result['next_cursor']))
    output.effect.operation_id = None
    output.effect.audit_event_id = None
    if output.effect.kind == 'receive':
        output.id = None
        output.effect.payment_id = None
        output.current.payment_id = None
    if output.effect.kind == 'receive' or output.effect.kind == 'update' and output.changed:
        output.current.revision_id = None
        if output.effect.after_header is not None and output.changed:
            output.effect.after_header.revision_id = None
    if output.effect.kind == 'receive' and output.effect.after_header is not None:
        output.effect.after_header.id = None
    if output.effect.kind == 'receive' or output.effect.kind == 'update' and output.changed:
        for component in output.current.components:
            component.component_key_id = None
            component.component_id = None
    output.prospective_pages = pages
    output.current.components = output.current.components[:50]
    return output


def items(s, ctx, inp):
    original = inp.request
    committed = operations.find(s, original.input.operation_key)
    if committed is not None:
        from bookflow.company.payment_authority import authorize
        authorize(s, json.loads(committed['request_snapshot'])['resolved_transaction_ids'], write=True)
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'intent_already_committed'})
    context = ctx.model_copy(update={'reason': original.context.reason, 'directive_id': original.context.directive_id})
    try:
        if original.command == 'invoice update':
            from bookflow.company.payment_invoice_corrections import prepare
            plan = prepare(s, context, original.input)
            plan.preview = plan.preview.settlement
        else:
            plan = payments.prepare(s, context, original.input, original.command.split()[-1])
    except BookflowError as exc:
        if exc.code in ('E_VERSION_CONFLICT', 'E_SELECTION_CONSUMED', 'E_APPLICATION_CAPACITY', 'E_APPLICATION_INACTIVE'):
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts', 'cause': exc.code}) from None
        raise
    if plan.preview.facts_fingerprint != inp.facts_fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts'})
    rows = logical_items(plan.preview, inp.kind)
    try:
        result = _page(s, inp, rows)
    except BookflowError as exc:
        if exc.code in ('E_QUERY_STALE', 'E_VALIDATION'):
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'prospective_continuation'}) from None
        raise
    result['facts_fingerprint'] = inp.facts_fingerprint
    return PaymentEffectItemsOutput(**result, projection='prospective', committed=False, kind=inp.kind)


def invoice_preview_output(s, ctx, inp, output):
    output = output.model_copy(deep=True)
    original = dict(request(inp, ctx, 'update'), command='invoice update')
    for kind in KINDS:
        model = PaymentPreviewItemsInput.model_validate(dict(request=original, facts_fingerprint=output.facts_fingerprint, kind=kind))
        rows = logical_items(output, kind)
        if kind == 'document_changes':
            for row in rows:
                if output.changed and 'invoice_id' in row:
                    row['revision_id'] = None
        result = _page(s, model, rows)
        setattr(output.effect, kind, [type(item).model_validate(value) for item, value in zip(getattr(output.effect, kind), result['items'])])
        output.prospective_pages.append(ProspectivePageOutput(request=model.request, facts_fingerprint=output.facts_fingerprint,
            kind=kind, total_count=len(rows), next_cursor=result['next_cursor']))
    output.effect.operation_id = None
    output.effect.audit_event_id = None
    # This convenience subset shares the complete document_changes channel.
    output.effect.payment_changes = output.effect.payment_changes[:50]
    if output.changed:
        output.current.revision_id = None
        output.effect.after_header.revision_id = None
    return output


def operation(s, inp):
    row = operations.find(s, inp.operation_key)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'payment_operation'})
    from bookflow.company.payment_authority import authorize
    request = json.loads(row['request_snapshot'])
    authorize(s, request['resolved_transaction_ids'])
    return row, request
