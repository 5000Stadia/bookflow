"""Scale of imports (R107 increment 5, design/bulk-import.md §5, §10 criterion 8): one kind and size per process.

    python tools/measure_import.py customer ROWS [WORKDIR] [--loop] [--profile FILE] [--seconds N] [--limit N]
    python tools/measure_import.py invoice INVOICES [WORKDIR] [--loop] [--profile FILE]

A fresh data root under WORKDIR (default: a temporary folder; use the root NVMe) and a company on the general chart.
``customer`` imports ROWS customers (email, phone, terms, credit limit, city, postal code); ``invoice`` imports
INVOICES invoices of 3 lines each, keyed by number, over three customers and three service items. Times, in seconds:
reading the file and guessing its mapping; `import preview`; `import run` called until done at its default slice (or
``--limit`` rows and ``--seconds``), as an agent would, with the slice count and the slowest slice. The process's
resident memory is read after the preview and after every slice. Its high-water mark (``VmHWM``) is reset once the
companies are made (writing 5 to ``/proc/self/clear_refs``), read after the preview (``hwm_preview_mb``), reset again
and read after the run (``hwm_run_mb``): the import's own peaks, apart from the setup's, which ``ru_maxrss``
(``peak_rss_mb``, the whole process) includes. With ``--loop``, a second company then runs
the same single command once per row in-process (`client.run`, as a script would), the criterion 8 baseline, and
``run_over_loop`` is the import's time over the loop's. ``--profile FILE`` writes a cProfile of the run phase.
Prints one JSON line.
"""
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

CUSTOMERS = ['Acme Plumbing', 'Bolt Electric', 'Cedar Homes']
LINES = (('Labor', '2', '85.00'), ('Parts', '3', '12.335'), ('Trip charge', '1', ''))


def rss_mb() -> float:
    with open('/proc/self/statm') as f:
        return round(int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 2 ** 20, 1)


def reset_hwm() -> None:
    """Reset the process's resident high-water mark to its current size (Linux)."""
    with open('/proc/self/clear_refs', 'w') as f:
        f.write('5')


def hwm_mb() -> float:
    with open('/proc/self/status') as f:
        kb = next(int(line.split()[1]) for line in f if line.startswith('VmHWM:'))
    return round(kb / 1024, 1)


