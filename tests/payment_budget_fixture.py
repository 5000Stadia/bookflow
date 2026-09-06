"""Explicit full-size payment budget fixture, created only by public commands.

Run as a script with a NEW disposable /tmp root. No reduced-size budget mode.
The progress manifest makes interrupted construction inspectable, not reusable
as a completed calibration fixture.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import atexit

import bookflow


def build(root):
    root = Path(root).resolve()
    if not str(root).startswith('/tmp/bookflow-payment-budget-') or root.exists():
        raise ValueError('requires a new /tmp/bookflow-payment-budget-* root')
    started = time.perf_counter()
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.run('organization new', {'name': 'Payment Budget Organization'})
    company = client.run('company new', dict(organization='Payment Budget Organization',
        legal_name='Payment Budget Company', home_currency='USD', timezone='UTC'))['company_id']
    commands = 3  # init, organization new, company new
    api = None
    def run(name, data, **context):
        nonlocal commands
        commands += 1
        if api is not None:
            headers = {'Authorization': f'Bearer {secret}'}
            if 'reason' in context:
                headers['X-Bookflow-Reason'] = context['reason']
            response = api.post(f'/companies/{company}/commands/'+name.replace(' ','.'),
                json=data, headers=headers)
            assert response.status_code == 200, (name,response.status_code,response.text)
            return response.json()
        return client.run(name, data, company=company, **context)
    parent = run('customer create', dict(name='Budget family Straße'))['id']
    jobs = [run('customer create', dict(name=f'Budget job {i:03}', parent_id=parent))['id'] for i in range(100)]
    bank = run('account create', dict(name='Budget bank', type='bank'))['id']
    income = run('account create', dict(name='Budget income', type='income'))['id']
    methods = [run('payment-method create', dict(name=f'Budget method {i}', kind='cash' if i == 0 else 'check'))['id'] for i in range(2)]
    code = next(row['id'] for row in run('sales-tax-code list', {})['items'] if not row['taxable'])
    item = run('item create', dict(name='Budget work', type='service', sales_enabled=True,
        description='Completed budget witness work', price='1', income_account_id=income, sales_tax_code_id=code))['id']
    info = run('company show', {})
    path = Path(info['path']) / 'company.db'
    # The public authenticated resident path avoids process-local connection
    # teardown per fixture command; it does not bypass dispatch or audit writes.
    from fastapi.testclient import TestClient
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    secret = client.token.issue(label='payment-budget-fixture')['secret']
    commands += 1
    handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False, publish_descriptor=False)
    atexit.register(handle.stop)
    api = TestClient(handle.app)
    manifest = dict(root=str(root), company=company, parent=parent, jobs=jobs, bank=bank,
        income=income, methods=methods, item=item, database=str(path), target=10000,
        complete=False, records=[], expected_bank=0, expected_income=0)
    for i in range(10000):
        party = jobs[i % len(jobs)]
        invoice = run('invoice post', dict(customer=party, date='2026-06-01', number=f'BUDGET-INV-{i:05}',
            lines=[dict(item=item, quantity='1')]))
        payment = run('payment receive', dict(customer=parent, date='2026-06-02', number=f'BUDGET-PAY-{i:05}',
            amount='1' if i % 10 == 0 else '0.60', deposit_to=bank, payment_method=methods[i % 2],
            operation_key=f'budget-receive-{i:05}', applications=dict(mode='inline', items=[
                dict(invoice=invoice['id'], expected_version=1, amount='1' if i % 10 == 0 else '0.40')])))
        application = payment['effect']['applications'][0]['application_id']
        amount = 100 if i % 10 == 0 else 60
        applied = 100 if i % 10 == 0 else 40
        payment_version, invoice_version = 1, 2
        voided = i % 17 == 0
        if i % 5 == 0 or voided:
            run('payment unapply', dict(payment=payment['id'], expected_version=payment_version,
                operation_key=f'budget-unapply-{i:05}', applications=[dict(application_id=application,
                    invoice_expected_version=invoice_version)]), reason='Explicit budget allocation correction')
            payment_version += 1
            invoice_version += 1
            applied = 0
        if voided:
            run('payment void', dict(payment=payment['id'], expected_version=payment_version,
                operation_key=f'budget-void-{i:05}'), reason='Cancel duplicate budget receipt')
            run('invoice void', dict(invoice=invoice['id'], expected_version=invoice_version),
                reason='Cancel duplicate budget invoice')
            payment_version += 1
            invoice_version += 1
        draft = run('payment selection create', dict(mode='new_receipt', customer=parent,
            date='2026-06-02', amount='1'))
        manifest['commands'] = commands
        manifest['expected_bank'] += 0 if voided else amount
        manifest['expected_income'] += 0 if voided else 100
        manifest['records'].append(dict(invoice=invoice['id'], payment=payment['id'],
            application=application, selection=draft['id'], party=party, received=amount,
            applied=applied, voided=voided, payment_version=payment_version, invoice_version=invoice_version))
        if (i + 1) % 100 == 0:
            manifest['elapsed_seconds'] = round(time.perf_counter() - started, 3)
            (root / 'payment-budget-manifest.json').write_text(json.dumps(manifest))
            print(json.dumps(dict(completed=i + 1, seconds=manifest['elapsed_seconds'])), flush=True)
    handle.stop()
    atexit.unregister(handle.stop)
    with sqlite3.connect(path) as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        counts = dict(db.execute('SELECT type,count(*) FROM transactions GROUP BY type'))
        assert counts == {'invoice': 10000, 'payment': 10000}, counts
        assert db.execute('SELECT count(*) FROM payment_selections').fetchone()[0] == 10000
        assert db.execute("SELECT count(*) FROM applications WHERE kind='apply'").fetchone()[0] == 10000
        balances = dict(db.execute('SELECT account_id,sum(debit_minor_units-credit_minor_units) FROM posting_lines GROUP BY account_id'))
        assert balances[bank] == manifest['expected_bank']
        assert balances[income] == -manifest['expected_income']
        manifest['counts'] = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in (
            'transactions','transaction_revisions','posting_batches','posting_lines','applications',
            'application_allocations','payment_operations','payment_selections','audit_events')}
    manifest['complete'] = True
    manifest['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    (root / 'payment-budget-manifest.json').write_text(json.dumps(manifest))
    print(json.dumps({k:v for k,v in manifest.items() if k not in ('records','jobs')}), flush=True)


if __name__ == '__main__':
    build(sys.argv[1])
