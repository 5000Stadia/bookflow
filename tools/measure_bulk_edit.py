"""Timings of grid edits (R107 increment 6, design/bulk-import.md §7, §11): one size per process.

    python tools/measure_bulk_edit.py ROWS [WORKDIR]

Builds a fresh data root under WORKDIR (default: a temporary folder; use the root NVMe), a company on the general
chart and ROWS items (imported, which is faster than typing them). Times, in seconds: `bulk-edit grid` of every item
with three columns (the page's read); rendering the grid page's template with that answer (the page's HTML, a table
of ROWS rows of inputs, with its size); `bulk-edit preview` of a 5 percent price rise rounded to 0.05 over every item
(each record read and its update dry-run); `bulk-edit apply` (one update each); `bulk-edit reverse --dry-run` and
`bulk-edit reverse`. Prints one JSON line, with the peak resident memory of the process.
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
    rows = int(sys.argv[1])
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix='bf-bulk-measure-'))
    root = work / f'root-{rows}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    company = client.company.new(legal_name='Measure', home_currency='USD', timezone='UTC', organization='Measure',
                                 chart='general')['company_id']

    def run(name, raw=None, **kw):
        return client.run(name, raw or {}, company=company, **kw)
    income = next(a['full_name'] for a in run('account list')['items'] if a['type'] == 'income')
    lines = ['Item,Type,Income Account,Sales Price,Description'] + [
        f'Part {i:05d},service,{income},{i % 900 + 1}.{i % 100:02d},Part number {i}' for i in range(1, rows + 1)]
    preview = run('import preview', {'kind': 'item', 'content': '\n'.join(lines) + '\n',
                                     'constants': {'sales_enabled': True}})
    while run('import run', {'run': preview['run']['id']})['run']['state'] == 'running':
        pass
    out = {'rows': rows}

    def timed(key, fn):
        start = time.perf_counter()
        value = fn()
        out[key] = round(time.perf_counter() - start, 3)
        return value
    grid = timed('grid', lambda: run('bulk-edit grid', {'kind': 'item', 'columns': ['price', 'cost', 'description'],
                                                        'limit': 5000}))
    assert len(grid['records']) == rows, len(grid['records'])
    from bookflow.adapters.workbench import bulk_edit_pages as pages
    from bookflow.adapters.workbench.pages import env
    cols = run('bulk-edit columns', {'kind': 'item'})['columns']
    template = env.get_template('bulk_edit.html')

    def render():
        return template.render(company_id=company, company={'company_id': company}, kind='item', kinds=pages.KINDS,
                               kind_label='Items', columns=cols, chosen=['price', 'cost', 'description'],
                               by_field={c['field']: c for c in cols}, ops=pages.OPS, step='grid', steps=pages.STEPS,
                               outcomes=pages.OUTCOMES, q='', inactive=False, can_write=True, grid=grid,
                               static_urls={}, math_currencies={})
    html = timed('render', render)
    out['html_kb'] = round(len(html.encode()) / 1024)
    ask = {'kind': 'item', 'operations': [{'op': 'percent', 'field': 'price', 'by': '5', 'round': '0.05'}]}
    edit = timed('preview', lambda: run('bulk-edit preview', ask))
    timed('apply', lambda: run('bulk-edit apply', {'run': edit['run']['id']}))
    plan = timed('reverse_dry_run', lambda: run('bulk-edit reverse', {'run': edit['run']['id']}, dry_run=True))
    timed('reverse', lambda: run('bulk-edit reverse', {'run': edit['run']['id'], 'fingerprint': plan['fingerprint']}))
    out['changed'] = edit['counts']['change']
    out['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
