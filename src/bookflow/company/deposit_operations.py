"""Private permanent deposit-family intent identity and current-authority recovery."""
import json
from pydantic import BaseModel
from bookflow.company import schema as c, document_effects as rows, payment_queries as q
from bookflow.company.deposit_models import AdditionalInput, CashBackInput, amount
from bookflow.company.deposit_lifecycle_models import LifecycleOutput, DocumentState
from bookflow.company import deposit_dependencies as dependencies
from bookflow.core.errors import BookflowError


def request(inp, ctx, s, verb, *, original=False):
    excluded = {'operation_key'} if original else {'operation_key','expected_facts_fingerprint','dependency_guard'}
    def typed(value):
        if isinstance(value, BaseModel):
            result = value.model_dump(mode='json', by_alias=True, exclude_unset=True)
            for name, field in type(value).model_fields.items():
                key = field.alias or name
                if key not in result:
                    continue
                raw = getattr(value, name)
                if verb == 'coordinate' and raw is not None and _source_money_field(type(value), name):
                    from bookflow.company.sales_models import money
                    result[key] = money(raw, s.company_info_row['home_currency'], key).to_dict()
                elif name == 'amount' and isinstance(value, (AdditionalInput, CashBackInput)):
                    result[key] = dict(minor_units=amount(raw, s.company_info_row['home_currency']), currency=s.company_info_row['home_currency'])
                elif isinstance(raw, (BaseModel, list)):
                    result[key] = typed(raw)
            return result
        return [typed(item) for item in value] if isinstance(value, list) else value
    return dict(schema_version=2 if verb=='coordinate' else 1, command='deposit '+verb, company_id=s.company_row['id'],
        input={k:v for k,v in (inp.model_dump(mode='json',by_alias=True,exclude_unset=True) if original else typed(inp)).items() if k not in excluded},
        provided_fields=sorted(inp.model_fields_set-excluded),
        context={'reason':ctx.reason} if ctx.reason is not None else {},
        context_provided_fields=['reason'] if ctx.reason is not None else [])


def _find(s, key):
    found = rows.rows(s, c.deposit_operations, c.deposit_operations.c.operation_key == key)
    return found[0] if found else None


def find(s, key):
    from bookflow.storage.migrate import feature_admission, FeatureRevision
    resolver=feature_admission(s.company,FeatureRevision('company','co0021'),resolver=_find)
    if resolver is None:
        raise BookflowError('E_SCHEMA_BEHIND')
    return resolver(s,key)


def state(s, identity):
    found = rows.rows(s,c.transactions,c.transactions.c.id==identity,c.transactions.c.type=='deposit')
    if len(found)!=1:
        raise BookflowError('E_RECORD_NOT_FOUND')
    h=found[0]
    profile=rows.rows(s,c.deposit_profiles,c.deposit_profiles.c.revision_id==h['current_revision_id'])[0]
    revision=rows.rows(s,c.transaction_revisions,c.transaction_revisions.c.id==h['current_revision_id'])[0]
    claims=rows.rows(s,c.deposit_current_memberships,c.deposit_current_memberships.c.transaction_id==identity)
    return DocumentState(id=h['id'],version=h['version'],revision_id=h['current_revision_id'],number=h['number'],status=h['status'],
        revision_date=revision['date'],currency=revision['currency'],revision_posting_total=profile['posting_total'],revision_subtotal=profile['subtotal'],
        revision_bank_total=profile['bank_total'],revision_cash_back=profile['cash_back'],effective_bank_total=profile['bank_total'] if h['status']=='posted' else 0,
        active_source_ids=tuple(sorted(r['source_transaction_id'] for r in claims)))


def recover(s, ctx, inp, verb):
    """Read-only; no default resolution, maintenance, event, or principal upsert."""
    saved=find(s,inp.operation_key)
    if saved is None:
        return None
    targets=rows.rows(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id==saved['id'])
    try:
        dependencies.authorize(s,saved['transaction_id'],[r['transaction_id'] for r in targets],write=True)
    except BookflowError as error:
        if error.code=='E_PERMISSION':raise BookflowError('E_PERMISSION',details={}) from None
        raise
    if saved['command']!='deposit '+verb or saved['request_hash']!=q.digest(request(inp,ctx,s,verb)):
        return None
    output=LifecycleOutput.model_validate_json(saved['effect_snapshot'])
    return output.model_copy(update={'changed':False,'new_effect':False,'idempotent_replay':True,'current':state(s,saved['transaction_id'])})


def permanent_recovery(inp, ctx, s, verb):
    """Hook-compatible exact-match adapter, deliberately not registered live."""
    from bookflow.core.registry import MatchedRecovery
    output = recover(s, ctx, inp, verb)
    return MatchedRecovery(output) if output is not None else None


def _source_money_field(model, field):
    """Only declared source Money unions; arbitrary custom properties stay literal."""
    from typing import get_args
    from bookflow.company.sales_models import SalesMoneyInput
    annotation = model.model_fields[field].annotation
    return SalesMoneyInput in get_args(annotation)


def decode_output(snapshot, command):
    if command == 'deposit coordinate':
        from bookflow.company.deposit_coordinate_models import CoordinateOutput
        return CoordinateOutput.model_validate_json(snapshot)
    return LifecycleOutput.model_validate_json(snapshot)
