"""Read-only prospective effect pages, bound to exact original intent and facts."""
import json
from copy import deepcopy

from bookflow.company import payment_queries as query, payment_operations as operations, payments
from bookflow.company.payment_models import PaymentPreviewItemsInput
from bookflow.company.payment_outputs import PaymentEffectItemsOutput, ProspectivePageOutput
from bookflow.core.errors import BookflowError

KINDS = ('source_components', 'applications', 'allocations', 'document_changes')


def request(inp, ctx, operation):
    return dict(command='payment ' + operation, input=inp.model_dump(mode='json', exclude_unset=True),
        context=({} if ctx.reason is None else {'reason': ctx.reason}) |
                ({} if ctx.directive_id is None else {'directive_id': ctx.directive_id}))


def logical_items(output, kind):
    rows = deepcopy(output.effect.model_dump(mode='json')[kind])
    for row in rows:
        for field in ('application_id', 'allocation_id'):
            if field in row:
                row[field] = None
        if output.effect.kind == 'receive':
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
        result = query.page(s, 'payment preview items', model, items, facts=[output.facts_fingerprint, kind, items])
        setattr(output.effect, kind, [type(item).model_validate(value) for item, value in zip(getattr(output.effect, kind), result['items'])])
        pages.append(ProspectivePageOutput(request=model.request, facts_fingerprint=output.facts_fingerprint,
            kind=kind, total_count=len(items), next_cursor=result['next_cursor']))
    output.effect.operation_id = None
    if output.effect.kind == 'receive':
        for component in output.current.components:
            component.component_key_id = None
            component.component_id = None
    output.prospective_pages = pages
    output.current.components = output.current.components[:50]
    return output


def items(s, ctx, inp):
    original = inp.request
    if operations.find(s, original.input.operation_key) is not None:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'intent_already_committed'})
    context = ctx.model_copy(update={'reason': original.context.reason, 'directive_id': original.context.directive_id})
    try:
        plan = payments.prepare(s, context, original.input, original.command.split()[-1])
    except BookflowError as exc:
        if exc.code in ('E_VERSION_CONFLICT', 'E_SELECTION_CONSUMED', 'E_APPLICATION_CAPACITY', 'E_APPLICATION_INACTIVE'):
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts', 'cause': exc.code}) from None
        raise
    if plan.preview.facts_fingerprint != inp.facts_fingerprint:
        raise BookflowError('E_PREVIEW_STALE', details={'reason': 'payment_facts'})
    rows = logical_items(plan.preview, inp.kind)
    result = query.page(s, 'payment preview items', inp, rows, facts=[inp.facts_fingerprint, inp.kind, rows])
    return PaymentEffectItemsOutput(**result, projection='prospective')


def operation(s, inp):
    row = operations.find(s, inp.operation_key)
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'payment_operation'})
    from bookflow.company.payment_authority import authorize
    request = json.loads(row['request_snapshot'])
    authorize(s, request['resolved_transaction_ids'])
    return row, request
