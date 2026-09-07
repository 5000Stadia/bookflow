"""Exact additive active examples, old raw rows and zero financial-command effects."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tomllib
import pytest
import bookflow
from bookflow.commands import hub_cmds
from bookflow.demo.recovery import resolve_intent
from tests.test_customer_payment_demo import snapshot

BASE='2dbce6683dcdd968b8c950c86f4c5e8321567027'
RESOURCE=Path(__file__).parents[1]/'src/bookflow/demo'
EXPECTED=json.loads((RESOURCE/'recovery-expected.json').read_text())

@pytest.mark.parametrize('filename',['seed.toml','reference.toml'])
def test_preserves_the_entire_combined_seed_prefix(filename):
    old=subprocess.check_output(['git','show',BASE+':src/bookflow/demo/'+filename])
    current=(RESOURCE/filename).read_bytes()
    assert current.startswith(old)
    before=tomllib.loads(old.decode())['commands'];after=tomllib.loads(current.decode())['commands']
    assert after[:len(before)]==before and len(after)-len(before)==43
    assert len(after)==EXPECTED['final_command_counts'][filename]

@pytest.fixture(scope='module')
def prefix_root(tmp_path_factory):
    root=tmp_path_factory.mktemp('recovery-seed-prefix')/'root'
    loader=hub_cmds._load_seed
    def load(resource='seed.toml'):
        seed=loader(resource)
        if resource in EXPECTED['base_command_counts']:
            seed=dict(seed,commands=seed['commands'][:EXPECTED['base_command_counts'][resource]])
        return seed
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('BOOKFLOW_DATA_ROOT',str(root));patch.delenv('BOOKFLOW_COMPANY',raising=False)
        patch.setattr(hub_cmds,'_load_seed',load)
        client=bookflow.connect(data_root=str(root));client.init();client.demo.reset(include_reference=True)
    return root

@pytest.mark.timeout(300)
@pytest.mark.parametrize('filename,company,prefix',[('seed.toml','Demo Plumbing Co','DEMO'),('reference.toml','Reference Plumbing Co','REF')])
def test_full_additive_recovery_seed_raw_and_financial_oracles(prefix_root,filename,company,prefix,tmp_path,monkeypatch):
    root=tmp_path/'root';shutil.copytree(prefix_root,root)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root));monkeypatch.delenv('BOOKFLOW_COMPANY',raising=False)
    client=bookflow.connect(data_root=str(root));path=Path(client.company.show(company=company)['path'])/'company.db'
    before=snapshot(path)
    parent=client.run('invoice settlement',dict(invoice=prefix+'-PAY-INV-CUSTOMER'),company=company)
    assert parent['due_minor_units']==1000
    captures={'pay_customer':client.run('customer show',dict(customer='Payment Example Customer'),company=company)}
    receipt_log=[]
    entries=tomllib.loads((RESOURCE/filename).read_text())['commands'][EXPECTED['base_command_counts'][filename]:]
    assert len(entries)==43
    for entry in entries:
        assert entry['command'].startswith(('payment recovery ','payment selection ')) or entry['command']=='invoice show'
        data=hub_cmds._resolve_seed_references(entry['input'],captures)
        data=resolve_intent(entry,data,captures,hub_cmds._resolve_seed_references)
        output=client.run(entry['command'],data,company=company,**({'reason':entry['reason']} if 'reason' in entry else {}))
        receipt_log.append(dict(command=entry['command'],input=data,output=output))
        if entry.get('capture'):captures[entry['capture']]=output
    after=snapshot(path)
    for table,rows in before.items():
        assert all(after[table].get(key)==value for key,value in rows.items()),table
        assert len(after[table])-len(rows)==EXPECTED['new_rows'].get(table,0),(table,len(after[table])-len(rows))
    for suffix,expected in EXPECTED['selections'].items():
        actual=captures['rec_'+suffix+'_selection']
        assert actual['amount']['minor_units']==expected['amount_minor_units']
        assert actual['applied_minor_units']==expected['selected_minor_units'] and actual['unapplied_minor_units']==expected['unapplied_minor_units']
        assert actual['version']==expected['version'] and actual['current_lifecycle']['state']==expected['lifecycle']
        if suffix!='done':
            assert actual['current_lifecycle']['received_entry_count']==expected['received'] and actual['current_lifecycle']['declared_entry_count']==expected['declared']
    assert captures['rec_pending']['total_count']==2 and captures['rec_discovery']['total_count']==4
    assert client.run('invoice settlement',dict(invoice=prefix+'-PAY-INV-CUSTOMER'),company=company)['due_minor_units']==1000
    old_digests={table:hashlib.sha256(repr(rows).encode()).hexdigest() for table,rows in before.items()}
    (tmp_path/'seed-evidence.json').write_text(json.dumps(dict(old_row_digests=old_digests,new_rows={t:len(after[t])-len(before[t]) for t in before},commands=receipt_log),indent=2))
