"""R186 bill pay: 284.60 + 75.25 + 40.00 restores 399.85 payable and zero cash movement."""
import sqlite3
from pathlib import Path

import pytest

from bookflow import BookflowError
from bookflow.company import batches
from bookflow.core.ids import new_id
from tests.test_bill_payment import books, _bills, _pay, _balances, _open_total  # noqa: F401


def _db(books):
    return sqlite3.connect(Path(books['run']('company show')['path']) / 'company.db')


def _snapshot(books):
    with _db(books) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (
            'transactions', 'ap_applications', 'posting_batches', 'posting_lines',
            'inventory_movements', 'batches', 'batch_effects', 'audit_events')}


def _reverse(books, batch_id, **context):
    return books['run']('batch reverse', {'batch': batch_id}, reason='Wrong bill selection', **context)


def _certify(books):
    run = books['run']
    opening = run('reconcile opening start', dict(
        operation_key=new_id(), account=books['bank'], opening_date='2017-03-01', entered_balance='0',
        references=[], evidence=dict(format=1, statement_reference=None, entered_text='Opening zero')),
        reason='Adopt opening')['draft']
    draft = run('reconcile start', dict(
        operation_key=new_id(), account=books['bank'], statement_date='2017-03-31',
        ending_balance='-399.85', opening_draft_id=opening['id']), reason='Statement')['draft']
    candidates = run('reconcile candidates', dict(draft=draft['id'], limit=200))['items']
    marked = run('reconcile mark', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=1,
        entries=[dict(movement=row['movement'], group_fingerprint=row['group_fingerprint'], action='mark')
                 for row in candidates]), reason='Cleared')['draft']
    preview = run('reconcile preview', dict(draft=draft['id'], expected_version=marked['version']))
    run('reconcile finish', dict(
        operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
        expected_facts_fingerprint=preview['expected_facts_fingerprint'],
        dependency_guard=preview['dependency_guard']), reason='Certify statement')
    return draft['id']


def test_bill_pay_batch_exact_plan_and_hand_worked_balances(books):
    bills = _bills(books)
    before = _balances(books)[0]
    assert before[books['payable']] == -39985
    assert before[books['parts']] == 29985 and before[books['freight']] == 10000
    paid = _pay(books, [{'bill': row['id']} for row in bills])
    assert paid['next'] == f"batch reverse {paid['batch_id']} --dry-run"
    assert _open_total(books) == 0
    assert _balances(books)[0][books['bank']] == -39985
    snapshot = _snapshot(books)
    plan = _reverse(books, paid['batch_id'], dry_run=True)
    assert _snapshot(books) == snapshot
    assert {row['record_id'] for row in plan['steps']} == {row['id'] for row in paid['payments']}
    assert all(row['steps'] == ['bill payment unapply', 'bill payment void'] for row in plan['steps'])
    assert _reverse(books, paid['batch_id'])['state'] == 'reversed'
    after = _balances(books)[0]
    assert after[books['payable']] == -39985 and after.get(books['bank'], 0) == 0
    assert after[books['parts']] == 29985 and after[books['freight']] == 10000
    assert {k: v for k, v in after.items() if v} == {k: v for k, v in before.items() if v}
    assert _open_total(books) == 39985
    assert _snapshot(books)['inventory_movements'] == snapshot['inventory_movements'] == []
    assert all(books['run']('bill payment show', {'payment': p['id']})['status'] == 'voided'
               for p in paid['payments'])


