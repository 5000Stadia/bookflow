"""R186: asset and finance-charge batches unwind the hand-worked books and retain history."""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_fixed_assets import books as _asset_books
from tests.test_finance_charges import books as charge_books, _policy, _invoice, _pay


@pytest.fixture
def asset_books(_asset_books):
    b = _asset_books
    member = next(r for r in b['client'].run('membership list', {'company': b['company']})['items']
                  if r['scope_type'] == 'company' and r['scope_id'] == b['company'])
    b['client'].run('membership grant', dict(user=member['user_id'], company=b['company'],
                                            expected_version=member['version'],
                                            grants=['transaction.journal_entry.delete']))
    return b


def _db(books):
    return Path(books['client'].company.show(company=books['company'])['path']) / 'company.db'


def _rows(books, sql):
    with sqlite3.connect(_db(books)) as conn:
        return conn.execute(sql).fetchall()


def _snapshot(books):
    with sqlite3.connect(_db(books)) as conn:
        return list(conn.iterdump())


def _balance(books):
    report = books['run']('report trial-balance', {'date_to': '2026-12-31'})
    return {r['account_id']: r['debit']['minor_units'] - r['credit']['minor_units']
            for r in report['rows'] if r['debit']['minor_units'] != r['credit']['minor_units']}


def _asset(books):
    item = books['asset']('Delivery van', '10000.00')
    books['run']('fixed-asset setup', dict(item=item, method='straight_line', salvage='1000.00', life_months=36))
    return item


def _charges(books):
    _policy(books)
    _invoice(books, books['acme'], 'INV-A', '1000.00', '2026-01-01', '2026-01-31')
    _invoice(books, books['birch'], 'INV-B', '50.00', '2026-01-21', '2026-02-20')


def _refused_unchanged(books, batch):
    before = _snapshot(books)
    with pytest.raises(BookflowError) as caught:
        books['run']('batch reverse', {'batch': batch})
    assert caught.value.code == 'E_BATCH_BLOCKED'
    assert _snapshot(books) == before
    return caught.value.details['blockers']


def test_depreciation_reversal_restores_books_and_keeps_links(asset_books):
    b = asset_books
    item = _asset(b)
    before, inventory = _balance(b), _rows(b, 'SELECT * FROM inventory_movements')
    out = b['run']('fixed-asset depreciate', {'through': '2026-03-31'})
    assert out['batch'] and out['next'] == f"batch reverse {out['batch']} --dry-run"
    assert out['total']['minor_units'] == 75000  # (10000 - 1000) / 36 * 3 = 750
    after = _balance(b)
    assert after[b['acc']['expense']] == 75000 and after[b['acc']['accum']] == -75000
    plan = b['run']('batch reverse', {'batch': out['batch']}, dry_run=True)
    assert {s['record_id'] for s in plan['steps']} == {e['transaction_id'] for e in out['entries']}
    assert all(s['record_type'] == 'journal_entry' and s['steps'] == ['journal delete'] for s in plan['steps'])
    links = _rows(b, 'SELECT * FROM fixed_asset_depreciations')
    assert b['run']('batch reverse', {'batch': out['batch']})['state'] == 'reversed'
    assert _balance(b) == before and _rows(b, 'SELECT * FROM inventory_movements') == inventory
    assert _rows(b, 'SELECT * FROM fixed_asset_depreciations') == links
    schedule = b['run']('fixed-asset schedule', {'item': item})
    assert schedule['accumulated_posted']['minor_units'] == 0 and schedule['pending_periods'] == 36
    assert schedule['book_value']['minor_units'] == 1000000


def test_depreciation_later_run_and_closed_period_block_whole_batch(asset_books):
    b = asset_books
    _asset(b)
    first = b['run']('fixed-asset depreciate', {'through': '2026-01-31'})
    second = b['run']('fixed-asset depreciate', {'through': '2026-02-28'})
    blockers = _refused_unchanged(b, first['batch'])
    assert any(x['code'] == 'later_batch' and x['record_id'] == second['batch'] for x in blockers)
    b['run']('batch reverse', {'batch': second['batch']})
    b['run']('company update', {'closing_date': '2026-01-31'})
    assert any('CLOSED' in x['code'] for x in _refused_unchanged(b, first['batch']))


