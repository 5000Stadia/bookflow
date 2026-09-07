"""Private authenticated nonposting durable drafts. No registered command/provider.

Call under an ordinary authenticated Session and owned company transaction.
This module owns a nonposting rollback boundary only; stage2 consumption must
join its financial owner's existing boundary, never call this writer inside C.
"""
import json
import copy
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.company import schema as c, document_effects as rows, payment_queries as q
from bookflow.company import deposit_draft_models as m, deposit_draft_validation as v
from bookflow.company import deposit_sources, deposits, deposit_dependencies, deposit_resolution
from bookflow.company import sales_defaults as defaults, custom_fields as cf, journals
from bookflow.core import clock, audit
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Touched
from bookflow.company.deposit_models import amount, Effect


def _row(s,table,identity):
    value=s.company.conn.execute(sa.select(table).where(table.c.id==identity)).mappings().one_or_none()
    if value is None:raise BookflowError('E_RECORD_NOT_FOUND')
    return dict(value)


def load(s,identity,revision_number=None,*,kind='draft',ctx=None,binding=None,write=False):
    binding=v.admit(s,ctx,binding,**{kind:identity},write=write)
    table=getattr(c,'deposit_'+('drafts' if kind=='draft' else 'selections'))
    header=_row(s,table,identity);rt=getattr(c,'deposit_'+kind+'_revisions')
    revisions=list(s.company.conn.execute(sa.select(rt).where(rt.c[kind+'_id']==identity).order_by(rt.c.version)).mappings())
    prior=None;high_water=-1
    for index,revision in enumerate(revisions,1):
        v.require(revision['version']==index and revision['previous_revision_id']==prior,'revision_chain')
        v.require(revision['high_water']>=high_water,'ordinal_high_water')
        high_water=revision['high_water'];prior=revision['id']
    v.require(bool(revisions) and revisions[-1]['id']==header['current_revision_id'] and revisions[-1]['version']==header['version'],'current_revision')
    revision=next((r for r in revisions if r['version']==revision_number),None) if revision_number else revisions[-1]
    if revision is None:raise BookflowError('E_RECORD_NOT_FOUND')
    if kind=='draft':
        maximum=s.company.conn.execute(sa.select(sa.func.max(c.deposit_draft_row_keys.c.ordinal)).where(c.deposit_draft_row_keys.c.draft_id==identity)).scalar_one() or 0
        v.require(high_water>=maximum,'ordinal_high_water')
    manifest=v.decode_revision(s,header,revision,kind)
    return header,dict(revision),manifest,binding


def output(s,header,revision,manifest,kind='draft'):
    edit=header.get('edit_transaction_id')
    if kind=='selection':edit=_row(s,c.deposit_drafts,header['target_draft_id'])['edit_transaction_id']
    stale=v.stale_sources(s,manifest,edit)
    common=dict(id=header['id'],version=header['version'],state=header['state'],revision_id=revision['id'],revision_number=revision['version'],
        manifest_hash=revision['manifest_hash'],stale_source_ids=stale)
    if kind=='draft':return m.DraftOutput(**common,header=manifest.header,summary=manifest.summary,edit_transaction_id=edit,baseline_version=header['baseline_version'],copy_transaction_id=header['copy_transaction_id'])
    return m.SelectionOutput(**common,source_count=len(manifest.sources),source_total=manifest.summary.source_total,
        target_draft_id=header['target_draft_id'],target_revision_id=header['target_revision_id'],accepted_revision_id=header['accepted_revision_id'])


def show(s,inp,*,ctx=None,binding=None):
    h,r,manifest,_=load(s,inp.draft,inp.revision_number,ctx=ctx,binding=binding)
    return output(s,h,r,manifest)


def _version(s,header,expected):
    if header['version']!=expected:
        from bookflow.core import versioning
        from bookflow.company.sales import _history_snapshot
        kind='deposit_draft' if 'edit_type' in header else 'deposit_selection'
        def history(version):
            result=versioning.history_from_entries(s.company,kind,header['id'],version,_history_snapshot)
            for entry in result:
                if entry.changed_columns is not None:entry.changed_columns=['composition']
            return result
        versioning.check_update(current_version=header['version'],current_updated_at=header['updated_at'],
            current_writer=versioning.current_writer(s.company,kind,header['id'],header),changes={'composition'},
            expected_version=expected,history_since=history,actor_id=s.actor.id)
    if header['state']!='open':raise BookflowError('E_DEPOSIT_DRAFT_STATE')


