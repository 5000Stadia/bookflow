"""Owned immutable draft expansion below bounded transport; no independent writer."""
import json
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_drafts as drafts, deposit_draft_validation as validation
from bookflow.company import payment_queries as q, deposit_sources
from bookflow.company.deposit_models import (Frozen, SourceInput, AdditionalInput, CashBackInput,
    SignedMoney, InlineDocument, ReplacementDocument, DraftDocument)
from bookflow.company.custom_fields import CustomFieldValuePatch, CustomFieldKindExpectations
from bookflow.core.errors import BookflowError
from bookflow.company.deposit_coordinate_models import CoordinateDocument, SourceResult


class DraftPin(Frozen):
    id: str
    version: int
    revision_id: str
    revision_number: int
    manifest_hash: str
    snapshot: str
    header_json: str
    keys_json: str


class ResolvedDepositDocument(Frozen):
    """Internal complete input; only the authenticated resolver supplies draft pins."""
    deposit_to: str
    date: str
    number: str | None
    memo: str | None
    cash_back: CashBackInput | None
    custom_fields: CustomFieldValuePatch
    expected_custom_field_kinds: CustomFieldKindExpectations
    sources: tuple[SourceInput | SourceResult, ...]
    additional: tuple[AdditionalInput, ...]
    pin: DraftPin | None = None


def load(s, ctx, doc, binding, *, target=None, expected_target_version=None):
    if type(doc) in (InlineDocument, ReplacementDocument, CoordinateDocument):
        return ResolvedDepositDocument(**{k:getattr(doc,k) for k in ('deposit_to','date','number','memo','cash_back','custom_fields','expected_custom_field_kinds')},sources=tuple(doc.sources),additional=tuple(doc.additional))
    if type(doc) is not DraftDocument:
        raise BookflowError('E_VALIDATION')
    h,r,m,_=drafts.load(s,doc.draft,ctx=ctx,binding=binding,write=True)
    drafts._version(s,h,doc.expected_version)
    issues=reference_issues(s,m,h['edit_transaction_id'])
    if issues:raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'issues':list(issues)})
    if h['edit_transaction_id']!=target or (target is not None and h['baseline_version']!=expected_target_version):
        raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'reason':'edit_target'})
    if target is not None:
        actual=drafts._row(s,c.transactions,target)
        validation.require(actual['current_revision_id']==h['baseline_revision_id'],'edit_revision')
    if m.summary.issues:
        raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'issues':list(m.summary.issues)})
    keys=list(s.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==h['id']).order_by(c.deposit_draft_row_keys.c.ordinal)).mappings())
    return from_manifest(h,r,m,[dict(k) for k in keys])


def from_manifest(h,r,m,keys):
    """Typed conversion of verified owner facts, also used by historical recipes."""
    validation.validate_manifest(m)
    if m.summary.issues:raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'issues':list(m.summary.issues)})
    sources=[]
    for row in m.sources:
        values=dict(source=row.source.transaction_id,source_type=row.source.source_type,expected_version=row.source.expected_header_version)
        if row.memo_origin=='entered':values['memo_override']=row.memo
        sources.append(SourceInput(**values))
    additional=[]
    for row in m.additional:
        values=dict(received_from=row.received_from,from_account=row.account.id,
            amount=SignedMoney(minor_units=row.units,currency=m.currency),memo=row.memo,
            check_number=row.check_number,payment_method=row.payment_method.id if row.payment_method else None,
            **{'class':row.class_ref.id if row.class_ref else None})
        additional.append(AdditionalInput.model_validate(values))
    customs={k:v.canonical_text for k,v in m.header.custom_fields.items() if v.canonical_text is not None}
    from bookflow.company.custom_fields import typed_value_from_canonical
    customs={k:typed_value_from_canonical(m.header.custom_fields[k].kind,v) for k,v in customs.items()}
    cb=m.header.cash_back
    return ResolvedDepositDocument(deposit_to=m.header.bank.id,date=m.header.date,number=m.header.number,memo=m.header.memo,
        cash_back=CashBackInput(account=cb.account.id,amount=SignedMoney(minor_units=cb.units,currency=m.currency),memo=cb.memo) if cb else None,
        custom_fields=CustomFieldValuePatch(customs),expected_custom_field_kinds=CustomFieldKindExpectations({k:v.expected_kind for k,v in m.header.custom_fields.items() if v.expected_kind is not None}),
        sources=tuple(sources),additional=tuple(additional),pin=DraftPin(id=h['id'],version=h['version'],revision_id=r['id'],revision_number=r['version'],
            manifest_hash=r['manifest_hash'],snapshot=r['snapshot'],header_json=q.canonical(h),keys_json=q.canonical(keys)))


