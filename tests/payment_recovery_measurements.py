"""Additional recovery measurements on the untouched 34-case fixture AFTER that gate.

This script does not replace or modify the calibrated fixture/budget programs.
It appends one explicit nonfinancial selection/attempt on an owned measured copy.
"""
import json
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
from uuid import uuid4
import bookflow
import sqlalchemy as sa
from fastapi.testclient import TestClient
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version
from bookflow.company.payment_queries import digest
from payment_raw_evidence import table as raw_table


def measure(manifest_path,output_path,changed_count=201,unresolved=False):
    assert changed_count in (201,257,403)
    manifest=json.loads(Path(manifest_path).read_text());root=Path(manifest['root']).resolve()
    assert str(root).startswith('/tmp/bookflow-payment-budget-recovery-')
    assert manifest['target']==len(manifest['records'])==10000 and manifest['commands']==33761
    client=bookflow.connect(data_root=str(root));company=manifest['company']
    report=dict(source=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),loaded_package=bookflow.__file__,root=str(root),fixture_commands=33761,changed_count=changed_count,unresolved=unresolved,measurements=[],complete=False)
    def save():Path(output_path).write_text(json.dumps(report,indent=2))
    secret=client.token.issue(label='Owned additional recovery measurements')['secret']
    handle=start_serving(root,client_version(),bind='127.0.0.1:8765',secure_cookies=False);api=TestClient(handle.app)
    def run(command,inp):
        response=api.post('/companies/'+company+'/commands/'+command.replace(' ','.'),json=inp,headers={'Authorization':'Bearer '+secret})
        assert response.status_code==200,(command,response.status_code,response.text[:1500])
        return response.json()
    def write(command,inp):
        started=time.perf_counter();result=run(command,inp)
        report['measurements'].append(dict(command=command,input=inp,write_ms=(time.perf_counter()-started)*1000,result=result));save()
        return result
    def read(label,command,inp):
        result=run(command,inp);elapsed=[]
        for _ in range(3):
            started=time.perf_counter();result=run(command,inp);elapsed.append((time.perf_counter()-started)*1000)
        statements=[];connections=[]
        def attach(connection,record,proxy):connection.set_trace_callback(statements.append);connections.append(connection)
        sa.event.listen(sa.engine.Engine,'checkout',attach)
        try:run(command,inp)
        finally:
            sa.event.remove(sa.engine.Engine,'checkout',attach)
            for connection in connections:
                try:connection.set_trace_callback(None)
                except sqlite3.ProgrammingError:pass
        report['measurements'].append(dict(label=label,command=command,input=inp,samples_ms=elapsed,median_ms=statistics.median(elapsed),within_100ms=statistics.median(elapsed)<100,sql_statements=len(statements),sql_selects=sum(s.lstrip().upper().startswith(('SELECT','WITH')) for s in statements),result=result));save()
        return result
    def finance():
        with sqlite3.connect(Path(manifest['database']).as_uri()+'?mode=ro',uri=True) as db:
            return {table:raw_table(db,table) for table in ('transactions','transaction_revisions','posting_batches','posting_lines','posting_line_sources','applications','application_allocations','payment_operations','payment_operation_items')}
    before=finance();report['financial_before']=before
    try:
        draft=write('payment selection create',dict(mode='new_receipt',customer=manifest['parent'],date='2026-06-02',amount='10.00',label='Additional recovery measurements'))
        with sqlite3.connect(Path(manifest['database']).as_uri()+'?mode=ro',uri=True) as db:
            eligible={row[0] for row in db.execute("SELECT t.id FROM transactions t JOIN transaction_revisions r ON r.id=t.current_revision_id WHERE t.type='invoice' AND t.status='posted' AND r.date<='2026-06-02'")}
        records=[row for row in manifest['records'] if row['invoice'] in eligible][:403]
        assert len(records)==403
        report['selected_fixture_records']=records;save()
        for offset in range(0,403,200):
            draft=write('payment selection update',dict(selection=draft['id'],expected_version=draft['version'],set_items=[dict(invoice=r['invoice'],expected_version=r['invoice_version'],amount='0.01',amount_origin='entered') for r in records[offset:offset+200]]))
        # Observed1 claims resolve through actual existing application/unapply
        # history. The fingerprint still includes every current invoice fact.
        entries=sorted([dict(invoice_id=r['invoice'],observed_invoice_version=1,action='set',amount_minor_units=None if unresolved else 2,currency='USD',amount_origin='unresolved' if unresolved else 'entered') for r in records[:changed_count]],key=lambda r:r['invoice_id'])
        generation=str(uuid4());header=dict(action='keep')
        intent=dict(domain='bookflow.payment.recovery.intent',format=1,selection=draft['id'],local_baseline_revision=draft['revision_id'],anchor_revision=draft['revision_id'],attempt_generation=generation,header_intent=header,entries=entries)
        begin=dict(recovery_key='measurement-'+generation,selection=draft['id'],expected_version=draft['version'],local_baseline_revision=draft['revision_id'],attempt_generation=generation,declared_entry_count=changed_count,intent_hash=digest(intent),header_intent=header)
        identifier=write('payment recovery begin',begin)['original_receipt']['recovery_id']
        write('payment recovery upload',dict(recovery_id=identifier,chunk_index=0,entries=entries[:200]))
        read('active.header','payment recovery show',dict(recovery_id=identifier))
        read('active.missing','payment recovery items',dict(recovery_id=identifier,kind='missing_ranges',limit=200))
        read('active.entries','payment recovery items',dict(recovery_id=identifier,limit=200))
        read('active.query','payment recovery query',dict(state='uploading',limit=50))
        for offset in range(200,changed_count,200):
            write('payment recovery upload',dict(recovery_id=identifier,chunk_index=offset//200,entries=entries[offset:offset+200]))
        staged_version=1+(changed_count+199)//200
        write('payment recovery seal',dict(recovery_id=identifier,expected_recovery_version=staged_version))
        request=dict(recovery_id=identifier,attempt_generation=generation,intent_hash=begin['intent_hash'])
        comparison=read('sealed.history-rich.compare','payment recovery compare',request)
        if unresolved:
            assert comparison['selected_minor_units'] is None and comparison['problem_count']>=changed_count
        else:
            assert comparison['selected_minor_units']==403+changed_count and comparison['unapplied_minor_units']==597-changed_count
        for kind in ('changes','problems'):
            cursor=None;seen=0;page_index=0
            while True:
                page=read('sealed.'+kind+'.'+str(page_index),'payment recovery compare-items',dict(**request,facts_fingerprint=comparison['facts_fingerprint'],kind=kind,limit=200,**({'cursor':cursor} if cursor else {})))
                seen+=len(page['items']);cursor=page['next_cursor'];page_index+=1
                if not cursor:break
            assert seen==comparison['change_count' if kind=='changes' else 'problem_count']
        published=write('payment recovery apply',dict(**request,expected_recovery_version=staged_version+1,expected_selection_version=draft['version'],expected_facts_fingerprint=comparison['facts_fingerprint']))
        assert published['current']['selection_id']==draft['id'] and published['current']['selection_version']==draft['version']+1
        read('published.original-receipt','payment recovery show',dict(recovery_id=identifier))
        report['financial_after']=finance();assert report['financial_after']==before
        report['complete']=True;save()
    finally:handle.stop()


if __name__=='__main__':measure(sys.argv[1],sys.argv[2],int(sys.argv[3]) if len(sys.argv)>3 else 201,len(sys.argv)>4 and sys.argv[4]=='unresolved')
