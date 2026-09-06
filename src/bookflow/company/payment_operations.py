"""Permanent original-intent receipts and currently authorized exact recovery."""
import json

from bookflow.company import schema as c, document_effects as effects
from bookflow.company import payment_queries as query
from bookflow.company.payment_authority import authorize
from bookflow.core.registry import MatchedRecovery


def request(inp, ctx, s, command):
    # Context adapters use None for an absent reason. A directive is execution
    # authority, not canonical receipt intent and not needed to read its result.
    value = dict(request_schema_version=1, command=command, company_id=s.company_row['id'],
        input=inp.model_dump(mode='json', exclude_unset=True, exclude={'operation_key', 'expected_facts_fingerprint'}),
        provided_fields=sorted(inp.model_fields_set - {'operation_key', 'expected_facts_fingerprint'}),
        context={'reason': ctx.reason} if ctx.reason is not None else {},
        context_provided_fields=['reason'] if ctx.reason is not None else [])
    return value


def find(s, operation_key):
    rows = effects.rows(s, c.payment_operations, c.payment_operations.c.operation_key == operation_key)
    return rows[0] if rows else None


def request_hash(inp, ctx, s, command):
    from bookflow.company.sales_models import money
    value = request(inp, ctx, s, command)
    currency = s.company_info_row['home_currency']
    def normalize(item, key=None):
        if key == 'amount' and item is not None:
            parsed = money(item, currency, 'amount')
            return dict(minor_units=parsed.minor_units, currency=parsed.currency)
        if isinstance(item, dict):
            return {key: normalize(value, key) for key, value in item.items()}
        if isinstance(item, list):
            return [normalize(value) for value in item]
        return item
    return query.digest(normalize(value))


def recover(inp, ctx, s, command):
    operation = find(s, inp.operation_key)
    if operation is None:
        return None
    saved = json.loads(operation['request_snapshot'])
    # Authorize the entire historical effect before comparing or disclosing it.
    authorize(s, saved['resolved_transaction_ids'], write=True)
    if operation['command'] != command or operation['request_hash'] != request_hash(inp, ctx, s, command):
        return None
    from bookflow.company.payments import current_output
    from bookflow.company.payment_outputs import PaymentWriteOutput
    output = json.loads(operation['effect_snapshot'])
    output['current'] = current_output(s, output['id'])
    output['idempotent_replay'] = True
    return MatchedRecovery(PaymentWriteOutput.model_validate(output))
