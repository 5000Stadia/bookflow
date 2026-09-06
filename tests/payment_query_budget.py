"""Measure the unchanged 100ms warm interactive contract on the complete fixture.

Explicit script: payment_query_budget.py MANIFEST OUTPUT.json. All cases run;
failures are recorded rather than skipping remaining measurements. No tracing
is enabled for timed samples. SQL capture is a separate diagnostic pass.
"""
import json
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time

import bookflow
from fastapi.testclient import TestClient
import sqlalchemy as sa
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version


def measure(manifest_path, output_path):
    manifest = json.loads(Path(manifest_path).read_text())
    assert manifest['complete'] and manifest['target'] == len(manifest['records']) == 10000
    assert not os.environ.get('BOOKFLOW_TRACE_DIR'), 'budgets must be untraced'
    root = Path(manifest['root']).resolve()
    assert str(root).startswith('/tmp/bookflow-payment-budget-')
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    client = bookflow.connect(data_root=str(root))
    secret = client.token.issue(label='payment-query-budget')['secret']
    company = manifest['company']
    records = manifest['records']
    sample = records[-1]
    inverse = records[5]
    context = dict(mode='new_receipt', customer=manifest['parent'], date='2026-06-02')
    cases = [
        ('payment.query.default', 'payment query', {}, 10000),
        ('payment.query.method', 'payment query', {'payment_method': manifest['methods'][1]}, 5000),
        ('payment.query.available', 'payment query', {'has_available_credit': True}, sum(not r['voided'] and r['received']>r['applied'] for r in records)),
        ('payment.query.voided', 'payment query', {'status':'voided'}, sum(r['voided'] for r in records)),
        ('payment.query.broad', 'payment query', {'q':'STRASSE'}, 10000),
        ('payment.query.missing', 'payment query', {'q':'Absent search value'}, 0),
        ('payment.query.late', 'payment query', {'q':'BUDGET-PAY-09999'}, 1),
        ('payment.query.amount_sort', 'payment query', {'sort':'unapplied'}, 10000),
        ('payment.query.date', 'payment query', {'date_from':'2026-06-02','date_to':'2026-06-02'}, 10000),
        ('payment.query.family', 'payment query', {'customer':manifest['parent'],'include_descendants':True}, 10000),
        ('payment.query.component', 'payment query', {'component_customer':manifest['jobs'][-1]}, 100),
        ('invoice.query.default', 'invoice query', {}, 10000),
        ('invoice.query.posted', 'invoice query', {'status':'posted'}, sum(not r['voided'] for r in records)),
        ('invoice.query.job', 'invoice query', {'customer':manifest['jobs'][-1]}, 100),
        ('invoice.query.late', 'invoice query', {'number':'BUDGET-INV-09999'}, 1),
        ('payment.selection.query', 'payment selection query', {'state':'open'}, 10000),
        ('payment.show', 'payment show', {'payment':sample['payment']}, None),
        ('payment.history', 'payment history', {'payment':sample['payment']}, None),
        ('payment.settlement', 'payment settlement', {'payment':sample['payment']}, None),
        ('payment.settlement.dated', 'payment settlement', {'payment':sample['payment'],'as_of':'2026-06-02'}, None),
        ('invoice.show', 'invoice show', {'invoice':sample['invoice']}, None),
        ('invoice.settlement', 'invoice settlement', {'invoice':sample['invoice']}, None),
        ('invoice.settlement.dated', 'invoice settlement', {'invoice':sample['invoice'],'as_of':'2026-06-01'}, None),
        ('application.show', 'application show', {'application':sample['application']}, None),
        ('application.history', 'application history', {'application':inverse['application']}, None),
        ('payment.selection.show', 'payment selection show', {'selection':sample['selection']}, None),
        ('payment.selection.items', 'payment selection items', {'selection':sample['selection']}, 0),
        ('payment.operation.show', 'payment operation show', {'operation_key':'budget-receive-09999'}, None),
        ('payment.invoices.family', 'payment invoices', context, sum(not r['voided'] and r['applied']<100 for r in records)),
        ('payment.invoices.job', 'payment invoices', dict(context,customer=manifest['jobs'][-1]), sum(not r['voided'] and r['applied']<100 and r['party']==manifest['jobs'][-1] for r in records)),
        ('payment.invoices.late', 'payment invoices', dict(context,q='BUDGET-INV-09999'), 1),
        ('payment.invoices.missing', 'payment invoices', dict(context,q='Absent search value'), 0),
        ('payment.invoices.credit', 'payment invoices', dict(mode='existing_credit',payment=sample['payment'],date='2026-06-02'), None),
        ('payment.suggest', 'payment suggest', dict(context,amount='1',strategy='exact_then_oldest'), 1),
    ]
    def state():
        with sqlite3.connect(manifest['database']) as db:
            return {table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in manifest['counts']} | {
                'header_versions':db.execute('SELECT sum(version) FROM transactions').fetchone()[0],
                'debits':db.execute('SELECT sum(debit_minor_units) FROM posting_lines').fetchone()[0],
                'credits':db.execute('SELECT sum(credit_minor_units) FROM posting_lines').fetchone()[0],
                'audit_watermark':db.execute('SELECT max(seq) FROM audit_events').fetchone()[0]}
    before = state()
    assert before['transactions']==20000 and before['payment_selections']==10000
    receipt = dict(source=os.environ.get('BOOKFLOW_BUDGET_SOURCE_SHA') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        loaded_package=str(Path(bookflow.__file__).resolve()),
        root=str(root), dataset=before, fixture_seconds=manifest['elapsed_seconds'],
        fixture_commands=manifest['commands'], threshold_ms=100, samples=3,
        statistic='median; all individual samples and maximum retained',
        transport='warm in-process authenticated TestClient; includes dispatch/serialization; no network/startup',
        cases=[], complete=False)
    def save():
        Path(output_path).write_text(json.dumps(receipt,indent=2))
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False,publish_descriptor=False)
    api = TestClient(handle.app)
    def run(command, data):
        response=api.post(f'/companies/{company}/commands/'+command.replace(' ','.'),json=data,
            headers={'Authorization':f'Bearer {secret}'})
        assert response.status_code==200,(command,response.status_code,response.text)
        return response.json()
    try:
        for label, command, data, expected in cases:
            print('START '+label,flush=True)
            row=dict(name=label,command=command,input=data)
            try:
                out=run(command,data)
                if expected is not None:
                    if 'total_count' in out:
                        assert out['total_count']==expected,(label,out.get('total_count'),expected)
                    else:
                        assert out['count']==min(50,expected) and out['has_more']==(expected>50),(label,out,expected)
                elapsed=[]
                for _ in range(3):
                    start=time.perf_counter();out=run(command,data);elapsed.append((time.perf_counter()-start)*1000)
                row.update(samples_ms=elapsed,median_ms=statistics.median(elapsed),max_ms=max(elapsed),
                    within_budget=statistics.median(elapsed)<100,total_count=out.get('total_count'),expected_total=expected)
                if 'items' in out:
                    counts={}
                    for limit in (10,200):
                        statements=[];connections=[]
                        def attach(connection, record, proxy):
                            connection.set_trace_callback(statements.append);connections.append(connection)
                        sa.event.listen(sa.engine.Engine,'checkout',attach)
                        try:
                            sized=run(command,dict(data,limit=limit))
                            counts[limit]=dict(statements=len(statements),selects=sum(s.lstrip().upper().startswith(('SELECT','WITH')) for s in statements),rows=len(sized['items']))
                        finally:
                            sa.event.remove(sa.engine.Engine,'checkout',attach)
                            for connection in connections:
                                try:connection.set_trace_callback(None)
                                except sqlite3.ProgrammingError:pass
                    row['sql_counts']=counts
            except Exception as exc:
                row['error']=repr(exc)
            receipt['cases'].append(row);save();print(json.dumps(row),flush=True)
    finally:
        handle.stop()
    receipt['after']=state()
    assert before==receipt['after'], 'read calibration mutated recorded financial/audit state'
    receipt['complete']=True
    receipt['passed']=all(row.get('within_budget',False) and 'error' not in row for row in receipt['cases'])
    save()
    print(json.dumps(dict(complete=True,passed=receipt['passed'],cases=len(cases))),flush=True)
    return receipt['passed']


if __name__=='__main__':
    sys.exit(0 if measure(sys.argv[1],sys.argv[2]) else 1)
