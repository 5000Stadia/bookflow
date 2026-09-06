"""Independent tax cents, transaction rollback and exact pre-reason recovery."""
import sqlite3
import json
import subprocess
import sys
from pathlib import Path
from datetime import timedelta

import pytest

from bookflow import BookflowError
from bookflow.core import clock
from tests.conftest import make_actor, as_user
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method, posted, snapshots
from tests.test_row8_journal import database_path


def test_receipt_preview_binds_resolved_automatic_number(client, sale):
    args = dict(customer=sale['customer'], date='2026-06-02', amount='1.00', payment_method=method(client), operation_key='number-reviewed')
    preview = client.run('payment receive', args, company=COMPANY, dry_run=True)
    client.run('payment receive', dict(args, operation_key='number-consumer'), company=COMPANY)
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('payment receive', dict(args, expected_facts_fingerprint=preview['facts_fingerprint']), company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE' and snapshots(client) == before
    fresh = client.run('payment receive', args, company=COMPANY, dry_run=True)
    assert fresh['facts_fingerprint'] != preview['facts_fingerprint']
    paid = client.run('payment receive', dict(args, expected_facts_fingerprint=fresh['facts_fingerprint']), company=COMPANY)
    assert paid['effect']['after_header']['number'] == fresh['effect']['after_header']['number']


def test_tax_partial_then_final_owning_cents_and_reports(client, sale):
    agency = client.vendor.create(name='Payment tax agency', is_tax_agency=True, company=COMPANY)['id']
    taxable = next(row['id'] for row in client.run('sales-tax-code list', {}, company=COMPANY)['items'] if row['taxable'])
    with sqlite3.connect(database_path(client)) as raw:
        liability = raw.execute("SELECT id FROM accounts WHERE system_role='sales_tax_payable'").fetchone()[0]
    info = client.run('company show', {}, company=COMPANY)
    client.run('company update', dict(expected_version=info['info_version'], sales_tax_enabled=True), company=COMPANY)
    tax = client.run('item create', dict(name='Payment eight percent tax', type='sales_tax_item', tax_percent='8',
        tax_agency_vendor_id=agency, liability_account_id=liability), company=COMPANY)['id']
    invoice = client.run('invoice post', dict(customer=sale['customer'], date='2026-06-01', sales_tax_item=tax,
        lines=[dict(item=sale['item'], unit_price='100.00', tax_code=taxable)]), company=COMPANY)
    payment_method = method(client)
    for ordinal, amount, expected in [(1, '54.01', {'net': 5001, 'tax': 400}), (2, '53.99', {'net': 4999, 'tax': 400})]:
        paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount=amount,
            payment_method=payment_method, operation_key=f'tax-part-{ordinal}',
            applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=ordinal, amount=amount)])), company=COMPANY)
        assert {row['logical_kind']: row['amount']['minor_units'] for row in paid['effect']['allocations']} == expected
    current = client.run('invoice settlement', dict(invoice=invoice['id']), company=COMPANY)
    assert (current['gross_minor_units'], current['applied_minor_units'], current['due_minor_units'], current['version']) == (10800, 10800, 0, 3)
    ar = invoice['revision']['profile']['control_account']['id']
    report = client.run('report general-ledger', dict(account=ar, date_from='2026-06-01', date_to='2026-06-30'), company=COMPANY)
    assert any(row.get('transaction_type') == 'payment' for row in report['rows'])


@pytest.mark.parametrize('table', ['payment_components', 'applications', 'application_allocations', 'payment_operations', 'payment_operation_items', 'audit_entries'])
def test_failed_receipt_rolls_back_all_financial_and_operation_state(client, sale, table):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'PAY-FAULT')
    args = dict(customer=sale['customer'], date='2026-06-01', amount='50.00', payment_method=method(client),
        operation_key='receipt-fault', applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='50.00')]))
    before = snapshots(client)
    with sqlite3.connect(database_path(client)) as raw:
        raw.execute(f"CREATE TRIGGER payment_test_fault BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'injected payment fault'); END")
    with pytest.raises(BookflowError):
        client.run('payment receive', args, company=COMPANY)
    assert snapshots(client) == before
    with sqlite3.connect(database_path(client)) as raw:
        raw.execute('DROP TRIGGER payment_test_fault')
    paid = client.run('payment receive', args, company=COMPANY)
    assert paid['current']['applied_minor_units'] == 5000