def peak_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def option(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def customer_file(rows, singles_too=False):
    lines = ['Customer,E-mail,Phone,Terms,Credit Limit,City,Zip']
    singles = []
    for i in range(rows):
        lines.append(f'Customer {i:06d},c{i}@example.test,0{i:05d},Net 30,{i % 997}.25,Springfield,0{i % 9999:04d}')
        if singles_too:
            singles.append({'name': f'Customer {i:06d}', 'email': f'c{i}@example.test', 'phone': f'0{i:05d}',
                            'terms_id': None, 'credit_limit': f'{i % 997}.25',
                            'billing_address': {'city': 'Springfield', 'postal_code': f'0{i % 9999:04d}'}})
    return '\n'.join(lines) + '\n', singles


def invoice_file(invoices, singles_too=False):
    lines = ['Invoice No,Customer,Date,Item,Description,Qty,Rate']
    singles = []
    for i in range(1, invoices + 1):
        body = {'customer': CUSTOMERS[i % 3], 'date': f'2026-02-{1 + i % 27:02d}', 'number': f'INV-{i:05d}',
                'lines': []}
        for item, qty, rate in LINES:
            lines.append(f'INV-{i:05d},{CUSTOMERS[i % 3]},2026-02-{1 + i % 27:02d},{item},{item} line,{qty},{rate}')
            line = {'item': item, 'quantity': qty, 'description': f'{item} line'}
            if rate:
                line['unit_price'] = rate
            body['lines'].append(line)
        if singles_too:
            singles.append(body)
    return '\n'.join(lines) + '\n', singles


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    for name in ('--profile', '--seconds', '--limit'):
        if name in sys.argv:
            args.remove(option(name))
    kind, size = args[0], int(args[1])
    work = Path(args[2]) if len(args) > 2 else Path(tempfile.mkdtemp(prefix='bf-import-scale-'))
    root = work / f'root-{kind}-{size}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    from bookflow.company import import_kinds, tabular
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    companies = [client.company.new(legal_name=name, home_currency='USD', timezone='UTC', organization='Measure',
                                    chart='general')['company_id'] for name in ('Measure', 'Baseline')]
    if kind == 'invoice':
        for company in companies:
            income = client.run('account show', {'account': 'Service Income'}, company=company)['id']
            for name in CUSTOMERS:
                client.run('customer create', {'name': name}, company=company)
            for name, price in (('Labor', '85'), ('Parts', '12.335'), ('Trip charge', '35')):
                client.run('item create', {'name': name, 'type': 'service', 'price': price,
                                           'income_account_id': income}, company=company)
        text = invoice_file(size)[0]  # the loop's inputs are made after the run
        make = invoice_file
        command, rows = 'invoice post', size * 3
    else:
        text = customer_file(size)[0]
        make = customer_file
        command, rows = 'customer create', size
    company = companies[0]

    def run(name, raw, **extra):
        return client.run(name, raw, company=company, reason='Measure', **extra)

    out = {'kind': kind, 'records': size, 'rows': rows, 'rss_start_mb': rss_mb(), 'peak_before_import_mb': peak_mb()}
    reset_hwm()
    t = time.perf_counter()
    table = tabular.read(text.encode('utf-8'))
    import_kinds.guess(kind, table.headers)
    out['parse_and_guess_s'] = round(time.perf_counter() - t, 3)
    del table
    t = time.perf_counter()
    body = {'kind': kind, 'content': text}
    if kind == 'invoice':
        body['decimal'] = '.'
    preview = run('import preview', body)
    out['preview_s'] = round(time.perf_counter() - t, 3)
    out['rss_after_preview_mb'] = rss_mb()
    out['hwm_preview_mb'] = hwm_mb()
    reset_hwm()
    want = {'create': size}
    assert preview['counts'] == want, preview['counts']
    out['preview_next'] = preview['next']
    run_body = {'run': preview['run']['id']}
    if '--limit' in sys.argv:
        run_body['limit'] = int(option('--limit'))
    if '--seconds' in sys.argv:
        run_body['seconds'] = int(option('--seconds'))
    profiler = None
    if '--profile' in sys.argv:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    t = time.perf_counter()
    slices, slowest, rss, blocks = 0, 0.0, [], []
    while True:
        s0 = time.perf_counter()
        done = run('import run', run_body)
        slices += 1
        slowest = max(slowest, time.perf_counter() - s0)
        rss.append(rss_mb())
        blocks.append(sys.getallocatedblocks())
        if done['run']['state'] != 'running':
            break
    out['run_s'] = round(time.perf_counter() - t, 3)
    if profiler is not None:
        profiler.disable()
        profiler.dump_stats(option('--profile'))
    assert done['run']['state'] == 'done' and done['counts'].get('created') == size, done['counts']
    out['slices'] = slices
    out['slowest_slice_s'] = round(slowest, 2)
    out['rss_slices_mb'] = {'first': rss[0], 'middle': rss[len(rss) // 2], 'last': rss[-1], 'max': max(rss)}
    out['hwm_run_mb'] = hwm_mb()
    out['peak_rss_mb'] = peak_mb()
    if '--series' in sys.argv:
        out['rss_series_mb'] = rss
        out['python_blocks_series'] = blocks
    out['preview_ms_per_row'] = round(1000 * out['preview_s'] / rows, 2)
    out['run_ms_per_record'] = round(1000 * out['run_s'] / size, 2)
    if '--loop' in sys.argv:
        singles = make(size, singles_too=True)[1]  # made only now, so the import's memory holds none of them
        if kind == 'customer':  # the import resolves "Net 30" to its id from the file; a script looks it up once
            terms = client.run('term show', {'term': 'Net 30'}, company=companies[1])['id']
            for single in singles:
                single['terms_id'] = terms
        t = time.perf_counter()
        for single in singles:
            client.run(command, single, company=companies[1], reason='Measure')
        out['loop_s'] = round(time.perf_counter() - t, 3)
        out['loop_ms_per_record'] = round(1000 * out['loop_s'] / size, 2)
        out['run_over_loop'] = round(out['run_s'] / out['loop_s'], 3)
    print(json.dumps(out))
    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
