"""R108 closure measurements, one isolated workload per process.

Run each of ``import``, ``reverse`` and ``move`` separately through bf-suite.sh:

    PYTHONPATH=src /path/to/python tools/measure_bank.py import --workdir .measure-bank

Import previews and imports 5,000 OFX lines, then reads a 100-line inbox query and renders its authenticated HTML page.
Reverse creates 200 actual checks through bank-line add and reverses them in one
200-entry slice. Move transfers an unposted 200-line import to another account.
Times exclude fresh-company setup and seeding. Each run uses a unique child of
workdir and removes only that child, including on failure. The import workload starts and stops an in-process host;
HTTP is exercised with TestClient, without a listening socket.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import sqlite3
import tempfile
import time


def statement(count):
    rows = [
        f'<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>202609{1 + i % 30:02d}'
        f'<TRNAMT>-{i + 1}.00<FITID>CLOSE{i:06d}<NAME>Measure supplier</STMTTRN>'
        for i in range(count)
    ]
    return ('OFXHEADER:100\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD'
            '<BANKACCTFROM><BANKID>1<ACCTID>99990001</BANKACCTFROM><BANKTRANLIST>'
            + ''.join(rows) + '</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')


def measure(workload, root):
    os.environ['BOOKFLOW_DATA_ROOT'] = str(root)
    import bookflow
    from bookflow.core import registry

    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Measure')
    company = client.company.new(legal_name='Measure', home_currency='USD', timezone='UTC',
                                 organization='Measure', chart='general')['company_id']

    def run(name, raw, **context):
        if registry.get(name).is_write:
            context['reason'] = 'R108 closure measurement'
        return client.run(name, raw, company=company, **context)

    bank = run('account create', dict(name='Measure bank', type='bank'))['id']
    target = run('account create', dict(name='Measure savings', type='bank'))['id']
    expense = run('account create', dict(name='Measure expense', type='expense'))['id']
    count = 5000 if workload == 'import' else 200
    data = dict(account=bank, content=statement(count), limit=0)
    result = {'workload': workload, 'lines': count, 'transport': 'Python command client'}

    def timed(key, name, raw, **context):
        started = time.perf_counter()
        value = run(name, raw, **context)
        result[key] = round(time.perf_counter() - started, 3)
        return value

    preview = timed('preview_s', 'bank-feed import', data, dry_run=True)
    imported = timed('import_s', 'bank-feed import', data)
    assert preview['counts'] == imported['counts']
    assert imported['counts']['new'] == count and imported['counts']['waiting'] == count
    page = timed('inbox_100_s', 'bank-line query', dict(account=bank, limit=100))
    assert len(page['lines']) == 100 and page['next_cursor']
    assert sum(page['counts'].values()) == count
    result['inbox_rows'] = len(page['lines'])

    if workload == 'import':
        from fastapi.testclient import TestClient
        from bookflow.commands.host_cmds import start_serving
        from bookflow.core.config import os_login
        from bookflow.core.context import client_version

        password = 'measure-bank-local-password'
        client.run('user set-password', {'username': os_login(), 'password': password})
        handle = start_serving(root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
        try:
            with TestClient(handle.app) as api:
                login = api.post('/login', json={'username': os_login(), 'password': password})
                assert login.status_code == 200 and login.cookies.get('bookflow_session')
                started = time.perf_counter()
                response = api.get(f'/c/{company}/bank-inbox', params={'account': bank})
                result['inbox_html_100_s'] = round(time.perf_counter() - started, 3)
                assert response.status_code == 200, response.text
                assert response.text.count('data-bank-line=') == 100
                assert 'Next 100 lines' in response.text and 'cursor=100' in response.text
                result['inbox_html_rows'] = 100
                result['html_transport'] = 'authenticated HTTP TestClient, first page render'
        finally:
            handle.stop()
        result['host_stopped'] = True
    elif workload == 'reverse':
        state = client.permission.show()
        if state.get('mode') != 'policy_v1':
            client.permission.activate(expected_generation=state['generation'],
                                       expected_catalog_sha256=state['catalog_sha256'])
        member = next(m for m in client.membership.list(company=company)['items']
                      if m['scope_type'] == 'company')
        client.membership.grant(user=member['user_id'], company=company, role='owner',
                                expected_version=member['version'], grants=['transaction.check.delete'])
        run('vendor create', dict(name='Measure supplier'))
        lines = run('bank-line query', dict(account=bank, limit=1000))['lines']
        started = time.perf_counter()
        entries = []
        for line in lines:
            added = run('bank-line add', dict(line=line['id'], payee='Measure supplier', account=expense))
            entries.extend(e['entry_id'] for e in added['entries'])
        result['seed_200_checks_s'] = round(time.perf_counter() - started, 3)
        assert len(set(entries)) == 200
        plan = timed('reverse_preview_s', 'bank-feed reverse', dict(import_id=imported['import_id']), dry_run=True)
        assert plan['entries'] == 200 and not plan['blockers']
        reversed_ = timed('reverse_200_s', 'bank-feed reverse',
                          dict(import_id=imported['import_id'], fingerprint=plan['fingerprint'], limit=200))
        assert reversed_['state'] == 'reversed' and reversed_['reversed_now'] == 200
        assert reversed_['remaining'] == 0
        assert run('bank-line query', dict(account=bank))['lines'] == []
        # Cancellation postings must bring both the bank and category back to zero.
        db = Path(client.company.show(company=company)['path']) / 'company.db'
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
            balances = conn.execute('SELECT account_id, sum(debit_minor_units-credit_minor_units) '
                                    'FROM posting_lines GROUP BY account_id').fetchall()
            assert balances and all(balance == 0 for _, balance in balances), balances
        result['created_entries_reversed'] = 200
        result['balances_restored'] = True
    elif workload == 'move':
        data = dict(import_id=imported['import_id'], to=target)
        plan = timed('move_preview_s', 'bank-feed move', data, dry_run=True)
        assert not plan['reversal']['blockers']
        moved = timed('move_s', 'bank-feed move', dict(data, fingerprint=plan['reversal']['fingerprint']))
        assert moved['reversal']['state'] == 'reversed' and moved['target']['counts']['new'] == count
        assert moved['reversal']['remaining'] == 0
        assert run('bank-line query', dict(account=bank))['lines'] == []
        destination = run('bank-line query', dict(account=target, limit=1000))
        assert len(destination['lines']) == count and destination['counts']['waiting'] == count
        result['moved_lines'] = count
        result['created_entries'] = 0
    result['peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workload', choices=('import', 'reverse', 'move'))
    parser.add_argument('--workdir', type=Path, required=True)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f'{args.workload}-', dir=args.workdir.resolve()))
    try:
        result = measure(args.workload, root)
    finally:
        shutil.rmtree(root)
    result['data_removed'] = True
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