def _custom(s,previous,patch,expectations,creating=False):
    from bookflow.company.journal_custom_fields import validate_kinds
    validate_kinds(s.company,patch,expectations,record_type='deposit')
    result=dict(previous)
    definitions={r['id']:r for r in cf._applicable_definitions(s.company.conn,'deposit')}
    if creating:
        keys=[key for key,r in definitions.items() if r['active']]
    else:keys=list(patch.root)
    for key in set(keys)|set(patch.root):
        if key not in definitions:raise BookflowError('E_RECORD_NOT_FOUND')
        d=cf.read_definition(s.company,key);old=result.get(key)
        provided=key in patch.root;value=patch.root[key] if provided else d['default']
        if old and not provided:continue
        canonical=None;choice=None
        if value is not None:
            if old and old.kind==d['kind'] and old.canonical_text is not None:
                prior_value=cf.typed_value_from_canonical(old.kind,old.canonical_text)
                if type(prior_value) is type(value) and prior_value==value:
                    result[key]=old.model_copy(update={'expected_kind':expectations.root[key]}) if key in expectations.root else old
                    continue
            canonical,_=cf.parse_typed_value(d['kind'],value,choices=cf._active_choice_map(s.company.conn,key) if d['kind']=='choice' else None)
            if d['kind']=='choice':choice=next(x for x in d['choices'] if x['value']==canonical)
        if not d['active'] and (old is None or old.kind!=d['kind'] or old.canonical_text!=canonical):raise BookflowError('E_VALIDATION',details={'field':'custom_fields','reason':'inactive'})
        result[key]=m.CustomCapture(definition_id=key,definition_version=d['version'],name=d['name'],kind=d['kind'],required=bool(d['required']),
            print_visible=None,position=d['position'],original_value_id=old.original_value_id if old else None,canonical_text=canonical,choice_id=choice['id'] if choice else None,
            choice_label=choice['value'] if choice else None,expected_kind=expectations.root.get(key),origin='entered' if provided else 'default' if value is not None else 'unresolved')
    return result


def patch_header(s,header,patch,*,creating=False):
    h=header.model_copy(deep=True)
    for name in ('date','number','memo','label'):
        if name in patch.model_fields_set:
            setattr(h,name,getattr(patch,name));h.origins[name]='entered' if name in ('memo','label') or getattr(patch,name) is not None else 'unresolved'
    if 'deposit_to' in patch.model_fields_set:
        h.bank=deposit_resolution.resolve_account(s,patch.deposit_to) if patch.deposit_to else None
        h.origins['deposit_to']='entered' if h.bank else 'unresolved'
    if 'cash_back' in patch.model_fields_set:
        if patch.cash_back is None:h.cash_back=None
        else:
            cash=h.cash_back.model_copy(deep=True) if h.cash_back else m.CashBack()
            for name in patch.cash_back.model_fields_set:
                value=getattr(patch.cash_back,name)
                if name=='account':cash.account=deposit_resolution.resolve_account(s,value) if value else None
                elif name=='amount':cash.units=amount(value,s.company_info_row['home_currency']) if value is not None else None
                else:cash.memo=value
                cash.origins[name]='entered' if name=='memo' or value is not None else 'unresolved'
            h.cash_back=cash
        h.origins['cash_back']='entered'
    h.custom_fields=_custom(s,h.custom_fields,patch.custom_fields,patch.expected_custom_field_kinds,creating)
    return h


def source_patch(s,entry,prior,ordinal,*,edit=None):
    source=deposit_sources.load(s,entry.source)
    if source.source_type!=entry.source_type:raise BookflowError('E_DEPOSIT_SOURCE_INELIGIBLE')
    if source.expected_header_version!=entry.expected_version:raise BookflowError('E_PREVIEW_STALE',details={'reason':'deposit_source'})
    claim=deposit_dependencies.active_claim(s,entry.source)
    if claim and claim['transaction_id']!=edit:raise BookflowError('E_DEPOSIT_SOURCE_CLAIMED')
    memo=prior.memo if prior else source.source_memo;origin=prior.memo_origin if prior else 'source'
    if 'memo_override' in entry.model_fields_set:memo=entry.memo_override;origin='entered'
    elif entry.memo_action=='restore_source':memo=source.source_memo;origin='source'
    elif origin=='source':memo=source.source_memo
    return m.Source(row_id=prior.row_id if prior else new_id(),ordinal=prior.ordinal if prior else ordinal,source=source,memo=memo,memo_origin=origin,
        occurrences=deposits.occurrences(source,prior.occurrences if prior else ()))


