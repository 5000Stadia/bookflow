"""Public reference-year witnesses with source arithmetic independent of the seed."""
from collections import Counter
import calendar
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import bookflow
import pytest

from bookflow.core.errors import BookflowError
from tests.conftest import as_user, make_actor

REFERENCE = 'Reference Plumbing Co'
DEMO = 'Demo Plumbing Co'
EXPECTED = json.loads(files('bookflow.demo').joinpath('reference-expected.json').read_text())


def cli_run(root, *args):
    process = subprocess.run([sys.executable, '-m', 'bookflow.adapters.cli.app', '--json', *args],
        env={**os.environ, 'BOOKFLOW_DATA_ROOT': str(root)}, capture_output=True, text=True)
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


@pytest.fixture(scope='session')
def reference_template(tmp_path_factory):
    root = tmp_path_factory.mktemp('reference-template') / 'root'
    cli_run(root, 'init')
    result = cli_run(root, 'demo', 'reset', '--include-reference')
    assert result['trashed_path'] is None
    assert result['reference_company_id'] != result['company_id']
    return root


@pytest.fixture
def reference_client(reference_template, tmp_path, monkeypatch):
    root = tmp_path / 'reference-root'
    shutil.copytree(reference_template, root)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    return bookflow.connect(data_root=str(root)), root


def source_effects():
    """Hand-entered business facts, expanded to signed immutable posting facts."""
    rows = []
    def entry(day, number, debit, credit, amount, kind='original'):
        rows.extend([(debit, day, number, kind, amount, 0), (credit, day, number, kind, 0, amount)])
    entry('2026-01-01', 'REF-OPEN', 'Checking', 'Opening Balance Equity', 1000000)
    entry('2026-01-02', 'REF-INSURANCE', 'Insurance Expense', 'Checking', 120000)
    for month, receipt in enumerate((300000,350000,400000,450000,500000,550000,600000,650000,700000,750000,800000,850000), 1):
        day = f'2026-{month:02}'
        entry(day+'-15', f'REF-SERVICE-{month:02}', 'Checking', 'Service Income', receipt)
        entry(day+'-20', f'REF-FEES-{month:02}', 'Professional Fees', 'Checking', 25000)
        if month >= 7:
            entry(day+'-28', f'REF-DEPRECIATION-{month:02}', 'Depreciation Expense', 'Accumulated Depreciation', 10000)
    entry('2026-05-15', 'REF-SERVICE-05', 'Service Income', 'Checking', 500000, 'reversal')
    entry('2026-05-15', 'REF-SERVICE-05', 'Checking', 'Service Income', 525000, 'replacement')
    entry('2026-06-15', 'REF-EQUIPMENT', 'Equipment', 'Checking', 240000)
    entry('2026-11-16', 'REF-DUPLICATE', 'Checking', 'Service Income', 90000)
    entry('2026-11-16', 'REF-DUPLICATE', 'Service Income', 'Checking', 90000, 'reversal')
    entry('2026-12-21', 'REF-CARD-FEE', 'Professional Fees', 'Business Credit Card', 30000)
    entry('2026-12-27', 'REF-CARD-PAYMENT', 'Business Credit Card', 'Checking', 10000)
    return rows


def test_stored_oracles_match_independent_source_arithmetic():
    assert EXPECTED['year'] == 2026 and EXPECTED['currency'] == 'USD'
    assert len(EXPECTED['monthly']) == 12
    for month, checkpoint in enumerate(EXPECTED['monthly'], 1):
        assert checkpoint['date_to'] == f'2026-{month:02}-{calendar.monthrange(2026, month)[1]}'
        for account, balance in checkpoint['balances'].items():
            facts = [row for row in source_effects() if row[0] == account and row[1] <= checkpoint['date_to']]
            gross = [sum(row[i] for row in facts) for i in (4,5)]
            assert checkpoint['gross_debits_credits'][account] == gross
            assert balance == gross[0] - gross[1]
        b = checkpoint['balances']
        assert sum(b.values()) == 0
        assert checkpoint['trial_balance'] == sum(max(0,n) for n in b.values())
        assert checkpoint['income'] == -b['Service Income']-b['Professional Fees']-b['Insurance Expense']-b['Depreciation Expense']
        assert checkpoint['net_assets'] == b['Checking']+b['Equipment']+b['Accumulated Depreciation']+b['Business Credit Card']
        assert checkpoint['net_assets'] == 1000000 + checkpoint['income']
        assert checkpoint['accounts_receivable'] == checkpoint['accounts_payable'] == 0
    assert EXPECTED['annual'] == EXPECTED['monthly'][-1]
    assert (EXPECTED['annual']['trial_balance'], EXPECTED['annual']['income'], EXPECTED['annual']['net_assets']) == (8005000,6415000,7415000)
    assert EXPECTED['monthly'][5]['trial_balance'] == 3575000
    assert EXPECTED['second_half']['opening'] == EXPECTED['monthly'][5]['balances']
    for account, gross in EXPECTED['second_half']['gross_debits_credits'].items():
        facts = [row for row in source_effects() if row[0] == account and row[1] >= '2026-07-01']
        assert gross == [sum(row[i] for row in facts) for i in (4,5)]


