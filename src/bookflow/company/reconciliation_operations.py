"""Private exact original receipts, independent canonical intent and replay.

No dispatch hook is registered. Runtime must authorize original action resources
and the complete saved/current graph before calling the pure comparison.
"""
import copy
from typing import Literal
import json
from pydantic import JsonValue, model_validator
from bookflow.company import reconciliation_commands_models as m
from bookflow.company.reconciliation_schema import COMMANDS
from bookflow.company.reconciliation_storage_validation import digest,RequestEnvelope,Envelope,Collection
from bookflow.company.reconciliation_preparation import require

class Request(m.Model):
    schema_version: Literal[1]=1
    command: str
    company_id: m.ID
    original_input: dict[str,JsonValue]
    provided_fields: tuple[str,...]
    context: dict[str,JsonValue]
    context_provided_fields: tuple[str,...]
    canonical_input: dict[str,JsonValue]
    resolved_captures: dict[str,JsonValue]

    @model_validator(mode='after')
    def command_shape(self):
        require(self.command in COMMANDS,'E_VALIDATION')
        model=m.INPUTS[self.command]
        model.model_validate_json(json.dumps(self.original_input))
        model.model_validate_json(json.dumps(self.canonical_input))
        require(set(self.provided_fields)==set(self.original_input),'E_VALIDATION')
        require(set(self.context_provided_fields)==set(self.context),'E_VALIDATION')
        return self

    def intent(self):
        excluded={'operation_key','expected_facts_fingerprint','dependency_guard'}
        return dict(schema_version=1,command=self.command,company_id=self.company_id,
            input={k:v for k,v in self.canonical_input.items() if k not in excluded},
            provided_fields=sorted(set(self.provided_fields)-excluded),context=self.context,
            context_provided_fields=sorted(self.context_provided_fields),resolved_captures=self.resolved_captures)

class Receipt(m.Model):
    operation_id: m.ID
    operation_key: m.OperationKey
    request: Request
    original_effect: dict[str,JsonValue]
    targets: tuple[m.OperationTarget,...]
    items: dict[Literal['request','effects','targets','generated'],tuple[dict[str,JsonValue],...]]

    @model_validator(mode='after')
    def complete(self):
        require(set(self.items)=={'request','effects','targets','generated'},'E_RECONCILIATION_SOURCE_INVALID')
        require(len({(v.kind,v.id) for v in self.targets})==len(self.targets),'E_RECONCILIATION_SOURCE_INVALID')
        require(list(self.items['targets'])==[v.model_dump(mode='json') for v in self.targets],'E_RECONCILIATION_SOURCE_INVALID')
        require(self.request.original_input['operation_key']==self.operation_key,'E_RECONCILIATION_SOURCE_INVALID')
        return self


def envelopes(receipt):
    collections={k:Collection(count=len(v),hash=digest(list(v))) for k,v in receipt.items.items()}
    request=RequestEnvelope(format=1,document=receipt.request.model_dump(mode='json'),canonical_intent=receipt.request.intent(),collections=collections)
    effect=Envelope(format=1,document=receipt.original_effect,collections=collections)
    return request,effect,digest(receipt.request.intent())


def recover(receipt,request, *, authority_transactions,current_targets,current_state):
    required={v.id for v in receipt.targets if v.kind=='transactions'}|set(current_targets)
    require(required<=set(authority_transactions),'E_PERMISSION')
    # Caller supplies exact saved capture interpretation, never fresh aliases or
    # current defaults. Unknown/mismatched intent returns to ordinary write gates.
    if receipt.operation_key!=request.original_input['operation_key'] or receipt.request.intent()!=request.intent():return None
    return m.RecoveryState(changed=False,new_effect=False,idempotent_replay=True,
        original_effect=copy.deepcopy(receipt.original_effect),current=copy.deepcopy(current_state))


def items(receipt,kind, *, limit=50,offset=0,expected_fingerprint=None):
    require(kind in receipt.items and type(limit) is int and 1<=limit<=200,'E_VALIDATION')
    values=receipt.items[kind]
    require(type(offset) is int and 0<=offset<=len(values),'E_QUERY_STALE')
    token=digest(dict(operation=receipt.operation_id,kind=kind,values=list(values)))
    require(expected_fingerprint in (None,token),'E_QUERY_STALE')
    return m.OperationPage(items=values[offset:offset+limit],count=len(values),next_offset=offset+limit if offset+limit<len(values) else None,fingerprint=token)
