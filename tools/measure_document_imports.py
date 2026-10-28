"""Timings of invoice imports (R107 increment 4, design/bulk-import.md §5, §11): one size per process.

    python tools/measure_document_imports.py INVOICES [WORKDIR]

A fresh data root under WORKDIR (default: a temporary folder; use the root NVMe), a company on the general chart with
three customers and three service items, and a file of INVOICES invoices of 3 lines each, keyed by number. Times, in
seconds: `import check`; `import preview` (each invoice dry-run through `invoice post`); `import run` called until
done; `import reverse --dry-run` then `import reverse` with its fingerprint, called until done (each invoice deleted
with its history, under the caller's explicit delete grant); and a loop calling `invoice post` once per invoice in a
second company, the single-command baseline. Prints one JSON line, with the peak resident memory of the process.
"""
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path


def main():
    invoices = int(sys.argv[1])
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix='bf-doc-measure-'))
    root = work / f'root-{invoices}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    companies = [client.company.new(legal_name=name, home_currency='USD', timezone='UTC', organization='Measure',
                                    chart='general')['company_id'] for name in ('Measure', 'Baseline')]
    for company in companies:
        member = next(r for r in client.run('membership list', {'company': company})['items']
                      if r['scope_type'] == 'company' and r['scope_id'] == company)
        client.run('membership grant', dict(user=member['user_id'], company=company, expected_version=member['version'],
                                            grants=['transaction.invoice.delete']))
        income = client.run('account show', {'account': 'Service Income'}, company=company)['id']
        for name in ('Acme Plumbing', 'Bolt Electric', 'Cedar Homes'):
            client.run('customer create', {'name': name}, company=company)
        for name, price in (('Labor', '85'), ('Parts', '12.335'), ('Trip charge', '35')):
            client.run('item create', {'name': name, 'type': 'service', 'price': price, 'income_account_id': income},
                       company=company)
    customers = ['Acme Plumbing', 'Bolt Electric', 'Cedar Homes']
    lines = ['Invoice No,Customer,Date,Item,Description,Qty,Rate']
    for i in range(1, invoices + 1):
        for item, qty, rate in (('Labor', '2', '85.00'), ('Parts', '3', '12.335'), ('Trip charge', '1', '')):
            lines.append(f'INV-{i:05d},{customers[i % 3]},2026-02-{1 + i % 27:02d},{item},{item} line,{qty},{rate}')
    content = '\n'.join(lines) + '\n'
    company = companies[0]

    def run(name, raw, **extra):
        return client.run(name, raw, company=company, **extra)
    out = {'invoices': invoices, 'rows': invoices * 3}
    t = time.perf_counter()
    check = run('import check', {'kind': 'invoice', 'content': content})
    out['check'] = round(time.perf_counter() - t, 2)
    assert check['ok'], check['summary'][:3]
    t = time.perf_counter()
    preview = run('import preview', {'kind': 'invoice', 'content': content, 'decimal': '.'}, reason='Measure')
    out['preview'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    while True:
        done = run('import run', {'run': preview['run']['id']}, reason='Measure')
        if done['run']['state'] != 'running':
            break
    out['run'] = round(time.perf_counter() - t, 2)
    assert done['counts'].get('created') == invoices, done['counts']
    t = time.perf_counter()
    plan = run('import reverse', {'run': preview['run']['id']}, dry_run=True, reason='Measure')
    out['reverse_dry_run'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    first = True
    while True:
        body = {'run': preview['run']['id']}
        if first:
            body['fingerprint'] = plan['fingerprint']
            first = False
        reversed_ = run('import reverse', body, reason='Measure')
        if reversed_['state'] != 'reversing':
            break
    out['reverse'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    for i in range(1, invoices + 1):
        client.run('invoice post', {'customer': customers[i % 3], 'date': f'2026-02-{1 + i % 27:02d}',
                                    'number': f'INV-{i:05d}', 'lines': [
                                        {'item': 'Labor', 'quantity': '2', 'unit_price': '85.00',
                                         'description': 'Labor line'},
                                        {'item': 'Parts', 'quantity': '3', 'unit_price': '12.335',
                                         'description': 'Parts line'},
                                        {'item': 'Trip charge', 'quantity': '1', 'description': 'Trip charge line'}]},
                   company=companies[1], reason='Measure')
    out['loop_invoice_post'] = round(time.perf_counter() - t, 2)
    out['run_over_loop'] = round(out['run'] / out['loop_invoice_post'], 2)
    out['peak_rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(json.dumps(out))
    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