def additional_patch(s,entry,prior,ordinal):
    row=prior.model_copy(deep=True) if prior else m.Additional(row_id=new_id(),ordinal=ordinal)
    for name in entry.model_fields_set-{'line_id'}:
        value=getattr(entry,name)
        if name=='received_from':
            if value is None:row.received_from=None;row.party_name=None
            else:
                table={'customer':c.customers,'vendor':c.vendors,'employee':c.employees,'other_name':c.other_names}[value.kind]
                actual=_row(s,table,value.id)
                if prior is None or prior.received_from!=value:journals.active(actual,value.kind)
                row.received_from=value;row.party_name=prior.party_name if prior and prior.received_from==value else actual.get('full_name') or actual['name']
        elif name=='from_account':row.account=deposit_resolution.resolve_account(s,value,prior.account if prior else None) if value else None
        elif name=='amount':row.units=amount(value,s.company_info_row['home_currency']) if value is not None else None
        elif name in ('class_id','payment_method'):
            ref=defaults._row(s.company,name,value,active=False) if value else None
            old=getattr(prior,'class_ref' if name=='class_id' else name) if prior else None
            if ref and (not old or ref['id']!=old.id):journals.active(ref,'class' if name=='class_id' else name)
            setattr(row,'class_ref' if name=='class_id' else name,old if ref and old and ref['id']==old.id else defaults._ref(ref) if ref else None)
        else:setattr(row,name,value)
        row.origins[name]='entered' if name not in ('received_from','from_account','amount') or value is not None else 'unresolved'
    return row


def manifest(currency,header,sources=(),additional=(),high_water=0):
    sources=tuple(sorted(sources,key=lambda r:r.ordinal));additional=tuple(sorted(additional,key=lambda r:r.ordinal))
    return v.validate_manifest(m.Manifest(currency=currency,header=header,sources=sources,additional=additional,high_water=high_water,
        summary=v.summary(currency,header,sources,additional)))


def changes(s,previous,inp,*,edit=None):
    sources={r.source.transaction_id:r for r in previous.sources};additional={r.row_id:r for r in previous.additional};maximum=previous.high_water
    for identity in inp.remove_sources:
        if identity not in sources:raise BookflowError('E_VALIDATION',details={'field':'remove_sources'})
        del sources[identity]
    for entry in inp.set_sources:
        prior=sources.get(entry.source)
        if prior is None:maximum+=1
        sources[entry.source]=source_patch(s,entry,prior,maximum,edit=edit)
    for identity in getattr(inp,'remove_lines',()):
        if identity not in additional:raise BookflowError('E_VALIDATION',details={'field':'remove_lines'})
        del additional[identity]
    for entry in getattr(inp,'set_additional',()):
        prior=additional.get(entry.line_id) if entry.line_id else None
        if entry.line_id and prior is None:raise BookflowError('E_VALIDATION',details={'field':'line_id'})
        if prior is None:maximum+=1
        row=additional_patch(s,entry,prior,maximum);additional[row.row_id]=row
    header=patch_header(s,previous.header,inp.header) if hasattr(inp,'header') else previous.header
    return manifest(previous.currency,header,sources.values(),additional.values(),maximum)


