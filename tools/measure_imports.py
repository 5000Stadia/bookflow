"""Baseline timings of list imports (R107 increment 1, design/bulk-import.md §5): one size per process.

    python tools/measure_imports.py ROWS [WORKDIR] [--baseline]

Builds a fresh data root under WORKDIR (default: a temporary folder; use the root NVMe), a company on the general
chart, and a customer file of ROWS rows. Times, in seconds: reading the file and guessing its mapping; `import
preview` (every row dry-run through `customer create`); `import run` to the end and `import reverse --dry-run` then
`import reverse` carrying its fingerprint, each called again until done at its default slice, as an agent would; and,
with ``--baseline``, in a second company, a loop calling `customer create` once per row in-process, the
single-command baseline. Prints one JSON line, with the peak resident memory of the process.
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
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    baseline = '--baseline' in sys.argv
    rows = int(args[0])
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.mkdtemp(prefix='bf-import-measure-'))
    root = work / f'root-{rows}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    from bookflow.company import import_kinds, tabular
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    company = client.company.new(legal_name='Measure', home_currency='USD', timezone='UTC', organization='Measure',
                                 chart='general')['company_id']
    baseline_company = client.company.new(legal_name='Baseline', home_currency='USD', timezone='UTC',
                                          organization='Measure', chart='general')['company_id']
    lines = ['Customer,E-mail,Phone,Terms,Credit Limit,City,Zip']
    raw_rows = []
    for i in range(rows):
        lines.append(f'Customer {i:06d},c{i}@example.test,0{i:05d},Net 30,{i % 997}.25,Springfield,0{i % 9999:04d}')
        raw_rows.append({'name': f'Customer {i:06d}', 'email': f'c{i}@example.test', 'phone': f'0{i:05d}',
                         'credit_limit': f'{i % 997}.25', 'billing_address': {'city': 'Springfield',
                                                                              'postal_code': f'0{i % 9999:04d}'}})
    text = '\n'.join(lines) + '\n'
    out = {'rows': rows}
    start = time.perf_counter()
    table = tabular.read(text.encode('utf-8'))
    import_kinds.guess('customer', table.headers)
    out['parse_and_guess_s'] = round(time.perf_counter() - start, 3)

    def run(name, raw, **context):
        return client.run(name, raw, company=company, **context)

    start = time.perf_counter()
    preview = run('import preview', {'kind': 'customer', 'content': text}, reason='Measure')
    out['preview_s'] = round(time.perf_counter() - start, 3)
    assert preview['counts'] == {'create': rows}, preview['counts']
    start = time.perf_counter()
    while True:
        done = run('import run', {'run': preview['run']['id']})
        if done['run']['state'] != 'running':
            break
    out['run_s'] = round(time.perf_counter() - start, 3)
    assert done['counts'].get('created') == rows, done['counts']
    start = time.perf_counter()
    plan = run('import reverse', {'run': preview['run']['id']}, dry_run=True)
    out['reverse_dry_run_s'] = round(time.perf_counter() - start, 3)
    assert not plan['blockers'] and plan['remaining'] == rows
    start = time.perf_counter()
    calls = 0
    reversed_ = {'state': 'reversing'}
    while reversed_['state'] == 'reversing':
        calls += 1
        reversed_ = run('import reverse', {'run': preview['run']['id'], 'fingerprint': plan['fingerprint']},
                        reason='Measure')
    out['reverse_calls'] = calls
    out['reverse_s'] = round(time.perf_counter() - start, 3)
    out['reverse_over_run'] = round(out['reverse_s'] / out['run_s'], 2)
    names = ['preview', 'run', 'reverse_dry_run', 'reverse']
    if baseline:
        start = time.perf_counter()
        for raw in raw_rows:
            client.run('customer create', raw, company=baseline_company)
        out['single_command_loop_s'] = round(time.perf_counter() - start, 3)
        out['run_over_loop'] = round(out['run_s'] / out['single_command_loop_s'], 2)
        names.append('single_command_loop')
    for name in names:
        out[f'{name}_ms_per_row'] = round(1000 * out[f'{name}_s'] / rows, 2)
    out['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    print(json.dumps(out))
    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
