"""Shared complete-intent preparation commands on the ordinary registry."""
import sqlalchemy as sa
from bookflow.core.registry import command, Plan
from bookflow.company import payment_recovery as recovery, payment_queries as q
from bookflow.company import payment_recovery_models as m, payment_recovery_outputs as out

ERRORS=['E_RECORD_NOT_FOUND','E_VERSION_CONFLICT','E_RECOVERY_PENDING','E_RECOVERY_INCOMPLETE',
        'E_RECOVERY_KEY_REUSED','E_RECOVERY_FINALIZED','E_PREVIEW_STALE','E_QUERY_STALE','E_SELECTION_CONSUMED']


def write(verb,model):
    def planner(inp,ctx,s):
        return recovery.prepare(s,ctx,inp,verb)
    cmd=command('payment recovery '+verb,scope='company',description={
        'begin':'Share a complete attempted payment edit declaration and block ordinary recording until recovery is resolved.',
        'upload':'Durably acknowledge one complete range of attempted edits. Exact retries preserve the original acknowledgement.',
        'seal':'Verify the complete declared attempt before review; missing edits cannot be silently omitted.',
        'apply':'Confirm the entire current comparison and publish one new revision of the original shared selection.',
        'abort':'Explicitly discard the entire attempt, retaining evidence and restoring the saved draft as a new revision.',
        'replace':'Replace the entire attempted edit declaration atomically while keeping recording blocked.',
    }[verb],input_model=model,output_model=out.RecoveryWriteOutput,writes={'company'},required_role='standard',
        capability='ledger.post',accepts_idempotency_key=True,error_codes=ERRORS)(planner)
    cmd.ledger=True
    cmd.authorize_input=recovery.authorize_input
    cmd.permanent_recovery=lambda inp,ctx,s: recovery.recover(inp,ctx,s,verb,cmd)
    cmd.replay=lambda inp,ctx,s,hit: recovery.replay(inp,ctx,s,hit,verb)
    cmd.applier(recovery.apply)
    return cmd

begin=write('begin',m.BeginInput)
upload=write('upload',m.UploadInput)
seal=write('seal',m.SealInput)
apply=write('apply',m.ApplyInput)
abort=write('abort',m.AbortInput)
replace=write('replace',m.ReplaceInput)

@command('payment recovery show',scope='company',description='Resolve an acknowledged or uncertain recovery by its identity or original key, with original receipts and current lifecycle.',
    input_model=m.ShowInput,output_model=out.RecoveryOutput,required_role='member',capability='ledger.read',error_codes=ERRORS)
def show(inp,ctx,s):
    return Plan(recovery.show_row(s,recovery.resolve(s,inp.recovery_id,inp.recovery_key)))

@command('payment recovery compare',scope='company',description='Review the complete sealed attempt against current authorized facts without changing any rows.',
    input_model=m.CompareInput,output_model=out.ComparisonOutput,required_role='member',capability='ledger.read',error_codes=ERRORS)
def compare(inp,ctx,s):
    preview,_=recovery.comparison(s,recovery.resolve(s,inp.recovery_id),inp)
    return Plan(preview)

@command('payment recovery compare-items',scope='company',description='Read every change or problem in the confirmed generation; relevant fact changes invalidate continuation.',
    input_model=m.CompareItemsInput,output_model=out.ItemsOutput,required_role='member',capability='ledger.read',error_codes=ERRORS)
def compare_items(inp,ctx,s):
    preview,data=recovery.comparison(s,recovery.resolve(s,inp.recovery_id),inp)
    if preview.facts_fingerprint!=inp.facts_fingerprint:
        recovery.fail('E_QUERY_STALE')
    return Plan(out.ItemsOutput(**q.page(s,'payment recovery compare-items',inp,data[inp.kind],facts=preview.facts_fingerprint)))

@command('payment recovery items',scope='company',description='Page acknowledged edits, original chunk receipts, or exact missing chunk ranges for this attempt.',
    input_model=m.ItemsInput,output_model=out.ItemsOutput,required_role='member',capability='ledger.read',error_codes=ERRORS)
def items(inp,ctx,s):
    row=recovery.resolve(s,inp.recovery_id)
    if inp.kind=='entries':
        statement=sa.select(recovery.I).where(recovery.I.c.recovery_id==row['id']).order_by(recovery.I.c.entry_index)
    elif inp.kind=='chunks':
        statement=sa.select(recovery.C).where(recovery.C.c.recovery_id==row['id']).order_by(recovery.C.c.chunk_index)
    else:
        # One row per gap between stored chunks, never one allocation per absent index.
        indices=s.company.conn.execute(sa.select(recovery.C.c.chunk_index).where(recovery.C.c.recovery_id==row['id']).order_by(recovery.C.c.chunk_index)).scalars()
        start=0;values=[]
        for index in indices:
            if index>start:
                values.append(dict(first_chunk_index=start,last_chunk_index=index-1,chunk_count=index-start))
            start=index+1
        end=(row['declared_entry_count']+199)//200
        if start<end:
            values.append(dict(first_chunk_index=start,last_chunk_index=end-1,chunk_count=end-start))
        return Plan(out.ItemsOutput(**q.page(s,'payment recovery items',inp,values,facts=[row['intent_hash'],row['version']])))
    result=q.sql_page(s,'payment recovery items',inp,statement,facts=[row['intent_hash'],row['version']])
    return Plan(out.ItemsOutput(**result))

@command('payment recovery query',scope='company',description='Discover complete and interrupted payment recovery attempts with authorized counts.',
    input_model=m.QueryInput,output_model=out.RecoveryPageOutput,required_role='member',capability='ledger.read',error_codes=ERRORS)
def query(inp,ctx,s):
    statement=sa.select(recovery.R)
    if inp.selection:
        statement=statement.where(recovery.R.c.selection_id==inp.selection)
    if inp.state:
        statement=statement.where(recovery.R.c.state==inp.state)
    statement=statement.where(recovery.readable_selection(s,recovery.R.c.selection_id)).order_by(recovery.R.c.created_at.desc(),recovery.R.c.id.desc())
    result=q.sql_page(s,'payment recovery query',inp,statement)
    result['items']=[recovery.show_row(s,row) for row in result['items']]
    return Plan(out.RecoveryPageOutput(**result))