@pytest.mark.parametrize('blocker', ['pending_print', 'printed', 'reconciled', 'E_PERIOD_CLOSED'])
def test_bill_pay_blockers_refuse_whole_batch_without_changes(books, blocker):
    bills = _bills(books)
    paid = _pay(books, [{'bill': row['id']} for row in bills],
                to_be_printed=blocker in ('pending_print', 'printed'))
    if blocker in ('pending_print', 'printed'):
        printed = books['run']('check print', {'account': books['bank'], 'first_number': '1001'}, reason='Print')
        if blocker == 'printed':
            books['run']('check print-confirm', {'run': printed['id']}, reason='Printed')
    elif blocker == 'reconciled':
        _certify(books)
    else:
        books['run']('company update', {'closing_date': '2017-03-31'}, reason='Close March')
    snapshot = _snapshot(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            _reverse(books, paid['batch_id'], dry_run=dry_run)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        assert blocker in {row['code'] for row in refused.value.details['blockers']}
        if blocker == 'pending_print':
            assert any(printed['id'] in row['message'] for row in refused.value.details['blockers'])
        assert _snapshot(books) == snapshot


def test_bill_pay_interruption_after_unapply_resumes_without_duplicate_effects(books, monkeypatch):
    bills = _bills(books)
    paid = _pay(books, [{'bill': row['id']} for row in bills])
    original = batches.run_nested

    def interrupt(s, ctx, command, payload, **kwargs):
        result = original(s, ctx, command, payload, **kwargs)
        if command == 'bill payment unapply' and not kwargs.get('dry_run'):
            raise RuntimeError('Interrupted after committed unapply')
        return result

    monkeypatch.setattr(batches, 'run_nested', interrupt)
    with pytest.raises(RuntimeError, match='Interrupted'):
        _reverse(books, paid['batch_id'])
    monkeypatch.setattr(batches, 'run_nested', original)
    assert _reverse(books, paid['batch_id'])['state'] == 'reversed'
    assert _open_total(books) == 39985
    assert _balances(books)[0].get(books['bank'], 0) == 0
    with _db(books) as db:
        assert db.execute("SELECT count(*) FROM ap_applications WHERE kind='unapply'").fetchone()[0] == 3


def test_bill_pay_batch_and_payments_roll_back_together(books, monkeypatch):
    bills = _bills(books)
    snapshot = _snapshot(books)
    original = batches.record_effect

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Interrupted in transaction')

    monkeypatch.setattr(batches, 'record_effect', failed)
    with pytest.raises(RuntimeError, match='Interrupted'):
        _pay(books, [{'bill': row['id']} for row in bills])
    assert _snapshot(books) == snapshot


def test_bill_pay_agent_without_inverse_grant_is_refused(books):
    import os
    from tests.conftest import make_actor, make_agent, as_user
    from bookflow.core.config import Config

    bills = _bills(books)
    paid = _pay(books, [{'bill': row['id']} for row in bills])
    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    owner = make_actor(root, 'reverse-owner', company_role=(books['company'], 'owner'))
    agent_id = make_agent(books['client'].run, 'reverse-agent', principals=owner,
                          company=books['company'], owner=owner)
    member = next(row for row in books['client'].run('membership list', {'company': books['company']})['items']
                  if row['user_id'] == agent_id and row['scope_type'] == 'company')
    books['client'].run('membership grant', {
        'user': agent_id, 'company': books['company'], 'role': 'standard',
        'expected_version': member['version'], 'denies': ['ledger.post']},
        reason='Read batches without posting authority')
    books['client'].run('agent authorize', {'agent': agent_id, 'confirm_permitted_use': True})
    config = Config.load(root / 'config.toml')
    config.set_user('reverse-agent', agent_id)
    config.save()
    agent = as_user(root, 'reverse-agent')
    before = _snapshot(books)
    for dry_run in (True, False):
        with pytest.raises(BookflowError) as refused:
            agent.run('batch reverse', {'batch': paid['batch_id']}, company=books['company'],
                      reason='Reverse the named bill payments', dry_run=dry_run)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        assert any(row['code'] == 'E_PERMISSION' for row in refused.value.details['blockers'])
        assert _snapshot(books) == before


@pytest.mark.parametrize('reapply', [False, True])
def test_bill_pay_later_settlement_edit_blocks_reversal(books, reapply):
    bills = _bills(books)
    paid = _pay(books, [{'bill': row['id']} for row in bills])
    payment = paid['payments'][0]
    books['run']('bill payment unapply', {'payment': payment['id']}, reason='Change the allocation')
    if reapply:
        # Even reattaching the same bills is a later decision; the batch cannot erase it.
        bill_ids = [row['id'] for row in bills if row['vendor_id'] == payment['vendor_id']]
        books['run']('bill payment apply', {'payment': payment['id'], 'bills': [{'bill': i} for i in bill_ids]},
                     reason='Allocate again')
    before = _snapshot(books)
    with pytest.raises(BookflowError) as refused:
        _reverse(books, paid['batch_id'])
    assert refused.value.code == 'E_BATCH_BLOCKED'
    assert any(row['code'] == 'edited_again' and row['record_id'] == payment['id']
               for row in refused.value.details['blockers'])
    assert _snapshot(books) == before
