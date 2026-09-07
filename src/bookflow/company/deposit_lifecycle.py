"""Private ordinary deposit aggregate. Caller owns the company writer transaction.

No registry registration, nested dispatch, or transaction commit.
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
    if ctx.on_behalf_of != binding.on_behalf_of:
        raise BookflowError('E_UNAUTHENTICATED')
    saved=operations.find(s,inp.operation_key)
    if saved is not None:
        targets=effects.rows(s,c.deposit_operation_targets,c.deposit_operation_targets.c.operation_id==saved['id'])
        try:
            history._authorize_binding_graph(s,binding,[row['transaction_id'] for row in targets],write=True)
        except BookflowError as error:
            if error.code=='E_PERMISSION':raise BookflowError('E_PERMISSION',details={}) from None
            raise
    return operations.recover(s,ctx,inp,verb,binding=binding)


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
    comparison = None
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
    from bookflow.company import deposit_draft_provider as provider
    document=None if verb=='void' else provider.load(s,ctx,inp.document,binding,
        target=old['id'] if old else None,expected_target_version=inp.expected_version if old else None)
    requested=[] if document is None else [r.source for r in document.sources]
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
        resolved,number,memo,custom_plan,changed,sequence,maximum=resolve_replacement(
            s,ctx,document,identity=identity,old=old,prior=prior,previous=previous,
            keys=keys,maximum=maximum,mapping=mapping,binding=binding)
    if changed:
        journals.open_dates(s,[resolved.intent.date]+([prior['date']] if prior else []))
        if verb!='void':
            if document.pin is None:
                deposit_validation.validate_current(resolved,s,replacing_deposit=identity,previous=previous)
            else:
                deposit_validation.validate_current_sources(resolved,s,replacing_deposit=identity)
                from bookflow.company.deposit_draft_models import Manifest
                provider.validate_references(s,resolved,Manifest.model_validate_json(document.pin.snapshot),old['id'] if old else None)
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
    if document is not None and document.pin is not None:facts['draft']=document.pin.model_dump(mode='json')
    fingerprint=q.digest(facts)
    if document is not None and document.pin is not None and comparison is not None:
        # compare already proved the complete current and historical relations
        # in this same writer snapshot. All intervening resolution is read-only;
        # sealing that exact current readset avoids a duplicate third traversal.
        # No historical/current proof or fresh binding check is omitted.
        readset = comparison.current
        recipe = history.recipe_for_readset(s, original_request, readset, binding)
    else:
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
    if document is not None and document.pin is not None:data['draft']=document.pin.model_dump(mode='json')
    return Prepared(verb,inp.model_dump_json(by_alias=True,exclude_unset=True),fingerprint,guard,q.canonical(data),custom_plan,binding)


def resolve_replacement(s, ctx, doc, *, identity, old, prior, previous, keys, maximum, mapping, binding, overlay=None):
    """Shared complete replacement resolution; coordinator supplies proven overlay."""
    from bookflow.company.deposit_coordinate_models import SourceResultOverlay
    if overlay is not None and type(overlay) is not SourceResultOverlay:
        raise BookflowError('E_INTERNAL', message='Expected owned source result overlay.')
    from bookflow.company import deposit_dependency_history as history
    from bookflow.company import deposit_draft_provider as provider
    if type(doc) is not provider.ResolvedDepositDocument:
        doc=provider.load(s,ctx,doc,binding,target=old['id'] if old else None,expected_target_version=old['version'] if old else None)
    draft_manifest=None;draft_keys={}
    if doc.pin is not None:
        _,_,draft_manifest=provider.read_pin(s,ctx,doc.pin,binding)
        draft_keys={r['id']:r for r in json.loads(doc.pin.keys_json)}
    def draft_identity(row,token):
        key=draft_keys[row.row_id]
        original=key['original_row_id'] if old and key['edit_transaction_id']==identity else None
        if original is not None:
            matched=[k for k in keys if k['id']==original]
            if len(matched)!=1 or matched[0]['ordinal']!=row.ordinal:raise BookflowError('E_VALIDATION')
            return original,row.ordinal
        identifier=new_id();mapping[identifier]=token
        return identifier,row.ordinal
    number,sequence=effects.allocate(s,'deposit',doc.number,identity if old else None)
    memo=doc.memo
    source_rows=[];additional=[]
    oldsources={r.source.transaction_id:r for r in previous.intent.sources} if previous else {}
    oldextras={r.row_id:r for r in previous.intent.additional} if previous else {}
    extra_lines={r['line_id']:oldextras[r['id']] for r in keys if r['id'] in oldextras}
    from bookflow.company.deposit_coordinate_models import SourceResult
    current_claims=dependencies.active_claims(s,[v.source for v in doc.sources if not isinstance(v,SourceResult)]) if doc.pin is not None else None
    current_sources=deposit_sources.load_many(s,[v.source for v in doc.sources if not isinstance(v,SourceResult)]) if doc.pin is not None else None
    for index,value in enumerate(doc.sources):
        from bookflow.company.deposit_coordinate_models import SourceResult
        if isinstance(value, SourceResult):
            if overlay is None or overlay.retained_row is None or overlay.source_id != value.source or overlay.deposit_id != identity:
                raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
            source_rows.append(overlay.retained_row)
            continue
        source=current_sources[value.source] if current_sources is not None else deposit_sources.load(s,value.source)
        if source.source_type!=value.source_type:raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
        claimed=current_claims.get(value.source) if current_claims is not None else dependencies.active_claim(s,value.source)
        if claimed and claimed['transaction_id']!=identity:
            raise BookflowError('E_DEPOSIT_SOURCE_CLAIMED',details=dependencies.claim_details(s,value.source,claimed))
        h=effects.rows(s,c.transactions,c.transactions.c.id==value.source)[0]
        # Drafts carry complete authenticated source facts, unlike an inline
        # expected-version-only request. Report that owned pin mismatch before
        # attempting to construct an unrelated inline version-history recipe.
        if draft_manifest is not None:
            captured=draft_manifest.sources[index]
            if source!=captured.source:raise BookflowError('E_PREVIEW_STALE',details={'reason':'draft_source'})
        history.version_meta(s,h,value.expected_version,binding)
        retained=oldsources.get(value.source)
        if draft_manifest is not None:
            row_id,ordinal=draft_identity(captured,f'draft-row-{captured.row_id}')
            maximum=max(maximum,ordinal)
        elif retained:
            row_id,ordinal=retained.row_id,retained.ordinal
        else:
            maximum+=1;row_id,ordinal=new_id(),maximum;mapping[row_id]=f'new-source-{index}'
        # Complete replacement: omission chooses captured source memo;
        # explicit null retains accepted G1's intentionally blank override.
        entered='memo_override' in value.model_fields_set
        origin='entered' if entered else 'source'
        rowmemo=value.memo_override if entered else source.source_memo
        source_rows.append(SourceRow(row_id=row_id,ordinal=ordinal,source=source,
            occurrences=deposits.occurrences(source,captured.occurrences if draft_manifest is not None else retained.occurrences if retained else ()),memo=rowmemo,memo_origin=origin))
    for index,value in enumerate(doc.additional):
        retained=extra_lines.get(value.line_id) if value.line_id else None
        if draft_manifest is not None:
            captured=draft_manifest.additional[index]
            row_id,ordinal=draft_identity(captured,f'draft-row-{captured.row_id}')
            retained=oldextras.get(row_id)
            maximum=max(maximum,ordinal)
        if value.line_id and retained is None:raise BookflowError('E_VALIDATION',details={'field':'line_id'})
        if draft_manifest is not None:pass
        elif retained:row_id,ordinal=retained.row_id,retained.ordinal
        else:
            maximum+=1;row_id,ordinal=new_id(),maximum;mapping[row_id]=f'new-additional-{index}'
        resolved_row=resolve_additional(s,value,row_id,ordinal,None if draft_manifest is not None else retained)
        if draft_manifest is not None:
            resolved_row=Additional(row_id=row_id,ordinal=ordinal,account=captured.account,
                units=resolved_row.units,memo=resolved_row.memo,check_number=resolved_row.check_number,
                payment_method=captured.payment_method,dimensions=Dimensions(
                    party_kind=captured.received_from.kind,party_id=captured.received_from.id,party_name=captured.party_name,
                    class_id=captured.class_ref.id if captured.class_ref else None,class_name=captured.class_ref.label if captured.class_ref else None))
        additional.append(resolved_row)
    bank=resolve_account(s,doc.deposit_to,previous.intent.bank if previous and draft_manifest is None else None)
    cashback=CashBack(account=resolve_account(s,doc.cash_back.account,previous.intent.cash_back.account if previous and previous.intent.cash_back and draft_manifest is None else None),
        units=amount(doc.cash_back.amount,s.company_info_row['home_currency']),memo=doc.cash_back.memo) if doc.cash_back else None
    if draft_manifest is not None:
        bank=draft_manifest.header.bank
        if cashback:
            cashback=CashBack(account=draft_manifest.header.cash_back.account,units=cashback.units,memo=cashback.memo)
    resolved=deposits.prepare(Intent(deposit_id=identity,date=doc.date,currency=s.company_info_row['home_currency'],bank=bank,
        sources=tuple(source_rows),additional=tuple(additional),cash_back=cashback))
    custom.validate_kinds(s.company,doc.custom_fields,doc.expected_custom_field_kinds,record_type='deposit')
    from bookflow.company.custom_fields import CustomFieldValuePatch
    old_custom=json.loads(prior['custom_fields_snapshot']) if prior else {}
    full_custom=CustomFieldValuePatch({**{key:None for key in old_custom if key not in doc.custom_fields.root},**doc.custom_fields.root})
    if draft_manifest is None:
        custom_plan=custom.prepare(s.company,identity,full_custom,old_custom,creating=old is None,record_type='deposit')
    else:
        from bookflow.company import deposit_draft_custom_fields as draft_custom
        custom_plan=draft_custom.prepare(s,identity,old_custom,draft_manifest,doc.pin.manifest_hash,creating=old is None)
    changed=old is None or _business(resolved)!=_business(previous) or (number,memo)!=(prior['number'],prior['memo']) or custom_plan.changed
    return resolved, number, memo, custom_plan, changed, sequence, maximum
