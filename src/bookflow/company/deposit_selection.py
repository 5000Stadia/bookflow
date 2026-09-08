"""Private independent Payments dialog drafts and atomic parent source replacement."""
import sqlalchemy as sa
from bookflow.company import schema as c, deposit_drafts as drafts, deposits
from bookflow.company import deposit_draft_models as m, deposit_draft_validation as v, deposit_source_queries as sources
from bookflow.core.ids import new_id
from bookflow.core.errors import BookflowError

INPUTS={'create':m.SelectionCreate,'update':m.SelectionUpdate,'clear':m.SelectionRef,
        'abandon':m.SelectionRef,'accept':m.SelectionAccept,'select-matching':m.SelectMatching}


def run(s,ctx,inp,verb,*,binding=None):
    if type(inp) is not INPUTS.get(verb):raise BookflowError('E_VALIDATION')
    if not s.company.raw.in_transaction:raise RuntimeError('Selection requires owned snapshot')
    old=None;parent_bundle=None
    if verb=='create':
        parent,_,value,binding=drafts.load(s,inp.draft,ctx=ctx,binding=binding,write=True)
        drafts._version(s,parent,inp.expected_version)
        header=drafts._new_header(s,ctx,'selection',target=parent)
        rows=[r.model_copy(update={'row_id':new_id()}) for r in value.sources]
        result=drafts.manifest(value.currency,m.Header(date=value.header.date),rows,high_water=value.high_water)
    else:
        old,revision,previous,binding=drafts.load(s,inp.selection,ctx=ctx,binding=binding,kind='selection',write=True)
        drafts._version(s,old,inp.expected_version)
        parent,pr,parent_value,_=drafts.load(s,old['target_draft_id'],ctx=ctx,binding=binding,write=True)
        header=drafts._next_header(s,ctx,old,'abandoned' if verb=='abandon' else 'accepted' if verb=='accept' else 'open')
        result=previous
        if verb=='update':
            v.admit(s,ctx,binding,selection=inp.selection,sources=[r.source for r in inp.set_sources],write=True)
            result=drafts.changes(s,previous,inp,edit=parent['edit_transaction_id'])
            if result==previous:return drafts.output(s,old,revision,previous,'selection')
        elif verb=='clear':result=drafts.manifest(previous.currency,previous.header,high_water=previous.high_water)
        elif verb=='select-matching':
            expected_date=parent_value.header.date
            if expected_date is None:
                from bookflow.core import clock
                expected_date=clock.now_iso()[:10]
            if inp.filter.date!=expected_date or inp.filter.for_deposit!=parent['edit_transaction_id'] or inp.filter.include_ineligible:
                raise BookflowError('E_VALIDATION',details={'field':'filter'})
            binding,candidates,facts=sources.candidates(s,inp.filter,binding=binding,ctx=ctx)
            _,_,fingerprint=sources.page(s,binding,'sources',inp.filter.model_dump(mode='json'),candidates,1,None,facts=facts)
            if fingerprint!=inp.facts_fingerprint:raise BookflowError('E_QUERY_STALE')
            v.admit(s,ctx,binding,selection=inp.selection,sources=[r.source.transaction_id for r in candidates],write=True)
            # Complete atomic set; the bounded public patch model is never involved.
            by_id={r.source.transaction_id:r for r in previous.sources};selected={} if inp.mode=='replace' else dict(by_id);maximum=previous.high_water
            for candidate in candidates:
                prior=by_id.get(candidate.source.transaction_id)
                if prior is None:maximum+=1
                entry=m.SourcePatch(source=candidate.source.transaction_id,source_type=candidate.source.source_type,expected_version=candidate.source.expected_header_version)
                selected[entry.source]=drafts.source_patch(s,entry,prior,maximum,edit=parent['edit_transaction_id'])
            result=drafts.manifest(previous.currency,previous.header,selected.values(),high_water=maximum)
            v.require_occurrence_retention(previous,result,strict_ordinals=True)
        elif verb=='accept':
            if inp.draft!=parent['id']:raise BookflowError('E_VALIDATION',details={'field':'draft'})
            drafts._version(s,parent,inp.expected_draft_version)
            if parent['current_revision_id']!=old['target_revision_id']:raise BookflowError('E_VERSION_CONFLICT',details={'reason':'selection_parent_changed'})
            if v.stale_sources(s,previous,parent['edit_transaction_id']):raise BookflowError('E_PREVIEW_STALE',details={'reason':'selection_source'})
            retained={r.source.transaction_id:r for r in parent_value.sources};rows=[];maximum=parent_value.high_water
            for row in previous.sources:
                prior=retained.get(row.source.transaction_id)
                if prior is None:maximum+=1
                # Selection remove/re-add does not remove the parent row. Reconcile
                # retained parent ordinals; truly new parent rows keep selection's.
                occurrences=deposits.occurrences(row.source,prior.occurrences) if prior else row.occurrences
                rows.append(row.model_copy(update={'row_id':prior.row_id if prior else new_id(),
                    'ordinal':prior.ordinal if prior else maximum,'occurrences':occurrences}))
            parent_result=drafts.manifest(parent_value.currency,parent_value.header,rows,parent_value.additional,maximum)
            v.require_occurrence_retention(parent_value,parent_result,strict_ordinals=True)
            # Parent date is checked, not only the selection's captured date.
            if v.stale_sources(s,parent_result,parent['edit_transaction_id']):raise BookflowError('E_PREVIEW_STALE')
            next_parent=drafts._next_header(s,ctx,parent)
            header['accepted_revision_id']=next_parent['current_revision_id']
            parent_bundle=(next_parent,parent,parent_result)
    event=new_id();packed=drafts.bundle(s,ctx,'selection',header,old,result,event)
    bundles=[]
    if parent_bundle:bundles.append(drafts.bundle(s,ctx,'draft',*parent_bundle,event))
    bundles.append(packed)
    if not s.dry_run:drafts.persist(s,ctx,bundles,event,'deposit selection '+verb)
    result_output=drafts.output(s,packed[1],packed[3],result,'selection')
    if parent_bundle:
        pb=bundles[0]
        return m.AcceptOutput(draft=drafts.output(s,pb[1],pb[3],pb[4]),selection=result_output)
    return result_output


def show(s,inp,*,ctx=None,binding=None):
    h,r,value,_=drafts.load(s,inp.selection,inp.revision_number,kind='selection',ctx=ctx,binding=binding)
    return drafts.output(s,h,r,value,'selection')


def items(s,inp,*,ctx=None,binding=None):
    h,r,value,binding=drafts.load(s,inp.selection,inp.revision_number,kind='selection',ctx=ctx,binding=binding)
    selected,cursor,fp=sources.page(s,binding,'selection-items',dict(selection=inp.selection,revision=r['id']),[row.model_dump(mode='json') for row in value.sources],inp.limit,inp.cursor,
        facts=[r['manifest_hash'],h['state'],v.stale_sources(s,value,drafts._row(s,c.deposit_drafts,h['target_draft_id'])['edit_transaction_id'])])
    from bookflow.company.payment_queries import canonical
    return m.SelectionItemsOutput.model_validate_json(canonical(dict(items=selected,total_count=len(value.sources),next_cursor=cursor,facts_fingerprint=fp))).model_dump(mode='json')


def query(s,inp,*,ctx=None,binding=None):return drafts.query(s,inp,ctx=ctx,binding=binding,kind='selection')
