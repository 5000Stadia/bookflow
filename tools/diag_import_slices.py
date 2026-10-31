"""Memory diagnostic (R107 increment 5): what one `import run` slice leaves behind once a company is large.

    python tools/diag_import_slices.py ROWS TRACED_SLICE

Imports ROWS customers at the default slice, untraced, printing resident memory and Python's allocated blocks after
each slice; slice TRACED_SLICE alone runs under tracemalloc, and what it allocated and still holds after a garbage
collection is listed by traceback, largest first.
"""
import gc
import os
import shutil
import sys
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_import as m  # noqa: E402


def main():
    rows, traced = int(sys.argv[1]), int(sys.argv[2])
    root = f'/tmp/bf-m4-scale-measure/diag-{rows}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = root
    import bookflow
    client = bookflow.connect(data_root=root)
    client.init()
    client.organization.new(name='M')
    co = client.company.new(legal_name='M', home_currency='USD', timezone='UTC', organization='M',
                            chart='general')['company_id']
    text, _ = m.customer_file(rows)
    p = client.run('import preview', {'kind': 'customer', 'content': text}, company=co, reason='x')
    del text
    i = 0
    while True:
        i += 1
        if i == traced:
            gc.collect()
            tracemalloc.start(25)
            before = tracemalloc.take_snapshot()
        d = client.run('import run', {'run': p['run']['id']}, company=co, reason='x')
        if i == traced:
            gc.collect()
            after = tracemalloc.take_snapshot()
            tracemalloc.stop()
            diff = after.compare_to(before, 'traceback')
            print('held after the traced slice MB', round(sum(s.size_diff for s in diff) / 2 ** 20, 2), flush=True)
            for stat in diff[:5]:
                print(stat)
                print('\n'.join(stat.traceback.format()[-40:]))
        print(i, 'rss', m.rss_mb(), 'blocks', sys.getallocatedblocks(), flush=True)
        if d['run']['state'] != 'running':
            break
    shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
