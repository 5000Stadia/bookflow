"""Pending print runs reverse as one native cancellation, under the caller's grant."""
from pathlib import Path
import sqlite3

import pytest

from bookflow import BookflowError
from bookflow.company import batches
from tests.test_check_printing import books, _check, _queue  # noqa: F401


def _snapshot(books):
    path = next(p for p in Path(books['client'].data_root).rglob('*.db') if p.name == 'company.db')
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def _print(books):
    _check(books, '12.34')
    _check(books, '5.66')
    return books['run']('check print', {'account': books['bank'], 'first_number': '1001'})


def test_pending_run_has_exact_plan_and_cancels_all_cheques(books):
    run = _print(books)
    assert run['next'] == f"batch reverse {run['batch_id']} --dry-run"
    assert books['run']('check print-run', {'run': run['id']})['batch_id'] == run['batch_id']
    before = _snapshot(books)
    plan = books['run']('batch reverse', {'batch': run['batch_id']}, dry_run=True)
    assert _snapshot(books) == before
    assert len(plan['steps']) == 1
    step = plan['steps'][0]
    assert step['record_id'] == run['id'] and step['record_type'] == 'check_print_run'
    assert step['steps'] == [{'command': 'check print-cancel', 'input': {'run': run['id']},
                             'account': books['bank'],
                             'next_check_number': {'before': '1003', 'after': '1001'},
                             'cheques': [{'transaction_id': i['transaction_id'],
                                          'check_number': i['check_number'], 'outcome': 'cancelled',
                                          'number_released': True} for i in run['items']]}]
    out = books['run']('batch reverse', {'batch': run['batch_id'], 'fingerprint': plan['fingerprint']})
    assert out['state'] == 'reversed' and out['reversed_now'] == 1
    cancelled = books['run']('check print-run', {'run': run['id']})
    assert cancelled['status'] == 'cancelled' and cancelled['spoiled_numbers'] == []
    assert _queue(books)['total']['amount'] == '18.00'
    assert _queue(books)['next_check_number'] == '1001'


def test_one_printed_cheque_names_blocker_without_changing_anything(books):
    run = _print(books)
    books['run']('check print-confirm', {'run': run['id'], 'failed_from': '1002'})
    before = _snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as refused:
            books['run']('batch reverse', {'batch': run['batch_id']}, dry_run=dry)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        blocker = next(b for b in refused.value.details['blockers'] if
                       'a printed cheque cannot be recalled' in b['message'])
        assert '1001' in blocker['message'] and run['items'][0]['transaction_id'] in blocker['message']
        assert _snapshot(books) == before


def test_inverse_grant_is_required(books):
    from tests.conftest import as_user, make_actor
    run = _print(books)
    root = Path(books['client'].data_root)
    make_actor(root, 'clerk')
    books['client'].run('membership grant', {'user': 'clerk', 'company': books['company'],
                          'expected_version': 0, 'role': 'standard', 'denies': ['check.print-confirm']})
    reader = as_user(root, 'clerk')
    reader.run('batch show', {'batch': run['batch_id']}, company=books['company'])
    before = _snapshot(books)
    for dry in (True, False):
        with pytest.raises(BookflowError) as refused:
            reader.run('batch reverse', {'batch': run['batch_id']}, company=books['company'], dry_run=dry)
        assert refused.value.code == 'E_BATCH_BLOCKED'
        assert any(b['code'] == 'E_PERMISSION' for b in refused.value.details['blockers'])
        assert _snapshot(books) == before


def test_effect_failure_rolls_back_run_and_number_pointer(books, monkeypatch):
    _check(books, '12.34')
    before = _snapshot(books)
    def fail(*args, **kwargs):
        raise BookflowError('E_VALIDATION', message='Effect failed')
    monkeypatch.setattr(batches, 'record_effect', fail)
    with pytest.raises(BookflowError):
        books['run']('check print', {'account': books['bank'], 'first_number': '1001'})
    assert _snapshot(books) == before


def test_pointer_change_invalidates_preview_and_cancellation_keeps_later_number(books):
    run = _print(books)
    plan = books['run']('batch reverse', {'batch': run['batch_id']}, dry_run=True)
    # A later issued cheque owns the account's next-number pointer now.
    _check(books, '1.00', to_be_printed=False, number='2000')
    with pytest.raises(BookflowError) as stale:
        books['run']('batch reverse', {'batch': run['batch_id'], 'fingerprint': plan['fingerprint']})
    assert stale.value.code == 'E_VERSION_CONFLICT'
    fresh = books['run']('batch reverse', {'batch': run['batch_id']}, dry_run=True)
    assert fresh['steps'][0]['steps'][0]['next_check_number'] == {'before': '2001', 'after': '2001'}
    books['run']('batch reverse', {'batch': run['batch_id'], 'fingerprint': fresh['fingerprint']})
    assert _queue(books)['next_check_number'] == '2001'


def test_reversal_resumes_after_native_cancel_commits(books, monkeypatch):
    run = _print(books)
    original = batches._finish
    def interrupted(*args, **kwargs):
        raise RuntimeError('interrupted after native cancellation')
    monkeypatch.setattr(batches, '_finish', interrupted)
    with pytest.raises(RuntimeError):
        books['run']('batch reverse', {'batch': run['batch_id']})
    assert books['run']('check print-run', {'run': run['id']})['status'] == 'cancelled'
    monkeypatch.setattr(batches, '_finish', original)
    resumed = books['run']('batch reverse', {'batch': run['batch_id']})
    assert resumed['state'] == 'reversed' and resumed['reversed_now'] == 0
