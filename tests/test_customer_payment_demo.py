"""Fixed active payment examples: raw old rows and independently enumerated increments."""
import json
import sqlite3
import subprocess
import tomllib
from pathlib import Path

import bookflow
import pytest
from bookflow.commands import hub_cmds

BASE='3f9a307e7b8f878d613f915087f6b552a17a04da'
RESOURCE=Path(__file__).parents[1]/'src/bookflow/demo'
EXPECTED=json.loads((RESOURCE/'payment-expected.json').read_text())


@pytest.mark.parametrize('filename,count',[('seed.toml',295),('reference.toml',201)])
def test_payment_append_keeps_exact_frozen_bytes_and_command_prefix(filename,count):
    old=subprocess.check_output(['git','show',BASE+':src/bookflow/demo/'+filename])
    current=(RESOURCE/filename).read_bytes()
    assert current.startswith(old)
    before=tomllib.loads(old.decode())['commands'];after=tomllib.loads(current.decode())['commands']
    assert len(before)==count and after[:count]==before and len(after)==EXPECTED['final_command_counts'][filename] == count+17+33


def snapshot(path):
    with sqlite3.connect(path) as db:
        result={}
        for (name,) in db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns=[row[1] for row in db.execute('PRAGMA table_info("'+name+'")')]
            # This sole presence field is mutable during authorized execution.
            columns=[column for column in columns if not (name=='principals' and column=='last_seen_at')]
            expressions=','.join('typeof("'+column+'"),quote("'+column+'")' for column in columns)
            result[name]={row[0]:row[1:] for row in db.execute('SELECT rowid,'+expressions+' FROM "'+name+'"')}
        return result


@pytest.fixture(scope='module')
def prefix_books(tmp_path_factory):
    root=tmp_path_factory.mktemp('payment-prefix-books')/'root'
    original=hub_cmds._load_seed
    def prefix(resource='seed.toml'):
        seed=original(resource)
        if resource in EXPECTED['old_command_counts']:
            seed=dict(seed,commands=seed['commands'][:EXPECTED['old_command_counts'][resource]])
        return seed
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT',str(root));patch.delenv('BOOKFLOW_COMPANY',raising=False)
        patch.setattr(hub_cmds,'_load_seed',prefix)
        client=bookflow.connect(data_root=str(root));client.init();client.demo.reset(include_reference=True)
    return root


@pytest.mark.parametrize('filename,company,prefix',[('seed.toml','Demo Plumbing Co','DEMO'),('reference.toml','Reference Plumbing Co','REF')])
def test_active_append_exact_preservation_and_business_oracles(prefix_books,filename,company,prefix,monkeypatch,tmp_path):
    import shutil
    root=tmp_path/'root';shutil.copytree(prefix_books,root)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root));monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    client=bookflow.connect(data_root=str(root));path=Path(client.company.show(company=company)['path'])/'company.db'
    before=snapshot(path)
    captures={'sale_exempt':client.run('sales-tax-code show',dict(sales_tax_code='SVC'),company=company)}
    receipts=[];resolved=[]
    start=EXPECTED['old_command_counts'][filename]+17  # the independently preserved tax block precedes payments
    entries=tomllib.loads((RESOURCE/filename).read_text())['commands'][start:start+EXPECTED['append_count']]
    assert len(entries)==33
    for entry in entries:
        data=hub_cmds._resolve_seed_references(entry['input'],captures)
        output=client.run(entry['command'],data,company=company,**({'reason':entry['reason']} if 'reason' in entry else {}))
        receipts.append(output);resolved.append((entry,data))
        if 'capture' in entry:captures[entry['capture']]=output
    after=snapshot(path)
    for table,rows in before.items():
        assert all(after[table].get(key)==value for key,value in rows.items()),table
    for table,count in EXPECTED['new_rows'].items():
        assert len(after[table])-len(before[table])==count,(table,len(after[table])-len(before[table]),count)
    with sqlite3.connect(path) as db:
        gross={name:[debit,credit] for name,debit,credit in db.execute('''SELECT a.name,sum(p.debit_minor_units),sum(p.credit_minor_units)
            FROM posting_lines p JOIN accounts a ON a.id=p.account_id JOIN transactions t ON t.id=p.transaction_id
            WHERE t.number LIKE ? GROUP BY a.name''',(prefix+'-PAY-%',))}
        assert gross==EXPECTED['gross_debits_credits']
        assert {name:d-c for name,(d,c) in gross.items()}==EXPECTED['net']
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    for owner,label in [('customer','parent'),('a','A'),('b','B')]:
        invoice=client.run('invoice settlement',dict(invoice=captures['pay_inv_'+owner]['id']),company=company)
        assert (invoice['due_minor_units'],invoice['version'])==(EXPECTED['invoice_due'][label],EXPECTED['invoice_versions'][label])
        party=client.customer.show(customer=captures['pay_'+owner]['id'],company=company)
        assert party['current_balance']['minor_units']==EXPECTED['party_AR'][label]
    for name in ('p1','p2'):
        payment=client.run('payment show',dict(payment=captures['pay_'+name]['id']),company=company)
        expected=EXPECTED['payments'][name.upper()]
        assert [payment['current'][field+'_minor_units'] for field in ('received','applied','available')]==[expected[field] for field in ('received','applied','available')]
        assert payment['version']==expected['version']
    # Original receipt retry remains exact after correction/unapply/reapply.
    stable=snapshot(path)
    for entry,data in resolved:
        if entry['command'].startswith('payment ') and 'operation_key' in data:
            recovered=client.run(entry['command'],data,company=company,**({'reason':entry['reason']} if 'reason' in entry else {}))
            assert recovered['idempotent_replay'] and not recovered['changed']
    assert snapshot(path)==stable
    # Normal query captures expose current immutable labels without changing seed writes.
    query = client.run('payment query', {'limit': 200}, company=company)
    for row in query['items']:
        shown = client.run('payment show', {'payment': row['id']}, company=company)
        assert row['payer_label'] == shown['revision']['profile']['payer']['label']
        assert row['method_label'] == shown['revision']['profile']['payment_method']['label']
    assert snapshot(path) == stable
    (tmp_path/'payment-query-capture.json').write_text(json.dumps(query, indent=2))
    (tmp_path/'payment-append-receipts.json').write_text(json.dumps(dict(commands=[dict(command=e['command'],input=d) for e,d in resolved],receipts=receipts),indent=2))