def read_pin(s,ctx,pin,binding):
    """Recheck immutable identity and current admission without re-expanding cash.

    Preparation already loaded the complete manifest. Financial validation still
    independently calls verify_pin; this function never certifies source facts.
    """
    validation.admit(s,ctx,binding,draft=pin.id,write=True)
    h=drafts._row(s,c.deposit_drafts,pin.id)
    drafts._version(s,h,pin.version)
    r=drafts._row(s,c.deposit_draft_revisions,h['current_revision_id'])
    validation.require(r['draft_id']==h['id'] and r['id']==pin.revision_id and r['version']==pin.revision_number and r['manifest_hash']==pin.manifest_hash and r['snapshot']==pin.snapshot,'draft_pin')
    validation.require(q.canonical(h)==pin.header_json,'draft_header')
    keys=list(s.company.conn.execute(sa.select(c.deposit_draft_row_keys).where(c.deposit_draft_row_keys.c.draft_id==h['id']).order_by(c.deposit_draft_row_keys.c.ordinal)).mappings())
    validation.require(q.canonical([dict(k) for k in keys])==pin.keys_json,'draft_keys')
    from bookflow.company.deposit_draft_models import Manifest
    m=Manifest.model_validate_json(r['snapshot'])
    validation.validate_manifest(m)
    validation.require(q.digest(m.model_dump(mode='json'))==r['manifest_hash'],'draft_manifest_hash')
    return h,r,m


def verify_pin(s,ctx,pin,binding):
    h,r,m=read_pin(s,ctx,pin,binding)
    # Independent SQL source/history/row decoding remains mandatory at the
    # financial validator. Equality above alone is never a provenance proof.
    loaded=drafts.load(s,pin.id,ctx=ctx,binding=binding,write=True)
    validation.require(loaded[2]==m,'draft_manifest')
    return h,r,m


def validate_row_origins(s, header, keys):
    """Original keys prove pinned composition membership; only edit reuses IDs."""
    origin = header['edit_transaction_id'] or header['copy_transaction_id']
    revision_id = header['baseline_revision_id'] or header['copy_revision_id']
    members = {}
    if origin is not None:
        from bookflow.company.deposit_models import Effect
        profile = s.company.conn.execute(sa.select(c.deposit_profiles).where(
            c.deposit_profiles.c.transaction_id == origin,
            c.deposit_profiles.c.revision_id == revision_id)).mappings().one_or_none()
        validation.require(profile is not None, 'draft_origin_profile')
        try:
            effect = Effect.model_validate_json(profile['facts_snapshot'])
        except (ValueError,TypeError):
            raise BookflowError('E_VALIDATION',details={'reason':'draft_origin_profile'}) from None
        for kind, group in (('source', effect.intent.sources), ('additional', effect.intent.additional)):
            for row in group:
                validation.require(row.row_id not in members, 'draft_origin_bijection')
                members[row.row_id] = (kind, row.ordinal)
    seen = set()
    for key in keys:
        pair = (key['edit_transaction_id'], key['original_row_id'])
        if pair == (None, None):
            continue
        validation.require(origin is not None and pair[0] == origin, 'draft_origin_owner')
        validation.require(members.get(pair[1]) == (key['kind'], key['ordinal']), 'draft_origin_member')
        validation.require(pair[1] not in seen, 'draft_origin_bijection')
        seen.add(pair[1])
        raw = s.company.conn.execute(sa.select(c.deposit_row_keys).where(
            c.deposit_row_keys.c.id == pair[1], c.deposit_row_keys.c.transaction_id == origin)).mappings().one_or_none()
        validation.require(raw is not None and (raw['kind'], raw['ordinal']) == members[pair[1]], 'draft_origin_key')