def test_depreciation_interrupted_reversal_resumes(asset_books, monkeypatch):
    b = asset_books
    _asset(b)
    before = _balance(b)
    out = b['run']('fixed-asset depreciate', {'through': '2026-03-31'})
    original = batches.reverse
    def interrupted(*args, **kwargs):
        def stop(count):
            raise RuntimeError('simulated worker exit')
        return original(*args, **kwargs, interrupt=stop)
    monkeypatch.setattr(batches, 'reverse', interrupted)
    with pytest.raises(RuntimeError, match='simulated worker exit'):
        b['run']('batch reverse', {'batch': out['batch']})
    monkeypatch.setattr(batches, 'reverse', original)
    assert b['run']('batch show', {'batch': out['batch']})['batch']['state'] == 'reversing'
    assert b['run']('batch reverse', {'batch': out['batch']})['state'] == 'reversed'
    assert _balance(b) == before


def test_finance_charge_reversal_restores_ar_and_retires_only_created_item(charge_books):
    b = charge_books
    _charges(b)
    before, inventory = _balance(b), _rows(b, 'SELECT * FROM inventory_movements')
    out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    assert out['total']['minor_units'] == 1679  # 1000 * 18% * 30 / 365 = 14.79; Birch minimum 2.00
    assert out['next'] == f"batch reverse {out['batch']} --dry-run"
    after = _balance(b)
    assert after[b['ar']] - before[b['ar']] == 1679 and after[b['fc_income']] == -1679
    plan = b['run']('batch reverse', {'batch': out['batch']}, dry_run=True)
    charge_ids = {r['transaction_id'] for r in out['rows'] if r['status'] == 'assessed'}
    assert {s['record_id'] for s in plan['steps'] if s['record_type'] == 'statement_charge'} == charge_ids
    item_step = next(s for s in plan['steps'] if s['record_type'] == 'item')
    assert len(plan['steps']) == 3 and plan['steps'][-1] == item_step
    assert all(s['steps'] == ['statement-charge void'] for s in plan['steps'][:-1])
    assert item_step['steps'] == ['item update', 'item deactivate']
    bases = _rows(b, 'SELECT * FROM finance_charges')
    b['run']('batch reverse', {'batch': out['batch']})
    assert _balance(b) == before and _rows(b, 'SELECT * FROM inventory_movements') == inventory
    assert _rows(b, 'SELECT * FROM finance_charges') == bases
    assert not b['run']('item show', {'item': item_step['record_id']})['active']
    aging = b['run']('report ar-aging', {'as_of': '2026-03-02'})
    assert {r['customer_id']: r['total']['minor_units'] for r in aging['rows']} == {b['acme']: 100000, b['birch']: 5000}
    # History does not prevent a corrected assessment on the same date.
    again = b['run']('finance-charge assess', {'date': '2026-03-02'})
    assert again['assessed_count'] == 2


def test_finance_charge_payment_and_closed_period_block_every_effect(charge_books):
    b = charge_books
    _charges(b)
    out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    charge_id = next(r['transaction_id'] for r in out['rows'] if r['customer_id'] == b['acme'])
    charge = b['run']('statement-charge show', {'statement_charge': charge_id})
    _pay(b, b['acme'], '1.00', '2026-03-03', invoice=charge)
    blockers = _refused_unchanged(b, out['batch'])
    assert any(x['record_id'] == charge_id and 'payment' in x['message'] for x in blockers)
    b['run']('company update', {'closing_date': '2026-03-02'})
    blockers = _refused_unchanged(b, out['batch'])
    assert any('CLOSED' in x['code'] for x in blockers)


def test_finance_charge_existing_item_is_not_retired(charge_books):
    b = charge_books
    _charges(b)
    item = b['run']('item create', {'name': 'Existing fee', 'type': 'other_charge', 'sales_enabled': True,
                                    'income_account_id': b['fc_income'], 'description': 'Late fee', 'price': '0.00'})
    out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    plan = b['run']('batch reverse', {'batch': out['batch']}, dry_run=True)
    assert len(plan['steps']) == 2 and all(s['record_type'] == 'statement_charge' for s in plan['steps'])
    b['run']('batch reverse', {'batch': out['batch']})
    assert b['run']('item show', {'item': item['id']})['active']


@pytest.mark.parametrize('fixture,denied', [('asset_books', 'transaction.journal_entry.delete'),
                                           ('charge_books', 'ledger.post')])
