"""Independent account-register oracles across periods, corrections and pages."""
import base64
import json
from pathlib import Path
import sqlite3

import pytest
from bookflow import BookflowError

COMPANY = 'Demo Plumbing Co'


@pytest.fixture
def register_accounts(client):
    return [client.account.create(name='Register query bank', type='bank', company=COMPANY)['id'],
            client.account.create(name='Register query equity', type='equity', company=COMPANY)['id']]


def _post(client, accounts, amount, day='2026-01-10', **kwargs):
    return client.journal.post(date=day, lines=[
        {'account': accounts[0], 'side': 'debit', 'amount': amount},
        {'account': accounts[1], 'side': 'credit', 'amount': amount}], company=COMPANY, **kwargs)


def _query(client, account, **kw):
    return client.run('register query', {'account': account, 'date_from': '2026-01-01',
        'date_to': '2026-01-31', **kw}, company=COMPANY)


def test_period_paging_and_all_entries_balance_have_distinct_metadata(client, register_accounts):
    bank, equity = register_accounts
    _post(client, register_accounts, '10.00', '2025-12-31')
    _post(client, register_accounts, '3.00')
    first = _query(client, bank, limit=1)
    assert first['totals']['opening']['minor_units'] == 1000
    assert first['totals']['closing']['minor_units'] == 1300
    assert first['current_balance']['balance']['minor_units'] == 1300
    future = _post(client, register_accounts, '5.00', '2026-03-01')
    rows = list(first['rows'])
    cursor = first['next_cursor']
    while cursor:
        page = _query(client, bank, limit=1, cursor=cursor)
        assert page['metadata'] == first['metadata']
        assert page['totals'] == first['totals']
        assert page['current_balance']['balance']['minor_units'] == 1800
        assert page['current_balance']['audit_watermark'] > first['current_balance']['audit_watermark']
        rows.extend(page['rows'])
        cursor = page['next_cursor']
    assert [r['kind'] for r in rows] == ['opening', 'posting', 'closing']
    assert [r['running_balance']['minor_units'] for r in rows] == [1000, 1300, 1300]
    assert all(r['transaction_id'] != future['id'] for r in rows)
    credit = _query(client, equity)
    assert credit['totals']['closing']['minor_units'] == 1300
    assert credit['ledger_totals']['closing']['minor_units'] == -1300
    assert credit['rows'][1]['increase']['minor_units'] == 300


def test_register_corrected_voided_history_and_historical_offset_labels(client, register_accounts):
    bank, equity = register_accounts
    klass = client.run('class create', {'name': 'Register historical class'}, company=COMPANY)['id']
    journal = client.journal.post(date='2026-01-05', memo='Original memo', lines=[
        {'account': bank, 'side': 'debit', 'amount': '4.00', 'description': 'Selected explanation'},
        {'account': equity, 'side': 'credit', 'amount': '4.00', 'class_id': klass}], company=COMPANY)
    client.account.update(account=equity, name='Renamed offset', company=COMPANY)
    client.run('class update', {'class': klass, 'name': 'Renamed class'}, company=COMPANY)
    changed = client.journal.update(journal=journal['id'], expected_version=1, memo='Correction memo', company=COMPANY)
    client.journal.void(journal=journal['id'], expected_version=changed['version'], reason='Cancel query witness', company=COMPANY)
    page = _query(client, bank)
    effects = [r for r in page['rows'] if r['kind'] == 'posting']
    assert sorted(r['batch_kind'] for r in effects) == ['original', 'replacement', 'reversal', 'reversal']
    assert all(r['category_label'] == 'Register query equity' for r in effects)
    assert all(r['class_summary'] == 'Register historical class' for r in effects)
    assert all(r['description'] == 'Selected explanation' for r in effects)
    assert {r['memo'] for r in effects} == {'Original memo', 'Correction memo'}
    assert {(r['memo'], r['revision_number']) for r in effects} == {('Original memo', 1), ('Correction memo', 2)}
    assert page['totals']['closing']['minor_units'] == 0
    assert page['current_balance']['balance']['minor_units'] == 0
    assert page['totals']['increases']['minor_units'] == page['totals']['decreases']['minor_units'] == 800


def test_mixed_split_and_general_journal_categories_do_not_multiply_rows(client, register_accounts):
    bank, equity = register_accounts
    third = client.account.create(name='Register query second offset', type='equity', company=COMPANY)['id']
    klass = client.run('class create', {'name': 'One split class'}, company=COMPANY)['id']
    client.journal.post(date='2026-01-10', lines=[
        {'account': bank, 'side': 'debit', 'amount': '5.00'},
        {'account': equity, 'side': 'credit', 'amount': '6.00', 'class_id': klass},
        {'account': third, 'side': 'debit', 'amount': '1.00'}], company=COMPANY)
    client.journal.post(date='2026-01-11', lines=[
        {'account': bank, 'side': 'debit', 'amount': '2.00', 'class_id': klass},
        {'account': bank, 'side': 'credit', 'amount': '1.00'},
        {'account': equity, 'side': 'credit', 'amount': '1.00'}], company=COMPANY)
    page = _query(client, bank)
    effects = [r for r in page['rows'] if r['kind'] == 'posting']
    assert len(effects) == 3
    assert effects[0]['category_label'] == 'Splits' and effects[0]['class_summary'] == 'Mixed'
    assert all(r['category_label'] == 'General journal' for r in effects[1:])
    assert effects[1]['class_summary'] == 'One split class'
    assert page['totals']['closing']['minor_units'] == 600


@pytest.mark.parametrize('change', ['backdated', 'number', 'preference', 'tamper', 'account'])
def test_register_continuations_bind_display_metadata_and_query(client, register_accounts, change):
    bank, equity = register_accounts
    _post(client, register_accounts, '1.00')
    first = _query(client, bank, limit=1)
    cursor, account = first['next_cursor'], bank
    expected = 'E_QUERY_STALE'
    if change == 'backdated':
        _post(client, register_accounts, '2.00', '2025-12-31')
    elif change == 'number':
        client.account.update(account=bank, number='1888', company=COMPANY)
    elif change == 'preference':
        shown = client.company.show(company=COMPANY)
        client.company.update(use_account_numbers=not shown['info']['use_account_numbers'], company=COMPANY)
    elif change == 'account':
        account, expected = equity, 'E_VALIDATION'
    else:
        payload, sig = cursor.split('.')
        state = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        state['account_id'] = equity
        cursor = base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip('=') + '.' + sig
        expected = 'E_VALIDATION'
    with pytest.raises(BookflowError) as caught:
        _query(client, account, limit=1, cursor=cursor)
    assert caught.value.code == expected
    assert _query(client, bank)['count'] > 0
