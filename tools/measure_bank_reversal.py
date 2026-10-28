"""Timings of reversing a bank import (R108 increment 2, design/bank-feeds.md §6 and §10.11): one size per process.

    python tools/measure_bank_reversal.py LINES [WORKDIR] [--created N] [--move] [--first-call]

Builds a fresh data root under WORKDIR (default: a temporary folder; use the root NVMe), a company on the general chart
with a bank account holding 200 journal entries (which the statement's first 200 lines match), imports an OFX statement
of LINES lines, and, with ``--created N``, posts N more journal entries recorded as entries the import created (one
``created`` effect each, as increment 3's `bank-line add` records them; the owner is granted their family Delete).
Times, in seconds: `bank-feed reverse --dry-run` (the plan, every inverse delete dry-run) and the reversal itself in
one slice (each created entry deleted by `journal delete`, then the lines hidden, matches undone and the account record
restored in one transaction). With ``--move`` the same is timed as `bank-feed move` to a second bank account (the
reversal, then the stored lines imported there in the transaction that finishes it). Prints one JSON line, with the
peak resident memory of the process. With ``--first-call`` the real call is the first slice only (the default
``--limit``, carrying the dry run's fingerprint), as a person or agent runs it: how long before the first answer.
"""
import json
import os
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

MATCHED = 200


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    created = int(sys.argv[sys.argv.index('--created') + 1]) if '--created' in sys.argv else 0
    if '--created' in sys.argv:
        args.remove(str(created))
    moving = '--move' in sys.argv
    first_call = '--first-call' in sys.argv
    lines = int(args[0])
    work = Path(args[1]) if len(args) > 1 else Path(tempfile.mkdtemp(prefix='bf-bank-reverse-'))
    root = work / f'root-{lines}-{created}'
    shutil.rmtree(root, ignore_errors=True)
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    company = client.company.new(legal_name='Measure', home_currency='USD', timezone='UTC', organization='Measure',
                                 chart='general')['company_id']

    def run(name, raw, **context):
        return client.run(name, raw, company=company, reason='Measure', **context)

    state = client.permission.show()
    if state.get('mode') != 'policy_v1':
        client.permission.activate(expected_generation=state['generation'],
                                   expected_catalog_sha256=state['catalog_sha256'])
    member = next(m for m in client.membership.list(company=company)['items'] if m['scope_type'] == 'company')
    client.membership.grant(user=member['user_id'], company=company, role='owner', expected_version=member['version'],
                            grants=['transaction.journal_entry.delete'])
    bank = run('account create', dict(name='Measure bank', type='bank'))['id']
    income = run('account create', dict(name='Measure income', type='income'))['id']
    start_day = date(2026, 1, 1)
    for i in range(min(MATCHED, lines)):
        amount = f'{100 + i}.{i % 100:02d}'
        run('journal post', dict(date=(start_day + timedelta(days=i % 300)).isoformat(), lines=[
            dict(account=bank, side='debit', amount=amount), dict(account=income, side='credit', amount=amount)]))
    body = []
    for i in range(lines):
        day = start_day + timedelta(days=i % 300)
        amount = f'{100 + i}.{i % 100:02d}' if i < MATCHED else f'-{(i % 5000) + 1}.{i % 100:02d}'
        body.append(f'<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{day:%Y%m%d}\n<TRNAMT>{amount}\n<FITID>M{i:07d}\n'
                    f'<NAME>PAYEE {i % 977}\n</STMTTRN>\n')
    text = ('OFXHEADER:100\n\n<OFX>\n<BANKMSGSRSV1><STMTTRNRS><STMTRS>\n<CURDEF>USD\n<BANKACCTFROM><BANKID>1\n'
            '<ACCTID>99990001\n</BANKACCTFROM>\n<BANKTRANLIST>\n' + ''.join(body)
            + '</BANKTRANLIST>\n</STMTRS></STMTTRNRS></BANKMSGSRSV1>\n</OFX>\n')
    done = run('bank-feed import', dict(account=bank, content=text, limit=0))
    out = {'lines': lines, 'matched': done['counts']['matched'], 'created': created, 'move': moving,
           'first_call': first_call}
    other = run('account create', dict(name='Measure savings', type='bank'))['id']
    t = time.perf_counter()
    made = [run('journal post', dict(date='2026-11-02', memo='created', lines=[
        dict(account=income, side='debit', amount=f'{1 + n}.00'), dict(account=bank, side='credit', amount=f'{1 + n}.00')]))
        for n in range(created)]
    out['seed_created_s'] = round(time.perf_counter() - t, 1)
    if made:
        db = Path(client.company.show(company=company)['path']) / 'company.db'
        with sqlite3.connect(db) as conn:
            seq = conn.execute('SELECT max(seq) FROM batch_effects WHERE batch_id = ?', (done['batch_id'],)).fetchone()[0]
            conn.executemany("INSERT INTO batch_effects (batch_id, seq, record_type, record_id, action, version_after, "
                             "created_at) VALUES (?, ?, 'transaction', ?, 'created', ?, '2026-10-28T00:00:00.000Z')",
                             [(done['batch_id'], seq + 1 + n, e['id'], e['version']) for n, e in enumerate(made)])
    name, data = ('bank-feed move', dict(import_id=done['import_id'], to=other)) if moving else (
        'bank-feed reverse', dict(import_id=done['import_id']))
    t = time.perf_counter()
    plan = run(name, data, dry_run=True)
    out['plan_s'] = round(time.perf_counter() - t, 3)
    plan = plan['reversal'] if moving else plan
    assert not plan['blockers'], plan['blockers'][:3]
    t = time.perf_counter()
    if first_call:
        result = run(name, dict(data, fingerprint=plan['fingerprint']))
        out['first_call_s'] = round(time.perf_counter() - t, 3)
        result = result['reversal'] if moving else result
        out['first_call_deleted'] = result['reversed_now']
        out['ms_per_deleted_entry'] = round(1000 * out['first_call_s'] / max(1, result['reversed_now']), 1)
        out['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        print(json.dumps(out))
        if len(args) <= 1:
            shutil.rmtree(work, ignore_errors=True)
        return
    result = run(name, dict(data, fingerprint=plan['fingerprint'], limit=max(1, created)))
    out['reverse_s'] = round(time.perf_counter() - t, 3)
    if moving:
        assert result['target']['counts']['new'] == lines, result['target']['counts']
        result = result['reversal']
    assert result['state'] == 'reversed' and result['lines'] == lines, result['state']
    if created:
        out['ms_per_created_entry'] = round(1000 * out['reverse_s'] / created, 1)
    out['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(json.dumps(out))
    if len(args) <= 1:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