def page_rows(call, **kwargs):
    rows, totals, cursor = [], None, None
    while True:
        page = call(**kwargs, cursor=cursor)
        if totals is None:
            totals = page['totals']
        assert totals == page['totals']
        rows.extend(page['rows'])
        cursor = page['next_cursor']
        if cursor is None:
            return rows, totals


@pytest.mark.parametrize('month', range(1,13))
def test_monthly_trial_balances(reference_client, month):
    c, _ = reference_client
    checkpoint = EXPECTED['monthly'][month-1]
    small, totals = page_rows(c.report.trial_balance, company=REFERENCE, date_to=checkpoint['date_to'], limit=2)
    large, _ = page_rows(c.report.trial_balance, company=REFERENCE, date_to=checkpoint['date_to'], limit=200)
    assert small == large
    assert {r['current_account_label']:r['signed_net']['minor_units'] for r in small} == {a:n for a,n in checkpoint['balances'].items() if n}
    assert totals['debit']['minor_units'] == totals['credit']['minor_units'] == checkpoint['trial_balance']


@pytest.mark.parametrize('account', list(EXPECTED['annual']['balances']))
@pytest.mark.parametrize('start', ['2026-01-01','2026-07-01'])
def test_gross_paged_ledger_every_source_and_running_balance(reference_client, account, start):
    c, _ = reference_client
    options = dict(company=REFERENCE, account=account, date_from=start, date_to='2026-12-31')
    rows, totals = page_rows(c.report.general_ledger, **options, limit=2)
    large, _ = page_rows(c.report.general_ledger, **options, limit=200)
    assert rows == large  # IDs, exact order, amounts, attribution and summaries.
    opening = 0 if start == '2026-01-01' else EXPECTED['second_half']['opening'][account]
    gross = (EXPECTED['annual'] if start == '2026-01-01' else EXPECTED['second_half'])['gross_debits_credits'][account]
    assert {k:v['minor_units'] for k,v in totals.items()} == dict(opening=opening, period_debits=gross[0], period_credits=gross[1], closing=EXPECTED['annual']['balances'][account])
    postings = [r for r in rows if r['kind'] == 'posting']
    assert len({r['posting_line_id'] for r in postings}) == len(postings)
    actual = Counter((account,r['effective_date'],r['transaction_number'],r['batch_kind'],r['debit']['minor_units'],r['credit']['minor_units']) for r in postings)
    assert actual == Counter(row for row in source_effects() if row[0] == account and row[1] >= start)
    running = opening
    for row in rows:
        if row['kind'] == 'posting':
            running += row['debit']['minor_units']-row['credit']['minor_units']
        assert row['signed_balance']['minor_units'] == running
    for row in postings:
        if row['batch_kind'] in ('reversal','replacement'):
            source = next(r for r in postings if r['batch_id'] == row['reverses_batch_id' if row['batch_kind'] == 'reversal' else 'replaces_batch_id'])
            assert row['transaction_id'] == source['transaction_id']
            if row['batch_kind'] == 'reversal':
                assert row['debit'] == source['credit'] and row['credit'] == source['debit']


