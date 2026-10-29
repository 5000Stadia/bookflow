"""WIP diagnostic (R107 increment 5): resident memory, traced memory and live objects per `import run` slice of N
customers, then the top tracemalloc growth from slice 2 to the end. Not yet run; see the WIP commit message.

    python tools/diag_import_slices.py N
"""
import gc, os, sys, shutil, time, tracemalloc, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_import as m
N = int(sys.argv[1])
root = f'/tmp/bf-m4-scale-measure/diag-{N}'
shutil.rmtree(root, ignore_errors=True)
os.environ['BOOKFLOW_DATA_ROOT'] = root
import bookflow
client = bookflow.connect(data_root=root); client.init()
client.organization.new(name='M')
co = client.company.new(legal_name='M', home_currency='USD', timezone='UTC', organization='M', chart='general')['company_id']
text, _ = m.customer_file(N)
p = client.run('import preview', {'kind': 'customer', 'content': text}, company=co, reason='x')
del text
gc.collect()
print('after preview rss', m.rss_mb(), 'objs', len(gc.get_objects()), flush=True)
tracemalloc.start(15)
snaps = []
i = 0
while True:
    tracemalloc.reset_peak()
    d = client.run('import run', {'run': p['run']['id']}, company=co, reason='x')
    i += 1
    cur, peak = tracemalloc.get_traced_memory()
    gc.collect()
    print(i, 'rss', m.rss_mb(), 'traced cur MB', round(cur/2**20,1), 'slice peak MB', round(peak/2**20,1), 'objs', len(gc.get_objects()), flush=True)
    if i == 2: s2 = tracemalloc.take_snapshot()
    if d['run']['state'] != 'running': break
s = tracemalloc.take_snapshot()
for st in s.compare_to(s2, 'traceback')[:6]:
    print(st); print('\n'.join(st.traceback.format()[-8:]))
shutil.rmtree(root, ignore_errors=True)
