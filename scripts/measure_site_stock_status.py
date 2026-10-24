"""Time `report stock-status` (company and --site) on the demo and on 1,000 items across five sites (R104).

Usage: PYTHONPATH=src python scripts/measure_site_stock_status.py [scratch-dir]
Prints one line per reading: what was read, how many rows, and the median of five runs in milliseconds.
"""
import os
import statistics
import sys
import tempfile
import time


def _time(run, name, args, repeat=5):
    took = []
    for _ in range(repeat):
        start = time.perf_counter()
        out = run(name, args)
        took.append((time.perf_counter() - start) * 1000)
    return len(out['rows']), statistics.median(took)


def main(scratch):
    import bookflow
    root = tempfile.mkdtemp(dir=scratch)
    os.environ['BOOKFLOW_DATA_ROOT'] = root
    client = bookflow.connect(data_root=root)
    client.init()
    client.run('demo reset', {})
    demo = client.company.list()['items'][0]['company_id']
    run = lambda name, args: client.run(name, args, company=demo)
    for label, args in (('demo company', {}), ('demo Van 1', {'site': 'Van 1'})):
        rows, ms = _time(run, 'report stock-status', dict(as_of='2026-12-31', limit=200, **args))
        print(f'stock-status {label}: {rows} rows, {ms:.0f} ms')

    client.organization.new(name='Scale org')
    co = client.company.new(legal_name='Scale', home_currency='USD', timezone='UTC', organization='Scale org',
                            chart='general')['company_id']
    run = lambda name, args, **kw: client.run(name, args, company=co, **kw)
    accounts = client.account.query(company=co, limit=200)['items']
    income = next(r['id'] for r in accounts if r['type'] == 'income')
    cogs = next(r['id'] for r in accounts if r['type'] == 'cost_of_goods_sold')
    vendor = client.vendor.create(company=co, name='Scale Supply')['id']
    sites = ['Main'] + [run('inventory-site create', {'name': f'Van {n}'}, reason='Scale')['name'] for n in range(1, 5)]
    items = [client.item.create(company=co, name=f'Part {n:04d}', type='inventory_part', description='p', price='5.00',
                                purchase_description='p', cost='2.00', cogs_account_id=cogs,
                                income_account_id=income)['id'] for n in range(1000)]
    for start in range(0, 1000, 100):
        for site in sites:
            run('bill post', dict(vendor=vendor, date='2026-01-05', items=[
                dict(item=item, quantity='3', unit_cost='2.00', site=site) for item in items[start:start + 100]]),
                reason='Scale stock')
    for label, args in (('1,000 items company', {}), ('1,000 items one site', {'site': 'Van 2'})):
        rows, ms = _time(run, 'report stock-status', dict(as_of='2026-12-31', limit=200, **args))
        print(f'stock-status {label}: {rows} rows/page, {ms:.0f} ms')
    rows, ms = _time(run, 'report quantity-by-site', dict(as_of='2026-12-31', limit=200))
    print(f'quantity-by-site 1,000 items x 5 sites: {rows} rows/page, {ms:.0f} ms')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else None)