def test_agent_without_inverse_grant_cannot_reverse_adopter(request, fixture, denied):
    import os
    from tests.conftest import make_actor, make_agent, as_user
    from bookflow.core.config import Config
    b = request.getfixturevalue(fixture)
    if fixture == 'asset_books':
        _asset(b)
        out = b['run']('fixed-asset depreciate', {'through': '2026-01-31'})
    else:
        _charges(b)
        out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    owner = make_actor(root, 'reverse-owner', company_role=(b['company'], 'owner'))
    if fixture == 'asset_books':
        owner_member = next(r for r in b['client'].run('membership list', {'company': b['company']})['items']
                            if r['user_id'] == owner and r['scope_type'] == 'company')
        b['client'].run('membership grant', dict(user=owner, company=b['company'],
                                               expected_version=owner_member['version'],
                                               grants=['transaction.journal_entry.delete']))
    agent_id = make_agent(b['client'].run, 'reverse-agent', principals=owner, company=b['company'], owner=owner)
    member = next(r for r in b['client'].run('membership list', {'company': b['company']})['items']
                  if r['user_id'] == agent_id and r['scope_type'] == 'company')
    b['client'].run('membership grant', {'user': agent_id, 'company': b['company'], 'role': 'standard',
                                       'expected_version': member['version'], 'denies': [denied]},
                   reason='grant batch reader')
    b['client'].run('agent authorize', {'agent': agent_id, 'confirm_permitted_use': True,
                                      'acknowledge_fresh_context': True}, reason='authorize reversal test agent')
    config = Config.load(root / 'config.toml')
    config.set_user('reverse-agent', agent_id)
    config.save()
    agent = as_user(root, 'reverse-agent')
    before = _snapshot(b)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as caught:
            agent.run('batch reverse', {'batch': out['batch']}, company=b['company'], reason='undo the named batch',
                      dry_run=dry_run)
        assert caught.value.code == 'E_BATCH_BLOCKED'
        assert any(x['code'] == 'E_PERMISSION' for x in caught.value.details['blockers'])
        assert _snapshot(b) == before


def test_finance_charge_slices_resume_and_retire_item_last(charge_books):
    b = charge_books
    _charges(b)
    before = _balance(b)
    out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    plan = b['run']('batch reverse', {'batch': out['batch']}, dry_run=True)
    item = next(s['record_id'] for s in plan['steps'] if s['record_type'] == 'item')
    for remaining in (2, 1):
        result = b['run']('batch reverse', {'batch': out['batch'], 'limit': 1})
        assert result['state'] == 'reversing' and result['remaining'] == remaining
        assert b['run']('item show', {'item': item})['active']
    assert b['run']('batch reverse', {'batch': out['batch'], 'limit': 1})['state'] == 'reversed'
    assert not b['run']('item show', {'item': item})['active']
    assert _balance(b) == before


@pytest.mark.parametrize('fixture', ['asset_books', 'charge_books'])
def test_effect_failure_rolls_back_its_accounting_write(request, monkeypatch, fixture):
    b = request.getfixturevalue(fixture)
    if fixture == 'asset_books':
        _asset(b)
        command, body = 'fixed-asset depreciate', {'through': '2026-01-31'}
    else:
        _charges(b)
        command, body = 'finance-charge assess', {'date': '2026-03-02'}
    original = batches.record_effect
    def fail_after_effect(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('effect fault')
    monkeypatch.setattr(batches, 'record_effect', fail_after_effect)
    before = _snapshot(b)
    with pytest.raises(RuntimeError, match='effect fault'):
        b['run'](command, body)
    assert _snapshot(b) == before


def test_finance_charge_item_retirement_collision_blocks_before_any_void(charge_books):
    b = charge_books
    _charges(b)
    out = b['run']('finance-charge assess', {'date': '2026-03-02'})
    name = batches.released_name('Finance Charge', out['batch'])
    b['run']('item create', {'name': name, 'type': 'other_charge', 'sales_enabled': True,
                            'income_account_id': b['fc_income'], 'description': 'Name already in use', 'price': '0.00'})
    before = _snapshot(b)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            b['run']('batch reverse', {'batch': out['batch']}, dry_run=dry_run)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        assert any(x['record_type'] == 'item' for x in refused.value.details['blockers'])
        assert _snapshot(b) == before
    assert all(b['run']('statement-charge show', {'statement_charge': row['transaction_id']})['status'] == 'posted'
               for row in out['rows'] if row['status'] == 'assessed')