def assert_balances(c):
    companies = c.company.list()['items']
    assert {r['display_name'] for r in companies} == {DEMO,REFERENCE}
    assert len({r['organization_id'] for r in companies}) == 1
    ids = {r['display_name']:r['company_id'] for r in companies}
    assert ids[DEMO] != ids[REFERENCE]
    for name, cid in ids.items():
        assert c.company.show(company=name)['company_id'] == cid
    accounts = c.account.list(company=REFERENCE)['items']
    for name, signed in EXPECTED['annual']['balances'].items():
        shown = c.account.show(company=REFERENCE, account=name)
        listed = next(r for r in accounts if r['name'] == name)
        normal = -signed if shown['normal_balance'] == 'credit' else signed
        assert shown['balance']['minor_units'] == listed['balance']['minor_units'] == normal
        assert shown['balance']['currency'] == 'USD'
        assert shown['id'] != c.account.show(company=DEMO,account='Checking')['id']
    assert c.report.trial_balance(company=REFERENCE,date_to='2026-12-31')['totals']['debit']['minor_units'] == 8005000
    assert c.report.trial_balance(company=DEMO,date_to='2026-12-31')['totals']['debit']['minor_units'] == 664595
    assert c.account.show(company=DEMO,account='Checking')['balance']['minor_units'] == 612095
    assert c.journal.query(company=DEMO)['count'] == 10


def test_company_selection_balances_audit_and_closed_root_copy(reference_client, tmp_path, monkeypatch):
    c, root = reference_client
    assert_balances(c)
    # All client calls close their sessions; copy the complete closed portable root.
    copied = tmp_path/'copied-root'
    shutil.copytree(root, copied)
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(copied))
    restored = bookflow.connect(data_root=str(copied))
    assert_balances(restored)
    original, _ = page_rows(c.report.general_ledger, company=REFERENCE,date_from='2026-01-01',date_to='2026-12-31',limit=200)
    reopened, _ = page_rows(restored.report.general_ledger, company=REFERENCE,date_from='2026-01-01',date_to='2026-12-31',limit=200)
    assert reopened == original
    for command in ('journal post','journal update','journal void'):
        events = restored.audit.list(company=REFERENCE,command=command)['items']
        assert events
        for event in events:
            shown = restored.audit.show(company=REFERENCE,event=event['id'])
            assert shown['reason'] and shown['interface'] == 'cli'


def snapshot(root):
    # Match the existing dry-run contract: root.lock and SQLite WAL/SHM are
    # ephemeral coordination files. Hash every durable file, including databases.
    return {str(p.relative_to(root)):(p.stat().st_mode,hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob('*') if p.is_file() and p.name != 'root.lock' and not p.name.endswith(('-wal','-shm'))}


def test_preview_default_repeated_reset_and_whole_organization_boundary(reference_client):
    c, root = reference_client
    before = snapshot(root)
    preview = c.demo.reset(include_reference=True,dry_run=True)
    assert preview['dry_run'] and preview['reference_display_name'] == REFERENCE
    assert preview['reference_company_id'] != preview['company_id']
    assert snapshot(root) == before
    old = c.company.list()['items']
    org_id = old[0]['organization_id']
    c.company.new(legal_name='Disposable sibling',home_currency='USD',organization=org_id)
    outside = c.organization.new(name='Keep organization')
    kept = c.company.new(legal_name='Keep company',home_currency='USD',organization='Keep organization')
    kept_path = Path(c.company.show(company=kept['company_id'])['path'])
    keep_before = snapshot(kept_path)
    reset = c.demo.reset(include_reference=True)
    assert all(r['company_id'] not in (reset['company_id'],reset['reference_company_id']) for r in old)
    assert Path(reset['trashed_path']).is_dir()
    assert (Path(reset['trashed_path'])/'Disposable sibling').is_dir()
    assert snapshot(kept_path) == keep_before
    assert c.company.show(company=kept['company_id'])['organization_id'] == outside['organization_id']
    default = c.demo.reset()
    assert default['reference_company_id'] is default['reference_display_name'] is None
    assert {r['display_name'] for r in c.company.list()['items']} == {DEMO,'Keep company'}
    assert (Path(default['trashed_path'])/REFERENCE).is_dir()


def test_first_default_preview_and_foreign_name_collision(tmp_path, monkeypatch):
    root = tmp_path/'fresh-root'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root))
    c = bookflow.connect(data_root=str(root)); c.init()
    before = snapshot(root)
    assert c.demo.reset(include_reference=True,dry_run=True)['reference_display_name'] == REFERENCE
    assert snapshot(root) == before
    c.organization.new(name='Demo Holdings LLC')
    before = snapshot(root)
    for dry_run in (True,False):
        with pytest.raises(BookflowError) as caught:
            c.demo.reset(include_reference=True,dry_run=dry_run)
        assert caught.value.code == 'E_NAME_TAKEN'
        assert snapshot(root) == before