def from_deposit(s,identity,version,*,copy_voided=False):
    h=_row(s,c.transactions,identity)
    if h['type']!='deposit':raise BookflowError('E_RECORD_NOT_FOUND')
    if h['status']!=('voided' if copy_voided else 'posted'):raise BookflowError('E_DEPOSIT_DRAFT_STATE')
    if h['version']!=version:raise BookflowError('E_VERSION_CONFLICT')
    rev=_row(s,c.transaction_revisions,h['current_revision_id'])
    profile=s.company.conn.execute(sa.select(c.deposit_profiles).where(c.deposit_profiles.c.revision_id==rev['id'])).mappings().one()
    try:effect=Effect.model_validate_json(profile['facts_snapshot'])
    except ValidationError:raise BookflowError('E_VALIDATION',details={'reason':'invalid_deposit_capture'}) from None
    intent=effect.intent
    header=m.Header(bank=intent.bank,date=intent.date,number=None if copy_voided else rev['number'],memo=rev['memo'],origins={k:'source' for k in ('deposit_to','date','number','memo')})
    if intent.cash_back:header.cash_back=m.CashBack(account=intent.cash_back.account,units=intent.cash_back.units,memo=intent.cash_back.memo,origins={k:'source' for k in ('account','amount','memo')})
    for key,raw in json.loads(rev['custom_fields_snapshot']).items():
        definition=cf.read_definition(s.company,key)
        header.custom_fields[key]=m.CustomCapture(definition_id=key,definition_version=raw['definition_version'],name=raw['name'],kind=raw['kind'],required=bool(definition['required']),
            print_visible=None,position=raw['position'],original_value_id=raw['value_id'],canonical_text=raw['canonical_text'],choice_id=raw.get('choice_id'),choice_label=raw.get('choice_label'),origin='source')
    sources=[];extras=[];originals={}
    for row in intent.sources:
        new=m.Source.model_validate(row.model_dump());oldkey=new.row_id;new.row_id=new_id();originals[new.row_id]=oldkey
        # Membership bumps the header without rewriting the saved cash revision.
        if copy_voided:
            current=_row(s,c.transactions,new.source.transaction_id)
            new.captured_header_version=new.source.expected_header_version
            new.source=new.source.model_copy(update={'expected_header_version':current['version']})
        else:
            actual=deposit_sources.load(s,new.source.transaction_id)
            if actual.revision_id!=new.source.revision_id:raise BookflowError('E_PREVIEW_STALE')
            new.source=actual
        sources.append(new)
    for row in intent.additional:
        new=m.Additional(row_id=new_id(),ordinal=row.ordinal,received_from=m.Party(kind=row.dimensions.party_kind,id=row.dimensions.party_id),party_name=row.dimensions.party_name,
            account=row.account,units=row.units,memo=row.memo,check_number=row.check_number,payment_method=row.payment_method,
            class_ref=defaults._ref(_row(s,c.classes,row.dimensions.class_id)) if row.dimensions.class_id else None,
            origins={k:'source' for k in ('received_from','from_account','amount','memo','check_number','payment_method','class_id')})
        originals[new.row_id]=row.row_id;extras.append(new)
    maximum=s.company.conn.execute(sa.select(sa.func.max(c.deposit_row_keys.c.ordinal)).where(c.deposit_row_keys.c.transaction_id==identity)).scalar_one() or 0
    return h,manifest(intent.currency,header,sources,extras,maximum),originals


def _new_header(s,ctx,kind,*,target=None,edit=None,copied=None):
    now=clock.now_iso()
    header=dict(id=new_id(),version=1,created_at=now,created_by=s.actor.id,created_via=ctx.interface.value,updated_at=now,updated_by=s.actor.id,updated_via=ctx.interface.value,
        state='open',current_revision_id=new_id(),audit_event_id=None)
    if kind=='draft':header.update(edit_transaction_id=edit['id'] if edit else None,edit_type='deposit' if edit else None,baseline_version=edit['version'] if edit else None,
        baseline_revision_id=edit['current_revision_id'] if edit else None,
        copy_transaction_id=copied['id'] if copied else None,copy_type='deposit' if copied else None,copy_version=copied['version'] if copied else None,copy_revision_id=copied['current_revision_id'] if copied else None,consumed_operation_id=None,consumed_revision_id=None)
    else:header.update(target_draft_id=target['id'],target_revision_id=target['current_revision_id'],accepted_revision_id=None)
    return header


def _next_header(s,ctx,old,state='open'):
    return dict(old,version=old['version']+1,state=state,current_revision_id=new_id(),updated_at=clock.now_iso(),updated_by=s.actor.id,updated_via=ctx.interface.value)


