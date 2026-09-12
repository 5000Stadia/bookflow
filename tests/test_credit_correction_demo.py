"""The visible correction demo adds immutable history with zero final ledger effect."""
import sqlite3
import subprocess
import tomllib
from pathlib import Path

BASE = '438b36b60ccc596def68f1a75a823d4c1a90d82d'
RESOURCE = Path(__file__).parents[1] / 'src/bookflow/demo/seed.toml'


def test_credit_example_appends_seven_commands_without_changing_old_seed():
    old = subprocess.check_output(['git', 'show', BASE + ':src/bookflow/demo/seed.toml'])
    new = RESOURCE.read_bytes()
    assert new.startswith(old)
    before = tomllib.loads(old.decode())['commands']
    after = tomllib.loads(new.decode())['commands']
    assert after[:len(before)] == before
    assert len(after) == len(before) + 7
    assert [row['command'] for row in after[len(before):]] == [
        'customer create', 'invoice post', 'credit-memo post', 'customer-credit apply',
        'credit-memo show', 'credit-memo update', 'credit-memo history']


def test_seeded_credit_correction_keeps_invoice_paid_and_history_visible(client):
    company = 'Demo Plumbing Co'
    credit = client.run('credit-memo show', {'credit_memo': 'DEMO-CREDIT-CORRECTED'}, company=company)
    assert credit['version'] == 3
    assert credit['revision']['memo'] == 'Corrected allowance; invoice remains paid'
    assert credit['total_minor_units'] == 4000
    assert credit['source_current']['applied_minor_units'] == 4000
    assert credit['source_current']['available_minor_units'] == 0
    original = client.run('credit-memo show', {'credit_memo': credit['id'], 'revision_number': 1}, company=company)
    assert original['total_minor_units'] == 6000
    invoice = client.run('invoice settlement', {'invoice': 'DEMO-CREDIT-INVOICE'}, company=company)
    assert invoice['due_minor_units'] == 0
    path = Path(client.company.show(company=company)['path']) / 'company.db'
    with sqlite3.connect(path) as db:
        net = db.execute('''SELECT p.account_id,sum(p.debit_minor_units-p.credit_minor_units)
            FROM posting_lines p JOIN transactions t ON t.id=p.transaction_id
            WHERE t.number IN ('DEMO-CREDIT-INVOICE','DEMO-CREDIT-CORRECTED')
            GROUP BY p.account_id''').fetchall()
        assert net and all(amount == 0 for _, amount in net)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