def test_readonly_and_sibling_permissions(reference_client):
    c, root = reference_client
    cid = c.company.show(company=REFERENCE)['company_id']
    make_actor(root,'reference-reader',company_role=(cid,'readonly'))
    reader = as_user(root,'reference-reader')
    assert [r['company_id'] for r in reader.company.list()['items']] == [cid]
    assert reader.report.trial_balance(company=cid,date_to='2026-12-31')['totals']['debit']['minor_units'] == 8005000
    for name, args, context in [('demo reset',{'include_reference':True},{}),('journal post',{'date':'2026-12-31','lines':[{'account':'Checking','side':'debit','amount':'1.00'},{'account':'Service Income','side':'credit','amount':'1.00'}]},{'company':cid})]:
        with pytest.raises(BookflowError) as caught:
            reader.run(name,args,**context)
        assert caught.value.code == 'E_PERMISSION'
    with pytest.raises(BookflowError):
        reader.report.trial_balance(company=DEMO,date_to='2026-12-31')


def test_partial_seed_failure_reports_committed_effects_and_rerun_recovers(reference_client, monkeypatch):
    from bookflow.commands import hub_cmds
    c, root = reference_client
    load = hub_cmds._load_seed
    def broken(resource='seed.toml'):
        seed = load(resource)
        if resource == 'reference.toml':
            # Fail after the opening journal and earlier account commands committed.
            seed['commands'] = seed['commands'][:6]+[{'command':'journal post','input':{'date':'bad'},'reason':'Injected fixture failure'}]
        return seed
    monkeypatch.setattr(hub_cmds,'_load_seed',broken)
    with pytest.raises(BookflowError) as caught:
        c.demo.reset(include_reference=True)
    error = caught.value
    assert error.code == 'E_PARTIAL_WRITE'
    assert 'remain saved' in error.message and 'entire disposable organization' in error.message
    assert error.details['incomplete_company_id'] == c.company.show(company=REFERENCE)['company_id']
    assert len(error.details['company_ids']) == 2 and error.details['request_id']
    assert c.account.show(company=REFERENCE,account='Checking')['balance']['minor_units'] == 1000000
    assert c.account.show(company=DEMO,account='Checking')['balance']['minor_units'] == 612095
    monkeypatch.setattr(hub_cmds,'_load_seed',load)
    assert c.demo.reset(include_reference=True)['trashed_path']
    assert_balances(c)


def test_public_boolean_schema_defaults_and_first_default_reset(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from bookflow.commands.hub_cmds import DemoResetInput, DemoResetOutput
    from bookflow.documentation.generate import render_tree
    assert DemoResetInput().include_reference is False
    for invalid in ('true', 1, None, []):
        with pytest.raises(ValidationError):
            DemoResetInput(include_reference=invalid)
    fields = DemoResetOutput.model_json_schema()['properties']
    for name in ('reference_company_id','reference_display_name'):
        assert fields[name]['default'] is None
        assert {'type':'null'} in fields[name]['anyOf']
    tree = render_tree()
    assert b'--include-reference' in tree['cli/demo.md']
    assert b'reference-year.md' in tree['index.md']
    root = tmp_path/'first-default'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT',str(root))
    c = bookflow.connect(data_root=str(root)); c.init()
    result = c.demo.reset()
    assert result['trashed_path'] is result['reference_company_id'] is result['reference_display_name'] is None
    assert [r['display_name'] for r in c.company.list()['items']] == [DEMO]
    assert c.journal.query(company=DEMO)['count'] == 10
    help_result = subprocess.run([sys.executable,'-m','bookflow.adapters.cli.app','demo','reset','--help'],
        capture_output=True,text=True,env={**os.environ,'NO_COLOR':'1'})
    assert help_result.returncode == 0 and '--include-reference' in help_result.stdout
