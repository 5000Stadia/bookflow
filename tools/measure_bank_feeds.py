"""Timings of a bank statement import into the inbox (R108 increment 1, design/bank-feeds.md §10.11): one size per
process.

    python tools/measure_bank_feeds.py LINES [WORKDIR] [--entries N]

Builds a fresh data root under WORKDIR (default: a temporary folder; use the root NVMe), a company on the general chart
with a bank account holding N journal entries (default 200), and an OFX statement of LINES lines whose first N are
those entries (so the matcher pairs them) and the rest new. Times, in seconds: the parse alone; `bank-feed import
--dry-run` (the preview: read, dedupe, match); the import itself (re-derived inside the write, then recorded with its
batch); and the same file again with `again` (every line already in). Prints one JSON line, with the peak resident
memory of the process.
"""
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    entries = int(sys.argv[sys.argv.index('--entries') + 1]) if '--entries' in sys.argv else 200
    if '--entries' in sys.argv:
        args.remove(str(entries))
    lines = int(args[0])
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.mkdtemp(prefix='bf-bank-measure-'))
    root = work / f'root-{lines}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    from bookflow.company import statement_files as files
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    company = client.company.new(legal_name='Measure', home_currency='USD', timezone='UTC', organization='Measure',
                                 chart='general')['company_id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, reason='Measure', **context)

    bank = run('account create', dict(name='Measure bank', type='bank'))['id']
    income = run('account create', dict(name='Measure income', type='income'))['id']
    start_day = date(2026, 1, 1)
    for i in range(entries):
        amount = f'{100 + i}.{i % 100:02d}'
        run('journal post', dict(date=(start_day + timedelta(days=i % 300)).isoformat(), lines=[
            dict(account=bank, side='debit', amount=amount), dict(account=income, side='credit', amount=amount)]))
    body = []
    for i in range(lines):
        day = start_day + timedelta(days=i % 300)
        amount = f'{100 + i}.{i % 100:02d}' if i < entries else f'-{(i % 5000) + 1}.{i % 100:02d}'
        body.append(f'<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{day:%Y%m%d}\n<TRNAMT>{amount}\n<FITID>M{i:07d}\n'
                    f'<NAME>PAYEE {i % 977}\n</STMTTRN>\n')
    text = ('OFXHEADER:100\n\n<OFX>\n<BANKMSGSRSV1><STMTTRNRS><STMTRS>\n<CURDEF>USD\n<BANKACCTFROM><BANKID>1\n'
            '<ACCTID>99990001\n</BANKACCTFROM>\n<BANKTRANLIST>\n' + ''.join(body)
            + '</BANKTRANLIST>\n</STMTRS></STMTTRNRS></BANKMSGSRSV1>\n</OFX>\n')
    out = {'lines': lines, 'entries': entries}
    t = time.perf_counter()
    files.parse(text, 'auto', 2)
    out['parse_s'] = round(time.perf_counter() - t, 3)
    data = dict(account=bank, content=text, limit=0)
    t = time.perf_counter()
    preview = run('bank-feed import', data, dry_run=True)
    out['preview_s'] = round(time.perf_counter() - t, 3)
    t = time.perf_counter()
    done = run('bank-feed import', data)
    out['import_s'] = round(time.perf_counter() - t, 3)
    assert done['counts'] == preview['counts'] and done['counts']['new'] == lines, done['counts']
    out['matched'] = done['counts']['matched']
    t = time.perf_counter()
    again = run('bank-feed import', {**data, 'again': True})
    out['again_s'] = round(time.perf_counter() - t, 3)
    assert again['counts']['already_in'] == lines
    out['ms_per_line_import'] = round(1000 * out['import_s'] / lines, 2)
    out['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(json.dumps(out))
    if len(args) <= 1:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
