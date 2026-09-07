"""Private ordinary deposit aggregate. Caller owns the company writer transaction.

No registry registration, draft provider, nested dispatch, or transaction commit.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from bookflow.core.publication import OSBinding
    from bookflow.adapters.http.app import Credential
from dataclasses import dataclass
import hashlib
import hmac
import json
import sqlalchemy as sa
from bookflow.company import schema as c, deposits, deposit_sources, deposit_validation
from bookflow.company import deposit_dependencies as dependencies, deposit_operations as operations
from bookflow.company import document_effects as effects, journals, sales, sales_defaults as defaults
from bookflow.company import journal_custom_fields as custom, payment_queries as q
from bookflow.company.deposit_models import (ReplacementDocument, InlineDocument, Account,
    SourceRow, Additional, CashBack, Dimensions, Intent, Effect, amount)
from bookflow.company.deposit_lifecycle_models import PostInput, UpdateInput, VoidInput
from bookflow.company.deposit_resolution import resolve_account, resolve_additional
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.exact import INT64_MAX


@dataclass(frozen=True)
class Prepared:
    verb: str
    input_json: str
    facts_fingerprint: str
    dependency_guard: str
    data_json: str
    custom_plan: custom.JournalCustomFieldPlan | None
    binding: OSBinding | Credential


INPUTS={'post':PostInput,'update':UpdateInput,'void':VoidInput}



def _business(effect):
    value=effect.model_dump(mode='json')
    # Expected source header version is concurrency evidence, not cash history.
    for row in value['intent']['sources']:
        row['source'].pop('expected_header_version')
    return value


def _logical(value, mapping):
    if isinstance(value,dict):return {k:_logical(v,mapping) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_logical(v,mapping) for v in value]
    if isinstance(value,str):
        if value in mapping:return mapping[value]
        for identity,token in mapping.items():
            value=value.replace(identity,token)
    return value



def recover(s,ctx,inp,verb,binding):
    """Exact business replay skips its old guard, never current admission."""
    from bookflow.company import deposit_dependency_history as history
    history._authorize_binding_graph(s,binding,(),write=True)
    saved=operations.find(s,inp.operation_key)
    if saved is not None:
        targets=effects.rows(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id==saved['id'])
        history._authorize_binding_graph(s,binding,[row['transaction_id'] for row in targets],write=True)
    return operations.recover(s,ctx,inp,verb)


def prepare(s,ctx,inp,verb, *, binding=None, expected_guard=None):
    """Complete snapshot resolution without DML; prospective IDs remain private."""
    if verb not in INPUTS:raise ValueError('unsupported private deposit action')
    inp=INPUTS[verb].model_validate_json(inp.model_dump_json(by_alias=True,exclude_unset=True))
    from bookflow.company import deposit_dependency_history as history
    from bookflow.core.publication import OSBinding
    if binding is None:
        binding = OSBinding.from_session(s, ctx.on_behalf_of)
    recovered=recover(s,ctx,inp,verb,binding)
    if recovered is not None:return recovered
    original_request = history.request(dict(command='deposit '+verb,
        input=inp.model_dump(mode='json', by_alias=True, exclude_unset=True),
        context={key: value for key, value in {'reason': ctx.reason, 'directive_id': ctx.directive_id}.items() if value is not None}))
    supplied_guard = getattr(inp, 'dependency_guard', None)
    if supplied_guard is None:
        supplied_guard = expected_guard
    if supplied_guard is not None:
        comparison = history.compare(s, supplied_guard, original_request, binding)
        if not comparison.matches:
            from bookflow.company.deposit_dependency_models import PageInput
            from bookflow.company.deposit_dependency_pages import changes_page
            page = changes_page(s, supplied_guard, original_request, PageInput(), binding)
            raise BookflowError('E_PREVIEW_STALE', details={'reason': 'deposit_dependencies',
                'history': 'unknown_history' if comparison.unknown_history else 'known_stale',
                'changes': page.model_dump(mode='json'),
                'original_request': original_request.model_dump(mode='json', by_alias=True, exclude_unset=True)})
    old=None;prior=None;previous=None
    if verb!='post':
        found=effects.rows(s,c.transactions,c.transactions.c.id==inp.deposit,c.transactions.c.type=='deposit')
        if len(found)!=1:raise BookflowError('E_RECORD_NOT_FOUND')
        old=found[0]
    requested=[] if verb=='void' or inp.document.mode=='draft' else [r.source for r in inp.document.sources]
    targets=dependencies.authorize(s,old['id'] if old else None,requested,write=True)
    history._authorize_binding_graph(s,binding,targets,write=True)
    if verb!='post' and (not ctx.reason or not ctx.reason.strip() or len(ctx.reason)>140):
        raise BookflowError('E_REASON_REQUIRED')
    if operations.find(s, inp.operation_key) is not None:
        raise BookflowError('E_DEPOSIT_OPERATION_KEY_REUSED')
    if old:
        history.version_meta(s,old,inp.expected_version,binding)
        prior=effects.rows(s,c.transaction_revisions,c.transaction_revisions.c.id==old['current_revision_id'])[0]
        profile=effects.rows(s,c.deposit_profiles,c.deposit_profiles.c.revision_id==prior['id'])[0]
        previous=Effect.model_validate_json(profile['facts_snapshot'])
        if verb=='update' and old['status']!='posted':raise BookflowError('E_VALIDATION')
    identity=old['id'] if old else new_id()
    keys=effects.rows(s,c.deposit_row_keys,c.deposit_row_keys.c.transaction_id==identity)
    headers=[r for r in keys if r['kind']=='header']
    if len(headers)>1:raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
    header_row=headers[0]['id'] if headers else new_id()
    maximum=max((r['ordinal'] for r in keys),default=0)
    mapping={} if old else {identity:'new-deposit'}
    if not headers:mapping[header_row]='new-header'
    sequence=None;custom_plan=None
    if verb=='void':
        resolved=previous;number=old['number'];memo=prior['memo'];changed=old['status']=='posted'
    else:
        doc=inp.document
        if doc.mode!='inline':
            raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'reason':'private draft provider not allocated'})
        number,sequence=effects.allocate(s,'deposit',doc.number,identity if old else None)
        memo=doc.memo
        source_rows=[];additional=[]
        oldsources={r.source.transaction_id:r for r in previous.intent.sources} if previous else {}
        oldextras={r.row_id:r for r in previous.intent.additional} if previous else {}
        extra_lines={r['line_id']:oldextras[r['id']] for r in keys if r['id'] in oldextras}
        for index,value in enumerate(doc.sources):
            source=deposit_sources.load(s,value.source)
            if source.source_type!=value.source_type:raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
            claimed=dependencies.active_claim(s,value.source)
            if claimed and claimed['transaction_id']!=identity:
                raise BookflowError('E_DEPOSIT_SOURCE_CLAIMED',details=dependencies.claim_details(s,value.source,claimed))
            h=effects.rows(s,c.transactions,c.transactions.c.id==value.source)[0]
            history.version_meta(s,h,value.expected_version,binding)
            retained=oldsources.get(value.source)
            if retained:
                row_id,ordinal=retained.row_id,retained.ordinal
            else:
                maximum+=1;row_id,ordinal=new_id(),maximum;mapping[row_id]=f'new-source-{index}'
            # Complete replacement: omission chooses captured source memo;
            # explicit null retains accepted G1's intentionally blank override.
            entered='memo_override' in value.model_fields_set
            origin='entered' if entered else 'source'
            rowmemo=value.memo_override if entered else source.source_memo
            source_rows.append(SourceRow(row_id=row_id,ordinal=ordinal,source=source,
                occurrences=deposits.occurrences(source,retained.occurrences if retained else ()),memo=rowmemo,memo_origin=origin))
        for index,value in enumerate(doc.additional):
            retained=extra_lines.get(value.line_id) if value.line_id else None
            if value.line_id and retained is None:raise BookflowError('E_VALIDATION',details={'field':'line_id'})
            if retained:row_id,ordinal=retained.row_id,retained.ordinal
            else:
                maximum+=1;row_id,ordinal=new_id(),maximum;mapping[row_id]=f'new-additional-{index}'
            additional.append(resolve_additional(s,value,row_id,ordinal,retained))
        bank=resolve_account(s,doc.deposit_to,previous.intent.bank if previous else None)
        cashback=CashBack(account=resolve_account(s,doc.cash_back.account,previous.intent.cash_back.account if previous and previous.intent.cash_back else None),
            units=amount(doc.cash_back.amount,s.company_info_row['home_currency']),memo=doc.cash_back.memo) if doc.cash_back else None
        resolved=deposits.prepare(Intent(deposit_id=identity,date=doc.date,currency=s.company_info_row['home_currency'],bank=bank,
            sources=tuple(source_rows),additional=tuple(additional),cash_back=cashback))
        custom.validate_kinds(s.company,doc.custom_fields,doc.expected_custom_field_kinds,record_type='deposit')
        from bookflow.company.custom_fields import CustomFieldValuePatch
        old_custom=json.loads(prior['custom_fields_snapshot']) if prior else {}
        full_custom=CustomFieldValuePatch({**{key:None for key in old_custom if key not in doc.custom_fields.root},**doc.custom_fields.root})
        custom_plan=custom.prepare(s.company,identity,full_custom,old_custom,creating=old is None,record_type='deposit')
        changed=old is None or _business(resolved)!=_business(previous) or (number,memo)!=(prior['number'],prior['memo']) or custom_plan.changed
    if changed:
        journals.open_dates(s,[resolved.intent.date]+([prior['date']] if prior else []))
        if verb!='void':
            deposit_validation.validate_current(resolved,s,replacing_deposit=identity,previous=previous)
    claim_rows=effects.rows(s,c.deposit_current_memberships,c.deposit_current_memberships.c.transaction_id==identity)
    current_claims=[effects.rows(s,c.deposit_memberships,c.deposit_memberships.c.id==r['membership_id'])[0] for r in claim_rows]
    source_headers={source:effects.rows(s,c.transactions,c.transactions.c.id==source)[0] for source in sorted(set(requested)|{r['source_transaction_id'] for r in current_claims})}
    for source in requested:
        claim=dependencies.active_claim(s,source)
        if claim and claim['transaction_id']!=identity:
            raise BookflowError('E_DEPOSIT_SOURCE_CLAIMED',details=dependencies.claim_details(s,source,claim))
    if changed and any(h['version']>=INT64_MAX for h in ([old] if old else [])+list(source_headers.values())):
        raise BookflowError('E_VALUE_RANGE')
    dependencies.reconciliation_status(s.company)
    account_ids={resolved.intent.bank.id}|{r.account.id for r in resolved.intent.additional}
    if resolved.intent.cash_back:account_ids.add(resolved.intent.cash_back.account.id)
    reference_accounts=[effects.rows(s,c.accounts,c.accounts.c.id==key)[0] for key in sorted(account_ids)]
    facts=dict(accounts=reference_accounts,request=operations.request(inp,ctx,s,verb),before=old,sources=source_headers,claims=current_claims,
        resolved=_logical(resolved.model_dump(mode='json'),mapping),number=number,memo=memo,
        custom=sales._custom_semantic(custom_plan.snapshot) if custom_plan else None,
        closing=s.company_info_row.get('closing_date'),schema='co0021')
    fingerprint=q.digest(facts)
    recipe, readset = history.capture(s, original_request, binding)
    guard = history.issue(s, recipe, readset, binding)
    if inp.expected_facts_fingerprint is not None and inp.expected_facts_fingerprint!=fingerprint:
        raise BookflowError('E_PREVIEW_STALE',details={'reason':'deposit_facts'})
    data=dict(before=old,prior=prior,previous=previous.model_dump(mode='json') if previous else None,
        financial=resolved.model_dump(mode='json'),changed=changed,identity=identity,header_row=header_row,
        header_ordinal=headers[0]['ordinal'] if headers else maximum+1,number=number,memo=memo,
        source_headers=source_headers,claims=current_claims,targets=targets,sequence=sequence,
        mapping=mapping,at=clock.now_iso(),event=new_id(),operation_id=new_id(),
        issuer=json.loads(prior['issuer_snapshot']) if prior else {**{k:v for k,v in s.company_info_row.items() if k in ('id','legal_name','home_currency') or k.startswith(('address_','legal_address_','ship_address_'))}, 'display_name':readset.issuer.display_name})
    return Prepared(verb,inp.model_dump_json(by_alias=True,exclude_unset=True),fingerprint,guard,q.canonical(data),custom_plan,binding)
