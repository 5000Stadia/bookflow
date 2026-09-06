"""Permanent original-intent receipts and currently authorized exact recovery."""
import json
from typing import get_args
from pydantic import BaseModel

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


def original_request(inp, ctx, s, command):
    value = request(inp, ctx, s, command)
    if 'expected_facts_fingerprint' in inp.model_fields_set:
        value['input']['expected_facts_fingerprint'] = inp.expected_facts_fingerprint
        value['provided_fields'] = sorted([*value['provided_fields'], 'expected_facts_fingerprint'])
    return value


def request_hash(inp, ctx, s, command):
    from bookflow.company.sales_models import money, SalesMoneyInput
    value = request(inp, ctx, s, command)
    currency = s.company_info_row['home_currency']
    def owns_money(annotation):
        return annotation is SalesMoneyInput or any(owns_money(arg) for arg in get_args(annotation))
    def normalize(item):
        if isinstance(item, BaseModel):
            result = item.model_dump(mode='json', exclude_unset=True)
            # Inspect the owning model, never arbitrary JSON keys: custom fields
            # named amount remain custom data, while every typed price is money.
            if isinstance(result, dict):
                for key, field in type(item).model_fields.items():
                    if key not in result:
                        continue
                    raw = getattr(item, key)
                    if raw is not None and owns_money(field.annotation):
                        parsed = money(raw, currency, key)
                        result[key] = dict(minor_units=parsed.minor_units, currency=parsed.currency)
                    elif isinstance(raw, (BaseModel, list)):
                        result[key] = normalize(raw)
            return result
        if isinstance(item, list):
            return [normalize(value) for value in item]
        return item
    normalized = normalize(inp)
    value['input'] = {key: normalized[key] for key in value['input']}
    return query.digest(value)


def recover(inp, ctx, s, command):
    if not inp.operation_key:
        return None
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
    if command == 'invoice update':
        from bookflow.company.sales_outputs import SalesWriteOutput
        output['settlement']['current'] = query.invoice_current(s, output['id'])
        output['settlement'].update(changed=False, new_effect=False, idempotent_replay=True)
        output.update(changed=False, idempotent_replay=True)
        return MatchedRecovery(SalesWriteOutput.model_validate(output))
    output['current'] = current_output(s, output['id'])
    output['idempotent_replay'] = True
    output['changed'] = False
    output['new_effect'] = False
    return MatchedRecovery(PaymentWriteOutput.model_validate(output))