def coordinate(s,ctx,inp,binding):
    if inp.replacement.mode!='document':return None
    from bookflow.company.deposit_coordinate_models import source_identity
    doc=load(s,ctx,inp.replacement.document,binding,target=inp.deposit,expected_target_version=inp.expected_version)
    if doc.pin is None:return doc
    identity=source_identity(inp.source_action)
    entries=[r for r in doc.sources if r.source==identity]
    if inp.replacement.draft_source_result=='remove':
        if entries:raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'reason':'source_remove_requires_absence'})
        return doc
    expected=inp.source_action.input.expected_version if inp.source_action.kind.endswith('_update') else inp.source_action.expected_version
    if len(entries)!=1 or entries[0].expected_version!=expected:
        raise BookflowError('E_DEPOSIT_DRAFT_STATE',details={'reason':'source_retain_requires_exact_baseline'})
    value=dict(source_result=True,source=identity)
    if 'memo_override' in entries[0].model_fields_set:value['memo_override']=entries[0].memo_override
    return doc.model_copy(update={'sources':tuple(SourceResult(**value) if r.source==identity else r for r in doc.sources)})


def reference_issues(s,manifest,edit=None):
    """Current provisional eligibility; never rewrites captured references/defaults."""
    issues=[]
    def account(ref,label,bank=False):
        if ref is None:return
        row=s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id==ref.id)).mappings().one_or_none()
        if row is None or not row['active'] or row['currency']!=manifest.currency:
            issues.append(label+':unavailable')
        elif bank and (row['type']!='bank' or row['system_role']=='undeposited_funds'):
            issues.append(label+':invalid_type')
        elif row['type']!=ref.type or row['system_role']!=ref.system_role:
            issues.append(label+':changed_type')
    account(manifest.header.bank,'deposit_to',True)
    if manifest.header.cash_back:account(manifest.header.cash_back.account,'cash_back.account')
    for row in manifest.additional:
        prefix='additional:'+row.row_id
        account(row.account,prefix+'.account')
        refs=[('class',c.classes,row.class_ref.id if row.class_ref else None),
              ('payment_method',c.payment_methods,row.payment_method.id if row.payment_method else None)]
        if row.received_from:
            tables={'customer':c.customers,'vendor':c.vendors,'employee':c.employees,'other_name':c.other_names}
            refs.append(('received_from',tables[row.received_from.kind],row.received_from.id))
        for label,table,identity in refs:
            if identity is not None:
                value=s.company.conn.execute(sa.select(table.c.active).where(table.c.id==identity)).scalar_one_or_none()
                if value is not True:issues.append(prefix+'.'+label+':unavailable')
    from bookflow.company import custom_fields as cf, journal_custom_fields as custom_owner
    definitions={r['id']:r for r in cf._applicable_definitions(s.company.conn,'deposit')}
    slots=custom_owner._slots(s.company.conn,edit,record_type='deposit') if edit else {}
    for key,capture in manifest.header.custom_fields.items():
        value=definitions.get(key)
        if value is None or value['kind']!=capture.kind:
            issues.append('custom_field:'+key+':changed')
        elif not value['active'] and capture.canonical_text is not None:
            slot=slots.get(key)
            if slot is None or not slot['active'] or slot['canonical_text']!=capture.canonical_text:
                issues.append('custom_field:'+key+':unavailable')
    for key,value in definitions.items():
        captured=manifest.header.custom_fields.get(key)
        if value['active'] and value['required'] and (captured is None or captured.canonical_text is None):
            issues.append('custom_field:'+key+':required')
    return tuple(issues)