def bundle(s,ctx,kind,header,previous,result,event,*,originals=None):
    """Closed typed table set; all head/link writes are checked before return."""
    header=dict(header,audit_event_id=event);at=header['updated_at']
    created=dict(created_at=at,created_by=s.actor.id,created_via=ctx.interface.value,audit_event_id=event)
    revision=dict(id=header['current_revision_id'],**{kind+'_id':header['id']},version=header['version'],previous_revision_id=previous['current_revision_id'] if previous else None,
        snapshot=result.model_dump_json(),manifest_hash=q.digest(result.model_dump(mode='json')),high_water=result.high_water,**created)
    if kind=='draft':revision.update(bank_account_id=result.header.bank.id if result.header.bank else None,cashback_account_id=result.header.cash_back.account.id if result.header.cash_back and result.header.cash_back.account else None)
    else:revision.update(target_draft_id=header['target_draft_id'],target_revision_id=header['target_revision_id'])
    inserts=[(getattr(c,'deposit_'+kind+'_revisions'),revision)]
    if kind=='draft':
        existing=set(s.company.conn.execute(sa.select(c.deposit_draft_row_keys.c.id).where(c.deposit_draft_row_keys.c.draft_id==header['id'])).scalars())
        for values,k in ((result.sources,'source'),(result.additional,'additional')):
            for row in values:
                if row.row_id not in existing:
                    original=(originals or {}).get(row.row_id)
                    inserts.append((c.deposit_draft_row_keys,dict(id=row.row_id,draft_id=header['id'],kind=k,ordinal=row.ordinal,edit_transaction_id=(header['edit_transaction_id'] or header['copy_transaction_id']) if original else None,original_row_id=original,**created)))
    for row in result.sources:
        source=row.source
        values=dict(revision_id=revision['id'],**{kind+'_id':header['id']},row_id=row.row_id,ordinal=row.ordinal,source_transaction_id=source.transaction_id,
            source_type=source.source_type,expected_header_version=source.expected_header_version,source_revision_id=source.revision_id,snapshot=row.model_dump_json(),memo=row.memo,memo_origin=row.memo_origin,**created)
        if kind=='draft':values['kind']='source'
        inserts.append((getattr(c,'deposit_'+kind+'_sources'),values))
    if kind=='draft':
        for row in result.additional:
            party=row.received_from
            inserts.append((c.deposit_draft_additional,dict(revision_id=revision['id'],draft_id=header['id'],row_id=row.row_id,ordinal=row.ordinal,kind='additional',
                party_kind=party.kind if party else None,**{k+'_id':party.id if party and party.kind==k else None for k in ('customer','vendor','employee','other_name')},
                account_id=row.account.id if row.account else None,class_id=row.class_ref.id if row.class_ref else None,payment_method_id=row.payment_method.id if row.payment_method else None,
                amount_minor_units=row.units,currency=result.currency,snapshot=row.model_dump_json(),**created)))
    return kind,header,previous,revision,result,inserts


def final_foreign_keys(s):
    if s.company.raw.execute('PRAGMA main.foreign_key_check').fetchone() is not None:
        raise BookflowError('E_VALIDATION',details={'reason':'deposit_draft_foreign_key'})


def persist(s,ctx,bundles,event,command):
    if not s.company.raw.in_transaction or s.dry_run:raise RuntimeError('Nonposting write requires owned company transaction')
    if s.company.raw.execute('PRAGMA foreign_keys').fetchone()[0]!=1:raise RuntimeError('Foreign keys required')
    touched=[]
    for kind,header,old,revision,result,inserts in bundles:
        touched.append(Touched('deposit_'+kind,header['id'],'update' if old else 'create',old['version'] if old else None,header['version'],header,old,db='company'))
        for table,row in inserts:touched.append(Touched(table.name[:-1] if table.name.endswith('s') else table.name,row.get('id',row.get('row_id')),'create',None,1,row,db='company'))
    s.company.raw.execute('SAVEPOINT bookflow_deposit_draft')
    try:
        audit.write_event_to(s.company,ctx,command,command,touched,actor_id=s.actor.id,actor_kind=s.actor.kind,event_id=event)
        for kind,header,old,revision,result,inserts in bundles:
            table=c.deposit_drafts if kind=='draft' else c.deposit_selections
            if old:
                count=s.company.conn.execute(table.update().where(table.c.id==old['id'],table.c.version==old['version'],table.c.state=='open').values(**header)).rowcount
                if count!=1:raise BookflowError('E_VERSION_CONFLICT')
            else:s.company.conn.execute(table.insert().values(**header))
            for table,row in inserts:s.company.conn.execute(table.insert().values(**row))
        # Deferred reciprocal heads are now all present, including both accept sides.
        final_foreign_keys(s)
        for kind,header,old,revision,result,inserts in bundles:v.decode_revision(s,header,revision,kind)
        s.company.raw.execute('RELEASE bookflow_deposit_draft')
    except BaseException:
        s.company.raw.execute('ROLLBACK TO bookflow_deposit_draft');s.company.raw.execute('RELEASE bookflow_deposit_draft');raise