def test_human_reasonless_exact_agent_recovery_after_expiry_writes_nothing(client, sale, root, monkeypatch):
    args = dict(customer=sale['customer'], date='2026-06-01', amount='1.00', payment_method=method(client), operation_key='human-reasonless')
    paid = client.run('payment receive', args, company=COMPANY, idempotency_key='transport-key')
    company = client.company.show(company=COMPANY)['id']
    owner = make_actor(root, 'payment-agent-owner', company_role=(company, 'standard'))
    make_actor(root, 'payment-agent', kind='agent', owner_user_id=owner, company_role=(company, 'standard'))
    agent = as_user(root, 'payment-agent')
    before = snapshots(client)
    then = clock.now() + timedelta(days=40)
    monkeypatch.setattr(clock, 'now', lambda: then)
    recovered = agent.run('payment receive', args, company=COMPANY)
    assert recovered['idempotent_replay'] and recovered['effect'] == paid['effect']
    assert snapshots(client) == before
    for change in (dict(operation_key='agent-fresh'), dict(amount='2.00')):
        with pytest.raises(BookflowError) as caught:
            agent.run('payment receive', dict(args, **change), company=COMPANY)
        assert caught.value.code == 'E_REASON_REQUIRED'
    assert snapshots(client) == before


def test_preview_pages_reject_stale_facts_without_consuming_key(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'PAY-PAGE-STALE')
    args = dict(customer=sale['customer'], date='2026-06-01', amount='50.00', payment_method=method(client),
        operation_key='page-stale', applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='50.00')]))
    preview = client.run('payment receive', args, company=COMPANY, dry_run=True)
    descriptor = next(row for row in preview['prospective_pages'] if row['kind'] == 'allocations')
    client.run('invoice update', dict(invoice=invoice['id'], expected_version=1, memo='New facts'), company=COMPANY)
    before = snapshots(client)
    with pytest.raises(BookflowError) as caught:
        client.run('payment preview items', dict(request=descriptor['request'], kind=descriptor['kind'], facts_fingerprint=descriptor['facts_fingerprint']), company=COMPANY)
    assert caught.value.code == 'E_PREVIEW_STALE'
    assert snapshots(client) == before


def test_schema_rejects_immutable_and_wrong_source_writes(client, sale):
    invoice = posted(client, sale['customer'], sale['item'], '100.00', 'PAY-SCHEMA')
    paid = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-01', amount='50.00', payment_method=method(client),
        operation_key='schema-source', applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='50.00')])), company=COMPANY)
    with sqlite3.connect(database_path(client)) as raw:
        raw.execute('PRAGMA foreign_keys=ON')
        for table in ('payment_components', 'payment_component_keys', 'applications', 'application_allocations', 'payment_operations'):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                raw.execute(f'UPDATE {table} SET created_via=created_via')
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                raw.execute(f'DELETE FROM {table}')
        raw.row_factory = sqlite3.Row
        original = dict(raw.execute('SELECT * FROM applications WHERE paying_transaction_id=?', (paid['id'],)).fetchone())
        for patch in ({'amount_minor_units': 0}, {'paid_transaction_id': paid['id']}, {'currency': 'JPY'},
                      {'kind': 'unapply', 'reverses_application_id': original['id'], 'amount_minor_units': 4999}):
            row = dict(original, id='RAW-ATTACK', **patch)
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(f'INSERT INTO applications ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))


def test_actual_cli_receipt_and_recoverable_request(client, sale, root):
    payment_method = method(client)
    args = [str(Path(sys.executable).parent / 'bookflow'), 'payment', 'receive', '--data-root', str(root),
            '--company', COMPANY, '--customer', sale['customer'], '--date', '2026-06-01', '--amount', '12.34',
            '--payment-method', payment_method, '--operation-key', 'actual-cli-payment', '--json']
    completed = subprocess.run(args, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt['current']['available_minor_units'] == 1234
    recovered = client.run('payment operation show', dict(operation_key='actual-cli-payment'), company=COMPANY)
    assert recovered['request']['input']['amount'] == '12.34'
    assert 'applications' not in recovered['request']['input'] and recovered['request']['context'] == {}
    before = snapshots(client)
    retry = subprocess.run(args, capture_output=True, text=True)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert json.loads(retry.stdout)['idempotent_replay']
    assert snapshots(client) == before


def test_balanced_but_wrong_proportional_split_is_independently_rejected(client, sale, monkeypatch):
    from bookflow.company import payment_calculations
    invoice = client.run('invoice post', dict(customer=sale['customer'], date='2026-06-01', lines=[
        dict(item=sale['item'], unit_price='1.00'), dict(item=sale['item'], unit_price='1.00')]), company=COMPANY)
    payment_method = method(client)
    original = payment_calculations.allocate
    def wrong(amount, capacities):
        result = original(amount, capacities)
        if len(result) == 2:
            first, second = sorted(result)
            result[first] += 1
            result[second] -= 1
        return result
    before = snapshots(client)
    monkeypatch.setattr(payment_calculations, 'allocate', wrong)
    with pytest.raises(BookflowError) as caught:
        client.run('payment receive', dict(customer=sale['customer'], date='2026-06-01', amount='1.00', payment_method=payment_method,
            operation_key='wrong-split', applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='1.00')])), company=COMPANY)
    assert caught.value.code == 'E_INTERNAL' and 'largest-remainder' in caught.value.message
    assert snapshots(client) == before