def validate_references(s,financial,manifest,edit=None):
    """Provisional references stay eligible; their captured display facts persist."""
    validation.require(not reference_issues(s,manifest,edit),'draft_references')
    actual=financial.intent
    validation.require(actual.bank==manifest.header.bank,'draft_bank_capture')
    captured_cash=manifest.header.cash_back
    validation.require((actual.cash_back is None)==(captured_cash is None),'draft_cashback')
    if captured_cash:
        validation.require(actual.cash_back.account==captured_cash.account,'draft_cashback_capture')
    validation.require(len(actual.additional)==len(manifest.additional),'draft_row_count')
    for captured,output in zip(manifest.additional,actual.additional):
        validation.require(output.account==captured.account,'draft_account_capture')
        validation.require(output.dimensions.party_name==captured.party_name,'draft_party_capture')
        validation.require(output.dimensions.class_name==(captured.class_ref.label if captured.class_ref else None),'draft_class_capture')
        validation.require(output.payment_method==captured.payment_method,'draft_method_capture')


def _validate_financial(s,ctx,data,financial,binding,overlay=None,custom_plan=None):
    """Independent complete draft-to-effect incidence and scalar checks."""
    if data.get('draft') is None:return
    pin=DraftPin.model_validate_json(q.canonical(data['draft']))
    h,r,m=verify_pin(s,ctx,pin,binding)
    validate_references(s,financial,m,h['edit_transaction_id'])
    from bookflow.company import deposit_draft_custom_fields as draft_custom
    validation.require(custom_plan is not None,'draft_custom_plan')
    draft_custom.validate(s,custom_plan,data['identity'],json.loads(custom_plan.previous_json),m,pin.manifest_hash,custom_plan.snapshot)
    actual=financial.intent
    validation.require((actual.date,actual.currency,actual.bank.id)==(m.header.date,m.currency,m.header.bank.id),'draft_header_values')
    validation.require(data['memo']==m.header.memo and (m.header.number is None or data['number']==m.header.number),'draft_header_values')
    if m.header.cash_back:
        validation.require(actual.cash_back is not None and (actual.cash_back.account.id,actual.cash_back.units,actual.cash_back.memo)==
            (m.header.cash_back.account.id,m.header.cash_back.units,m.header.cash_back.memo),'draft_cashback')
    else:validation.require(actual.cash_back is None,'draft_cashback')
    validation.require(len(actual.sources)==len(m.sources) and len(actual.additional)==len(m.additional),'draft_row_count')
    keys={k['id']:k for k in json.loads(pin.keys_json)}
    for captured,output in zip(m.sources,actual.sources):
        validation.require(output.ordinal==captured.ordinal,'draft_ordinal')
        if overlay is not None and captured.source.transaction_id==overlay.source_id:
            validation.require(output==overlay.retained_row,'draft_overlay')
        else:
            from bookflow.company import deposits
            validation.require(output.occurrences==deposits.occurrences(captured.source,captured.occurrences),'draft_occurrences')
            validation.require(output.source==captured.source and (output.memo,output.memo_origin)==(captured.memo,captured.memo_origin),'draft_source')
    for captured,output in zip(m.additional,actual.additional):
        validation.require((output.ordinal,output.units,output.account.id,output.memo,output.check_number)==
            (captured.ordinal,captured.units,captured.account.id,captured.memo,captured.check_number),'draft_additional')
        validation.require((output.dimensions.party_kind,output.dimensions.party_id,output.dimensions.class_id)==
            (captured.received_from.kind,captured.received_from.id,captured.class_ref.id if captured.class_ref else None),'draft_dimensions')
        validation.require((output.payment_method.id if output.payment_method else None)==(captured.payment_method.id if captured.payment_method else None),'draft_method')
    for captured,output in [*zip(m.sources,actual.sources),*zip(m.additional,actual.additional)]:
        key=keys[captured.row_id]
        if h['edit_transaction_id'] is not None and key['edit_transaction_id']==h['edit_transaction_id']:
            validation.require(output.row_id==key['original_row_id'],'draft_retained_identity')
        else:
            validation.require(data['mapping'].get(output.row_id)=='draft-row-'+captured.row_id,'draft_new_identity')


def validate_financial(s,ctx,data,financial,binding,overlay=None,custom_plan=None):
    try:
        _validate_financial(s,ctx,data,financial,binding,overlay,custom_plan)
    except (ValueError,TypeError,KeyError,AttributeError):
        raise BookflowError('E_VALIDATION',details={'reason':'draft_financial_facts'}) from None