INPUTS={'create':m.DraftCreate,'update':m.DraftUpdate,'clear':m.DraftRef,'abandon':m.DraftRef}
def run(s,ctx,inp,verb,*,binding=None):
    if type(inp) is not INPUTS.get(verb):raise BookflowError('E_VALIDATION')
    if not s.company.raw.in_transaction:raise RuntimeError('Draft preparation requires owned snapshot')
    old=None;originals={}
    if verb=='create':
        identity=inp.from_deposit or inp.copy_from_voided
        binding=v.admit(s,ctx,binding,sources=[identity] if identity else (),write=True)
        edit=None;copied=None
        if identity:
            source,result,originals=from_deposit(s,identity,inp.expected_version,copy_voided=inp.copy_from_voided is not None)
            if inp.copy_from_voided:copied=source
            else:edit=source
        else:result=manifest(s.company_info_row['home_currency'],m.Header())
        header=_new_header(s,ctx,'draft',edit=edit,copied=copied)
        result=manifest(result.currency,patch_header(s,result.header,inp.header,creating=edit is None and copied is None),result.sources,result.additional,result.high_water)
    else:
        old,revision,previous,binding=load(s,inp.draft,ctx=ctx,binding=binding,write=True);_version(s,old,inp.expected_version)
        if verb=='update':
            v.admit(s,ctx,binding,sources=[r.source for r in inp.set_sources],draft=inp.draft,write=True)
            result=changes(s,previous,inp,edit=old['edit_transaction_id'])
            if result==previous:return output(s,old,revision,previous)
        elif verb=='clear':result=manifest(previous.currency,m.Header(),high_water=previous.high_water)
        else:result=previous
        header=_next_header(s,ctx,old,'abandoned' if verb=='abandon' else 'open')
    event=new_id();packed=bundle(s,ctx,'draft',header,old,result,event,originals=originals)
    if not s.dry_run:persist(s,ctx,[packed],event,'deposit draft '+verb)
    return output(s,packed[1],packed[3],result)


def items(s,inp,*,ctx=None,binding=None):
    from bookflow.company.deposit_source_queries import page
    h,r,value,binding=load(s,inp.draft,inp.revision_number,ctx=ctx,binding=binding)
    values=[dict(kind='source',**x.model_dump(mode='json')) for x in value.sources] if inp.kind!='additional' else []
    if inp.kind!='source':values += [dict(kind='additional',**x.model_dump(mode='json')) for x in value.additional]
    values.sort(key=lambda r:r['ordinal'])
    selected,cursor,fp=page(s,binding,'draft-items',dict(draft=inp.draft,revision=r['id'],kind=inp.kind),values,inp.limit,inp.cursor,facts=[r['manifest_hash'],h['state'],v.stale_sources(s,value,h['edit_transaction_id'])])
    return m.DraftItemsOutput.model_validate_json(q.canonical(dict(items=selected,total_count=len(values),next_cursor=cursor,facts_fingerprint=fp))).model_dump(mode='json')


def query(s,inp,*,ctx=None,binding=None,kind='draft'):
    from bookflow.company.deposit_source_queries import page
    binding=v.admit(s,ctx,binding);values=[]
    table=c.deposit_drafts if kind=='draft' else c.deposit_selections
    for row in s.company.conn.execute(sa.select(table).order_by(table.c.id)).mappings():
        try:h,r,value,_=load(s,row['id'],ctx=ctx,binding=binding,kind=kind)
        except BookflowError as error:
            if error.code=='E_PERMISSION':continue
            raise
        if inp.state is not None and row['state']!=inp.state:continue
        if kind=='selection' and inp.draft is not None and row['target_draft_id']!=inp.draft:continue
        values.append(output(s,h,r,value,kind).model_dump(mode='json'))
    contract=inp.model_dump(mode='json',exclude={'cursor','limit'})
    selected,cursor,fp=page(s,binding,kind+'-query',contract,values,inp.limit,inp.cursor)
    model=m.DraftQueryOutput if kind=='draft' else m.SelectionQueryOutput
    return model.model_validate_json(q.canonical(dict(items=selected,total_count=len(values),next_cursor=cursor,facts_fingerprint=fp))).model_dump(mode='json')
